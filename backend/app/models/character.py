"""Character 表的 Python 表示。

字段对齐 prompts/character_focus.md v3 的输入 schema(name 必填,其他可空)。

Sprint 6.A1(2026-05-18)语义升级:
  本 model **就是 agent 档案** — 不是"角色对焦时用户填的档案",而是统一的 agent 档案层。
  4 个原有字段重定位:
    identity     → agent 身份(我是谁)
    personality  → agent 性格(我会怎么做选择)
    quotes       → agent 台词风格示例(我说话什么语气)
    no_go_list   → agent 禁忌(我绝对不会做什么)
  3 个新字段(migration 035):
    is_protagonist           AI 判定 + 用户可改的主角标
    protagonist_score        4 维度加权评分 0.0-1.0
    protagonist_reasons_json 判定原因列表(透明给用户)
    protagonist_user_pinned  用户手动勾过 → AI 重判不覆盖

Sprint 6.A2 M4.3(2026-05-20)— 行为基线(migration 041):
  behavior_baseline 是 dict,4 维度:
    speech_register      语气登记("卑微" / "平和" / "强硬" / "恶意")
    emotional_intensity  情绪强度 1-10(基线值,本轮发言强度浮动 ±2 内)
    moral_compass        道德罗盘("善" / "灰" / "恶")
    out_of_baseline_examples 用户给的雷区描述列表
  NULL = 老数据,consistency_checker fallback 用 personality + no_go_list。

Sprint 6.A2 M7.B(2026-05-20)— 续作产物角色来源标签(migration 045):
  origin_simulation_id: 标记该角色"来自哪次推演"
    None  = 原作角色(extract 抽出 / 用户手动创建)
    str   = 续作 narrator 产物中新登场,sequel_character_sync 自动入库
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Character:
    id: str
    project_id: str
    name: str
    identity: str
    personality: str
    quotes: list[str]
    no_go_list: list[str]
    position_x: float
    position_y: float
    position_z: float
    color: Optional[str]
    created_at: str
    updated_at: str
    # Sprint 6.A1(2026-05-18):主角判定结果字段(migration 035)
    is_protagonist: bool = False
    protagonist_score: float = 0.0
    protagonist_reasons: list[str] = field(default_factory=list)
    protagonist_user_pinned: bool = False
    # Sprint 6.A2 M4.3(2026-05-20):行为基线 dict(migration 041);None = 老数据
    behavior_baseline: Optional[dict] = None
    # Sprint 6.A2 M7.B(2026-05-20):续作产物来源标记(migration 045);None = 原作角色
    origin_simulation_id: Optional[str] = None
    # Sprint 6.A2 FOCUS(2026-05-21,migration 054):角色别名 list
    # 来源:① build_graph LLM 抽取 entities[].aliases(v4 prompt 起强化称谓归一)
    #       ② 用户在主角面板手动合并卡片时,source.aliases + source.name 累积到 target.aliases
    aliases: list[str] = field(default_factory=list)
    # P1.B(2026-05-24,migration 059):角色生命/物理状态锁定 — 治"已死角色复活"瑕疵
    # life_status 枚举:
    #   'alive'       默认 — 自由出场
    #   'deceased'    已死 — 任何场景**不许出现**(铁律,prepend hard_constraints)
    #   'in_facility' 在特定地点(疗养院/监狱/异国)— 主角不到该地不应同框
    #   'absent'      暂时离开 — 类似 in_facility(软铁律)
    #   'unknown'     未知 — 同 alive
    # status_note:用户填的状态描述补充("在阿美寮疗养院" / "1969年自杀" / "去德国深造")
    # 普适性:任何作品都需 — 三体里"叶文洁(in_prison)"、红楼"林黛玉(deceased)"
    life_status: str = "alive"
    status_note: str = ""
    # SP-2(2026-05-28,migration 068)— 角色驱动层(灵魂续写北极星)
    # 描述层(identity/personality/...)→ 驱动层(want/need/secret/arc/blind):
    # 让角色从被动反应升级为主动 agent,推剧情走.
    # 五字段允许 NULL(老角色兼容);用户手填 / SP-2.1 让 build_graph 抽取
    surface_goal: Optional[str] = None       # 表层目标(嘴上追的)
    deep_need: Optional[str] = None          # 深层需要(其实缺却不承认的)
    fatal_blind_spot: Optional[str] = None   # 致命盲区
    arc_from_to: Optional[str] = None        # 预期弧光"从 X 到 Y"
    secrets: list[dict] = field(default_factory=list)  # 秘密列表 [{description, hidden_from?}]

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Character":
        # Sprint D.7 polish:json 字段 try/except 防脏数据 crash(参考 canonical_audit /
        # counterfactual_change 已有兜底)。null / 空字符串 / 非 list JSON → 安全 fallback []
        def _safe_list(raw: object) -> list[str]:
            if not raw or not isinstance(raw, str):
                return []
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, list) else []
            except (json.JSONDecodeError, TypeError):
                return []

        # Sprint 6.A1:主角字段(老 db 行可能缺,getattr 兜底)
        def _safe_get(key: str, default):
            try:
                return row[key]
            except (KeyError, IndexError):
                return default

        # Sprint 6.A2 M4.3(2026-05-20):behavior_baseline 解析(JSON dict;非法值 → None)
        def _safe_dict(raw: object) -> Optional[dict]:
            if not raw or not isinstance(raw, str):
                return None
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, dict) else None
            except (json.JSONDecodeError, TypeError):
                return None

        # SP-2(2026-05-28):secrets 字段解析(JSON list[dict])
        def _safe_list_of_dict(raw: object) -> list[dict]:
            if not raw or not isinstance(raw, str):
                return []
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return [s for s in parsed if isinstance(s, dict)]
            except (json.JSONDecodeError, TypeError):
                pass
            return []

        return cls(
            id=row["id"],
            project_id=row["project_id"],
            name=row["name"],
            identity=row["identity"],
            personality=row["personality"],
            quotes=_safe_list(row["quotes"]),
            no_go_list=_safe_list(row["no_go_list"]),
            position_x=row["position_x"],
            position_y=row["position_y"],
            position_z=row["position_z"],
            color=row["color"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            is_protagonist=bool(_safe_get("is_protagonist", 0)),
            protagonist_score=float(_safe_get("protagonist_score", 0.0)),
            protagonist_reasons=_safe_list(_safe_get("protagonist_reasons_json", "[]")),
            protagonist_user_pinned=bool(_safe_get("protagonist_user_pinned", 0)),
            behavior_baseline=_safe_dict(_safe_get("behavior_baseline_json", None)),
            origin_simulation_id=_safe_get("origin_simulation_id", None),
            # Sprint 6.A2 FOCUS(2026-05-21):aliases 列(migration 054 起);老 db row 兜底 []
            aliases=_safe_list(_safe_get("aliases_json", None)),
            # P1.B(2026-05-24):角色状态锁定;老 db 行无该列 → 默认 alive/'' 向后兼容
            life_status=_safe_get("life_status", "alive") or "alive",
            status_note=_safe_get("status_note", "") or "",
            # SP-2(2026-05-28,migration 068)— 角色驱动层兜底
            surface_goal=_safe_get("surface_goal", None),
            deep_need=_safe_get("deep_need", None),
            fatal_blind_spot=_safe_get("fatal_blind_spot", None),
            arc_from_to=_safe_get("arc_from_to", None),
            # secrets 字段:JSON list of dict;脏数据 / 老库 / 非 list → 安全 []
            secrets=_safe_list_of_dict(_safe_get("secret_json", None)),
        )
