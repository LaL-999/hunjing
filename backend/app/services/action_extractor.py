"""Sprint 6.A2 M5.2(2026-05-20)— Action Ledger Extractor(原子动作流水)。

每幕 narrator 合稿后调用,LLM 从 narrative_segment 抽取关键原子动作。

核心逻辑:
  - 动作三元组(actor + verb + object_or_target)
  - is_repeatable 判定:
    * 找到 / 拨打 / 发送 / 射击 / 击杀 / 拆开 / 解开 → 不可重复(0)
    * 说话 / 走动 / 看一眼 / 点头 → 可重复(1)
  - LLM 有疑议时默认严格(0)— 用户体验:误判"可重复"导致跨幕复制 = 比误判"不可重复"略限制创作 更坏

API:
  - extract_actions_from_segment(conn, sim, scene_index, narrative, agents) → tuple[int, dict]
    返回 (落库 action 数量, llm_usage)
  - list_atomic_actions(conn, simulation_id) → list[ActionLedgerEntry]
    给 narrator/agent_dialogue 注入 "禁止重复" 用(只返 is_repeatable=0 的)
"""
from __future__ import annotations

import logging
import sqlite3
import uuid
from pathlib import Path
from typing import Optional

from app.db import execute, fetch_all, fetch_one
from app.models.action_ledger import ActionLedgerEntry
from app.models.character import Character
from app.models.simulation import Simulation
from app.services.llm_client import call_llm_json
from app.services.project_service import iso_now

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parents[3] / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


# M11.C / M11.F.2(2026-05-24)— 一次性事件关键词兜底清单。
#
# **主路径**:prompts/m5_action_extractor.md 让 LLM 用"三大判定属性"
# (终止性 / 决定性 / 唯一性)主动判定 — 这是真治本的通用规则,覆盖所有作品风格
# (文学 / 科幻 / 武侠 / 古风 / 现代都市)。
#
# **兜底路径(本清单)**:LLM 偶尔漏判时,中文关键词字符串匹配强制覆盖。
# 覆盖范围按题材分组,**已穷尽常见高频词**,但永远不能 100% 完整 —
# 真正普适性靠 prompt 层的抽象规则,本清单仅作"网漏之鱼"兜底。
_ONE_TIME_EVENT_KEYWORDS: tuple[str, ...] = (
    # ─── 通用 ───
    "第一次", "初次", "首次",  # 唯一性标记
    # ─── 信件 / 通讯类(终止性 — 一封信只发一次)───
    "写信", "寄信", "寄出", "投递", "贴邮票", "封口",
    "拨电话", "拨通", "接通",   # 通讯
    # ─── 告别 / 死亡 / 葬礼(终止性 — 不可逆)───
    "告别", "道别", "永别", "诀别", "送别",
    "死亡", "死去", "去世", "离世", "丧命", "毙命", "断气", "咽气",
    "葬礼", "出殡", "下葬", "悼念", "守灵", "祭奠",
    "婚礼", "结婚", "迎亲", "拜堂",
    # ─── 决定性表态(现代题材)───
    "辞职", "提分手", "求婚", "表白", "分手", "离婚",
    # ─── 科幻题材关键词(决定性 / 终止性)───
    "发射", "启动自毁", "切断通讯", "上传意识", "封存", "格式化",
    "按下按钮", "启动信标", "引爆",  # 决定性操作
    # ─── 武侠 / 古风题材(终止性 / 决定性)───
    "立誓", "立下毒誓", "金盆洗手", "拜师", "收徒", "出师",
    "殒命", "自刎", "自尽", "殉情", "殉国", "羽化",
    "出家", "削发", "还俗", "归隐",
    # ─── 仪式 / 典礼(唯一性)───
    "加冠", "及笄", "登基", "即位", "退位", "废储", "立储", "册封",
    # ─── 决定性书面行为 ───
    "立下", "签下", "落款", "盖章", "盖印", "盖玉玺",
    "绝笔", "遗书", "遗言", "宣判", "宣告", "宣布",
)


def _is_one_time_event(
    verb: str, object_name: Optional[str], description: str,
) -> bool:
    """判定该原子动作是否属于"一次性事件白名单"。

    用 verb + object_name + description 拼起来做关键词匹配。
    命中即视为一次性事件,is_repeatable 强制覆盖为 0(不可重复)。
    """
    text = " ".join(filter(None, [verb, object_name or "", description]))
    return any(kw in text for kw in _ONE_TIME_EVENT_KEYWORDS)


# P0U.3(2026-05-24)— 代词 actor 黑名单。
# LLM 偶尔违反 m5 prompt 铁律 3 用代词代替本名(如 actor_name="他"),
# 这会让 list_saturated_prop_usages 的 GROUP BY 把同一人当多人,
# **绕过道具消费冷却检测** — 治不了"摸烟划火望火光"L9/L21 改主语复读。
#
# 程序兜底:抽到代词 actor 直接**丢弃这条 action**(不归一化,因为
# Character model 没 gender 字段,无法靠性别消歧)。
# LLM-first 在 prompt 层处理,程序层只兜底 LLM 漏判的极端情况。
_PRONOUN_ACTOR_BLACKLIST: frozenset[str] = frozenset({
    "他", "她", "它", "他们", "她们", "它们",
    "这人", "那人", "某人", "有人",
    # 身份代称(narrator 偶尔会写,LLM 可能照抄):
    "老人", "年轻人", "中年人", "少年", "少女",
    "男人", "女人", "孩子", "客人",
})


def _is_pronoun_actor(actor_name: str) -> bool:
    """检测 actor_name 是否为代词 / 身份代称(非本名)。

    用 strict 字符串比对,避免误伤("老人头" / "年轻男子" 仍可能是真名)。
    """
    return actor_name.strip() in _PRONOUN_ACTOR_BLACKLIST


def extract_actions_from_segment(
    conn: sqlite3.Connection,
    sim: Simulation,
    scene_index: int,
    narrative_segment: str,
    agents: list[Character],
) -> tuple[int, dict]:
    """从一幕 narrative_segment 抽原子动作落 action_ledger。

    Returns:
      (inserted_count, llm_usage)
    """
    if not narrative_segment or len(narrative_segment.strip()) < 50:
        return 0, {"input_tokens": 0, "output_tokens": 0}

    # 拉已落库的不可重复动作作 baseline 给 LLM
    existing_atomic = [
        a.to_prompt_line() for a in list_atomic_actions(conn, sim.id)
    ]

    # 注:actor_entity_id 引用 canonical_entities.id(不是 characters.id)
    # 这里暂不做实体对齐(canonical_entities 是按 LLM 抽出 canonical_name 建的,
    # 和 characters 表无 1:1 映射),actor_entity_id 默认 NULL。
    # 未来可加 entity_alignment_service 做反查。

    user_input = {
        "scene_index": scene_index,
        "narrative_segment": narrative_segment,
        "agents_present": [
            {"id": a.id, "name": a.name} for a in agents
        ],
        "existing_atomic_actions": existing_atomic,
    }

    system_prompt = _load_prompt("m5_action_extractor.md")
    try:
        parsed, usage = call_llm_json(
            system_prompt, user_input,
            max_tokens=1200, temperature=0.2,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"action_extractor LLM failed sim={sim.id} scene={scene_index}: {e}"
        )
        return 0, {"input_tokens": 0, "output_tokens": 0}

    if not isinstance(parsed, dict):
        return 0, usage
    actions = parsed.get("actions") or []
    if not isinstance(actions, list):
        return 0, usage

    inserted = 0
    skipped_pronoun = 0
    now = iso_now()
    for act in actions:
        if not isinstance(act, dict):
            continue
        actor_name = str(act.get("actor_name") or "").strip()[:30]
        verb = str(act.get("verb") or "").strip()[:30]
        if not actor_name or not verb:
            continue

        # P0U.3(2026-05-24)— LLM 偶尔违反 prompt 铁律 3 用代词作 actor_name,
        # 这会让 GROUP BY 把同一人按代词分裂成多个,绕过道具消费冷却检测。
        # 程序兜底:代词 actor 直接丢弃,不入 ledger。
        if _is_pronoun_actor(actor_name):
            skipped_pronoun += 1
            logger.info(
                f"action_extractor: 跳过代词 actor_name={actor_name!r}(违反 P0U.3 铁律) "
                f"verb={verb!r} sim={sim.id} scene={scene_index}"
            )
            continue

        object_name_raw = act.get("object_name")
        object_name = (
            str(object_name_raw).strip()[:50]
            if object_name_raw and isinstance(object_name_raw, str)
            else None
        )
        description = str(act.get("description") or "").strip()[:300]
        if not description:
            continue
        is_repeatable_raw = act.get("is_repeatable")
        # 严格策略:LLM 没明确说 1 就默认 0(不可重复)
        is_repeatable = 1 if is_repeatable_raw is True else 0

        # M11.C(2026-05-24):一次性事件强制覆盖
        # 即便 LLM 误判 True,白名单命中(写信/告别/死亡/葬礼/婚礼/...)→ 强制 0
        if is_repeatable == 1 and _is_one_time_event(verb, object_name, description):
            is_repeatable = 0
            logger.info(
                f"action_extractor: 一次性事件白名单命中,强制 is_repeatable=0 "
                f"(verb={verb!r}, object={object_name!r}, sim={sim.id} scene={scene_index})"
            )

        execute(
            conn,
            """INSERT INTO action_ledger
               (id, simulation_id, scene_index, actor_name, actor_entity_id,
                verb, object_name, object_entity_id, description,
                is_repeatable, created_at)
               VALUES (?, ?, ?, ?, NULL, ?, ?, NULL, ?, ?, ?)""",
            (
                uuid.uuid4().hex, sim.id, scene_index, actor_name,
                verb, object_name, description, is_repeatable, now,
            ),
        )
        inserted += 1

    conn.commit()
    if skipped_pronoun > 0:
        logger.warning(
            f"action_extractor: 共跳过 {skipped_pronoun} 条代词 actor 的 action "
            f"(LLM 违反 P0U.3 铁律,sim={sim.id} scene={scene_index})"
        )
    return inserted, usage


def list_atomic_actions(
    conn: sqlite3.Connection,
    simulation_id: str,
) -> list[ActionLedgerEntry]:
    """拉某 sim 全部不可重复(is_repeatable=0)的已发生原子动作。

    narrator/agent_dialogue 调用前注入"禁止重复"列表用。
    按 scene_index 升序。
    """
    rows = fetch_all(
        conn,
        """SELECT * FROM action_ledger
           WHERE simulation_id=? AND is_repeatable=0
           ORDER BY scene_index ASC, created_at ASC""",
        (simulation_id,),
    )
    return [ActionLedgerEntry.from_row(r) for r in rows]


def list_saturated_prop_usages(
    conn: sqlite3.Connection,
    simulation_id: str,
    *,
    since_scene_index: int = 0,
    threshold: int = 3,
) -> list[dict]:
    """找出"道具消费饱和"的 actor+verb+object 三元组(M11.A 道具消费冷却用)。

    返回过去 since_scene_index 至今,同一 (actor, verb, object) 三元组出现
    ≥ threshold 次的清单。**包含 is_repeatable=1 的"日常动作"**,因为薄荷糖
    锁死的根因正是 LLM 反复消费同一可重复动作。

    返回 list[{actor, verb, object, count, last_scene}]。
    """
    rows = fetch_all(
        conn,
        """SELECT actor_name, verb, COALESCE(object_name, '') AS object_name,
                  COUNT(*) AS cnt, MAX(scene_index) AS last_scene
           FROM action_ledger
           WHERE simulation_id = ? AND scene_index >= ?
           GROUP BY actor_name, verb, COALESCE(object_name, '')
           HAVING COUNT(*) >= ?
           ORDER BY cnt DESC, last_scene DESC""",
        (simulation_id, since_scene_index, threshold),
    )
    return [
        {
            "actor": r["actor_name"],
            "verb": r["verb"],
            "object": r["object_name"] or "",
            "count": int(r["cnt"]),
            "last_scene": int(r["last_scene"]),
        }
        for r in rows
    ]


def count_actor_appearances(
    conn: sqlite3.Connection,
    simulation_id: str,
    actor_name: str,
) -> int:
    """该 actor 在 action_ledger 里出现的 distinct 幕数(M11.D 实体出场状态机用)。

    用途:hard_constraints 拼"该实体已交互 N 次"信号,治"神秘女孩两次第一次见面"瑕疵。
    """
    row = fetch_one(
        conn,
        "SELECT COUNT(DISTINCT scene_index) AS cnt FROM action_ledger "
        "WHERE simulation_id=? AND actor_name=?",
        (simulation_id, actor_name),
    )
    return int(row["cnt"]) if row else 0


def list_recent_actor_actions(
    conn: sqlite3.Connection,
    simulation_id: str,
    actor_names: list[str],
    *,
    since_scene_index: int = 0,
    limit: int = 20,
) -> list[ActionLedgerEntry]:
    """拉指定角色在最近 N 幕的原子动作(scene_picker 用,治"主角动作循环")。

    与 list_atomic_actions 的区别:
      - 多了 actor_names 过滤(只看主角,排除路人)
      - 多了 since_scene_index 过滤(只看最近 N 幕,远古动作无信号)
      - ORDER BY scene_index DESC(最新的在前,给 LLM 看高信号优先)
      - 不限 is_repeatable(scene_picker 关心"主角是不是在循环",可重复的"反复摸纸条"也算)

    场景:scene_picker 选下一幕场景时,要看主角"过去 5 幕都在干啥",
    如果都是"坐地板 / 摸纸条 / 想绿子",就该换场景打破循环。
    """
    if not actor_names:
        return []
    placeholders = ",".join(["?"] * len(actor_names))
    rows = fetch_all(
        conn,
        f"""SELECT * FROM action_ledger
            WHERE simulation_id=? AND scene_index>=? AND actor_name IN ({placeholders})
            ORDER BY scene_index DESC, created_at DESC
            LIMIT ?""",
        (simulation_id, since_scene_index, *actor_names, limit),
    )
    return [ActionLedgerEntry.from_row(r) for r in rows]
