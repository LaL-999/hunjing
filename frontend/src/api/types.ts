/**
 * API 类型定义 — 严格对齐后端 backend/app/schemas/*。
 *
 * 后端 schema 改动时,这里同步改;不要让前端类型和后端 Pydantic 定义漂移。
 */

// ========== auth ==========

export interface SendOtpRequest {
  email: string;
}

export interface SendOtpResponse {
  ok: boolean;
  message: string;
}

export interface VerifyOtpRequest {
  email: string;
  code: string;
}

/** Sprint D.1(2026-05-12)— 订阅模式;v5(2026-06-26)BYOK 主打转向后:
 *    free        — 免费,月度 20 credit
 *    pro         — ¥68/月 或 ¥693.60/年,月度 600 credit(单价 ¥0.11),全创作态解锁
 *    max         — ¥218/月 或 ¥2223.60/年,月度 2000 credit(单价 ¥0.11),全创作态解锁
 *    super_max   — 【v5 下架不可售】仅保留字面量兜底历史老用户快照,不再出现在售卖路径
 *    founder     — env 白名单,各项 999999
 *  主打方案是 BYOK(自携密钥)¥5/月,见 UpgradeModal 顶部 hero + 门户页。
 *  老 'standard' / 'super' 字面量已被 migration 021 迁移到 'pro' / 'max'。 */
export type Plan = "free" | "pro" | "max" | "super_max" | "founder";

export interface VerifyOtpResponse {
  token: string;
  user_id: string;
  plan: Plan;
  expires_at: string;
}

export interface CurrentUser {
  id: string;
  email: string | null;
  plan: Plan;
  created_at: string;
  // 2026-06-25:用户资料(作品广场作者展示)
  nickname?: string | null;
  avatar_url?: string | null;
}

// ========== consent ==========

export interface ConsentChecks {
  adult: boolean;
  terms: boolean;
  privacy: boolean;
  pricing: boolean;
}

export interface CreateConsentRequest {
  version: string;
  checks: ConsentChecks;
}

export interface ConsentRecord {
  id: string;
  version: string;
  accepted_at: string;
}

// ========== projects ==========

export type ProjectType = "novel" | "comic" | "anime" | "generic";
/** 项目 4 态字面量
 *
 * Sprint D.9 启动前关键说明(2026-05-12 ADR v2 修订后口径):
 *  - `'initial'`:初始态 — 空白世界,用户手动建角色/关系/事件
 *  - `'middle'`:中间态 — 导入作品,AI 抽图谱,3D 图谱里改属性 + 反事实重塑
 *  - `'end'`:末尾态 — 导入作品,不改原作直接续写
 *  - `'cycle'`:**漫画创作态**(语义已重定义,字面量保留向下兼容)
 *      ⚠️ 原"周期态"语义已废弃(与中间态滚雪球 1.O 重合,见 ADR_漫画创作态架构.md)
 *      ⚠️ 漫画态消费前 3 态产物 → 8 agent 流水线 → 输出漫画;**不走 simulation**
 *      ⚠️ 凡 `=== 'cycle'` 的代码路径意图都是"漫画态",**不要按周期态语义实现**
 */
/**
 * ProjectMode — 浑晶创作态枚举
 *
 * Sprint SP-S(2026-06-07)新增第 5 态 `screenplay` 剧创态:
 *   入口在 Dashboard 4 象限扩为 6 卡,位置:末尾态下方
 *   小说 → 剧本 YAML;独立路由 /screenplay(不走通用 NewProjectModal)
 *   后端通过 `huimeng_bridge` 借力中间态的 character_drivers / story_facts
 *
 * `more` 不是真实 mode,只是 UI 占位卡(状态恒为 "soon")— 不会进 DB
 */
export type ProjectMode = "initial" | "middle" | "end" | "cycle" | "screenplay" | "more";

export interface Project {
  id: string;
  user_id: string;
  name: string;
  type: ProjectType;
  /** type='generic' 时用户填的自定义类型名(其它 type 为 null) */
  custom_type_name: string | null;
  tags: string[];
  mode: ProjectMode;
  created_at: string;
  updated_at: string;
  /** 2.C+ polish: 6 维度世界观 baseline(infer_meta v2 自动识别 / 老项目可手动补识别)
   *  缺失或未识别 → 空 dict;前端反事实工作台「世界观」tab 用此预填"原"字段值。
   *  Key 限于 WorldCounterfactualField 但用 string 索引兼容 dict 解析失败时的空值。 */
  world_baseline: Record<string, string>;
  /** Sprint 3.A polish: 3D 图谱关系连线显示阈值,10-80,默认 30。
   *  数字越高显示越严格(只显强关系),越低越宽松(包括弱关系也显)。
   *  用户在 ProjectGraphView 顶栏滑块调整 → PATCH /projects/{id} 持久化 */
  graph_strength_threshold: number;
  /** Sprint 6.A2 FOCUS.2(2026-05-21):作品叙述视角
   *  null = 未识别(老项目 / extract 未跑)
   *  first/second/third/mixed = 已识别;续写时各 prompt 强制延续相同人称 */
  narrative_pov: NarrativePov | null;
  /** P2.B 升级(2026-05-24,migration 061):AI 推断的叙事节奏档位
   *  null = 未推断(首次创建续作时懒触发) */
  inferred_pacing?: "slow" | "standard" | "fast" | null;
  /** AI 推断的解释 — 给用户看的"为什么这个档位" */
  inferred_pacing_reasoning?: string | null;
  /** 原始推断指标:段落均长 / 对白比 / 场景跨度等 */
  inferred_pacing_metrics?: Record<string, unknown> | null;
  /** ISO 8601 推断完成时间 */
  inferred_pacing_at?: string | null;
  /** SP-1 故事内核三件套(2026-05-28,migration 066)— 灵魂续写北极星·目的层
   *  · core_dramatic_question:一句话脊柱(整本书围着它转)
   *  · theme:主题(独立于基调)
   *  · ending_direction:终点情绪 / 走向
   *  三字段允许 NULL(老项目兼容);用户可手填,LLM 推断后回填(SP-1.5) */
  core_dramatic_question?: string | null;
  theme?: string | null;
  ending_direction?: string | null;
  /** SP-8 视角扩展(2026-05-28,migration 073)— 灵魂续写北极星·叙述层
   *  · narrative_focus_character_id:聚焦视角角色(谁的"我" / 谁的内心被记)
   *  · narrator_reliability:reliable 可靠 / unreliable 不可靠 / uncertain 半可靠
   *  · narrative_distance:omniscient 全知 / limited 有限 / close 贴近 / intimate 沉浸
   *  三字段全可选;老项目 / 漫画态可不填 */
  narrative_focus_character_id?: string | null;
  narrator_reliability?: "reliable" | "unreliable" | "uncertain" | null;
  narrative_distance?: "omniscient" | "limited" | "close" | "intimate" | null;
  /** 2026-06-01:作品篇幅 — short/medium/long,决定 narrator 细节颗粒度 */
  expected_length?: "short" | "medium" | "long";
  /** 2026-06-01:章节字数区间(min,max)— 阅读器按此切章 + AI 注入元信息知道在写第几章 */
  chapter_size_min?: number;
  chapter_size_max?: number;
}

export interface CreateProjectRequest {
  name: string;
  type: ProjectType;
  custom_type_name?: string;
  tags?: string[];
  mode?: ProjectMode;
}

export interface UpdateProjectRequest {
  name?: string;
  type?: ProjectType;
  custom_type_name?: string;
  tags?: string[];
  /** Sprint 3.A polish: 3D 图谱关系连线显示阈值 10-80 */
  graph_strength_threshold?: number;
  /** INIT.5(2026-05-21):初始态用户编辑的世界观 6 维 baseline */
  world_baseline?: Record<string, string>;
  /** Sprint 6.A2 FOCUS.2(2026-05-21):作品叙述视角(用户可改 / extract 自动写) */
  narrative_pov?: NarrativePov | null;
  /** SP-1(2026-05-28):故事内核三件套(用户可手填) */
  core_dramatic_question?: string | null;
  theme?: string | null;
  ending_direction?: string | null;
  /** SP-8(2026-05-28):视角扩展三件套(用户可手填) */
  narrative_focus_character_id?: string | null;
  narrator_reliability?: "reliable" | "unreliable" | "uncertain" | null;
  narrative_distance?: "omniscient" | "limited" | "close" | "intimate" | null;
  /** 2026-06-01:作品篇幅 */
  expected_length?: "short" | "medium" | "long";
  /** 2026-06-01:章节字数区间(在 PATCH 时只传 min/max 二选一也可,后端会保持 max ≥ min+500) */
  chapter_size_min?: number;
  chapter_size_max?: number;
}

/** Sprint 6.A2 FOCUS.2(2026-05-21):叙述视角枚举(对齐后端 Literal) */
export type NarrativePov = "first" | "second" | "third" | "mixed";

// ========== characters ==========

/**
 * Sprint 6.A1(2026-05-18)— Character 升级为统一 agent 档案层(前三态通用)。
 * 4 个原有字段(identity / personality / quotes / no_go_list)语义重定位为 "agent 档案":
 *   identity     → 我是谁(身份 / 关系网 / 历史背景,1-3 句第一人称)
 *   personality  → 我会怎么做选择(行为倾向 / 价值观,2-5 句第一人称)
 *   quotes       → 我说话什么语气(3-8 句模仿原作语气的台词示例)
 *   no_go_list   → 我绝对不会做什么(3-6 条禁忌)
 * 4 个新字段(主角判定结果):
 *   is_protagonist         AI 判定 + 用户可改的主角标
 *   protagonist_score      0.0-1.0 综合评分(给主角墙排序用)
 *   protagonist_reasons    判定原因数组(如 "出场 23 次 ✓")
 *   protagonist_user_pinned  用户手动改过 is_protagonist → 后续 AI 重判不覆盖
 */
/**
 * Sprint 6.A2 INIT.3(2026-05-21):4 维度行为基线
 *   speech_register      4 档枚举(卑微 / 平和 / 强硬 / 恶意)
 *   emotional_intensity  0-10 基线值(发言强度浮动 ±2 内)
 *   moral_compass        3 档枚举(善 / 灰 / 恶)
 *
 * P0G.2(2026-05-24)— 删除 out_of_baseline_examples 字段
 *   原因:与 no_go_list 语义重复 + 作为隐藏字段违反"透明 AI 协作"
 *   迁移:已有数据见 migration 062,已 merge 进 no_go_list
 *
 * 老 db 角色 null;consistency_checker 自动 fallback 用 personality + no_go_list。
 * 枚举值与 backend/app/schemas/character.py 完全一致。
 */
export type SpeechRegister = "卑微" | "平和" | "强硬" | "恶意";
export type MoralCompass = "善" | "灰" | "恶";

export interface BehaviorBaseline {
  speech_register?: SpeechRegister | null;
  emotional_intensity?: number | null;
  moral_compass?: MoralCompass | null;
  /**
   * @deprecated P0G.2(2026-05-24)— 字段已彻底删除,后端不再返回,migration 062 已 merge 进 no_go_list。
   * 此 type 字段保留仅为编译兼容(老前端代码引用)— 运行时永远为 undefined。
   * 下次 sprint 应清理所有前端引用并完全删除。
   */
  out_of_baseline_examples?: string[];
}

/** SP-2(2026-05-28)— 角色秘密一条 */
export interface SecretItem {
  description: string;
  hidden_from: string[]; // 对哪些角色名瞒着,空 = 对全员瞒
}

export interface Character {
  id: string;
  project_id: string;
  name: string;
  identity: string;
  personality: string;
  quotes: string[];
  no_go_list: string[];
  position_x: number;
  position_y: number;
  position_z: number;
  color: string | null;
  created_at: string;
  updated_at: string;
  // Sprint 6.A1 主角字段
  is_protagonist: boolean;
  protagonist_score: number;
  protagonist_reasons: string[];
  protagonist_user_pinned: boolean;
  // Sprint 6.A2 INIT.3 行为基线(null = 老数据 fallback)
  behavior_baseline?: BehaviorBaseline | null;
  // P1.B(2026-05-24):角色生命/物理状态锁定(治"已死角色复活"瑕疵)
  life_status?: "alive" | "deceased" | "in_facility" | "absent" | "unknown";
  status_note?: string;
  // B5.2:别名(LLM 抽 + 用户合并累积)
  aliases?: string[];
  // SP-2(2026-05-28,migration 068)— 角色驱动五件套
  /** 表层目标(他自以为要的) */
  surface_goal?: string | null;
  /** 深层渴求(他真正缺的) */
  deep_need?: string | null;
  /** 致命盲点(他看不见的事) */
  fatal_blind_spot?: string | null;
  /** 弧光(开篇 → 结局的内在变化) */
  arc_from_to?: string | null;
  /** 秘密列表(对谁瞒) */
  secrets?: SecretItem[];
}

export interface CreateCharacterRequest {
  name: string;
  identity?: string;
  personality?: string;
  quotes?: string[];
  no_go_list?: string[];
  position_x?: number;
  position_y?: number;
  position_z?: number;
  color?: string;
  // Sprint 6.A2 INIT.3:创建时即可填 4 维度
  behavior_baseline?: BehaviorBaseline | null;
  // P1.B(2026-05-24):创建时也带状态
  life_status?: "alive" | "deceased" | "in_facility" | "absent" | "unknown";
  status_note?: string;
  // SP-2(2026-05-28):创建时也可带角色驱动五件套
  surface_goal?: string | null;
  deep_need?: string | null;
  fatal_blind_spot?: string | null;
  arc_from_to?: string | null;
  secrets?: SecretItem[] | null;
}

export interface UpdateCharacterRequest {
  name?: string;
  identity?: string;
  personality?: string;
  quotes?: string[];
  no_go_list?: string[];
  /** B5.2(2026-05-27):aliases 可手动 PATCH(之前只能 build_graph LLM / enricher 写) */
  aliases?: string[];
  position_x?: number;
  position_y?: number;
  position_z?: number;
  color?: string;
  /** Sprint 6.A1:用户手动改主角标 → 后端自动设 protagonist_user_pinned=true */
  is_protagonist?: boolean;
  /** Sprint 6.A2 INIT.3:整对象覆盖语义 — 传 BehaviorBaseline 即替换;传 null 清空 */
  behavior_baseline?: BehaviorBaseline | null;
  /** P1.B(2026-05-24):角色状态可单独 PATCH */
  life_status?: "alive" | "deceased" | "in_facility" | "absent" | "unknown";
  status_note?: string;
  /** SP-2(2026-05-28):角色驱动五件套(单独 PATCH) */
  surface_goal?: string | null;
  deep_need?: string | null;
  fatal_blind_spot?: string | null;
  arc_from_to?: string | null;
  secrets?: SecretItem[] | null;
}

// Sprint 6.A2 M2(2026-05-18):场景图谱
export interface ProjectScene {
  id: string;
  project_id: string;
  name: string;
  aliases: string[];
  description: string;
  appearance_chunk_count: number;
  created_at: string;
  updated_at: string;
  /** M8.A(2026-05-20):None = 原作图谱抽出;非 None = 续作生成入库 */
  origin_simulation_id?: string | null;
}

// M8.C(2026-05-21)编辑 / 合并请求
export interface UpdateProjectSceneRequest {
  name?: string;
  description?: string;
  aliases?: string[];
}

/** INIT.6(2026-05-21):初始态用户从零创建场景 */
export interface CreateProjectSceneRequest {
  name: string;
  description?: string;
  aliases?: string[];
}

export interface MergeProjectScenesRequest {
  source_scene_id: string;
  target_scene_id: string;
}

/** 场景"常客"角色 — character_affinity.compute_scene_regulars 输出 */
export interface SceneRegular {
  character_id: string;
  character_name: string;
  co_occurrence_count: number;
  is_protagonist: boolean;
}

export interface SceneRegularsResponse {
  scene_name: string;
  regulars: SceneRegular[];
}

// Sprint 6.A1:主角判定 / agent 档案补全 endpoint 响应
export interface ProtagonistMetricsDetail {
  appearance_count: number;
  span_ratio: number;
  interaction_count: number;
  event_participation: number;
}

export interface ProtagonistJudgeReportItem {
  character_id: string;
  character_name: string;
  score: number;
  is_candidate: boolean;
  is_protagonist_after: boolean;
  reasons: string[];
  metrics_detail: ProtagonistMetricsDetail;
}

export interface ProtagonistJudgeReport {
  judged_count: number;
  protagonist_count: number;
  skipped_pinned: number;
  metrics: ProtagonistJudgeReportItem[];
}

export interface EnrichAgentProfileReport {
  character: Character;
  report: {
    character_id: string;
    updated_fields: string[];
    skipped_fields: string[];
    context_samples: number;
    usage: { input_tokens: number; output_tokens: number };
    error: string | null;
  };
}

// ========== relationships ==========

/** Sprint 6.A2 M7.G(2026-05-20)关系类型自由化:
 *  - 旧:Literal 7 种(亲属/敌对/朋友/情侣/师徒/同事/其他)
 *  - 新:任意 1-20 字字符串(用户可自定义,LLM 可自创)
 *  - DB CHECK 改为 length BETWEEN 1 AND 20(migration 046)
 *  - 前端 dropdown 用 PRESET_RELATIONSHIP_TYPES,加"自定义"输入入口
 */
export type RelationshipType = string;

/** 前端 dropdown 预设清单(M7.G 2026-05-20)— 覆盖最常用,用户可绕过输入自定义 */
export const PRESET_RELATIONSHIP_TYPES: readonly string[] = [
  // 亲属类
  "亲属", "兄弟", "兄妹", "姐妹", "姐弟", "父子", "父女", "母子", "母女", "夫妻",
  // 友情类
  "朋友", "挚友", "青梅竹马",
  // 爱情类
  "情侣", "暗恋", "暧昧", "前任",
  // 学习 / 职场
  "师徒", "师生", "同学", "同事", "上下级", "主仆",
  // 对立
  "敌对", "宿敌",
  // 结构性(LOCATION / EVENT 节点连人物用)
  "位于", "参与", "提及",
  // 兜底
  "其他",
];

/** Sprint 3.A polish — 与后端 build_graph.md prompt v2 输出 5 级 enum 对齐
 *  score 映射(transformBackendGraph 用,UI 给关系连线调色 + CrystalGraph
 *  rank 排序的稳定 key):
 *    weak=10 / moderately_weak=30 / moderate=50 / moderately_strong=70 / strong=90
 *
 *  Sprint D.7 fix:阈值机制从"绝对 score 切"切换为"rank-based 百分位切" —
 *  阈值 X% → 隐藏 rank 最低的 X% 关系(弱在先、强在后);0=全显 / 100=全隐 */
export type RelationshipStrength =
  | "strong"
  | "moderately_strong"
  | "moderate"
  | "moderately_weak"
  | "weak";

// ========== Comic(D.9 漫画态)==========

/** 漫画态状态机 — 对齐后端 COMIC_STATES(backend/app/models/comic.py) */
export type ComicState =
  | "queued"
  | "scripting"
  | "extracting_visuals"
  | "style_uploading"
  | "style_analyzing"
  | "style_voting"
  | "character_anchoring"
  | "designing"
  | "generating"
  | "composing"
  | "done"
  | "failed"
  | "cancelled";

/** 漫画态输入源 */
export interface ComicSource {
  type: "internal" | "external";
  simulation_ids?: string[];
  upload_ids?: string[];
}

// ========== Sprint C.4 AI Planner ==========

export type UserPlanPreference = "auto" | "short" | "long";

export interface PlanPreviewRequest {
  source: ComicSource;
  user_preference?: UserPlanPreference;
}

export interface PlanPreviewResponse {
  /** 推荐总页数 — Sprint 3 Phase 1 硬限 6-12;Phase 2 接通真分批承接后放开到 30 */
  recommended_total_pages: number;
  panels_per_page: number;
  estimated_credits: number;
  reasoning: string;
  /** 源文本字数 > 9600 字时 true(提示用户可分多本)*/
  long_text_warning: boolean;
  source_char_count: number;
  character_count: number;
}

/** 画风定调员 v2 的候选样张(Agent #3 v2 产物) */
export interface ComicCandidateImage {
  index: number;
  variant_hint: string;
  image_url: string | null;
  error?: string;
}

/** 漫画态项目(对齐后端 Comic.to_response) */
export interface Comic {
  id: string;
  user_id: string;
  name: string;
  source: ComicSource;
  // 画风定调员 v2 产物
  style_tag: string | null;
  style_anchor_image_url: string | null;
  style_candidates: ComicCandidateImage[];
  style_reference_image_urls: string[];
  style_visual_dna: Array<Record<string, unknown>> | null;
  style_detailed_prompt: string | null;
  // 编剧产物
  script: Record<string, unknown> | null;
  // 一致性 L4
  generation_seed: number | null;
  // 状态机
  state: ComicState;
  progress_percent: number;
  // 元数据
  cost_yuan: number;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  /** Sprint C.4:目标页数(6-18,由 AI Planner 推荐 + 用户调整)*/
  target_pages: number;
  is_alive: boolean;
}

/** 漫画态 state → 中文 label(UI 状态显示) */
export const COMIC_STATE_LABEL: Record<ComicState, string> = {
  queued: "排队中",
  scripting: "编剧中",
  extracting_visuals: "抽取素材中",
  style_uploading: "等待上传参考图",
  style_analyzing: "画风分析中",
  style_voting: "等待你选画风",
  character_anchoring: "锚定角色形象",
  designing: "分镜设计中",
  generating: "生成漫画中",
  composing: "排版嵌字中",
  done: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

/** state 是否"等待用户操作"(UI 显示可点击 CTA) */
export const COMIC_USER_DRIVEN_STATES: ComicState[] = [
  "style_uploading",
  "style_voting",
];

/** Sprint 2.B+ 四修:cancel 退款档位 — 对齐后端 quota_service.compute_cancel_refund_units */
export type ComicRefundPhase = "full" | "half" | "none" | "noop";

export interface ComicCancelRefund {
  phase: ComicRefundPhase;
  /** quota_consumed 写入值:0 / -50 / -100。负值表"补偿退回" */
  units: number;
  /** 中文文案,前端 toast 直接用 */
  label: string;
}

/** POST /comics/{id}/cancel 响应 */
export interface ComicCancelResponse {
  comic: Comic;
  refund: ComicCancelRefund;
}

// ============================================================
// Sprint 3 Phase 1+ 漫画页面 / 格(对应 backend comic_pages 表)
// ============================================================
// Sprint 4.A 起从 ComicProjectView 内联抽出,供 ComicReaderView 复用

export interface ComicDialogue {
  speaker: string;
  text: string;
}

export interface ComicPagePanel {
  panel_index: number;
  /** Seedream 生成图 URL;失败时为 null(reader 显占位) */
  image_url: string | null;
  /** 实际发给 Seedream 的 200-400 字 prompt(debug 用,非必显示) */
  prompt_used: string | null;
  dialogues: ComicDialogue[];
  /** 旁白(单条 ≤ 30 字,reader 顶部条状显) */
  narrator: string | null;
  /** 拟声词(BANG! 等,reader 角标显) */
  sfx: string[];
  regenerated_count: number;
}

export interface ComicPageRecord {
  id: string;
  /** 页号,1-based,与 script.pages[i].page_index 对齐 */
  page_index: number;
  panels: ComicPagePanel[];
  /** Sprint 4.C typesetter 合成的整页 PNG;Sprint 4.A 阅读器优先用 panel.image_url 拼,留 composed_url 给后续切换 */
  composed_url: string | null;
  /** 'queued' | 'generating' | 'composed' | 'failed' */
  state: string;
  regenerated_count: number;
}

// ============================================================
// Sprint 4.D(2026-05-13)— Ready Uploads(漫画 modal external 源选择)
// ============================================================

/** GET /api/uploads/ready 返回的单项(列出当前用户所有 state='ready' 的 uploads) */
export interface ReadyUploadItem {
  id: string;
  filename: string;
  parsed_text_chars: number | null;
  project_id: string;
  /** LEFT JOIN projects 来的 name;项目已删除时为 "(项目已删除)" */
  project_name: string;
  uploaded_at: string;
}

/** 导出结果:blob + 缺页号(typesetter 失败的页,前端 toast 提示) */
export interface ComicExportResult {
  blob: Blob;
  filename: string;
  missingPages: number[];
}

/** 强度数字映射(给 transformBackendGraph 排序 + UI 调色用) */
export const RELATIONSHIP_STRENGTH_SCORE: Record<RelationshipStrength, number> = {
  strong: 90,
  moderately_strong: 70,
  moderate: 50,
  moderately_weak: 30,
  weak: 10,
};

/** 阈值滑块的中文 label — UI 给用户的"我现在用的是什么阈值"提示
 *  Sprint D.7 fix:6 档,对应 rank-based 百分位语义("隐藏 X% 弱关系") */
export const STRENGTH_THRESHOLD_HINT: Array<{
  max: number;
  label: string;
  hint: string;
}> = [
  { max: 0,   label: "全显示", hint: "保留所有关系连线,包括最弱" },
  { max: 30,  label: "默认",   hint: "隐藏最弱 30%,关系网清爽" },
  { max: 50,  label: "中位",   hint: "保留较强一半,弱关系全过滤" },
  { max: 70,  label: "严格",   hint: "只看较强 30%,聚焦核心关系" },
  { max: 99,  label: "极严格", hint: "只看最强少数,主线核心" },
  { max: 100, label: "全隐藏", hint: "完全隐藏所有关系连线,只看节点" },
];

/**
 * Sprint 6.A2 M1(2026-05-18)— Relationship 加 current_phase_id 字段。
 * NULL = 老数据 / 无 phases(走 type fallback)
 * 非 NULL = 续写时优先用 phase 的 type / strength
 */
/** SP-7(2026-05-28)— 关系正负极性
 *  positive 喜爱/亲近 / negative 仇恨/对立 / neutral 中性
 *  与 strength(紧密度)正交:strong+negative = 强烈仇恨 */
export type RelationshipPolarity = "positive" | "negative" | "neutral";

export interface Relationship {
  id: string;
  project_id: string;
  source_id: string;
  target_id: string;
  type: RelationshipType;
  description: string;
  color: string | null;
  strength: RelationshipStrength;
  created_at: string;
  /** Sprint 6.A2 M1:指向 relationship_phases 表当前生效阶段 */
  current_phase_id: string | null;
  /** SP-7(2026-05-28):正负极性(null = 未标,前端显灰 chip)*/
  polarity?: RelationshipPolarity | null;
}

export interface CreateRelationshipRequest {
  source_id: string;
  target_id: string;
  type: RelationshipType;
  description?: string;
  color?: string;
  strength?: RelationshipStrength;
  polarity?: RelationshipPolarity | null;
}

export interface UpdateRelationshipRequest {
  type?: RelationshipType;
  description?: string;
  color?: string;
  strength?: RelationshipStrength;
  /** Sprint 6.A2 M1:用户切换当前生效 phase(null = 回退 type fallback)*/
  current_phase_id?: string | null;
  /** SP-7(2026-05-28):用户可改极性,null 清空 */
  polarity?: RelationshipPolarity | null;
}

// ========== Sprint 6.A2 M1:关系时间轴 phase ==========

/** 关系演化的单个阶段(暗恋→情侣→仇敌 等)*/
export interface RelationshipPhase {
  id: string;
  relationship_id: string;
  phase_index: number;
  type: RelationshipType;
  strength: RelationshipStrength;
  start_anchor: string | null;    // "第 1 章" / "T1" / null=未知
  end_anchor: string | null;      // null = 持续到现在(最新 phase)
  trigger_event_id: string | null;
  notes: string;
  created_at: string;
  updated_at: string;
}

export interface CreateRelationshipPhaseRequest {
  type: RelationshipType;
  strength?: RelationshipStrength;
  start_anchor?: string | null;
  end_anchor?: string | null;
  trigger_event_id?: string | null;
  notes?: string;
  auto_set_current?: boolean;     // 默认 true:新加的 phase 自动成为 current
}

export interface UpdateRelationshipPhaseRequest {
  type?: RelationshipType;
  strength?: RelationshipStrength;
  start_anchor?: string | null;
  end_anchor?: string | null;
  trigger_event_id?: string | null;
  notes?: string;
}

// ========== events ==========

export interface ProjectEvent {
  id: string;
  project_id: string;
  description: string;
  participants: string[];
  created_at: string;
  /** INIT.7(2026-05-21):时间锚(如"第 5 章" / "T0+3 天");null = 未指定 */
  time_anchor?: string | null;
}

export interface CreateEventRequest {
  description: string;
  participants?: string[];
  /** INIT.7(2026-05-21):时间锚(可选) */
  time_anchor?: string;
}

export interface UpdateEventRequest {
  description?: string;
  participants?: string[];
  /** INIT.7(2026-05-21):时间锚可更新 */
  time_anchor?: string;
}

// ========== project graph(组合接口) ==========

export interface ProjectGraph {
  project: Project;
  characters: Character[];
  relationships: Relationship[];
  events: ProjectEvent[];
}

// ========== refine(角色对焦) ==========

export type SuggestionKind =
  | "identity_补全"
  | "personality_补充"
  | "quote_补充"
  | "no_go_补充"
  | "consistency_警告"
  // Sprint 6.A2 M1(2026-05-18):evolution_hint 不报矛盾,而是提示"关系演化",
  // 给用户 3 按钮选(加 phase / 替换 type / 真矛盾去改)
  | "evolution_hint"
  // Sprint 6.A2 FOCUS(2026-05-21):behavior_baseline 子字段补全
  // P0G.2(2026-05-24):3 子字段 — speech_register / emotional_intensity / moral_compass
  // (out_of_baseline_examples 已删,见 migration 062)
  | "behavior_baseline_补充";

export type RefinementStatus =
  | "pending"
  | "accepted"
  | "rejected"
  | "edited"
  | "skipped";

/** suggestion_payload 的形态(对齐 prompts/character_focus.md v3 + FOCUS) */
export interface SuggestionPayloadAppend {
  /** P0G.2(2026-05-24):3 类 append field;原 behavior_baseline.out_of_baseline_examples 已删 */
  field:
    | "personality"
    | "quotes"
    | "no_go_list"
    /** @deprecated P0G.2 — 后端不再处理此字段,保留 type 仅为编译兼容老 LLM 输出 */
    | "behavior_baseline.out_of_baseline_examples";
  append: string | string[];
}
export interface SuggestionPayloadValue {
  /** Sprint 6.A2 FOCUS(2026-05-21):value 新增 behavior_baseline.<subfield>(3 子字段)*/
  field:
    | "identity"
    | "behavior_baseline.speech_register"
    | "behavior_baseline.emotional_intensity"
    | "behavior_baseline.moral_compass";
  value: string | number;
}
export interface SuggestionPayloadWarning {
  kind: "warning";
  fields: string[];
  current_a?: string;
  current_b?: string;
}
/** Sprint 6.A2 M1:evolution_hint payload — 提示关系演化(非矛盾)。
 *  前端在 CharacterFocusModal 跳 3 按钮:加 phase / 替换 type / 真矛盾去改 */
export interface SuggestionPayloadEvolutionHint {
  kind: "evolution_hint";
  relationship_id: string;
  earlier_type: RelationshipType;
  current_type: RelationshipType;
  evidence: string;
}
export type SuggestionPayload =
  | SuggestionPayloadAppend
  | SuggestionPayloadValue
  | SuggestionPayloadWarning
  | SuggestionPayloadEvolutionHint;

export interface RefinementItem {
  id: string;
  character_id: string;
  character_name: string;
  suggestion_kind: SuggestionKind;
  suggestion_text: string;
  suggestion_payload: SuggestionPayload;
  status: RefinementStatus;
}

export interface RefineSessionStats {
  characters_count: number;
  refinements_count: number;
  tokens: { input_tokens: number; output_tokens: number };
  cost_yuan: number;
  duration_ms: number;
  filtered_out: number;
}

export interface RefineSessionResponse {
  session_id: string;
  refinements: RefinementItem[];
  stats: RefineSessionStats;
}

export interface ActionRefinementRequest {
  action: "accept" | "reject" | "edit";
  user_edit?: SuggestionPayload;
}

export interface ActionRefinementResponse {
  id: string;
  status: RefinementStatus;
  applied_to_character: boolean;
}

export interface SkipSessionResponse {
  skipped: boolean;
  remaining_refinements: number;
}

// ========== simulation(续写引擎,Sprint 1.G + 1.H) ==========

export type SimulationState =
  | "queued"
  | "directing"
  | "composing"
  | "done"
  | "failed"
  | "cancelled";

/** Sprint 1.Q 起 'A' 'C' 已废弃但保留兼容(后端 Pydantic 自动归并到 'auto')*/
export type SimulationStyle = "auto" | "custom" | "A" | "C";

/** 创建推演的 POST body — 对齐 backend CreateSimulationRequest */
export interface CreateSimulationRequest {
  divergence: string;
  /** 重塑度 10-90,默认 50。后端按 plan.reshape_max_percent 闸门 */
  reshape_percent?: number;
  /** 目标字数 1000-20000,默认 4000 */
  target_chars?: number;
  /** A=古雅笔法 / C=现代白话 / custom=自定义,默认 A */
  style?: SimulationStyle;
  /** style='custom' 时必填(≥10 字),其它 style 静默丢弃 */
  custom_style_hint?: string;
  /** 滚雪球续写:前文 sim ids(同项目 + done),最多 10 条;空 = 独立推演 */
  context_simulation_ids?: string[];
  /** 2.C+ 本次推演用哪些反事实:
   *    null/undefined = 全部 active(默认)
   *    [] = 显式不用反事实(纯按原作演)
   *    [id1, ...] = 用户在 SimulationDock 勾选的子集 */
  selected_counterfactual_ids?: string[] | null;
  /** Sprint 6.A2 M3.C(2026-05-18):续写模式
   *    quick    单 LLM 编排(默认,~78 c,30-60s)
   *    evolution 灵魂续写:多 agent 独立 + 私有记忆 + RAG(~400-800 c,5-15 min)
   */
  mode?: "quick" | "evolution";
  /** Sprint 6.A2 M6(2026-05-20):outline-first 长篇生成
   *  仅 mode='evolution' 时生效;true → 创建后先生成 outline 让用户审核 */
  use_outline_first?: boolean;
  /** Sprint 6.A2 M7.J(2026-05-20):中间态起点锚点事件 id
   *  仅 project.mode='middle' 接受非空;LLM 以该事件刚结束为时间起点续推
   *  null/undefined = 旧"独立新场景"语义(向后兼容) */
  anchor_event_id?: string | null;
  /** P2.A(2026-05-24):走向终章开关
   *  false=默认(允许超长篇续作)/ true=本部续作以大结局收尾 */
  with_grand_finale?: boolean;
  /** P2.B(2026-05-24):叙事节奏档位
   *  slow=慢(允许多幕同场景)/ standard=默认 / fast=紧凑(主动制造波折) */
  narrative_pacing?: "slow" | "standard" | "fast";
  /** P0H.2(2026-05-24):每章字数(1000-10000)— 前端按此值切分加 ## 章节 N */
  chapter_size_chars?: number;
  /** 阶段 3A(2026-06-02):本次续作要继承的项目伏笔 ids 子集
   *  null/undefined = 默认全继承(向后兼容);[] = 不继承任何;[...] = 选择性继承 */
  inherited_foreshadow_ids?: string[] | null;
}

export interface SimulationCreatedResponse {
  simulation_id: string;
  state: SimulationState;
  /** M6:outline-first 模式下 true → 前端跳转 OutlineReviewView */
  use_outline_first?: boolean;
  /** M6:outline 当前状态:drafting / awaiting_user / failed / null */
  outline_state?: string | null;
}

// ============================================================
// Sprint 6.A2 M6(2026-05-20)— Outline 类型
// ============================================================

export type OutlineState =
  | "drafting"
  | "awaiting_user"
  | "approved"
  | "generating"
  | "done"
  | "failed";

export type OutlineSceneState = "pending" | "running" | "done" | "failed";

export interface OutlineSceneKeyProp {
  name: string;
  action: string; // "introduced" / "referenced" / "used_as_medium" / ...
  properties: Record<string, string>;
}

export type PacingTempo = "fast" | "normal" | "slow";

export interface OutlineScene {
  id: string;
  outline_id: string;
  scene_index: number;
  scene_summary: string;
  scene_purpose: string;
  location: string;
  time_anchor: string;
  characters_present: string[];
  key_events: string[];
  key_props: OutlineSceneKeyProp[];
  transition_from_last: string;
  user_edited: boolean;
  state: OutlineSceneState;
  generated_simulation_scene_id: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  // Sprint 6.A2 MP(2026-05-21)— planner 张力 / 节奏(可空)
  tension_percent?: number | null;
  pacing_tempo?: PacingTempo | null;
  // SP-6(2026-05-28,migration 071)— 宏观三幕结构标记
  structure_act?: "act1_setup" | "act2_confrontation" | "act3_resolution" | null;
}

/** SP-5(2026-05-28):伏笔(plot_thread)— 后端 migration 070 暴露 */
export interface PlotThread {
  id: string;
  simulation_id: string;
  introduced_at_scene_index: number;
  description: string;
  resolved_at_scene_index: number | null;
  priority: 1 | 2 | 3;
  staleness: number;
  created_at: string;
  updated_at: string;
  // 2026-06-02 cleanup:expected_resolution_scene 删除(migration 080)
  is_abandoned: boolean;
  // 后端 @property 派生字段(_thread_to_dict 注入)
  status: "active" | "resolved" | "abandoned";
  is_active: boolean;
  must_advance: boolean;
}

export interface PlotThreadsResponse {
  threads: PlotThread[];
  counts: { active: number; resolved: number; abandoned: number };
}

// 阶段 3A(2026-06-02):项目级跨代伏笔(用户创建续作时主动选继承)
export interface ProjectForeshadow {
  id: string;
  content: string;
  priority: "high" | "medium" | "low";
  introduced_in_simulation_id: string;
  introduced_scene_index: number;
  created_at: string;
}

export interface ProjectForeshadowsResponse {
  foreshadows: ProjectForeshadow[];
  total: number;
}

// ============================================================
// SP-3(2026-05-28)— 知识边界(谁知道什么)
// ============================================================

export type KnowledgeConfidence = "suspected" | "confirmed" | "wrong";

/** 谁知道这条事实(后端 include_knowledge=true 时随 fact 一起返) */
export interface KnowledgeEntry {
  character_id: string;
  character_name: string;
  known_since_scene: number | null;
  confidence: KnowledgeConfidence;
}

/** 项目级事实(全局唯一,n 个角色可标 known) */
export interface StoryFact {
  id: string;
  project_id: string;
  description: string;
  first_revealed_scene: number | null;
  is_sensitive: boolean;
  created_at: string;
  /** 仅在 GET ?include_knowledge=true 时返 */
  known_by?: KnowledgeEntry[];
}

export interface StoryFactsResponse {
  facts: StoryFact[];
}

export interface CreateStoryFactRequest {
  description: string;
  first_revealed_scene?: number | null;
  is_sensitive?: boolean;
}

export interface MarkKnownRequest {
  known_since_scene?: number | null;
  confidence?: KnowledgeConfidence;
}

/** GET /characters/{cid}/known_facts 返回 */
export interface KnownFact {
  id: string;
  description: string;
  is_sensitive: boolean;
  known_since_scene: number | null;
  confidence: KnowledgeConfidence;
}

export interface KnownFactsResponse {
  character_id: string;
  facts: KnownFact[];
}

// ============================================================
// SP-9(2026-05-29)— 反事实分支并排对比
// GET /api/simulations/{a}/compare/{b}
// ============================================================

export interface CompareSimMeta {
  id: string;
  project_id: string;
  project_name: string;
  state: SimulationState;
  divergence: string;
  reshape_percent: number;
  rounds_planned: number;
  current_round: number;
  target_chars: number;
  style: string;
  created_at: string;
  completed_at: string | null;
  cost_yuan: number;
}

export interface CompareCounterfactualEntry {
  id: string;
  project_id: string;
  target_type: "character" | "event" | "relationship" | "world";
  target_id: string;
  /** 后端 _resolve_target_name 加的:character.name / event.description / relationship 表达 */
  target_name: string;
  field: string;
  old_value: string | null;
  new_value: string | null;
  user_intent: string | null;
  created_at: string;
  reverted_at: string | null;
  is_active: boolean;
  applied_in_simulations: string[];
}

export interface CompareCounterfactualDiff {
  common: CompareCounterfactualEntry[];
  only_in_a: CompareCounterfactualEntry[];
  only_in_b: CompareCounterfactualEntry[];
}

export interface CompareSceneSnapshot {
  scene_index: number;
  scene_name: string;
  scene_source: string;
  time_anchor: string;
  characters_present: string[];
  narrative_segment: string;
}

export interface CompareSceneAlignmentItem {
  scene_index: number;
  a: CompareSceneSnapshot | null;
  b: CompareSceneSnapshot | null;
  /** 双方都有 / 仅 a / 仅 b */
  diff_kind: "both" | "only_a" | "only_b";
  /** 双方都有时,若 scene_name 相同 + 字数比 ≥ 0.7 → similar=true */
  similar: boolean;
}

export interface CompareStats {
  scenes_a_count: number;
  scenes_b_count: number;
  common_scene_count: number;
  similar_scene_count: number;
}

export interface SimulationCompareResponse {
  sim_a: CompareSimMeta;
  sim_b: CompareSimMeta;
  counterfactual_diff: CompareCounterfactualDiff;
  scene_alignment: CompareSceneAlignmentItem[];
  stats: CompareStats;
}

// 2026-06-06:续作家族树(GET /api/projects/:id/simulation_family_tree)
export interface FamilyTreeNode {
  id: string;
  state: string;                    // queued/generating/done/failed/cancelled
  mode: string;                     // quick / evolution
  divergence: string;
  narrative_chars: number;
  created_at: string;
  completed_at: string | null;
  parent_ids: string[];             // context_simulation_ids
  combination_run_id: string | null;
  tree_path: string[] | null;       // 反事实组合树叶子路径如 ["a","b","a"]
  is_final_compilation: boolean;
  compiled_from_sim_id: string | null;
  with_grand_finale: boolean;
  use_outline_first: boolean;
  inheritance_depth: number;        // 根=0,直接父辈+1
}

export interface FamilyTreeCombinationRun {
  id: string;
  total_combinations: number;
  state: string;
  created_at: string;
  selected_variables_count: number;
}

export interface FamilyTreeStats {
  total_sims: number;
  roots: number;
  max_depth: number;
  combo_batches: number;
}

export interface SimulationFamilyTreeResponse {
  nodes: FamilyTreeNode[];
  combination_runs: FamilyTreeCombinationRun[];
  stats: FamilyTreeStats;
}

// SP-9 enhancement(2026-05-29 末)— LLM 因果分析报告
export interface CompareTurningPoint {
  scene_index: number;
  what_diverged: string;
  /** 最可能导致此差异的反事实变量 id;空字符串 = LLM 随机性无主因 */
  likely_cause_cf_id: string;
}

export interface CompareCfImpactChain {
  cf_id: string;
  side: "only_in_a" | "only_in_b" | "common";
  narrative_consequence: string;
}

export interface CompareAnalysisReport {
  summary: string;
  verdict_a: string;
  verdict_b: string;
  key_turning_points: CompareTurningPoint[];
  cf_impact_chains: CompareCfImpactChain[];
  reasoning: string;
  /** LLM 调用失败 / 数据不足时 service 返程序级简短分析,is_fallback=true */
  is_fallback: boolean;
}

export interface SimulationOutline {
  id: string;
  simulation_id: string;
  state: OutlineState;
  total_scenes_planned: number;
  global_theme: string;
  global_arc: string;
  user_approved_at: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  scenes: OutlineScene[];
}

export interface UpdateOutlineSceneRequest {
  scene_summary?: string;
  scene_purpose?: string;
  location?: string;
  time_anchor?: string;
  characters_present?: string[];
  key_events?: string[];
  key_props?: OutlineSceneKeyProp[];
  transition_from_last?: string;
}

export interface UpdateOutlineGlobalRequest {
  global_theme?: string;
  global_arc?: string;
}

/** 列表用 — summary,无 timeline / narrative / characters_snapshot 重字段 */
/** M7.D(2026-05-20)继承链上的祖先节点 */
export interface InheritanceChainNode {
  id: string;
  divergence_short: string;
  depth: number;        // 0 = 原作根节点;N = 第 N 代
}

export interface SimulationSummary {
  id: string;
  project_id: string;
  state: SimulationState;
  divergence: string;
  reshape_percent: number;
  rounds_planned: number;
  current_round: number;
  cost_yuan: number;
  error_message: string | null;
  /** 滚雪球前文 sim ids,空 = 独立推演 */
  context_simulation_ids: string[];
  created_at: string;
  completed_at: string | null;
  /** M7.D-fix(2026-05-20)本 sim 在继承链中的代数;0 = 独立推演 / 原作根
   *  提升到基类后,单项目 sims 列表(/projects/{id}/simulations)也会透出 */
  inheritance_depth: number;
  /** M7.D-fix(2026-05-20)祖先链(不含本 sim 自己) */
  ancestors_chain: InheritanceChainNode[];
  /** hotfix(2026-06-01):续写模式 — 用于列表区分 chip */
  mode?: "quick" | "evolution";
  /** 仅 mode='evolution' 有意义;true=灵魂·outline */
  use_outline_first?: boolean;
  /** 2026-06-01 v2:独立合并最终作品标识 — true 时列表显示 amber 卡片(替代旧 has_final_work) */
  is_final_compilation?: boolean;
  /** 仅 is_final_compilation=true 有值,指向源 sim */
  compiled_from_sim_id?: string | null;
}

/** 跨项目列表用 — Sprint 1.J 我的剧情线。GET /api/simulations 返回 */
export interface SimulationSummaryWithProject extends SimulationSummary {
  project_name: string;
}

/** 详情用 — full,GET /api/simulations/{id} 返回 */
export interface SimulationFull extends SimulationSummary {
  target_chars: number;
  style: SimulationStyle;
  custom_style_hint: string | null;
  /** 被后续 sim 引用为前文时,缓存的 ~800 字摘要(否则 null)*/
  narrative_summary: string | null;
  characters_snapshot: Array<Record<string, unknown>>;
  timeline: { rounds: SimulationRound[] } | null;
  narrative: string | null;
  tokens_input: number;
  tokens_output: number;
  started_at: string | null;
  /** Sprint 3.A 末尾态(end mode)专属:创建推演时缓存的原作末尾 ~2000 字
   *  其它 mode 为 null;详情页用此让用户看见"AI 接的是哪段原文" */
  original_tail_excerpt: string | null;
  /** Sprint 6.A2 M3.B(2026-05-18):续写模式
   *    'quick'     单 LLM 编排(原 director-agents-composer 主路径)
   *    'evolution' 灵魂续写:多 agent 独立 + 私有记忆 + RAG(M3.A/B/C)
   *  前端进度文案 / 标题 / stageLabel 用此区分"轮" vs "幕"等措辞 */
  mode: "quick" | "evolution";
  /** Sprint 6.A2 M6(2026-05-20):outline-first 长篇生成标识
   *  SimulationDetailView 用此判断是否要显"前往审核 outline" banner */
  use_outline_first?: boolean;
  /** P0H.2(2026-05-24):每章字数 — SimulationReadView 按此值切 narrative 加 ## 章节 N */
  chapter_size_chars?: number;
  /** 2026-06-01:章节体系 — 创建时锁定的起始章号(沿继承链推导)*/
  start_chapter_locked?: number | null;
  /** 2026-06-01:走向终章 + 滚雪球合并的最终作品 — 非空 = 已合并落库
   *  v2 deprecated:新合并产物建独立 sim 行,此字段保留向下兼容 */
  final_compiled_narrative?: string | null;
  /** 2026-06-01 v2:独立合并产物完整字段 */
  is_final_compilation?: boolean;
  compiled_from_sim_id?: string | null;
}

// ============================================================
// Sprint 6.A2 路线图 #2(2026-05-22):角色情绪曲线可视化
// 数据源:GET /api/simulations/{sim_id}/emotional_states
// 后端 character_emotional_states 表(migration 042 M5.6)落每幕末每角色 8 维向量
// 前端 CharacterEmotionChart 组件按 character_id group + 画 8 色折线图
// 8 维对齐 backend models/emotional_state.py(Plutchik 简化版)
// ============================================================

export type EmotionKey =
  | "joy" | "sadness" | "anger" | "fear"
  | "surprise" | "disgust" | "trust" | "anticipation";

/** 8 维情绪键序(对齐 backend EMOTION_KEYS;固定顺序便于 chart 配色 index) */
export const EMOTION_KEYS: readonly EmotionKey[] = [
  "joy", "sadness", "anger", "fear",
  "surprise", "disgust", "trust", "anticipation",
] as const;

/** 中文标签(UI 友好,对齐 backend EMOTION_LABELS_CN) */
export const EMOTION_LABELS_CN: Record<EmotionKey, string> = {
  joy: "喜悦",
  sadness: "悲伤",
  anger: "愤怒",
  fear: "恐惧",
  surprise: "惊讶",
  disgust: "厌恶",
  trust: "信任",
  anticipation: "期待",
};

/** 单条情绪记录(某角色 × 某幕末),每维 emotion 值 0-10 */
export interface EmotionalStateRecord {
  character_id: string;
  character_name: string;
  scene_index: number;
  emotion: Record<EmotionKey, number>;
  rationale: string;
}

/** Sprint 6.A2 路线图 #2 二期(2026-05-22):跨多次推演的角色情绪轨迹
 *  GET /api/projects/{id}/characters/{cid}/emotional_states 返回 list[CharacterEmotionsBySim]
 *  ProjectView 角色卡展开 "情绪总览" 入口 → CharacterEmotionOverviewModal 用 */
export interface CharacterEmotionsBySim {
  sim_id: string;
  sim_state: string;            // done / failed / cancelled / queued / directing / composing
  sim_created_at: string;       // ISO,前端排序 + chip 显时间
  sim_divergence: string;       // 推演锚点,chip 显摘要
  records: EmotionalStateRecord[];   // 该 sim 内按 scene_index ASC
}

export interface SimulationRound {
  round: number;
  director_plan: {
    location: string;
    time_advance: string;
    round_seed: string;
    narrator_note: string;
    present_agents: string[];
    speaking_agents: string[];
  };
  events: Array<{
    speaker: string;
    speaker_id: string;
    monologue: string;
    action: string;
    dialogue: string;
  }>;
}

// ========== quota(配额闸门) ==========

// Sprint C.1(2026-05-13)credit 重构:删除 AI 类次数 kind(refine / continuation / extract / comic)
//   AI 调用配额由 credit_balance 计量,InsufficientCredits 走独立错误码
//   保留资源容量类 kind(reshape / characters / projects)
// ECON-1(2026-05-27 末⁴):订阅模式 v5 重设 — PlanLimits/QuotaLimits 字段含义不变,数字重设
//   - monthly_credits_quota: 20 / 600 / 2000 / 6500(free/pro/max/super_max)
//   - single_credit_price_cents: 0 / 23 / 22 / 21
//   - comics_per_month: 全档清零(漫画态改为单买漫画包,ECON-2 sprint 实现)
// Sprint 5.B(2026-05-18)漫画态降级:加 comics_per_month — 漫画态独立次数池,不走 credit
export type QuotaKind =
  | "reshape_percent"
  | "characters_per_project"
  | "projects_total"
  | "comics_per_month";

/** 订阅档配额(Sprint 5.B 加 comics_per_month 漫画次数池)*/
export interface QuotaLimits {
  monthly_credits_quota: number;        // 月度发放 credit 数(订阅 wallet)
  single_credit_price_cents: number;    // 单 credit 售价(分,加购包参考)
  characters_per_project: number;
  projects_total: number;
  reshape_max_percent: number;
  /** Sprint 5.B:漫画态月度免费次数(Free 0 / Pro 1 / Max 2 / 超级 Max 4)*/
  comics_per_month: number;
}

/** Credit 钱包余额(GET /api/quota 返回的 credit_balance 子结构)*/
export interface CreditBalance {
  subscription_credits: number;   // 订阅 wallet(月末清零)
  addon_credits: number;          // 加购 wallet(1 年有效期)
  total_credits: number;          // 总余额(subscription + addon)
  month_start: string;            // 本月重置参考点 ISO
}

export interface QuotaStatus {
  plan: Plan;
  limits: QuotaLimits;
  credit_balance: CreditBalance;
  usage: {
    projects_total: number;
    /** Sprint 5.B:本月已占用漫画次数(failed / cancelled 不计入)*/
    comics_used_this_month?: number;
  };
}

/** 后端 429 detail 形态(资源容量类 QuotaExceeded → 转 HTTP) */
export interface QuotaExceededDetail {
  code: "QUOTA_EXCEEDED";
  kind: QuotaKind;
  used: number;
  limit: number;
  plan: Plan;
  message: string;
}

/** Sprint C.1:AI credit 不足时后端返此 detail */
export interface InsufficientCreditsDetail {
  code: "INSUFFICIENT_CREDITS";
  needed: number;                 // 本次需要多少 credit
  available: number;              // 当前可用余额
  action: string;                 // 'refine' / 'continuation' / 'extract' / 'comic_*'
  message: string;
}

// ========== 加购包(Sprint C.1)==========

export type AddonPackageSize = "small" | "medium" | "large";

export interface AddonPurchaseRequest {
  package_size: AddonPackageSize;
  notes?: string;
}

export interface AddonPurchaseResponse {
  purchased_credits: number;
  package_size: AddonPackageSize;
  price_cents: number;
  expires_at: string;
  credit_balance: CreditBalance;
}

/** 加购包静态定义(前端硬编码,对齐后端 credit_service.ADDON_PACKAGES) */
export interface AddonPackageDef {
  size: AddonPackageSize;
  label: string;       // "小包" / "中包" / "大包"
  credits: number;
  price_cents: number;
  unit_price_yuan: number;
  /** 与 Pro 订阅单价(¥0.1423/c)对比的"贵 X%"标签,给用户清晰对比 */
  premium_over_pro_pct: number;
}

export const ADDON_PACKAGES: AddonPackageDef[] = [
  // v5(2026-06-26)全线约减半,对齐后端 credit_service.ADDON_PACKAGES
  { size: "small",  label: "小包", credits: 100,  price_cents: 1000,  unit_price_yuan: 0.10, premium_over_pro_pct: 0 },
  { size: "medium", label: "中包", credits: 500,  price_cents: 4200,  unit_price_yuan: 0.084, premium_over_pro_pct: 0 },
  { size: "large",  label: "大包", credits: 2000, price_cents: 16000, unit_price_yuan: 0.08, premium_over_pro_pct: 0 },
];

/** Credit 交易明细(GET /api/credit/transactions) */
export interface CreditTransaction {
  id: string;
  delta: number;
  wallet: "subscription" | "addon";
  kind:
    | "subscribe_grant"
    | "addon_purchase"
    | "consume"
    | "refund"
    | "month_reset"
    | "addon_expire";
  action: string | null;
  related_id: string | null;
  cost_yuan: number;
  metadata: Record<string, unknown> | null;
  created_at: string;
}

// ========== Billing(订阅 / 价格快照,Sprint E.4)==========

export type PaidPlan = "pro" | "max";   // v5:super_max 下架不可售(Plan 仍留它兜底老用户)
export type BillingCycle = "monthly" | "yearly";
export type SnapshotState = "active" | "cancelled" | "expired" | "upgraded";

/** 用户订阅快照(GET /api/billing/snapshot 返,无快照时 204)
 *  承载协议第三章"老用户老规则"承诺:订阅时点的价格 + 配额冻结,
 *  平台未来调价不影响 active 用户。 */
export interface PlanSnapshot {
  id: string;
  plan: PaidPlan;
  billing_cycle: BillingCycle;
  price_cents: number;
  price_yuan_fmt: string;     // "138.00"
  limits: {
    monthly_credits_quota: number;
    single_credit_price_cents: number;
    characters_per_project: number;
    projects_total: number;
    reshape_max_percent: number;
  };
  grandfather_at: string;
  current_period_start: string;
  current_period_end: string;
  state: SnapshotState;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface SubscribeRequest {
  plan: PaidPlan;
  billing_cycle: BillingCycle;
  notes?: string;
}

export interface UpgradeRequest {
  plan: PaidPlan;
  billing_cycle: BillingCycle;
  notes?: string;
}

// ========== 自洽守护者(Sprint 1.R)==========

/** 8 个评估维度,与 backend schemas/audit.py ALL_KINDS 一一对应 */
export type AuditIssueKind =
  | "character_thin"
  | "event_inconsistent"
  | "relationship_off"
  | "dialogue_flat"
  | "turn_jarring"
  | "pacing_off"
  | "opening_weak"
  | "whitespace_imbalance";

/** 用户可修类(UI 显「去修」按钮 → 跳编辑面)*/
export const USER_FIXABLE_AUDIT_KINDS: ReadonlySet<AuditIssueKind> = new Set([
  "character_thin",
  "event_inconsistent",
  "relationship_off",
]);

/** M7.C(2026-05-20)结构化修复 payload — 给"采纳"按钮一键应用用 */
export interface AuditFixOperation {
  field: string;           // personality / quotes / no_go_list / identity / description / type
  op: "append" | "replace";
  value: string | string[];
}

/** M7.K(2026-05-20)sim_config target 的 patches — LLM-only 类采纳用 */
export interface AuditSimConfigPatches {
  reshape_percent_delta?: number;     // -30..+30
  target_chars_delta?: number;        // -5000..+10000
  custom_style_hint?: string;         // ≤ 100 字
  divergence_prefix?: string;         // ≤ 30 字
}

export interface AuditFixPayload {
  target: "character" | "event" | "relationship" | "sim_config";
  /** target=character/event/relationship 时填 */
  operations?: AuditFixOperation[];
  /** target=sim_config 时填(M7.K)*/
  patches?: AuditSimConfigPatches;
}

export interface AuditIssue {
  kind: AuditIssueKind;
  /** character_thin / event_inconsistent / relationship_off 时填,其余 LLM-only 类为 null */
  subject_id: string | null;
  subject_name: string;
  evidence_in_narrative: string;
  root_cause_in_setup: string;
  actionable_fix: string;
  /** M7.C(2026-05-20)结构化修复 payload — LLM 给的话才有,null = 无法自动采纳 */
  actionable_fix_payload?: AuditFixPayload | null;
  /** M7.C(2026-05-20)该 issue 被采纳的时间;null = 尚未采纳 */
  accepted_at?: string | null;
}

export interface AuditResponse {
  id: string;
  simulation_id: string;
  overall_score: number;             // 0-100
  issues: AuditIssue[];
  regenerate_recommendation: string;
  cost_yuan: number;
  duration_ms: number;
  triggered_at: string;              // ISO 8601
}

/** M7.C(2026-05-20)采纳端点响应 */
export interface AcceptedOperation {
  field: string;
  op: "append" | "replace";
  value: string | string[];
  new_field_value: string | string[];
}

export interface AcceptAuditIssueResponse {
  audit_id: string;
  issue_idx: number;
  target: "character" | "event" | "relationship" | "sim_config";
  /** target='sim_config' 时 = audit.simulation_id;其它 target = subject 真实 id */
  subject_id: string;
  subject_name: string;
  operations_applied: AcceptedOperation[];
  operations_skipped: { field?: string; op?: string; reason: string }[];
  /** M7.K(2026-05-20)— target='sim_config' 时返回的推荐配置;
   *  前端跳回项目页 dock 时按此预填 reshape / target_chars / custom_style_hint / divergence */
  sim_config_patch?: AuditSimConfigPatches | null;
  accepted_at: string;
}

// ========== 正典守护者(Sprint 2.D 差异化王牌)==========

/** 12 维度审计 — 对齐 backend canonical_audit.py VALID_DIMENSIONS
 *   B5.3(2026-05-27)加 body_register_alignment(第 9 维)
 *   P5.2(2026-05-27)加 outline_execution(第 10 维)
 *   SP-3.1(2026-06-02)加 information_boundary(第 11 维)
 *   SP-1 终审(2026-06-02)加 story_core_adherence(第 12 维)
 */
export type CanonicalDimension =
  | "character_consistency"     // 角色性格连贯
  | "relationship_network"      // 关系网络
  | "worldview"                 // 世界观一致
  | "era_physics"               // 时代物理可行性
  | "tone"                      // 叙事语调底色
  | "event_causality"           // 关键事件因果
  | "value_orientation"         // 道德/价值取向
  | "detail_authenticity"       // 细节真实
  | "body_register_alignment"   // 身体描写尺度对齐(B5.3 / 2026-05-27,灵魂续写关键)
  | "outline_execution"         // outline 执行率(P5.2 / 2026-05-27,治剧情空心化)
  | "information_boundary"      // 信息边界(SP-3.1 / 2026-06-02,角色用了不该知道的信息)
  | "story_core_adherence";     // 故事内核坚守度(SP-1 终审 / 2026-06-02,治偏离故事脊柱/主题/终点)

/** 4 档严重度 */
export type CanonicalSeverity =
  | "strict_canonical"   // 严格符合(隐藏)
  | "minor_drift"        // 轻微偏离
  | "obvious_drift"      // 明显偏离
  | "severe_breach";     // 严重背离

/** 维度中文标签 + 简介(给 UI 展示) */
export const CANONICAL_DIMENSION_META: Record<
  CanonicalDimension,
  { label: string; hint: string }
> = {
  character_consistency:    { label: "角色性格", hint: "产物中角色行为是否符合原作底色" },
  relationship_network:     { label: "关系网络", hint: "亲疏 / 敌友 / 等级结构" },
  worldview:                { label: "世界观",  hint: "体裁 / 超能力体系 / 时间轴 / 基调" },
  era_physics:              { label: "时代物理", hint: "物品 / 技术 / 场景是否符合原作时代" },
  tone:                     { label: "叙事语调", hint: "氛围 / 悲喜 / 明暗" },
  event_causality:          { label: "事件因果", hint: "伏笔呼应 / 关键决策连锁" },
  value_orientation:        { label: "价值取向", hint: "道德 / 哲学底层" },
  detail_authenticity:      { label: "细节真实", hint: "称谓 / 礼仪 / 风俗 / 用语习惯" },
  body_register_alignment:  { label: "身体描写尺度", hint: "频率/直白度/态度/功能 是否贴合原作风骨(灵魂续写)" },
  outline_execution:        { label: "剧情执行率", hint: "outline 规定的关键事件是否在产物中显性完成(治剧情空心化)" },
  information_boundary:     { label: "信息边界", hint: "角色是否用了上帝视角的信息(没在场/未告知/已被排除)— 治 AI 写作最大连贯 bug" },
  story_core_adherence:     { label: "故事内核坚守", hint: "是否偏离故事脊柱 / 主题 / 终点情绪(治 LLM 提前泄气)" },
};

/** 严重度颜色 + 中文标签(给 chip 用) */
export const CANONICAL_SEVERITY_META: Record<
  CanonicalSeverity,
  { label: string; color: string; bg: string }
> = {
  strict_canonical: { label: "严格符合", color: "#16A34A", bg: "rgba(22, 163, 74, 0.12)" },
  minor_drift:      { label: "轻微偏离", color: "#0E7490", bg: "rgba(14, 116, 144, 0.12)" },
  obvious_drift:    { label: "明显偏离", color: "#B45309", bg: "rgba(245, 158, 11, 0.18)" },
  severe_breach:    { label: "严重背离", color: "#DC2626", bg: "rgba(220, 38, 38, 0.12)" },
};

export interface CanonicalIssue {
  dimension: CanonicalDimension;
  severity: CanonicalSeverity;
  finding: string;
  evidence_excerpt: string;
  canon_reference: string;
  counterfactual_exempt: boolean;
  exempt_reason: string | null;
}

export interface CanonicalAuditResponse {
  id: string;
  simulation_id: string;
  project_id: string;
  state: "running" | "done" | "failed";
  issues: CanonicalIssue[];
  tokens_input: number;
  tokens_output: number;
  cost_yuan: number;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
  /** Sprint 6.A2 路线图 #7(2026-05-23):0-100 总分(state='done' 才有值)+ 非反事实豁免的问题数 */
  overall_score?: number | null;
  effective_issues_count?: number;
}

// ========== 去 IP 化导出(Sprint 2.E 四层版权防护核心层)==========

/** 与后端 schemas/de_ip.py DeIpDictionaryResponse 对齐 */
export interface DeIpDictionary {
  project_id: string;
  /** {"林黛玉": "黛影", "贾府": "仁府", ...} */
  mapping: Record<string, string>;
  /** LLM 给的字典设计说明(可空)*/
  notes: string | null;
  tokens_input: number;
  tokens_output: number;
  cost_yuan: number;
  created_at: string;
  updated_at: string;
}

/** POST /api/simulations/{id}/export body */
export interface ExportSimulationRequest {
  version: "original" | "de_ip";
}

/** 替换 trail 单条(仅 de_ip 版返,按 count 降序) */
export interface ExportReplacementEntry {
  original: string;
  replaced: string;
  count: number;
}

/** POST /api/simulations/{id}/export 返 */
export interface ExportSimulationResponse {
  filename: string;     // 'narrative_xxx.md' or 'narrative_xxx_deip.md'
  content: string;      // markdown 全文
  version: "original" | "de_ip";
  replacements: ExportReplacementEntry[] | null;
  total_replacements: number;
}

// ========== 文件上传(Sprint 2.A 中间态)==========

/** 与后端 schemas/upload.py UploadResponse 对齐 */
export interface UploadResponse {
  id: string;
  project_id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  parsed_text_chars: number | null;
  state:
    | "uploaded"
    | "parsed"
    | "rejected"
    | "extracting"
    | "ready"
    | "failed";
  error_message: string | null;
  uploaded_at: string;
}

/** 文件上传错误码(与后端 routers/uploads.py 对齐)*/
export type UploadErrorCode =
  | "UPLOAD_FORMAT_REJECTED"
  | "UPLOAD_DUPLICATE"
  | "UPLOAD_PARSE_FAILED"
  | "UPLOAD_RED_FLAG";

/** 红旗命中错误 detail(category 给前端 toast 显类目化提示)*/
export interface UploadRedFlagDetail {
  code: "UPLOAD_RED_FLAG";
  category: "political" | "sexual" | "violence" | "privacy";
  matched_text: string;
  message: string;
}

// ========== 自动图谱抽取(Sprint 2.B 中间态)==========

/** SSE 推送的单条事件(对齐 backend extract_service _emit)。
 *
 * polling_tick 例外:不是 SSE 事件,是 SSE 失败降级 polling 时前端合成的"心跳"
 * 事件,用 detail 差分推断进度(每 2s 一次,但只有 state 或 tokens 变化才入列)。
 *
 * chunk_failed:某分块抽取失败被跳过(常见:LLM 输出 JSON 截断 / 超时)。
 *   不阻塞整体抽取(其他块仍合并出图谱),但要让用户看到,避免"AI 神秘卡住"假象。
 *
 * chunk_skipped:resume 模式下命中已完成块(extract_chunk_results 表 hash 一致)。
 *   该块免抽,显示"第 N 块已恢复"。这是断点续抽的核心 UX 信号。
 *
 * minimal_batch_*:阶段 2.b 配角批量轻量补全(2026-05 方案 D)— top 30 主角之外
 *   的配角分批一次 LLM 调用补 personality / quotes / no_go_list 简略字段,让红楼梦
 *   238 角色不再有半空白卡片。
 */
export type ExtractEventKind =
  | "snapshot"
  | "state_change"
  | "extract_graph_start"
  | "chunk_done"
  | "chunk_failed"
  | "chunk_skipped"
  | "extract_graph_done"
  | "entities_pending_review"   // Sprint 6.A2 FOCUS.10(2026-05-22):人机协同审核暂停
  | "characters_start"
  | "profile_start"
  | "profile_done"
  | "profile_failed"
  | "minimal_batch_start"
  | "minimal_batch_done"
  | "minimal_batch_failed"
  | "meta_inferred"
  | "save_done"
  | "life_status_done"   // P0K.1(2026-05-24):每个角色生命状态判定完成进度
  | "gender_audit_done"  // P0M.1(2026-05-24):每个角色性别 / 亲属关系审计完成进度
  | "missed_protagonist_warning"   // P0L.3(2026-05-24):主角缺位 post-check 警告
  | "done"
  | "error"
  | "polling_tick";

export interface ExtractEvent {
  kind: ExtractEventKind;
  // snapshot 字段
  id?: string;
  state?: string;
  // 计数 / 进度
  characters_count?: number;
  relationships_count?: number;
  events_count?: number;
  skipped_count?: number;
  entities_count?: number;
  relations_count?: number;
  total_chunks?: number;
  chunk_index?: number;
  // FOCUS.9(2026-05-22):chunk_done 带本块统计,前端显"段内抽了 X 角色 Y 关系"
  chunk_persons_count?: number;
  chunk_entities_count?: number;
  chunk_relations_count?: number;
  // FOCUS.9(2026-05-22):extract_graph_done 附带漏抽人名候选(从 description 反向扫出来)
  // 前端用来显"⚠ 以下角色可能漏抽,要不要手动添加"卡片
  missed_persons?: string[];
  // FOCUS.10(2026-05-22):entities_pending_review 事件附带待审角色列表
  persons?: Array<{
    name: string;
    description: string;
    aliases: string[];
    text_occurrences: number;
  }>;
  // P0K.1(2026-05-24):life_status_done 事件携带
  name?: string;           // 该角色名
  life_status?: string;    // alive | deceased | in_facility | absent | unknown
  progress?: number;       // 1-based 已完成数
  total?: number;          // 总角色数
  // P0L.3(2026-05-24):missed_protagonist_warning 事件携带
  narrative_pov?: string;  // first | second | third | mixed
  // P0M.1(2026-05-24):gender_audit_done 事件携带
  verdict?: string;  // correct | wrong | no_evidence | skipped | error
  total_chars?: number;
  total_profiles?: number;
  profile_index?: number;
  character_name?: string;
  names?: string[];
  // 批量轻量补全(阶段 2.b)
  total_remaining_persons?: number;
  total_batches?: number;
  batch_index?: number;
  persons_filled?: number;
  persons_count?: number;
  // meta
  inferred_type?: string | null;
  inferred_custom_type_name?: string | null;
  inferred_tags?: string[];
  // 计费
  tokens_input?: number;
  tokens_output?: number;
  cost_yuan?: number;
  // error
  message?: string;
  error_message?: string | null;
  is_admin_retag?: boolean;
  // 客户端补:接收时间戳(UI 排序展示用)
  _ts?: number;
}

/** 与后端 schemas/extract_job.py ExtractJobResponse 对齐 */
export interface ExtractJobResponse {
  id: string;
  upload_id: string;
  project_id: string;
  state:
    | "queued"
    | "extracting_graph"
    | "entities_pending_review"   // Sprint 6.A2 FOCUS.10(2026-05-22):人机协同审核
    | "generating_characters"
    | "inferring_meta"
    | "saving"
    | "done"
    | "failed";
  is_admin_retag: boolean;
  inferred_type: string | null;
  inferred_custom_type_name: string | null;
  inferred_tags: string[];
  characters_count: number;
  relationships_count: number;
  events_count: number;
  skipped_count: number;
  /** 中间过程持续累加,SSE 失败降级 polling 时用作 chunk 进度信号 */
  tokens_input: number;
  tokens_output: number;
  cost_yuan: number;
  error_message: string | null;
  started_at: string;
  completed_at: string | null;
  /** 当前 backend 进程是否有活跃 worker;false + state in extracting_* = 僵尸态 */
  is_alive: boolean;
  /** state='failed' 且 chunk_results 不空 → 用户可点继续抽取(从断点恢复) */
  resumable: boolean;
  /** 已完成块数 — 给前端显"已抽 N 块,可恢复成本" */
  completed_chunks_count: number;
}

// ========== 反事实变量 + 重塑度三维度(Sprint 2.C)==========

/** 与后端 schemas/counterfactual.CounterfactualChangeResponse 对齐 */
export interface CounterfactualChange {
  id: string;
  project_id: string;
  /** 'world' 是 2.C+ 加 — 全局世界观反事实(target_id 固定 '_global_') */
  target_type: "character" | "event" | "relationship" | "world";
  target_id: string;
  field: string;
  old_value: string | null;
  new_value: string | null;
  /** 2.C+ 用户自然语言意图(LLM 编排时优先级最高) */
  user_intent: string | null;
  created_at: string;
  reverted_at: string | null;
  is_active: boolean;
  applied_in_simulations: string[];
}

/** 2.C+ world 反事实的 6 个合法 field */
export type WorldCounterfactualField =
  | "genre"
  | "setting"
  | "magic_system"
  | "time_axis"
  | "tone"
  | "free_form";

/** 2.C+ world field 的中文标签 — 保留 label 用于 UI 显示;
 *  placeholder / example 由「使用指南」统一承载,字段输入框不再预设例句 */
export const WORLD_FIELD_META: Record<
  WorldCounterfactualField,
  { label: string }
> = {
  genre: { label: "体裁" },
  setting: { label: "背景设定" },
  magic_system: { label: "超能力体系" },
  time_axis: { label: "时间轴" },
  tone: { label: "整体基调" },
  free_form: { label: "自由描述" },
};

/** POST /api/projects/{id}/counterfactuals 请求体 */
export interface CreateCounterfactualRequest {
  target_type: "character" | "event" | "relationship";
  target_id: string;
  field: string;
  old_value?: string | null;
  new_value?: string | null;
  user_intent?: string | null;
}

/** POST /api/projects/{id}/counterfactuals/world 请求体 */
export interface CreateWorldCounterfactualRequest {
  field: WorldCounterfactualField;
  old_value?: string | null;
  new_value: string;
  user_intent?: string | null;
}

/** GET /api/projects/{id}/counterfactuals 返回 — active 列表 + 按 type 分桶 */
export interface CounterfactualOverview {
  total_active: number;
  by_type: { character: number; event: number; relationship: number };
  items: CounterfactualChange[];
}

/** GET /api/projects/{id}/reshape_preview?reshape_percent=X 返回 — 三维度实时预览 */
export interface ReshapePreview {
  reshape_percent: number;
  /** 第 1 维:该 reshape % 下用户能改的最大角色数 */
  max_touched_characters: number;
  /** 第 2 维:派生的 agent 互动轮次 */
  rounds_planned: number;
  /** 第 3 维:与改动节点的 BFS 影响半径(0-8 跳) */
  graph_distance_hops: number;
  /** 当前已改的去重 character 数 */
  current_touched_count: number;
  /** 当前 BFS 触达的节点 id 集合(给 3D 图谱 highlight 用) */
  current_affected_node_ids: string[];
  /** 当前用户 plan 的 reshape 上限(给滑块禁用区显) */
  plan_max_percent: number;
  /** Sprint 6.A2 M3.D-fix2 v2(2026-05-18):基于重塑度推断的字数区间
   *  前端 SimulationDock 叙事长度滑块应 clamp 到 [chars_low, chars_high]
   *  防止用户选出"50% 重塑度 + 4000 字"这种不匹配组合 */
  chars_center: number;
  chars_low: number;
  chars_high: number;
  chars_label: string;
}

/** POST /api/counterfactuals/{id}/revert 返回 */
export interface RevertCounterfactualResult {
  counterfactual: CounterfactualChange;
  /** 是否成功把 db 字段还原到 old_value(失败也 200,前端给 toast) */
  target_field_restored: boolean;
  restore_error: string | null;
}

/** POST /projects/{id}/simulations 拒新 422 detail(角色数超 reshape 上限) */
export interface ReshapeCharacterLimitDetail {
  code: "RESHAPE_CHARACTER_LIMIT_EXCEEDED";
  current: number;
  limit: number;
  reshape_percent: number;
  message: string;
}

// ============================================================
// Sprint 6.A2 CT(2026-05-21)— 反事实组合树
// ============================================================

/** 一个勾选的反事实变量(2 态:label_a 原 / label_b 改)*/
export interface SelectedVariable {
  counterfactual_id: string;
  label_a: string;
  label_b: string;
}

/** POST /api/projects/{pid}/counterfactual-combinations 请求体 */
export interface CreateCombinationRunRequest {
  selected_variables: SelectedVariable[]; // 1-3 个
  reshape_percent: number; // 10-90
  target_chars: number;
  style: string;
  custom_style_hint?: string | null;
  use_outline_first: boolean;
  divergence: string;
}

/** POST /api/projects/{pid}/counterfactual-combinations/preview 请求体 */
export interface CombinationPreviewRequest {
  selected_variable_count: number; // 1-3
  reshape_percent: number;
  target_chars: number;
}

/** POST .../preview 响应 */
export interface CombinationPreviewResponse {
  total_combinations: number; // 2 / 4 / 8
  estimated_token_per_sim: number;
  estimated_total_tokens: number;
  estimated_minutes_per_sim: number;
  estimated_total_minutes: number;
  estimated_credits: number;
}

/** 批次记录 — POST 创建 + GET tree 都返此结构作为 combination_run 字段 */
export interface CombinationRun {
  id: string;
  project_id: string;
  user_id: string;
  selected_variables: SelectedVariable[];
  total_combinations: number;
  state: "pending" | "generating" | "partial" | "done" | "failed";
  error_message?: string | null;
  created_at: string;
  completed_at?: string | null;
}

/** 决策树叶节点 sim 状态 */
export interface CombinationLeafSim {
  simulation_id: string;
  tree_path: ("a" | "b")[]; // 长度 = selected_variables 数
  sim_state: string; // queued / generating / completed / failed
  sim_narrative_chars: number;
  sim_created_at: string;
  sim_completed_at?: string | null;
  // 2026-06-05:outline-first 模式 sim 创建后 outline 状态(awaiting_user 需要用户审核)
  outline_id?: string | null;
  outline_state?: string | null; // drafting / awaiting_user / done / failed
}

/** GET /api/counterfactual-combinations/{id}/tree 响应 */
export interface CombinationTreeResponse {
  combination_run: CombinationRun;
  leaves: CombinationLeafSim[];
}

// ========== 全局搜索(Sprint 6.A2 路线图 #6,2026-05-23)==========

export interface SearchProjectItem {
  id: string;
  name: string;
  mode: string;
  tags: string[];
}

export interface SearchCharacterItem {
  id: string;
  name: string;
  project_id: string;
  project_name: string;
  identity_excerpt: string;
}

export interface SearchEventItem {
  id: string;
  description: string;
  time_anchor: string | null;
  project_id: string;
  project_name: string;
}

export interface SearchSceneItem {
  id: string;
  name: string;
  description: string;
  project_id: string;
  project_name: string;
}

export interface SearchSimulationItem {
  id: string;
  divergence: string;
  state: string;
  created_at: string;
  project_id: string;
  project_name: string;
}

// 2026-06-09 新增 — 剧创态 + 漫创态搜索结果

/** 剧创态小说(sp_novels) */
export interface SearchNovelItem {
  id: string;
  title: string;
  total_chapters: number;
  total_chars: number;
  source_format: string;
}

/** 剧创态剧本(sp_screenplays)— 通过 novel 关联,展示用 novel_title */
export interface SearchScreenplayItem {
  id: string;
  novel_id: string;
  novel_title: string;
  scene_count: number;
  created_at: string;
}

/** 漫创态作品(comic_projects) */
export interface SearchComicItem {
  id: string;
  name: string;
  state: string;
  progress_percent: number;
  style_tag: string | null;
}

export interface GlobalSearchResponse {
  query: string;
  projects: SearchProjectItem[];
  characters: SearchCharacterItem[];
  events: SearchEventItem[];
  scenes: SearchSceneItem[];
  simulations: SearchSimulationItem[];
  // 2026-06-09 新增(后端 schema 加了 default_factory=list,老 server 不会断):
  novels?: SearchNovelItem[];
  screenplays?: SearchScreenplayItem[];
  comics?: SearchComicItem[];
}

// ========== 错误响应 ==========

export interface ApiErrorDetail {
  code: string;
  message: string;
  [key: string]: unknown;
}

export interface ApiErrorBody {
  detail: ApiErrorDetail | string;
}

/** 统一 API 错误对象,client 抛出 */
export class ApiError extends Error {
  status: number;
  code: string;
  detail: unknown;

  constructor(status: number, code: string, message: string, detail?: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

// ========== P3 作者指南针 Agent(2026-05-26)==========
// 让续作真正"像川端 / 像村上",双轨制(外部研究 + 内部反推)+ 用户审阅锁定

export type AuthorCompassStatus = "pending" | "running" | "done" | "failed";

/**
 * 外部研究轨结果 — LLM 用世界知识吐作家结构化画像。
 * 字段允许部分缺失(LLM 不确定就空)。
 */
export interface AuthorExternalProfile {
  流派?: string;
  年代?: string;
  文化背景?: string;
  主题偏好?: string[];
  风格标签?: string[];
  雷区?: string[];
  代表手法?: string[];
  致敬作品?: string[];
  /** 用户可能添加任意字段(锁定后 final_compass 自由扩展)*/
  [key: string]: unknown;
}

/**
 * 内部反推轨结果 — LLM 读原作分布采样反推量化参数。
 */
export interface AuthorInternalMetrics {
  句长?: {
    短句占比?: number;
    中句占比?: number;
    长句占比?: number;
    评注?: string;
  };
  对白率?: {
    比例?: number;
    评注?: string;
  };
  感官比例?: {
    视觉?: number;
    听觉?: number;
    嗅觉?: number;
    触觉?: number;
    味觉?: number;
    评注?: string;
  };
  段落节奏?: {
    平均段长字数?: number;
    短长段配比?: string;
    评注?: string;
  };
  意象偏好?: string[];
  视角?: {
    人称?: string;
    全知或限知?: string;
    评注?: string;
  };
  基调?: {
    情感色彩?: string;
    节奏感?: string;
    评注?: string;
  };
  // P4(2026-05-27):身体描写尺度 — 灵魂续写关键维度
  // 2026-06-06:补类型声明 — 之前依赖 [key: string]: unknown,组件访问 .频率/.直白度/.评注 报错
  身体描写尺度?: {
    频率?: string;
    直白度?: string;
    评注?: string;
  };
  [key: string]: unknown;
}

export interface AuthorCompassResponse {
  id: string;
  project_id: string;

  author_name: string | null;
  work_title: string | null;

  external_profile: AuthorExternalProfile | null;
  external_status: AuthorCompassStatus;
  external_error: string | null;
  external_at: string | null;

  internal_metrics: AuthorInternalMetrics | null;
  internal_status: AuthorCompassStatus;
  internal_error: string | null;
  internal_at: string | null;

  /** 用户审阅后的最终版本(锁定后续作读这版)*/
  final_compass: Record<string, unknown> | null;
  user_locked: boolean;

  created_at: string;
  updated_at: string;
}

export interface AnalyzeAuthorCompassRequest {
  author_name?: string;
  work_title?: string;
}

export interface UpdateAuthorCompassRequest {
  author_name?: string;
  work_title?: string;
  final_compass?: Record<string, unknown>;
  user_locked?: boolean;
}

// ==========================================================
// BYOK 自携密钥(2026-06-04)
//   用户花 30 元/月 买激活码 → 输入解锁 → 配自己的 LLM API key
//   过期自动回到平台默认 key
// ==========================================================

export type BYOKModality = "text" | "image";

export type BYOKProviderId =
  // 文本
  | "deepseek"
  | "qwen"
  | "zhipu"
  | "doubao"
  | "moonshot"
  | "custom"
  // 图像(v5 item2)
  | "siliconflow"
  | "seedream"
  | "cogview"
  | "custom_image";

export interface BYOKStatusResponse {
  has_active_subscription: boolean;
  has_unused_subscription: boolean;
  active_subscription_expires_at: string | null;
  active_code: string | null;
  configs_count: number;
  default_provider: string | null;
}

export interface BYOKPurchaseRequest {
  months: number;
}

export interface BYOKPurchaseResponse {
  code: string;
  expires_at: string;
  price_cents: number;
}

export interface BYOKActivateRequest {
  code: string;
}

export interface BYOKActivateResponse {
  is_active: boolean;
  expires_at: string;
}

export interface BYOKConfigUpsertRequest {
  provider: BYOKProviderId;
  display_name?: string | null;
  base_url: string;
  model_name: string;
  api_key: string;
  is_default: boolean;
  modality?: BYOKModality;   // v5 item2:默认 text
}

export interface BYOKConfigResponse {
  id: string;
  provider: string;
  display_name: string | null;
  base_url: string;
  model_name: string;
  api_key_mask: string;
  is_default: boolean;
  modality?: BYOKModality;   // v5 item2
  last_test_ok: boolean | null;
  last_test_at: string | null;
  last_test_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface BYOKConfigsListResponse {
  configs: BYOKConfigResponse[];
}

export interface BYOKTestResponse {
  ok: boolean;
  error: string | null;
  latency_ms: number | null;
}

// ==========================================================
// BYOK 个人收款码支付(2026-06-05)
// ==========================================================

export interface BYOKPaymentConfigResponse {
  payee_name: string;
  wechat_qr_url: string | null;
  alipay_qr_url: string | null;
  monthly_price_yuan: number;
}

export interface CreatePaymentOrderRequest {
  months: number;
}

export interface CreatePaymentOrderResponse {
  order_id: string;
  amount_cents: number;
  amount_display: string;
  months: number;
  payee_name: string;
  wechat_qr_url: string;
  alipay_qr_url: string | null;
  expires_at: string;
  instruction_text: string;
}

export type BYOKOrderStatus =
  | "pending"
  | "submitted"
  | "paid"
  | "rejected"
  | "manual_review"
  | "expired";

export interface PaymentOrderStatusResponse {
  order_id: string;
  status: BYOKOrderStatus;
  amount_cents: number;
  months: number;
  created_at: string;
  expires_at: string;
  proof_submitted_at: string | null;
  detected_amount_cents: number | null;
  detected_payee_name: string | null;
  detected_pay_time: string | null;
  detection_pass_reason: string | null;
  rejected_reason: string | null;
  activated_code: string | null;
  activated_expires_at: string | null;
}

export interface SubmitProofResponse {
  order_id: string;
  status: BYOKOrderStatus;
  message: string;
}

// ============================================================
// BYOK admin 后台(2026-06-05)— 仅 founder 用
// ============================================================

export interface AdminPaymentOrderResponse {
  order_id: string;
  user_id: string;
  user_email: string | null;
  status: BYOKOrderStatus;
  amount_cents: number;
  months: number;
  created_at: string;
  expires_at: string;
  proof_submitted_at: string | null;
  proof_image_url: string | null;

  detected_amount_cents: number | null;
  detected_payee_name: string | null;
  detected_pay_time: string | null;
  detected_transaction_id: string | null;
  detection_pass_reason: string | null;
  detection_run_at: string | null;

  rejected_reason: string | null;
  activated_subscription_id: string | null;
  activated_code: string | null;
  activated_expires_at: string | null;
}

export interface AdminPaymentOrdersListResponse {
  orders: AdminPaymentOrderResponse[];
  total: number;
  pending_review_count: number;
}

export interface AdminApproveRequest {
  note?: string | null;
}

export interface AdminRejectRequest {
  reason: string;
}
