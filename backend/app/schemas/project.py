"""Project API schema。"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


ProjectType = Literal["novel", "comic", "anime", "generic"]
# Sprint D.9 启动前关键说明(2026-05-12 ADR v2 修订后口径):
#   - 'initial':初始态 — 空白世界,用户手动建角色/关系/事件
#   - 'middle':中间态 — 导入作品,AI 抽图谱,3D 图谱改属性 + 反事实重塑
#   - 'end':末尾态 — 导入作品,不改原作直接续写
#   - 'cycle':**漫画创作态**(字面量保留向下兼容,原"周期态"语义已废弃)
#     ⚠️ 漫画态消费前 3 态产物 → 8 agent 流水线 → 输出漫画;**不走 simulation**
#     ⚠️ 凡 mode == 'cycle' 的路径意图都是"漫画态",不要按周期态语义实现
#     详:docs/ADR_漫画创作态架构.md v2
ProjectMode = Literal["initial", "middle", "end", "cycle"]


class CreateProjectRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=30)
    type: ProjectType
    custom_type_name: Optional[str] = Field(
        default=None, max_length=30,
        description="type='generic' 时,用户可填自定义类型名(如'舞台剧''广播剧')。其它 type 该字段被忽略。",
    )
    tags: list[str] = Field(default_factory=list, max_length=10)
    mode: ProjectMode = "initial"  # 阶段 1 默认 initial

    @model_validator(mode="after")
    def _check_custom_type(self) -> "CreateProjectRequest":
        # generic + 给了 custom_type_name → 验长度 ≥ 1(已 max_length=30 限上限)
        # 不强制 generic 必填 custom_type_name(允许"其他"作为兜底,场景描述会回退到"故事")
        if self.type != "generic":
            self.custom_type_name = None  # 静默丢弃,保持数据干净
        elif self.custom_type_name is not None:
            stripped = self.custom_type_name.strip()
            self.custom_type_name = stripped or None
        return self


class UpdateProjectRequest(BaseModel):
    """部分更新,所有字段都可选(exclude_unset)。"""
    name: Optional[str] = Field(None, min_length=1, max_length=30)
    type: Optional[ProjectType] = None
    custom_type_name: Optional[str] = Field(None, max_length=30)
    tags: Optional[list[str]] = Field(None, max_length=10)
    # Sprint 3.A polish:3D 图谱关系连线显示阈值;Sprint D.7 fix:范围 10-80 → 0-100
    # 语义从绝对 score 切换到 rank-based 百分位 — X 表示"隐藏弱关系前 X%"
    # 0=全显 / 30=默认(隐藏弱 30%)/ 100=全隐
    graph_strength_threshold: Optional[int] = Field(None, ge=0, le=100)
    # INIT.5(2026-05-21):初始态用户手动编辑世界观 baseline 6 维
    # 6 维 key:genre / setting / magic_system / time_axis / tone / free_form
    # 每个值 ≤ 200 字符;前端 INIT.5 世界观 section 直接 PATCH 该字段
    world_baseline: Optional[dict[str, str]] = None
    # Sprint 6.A2 FOCUS.2(2026-05-21):作品叙述视角,用户可手动改 / extract 自动写
    # 枚举严格:first | second | third | mixed
    narrative_pov: Optional[Literal["first", "second", "third", "mixed"]] = None
    # SP-1 故事内核三件套(2026-05-28,migration 066):用户可手动改 / build_graph 抽取后回填
    core_dramatic_question: Optional[str] = Field(None, max_length=300)
    theme: Optional[str] = Field(None, max_length=200)
    ending_direction: Optional[str] = Field(None, max_length=300)
    # SP-8 视角扩展三件套(2026-05-28,migration 073):用户可手动改 / build_graph 抽取后回填
    narrative_focus_character_id: Optional[str] = Field(None, max_length=64)
    narrator_reliability: Optional[Literal["reliable", "unreliable", "uncertain"]] = None
    narrative_distance: Optional[Literal["omniscient", "limited", "close", "intimate"]] = None
    # 2026-06-01:作品篇幅 — 决定 narrator 细节颗粒度档位
    expected_length: Optional[Literal["short", "medium", "long"]] = None
    # 2026-06-01:章节字数区间(min/max)— 在区间内贪婪找段落切章
    chapter_size_min: Optional[int] = Field(None, ge=500, le=8000)
    chapter_size_max: Optional[int] = Field(None, ge=1500, le=10000)


class ProjectResponse(BaseModel):
    id: str
    user_id: str
    name: str
    type: ProjectType
    custom_type_name: Optional[str]
    tags: list[str]
    mode: ProjectMode
    created_at: str
    updated_at: str
    # 2.C+ polish: 6 维度世界观 baseline(infer_meta 自动识别 / 老项目可手动补识别)
    # 缺失或未识别 → 空 dict;前端兜底显空字符串
    world_baseline: dict[str, str] = {}
    # Sprint 3.A polish: 3D 图谱关系连线显示阈值;Sprint D.7 fix:rank-based 百分位 0-100,默认 30
    graph_strength_threshold: int = 30
    # Sprint 6.A2 FOCUS.2(2026-05-21):作品叙述视角(None = 未识别)
    narrative_pov: Optional[Literal["first", "second", "third", "mixed"]] = None
    # P2.B 升级(2026-05-24,migration 061):AI 推断的叙事节奏档(NULL = 未推断)
    # 首次创建续作时懒触发 pacing_inferer,推断后缓存
    inferred_pacing: Optional[Literal["slow", "standard", "fast"]] = None
    inferred_pacing_reasoning: Optional[str] = None
    inferred_pacing_metrics: Optional[dict] = None  # {avg_paragraph_chars, dialogue_ratio, ...}
    inferred_pacing_at: Optional[str] = None
    # SP-1 故事内核三件套(2026-05-28,migration 066)
    core_dramatic_question: Optional[str] = None
    theme: Optional[str] = None
    ending_direction: Optional[str] = None
    # SP-8 视角扩展三件套(2026-05-28,migration 073)
    narrative_focus_character_id: Optional[str] = None
    narrator_reliability: Optional[Literal["reliable", "unreliable", "uncertain"]] = None
    narrative_distance: Optional[Literal["omniscient", "limited", "close", "intimate"]] = None
    # 2026-06-01:作品篇幅 — 决定 narrator 细节颗粒度档位
    expected_length: Literal["short", "medium", "long"] = "medium"
    # 2026-06-01:章节字数区间(决定阅读章节切分点 + AI 注入元信息)
    chapter_size_min: int = 1500
    chapter_size_max: int = 2500
