"""physical_constraints_util — P0X.2(2026-05-26)受限角色物理 / 语言能力共享 module.

起源:雪国实测在 4 份续作里(13574 / 14345 / 15070 / 15365)师傅 status_note=
"半身不遂"产生了 4 种**完全不同**的行为描写:
  - 13574:瘫床喂粥,含混 5 字断词
  - 14345:拄拐杖站门槛,说完整 3 句话
  - 15070:拄木杖步行 + 蹲下 + 站直,说 4 完整分句
  - 15365:半倚榻上,说 2-3 完整分句

每次续作,LLM **重新解读** status_note。即使 P0V.1 在 hard_constraints 把
"半身不遂"翻译成具体禁律注入 prompt,**快速模式不走这条路径**(走的是
simulation_service 直出),所以快速模式完全没约束。

本 module 的职责:
  把 "physical_flags → 具体禁律" 的翻译规则**抽到一个共享层**,让所有产物模式
  (evolution / quick / 未来的 sequel)都用同一份铁律,**不再让 LLM 自由解读**。

API:
  - infer_physical_flags(status_note) → list[str]
    从 status_note 关键词推出 flags(半身不遂 / 卧床 / 昏迷 / 失明 / 失语)
  - PHYSICAL_FORBIDDEN_VERBS: dict[flag, list[verb]]
    每个 flag 对应的违禁动词清单
  - build_physical_constraints_block(conn, project_id) → str
    生成完整 prompt 注入段(prepend 用),evolution + quick 共用

设计原则:
  - **0 schema 改动**:不引入新字段,纯关键词识别 + 规则翻译
  - **LLM-first**:把抽象状态翻译成 LLM 能执行的禁律,而非靠 LLM 自己想
  - **跨模式一致**:evolution / quick 见到的铁律完全相同

普适性:任何作品的"病弱角色"都适用 — 红楼黛玉(病弱)/ 三体史强(老年伤残)/
任何带身体伤残设定的角色。

created 2026-05-26 / P0X.2
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Optional

logger = logging.getLogger(__name__)


# 受限角色违禁动词清单(按 flag 分桶).
#
# 起源:15070 实测师傅 status_note="半身不遂"但仍出现"拄木杖走到"/"蹲下身"/
# "站直身子"/"伸手掀开白布"/"说连续 3 完整分句"等违反铁律的行为。
# P0V.1 prompt 注入但 LLM 仍违反,加事后检测词单。
PHYSICAL_FORBIDDEN_VERBS: dict[str, list[str]] = {
    "半身不遂": [
        # 站立类
        "站立", "站起", "站直", "起身", "起立", "站到",
        # 步行类
        "走到", "走来", "走去", "走过", "走出", "走向",
        "迈步", "迈出", "迈进", "跨步", "跨过", "跨进", "跨入", "跨出",
        # 蹲下 / 弯腰类
        "蹲下", "蹲身", "蹲坐", "弯腰", "俯身", "俯下",
        # 主动手部动作
        "伸手", "伸出", "伸过", "攥住", "攥紧", "抓住", "抓起", "握住",
        # 拄杖 / 撑起
        "拄着", "拄起", "扶着门", "扶着墙", "撑起",
    ],
    "卧床": [
        "下地", "下床", "离开床", "离开榻",
        "走到", "走来", "走去", "走过", "走出",
        "迈步", "跨步",
        "起身", "站起", "站立",
    ],
    "昏迷": [
        "说", "说道", "开口", "回答", "答道", "回道",
        "看", "望", "凝视", "盯", "瞥",
        "起身", "站起", "走", "迈步", "伸手",
        "想", "心想", "感到", "意识到",
    ],
    "失明": [
        "看见", "看着", "看到", "看向", "望见", "望着",
        "凝视", "端详", "盯着", "盯住", "瞥见", "瞥了",
        "目光落", "目光投", "目光移", "目光扫",
        "目睹", "注视",
    ],
    "失语": [
        "说道", "说着", "开口", "开声", "回答", "答道",
        "喃喃", "低语", "细语", "高声", "大声",
        # 注:含混嘟囔 / 单字呼唤 不算违禁(见 build 段的"正例")
    ],
}


def infer_physical_flags(status_note: Optional[str]) -> list[str]:
    """从 status_note 关键词推出 physical flags.

    与 hard_constraints.py / consistency_checker.py 之前的实现完全一致,
    避免 3 处独立维护漂移。

    支持的关键词分桶:
      - 半身不遂:半身不遂 / 偏瘫 / 瘫痪 / 中风
      - 卧床:卧床 / 病榻 / 重病 / 病重 / 瘫坐
      - 昏迷:昏迷 / 意识模糊 / 意识不清
      - 失明:失明 / 盲人 / [endswith]盲 / [contains]瞎
      - 失语:失语 / 哑 / 无法言语
    """
    if not status_note:
        return []
    note = status_note
    flags: list[str] = []
    if any(k in note for k in ("半身不遂", "偏瘫", "瘫痪", "中风")):
        flags.append("半身不遂")
    if any(k in note for k in ("卧床", "病榻", "重病", "病重", "瘫坐")):
        flags.append("卧床")
    if any(k in note for k in ("昏迷", "意识模糊", "意识不清")):
        flags.append("昏迷")
    if any(k in note for k in ("失明", "盲人")) or note.endswith("盲") or "瞎" in note:
        flags.append("失明")
    if any(k in note for k in ("失语", "哑", "无法言语")):
        flags.append("失语")
    return flags


def get_forbidden_verbs_for_flags(flags: list[str]) -> set[str]:
    """聚合多个 flag 的所有违禁动词."""
    verbs: set[str] = set()
    for f in flags:
        verbs.update(PHYSICAL_FORBIDDEN_VERBS.get(f, []))
    return verbs


def build_physical_constraints_block(
    conn: sqlite3.Connection,
    project_id: str,
) -> str:
    """生成 prompt 注入段,evolution / quick 模式都用同一份铁律.

    Args:
      conn: sqlite 连接
      project_id: 项目 id

    Returns:
      完整的 prompt 注入文本(可空 — 若没有受限角色)。
      格式:【角色物理 / 语言能力锁定】+ 角色清单 + 5 类禁律规则

    用法:
      block = build_physical_constraints_block(conn, sim.project_id)
      if block:
          system_prompt = block + "\\n\\n" + system_prompt   # prepend
    """
    try:
        from app.db import fetch_all
        char_rows = fetch_all(
            conn,
            "SELECT name, status_note FROM characters "
            "WHERE project_id=? AND life_status != 'deceased'",
            (project_id,),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"build_physical_constraints_block: query failed: {e}")
        return ""

    physical_constrained: list[tuple[str, str, list[str]]] = []
    for r in char_rows:
        note = r["status_note"] or ""
        if not note:
            continue
        flags = infer_physical_flags(note)
        if flags:
            physical_constrained.append((r["name"], note, flags))

    if not physical_constrained:
        return ""

    lines = ["【角色物理 / 语言能力锁定(本幕硬约束,P0V.1 / P0X.2)】"]
    for name, note, flags in physical_constrained:
        flag_str = " + ".join(flags)
        lines.append(f"  - {name}(状态:{note}) → 锁定:【{flag_str}】")
    lines.append(
        "\n  铁律 — 受限角色在本幕**绝对不能**做以下(违反 = 产物作废):\n"
        "  1. 【半身不遂 / 偏瘫】:\n"
        "     ✗ 不能稳定站立 / 不能拄拐杖步行 / 不能伸手攥住外物\n"
        "     ✗ 不能在物理对峙 / 阻拦 / 守门槛等需要站立的场景中作为主体行动者\n"
        "     ✓ 只能卧床 / 倚靠 / 被人搀扶 / 在他人身边以坐姿或躺姿出现\n"
        "  2. 【卧床 / 病重】:\n"
        "     ✗ 不能离开床榻 / 不能主动到外场景 / 不能下地行走\n"
        "     ✓ 别人到她床前来,她在床上躺 / 坐 / 倚\n"
        "  3. 【昏迷】:\n"
        "     ✗ 不能说话 / 不能有内心活动 / 不能感知周围\n"
        "     ✓ 只能作为被照料的客体出现\n"
        "  4. 【失明】:\n"
        "     ✗ 不能写她'看见 / 望见 / 目光落在 X / 凝视 / 端详'\n"
        "     ✓ 可以'听见 / 摸到 / 凭声音判断 / 侧耳朝向'\n"
        "  5. 【失语】:\n"
        "     ✗ 不能说话 / 不能有完整对白\n"
        "     ✓ 可以'含混嘟囔 / 单字 / 比划手势'\n"
        "  \n"
        "  6. **通用语言能力铁律**(适用于上述 1/2/3 状态的角色):\n"
        "     ✗ 不能说**连续 ≥ 2 个完整分句的整句**\n"
        "       (反例:'琴搁这儿。人走了就走了。你起来,门槛凉。' — 3 个完整分句,违反)\n"
        "     ✓ 只能含混断词 / 单字呼唤 / 5 字以内短句\n"
        "       (正例:'驹……粥……让那丫头……进来' — 含混断词,合规)\n"
        "  \n"
        "  违反这些 = 角色行为基线漂移,**整次产出作废**。\n"
        "  普适性:任何作品都适用 — 红楼黛玉(病弱)/ 三体史强(老年伤残)/ "
        "任何带身体伤残设定的角色"
    )
    return "\n".join(lines)


__all__ = [
    "PHYSICAL_FORBIDDEN_VERBS",
    "infer_physical_flags",
    "get_forbidden_verbs_for_flags",
    "build_physical_constraints_block",
]
