"""明示陌生关系派生(2026-06-02 patch E).

治"角色关系闪现"创作质量瑕疵:用户 outline 写明"男主独立认识每个女主",
但 LLM 生成 narrative 时,看到多个女主在场会**自动假设她们互相认识**
→ "刘欣悦,你好,静怡姐" 这种闪现旧识称呼.

根因:relationships 表只记**已存在**的关系,**没记**"明示陌生"对.
LLM 没有显式的"这两个角色互不相识"信号 → 默认假设她们有联系.

修复(默认黑名单模式 — 用户拍板):
  - 项目内任意两个角色 × 未在 relationships 表声明关系 → 自动派生"陌生"对
  - hard_constraints 注入 section 1.7,LLM 必须严格遵守

API:
  derive_unfamiliar_pairs(conn, project_id) -> list[UnfamiliarPair]
    返回所有"应当作陌生人处理"的角色对(对子内顺序 a.id < b.id 防重)
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

from app.db import fetch_all


@dataclass
class UnfamiliarPair:
    """一对应当作陌生人处理的角色."""
    a_id: str
    a_name: str
    b_id: str
    b_name: str


def derive_unfamiliar_pairs(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    exclude_protagonist: bool = True,
    max_pairs: int = 30,
) -> list[UnfamiliarPair]:
    """派生 project 内所有"明示陌生"角色对.

    算法:
      1. 拉项目所有 characters
      2. 拉项目所有 relationships(source_id, target_id 对)
      3. 对所有两两组合(a, b),若 (a,b) 和 (b,a) 都不在 relationships → 加入陌生对

    Args:
      exclude_protagonist: True 时排除"包含主角的对" — 主角认识每个女主是已知的,
                          只关心配角之间的陌生关系(避免噪音)
      max_pairs: 上限,防 prompt 爆炸(20+ 角色的项目两两组合会爆 200+ 对)

    Returns:
      陌生对列表;对子内 a_id < b_id 保证幂等
    """
    char_rows = fetch_all(
        conn,
        "SELECT id, name, is_protagonist FROM characters "
        "WHERE project_id=? ORDER BY name",
        (project_id,),
    )
    if not char_rows or len(char_rows) < 2:
        return []

    # 主角 id set
    protagonist_ids: set[str] = set()
    if exclude_protagonist:
        for r in char_rows:
            try:
                if r["is_protagonist"]:
                    protagonist_ids.add(r["id"])
            except (KeyError, IndexError):
                pass

    rel_rows = fetch_all(
        conn,
        "SELECT source_id, target_id FROM relationships WHERE project_id=?",
        (project_id,),
    )
    known_pairs: set[tuple[str, str]] = set()
    for r in rel_rows:
        a, b = r["source_id"], r["target_id"]
        # 双向 normalize:无向对 (min, max)
        pair = (a, b) if a < b else (b, a)
        known_pairs.add(pair)

    # 算所有两两组合
    unfamiliar: list[UnfamiliarPair] = []
    chars = list(char_rows)
    for i in range(len(chars)):
        for j in range(i + 1, len(chars)):
            ca, cb = chars[i], chars[j]
            a_id, b_id = ca["id"], cb["id"]
            # 主角排除规则
            if exclude_protagonist and (a_id in protagonist_ids or b_id in protagonist_ids):
                continue
            # 已声明关系 → 跳过
            pair = (a_id, b_id) if a_id < b_id else (b_id, a_id)
            if pair in known_pairs:
                continue
            # 加入陌生对(对子内按 id 升序保证幂等)
            if a_id < b_id:
                unfamiliar.append(UnfamiliarPair(
                    a_id=a_id, a_name=ca["name"],
                    b_id=b_id, b_name=cb["name"],
                ))
            else:
                unfamiliar.append(UnfamiliarPair(
                    a_id=b_id, a_name=cb["name"],
                    b_id=a_id, b_name=ca["name"],
                ))
            if len(unfamiliar) >= max_pairs:
                return unfamiliar

    return unfamiliar


def build_unfamiliar_pairs_block(
    conn: sqlite3.Connection,
    project_id: str,
) -> str:
    """生成 hard_constraints 可注入的"明示陌生关系"段.

    返空字符串 → 无陌生对(项目角色少 / 关系网填得很全) → 不注入这个 section.
    非空 → 列出陌生对 + 4 条铁律.
    """
    pairs = derive_unfamiliar_pairs(conn, project_id)
    if not pairs:
        return ""

    lines = [
        "【明示陌生关系(本项目内显式声明:以下角色对互不相识)】",
        "  以下角色对在 relationships 关系表中**未声明任何关系** →",
        "  系统视为「彼此陌生 / 互不相识」,LLM 必须严格遵守:",
    ]
    for p in pairs:
        lines.append(f"    · 「{p.a_name}」 ⟷ 「{p.b_name}」")

    lines.append("")
    lines.append("  **明示陌生铁律**(违反 = 产出作废):")
    lines.append("    ① 同场出现时,他们**必须是初次见面** — 不许设定为旧相识 / 老熟人")
    lines.append("    ② **绝对禁止**互相直呼姓名 / 昵称 / 亲昵称呼(「静怡姐」/「淼淼」/「悦悦」等)")
    lines.append("       — 初次见面前不可能知道对方的名字 / 关系定位")
    lines.append("    ③ **绝对禁止**安排他们有共同回忆 / 共同朋友 / 共享秘密 / 互相调侃过往")
    lines.append("    ④ 若必须同场互动 → 必须经历**自我介绍**或**保持距离 / 局促 / 试探**等陌生人状态")
    lines.append("    ⑤ 若是首次互动,narrator 必须明确写「第一次见面」的氛围(打量 / 礼貌客套 / 简单介绍)")
    lines.append("    ⑥ 不许通过「我听 [主角] 提起过你」等**侧面熟络化**手法绕过 ② —")
    lines.append("       即使主角分别认识两人,两人之间仍然是陌生人,直到他们正式见面")

    return "\n".join(lines)


__all__ = [
    "UnfamiliarPair",
    "derive_unfamiliar_pairs",
    "build_unfamiliar_pairs_block",
]
