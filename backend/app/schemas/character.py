"""Character API schema。

字段对齐 prompts/character_focus.md v3 输入(name 必填,其他可空)+ 3D 位置 + 颜色。

Sprint 6.A1(2026-05-18):
  - Character 升级为统一 agent 档案层(前三态通用)
  - PATCH 接受新的主角字段:is_protagonist / protagonist_user_pinned
  - protagonist_score / protagonist_reasons_json 由 judger 后台写入,不接受手动 PATCH

Sprint 6.A2 INIT.3(2026-05-21)— 行为基线 4 维 schema:
  - 新 `BehaviorBaseline` Pydantic 模型:speech_register / emotional_intensity /
    moral_compass / out_of_baseline_examples
  - Create / Update / Response 三 schema 加 `behavior_baseline` Optional 字段
  - 老 db 角色 NULL,初始态 UI 给 4 维 slider/select/textarea 入口让用户精调
  - 4 维度枚举值与 prompts/m4_consistency_checker.md + migration 041 注释一致,
    consistency_checker 拿到后做行为漂移自检(治瑕疵 3)
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# Sprint 6.A2 INIT.3(2026-05-21):4 维度行为基线
#
# 枚举值精确对齐 migration 041 注释 + prompts/m4_consistency_checker.md:
#   speech_register      4 档:卑微 / 平和 / 强硬 / 恶意
#   moral_compass        3 档:善 / 灰 / 恶
#   emotional_intensity  0-10 整数(基线值,本轮发言强度浮动 ±2 内)
#   out_of_baseline_examples 用户给的"绝对不会做"雷区描述列表
#
# 全字段 Optional — 允许部分填(用户精调到一半保存)。consistency_checker 看
# behavior_baseline is None or 不是 dict 时,自动 fallback 用 personality + no_go_list。
SpeechRegister = Literal["卑微", "平和", "强硬", "恶意"]
MoralCompass = Literal["善", "灰", "恶"]

# P1.B(2026-05-24,migration 059):角色生命/物理状态枚举
#   alive       默认 — 自由出场
#   deceased    已死 — hard_constraints 强制铁律"任何场景不许出现"
#   in_facility 在特定地点(疗养院/监狱/异国)— 软铁律"主角不到该地不应同框"
#   absent      暂时离开 — 类似 in_facility
#   unknown     未知 — 同 alive,不约束
LifeStatus = Literal["alive", "deceased", "in_facility", "absent", "unknown"]


class BehaviorBaseline(BaseModel):
    """角色行为基线 3 维度(P0G.2,2026-05-24:删 out_of_baseline_examples,
    其与 no_go_list 语义重复,且作为隐藏字段违反"透明 AI 协作"。
    数据迁移见 migration 062 — 已存内容已 merge 进 no_go_list。)
    consistency_checker / refine_service 等需同步停止读 out_of_baseline_examples。
    """
    speech_register: Optional[SpeechRegister] = None
    emotional_intensity: Optional[int] = Field(default=None, ge=0, le=10)
    moral_compass: Optional[MoralCompass] = None


class SecretItem(BaseModel):
    """SP-2(2026-05-28):一条角色秘密.

    description:这个秘密本身的内容(必填,1-300 字)
    hidden_from:对哪些角色名瞒着(可选,空列表 = 对全员瞒;部分名字 = 仅对这些人瞒)
    """
    description: str = Field(..., min_length=1, max_length=300)
    hidden_from: list[str] = Field(default_factory=list, max_length=20)


class CreateCharacterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=20)
    identity: str = Field(default="", max_length=200)
    personality: str = Field(default="", max_length=500)
    quotes: list[str] = Field(default_factory=list, max_length=20)
    no_go_list: list[str] = Field(default_factory=list, max_length=20)
    position_x: float = 0.0
    position_y: float = 0.0
    position_z: float = 0.0
    color: Optional[str] = Field(None, max_length=20)
    # Sprint 6.A2 INIT.3(2026-05-21):创建时即可填 4 维度
    behavior_baseline: Optional[BehaviorBaseline] = None
    # P1.B(2026-05-24):角色生命/物理状态(默认 alive)
    life_status: LifeStatus = "alive"
    status_note: str = Field(default="", max_length=200)
    # SP-2(2026-05-28,migration 068)— 角色驱动五件套
    surface_goal: Optional[str] = Field(None, max_length=300)
    deep_need: Optional[str] = Field(None, max_length=300)
    fatal_blind_spot: Optional[str] = Field(None, max_length=300)
    arc_from_to: Optional[str] = Field(None, max_length=300)
    secrets: list[SecretItem] = Field(default_factory=list, max_length=10)


class UpdateCharacterRequest(BaseModel):
    """部分更新,所有字段可选。

    Sprint 6.A1:`is_protagonist` 可由用户手动改;改时自动设
    `protagonist_user_pinned=true`(防 AI 后续重判覆盖用户决定)。

    Sprint 6.A2 INIT.3(2026-05-21):
      `behavior_baseline` 整对象覆盖语义 — 传 BehaviorBaseline 即整体替换;
      传 None 清空(consistency_checker fallback);未传字段 = 不动(exclude_unset)。
    """
    name: Optional[str] = Field(None, min_length=1, max_length=20)
    identity: Optional[str] = Field(None, max_length=200)
    personality: Optional[str] = Field(None, max_length=500)
    quotes: Optional[list[str]] = Field(None, max_length=20)
    no_go_list: Optional[list[str]] = Field(None, max_length=20)
    # B5.2(2026-05-27):aliases 可手动 PATCH(此前只能由 build_graph LLM 或 enricher 写)
    aliases: Optional[list[str]] = Field(None, max_length=20)
    position_x: Optional[float] = None
    position_y: Optional[float] = None
    position_z: Optional[float] = None
    color: Optional[str] = Field(None, max_length=20)
    # Sprint 6.A1:主角手动覆盖(score / reasons 由 judger 写,不接受手动 PATCH)
    is_protagonist: Optional[bool] = None
    # Sprint 6.A2 INIT.3(2026-05-21):4 维度行为基线
    behavior_baseline: Optional[BehaviorBaseline] = None
    # P1.B(2026-05-24):角色生命/物理状态(可单独 PATCH)
    life_status: Optional[LifeStatus] = None
    status_note: Optional[str] = Field(None, max_length=200)
    # SP-2(2026-05-28,migration 068)— 角色驱动五件套
    surface_goal: Optional[str] = Field(None, max_length=300)
    deep_need: Optional[str] = Field(None, max_length=300)
    fatal_blind_spot: Optional[str] = Field(None, max_length=300)
    arc_from_to: Optional[str] = Field(None, max_length=300)
    secrets: Optional[list[SecretItem]] = Field(None, max_length=10)


class CharacterResponse(BaseModel):
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
    # Sprint 6.A1:主角判定结果(前端 ProtagonistWall 显示用)
    is_protagonist: bool = False
    protagonist_score: float = 0.0
    protagonist_reasons: list[str] = Field(default_factory=list)
    protagonist_user_pinned: bool = False
    # Sprint 6.A2 M7.B(2026-05-20):续作产物来源(前端可显示"续作生成"badge)
    # None = 原作角色(extract / 手动);str = 来自该 simulation_id 的产物
    origin_simulation_id: Optional[str] = None
    # Sprint 6.A2 INIT.3(2026-05-21):4 维度行为基线(null = 老数据 fallback)
    behavior_baseline: Optional[BehaviorBaseline] = None
    # Sprint 6.A2 FOCUS(2026-05-21):别名 list — LLM 抽取的多种称呼 + 用户合并卡片累积
    aliases: list[str] = Field(default_factory=list)
    # P1.B(2026-05-24):角色生命/物理状态(老 db 行默认 alive/'')
    life_status: LifeStatus = "alive"
    status_note: str = ""
    # SP-2(2026-05-28,migration 068)— 角色驱动五件套(null = 老角色未填)
    surface_goal: Optional[str] = None
    deep_need: Optional[str] = None
    fatal_blind_spot: Optional[str] = None
    arc_from_to: Optional[str] = None
    secrets: list[SecretItem] = Field(default_factory=list)
