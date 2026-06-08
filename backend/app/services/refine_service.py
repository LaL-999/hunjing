"""角色对焦服务 — Sprint 1.D 灵魂。

对接 prompts/character_focus.md v3 LOCKED + docs/MVP阶段1_角色对焦组件设计.md §16
三层兜底(已知 LLM 行为漂移的代码兜底)。

主流程:
  refine_project    用户触发对焦
  action_refinement 处理一条建议(accept/reject/edit)
  skip_session      跳过整个 session

§16 三层兜底实现位置:
  filter_llm_output()        — 兜底 ②③(LLM 输出落库前过滤)
  _apply_payload_to_character() — 兜底 ①(value 覆盖非空字段时 raise)
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.db import execute, fetch_all, fetch_one, transaction
from app.models.character import Character
from app.services.llm_client import (
    LlmCallFailed,
    LlmJsonParseFailed,
    call_llm_json,
    estimate_cost_yuan,
)
from app.services.project_service import (
    ResourceNotFoundOrForbidden,
    get_project_or_403,
    iso_now,
)
from app.services.credit_service import (
    consume_credits,
    credit_units_for_text_call,
)

# === 路径 ===
PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "prompts"
PROMPT_FILE = "character_focus.md"

# === 业务常量 ===
MIN_CHARACTERS_FOR_REFINE = 3
MAX_REFINEMENTS_PER_SESSION = 25  # 与 prompt 铁律 4 一致

VALID_KINDS = {
    "identity_补全",
    "personality_补充",
    "quote_补充",
    "no_go_补充",
    "consistency_警告",
    # Sprint 6.A2 M1(2026-05-18):evolution_hint — 提示关系演化(非矛盾)
    # 前端跳 3 按钮(加 phase / 替换 type / 真矛盾去改)
    "evolution_hint",
    # Sprint 6.A2 FOCUS(2026-05-21):behavior_baseline 子字段补全
    # P0G.2(2026-05-24):3 子字段(out_of_baseline_examples 已删,见 migration 062)
    "behavior_baseline_补充",
}

# Sprint 6.A2 FOCUS(2026-05-21):behavior_baseline 枚举白名单(prompt v4 同源)
_SPEECH_REGISTER_VALID = {"卑微", "平和", "强硬", "恶意"}
_MORAL_COMPASS_VALID = {"善", "灰", "恶"}
_BEHAVIOR_BASELINE_FIELDS = {
    "speech_register",
    "emotional_intensity",
    "moral_compass",
}


# === §16 兜底 ②:已知作品专属词典 ===
# 后续运营可热更新(阶段 2 移到 SQLite 一张表;现在 inline)
KNOWN_WORK_RESERVED_TERMS: dict[str, list[str]] = {
    "李寻欢": ["飞刀", "兵器谱", "林诗音", "百晓生", "金钱帮", "上官金虹", "阿飞"],
    "孙小红": ["百晓生", "李寻欢", "金钱帮"],
    "上官金虹": ["金钱帮", "李寻欢", "百晓生"],
    "雪诺": ["守夜人", "长城", "临冬城", "私生子", "异鬼", "瑟曦", "丹妮"],
    "宝玉": ["通灵宝玉", "大观园", "怡红院", "绛珠草", "黛玉", "宝钗"],
    "鸣人": ["忍者", "火影", "九尾", "木叶", "查克拉", "佐助", "卡卡西"],
    "哈利波特": ["魔杖", "霍格沃茨", "伏地魔", "闪电疤", "邓布利多", "罗恩", "赫敏"],
    "佐助": ["写轮眼", "宇智波", "复仇", "鸣人", "卡卡西"],
    "福尔摩斯": ["贝克街", "烟斗", "华生", "莫里亚蒂"],
}

# === §16 兜底 ③:疑似自相矛盾的措辞 ===
SELF_CONTRADICTION_PHRASES = (
    "是空的",
    "为空",
    "字数不足",
    "字数太少",
    "过于简单",
    "未填",
    "没有填",
)


# === 异常 ===

class TooFewCharactersError(Exception):
    """角色数 < MIN_CHARACTERS_FOR_REFINE,不足以触发对焦。"""


class RefinementAlreadyActioned(Exception):
    """refinement 已处理过(accept/reject/edit/skipped)。"""


class WouldOverwriteNonEmpty(Exception):
    """§16 兜底 ① 拦截:value 试图覆盖非空字段。"""

    def __init__(self, field: str, current_value: str):
        super().__init__(
            f"建议会覆盖用户已填的 {field}={current_value[:30]!r},已拒绝"
        )
        self.field = field
        self.current_value = current_value


# === 工具 ===

def _load_prompt() -> str:
    return (PROMPTS_DIR / PROMPT_FILE).read_text(encoding="utf-8")


# ======================================================================
# 主流程 ① — refine_project(LLM + 三层兜底过滤 + 落库)
# ======================================================================

def refine_project(
    conn: sqlite3.Connection, project_id: str, user_id: str
) -> dict:
    """触发对焦。返回 {session_id, refinements: [...], stats: {...}}。

    异常:
      ResourceNotFoundOrForbidden — project 不属于当前用户
      TooFewCharactersError       — 角色数 < 3
      LlmCallFailed               — LLM 调用失败
      LlmJsonParseFailed          — LLM 响应非合法 JSON / 顶层非 array
    """
    project = get_project_or_403(conn, project_id, user_id)

    char_rows = fetch_all(
        conn,
        "SELECT * FROM characters WHERE project_id=? ORDER BY created_at",
        (project_id,),
    )
    characters = [Character.from_row(r) for r in char_rows]
    if len(characters) < MIN_CHARACTERS_FOR_REFINE:
        raise TooFewCharactersError(
            f"至少需要 {MIN_CHARACTERS_FOR_REFINE} 个角色才能对焦,当前 {len(characters)} 个"
        )

    rel_rows = fetch_all(
        conn,
        "SELECT * FROM relationships WHERE project_id=? ORDER BY created_at",
        (project_id,),
    )

    # 拼 LLM 输入(对齐 prompts/character_focus.md v3 schema)
    prompt_input = {
        "project": {
            "name": project.name,
            "type": project.type,
            "tags": project.tags,
        },
        "characters": [
            {
                "id": c.id,
                "name": c.name,
                "identity": c.identity,
                "personality": c.personality,
                "quotes": c.quotes,
                "no_go_list": c.no_go_list,
                # Sprint 6.A2 FOCUS(2026-05-21):暴露 behavior_baseline 给 LLM
                # → LLM 才能识别哪个子字段已填、哪个为空,精准补缺
                "behavior_baseline": c.behavior_baseline,
            }
            for c in characters
        ],
        "relationships": [
            {
                # Sprint 6.A2 M1(2026-05-18):必须传 id 给 LLM,evolution_hint payload
                # 才能填真实 UUID;否则 LLM 凭 source_id→target_id 凑一个假 id 流到前端
                "id": r["id"],
                "source_id": r["source_id"],
                "target_id": r["target_id"],
                "type": r["type"],
                "description": r["description"],
            }
            for r in rel_rows
        ],
    }

    # 调 LLM
    started_at = datetime.now(timezone.utc)
    system_prompt = _load_prompt()
    parsed, usage = call_llm_json(system_prompt, prompt_input)
    duration_ms = int(
        (datetime.now(timezone.utc) - started_at).total_seconds() * 1000
    )

    # P0F.1(2026-05-24)— LLM 漂移容忍
    # 117 角色一次性输入时,LLM 易漂移把 array 包成 dict({refinements: [...]}/{suggestions: [...]})
    # prompt 虽然要求顶层数组,但实战 LLM 偶有漂移 → 自动解包常见包裹键
    if isinstance(parsed, dict):
        for wrap_key in ("refinements", "suggestions", "data", "items", "results", "output"):
            inner = parsed.get(wrap_key)
            if isinstance(inner, list):
                import logging
                logging.getLogger(__name__).info(
                    f"refine: LLM 漂移输出 dict[{wrap_key!r}],自动解包为 array"
                )
                parsed = inner
                break

    if not isinstance(parsed, list):
        raise LlmJsonParseFailed(
            f"LLM 输出顶层不是数组,实际:{type(parsed).__name__}"
        )

    # === §16 三层兜底过滤 ===
    char_lookup = {c.id: c for c in characters}
    rel_id_set = {r["id"] for r in rel_rows}
    filtered = filter_llm_output(parsed, char_lookup, rel_id_set)

    # 落库
    session_id = str(uuid.uuid4())
    cost_yuan = estimate_cost_yuan(usage["input_tokens"], usage["output_tokens"])
    triggered_at = iso_now()

    refinement_ids: list[str] = []
    with transaction(conn) as tx:
        execute(
            tx,
            "INSERT INTO refine_sessions "
            "(id, project_id, user_id, triggered_at, "
            " tokens_input, tokens_output, cost_yuan, duration_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id, project_id, user_id, triggered_at,
                usage["input_tokens"], usage["output_tokens"],
                cost_yuan, duration_ms,
            ),
        )
        for item in filtered:
            ref_id = str(uuid.uuid4())
            refinement_ids.append(ref_id)
            execute(
                tx,
                "INSERT INTO character_refinements "
                "(id, session_id, character_id, suggestion_kind, suggestion_text, "
                " suggestion_payload, status) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
                (
                    ref_id, session_id, item["character_id"],
                    item["suggestion_kind"], item["suggestion_text"],
                    json.dumps(item["suggestion_payload"], ensure_ascii=False),
                ),
            )

    # Sprint C.1:refine 成功 → 扣 credit(按真实 token 数算)
    # 失败不阻塞 refine 结果(log 一行 warning,运维 audit 跟进)
    try:
        units = credit_units_for_text_call(
            usage["input_tokens"], usage["output_tokens"]
        )
        consume_credits(
            conn,
            user_id=user_id,
            action="refine",
            units=units,
            related_id=session_id,
            cost_yuan=cost_yuan,
            metadata={
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
                "vendor": "deepseek",
            },
        )
    except Exception as e:  # noqa: BLE001
        import logging
        logging.warning("consume_credits(refine) 失败但不影响 refine 结果: %s", e)

    return {
        "session_id": session_id,
        "refinements": [
            {
                "id": ref_id,
                "character_id": item["character_id"],
                "character_name": char_lookup[item["character_id"]].name,
                "suggestion_kind": item["suggestion_kind"],
                "suggestion_text": item["suggestion_text"],
                "suggestion_payload": item["suggestion_payload"],
                "status": "pending",
            }
            for ref_id, item in zip(refinement_ids, filtered)
        ],
        "stats": {
            "characters_count": len(characters),
            "refinements_count": len(filtered),
            "tokens": usage,
            "cost_yuan": round(cost_yuan, 4),
            "duration_ms": duration_ms,
            "filtered_out": len(parsed) - len(filtered),
        },
    }


# ======================================================================
# §16 兜底过滤(②③组合)— LLM 输出落库前过滤
# ======================================================================

def filter_llm_output(
    items: list,
    char_lookup: dict[str, Character],
    rel_id_set: set[str] | None = None,
) -> list[dict]:
    """对 LLM 输出做三层兜底过滤,返回有效的 refinements 列表。

    保护层:
      ② 已知作品专属词拦截 — 输入李寻欢时,过滤含"飞刀/百晓生/..."的建议
      ③ 语义自相矛盾过滤 — suggestion_text 说"是空的"但实际非空
      + 基础 schema 校验(suggestion_kind / character_id 合法等)
      + 数量上限 ≤ MAX_REFINEMENTS_PER_SESSION

    Sprint 6.A2 M1(2026-05-18):
      + evolution_hint payload.relationship_id 必须在 rel_id_set 里;
        否则 LLM 错填了 source_id→target_id 拼接串 → 整条 evolution_hint 丢弃。
        语义:rel_id_set=None → 跳过此校验(向后兼容老调用方);
              rel_id_set=set() → 严格校验,任何 evolution_hint 都被丢弃。
    """
    filtered: list[dict] = []

    for item in items:
        if not isinstance(item, dict):
            continue

        cid = item.get("character_id")
        kind = item.get("suggestion_kind")
        text = item.get("suggestion_text", "")
        payload = item.get("suggestion_payload", {})

        # 基础 schema 校验
        if cid not in char_lookup:
            continue
        if kind not in VALID_KINDS:
            continue
        if not isinstance(text, str) or not text:
            continue
        if not isinstance(payload, dict):
            continue

        char = char_lookup[cid]

        # 兜底预筛 ①(防御层):value 字段覆盖非空 → 直接过滤(避免落库后再触发异常)
        if "value" in payload and "field" in payload:
            field = payload["field"]
            # Sprint 6.A2 FOCUS(2026-05-21):value 允许的两类 field 路径:
            #   1) "identity"(铁律 5,只允许空字段填)
            #   2) "behavior_baseline.<subfield>" 中 speech_register / emotional_intensity /
            #      moral_compass(value 模式)
            # P0G.2(2026-05-24):out_of_baseline_examples 字段已删,任何引用都丢弃
            if field == "identity":
                current = (getattr(char, "identity", "") or "").strip()
                if current:
                    continue  # 已填 → 丢弃
            elif isinstance(field, str) and field.startswith("behavior_baseline."):
                sub = field.split(".", 1)[1] if "." in field else ""
                if sub not in {"speech_register", "emotional_intensity", "moral_compass"}:
                    continue  # 未知 / 已删 subfield 丢弃
                # 现状非空 → 丢弃(防覆盖)
                baseline = getattr(char, "behavior_baseline", None) or {}
                existing = baseline.get(sub) if isinstance(baseline, dict) else None
                if sub == "emotional_intensity":
                    if existing is not None:
                        continue
                else:
                    if existing:  # 非空字符串
                        continue
                # 枚举 / 范围校验(LLM 输出非法值 → 丢弃)
                val = payload["value"]
                if sub == "speech_register" and val not in _SPEECH_REGISTER_VALID:
                    continue
                if sub == "moral_compass" and val not in _MORAL_COMPASS_VALID:
                    continue
                if sub == "emotional_intensity":
                    if not isinstance(val, (int, float)):
                        continue
                    if int(val) < 1 or int(val) > 10:
                        continue
            else:
                continue  # value 只允许 identity / behavior_baseline.*

        # Sprint 6.A2 M1:evolution_hint 校验 relationship_id 真实存在
        # (LLM 可能拼成 "source_id→target_id" 假 id;不存在 → 丢弃整条)
        # rel_id_set=None → 跳过校验(向后兼容);rel_id_set=set/集合 → 严格匹配
        if kind == "evolution_hint" and rel_id_set is not None:
            rel_id = payload.get("relationship_id")
            if not isinstance(rel_id, str) or rel_id not in rel_id_set:
                import logging
                logging.warning(
                    f"evolution_hint dropped: relationship_id {rel_id!r} "
                    f"not in real relationships (LLM 凭空拼了假 id)"
                )
                continue

        # 兜底 ②:已知作品专属词
        if _contains_reserved_term(item, char_lookup):
            continue

        # 兜底 ③:语义自相矛盾
        if _is_self_contradictory(text, char):
            continue

        filtered.append(item)

        if len(filtered) >= MAX_REFINEMENTS_PER_SESSION:
            break

    return filtered


def _contains_reserved_term(
    item: dict, char_lookup: dict[str, Character]
) -> bool:
    """如果输入角色集合里有"已知作品角色名",则该作品所有 reserved_terms 被禁用。

    例:输入 c1=李寻欢,session 内任何建议含"飞刀/百晓生/林诗音/..."都被拒绝。
    双向:孙小红/上官金虹的 reserved_terms 也合入禁用集。

    重要细节:**用户主动起的角色名要从禁用集里剔除**。
    因为如果用户既起了"李寻欢"又起了"上官金虹",显然他想要这两个角色都存在,
    给上官金虹补 personality 时不能因为字典里"李寻欢→上官金虹"的禁用关系而拒绝。
    """
    forbidden: set[str] = set()
    for c in char_lookup.values():
        if c.name in KNOWN_WORK_RESERVED_TERMS:
            forbidden.update(KNOWN_WORK_RESERVED_TERMS[c.name])

    # 用户主动选择的角色名不算 reserved(允许提及自己创建的角色)
    user_chosen_names = {c.name for c in char_lookup.values()}
    forbidden -= user_chosen_names

    if not forbidden:
        return False

    haystack = json.dumps(item, ensure_ascii=False)
    return any(term in haystack for term in forbidden)


def _is_self_contradictory(text: str, char: Character) -> bool:
    """suggestion_text 自陈"是空的"但 character 对应字段实际非空 → 拒绝。

    最常见的误判模式:LLM 把 "调查记者"4 字截取为短串,误说"identity 是空的"
    但 char.identity 实际是"新生代调查记者,擅长社会工程学"15 字非空。
    """
    # 简化版:任何 SELF_CONTRADICTION_PHRASES 出现 + identity 实际非空 → 拒绝
    # (其他字段的"是空的"误判较少见,先只防 identity)
    if char.identity and char.identity.strip():
        for phrase in SELF_CONTRADICTION_PHRASES:
            if phrase in text:
                return True
    return False


# ======================================================================
# 主流程 ② — action_refinement(accept / reject / edit)
# ======================================================================

def action_refinement(
    conn: sqlite3.Connection,
    refinement_id: str,
    user_id: str,
    action: str,
    user_edit: dict | None = None,
) -> dict:
    """处理一条 refinement。

    异常:
      ResourceNotFoundOrForbidden — refinement 不属于当前用户
      RefinementAlreadyActioned   — refinement 已处理过
      WouldOverwriteNonEmpty      — §16 兜底 ① 硬实施:value 覆盖非空字段
    """
    # 鉴权:JOIN 查 refinement → session → user_id
    row = fetch_one(
        conn,
        "SELECT cr.id, cr.character_id, cr.suggestion_payload, cr.status "
        "FROM character_refinements cr "
        "JOIN refine_sessions rs ON rs.id = cr.session_id "
        "WHERE cr.id=? AND rs.user_id=?",
        (refinement_id, user_id),
    )
    if not row:
        raise ResourceNotFoundOrForbidden("refinement", refinement_id)
    if row["status"] != "pending":
        raise RefinementAlreadyActioned(
            f"refinement 状态已为 {row['status']!r},不能再操作"
        )

    char_row = fetch_one(
        conn, "SELECT * FROM characters WHERE id=?", (row["character_id"],)
    )
    char = Character.from_row(char_row)

    payload = json.loads(row["suggestion_payload"])
    applied_payload = user_edit if (action == "edit" and user_edit) else payload

    if action in ("accept", "edit"):
        _apply_payload_to_character(conn, char, applied_payload)

    # action(动词)→ status(已完成状态形容词)的映射
    # 必须对齐 migration 006 character_refinements.status CHECK 约束
    ACTION_TO_STATUS = {
        "accept": "accepted",
        "reject": "rejected",
        "edit":   "edited",
    }
    final_status = ACTION_TO_STATUS[action]

    actioned_at = iso_now()
    user_edit_json = json.dumps(user_edit, ensure_ascii=False) if user_edit else None
    execute(
        conn,
        "UPDATE character_refinements "
        "SET status=?, user_edit=?, actioned_at=? WHERE id=?",
        (final_status, user_edit_json, actioned_at, refinement_id),
    )
    conn.commit()

    return {
        "id": refinement_id,
        "status": final_status,
        "applied_to_character": action in ("accept", "edit"),
    }


def _apply_payload_to_character(
    conn: sqlite3.Connection, char: Character, payload: dict
) -> None:
    """实际改 characters 表。**§16 兜底 ① 硬实施在此**:value 覆盖非空字段 → raise。"""
    if payload.get("kind") == "warning":
        # consistency_警告 不带写库 payload;accept 时无 user_edit 等于空操作
        return

    field = payload.get("field")
    if not field:
        return

    # Sprint 6.A2 FOCUS(2026-05-21):behavior_baseline.<subfield> 分支
    # field 形如 "behavior_baseline.speech_register" → 解析 subfield → 写 behavior_baseline_json
    if isinstance(field, str) and field.startswith("behavior_baseline."):
        _apply_behavior_baseline_payload(conn, char, field, payload)
        return

    if "value" in payload:
        # === §16 兜底 ① 硬实施 ===
        if field != "identity":
            return  # value 仅允许 identity(铁律 5)
        current_identity = (char.identity or "").strip()
        if current_identity:
            raise WouldOverwriteNonEmpty(field, current_identity)
        execute(
            conn,
            "UPDATE characters SET identity=?, updated_at=? WHERE id=?",
            (payload["value"], iso_now(), char.id),
        )
    elif "append" in payload:
        appended = payload["append"]
        now = iso_now()
        if field == "personality":
            existing = char.personality or ""
            new_value = (
                (existing + " " + appended).strip() if existing else appended
            )
            execute(
                conn,
                "UPDATE characters SET personality=?, updated_at=? WHERE id=?",
                (new_value, now, char.id),
            )
        elif field == "quotes":
            new_list = list(char.quotes or []) + (
                appended if isinstance(appended, list) else [appended]
            )
            execute(
                conn,
                "UPDATE characters SET quotes=?, updated_at=? WHERE id=?",
                (json.dumps(new_list, ensure_ascii=False), now, char.id),
            )
        elif field == "no_go_list":
            new_list = list(char.no_go_list or []) + (
                appended if isinstance(appended, list) else [appended]
            )
            execute(
                conn,
                "UPDATE characters SET no_go_list=?, updated_at=? WHERE id=?",
                (json.dumps(new_list, ensure_ascii=False), now, char.id),
            )


def _apply_behavior_baseline_payload(
    conn: sqlite3.Connection, char: Character, field: str, payload: dict
) -> None:
    """Sprint 6.A2 FOCUS(2026-05-21):应用 behavior_baseline_补充 payload。

    P0G.2(2026-05-24):删 out_of_baseline_examples 子字段 — append 模式同时移除。
    现 3 子字段全走 value 模式:

    field 形如 "behavior_baseline.<subfield>":
      - speech_register  (value 枚举,只允许空字段填)
      - emotional_intensity  (value 1-10,只允许 null 填)
      - moral_compass  (value 枚举,只允许空字段填)

    behavior_baseline 是 JSON 列(behavior_baseline_json),读出 dict / 改子字段 / 写回。
    """
    subfield = field.split(".", 1)[1] if "." in field else ""
    if subfield not in _BEHAVIOR_BASELINE_FIELDS:
        return  # 未知 / 已删 subfield 静默拒绝(含 out_of_baseline_examples)

    # 读出当前 behavior_baseline dict(None / 空 → 空 dict)
    current = dict(char.behavior_baseline) if char.behavior_baseline else {}
    current_sub = current.get(subfield)

    if "value" in payload:
        new_value = payload["value"]

        # === §16 兜底 ①:子字段已填 → 拒绝覆盖 ===
        if subfield in ("speech_register", "moral_compass"):
            if current_sub:  # 非空字符串
                raise WouldOverwriteNonEmpty(field, str(current_sub))
            # 枚举白名单校验
            valid_set = (
                _SPEECH_REGISTER_VALID
                if subfield == "speech_register"
                else _MORAL_COMPASS_VALID
            )
            if new_value not in valid_set:
                return  # 非法枚举静默拒绝
        elif subfield == "emotional_intensity":
            if current_sub is not None:
                raise WouldOverwriteNonEmpty(field, str(current_sub))
            # 1-10 整数校验
            if not isinstance(new_value, (int, float)):
                return
            ivalue = int(new_value)
            if ivalue < 1 or ivalue > 10:
                return
            new_value = ivalue

        current[subfield] = new_value
        _write_behavior_baseline(conn, char.id, current)
        return

    # P0G.2:不再支持 append 模式(原仅 out_of_baseline_examples 用,现已删)
    # LLM 输出 append 时静默丢弃


def _write_behavior_baseline(
    conn: sqlite3.Connection, character_id: str, baseline: dict
) -> None:
    """写 behavior_baseline_json 列。空 dict → 写 NULL(对齐"老数据"语义)。"""
    raw = json.dumps(baseline, ensure_ascii=False) if baseline else None
    execute(
        conn,
        "UPDATE characters SET behavior_baseline_json=?, updated_at=? WHERE id=?",
        (raw, iso_now(), character_id),
    )


# ======================================================================
# 主流程 ③ — skip_session
# ======================================================================

def skip_session(
    conn: sqlite3.Connection, session_id: str, user_id: str, reason: str
) -> dict:
    """跳过整个 session。把所有 pending 标记 skipped + 记 completed_at。"""
    row = fetch_one(
        conn,
        "SELECT id, completed_at FROM refine_sessions WHERE id=? AND user_id=?",
        (session_id, user_id),
    )
    if not row:
        raise ResourceNotFoundOrForbidden("refine_session", session_id)
    if row["completed_at"]:
        raise RefinementAlreadyActioned(
            f"session 已完成 ({row['completed_at']})"
        )

    completed_at = iso_now()
    with transaction(conn) as tx:
        cur = tx.execute(
            "UPDATE character_refinements "
            "SET status='skipped', actioned_at=? "
            "WHERE session_id=? AND status='pending'",
            (completed_at, session_id),
        )
        skipped_count = cur.rowcount

        execute(
            tx,
            "UPDATE refine_sessions SET completed_at=?, skip_reason=? WHERE id=?",
            (completed_at, reason, session_id),
        )

    return {"skipped": True, "remaining_refinements": skipped_count}
