"""Simulation API schema。"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


# ========== POST /api/projects/{project_id}/simulations ==========

class CreateSimulationRequest(BaseModel):
    """触发推演 — 用户填锚点描述 + 重塑度,其他参数有合理默认值。

    reshape_percent 是用户面向滑块,后端线性派生 rounds_planned。
    schema 只做 [10, 90] 整数范围校验;reshape 上限由 enforce_reshape_quota
    在 router 闸门;AI 调用 credit 由 credit_service 在 LLM 完成时按真实 token 扣。

    style='custom' 时 custom_style_hint 必填(>=10 字),否则该字段被忽略。
    """
    divergence: str = Field(
        ...,
        min_length=10,
        max_length=500,
        description="反事实锚点 — '如果 X 没发生' 那一刻。10-500 字描述",
    )
    reshape_percent: int = Field(
        default=50, ge=10, le=90,
        description="重塑度 — 10-90 整数。免费档 ≤30,标准 ≤60,超级 ≤90。"
                    "派生轮次:10%→5 / 30%→16 / 60%→33 / 90%→50",
    )
    # Sprint 6.A2 M3.D-fix2 v2(2026-05-18):上限从 20000 提到 30000,
    # 对齐 90% 重塑度的推荐区间上界(12300-22700,留余量)。
    # 严格区间校验在 _check_target_chars_in_reshape_range 里做。
    target_chars: int = Field(default=4000, ge=1000, le=30000)
    # Sprint 1.Q:语体不再预设几档。两档:
    #   auto    — LLM 根据角色 / 题材标签 / divergence 自适应(7 类语体框架内挑或混合)
    #   custom  — 用户填一段描述强制覆盖
    # 兼容:旧 'A' 'C' API 调用仍接受,内部都按 'auto' 走(都加载 composer.md)
    style: Literal["auto", "custom", "A", "C"] = Field(
        default="auto",
        description="auto=AI 自适应(默认) / custom=用户自定义。"
                    "'A' 'C' 已废弃但兼容旧客户端。",
    )
    custom_style_hint: Optional[str] = Field(
        default=None, max_length=500,
        description="style='custom' 时必填的语体描述(≥10 字),"
                    "注入 composer prompt 强制覆盖语体自评估",
    )
    context_simulation_ids: Optional[list[str]] = Field(
        default=None, max_length=10,
        description="滚雪球续写 — 前文 simulation ids,最多 10 条。"
                    "后端验证:必须同项目 + state='done' + 属于当前用户。"
                    "为空 / null = 独立推演。",
    )
    selected_counterfactual_ids: Optional[list[str]] = Field(
        default=None,
        description="2.C+ 本次推演用哪些反事实 — null=全部 active(默认);"
                    "[]=不用任何反事实(纯按原作演);[id1,...]=只用子集。"
                    "前端 SimulationDock 反事实摘要区的勾选框写入。",
    )
    # Sprint 6.A2 M3.B(2026-05-18):续写模式
    #   quick     原 director-agents-composer 主路径(默认,¥0.78/篇,30-60s)
    #   evolution 灵魂续写:agent_evolution_engine 真多 agent + 私有记忆 + 多轮对话 + narrator
    #             (¥4-8/篇,5-15min,400-800 credits)
    mode: Literal["quick", "evolution"] = Field(
        default="quick",
        description="续写模式:quick(快速)/ evolution(灵魂续写,5-10× 成本但产物质量更高)",
    )
    # Sprint 6.A2 M6(2026-05-20):outline-first 长篇生成
    #   0/false = 走原 quick / evolution 自由 scene_picker
    #   1/true  = 走 outline-first(创建后先生 outline 给用户审核,审核后按 outline 跑)
    # 只在 mode='evolution' 时有意义(quick 不支持)
    use_outline_first: bool = Field(
        default=False,
        description="是否走 outline-first 长篇生成(M6 治本路径,仅 evolution 支持)",
    )
    # Sprint 6.A2 M7.J(2026-05-20):中间态起点锚点事件
    # 仅 project.mode='middle' 时接受非空值;end / initial 态强制 None
    # 非空时 → simulation_service 验证 event_id 属于本 project,prompt 注入「## 起点锚点」段
    # NULL = 旧"独立新场景"语义(向后兼容)
    anchor_event_id: Optional[str] = Field(
        default=None,
        max_length=64,
        description=(
            "中间态起点锚点事件 id — LLM 以该事件刚结束为时间起点续推;"
            "仅 mode='middle' 接受非空;end/initial 强制 NULL"
        ),
    )
    # P2.A(2026-05-24):是否走向大结局
    # false=默认 — 禁止主角"告别过去/接受未来"类大结局动作,允许超长篇续作
    # true =鼓励 — 主线收束 / 情绪闭环 / 角色弧光完成
    with_grand_finale: bool = Field(
        default=False,
        description="走向终章 — 启用后 narrator 会安排剧情收束。默认关闭便于超长篇续作。",
    )
    # P2.B(2026-05-24):叙事节奏档位
    # slow     允许多幕同场景 + 鼓励氛围铺陈
    # standard 默认 — 当前行为
    # fast     紧凑:每 2-3 幕必须切场景 + 主动制造波折
    narrative_pacing: Literal["slow", "standard", "fast"] = Field(
        default="standard",
        description="叙事节奏 — slow(慢)/standard(默认)/fast(紧凑)",
    )
    # P0H.2(2026-05-24):用户自定义每章字数 — 前端切分用
    chapter_size_chars: int = Field(
        default=2000, ge=1000, le=10000,
        description="每章字数(1000-10000)。前端按此值在 narrative 中自动加 ## 章节 N",
    )
    # 阶段 3A(2026-06-02):用户主动选择继承到本次续作的伏笔(跨代账本子集)
    # - null = 用户没用新 UI / 老客户端 → outline_generator 默认拉所有 open(向后兼容,F1.4 行为)
    # - []   = 用户主动空选 → outline_generator 一条伏笔都不读(独立创作)
    # - [...] = 用户勾选的具体 ids → outline_generator 只读这些
    # 前端 SimulationDock 的"继承伏笔"面板写入
    inherited_foreshadow_ids: Optional[list[str]] = Field(
        default=None,
        description=(
            "本次续作要继承的项目伏笔 ids 子集.\n"
            "null = 默认全继承(向后兼容);[] = 不继承任何;[...] = 选择性继承."
        ),
    )

    @model_validator(mode="after")
    def _check_custom_hint(self) -> "CreateSimulationRequest":
        if self.style == "custom":
            hint = (self.custom_style_hint or "").strip()
            if len(hint) < 10:
                raise ValueError(
                    "选择自定义语体时,需写至少 10 字的描述(让 AI 知道你想要的氛围)"
                )
        # 旧 'A' 'C' 内部归并到 'auto'(LLM 自适应,新 composer.md 兼容处理)
        if self.style in ("A", "C"):
            self.style = "auto"
        return self

    @model_validator(mode="after")
    def _soft_check_target_chars(self) -> "CreateSimulationRequest":
        """Sprint 6.A2 M3.D-fix2 v2(2026-05-18,用户拍板"以体面方式"):
        不硬拦截 target_chars 与 reshape_percent 的不匹配组合 — 那会破坏向后兼容。

        实际字数控制走 3 道软引导:
          1. 前端 SimulationDock 滑块 clamp 到 reshape_preview 推荐区间(UX 层 99% 拦截)
          2. composer prompt(quick)/ narrator prompt(evolution)告知 LLM 字数目标
          3. 灵魂续写主循环动态停止(达 target × 0.95 即 break)

        极端不匹配(如 reshape=10% + target_chars=30000)会自然产出靠近 reshape 推断的字数,
        而不是 LLM 强写 30000(因为 evolution 主循环 max_scenes 由 rounds_planned 限,
        quick 由 composer prompt 引导)。
        """
        # 仅保留极端边界检查(防 LLM 误读极端值导致跑飞)
        from app.services.simulation_service import reshape_to_recommended_chars
        info = reshape_to_recommended_chars(self.reshape_percent)
        # 允许用户传入的字数偏离推荐中心 ≤ 3× 或 ≥ 1/3 (极宽松,只挡极端误用)
        if self.target_chars > info["center"] * 3 or self.target_chars < info["center"] // 3:
            import logging
            logging.info(
                f"target_chars={self.target_chars} 偏离 {self.reshape_percent}% 重塑度"
                f"推荐中心 {info['center']} 较多;实际产出可能贴近 reshape 推断字数"
                f"(由主循环 rounds_planned + 字数控制机制决定)"
            )
        return self


class SimulationCreatedResponse(BaseModel):
    simulation_id: str
    state: str
    # Sprint 6.A2 M6(2026-05-20):outline-first 创建后告知前端是否需要跳转 OutlineReview
    use_outline_first: bool = False
    outline_state: Optional[str] = None  # drafting / awaiting_user / failed / None


# ========== GET responses ==========

class InheritanceChainNode(BaseModel):
    """Sprint 6.A2 M7.D(2026-05-20)— 接续继承链上的一个祖先节点。"""
    id: str
    divergence_short: str = Field(description="该代的剧情锚点摘要,≤ 40 字")
    depth: int = Field(ge=0, description="0=原作根节点(独立推演);N=接续了 N 次")


class SimulationSummaryResponse(BaseModel):
    """sim 列表响应基类。

    Sprint 6.A2 M7.D-fix(2026-05-20):
      inheritance_depth + ancestors_chain 提升到基类 — 让单项目列表端点
      `/projects/{id}/simulations` 也透出继承字段,给 ProjectView 的"作品列表"
      Tab 展示继承 badge 用。
    """
    id: str
    project_id: str
    state: str
    divergence: str
    reshape_percent: int
    rounds_planned: int
    current_round: int
    cost_yuan: float
    error_message: Optional[str]
    context_simulation_ids: list[str]
    created_at: str
    completed_at: Optional[str]
    # M7.D(2026-05-20):继承链元数据
    inheritance_depth: int = Field(
        default=0,
        ge=0,
        description="本 sim 在继承链中的代数;0=独立推演 / 原作根节点,N=第 N 代接续",
    )
    ancestors_chain: list[InheritanceChainNode] = Field(
        default_factory=list,
        description=(
            "祖先链(不含本 sim 自己),从原作根节点到父辈,按 depth 升序;"
            "前端 tooltip 渲染'① 原作 → ② 第 1 代 ... → 本 sim'用"
        ),
    )
    # hotfix(2026-06-01):透出续写模式 — 让作品列表显示"快速 / 灵魂 / 灵魂·outline"
    mode: str = Field(
        default="quick",
        description="续写模式:'quick'=快速;'evolution'=灵魂续写",
    )
    use_outline_first: bool = Field(
        default=False,
        description="是否走 outline-first(只在 mode='evolution' 有意义);True=灵魂·outline",
    )
    is_final_compilation: bool = Field(
        default=False,
        description=(
            "2026-06-01 v2:本 sim 是否为独立合并最终作品(自动新建的合并产物 sim 行)— "
            "True 时前端列表显示独立 amber 卡片;false 时显示为普通推演.\n"
            "替代旧 has_final_work 字段(那是查源 sim 的 final_compiled_narrative,现在改为独立 sim).\n"
        ),
    )
    compiled_from_sim_id: Optional[str] = Field(
        default=None,
        description="合并产物的源 sim id;仅 is_final_compilation=True 有值",
    )


class SimulationSummaryWithProjectResponse(SimulationSummaryResponse):
    """Sprint 1.J 我的剧情线 — GET /api/simulations 跨项目列表用。
    多带一个 project_name 字段,前端时间轴卡片直接渲染,无需二次查询。
    """
    project_name: str


class SimulationFullResponse(BaseModel):
    id: str
    project_id: str
    state: str
    divergence: str
    reshape_percent: int
    rounds_planned: int
    current_round: int
    target_chars: int
    style: str
    custom_style_hint: Optional[str]
    context_simulation_ids: list[str]
    narrative_summary: Optional[str]
    characters_snapshot: list[dict[str, Any]]
    timeline: Optional[dict[str, Any]]
    narrative: Optional[str]
    tokens_input: int
    tokens_output: int
    cost_yuan: float
    error_message: Optional[str]
    created_at: str
    started_at: Optional[str]
    completed_at: Optional[str]
    # Sprint 3.A 末尾态:创建时缓存的原作末段(其它 mode 为 None);
    # 详情页用此让用户看见"AI 接的是哪段原文"
    original_tail_excerpt: Optional[str] = None
    # Sprint 6.A2 M3.B:续写模式 'quick' / 'evolution'(默认 quick)
    mode: str = "quick"
    # Sprint 6.A2 M6(2026-05-20):outline-first 长篇生成标识
    # 详情页用此判断是否要显"前往审核 outline" banner
    use_outline_first: bool = False
    # 2026-06-01:章节体系
    # 创建本 sim 时锁定的起始章号(沿继承链推导);老 sim 为 None
    start_chapter_locked: Optional[int] = None
    # 走向终章 + 多代滚雪球 → 沿继承链合并的最终作品(本 sim 是最后一代时才有)
    # 2026-06-01 v2 DEPRECATED:新合并产物建独立 sim 行(is_final_compilation=1)
    # 此字段保留**仅供老 sim 向下兼容读取**,新代码必须用 is_final_compilation 判断
    # 计划:2026-12 之后审视是否可彻底移除(看老 sim 数据量)
    final_compiled_narrative: Optional[str] = Field(
        default=None,
        deprecated=True,
        description="v2 已废弃 — 用 is_final_compilation + compiled_from_sim_id 替代",
    )
    # 2026-06-01 v2:独立合并产物完整字段
    is_final_compilation: bool = False
    compiled_from_sim_id: Optional[str] = None


# ========== GET /api/simulations/{simulation_id}/emotional_states ==========
# Sprint 6.A2 路线图 #2(2026-05-22):角色情绪曲线可视化
# 后端 character_emotional_states 表(migration 042 M5.6)已落每幕末每角色 8 维向量,
# 此接口把数据扁平化给前端 CharacterEmotionChart 画 8 色折线图(横轴 scene_index 纵轴 0-10)
# 前端按 character_id 分组渲染

class EmotionalStateResponse(BaseModel):
    """单条角色情绪记录 — 给前端可视化用,已 LEFT JOIN characters 拼出 character_name。"""
    character_id: str
    character_name: str
    scene_index: int
    emotion: dict[str, int] = Field(
        ...,
        description="8 维 Plutchik 简化情绪向量,每维 0-10 整数。"
                    "键:joy/sadness/anger/fear/surprise/disgust/trust/anticipation",
    )
    rationale: str


class CharacterEmotionsBySim(BaseModel):
    """Sprint 6.A2 路线图 #2 二期(2026-05-22):某角色在某 sim 内的情绪轨迹 + sim 元数据。

    GET /api/projects/{id}/characters/{cid}/emotional_states 返回 list[CharacterEmotionsBySim]
    前端 CharacterEmotionOverviewModal 用此数据:
      - 顶部 chip 列表 = 每个 item 一个 chip(显时间 + 锚点摘要)
      - 选中 chip → 把对应 records 喂给 CharacterEmotionChart 画曲线
    """
    sim_id: str
    sim_state: str = Field(description="done / failed / cancelled / queued / directing / composing")
    sim_created_at: str
    sim_divergence: str = Field(description="推演锚点全文(前端 chip 显前 N 字摘要)")
    records: list[EmotionalStateResponse] = Field(
        description="该角色在该 sim 内全部情绪记录,按 scene_index ASC",
    )


# ========== POST /api/simulations/{id}/inject_scene_hint ==========

class SceneHintRequest(BaseModel):
    """Sprint 6.A2 路线图 #5(2026-05-23):续写过程中给下一幕塞 hint。

    用户在 SimulationDetailView 进度屏下方的输入框写一句话(比如"让主角这里要爆发"),
    POST 此端点 → 后端写入 simulations.pending_scene_hint。
    续写主循环下一幕开始前读取并立即清空(消耗式)→ hint 进入 director system prompt。
    """
    hint: str = Field(
        ...,
        min_length=1,
        max_length=300,
        description="1-300 字。续写下一幕生成时 AI 会把这句话纳入考虑。"
                    "只影响下一幕,用完即清空;不影响后续幕。",
    )


class SceneHintResponse(BaseModel):
    """提交成功后告诉前端"将在第 N+1 幕生效"。"""
    accepted: bool = Field(description="是否接受 — sim 已 done / failed / cancelled 时拒绝")
    will_apply_to_scene: Optional[int] = Field(
        default=None,
        description="hint 将生效的幕号(当前 current_round + 1);accepted=False 时为 null",
    )
    hint_preview: Optional[str] = Field(
        default=None,
        description="确认收到的 hint 前 60 字(给前端 toast 显示用);accepted=False 为 null",
    )
