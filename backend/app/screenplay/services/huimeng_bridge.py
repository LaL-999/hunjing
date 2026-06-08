"""浑晶桥接层 — 把剧创态 6 个 agent 接通父平台 SP-2/3/4/7 资产。

这是阶段 5 的核心(2026-06-08)。
**不可妥协**:产品差异化命脉。决定剧创态是"独立 SaaS"还是"浑晶真护城河"。

---

接通的父平台资产(全部为可选 — 父平台不强制存在,bridge 失败必须降级):

| 父平台资产 | sprint | 用途 |
|---|---|---|
| `characters.surface_goal/deep_need/fatal_blind_spot/arc_from_to/secret_json` | SP-2 | 角色驱动力 —— 想要 vs 需要 vs 秘密 |
| `story_facts` + `character_knowledge` | SP-3 | 知识边界 —— 角色不说不知道的事 |
| `character_state_snapshots` | SP-4 | 状态时间线 —— 跨场一致性检查(优化器用)|
| `relationships.polarity` | SP-7 | 关系正负极 —— 互称语气 / 戏剧张力 |

---

设计原则:

1. **异常隔离**:每个 get_* 函数失败(浑晶 service 抛错 / 表不存在 / 字段缺失)
   一律返空块(`""` for prompt block, `[]` for list, `None` for single)— **绝不阻断**
   剧创态 LLM 调用。剧本质量降级但仍能出片。

2. **解析 link**:`_resolve_project_id(novel_id)` 先看 `sp_novels.linked_project_id`;
   若 NULL,启发式扫该用户所有 project 找同名角色匹配(暂未实现,先返 None
   表"未链接"— 这意味着未绑定的小说不享受桥接增益,用户须主动 link)。

3. **缓存**:同一次 compose 内调用 6 agent 共 ~30 次,每次都跑相同的"角色名→
   driver"查询太浪费。bridge 用 functools.lru_cache 装饰内部 helper(注意:
   project_id 必须在 key 里,否则跨项目串数据)。

4. **只读**:bridge 不写父平台数据。剧创态发现的"新事实"不回灌父平台(避免
   污染用户的浑晶项目)。

5. **角色名匹配**:剧本里的角色名可能与父平台 characters.name 略不同
   (繁简 / 别名 / 全名 vs 简称)。bridge 走两级匹配:
     a. 精确 name 匹配(SQL `IN`)
     b. 启发式 fallback —— TODO,先 a 兜底

API 风格:
  - 输入:`conn`(显式连接,可复用)+ `user_id`(防越权)+ `novel_id` + 必要业务参数
  - 输出:**prompt 可直接拼接的 markdown 块** 或 **JSON-serializable dict/list**
  - 不抛异常给上游 — 内部 catch + log + 返空
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Optional

from app.db import fetch_one


logger = logging.getLogger(__name__)


# ============================================================
# 内部:解析 novel → project_id
# ============================================================

def _resolve_project_id(
    conn: sqlite3.Connection,
    user_id: str,
    novel_id: str,
) -> Optional[str]:
    """从 sp_novels 拉 linked_project_id(校验归属当前用户)。

    Returns:
        project_id 字符串 / None(未绑定 或 novel 不属于该用户)

    隔离:必须校验 user_id,防止越权拉别人小说的 link 信息。
    """
    try:
        row = fetch_one(
            conn,
            "SELECT linked_project_id FROM sp_novels WHERE id=? AND user_id=?",
            (novel_id, user_id),
        )
        if row is None:
            return None
        return row["linked_project_id"] or None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"bridge: resolve project_id failed: {e}")
        return None


# ============================================================
# 1. SP-2 角色驱动力(想要 vs 需要 vs 秘密)
# ============================================================

def get_character_drivers_block(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    novel_id: str,
    character_names: list[str],
) -> str:
    """构造"角色驱动力"prompt 块(markdown)。

    复用父平台 `character_drivers_util.build_character_drivers_block`,
    把它接到剧创态 agent 的 prompt 链路里。

    Args:
        character_names: 本场景在场角色名

    Returns:
        非空 markdown 块 / "" (未 link / 角色都没填驱动字段 / 异常)

    使用场景:
      - element_extractor:让 LLM 抽对白时给角色注入"想要 vs 需要"张力
      - dialogue_attributor:模糊归属时用 driver 判定该是谁说的
      - adaptation_decision:改编决策考虑角色秘密会不会泄露
    """
    if not character_names:
        return ""
    project_id = _resolve_project_id(conn, user_id, novel_id)
    if not project_id:
        return ""
    try:
        from app.services.character_drivers_util import build_character_drivers_block
        return build_character_drivers_block(conn, project_id, character_names)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"bridge: character_drivers failed: {e}")
        return ""


# ============================================================
# 2. SP-3 知识边界(信息不对称)
# ============================================================

def get_character_knowledge_block(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    novel_id: str,
    character_names: list[str],
    current_scene_index: Optional[int] = None,
) -> str:
    """构造"知识边界"prompt 块(markdown)。

    复用父平台 `character_knowledge_util.build_knowledge_block`。

    Args:
        current_scene_index: 当前场景索引(过滤"还没到的幕"的信息)

    Returns:
        非空 markdown 块 / "" (未 link / 项目没事实 / 异常)

    使用场景:
      - element_extractor:让角色不说他不知道的事 — AI 写作最大连贯 bug
      - scene_splitter:某场涉及未知事实首次得知瞬间 → 自然形成场景边界
      - adaptation_decision:改编决策考虑秘密暴露(决策的关键判定)
    """
    if not character_names:
        return ""
    project_id = _resolve_project_id(conn, user_id, novel_id)
    if not project_id:
        return ""
    try:
        from app.services.character_knowledge_util import build_knowledge_block
        return build_knowledge_block(
            conn, project_id, character_names,
            current_scene_index=current_scene_index,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"bridge: character_knowledge failed: {e}")
        return ""


# ============================================================
# 3. SP-4 状态时间线快照(跨场一致性)
# ============================================================

def get_character_snapshots_block(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    novel_id: str,
    character_names: list[str],
) -> str:
    """构造"角色状态时间线快照"prompt 块。

    SP-4 snapshot 是 simulation_id 维度的(continuation 创作态的产物),
    剧创态没有 simulation_id。本 fn 走启发式:返该项目最近一次 simulation
    的最末场快照 — 等于"角色当前状态"。若用户没跑过推演,返 ""。

    使用场景:
      - screenplay_optimizer:跨场对比角色 emotion/position/inventory,
        检测人设漂移(同一角色在 第3场和第15场行为冲突 = 优化提示)

    Returns:
        非空 markdown 块 / "" (未 link / 无 simulation / 异常)
    """
    if not character_names:
        return ""
    project_id = _resolve_project_id(conn, user_id, novel_id)
    if not project_id:
        return ""
    try:
        # 1. 找该项目最近完成的 simulation
        # 注:simulations 表字段叫 `state`(不是 status),CHECK 取值含 'done'
        # bug fix 2026-06-08:原来写 status='done' 会 silent fail(SQL no such column),
        # bridge try/except 吞错 → SP-4 资产永远拉不到。
        sim = fetch_one(
            conn,
            "SELECT id FROM simulations "
            "WHERE project_id=? AND state='done' "
            "ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        )
        if sim is None:
            return ""
        sim_id = sim["id"]

        # 2. 拿在场角色的 id
        placeholders = ",".join(["?"] * len(character_names))
        rows = conn.execute(
            f"SELECT id, name FROM characters "
            f"WHERE project_id=? AND name IN ({placeholders})",
            (project_id, *character_names),
        ).fetchall()
        if not rows:
            return ""

        # 3. 对每个角色拿最新快照
        from app.services.character_snapshot_service import get_latest_snapshot
        char_blocks: list[str] = []
        for r in rows:
            snap = get_latest_snapshot(conn, sim_id, r["id"])
            if snap is None:
                continue
            parts = [f"· {r['name']}:"]
            if snap.get("position"):
                parts.append(f"    位置:{snap['position']}")
            if snap.get("hp_status"):
                parts.append(f"    身体状态:{snap['hp_status']}")
            if snap.get("status_note"):
                parts.append(f"    备注:{snap['status_note']}")
            emo = snap.get("emotion_vec")
            if emo and isinstance(emo, dict):
                # 挑前 2 个最强情绪
                top = sorted(emo.items(), key=lambda kv: -float(kv[1]))[:2]
                emo_str = " / ".join(f"{k}={v:.1f}" for k, v in top)
                parts.append(f"    主导情绪:{emo_str}")
            inv = snap.get("inventory")
            if inv and isinstance(inv, list):
                parts.append(f"    随身物品:{', '.join(str(x) for x in inv[:5])}")
            if len(parts) > 1:  # 至少有一条状态信息
                char_blocks.append("\n".join(parts))

        if not char_blocks:
            return ""

        out_lines = [
            "【角色当前状态(来自浑晶推演时间线 — 跨场一致性 baseline)】",
        ]
        out_lines.extend(char_blocks)
        out_lines.append(
            "\n  ⚠ 用法:\n"
            "    1. 优化场景时,新场角色的位置 / 情绪 / 物品**必须从这条 baseline 演化**,\n"
            "       不许凭空闪现到不可能的地点 / 装备\n"
            "    2. 情绪向量是趋势 — 一场内允许小幅波动,但反向跳变需戏剧动机支撑"
        )
        return "\n".join(out_lines)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"bridge: character_snapshots failed: {e}")
        return ""


# ============================================================
# 4. SP-7 关系正负极
# ============================================================

def get_relationship_polarity_block(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    novel_id: str,
    character_names: list[str],
) -> str:
    """构造"关系正负极"prompt 块(markdown)。

    查 relationships 表,过滤本场在场角色之间的关系,输出 polarity。

    Args:
        character_names: 本场在场角色名(N×N 关系矩阵会被截断到这 N 个内)

    使用场景:
      - scene_splitter:场景切分判定 — 情感转折点(positive→negative)= 场切
      - element_extractor:互称语气 — 夫妻 vs 仇敌互相称呼差异巨大
      - dialogue_attributor:模糊归属 — 用关系类型 + polarity 帮判定

    Returns:
        非空 markdown 块 / "" (未 link / 关系不全 / 异常)
    """
    if len(character_names) < 2:
        return ""  # 少于 2 角色没有关系可谈
    project_id = _resolve_project_id(conn, user_id, novel_id)
    if not project_id:
        return ""
    try:
        placeholders = ",".join(["?"] * len(character_names))
        # 拿在场角色的 id + name
        char_rows = conn.execute(
            f"SELECT id, name FROM characters "
            f"WHERE project_id=? AND name IN ({placeholders})",
            (project_id, *character_names),
        ).fetchall()
        if len(char_rows) < 2:
            return ""

        char_ids = [r["id"] for r in char_rows]
        char_id_to_name = {r["id"]: r["name"] for r in char_rows}

        id_placeholders = ",".join(["?"] * len(char_ids))
        rel_rows = conn.execute(
            f"SELECT source_id, target_id, type, polarity, description "
            f"FROM relationships "
            f"WHERE project_id=? "
            f"  AND source_id IN ({id_placeholders}) "
            f"  AND target_id IN ({id_placeholders})",
            (project_id, *char_ids, *char_ids),
        ).fetchall()
        if not rel_rows:
            return ""

        # 输出
        rel_lines: list[str] = []
        for r in rel_rows:
            src = char_id_to_name.get(r["source_id"], "?")
            tgt = char_id_to_name.get(r["target_id"], "?")
            rel_type = r["type"] or "未知"
            polarity = r["polarity"]
            polarity_label = {
                "positive": "正向(亲密 / 喜爱 / 互相支持)",
                "negative": "负向(敌对 / 怨恨 / 互相伤害)",
                "neutral": "中性(平等 / 公事 / 不带感情)",
            }.get(polarity or "", "未标")
            desc = (r["description"] or "").strip()
            line = f"· {src} → {tgt} 【{rel_type}】 {polarity_label}"
            if desc:
                line += f" — {desc}"
            rel_lines.append(line)

        out = [
            "【人物关系(SP-7 父平台资产)— 本场在场角色之间的连接】",
        ]
        out.extend(rel_lines)
        out.append(
            "\n  ⚠ 铁律:\n"
            "    1. 互称语气必须匹配 polarity —— positive 互称用昵称 / 亲密称谓;\n"
            "       negative 直呼姓名甚至贬称;neutral 用正式头衔或全名\n"
            "    2. 同一段对话内 polarity 不许悄悄翻转 —— 翻转必须 explicit 戏剧动机\n"
            "       (一句话或一动作触发)+ 场切建议"
        )
        return "\n".join(out)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"bridge: relationship_polarity failed: {e}")
        return ""


# ============================================================
# 5. SP-3 故事事实(项目级)
# ============================================================

def get_story_facts_block(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    novel_id: str,
    limit: int = 20,
) -> str:
    """构造"故事事实"prompt 块。

    用法:让剧创态不编造原作没有的细节。原作里"林叔叔有间钥匙工坊" =
    一个 story_fact,剧创态不许把它说成"鞋匠铺"。

    Returns:
        非空 markdown 块 / "" (未 link / 项目无事实 / 异常)
    """
    project_id = _resolve_project_id(conn, user_id, novel_id)
    if not project_id:
        return ""
    try:
        from app.services.character_knowledge_service import list_project_facts
        facts = list_project_facts(conn, project_id)
        if not facts:
            return ""
        facts = facts[:limit]

        lines = [
            "【故事事实(SP-3 父平台资产)— 原作锚定的硬事实】",
        ]
        for f in facts:
            sens_mark = "🔒" if f.get("is_sensitive") else "·"
            scene_mark = ""
            fr = f.get("first_revealed_scene")
            if isinstance(fr, int):
                scene_mark = f"(首次出现:第 {fr} 幕)"
            lines.append(f"  {sens_mark} {f.get('description', '')}{scene_mark}")
        lines.append(
            "\n  ⚠ 铁律:\n"
            "    1. 上述事实是原作锚定 — 剧本改编不许擅自篡改细节\n"
            "       (例:原作「钥匙工坊」不许写成「鞋匠铺」)\n"
            "    2. 🔒 标记的是敏感事实(角色秘密 / 关键剧情触发)— 暴露需 outline 规划"
        )
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"bridge: story_facts failed: {e}")
        return ""


# ============================================================
# 6. 链接信息查询(轻量,给 storage agent 复用预存的角色)
# ============================================================

def get_linked_characters(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    novel_id: str,
) -> list[dict]:
    """返链接项目的角色清单(给 story_bible_extractor 复用)。

    用法:用户已经在浑晶建过该故事的角色档 → story_bible_extractor 不必从 0
    重抽,而是直接复用 characters 表的 name / identity / personality。

    Returns:
        list[{id, name, identity, personality, aka_json, ...}] / [] (未 link / 异常)
    """
    project_id = _resolve_project_id(conn, user_id, novel_id)
    if not project_id:
        return []
    try:
        rows = conn.execute(
            "SELECT id, name, identity, personality, "
            "       surface_goal, deep_need, secret_json "
            "FROM characters WHERE project_id=? "
            "ORDER BY name",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"bridge: linked_characters failed: {e}")
        return []


# ============================================================
# 链接管理 — endpoint 用
# ============================================================

def link_novel_to_project(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    novel_id: str,
    project_id: Optional[str],
) -> bool:
    """设置 / 清除 sp_novels.linked_project_id。

    Args:
        project_id: 非空 → 校验该 project 属于当前用户后 link;
                    None  → 清除链接

    Returns:
        True 操作成功 / False novel 不存在或不属于该用户,或 project 不属于该用户
    """
    try:
        # 校验 novel 归属
        own = fetch_one(
            conn,
            "SELECT 1 FROM sp_novels WHERE id=? AND user_id=?",
            (novel_id, user_id),
        )
        if own is None:
            return False

        if project_id is not None:
            # 校验 project 归属
            proj = fetch_one(
                conn,
                "SELECT 1 FROM projects WHERE id=? AND user_id=?",
                (project_id, user_id),
            )
            if proj is None:
                return False

        conn.execute(
            "UPDATE sp_novels SET linked_project_id=? WHERE id=? AND user_id=?",
            (project_id, novel_id, user_id),
        )
        conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"bridge: link_novel_to_project failed: {e}")
        return False


__all__ = [
    "get_character_drivers_block",
    "get_character_knowledge_block",
    "get_character_snapshots_block",
    "get_relationship_polarity_block",
    "get_story_facts_block",
    "get_linked_characters",
    "link_novel_to_project",
]
