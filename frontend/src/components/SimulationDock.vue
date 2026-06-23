<script setup lang="ts">
/**
 * SimulationDock — 续写引擎模态(Sprint 1.H)。
 *
 * 4 屏(根据 useSimulation.phase 派生):
 *   configuring / creating  填表 + 提交中
 *   running                 后台跑,2s 轮询展示进度
 *   done                    展示 narrative(简陋版,1.I 升级 NarrativeStream)
 *   failed / error          失败展示,提供"重试"回 configuring
 *
 * Props:
 *   open             父控制开关
 *   projectId        当前项目 id
 *   projectName      done 屏展示
 *   characterCount   父传入的角色数,< 3 时禁用提交按钮
 *
 * Emits:
 *   close                       用户关闭(任何阶段。running 中关 = 后台继续跑)
 *   quota-exceeded(detail)      配额超限(continuation 或 reshape_percent),
 *                               父弹 UpgradeModal
 *
 * UX 关键:
 *   - reshape 滑块 max 由 quota.status.limits.reshape_max_percent 限制(动态)
 *   - 角色 < 3 / 配额 0 → submit 按钮 disabled + banner 提示,不让用户拖到提交才报错
 *   - running 中允许关 modal — 后端独立跑,不依赖前端连接
 */
import { computed, ref, watch } from "vue";
import { useRouter } from "vue-router";

import { api } from "../api/client";
import {
  type CreateSimulationRequest,
  type InsufficientCreditsDetail,
  type QuotaExceededDetail,
  type ReshapeCharacterLimitDetail,
  type SimulationStyle,
  type SimulationSummary,
  type ProjectForeshadow,
  type ProjectForeshadowsResponse,
  type ProjectMode,
} from "../api/types";
import CreditEstimate from "./CreditEstimate.vue";
import NarrativeStream from "./NarrativeStream.vue";
import ReshapeSlider from "./ReshapeSlider.vue";
import {
  formatSimulationEvent,
  useSimulation,
  type FormattedEvent,
} from "../composables/useSimulation";
import { estimateForAction } from "../composables/useCreditEstimate";
import { useCounterfactuals } from "../composables/useCounterfactuals";
import { useQuotaStore } from "../stores/quota";
import { toast } from "../composables/useToast";

const props = defineProps<{
  open: boolean;
  projectId: string;
  projectName: string;
  characterCount: number;
  /**
   * 1.M.3:从 3D 图谱框选 N 角色后点「AI 续写」时,父组件传过来的预填文本
   * (例如 "围绕林晚 / 苏宁 / 周野,接下来发生……")。打开时若非空就填进
   * divergence,用户可继续编辑。
   */
  prefillDivergence?: string;
  /**
   * Sprint 6.A2 M7.I(2026-05-20)— 父打开 dock 时预填"滚雪球前文 sim"。
   * 场景:用户在 SimulationDetailView 点「📖 基于本篇续写」→ 跳回项目 + 自动打开 dock +
   * 已勾选当前 sim 作为前文。打开时若非空,snowballOn 自动开 + 预选这些 id。
   */
  prefillContextIds?: string[];
  /**
   * Sprint 6.A2 M7.J(2026-05-20)— 项目下的 events 列表(供 anchor dropdown 选)
   * 仅 projectMode='middle' 时使用;父传过来 [{ id, description }]
   * 不传 / 空数组 → dock 不显示 anchor 区
   */
  projectEvents?: { id: string; description: string }[];
  /**
   * Sprint 6.A2 M7.K(2026-05-20)— 来自 SimulationDetailView LLM-only 类采纳跳转
   * 包含 4 个可选 patch:reshape_percent_delta / target_chars_delta / custom_style_hint /
   * divergence_prefix;打开时应用到对应字段(delta 是增量,prefix 是 prepend)
   */
  prefillSimPatch?: {
    reshape_percent_delta?: number;
    target_chars_delta?: number;
    custom_style_hint?: string;
    divergence_prefix?: string;
  } | null;
  /**
   * Sprint 6.A2 M7.K-fix(2026-05-20)— issue 中文标签(如"转折突兀" / "对白扁平")
   * 用于 banner 显示"已应用自洽守护者建议(<issueLabel>)"提示用户字段是预填的
   */
  auditSourceLabel?: string;
  /**
   * 2.C:可选 — 父(ProjectGraphView)如果想让 SimulationDock 与 CounterfactualWorkbench
   * 共享同一个 useCounterfactuals 状态,传过来。不传则 dock 自己实例化(向下兼容)。
   */
  counterfactualsInstance?: ReturnType<typeof useCounterfactuals>;
  /**
   * 2.C+ 项目 mode — 按 mode 差异化标题 / 子标题 / 反事实摘要区:
   *   initial → AI 推演(从用户设的世界开始演)
   *   middle  → AI 重塑(基于反事实变量推演 what-if + 显反事实摘要)
   *   end     → AI 续写(从原作末尾接,不动原作)
   *   cycle   → AI 长篇(在已有产物上累积新章 — 暂同 middle)
   */
  // ProjectMode 含 screenplay/more,但本 dock 仅对经典态(initial/middle/end/cycle)
  // 有意义;screenplay/more 项目不会打开此 dock,落 dockHeaderConfig 默认分支即可。
  projectMode?: ProjectMode;
  /**
   * 2.C+ 父传"打开反事实工作台"回调 — 中间态 dock 摘要区会有 [✎ 编辑反事实] 按钮调它
   */
  onOpenWorkbench?: () => void;
}>();

const emit = defineEmits<{
  (e: "close"): void;
  // Sprint C.3:detail 类型扩为 union — 资源容量类(QUOTA_EXCEEDED)用 UpgradeModal,
  // credit 不足(INSUFFICIENT_CREDITS)用 AddonPurchaseModal
  (
    e: "quota-exceeded",
    detail: QuotaExceededDetail | InsufficientCreditsDetail,
  ): void;
  /**
   * Sprint 6.A2 #2.5(2026-05-22):sim 创建成功通知父
   * ProjectView 监听用于 mark pending audit patches applied(DB 标 applied_at)
   * 仅在 session.start 不 error 且 simulation 已落库时发出
   */
  (e: "created"): void;
}>();

const session = useSimulation(() => props.projectId);
const quota = useQuotaStore();
const router = useRouter();

// Sprint 2.C 反事实变量 + 重塑度三维度
// 优先用父传的 instance(与 CounterfactualWorkbench 共享状态);没传就自己 fallback
const cf = props.counterfactualsInstance ?? useCounterfactuals(props.projectId);
watch(
  () => props.projectId,
  (pid) => {
    if (pid && !props.counterfactualsInstance) cf.bind(pid);
  },
  { immediate: true },
);

// 2.C+ 按 mode 差异化标题 / 子标题
const dockHeaderConfig = computed(() => {
  const m = props.projectMode ?? "initial";
  if (m === "middle") return {
    icon: "⟲",
    title: "AI 重塑",
    subtitle: "基于反事实变量推演 what-if — 改一个变量,故事走向另一条路",
  };
  if (m === "end") return {
    icon: "→",
    title: "AI 续写",
    subtitle: "从原作末尾接着写,不动原作正文",
  };
  if (m === "cycle") return {
    icon: "∞",
    title: "AI 长篇",
    subtitle: "在已有产物上累积新章,反事实可逐轮叠加",
  };
  return {
    icon: "✦",
    title: "AI 推演",
    subtitle: "用你的角色互动推演故事",
  };
});

// M7.I(2026-05-20):已选反事实数(给摘要区 badge "已选 N/总数" 用)
const cfSelectedCount = computed(
  () => cf.items.value.filter((c) => cf.isSelected(c.id)).length,
);

// 2.C+ 中间态 / 周期态显反事实摘要区
const showCounterfactualSummary = computed(
  () => (props.projectMode === "middle" || props.projectMode === "cycle")
        && cf.totalActive.value > 0,
);

// ============================================================
// 表单 state(configuring 阶段使用)
// ============================================================

const divergence = ref("");
const reshapePercent = ref(50);
const targetChars = ref(4000);
const style = ref<SimulationStyle>("auto");
const customStyleHint = ref("");
// Sprint 6.A2 M3.C(2026-05-18):续写模式选择
// 'quick' 默认 — 单 LLM 编排,30-60s,~78 credits / 篇
// 'evolution' 灵魂续写 — 多 agent 独立 LLM 进程 + 私有记忆 + RAG 召回原著 chunk
//   5-15min,400-800 credits / 篇,但产物质量大幅提升("agent 像真人")
const simulationMode = ref<"quick" | "evolution">("quick");
// Sprint 6.A2 M6(2026-05-20):outline-first 长篇生成(仅 evolution 支持)
// 默认开 — 用户拍板"该有的功能不要少",治本路径作为默认体验
const useOutlineFirst = ref<boolean>(true);
// P2.A(2026-05-24):走向终章 — 默认 false 便于超长篇续作
const withGrandFinale = ref<boolean>(false);
// P2.B(2026-05-24):叙事节奏档(默认 standard;dock 打开时由 AI 推断覆盖)
const narrativePacing = ref<"slow" | "standard" | "fast">("standard");
// P0H.2(2026-05-24):每章字数 — 兼容字段
// 2026-06-02 hotfix:UI 控件已删(项目级 chapter_size_min/max 区间卡覆盖),
//                   此 ref 保留默认 2000 仅用于 sim 创建时填 chapter_size_chars 字段(老数据兼容)
const chapterSizeChars = ref<number>(2000);
// P2.B 升级:AI 推断的节奏(只读,供 select 上方"AI 推断徽标"展示)
const inferredPacing = ref<"slow" | "standard" | "fast" | null>(null);
const inferredPacingReasoning = ref<string | null>(null);
const pacingInferring = ref<boolean>(false);  // dock 打开时同步调推断 endpoint 期间显 loading
const pacingUserChanged = ref<boolean>(false);  // 用户手动改过 → 不再被 AI 覆盖

// M6-fix2(2026-05-20):基于 target_chars 智能提示是否开 outline-first
const outlineFirstHint = computed<{ tone: string; icon: string; text: string } | null>(
  () => {
    // 只在 evolution 模式下提示;quick 模式无 outline 概念
    if (simulationMode.value !== "evolution") return null;
    const chars = targetChars.value;
    // 长篇关闭 → 红色警告
    if (chars >= 5000 && !useOutlineFirst.value) {
      return {
        tone: "warn",
        icon: "⚠️",
        text: `长篇(${chars.toLocaleString()} 字)未开大纲,容易出现剧情瑕疵,建议开启`,
      };
    }
    // 长篇已开 → 绿色确认
    if (chars >= 5000 && useOutlineFirst.value) {
      return {
        tone: "ok",
        icon: "✓",
        text: `长篇(${chars.toLocaleString()} 字)已开大纲,创建后可先审核再生成`,
      };
    }
    // 短篇仍开 → 灰色提示(成本)
    if (chars < 2500 && useOutlineFirst.value) {
      return {
        tone: "info",
        icon: "ℹ",
        text: `短篇(${chars.toLocaleString()} 字)开大纲收益不大,可省时间`,
      };
    }
    // 中等 + 已开:无提示(默认体验)
    // 中等 + 未开:中性建议
    if (chars >= 2500 && chars < 5000 && !useOutlineFirst.value) {
      return {
        tone: "info",
        icon: "💡",
        text: `中篇(${chars.toLocaleString()} 字)建议开大纲,降低剧情瑕疵`,
      };
    }
    return null;
  },
);

// 滚雪球(Sprint 1.O)
const snowballOn = ref(false);

// M7.K-fix(2026-05-20)audit 预填 banner — 让用户感知到"reshape/笔法/锚点是采纳建议预填的"
// dock open 时若 prefillSimPatch 非空 → 显示 banner 列预填项;用户点 × 关闭(本次会话不再显)
const auditBannerDismissed = ref(false);
const auditBannerEntries = ref<string[]>([]);   // 每条 = "重塑度 50 → 30(降 reshape 让 LLM 更保守)" 这种描述

/**
 * Sprint 6.A2 polish(2026-05-22):patch 应用 → cf.preview clamp 的精确 banner 协调
 * 字数 patch 应用时 cf.preview 还没拉到 → 应用值可能越界 → 之后 watch clamp 拉回
 * 用 pending 暂存"建议值",等 cf.preview ready + clamp 发生后,在 watch 里生成最终 banner 文案
 * 让用户看到"建议 3000 字 → 受 reshape 50% 字数区间约束,实际 9800"这种诚实信息
 */
const pendingTargetCharsAdjust = ref<{ before: number; suggested: number } | null>(null);

// P-1 修复(2026-05-23):用户手动拖 reshape 滑块后,audit banner 显示与实际字数不符
// 根因:`pendingTargetCharsAdjust` 是 use-once,首次 preview clamp 后清空;之后用户改 reshape
// preview 重拉 → watch clamp 仍触发,但已没有 pending 数据可用 → banner 文案陈旧
// 修法:记录 patch 应用完成后的 reshape baseline;用户手动改 reshape 时清整个 banner(过期建议)
let _reshapePostPatch: number | null = null;
const showAuditBanner = computed(
  () =>
    !!props.prefillSimPatch
    && !auditBannerDismissed.value
    && auditBannerEntries.value.length > 0,
);

// M7.J(2026-05-20):中间态起点锚点(仅 middle 显示;默认空 = 旧"独立新场景"语义)
const anchorEventId = ref<string>("");
const showAnchorField = computed(
  () => props.projectMode === "middle"
    && (props.projectEvents ?? []).length > 0,
);
const anchorEventPreview = computed(() => {
  if (!anchorEventId.value) return "";
  const ev = (props.projectEvents ?? []).find((e) => e.id === anchorEventId.value);
  return ev?.description ?? "";
});
const projectDoneSims = ref<SimulationSummary[]>([]);
const selectedContextIds = ref<string[]>([]);
const loadingPriorList = ref(false);

// 阶段 3B(2026-06-02):项目级伏笔继承面板
// 用户主动选择哪些跨代伏笔继承到本次续作 — null = 默认全继承(向后兼容)
// 加载时机:勾"接续之前剧情"且有 context → fetch
// 默认勾选策略:**P1 高优先级默认勾,P2/P3 默认不勾**(用户拍板)
const projectForeshadows = ref<ProjectForeshadow[]>([]);
const selectedForeshadowIds = ref<Set<string>>(new Set());
const loadingForeshadows = ref(false);

async function loadProjectForeshadows() {
  if (!props.projectId) return;
  loadingForeshadows.value = true;
  try {
    const data = await api.get<ProjectForeshadowsResponse>(
      `/projects/${props.projectId}/foreshadows`,
    );
    projectForeshadows.value = data.foreshadows ?? [];
    // 默认勾选策略:P1(high)默认勾,P2/P3 默认不勾
    const defaultChecked = new Set<string>();
    for (const f of projectForeshadows.value) {
      if (f.priority === "high") defaultChecked.add(f.id);
    }
    selectedForeshadowIds.value = defaultChecked;
  } catch {
    projectForeshadows.value = [];
    selectedForeshadowIds.value = new Set();
  } finally {
    loadingForeshadows.value = false;
  }
}

function toggleForeshadow(id: string) {
  const s = new Set(selectedForeshadowIds.value);
  if (s.has(id)) s.delete(id);
  else s.add(id);
  selectedForeshadowIds.value = s;
}

// 当 snowball 开 + 有 context 时显示伏笔面板
const showForeshadowPanel = computed(
  () => snowballOn.value && selectedContextIds.value.length > 0,
);

// snowball 开起来时拉伏笔
watch(showForeshadowPanel, (newVal) => {
  if (newVal && projectForeshadows.value.length === 0) {
    void loadProjectForeshadows();
  }
});

// 2026-06-01:过滤出可作前作的 sim(排除合并产物 — is_final_compilation 不能再作为前作)
const eligiblePriorSims = computed<SimulationSummary[]>(() =>
  projectDoneSims.value.filter((s) => !s.is_final_compilation),
);

// 2026-06-01:计算每个 sim 的祖先链 + 自己(从根 → 自己,按时序)
// 返该 sim 应作为"链尾"时的完整 selectedContextIds
function getSimAncestorChain(simId: string): string[] {
  const byId = new Map(eligiblePriorSims.value.map((s) => [s.id, s]));
  const sim = byId.get(simId);
  if (!sim) return [];

  // ancestors_chain 是后端 M7.D 已计算好的"祖先链(不含自身)"
  // InheritanceChainNode 字段:{ id, divergence_short, depth }
  // depth=0 是原作根节点;按 depth 升序拍 = 根 → 父辈;再拼上 simId(本 sim 是链尾)
  const ancestors = (sim.ancestors_chain ?? [])
    .slice()
    .sort((a, b) => a.depth - b.depth); // depth 小 = 根节点优先
  const chain: string[] = ancestors.map((a) => a.id).filter(Boolean);
  chain.push(simId);
  return chain;
}

// 同代分叉判定:返 sim 在当前 selectedContextIds 链中"应在第几代"
// 若已选 sim X 跟新 sim Y 同 inheritance_depth 但 X !== Y → Y 被 disabled
function isDisabledBySameDepthConflict(simId: string): boolean {
  const byId = new Map(eligiblePriorSims.value.map((s) => [s.id, s]));
  const sim = byId.get(simId);
  if (!sim) return false;
  if (selectedContextIds.value.includes(simId)) return false; // 已选不算冲突
  // 当前选中链里有没有同 depth 但不同 id 的 sim?
  const myDepth = sim.inheritance_depth ?? 0;
  for (const selId of selectedContextIds.value) {
    if (selId === simId) continue;
    const sel = byId.get(selId);
    if (!sel) continue;
    if ((sel.inheritance_depth ?? 0) === myDepth) {
      return true; // 同代分叉冲突
    }
  }
  return false;
}

// 该 sim 是否为"强制锁定的祖先"(被链尾自动展开选中,用户不能取消)
function isLockedAncestor(simId: string): boolean {
  // 链尾 = selectedContextIds 最后一个;链尾自动展开的祖先都被锁
  if (selectedContextIds.value.length === 0) return false;
  const tailId = selectedContextIds.value[selectedContextIds.value.length - 1];
  return simId !== tailId && selectedContextIds.value.includes(simId);
}

function getGenerationLabel(sim: SimulationSummary): string {
  const d = sim.inheritance_depth ?? 0;
  if (d === 0) return "第 1 代(独立起步)";
  return `第 ${d + 1} 代接续`;
}

const totalPriorChars = computed(() => {
  // 估算:每条 narrative 大约 = (rounds * 250)字 ;粗略上限提示用
  // 实际后端按 narrative 字数 8000 阈值决定全文/摘要
  let total = 0;
  for (const sim of eligiblePriorSims.value) {
    if (selectedContextIds.value.includes(sim.id)) {
      total += sim.rounds_planned * 250;
    }
  }
  return total;
});

const reshapeMax = computed(() =>
  quota.status?.limits.reshape_max_percent ?? 30,
);

// 2026-06-02:统计当前列表中"被同代互斥禁用"的姐妹分支数量(给 banner 用)
const siblingBranchCount = computed(() => {
  if (selectedContextIds.value.length === 0) return 0;
  let cnt = 0;
  for (const sim of eligiblePriorSims.value) {
    if (selectedContextIds.value.includes(sim.id)) continue;
    if (isLockedAncestor(sim.id)) continue;
    if (isDisabledBySameDepthConflict(sim.id)) cnt += 1;
  }
  return cnt;
});

// Sprint C.1 credit 重构(2026-05-13):
// 删除 continuation_per_month 次数闸门 — 提交时仅检查 credit 余额是否 > 0
// (LLM 真扣 credit 在 simulation 跑完后,按真实 token 数;此处只防"明显余额耗尽")
const creditBalance = computed<number>(
  () => quota.status?.credit_balance.total_credits ?? 0,
);

// Sprint C.3:提交按钮上方"预计消耗"卡片的预估
const creditEstimate = computed(() =>
  estimateForAction("simulation", {
    reshapePercent: reshapePercent.value,
    contextSimulationCount: selectedContextIds.value.length,
  }),
);

/** 提交按钮可用条件:文本合法 + 角色够 + credit > 0 + 不在创建中 + custom 时 hint 够长 + 滚雪球开但没选前文 → 不让提交
 *  Sprint 3.A:末尾态跳过 divergence 长度检查(用户拍板不让用户写锚点) */
const canSubmit = computed(() => {
  const len = divergence.value.trim().length;
  const customOk =
    style.value !== "custom" || customStyleHint.value.trim().length >= 10;
  const snowballOk =
    !snowballOn.value || selectedContextIds.value.length > 0;
  const divergenceOk = isEndMode.value || (len >= 10 && len <= 500);
  return (
    divergenceOk &&
    reshapePercent.value >= 10 &&
    reshapePercent.value <= reshapeMax.value &&
    props.characterCount >= 3 &&
    creditBalance.value > 0 &&
    customOk &&
    snowballOk &&
    session.phase.value === "configuring"
  );
});

/** divergence 字段的 label:独立推演 vs 接续推演 用不同措辞,意图更准 */
const divergenceLabel = computed(() =>
  snowballOn.value ? "接下来想看到的剧情" : "锚点描述",
);

/**
 * Sprint 3.A 末尾态:用户拍板"末尾态是继承原作作者意志,不应让用户写锚点描述
 * (用户的锚点会污染产物方向)"。dock 在 end mode 隐藏 divergence 输入,
 * 提交时前端自动填一段固定文案让后端 schema 通过(min_length=10 校验)。
 *
 * 这段固定文案在 director / composer prompt 里仍会作为"用户意图"展示,但
 * 措辞中性,LLM 实际编排时仍以"原作末段"section(铁律 9.5 / 3.A)为最高优先级。
 */
const isEndMode = computed(() => props.projectMode === "end");
const END_MODE_DEFAULT_DIVERGENCE =
  "继承原作末段的作者意志,自然延续剧情,不引入用户额外干预";

/** 诊断:为什么 submit 按钮 disabled — 给用户具体原因,避免"按钮灰了不知道为啥" */
const submitBlockedReason = computed<string | null>(() => {
  if (session.phase.value === "creating") return null; // 创建中显"创建中…"足够
  const len = divergence.value.trim().length;
  // Sprint 3.A 末尾态:不要求 divergence,跳过锚点长度类的 blocked reason
  if (!isEndMode.value) {
    if (len === 0) return "先填一段锚点描述";
    if (len < 10) return `锚点描述至少 10 字,当前 ${len} 字`;
    if (len > 500) return `锚点描述最多 500 字,当前 ${len} 字`;
  }
  if (props.characterCount < 3) {
    return `角色不足:需要至少 3 个,当前 ${props.characterCount} 个`;
  }
  if (
    quota.status?.plan !== "founder" &&
    creditBalance.value <= 0
  ) {
    // Sprint C.1:credit 余额耗尽
    return `credit 余额已用尽,升档 / 加购才能继续 AI 创作`;
  }
  if (reshapePercent.value > reshapeMax.value) {
    return `重塑度超过当前 plan 上限 ${reshapeMax.value}%`;
  }
  if (
    style.value === "custom" &&
    customStyleHint.value.trim().length < 10
  ) {
    return `自定义笔法风格描述至少 10 字,当前 ${customStyleHint.value.trim().length} 字`;
  }
  if (
    snowballOn.value && selectedContextIds.value.length === 0
  ) {
    return `勾了"接续之前的剧情"但未选任何前文`;
  }
  return null;
});

const charsLabel = computed(() => {
  const t = targetChars.value;
  if (t < 5000) return `约 ${(t / 1000).toFixed(1)} 千字短篇`;
  if (t < 15000) return `约 ${(t / 1000).toFixed(0)} 千字中篇`;
  return `约 ${(t / 10000).toFixed(1)} 万字长篇`;
});

// Sprint 6.A2 M3.D-fix2 v2(2026-05-18):字数滑块 min/max 来自 reshape_preview 推断区间
// preview 还没拉时用宽松默认值;拉到后 clamp 到 [chars_low, chars_high]
const charsRangeMin = computed<number>(() => {
  const low = cf.preview.value?.chars_low;
  return typeof low === "number" && low > 0 ? low : 1000;
});
const charsRangeMax = computed<number>(() => {
  const high = cf.preview.value?.chars_high;
  return typeof high === "number" && high > 0 ? high : 30000;
});

// P-1 修复(2026-05-23):用户手动改 reshape 滑块 → banner 已过期,清掉
// _reshapePostPatch 在 patch 应用末尾被设;首次手动改不等于 patch 设的值 → 清 banner
watch(reshapePercent, (newVal) => {
  if (_reshapePostPatch !== null && newVal !== _reshapePostPatch) {
    auditBannerEntries.value = [];
    _reshapePostPatch = null;   // 一次清完防反复
  }
});

// reshape 变 → preview 推断字数区间变 → 若当前 targetChars 越界,自动 clamp 到推荐中心
watch(
  () => cf.preview.value,
  (newPreview) => {
    if (!newPreview) return;
    const { chars_low, chars_high, chars_center } = newPreview;
    const beforeClamp = targetChars.value;
    let wasClamped = false;
    if (beforeClamp < chars_low || beforeClamp > chars_high) {
      // 越界 → 拉回推荐中心(取 500 步对齐值)
      targetChars.value = Math.round(chars_center / 500) * 500;
      wasClamped = true;
    }

    // Sprint 6.A2 polish(2026-05-22):patch 应用阶段暂存的字数建议在这里"定稿"
    // 此时 cf.preview 已 ready,clamp(若发生)也已执行,可以生成最终 banner 文案告诉用户实际值
    if (pendingTargetCharsAdjust.value) {
      const { before: tcBefore, suggested } = pendingTargetCharsAdjust.value;
      const final = targetChars.value;
      const dir = suggested < tcBefore ? "精简" : "拉长";
      if (wasClamped && final !== suggested) {
        // 建议值越界 → 被 clamp 回区间
        auditBannerEntries.value.push(
          `叙事长度 建议 ${suggested.toLocaleString()} 字(${dir})— 当前 reshape ${reshapePercent.value}% 字数区间 `
          + `${chars_low.toLocaleString()}-${chars_high.toLocaleString()},实际调整到 ${final.toLocaleString()} 字`,
        );
      } else if (final !== tcBefore) {
        // 建议值在区间内,正常应用
        auditBannerEntries.value.push(
          `叙事长度 ${tcBefore.toLocaleString()} → ${final.toLocaleString()} 字(${dir})`,
        );
      }
      pendingTargetCharsAdjust.value = null;
    }
  },
);

// Sprint 1.Q 语体自适应 — 不再预设固定档,让 AI 根据角色 / 题材自动判断
// 用户能强制指定时,选"自定义"写一段描述(如"民国白话武侠"/"赛博朋克冷峻"/"沙雕轻喜剧")
const STYLE_OPTIONS: Array<{ value: SimulationStyle; label: string; hint: string }> = [
  {
    value: "auto",
    label: "AI 自动适配",
    hint: "根据你的角色名 / 题材标签自动选合适语体",
  },
  {
    value: "custom",
    label: "自定义",
    hint: "下方写一段语体描述,AI 严格遵守",
  },
];

// ============================================================
// 进入 / 退出
// ============================================================

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      // 重置表单 + 拉一次 quota(reshape max 实时)
      // 1.M.3:若父组件传了 prefillDivergence,优先用它(框选触发的场景)
      divergence.value = props.prefillDivergence ?? "";
      // 默认 50,但夹到 plan 上限
      reshapePercent.value = Math.min(50, reshapeMax.value);
      targetChars.value = 4000;
      style.value = "auto";
      customStyleHint.value = "";
      // M7.I(2026-05-20):接收父预填的 context — 来自 SimulationDetailView 的"基于本篇续写"
      const prefillCtx = props.prefillContextIds ?? [];
      snowballOn.value = prefillCtx.length > 0;
      selectedContextIds.value = prefillCtx.slice(0, 10);    // 上限 10 条
      projectDoneSims.value = [];
      // M7.J(2026-05-20):起点锚点重置(每次开 dock 默认"不设锚点")
      anchorEventId.value = "";

      // M7.K(2026-05-20):应用 sim_config patch — 来自 SimulationDetailView LLM-only 类采纳
      // 在 prefillDivergence + 默认 reshape/target_chars 设完之后应用,patch 是增量
      // M7.K-fix(2026-05-20):每次打开 dock reset banner 状态 + 记录预填项给 banner 显示
      auditBannerDismissed.value = false;
      auditBannerEntries.value = [];
      const patch = props.prefillSimPatch;
      if (patch) {
        if (typeof patch.reshape_percent_delta === "number") {
          const before = reshapePercent.value;
          const after = Math.max(10, Math.min(
            reshapeMax.value,
            before + patch.reshape_percent_delta,
          ));
          reshapePercent.value = after;
          if (after !== before) {
            const dir = patch.reshape_percent_delta < 0 ? "降" : "升";
            auditBannerEntries.value.push(
              `重塑度 ${before}% → ${after}%(${dir === "降" ? "AI 更保守" : "AI 更放飞"})`
            );
          }
        }
        if (typeof patch.target_chars_delta === "number") {
          // Sprint 6.A2 polish(2026-05-22):暂存"建议值",不立即 push banner —
          // cf.preview 还没拉到,suggested 可能被字数区间 clamp 拉回中心(L394 watch)
          // 等 watch 拉到 preview + 做完 clamp,才知道最终值,banner 此时统一生成
          const before = targetChars.value;
          const suggested = Math.max(1000, Math.min(
            30000,
            before + patch.target_chars_delta,
          ));
          targetChars.value = suggested;
          pendingTargetCharsAdjust.value = { before, suggested };
        }
        if (patch.custom_style_hint && patch.custom_style_hint.length >= 10) {
          // 满足 ≥10 字才切到 custom 模式(否则会校验失败);否则丢弃,留 auto
          style.value = "custom";
          customStyleHint.value = patch.custom_style_hint;
          auditBannerEntries.value.push(
            `笔法切到自定义:"${patch.custom_style_hint}"`
          );
        }
        if (patch.divergence_prefix) {
          // prepend 到 divergence(若用户已填 prefillDivergence,prefix 加在前面)
          const existing = divergence.value.trim();
          divergence.value = existing
            ? `${patch.divergence_prefix} — ${existing}`
            : patch.divergence_prefix;
          auditBannerEntries.value.push(
            `续写锚点已加引导前缀:"${patch.divergence_prefix}"`
          );
        }
        // P-1 修复(2026-05-23):记录 patch 应用后的 reshape baseline
        _reshapePostPatch = reshapePercent.value;
      }
      void quota.refresh();
      void loadProjectDoneSims();
      // Sprint 2.C:打开 dock 时拉一次反事实 + 立即预览三维(用初始 reshape%)
      void cf.reload().then(() => {
        // 2.C+ 中间态 / 周期态:默认勾全部 active(用户可在摘要区取消)
        if (props.projectMode === "middle" || props.projectMode === "cycle") {
          cf.initSelectionAsAllActive();
        }
      });
      void cf.previewAt(reshapePercent.value, true);
      session.openConfiguring();
      // P2.B 升级(2026-05-24):打开 dock 时同步触发 AI 节奏推断(已缓存秒返,否则 LLM 2-3s)
      // 结果到来后若用户未手动改过 narrativePacing,自动覆盖为推断值
      pacingUserChanged.value = false;
      withGrandFinale.value = false;
      narrativePacing.value = "standard";  // 等推断结果到来再覆盖
      inferredPacing.value = null;
      inferredPacingReasoning.value = null;
      pacingInferring.value = true;
      void api
        .post<{
          inferred_pacing: "slow" | "standard" | "fast" | null;
          inferred_pacing_reasoning: string | null;
          inferred_pacing_metrics: Record<string, unknown> | null;
          inferred_pacing_at: string | null;
          cached: boolean;
        }>(`/projects/${props.projectId}/ensure-pacing`, {})
        .then((res) => {
          if (res.inferred_pacing) {
            inferredPacing.value = res.inferred_pacing;
            inferredPacingReasoning.value = res.inferred_pacing_reasoning;
            // 仅当用户未手动改过时,才用推断值覆盖
            if (!pacingUserChanged.value) {
              narrativePacing.value = res.inferred_pacing;
            }
          }
        })
        .catch(() => {
          // 失败兜底:保持 standard,不弹错误(后端本身也 fallback 不会 5xx)
        })
        .finally(() => {
          pacingInferring.value = false;
        });
    } else {
      session.reset();
    }
  },
);

// Sprint 2.C:reshape 滑块拖动 → debounce 300ms 预览三维
watch(reshapePercent, (newPercent) => {
  if (props.open) {
    void cf.previewAt(newPercent);
  }
});

// 配额超限 watch:抛给父弹 UpgradeModal,自己关 modal
watch(
  () => session.errorCode.value,
  (code) => {
    if (
      (code === "QUOTA_EXCEEDED" || code === "INSUFFICIENT_CREDITS") &&
      session.errorDetail.value
    ) {
      // Sprint C.1:两种 429 都触发升级 modal
      // - QUOTA_EXCEEDED:资源容量类(reshape / project / character)
      // - INSUFFICIENT_CREDITS:AI credit 不足
      emit(
        "quota-exceeded",
        session.errorDetail.value as
          | QuotaExceededDetail
          | InsufficientCreditsDetail,
      );
      emit("close");
    } else if (code === "RESHAPE_CHARACTER_LIMIT_EXCEEDED") {
      // Sprint 2.C 反事实第 1 维:已改角色数 > 当前 reshape % 上限
      // 留 dock 开着让用户调高 reshape 或撤销反事实(CounterfactualWorkbench),不强关
      const detail = session.errorDetail.value as ReshapeCharacterLimitDetail | null;
      const msg = detail
        ? `已改 ${detail.current} 个角色,超过重塑度 ${detail.reshape_percent}% 的上限(${detail.limit} 个)`
        : "角色数超过重塑度上限";
      toast.warning(msg + ",请调高重塑度或撤销部分改动", 6000);
      // 刷新预览让 ReshapeSlider 警告条出来
      void cf.previewAt(reshapePercent.value, true);
    } else if (code === "END_MODE_NO_UPLOAD") {
      // Sprint 3.A 末尾态:项目还没有 ready upload(未上传 / 未抽图谱完)
      // 提示用户先走"上传 → AI 抽图谱"流程,关闭 dock 让用户专心去 ProjectView 操作
      toast.warning(
        "请先上传作品并完成 AI 抽图谱后再续写",
        7000,
      );
      emit("close");
    }
  },
);

// ============================================================
// 提交 / 关闭
// ============================================================

// 2026-06-02 hotfix R1:提交点击瞬间禁用,防重复触发(网络慢时用户反复点会创建多个 sim)
const isSubmitting = ref(false);

async function handleSubmit() {
  if (!canSubmit.value || isSubmitting.value) return;
  isSubmitting.value = true;
  try {
    await _handleSubmitInner();
  } finally {
    // 留一拍延迟避免 phase=creating 切换的瞬间用户能点
    setTimeout(() => { isSubmitting.value = false; }, 800);
  }
}

async function _handleSubmitInner() {
  // Sprint 3.A 末尾态:UI 无 divergence 输入,前端补一段固定文案让后端 schema
  // 通过(min_length=10)。LLM 编排时这段作为"用户意图",但措辞中性,
  // 实际由 director / composer prompt 里的"原作末段"section 主导。
  const submittedDivergence = isEndMode.value
    ? END_MODE_DEFAULT_DIVERGENCE
    : divergence.value.trim();
  const params: CreateSimulationRequest = {
    divergence: submittedDivergence,
    reshape_percent: reshapePercent.value,
    target_chars: targetChars.value,
    style: style.value,
    // Sprint 6.A2 M3.C(2026-05-18):续写模式
    //   'quick' 默认 — 单 LLM 编排,30-60s,~78c
    //   'evolution' 灵魂续写 — 多 agent 独立 + 私有记忆 + RAG,5-15min,400-800c
    mode: simulationMode.value,
    // Sprint 6.A2 M6(2026-05-20):outline-first 长篇生成
    //   仅 evolution 模式有意义;quick 模式总是 false
    use_outline_first:
      simulationMode.value === "evolution" && useOutlineFirst.value,
    // P2.A(2026-05-24):走向终章开关
    with_grand_finale: withGrandFinale.value,
    // P2.B(2026-05-24):叙事节奏档位
    narrative_pacing: narrativePacing.value,
    // P0H.2(2026-05-24):每章字数
    chapter_size_chars: chapterSizeChars.value,
  };
  if (style.value === "custom") {
    params.custom_style_hint = customStyleHint.value.trim();
  }
  // 滚雪球(Sprint 1.O):勾了开关且选了至少 1 条前文
  if (snowballOn.value && selectedContextIds.value.length > 0) {
    params.context_simulation_ids = selectedContextIds.value;
    // 阶段 3B(2026-06-02):用户选的伏笔继承列表
    //   仅当有可继承伏笔且面板渲染时,才传 ids(否则保持 undefined → 后端默认全继承)
    //   如果伏笔列表加载成功且 ≥ 1 条 → 即使用户全没勾(空 Set),也要传 [] 让后端知道是"主动空选"
    if (projectForeshadows.value.length > 0) {
      params.inherited_foreshadow_ids = Array.from(selectedForeshadowIds.value);
    }
  }
  // M7.J(2026-05-20):中间态起点锚点(可选)— 仅 middle 接受 + 非空才塞
  if (showAnchorField.value && anchorEventId.value) {
    params.anchor_event_id = anchorEventId.value;
  }
  // 2.C+ 中间态 / 周期态:把用户勾的反事实子集传给后端
  if (
    (props.projectMode === "middle" || props.projectMode === "cycle")
    && cf.totalActive.value > 0
  ) {
    params.selected_counterfactual_ids = cf.getSelectedIdsForRequest();
  }
  await session.start(params);
  // 创建成功后 quota 数字变了,刷一下让后续渲染 / sidebar 同步
  void quota.refresh();

  // Sprint 6.A2 #2.5(2026-05-22):成功创建 sim → 通知父 mark audit patches applied
  // 失败(quota exceeded / LLM error 等)时 session.phase 是 error,不发 → patches 不被消费
  if (session.phase.value !== "error" && session.simulation.value) {
    emit("created");
  }

  // Sprint 6.A2 M6(2026-05-20):outline-first 模式下,创建成功后跳转到 outline 审核页
  // session.simulation.value 在 start() 成功后会被设置;若 outline-first → 跳路由
  // (若 session.phase.value === 'error' 则不跳,留在 dock 显示错误)
  //
  // hotfix(2026-06-01):非 outline 的灵魂续写也直接跳详情页 — 之前留在 dock 显示
  // 快速模式的"AI 推演中…"模态,用户得手动关掉再从作品列表点进去,UX 错位
  if (
    session.phase.value !== "error"
    && session.simulation.value
  ) {
    if (params.use_outline_first) {
      const newSimId = session.simulation.value.id;
      emit("close");
      void router.push(`/simulations/${newSimId}/outline`);
    } else if (params.mode === "evolution") {
      // 非 outline 的灵魂续写 → 直接跳 SimulationDetailView(图二那种实时进度页)
      const newSimId = session.simulation.value.id;
      emit("close");
      void router.push(`/simulations/${newSimId}`);
    }
    // quick 模式 → 维持原行为,留在 dock 让用户看进度模态
  }
}

/** 拉本项目所有 done 的 simulation 给"接续之前"列表。失败静默(无前文也不影响主推演)*/
async function loadProjectDoneSims() {
  if (!props.projectId) return;
  loadingPriorList.value = true;
  try {
    const all = await api.get<SimulationSummary[]>(
      `/projects/${props.projectId}/simulations`,
    );
    projectDoneSims.value = all.filter((s) => s.state === "done");

    // 2026-06-01 v2:祖先链自动展开 — "基于本篇续写" prefillContextIds 可能只带链尾,
    // 这里要展开成完整祖先链(原作根 → ... → 链尾),让用户在 UI 看到全部已选
    if (selectedContextIds.value.length > 0) {
      const tailId = selectedContextIds.value[selectedContextIds.value.length - 1];
      const expanded = getSimAncestorChain(tailId);
      if (expanded.length > 0 && expanded.length !== selectedContextIds.value.length) {
        selectedContextIds.value = expanded.slice(-10); // 最多 10 条
      }
    }
  } catch {
    projectDoneSims.value = [];
  } finally {
    loadingPriorList.value = false;
  }
}

function toggleContextSim(simId: string) {
  // 2026-06-01 v2:祖先链锁定 — 用户实际选的是"链尾",祖先自动展开
  // 治"用户基于 sim2 续写但读不到 sim1 内容"+ "手动多选时顺序错乱"
  // 同代分叉禁用 → UI 层 :disabled 已挡;此处兜底防御
  if (isDisabledBySameDepthConflict(simId)) return;

  // 已选 sim 的处理
  const existing = selectedContextIds.value.indexOf(simId);
  if (existing >= 0) {
    // 是当前链尾 → 整条链清空
    const isTail = existing === selectedContextIds.value.length - 1;
    if (isTail) {
      selectedContextIds.value = [];
      return;
    }
    // 是中间祖先 → 锁定的不允许取消(UI 上 disabled 也会挡)
    // 安全降级:截断到该 sim 为链尾
    selectedContextIds.value = selectedContextIds.value.slice(0, existing + 1);
    return;
  }

  // 未选 sim → 计算其完整祖先链 + 自己,整条赋值
  const chain = getSimAncestorChain(simId);
  if (chain.length === 0) {
    // 找不到 sim 对应的祖先链 — 降级原行为(直接 push)
    if (selectedContextIds.value.length >= 10) return;
    selectedContextIds.value.push(simId);
    return;
  }
  if (chain.length > 10) {
    // 链过长 → 截掉远祖,保留最近 10 代(含链尾)
    selectedContextIds.value = chain.slice(-10);
  } else {
    selectedContextIds.value = chain;
  }
}

function fmtPriorTime(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${m}-${day} ${hh}:${mm}`;
}

function handleClose() {
  emit("close");
}

function handleBackdrop(e: MouseEvent) {
  if (e.target !== e.currentTarget) return;
  emit("close");
}

function handleEsc() {
  emit("close");
}

function handleRetry() {
  session.openConfiguring();
}

// ============================================================
// 事件流(Sprint 1.L)— formatter 共享自 useSimulation
// 倒序(最新在上),最多 30 条
// ============================================================

const formattedEvents = computed<FormattedEvent[]>(() => {
  const out: FormattedEvent[] = [];
  const arr = session.events.value;
  for (let i = arr.length - 1; i >= 0 && out.length < 30; i--) {
    const f = formatSimulationEvent(arr[i], i);
    if (f) out.push(f);
  }
  return out;
});
</script>

<template>
  <Teleport to="body">
    <transition name="modal-fade">
      <div
        v-if="open"
        class="modal-backdrop"
        role="dialog"
        aria-modal="true"
        aria-label="AI 续写"
        @click="handleBackdrop"
        @keydown.esc="handleEsc"
      >
        <div
          class="modal-card surface"
          :class="{ 'modal-card-wide': session.phase.value === 'done' }"
        >
          <button
            class="close-btn"
            type="button"
            aria-label="关闭"
            @click="handleClose"
          >×</button>

          <!-- ============ configuring / creating ============ -->
          <template
            v-if="
              session.phase.value === 'configuring' ||
              session.phase.value === 'creating'
            "
          >
            <header class="modal-header">
              <h2 class="modal-title">{{ dockHeaderConfig.icon }} {{ dockHeaderConfig.title }}</h2>
              <p class="modal-subtitle">{{ dockHeaderConfig.subtitle }}</p>
            </header>

            <!-- M7.K-fix(2026-05-20)audit 预填 banner — 让用户感知到字段是来自自洽守护者建议
                 关闭按钮只关闭提示,字段已应用的 patch 不回滚(用户可继续手动调整) -->
            <section v-if="showAuditBanner" class="audit-banner">
              <header class="audit-banner-head">
                <span class="audit-banner-title">
                  📋 已应用自洽守护者建议<span
                    v-if="props.auditSourceLabel"
                    class="audit-banner-issue"
                  >({{ props.auditSourceLabel }})</span>
                </span>
                <button
                  type="button"
                  class="audit-banner-close"
                  aria-label="关闭提示"
                  title="关闭提示(预填字段保留)"
                  @click="auditBannerDismissed = true"
                >×</button>
              </header>
              <ul class="audit-banner-list">
                <li v-for="(entry, i) in auditBannerEntries" :key="i">{{ entry }}</li>
              </ul>
              <p class="audit-banner-foot">
                ↑ 上述字段已自动预填,可在下方继续手动调整再提交
              </p>
            </section>

            <!-- 2.C+ 中间态 / 周期态:反事实摘要区 + 选集 checkbox + 编辑入口
                 M7.I(2026-05-20):加 "已选 N/总数" badge + 提示文案,让用户知道勾选可改 -->
            <section v-if="showCounterfactualSummary" class="cf-summary-section">
              <header class="cf-summary-header">
                <span class="cf-summary-title">
                  <span class="cf-summary-icon">⟲</span>
                  本次推演用的反事实
                  <span v-if="cf.items.value.length > 0" class="cf-count-badge">
                    已选 {{ cfSelectedCount }} / {{ cf.items.value.length }}
                  </span>
                </span>
                <button
                  v-if="props.onOpenWorkbench"
                  type="button"
                  class="cf-edit-btn"
                  @click="props.onOpenWorkbench()"
                >✎ 编辑反事实</button>
              </header>
              <p v-if="cf.items.value.length > 0" class="cf-summary-hint">
                💡 取消勾选下方某条 → 本次推演不注入该反事实
                (回滚到原作设定;不影响反事实工作台本身)
              </p>
              <p v-else class="cf-summary-empty">
                还没有反事实变量。点「✎ 编辑反事实」打开工作台,改角色性格 / 事件结果 /
                世界观维度 — 然后再回这里勾选注入本次推演。
              </p>
              <ul v-if="cf.items.value.length > 0" class="cf-summary-list">
                <li
                  v-for="item in cf.items.value"
                  :key="item.id"
                  class="cf-summary-item"
                  :class="{ 'is-deselected': !cf.isSelected(item.id) }"
                >
                  <label class="cf-checkbox-label">
                    <input
                      type="checkbox"
                      :checked="cf.isSelected(item.id)"
                      @change="cf.toggleSelected(item.id)"
                    />
                    <span class="cf-item-text">
                      <span
                        class="cf-item-type"
                        :class="`type-${item.target_type}`"
                      >{{ {
                        character: '角色',
                        event: '事件',
                        relationship: '关系',
                        world: '世界观',
                      }[item.target_type] }}</span>
                      <span class="cf-item-field">「{{ item.field }}」</span>
                      <span v-if="item.user_intent" class="cf-item-intent">
                        ★ {{ item.user_intent }}
                      </span>
                      <span v-else class="cf-item-diff mono">
                        {{ (item.new_value ?? '').slice(0, 40) }}
                      </span>
                    </span>
                  </label>
                </li>
              </ul>
              <!-- M7.J(2026-05-20)中间态起点锚点 — 与反事实区视觉同源 -->
              <div v-if="showAnchorField" class="anchor-field">
                <label class="anchor-label" for="sim-anchor-event">
                  📍 起点锚点(可选 — 让 AI 以原作某事件刚结束为时间起点续推)
                </label>
                <select
                  id="sim-anchor-event"
                  v-model="anchorEventId"
                  class="anchor-select"
                  :disabled="session.phase.value === 'creating'"
                >
                  <option value="">不设锚点 — 独立新场景(默认)</option>
                  <option
                    v-for="ev in (props.projectEvents ?? [])"
                    :key="ev.id"
                    :value="ev.id"
                  >{{ ev.description.slice(0, 60) }}{{ ev.description.length > 60 ? '…' : '' }}</option>
                </select>
                <p v-if="anchorEventPreview" class="anchor-preview">
                  <strong>第 1 幕将承接此事件刚结束的场景与情绪</strong>:
                  {{ anchorEventPreview.slice(0, 120) }}{{ anchorEventPreview.length > 120 ? '…' : '' }}
                </p>
              </div>
            </section>

            <form class="form" @submit.prevent="handleSubmit">
              <!-- divergence — 末尾态不显示锚点输入(用户拍板:继承原作意志,
                   不让用户写锚点污染产物方向);改显一行说明文案 -->
              <div v-if="!isEndMode" class="field">
                <label for="sim-div" class="field-label">
                  <span>{{ divergenceLabel }}</span>
                  <span class="field-meta">{{ divergence.length }} / 500 · 至少 10 字</span>
                </label>
                <textarea
                  id="sim-div"
                  v-model="divergence"
                  class="text-input textarea"
                  :placeholder="
                    snowballOn
                      ? '例:接着上面的剧情,接下来李寻欢决定独闯金钱帮总舵...'
                      : '例:李寻欢与孙小红重逢于江畔客栈,上官金虹在外暗中窥伺...'
                  "
                  maxlength="500"
                  rows="3"
                  :disabled="session.phase.value === 'creating'"
                ></textarea>
              </div>
              <div v-else class="end-mode-note">
                <span class="end-mode-note-icon">📜</span>
                <!-- Sprint 6.A2 M3.D-fix5(2026-05-19):末尾态 + 滚雪球时换文案,
                     用户实测发现两个 banner 同时显时不知接哪段;改为语义二选一:
                       未勾滚雪球 → 接原作末段(老语义)
                       勾了滚雪球 → 接前续写末段(本次不接原作末段) -->
                <p v-if="!snowballOn" class="end-mode-note-text">
                  <strong>无需锚点描述</strong> — 末尾态由 AI 继承原作末段的作者意志自然延续,
                  避免用户额外干预把续作引向偏离原作方向。
                </p>
                <p v-else class="end-mode-note-text">
                  <strong>从前作末尾继续写</strong> — 本次续写不再接原作末段,
                  而是从你选的前作末尾自然延续,避免与前作时间错乱。
                </p>
              </div>

              <!-- 滚雪球续写(Sprint 1.O)-->
              <div class="field snowball-field">
                <label class="snowball-toggle">
                  <input
                    type="checkbox"
                    v-model="snowballOn"
                    :disabled="
                      session.phase.value === 'creating' ||
                      projectDoneSims.length === 0
                    "
                  />
                  <span class="snowball-label">
                    接续之前的剧情
                    <!-- M7.I(2026-05-20):done sim ≥ 1 时显眼提示,让用户感知到"滚雪球可用" -->
                    <span
                      v-if="projectDoneSims.length > 0 && !snowballOn"
                      class="snowball-available-chip"
                    >📚 本项目已有 {{ projectDoneSims.length }} 篇可续写</span>
                    <span class="snowball-hint">
                      让 AI 在你之前的产物基础上继续写
                    </span>
                  </span>
                </label>

                <p
                  v-if="!loadingPriorList && projectDoneSims.length === 0"
                  class="snowball-empty"
                >
                  本项目还没有已完成的推演 — 先独立推演一次,之后再回来接续。
                </p>

                <div v-if="snowballOn && eligiblePriorSims.length > 0" class="snowball-list">
                  <p class="snowball-list-title">
                    选择前文(本项目历史,最多 10 条)
                    <span class="snowball-counter">
                      已选 {{ selectedContextIds.length }} / 10
                      <span v-if="selectedContextIds.length > 0" class="snowball-est mono">
                        · 估算 ~{{ totalPriorChars.toLocaleString() }} 字
                      </span>
                    </span>
                  </p>
                  <!-- 2026-06-01 v2:祖先链自动锁定 banner — 治"用户基于本篇续写读不到前作"问题 -->
                  <p v-if="selectedContextIds.length > 1" class="snowball-chain-banner">
                    <span class="chain-banner-icon">🔗</span>
                    已自动包含 <strong>{{ selectedContextIds.length }} 篇</strong>
                    前作 — 系统按时序读全链(近 2 代用全文 · 更远用摘要),你只选链尾,祖先自动锁定
                  </p>
                  <!-- 2026-06-02:有姐妹分支(被禁用)时,解释为什么不读它们 -->
                  <p v-if="siblingBranchCount > 0" class="snowball-sibling-banner">
                    <span class="chain-banner-icon">⌥</span>
                    <strong>{{ siblingBranchCount }} 条姐妹分支</strong>(灰色)是已选链尾的「平行宇宙」—
                    它们跟链尾基于同一前作但讲了不同走向,**同读会让 AI 精分**所以互斥;<br />
                    祖先链中间所有代都被系统读取,不会丢信息
                  </p>
                  <ul class="snowball-items">
                    <li
                      v-for="sim in eligiblePriorSims"
                      :key="sim.id"
                      class="snowball-item"
                      :class="{
                        'is-selected': selectedContextIds.includes(sim.id),
                        'is-locked-ancestor': isLockedAncestor(sim.id),
                        'is-disabled-conflict': isDisabledBySameDepthConflict(sim.id),
                      }"
                    >
                      <label class="snowball-item-label">
                        <input
                          type="checkbox"
                          :checked="selectedContextIds.includes(sim.id)"
                          :disabled="
                            isDisabledBySameDepthConflict(sim.id) ||
                            isLockedAncestor(sim.id) ||
                            (!selectedContextIds.includes(sim.id) &&
                              selectedContextIds.length >= 10)
                          "
                          :title="
                            isLockedAncestor(sim.id)
                              ? '该前作是必读祖先,已被系统自动锁定,不可单独取消(取消链尾即可释放)'
                              : isDisabledBySameDepthConflict(sim.id)
                              ? '这是已选链尾的【姐妹分支】(平行宇宙剧情)— 它跟已选链尾基于同一前作,讲的是不同走向\n同代只能选一条线,否则 LLM 会精分\n\n注:链中间所有代实际都被系统读取(近 2 代用全文,更远代用摘要),不会丢信息'
                              : ''
                          "
                          @change="toggleContextSim(sim.id)"
                        />
                        <span class="snowball-item-gen mono">
                          {{ getGenerationLabel(sim) }}
                        </span>
                        <span class="snowball-item-time mono">
                          {{ fmtPriorTime(sim.created_at) }}
                        </span>
                        <span class="snowball-item-meta mono">
                          {{ sim.rounds_planned }} 轮
                        </span>
                        <span class="snowball-item-div">{{ sim.divergence }}</span>
                        <!-- 必读祖先标记 -->
                        <span
                          v-if="isLockedAncestor(sim.id)"
                          class="snowball-locked-tag"
                          aria-label="必读祖先"
                        >🔒 必读</span>
                        <!-- 2026-06-02:姐妹分支标记(平行宇宙,被互斥禁用)-->
                        <span
                          v-else-if="isDisabledBySameDepthConflict(sim.id)"
                          class="snowball-sibling-tag"
                          title="平行宇宙姐妹分支"
                        >⌥ 姐妹</span>
                      </label>
                    </li>
                  </ul>
                </div>
              </div>

              <!-- 阶段 3B(2026-06-02):伏笔继承面板
                   勾"接续之前剧情"+ 有 context 时显示;勾选要让 AI 在本次续作中追的伏笔.
                   默认 P1 主线全勾,P2/P3 不勾;用户可自由调整 -->
              <div v-if="showForeshadowPanel" class="foreshadow-inherit-section">
                <div class="foreshadow-inherit-header">
                  <strong class="foreshadow-inherit-title">
                    📚 选择本次续作要追的伏笔
                  </strong>
                  <span class="foreshadow-inherit-meta">
                    已选 {{ selectedForeshadowIds.size }} / {{ projectForeshadows.length }}
                  </span>
                </div>
                <p class="foreshadow-inherit-hint">
                  来自前作的未解伏笔.勾选的伏笔会进入 outline + 每幕 LLM 的"必须主动推进"清单;
                  没勾的本次续作不读 — 让 AI 别在它身上分注意力.
                  <strong>P1 主线默认勾</strong>(产品建议).
                </p>
                <div v-if="loadingForeshadows" class="foreshadow-inherit-loading">
                  加载伏笔中…
                </div>
                <div
                  v-else-if="projectForeshadows.length === 0"
                  class="foreshadow-inherit-empty"
                >
                  无可继承伏笔(本作品前作还没产生伏笔账本).
                </div>
                <ul v-else class="foreshadow-inherit-list">
                  <li
                    v-for="f in projectForeshadows"
                    :key="f.id"
                    class="foreshadow-item"
                    :class="{ 'is-checked': selectedForeshadowIds.has(f.id) }"
                  >
                    <label class="foreshadow-label">
                      <input
                        type="checkbox"
                        :checked="selectedForeshadowIds.has(f.id)"
                        @change="toggleForeshadow(f.id)"
                      />
                      <span
                        class="foreshadow-prio mono"
                        :class="`prio-${f.priority}`"
                      >{{
                        f.priority === "high" ? "P1 主线" :
                        f.priority === "medium" ? "P2 支线" : "P3 背景"
                      }}</span>
                      <span class="foreshadow-content">{{ f.content }}</span>
                    </label>
                  </li>
                </ul>
              </div>

              <!-- Sprint 2.C 重塑度 — 独立浅色 ReshapeSlider 组件 + 三维度可视化 -->
              <ReshapeSlider
                v-model="reshapePercent"
                :plan-max-percent="reshapeMax"
                :preview="cf.preview.value"
                :is-over-character-limit="cf.isOverCharacterLimit.value"
                :disabled="session.phase.value === 'creating'"
              />

              <!-- 反事实总数 chip(让用户在推演前心里有数)
                   2026-05-12 修复:加 showCounterfactualSummary 与上方摘要区共用 mode 守门 —
                   初始态用户就是作者,改自己创建的世界不叫"反事实",这条 chip 在初始/末尾态
                   语义错位。提交逻辑(L377-382)本就只在 middle/cycle 注入,UI 现在跟齐。 -->
              <div v-if="showCounterfactualSummary" class="cf-chip-row">
                <span class="cf-chip">
                  <span class="cf-chip-icon">⟲</span>
                  共 {{ cf.totalActive.value }} 个反事实变量将注入本次推演
                </span>
                <span class="cf-chip-sub mono">
                  (角色 {{ cf.byType.value.character }} / 事件 {{ cf.byType.value.event }} / 关系 {{ cf.byType.value.relationship }})
                </span>
              </div>

              <!-- length -->
              <div class="field">
                <label class="field-label">
                  <span>叙事长度</span>
                  <span class="field-value">{{ targetChars.toLocaleString() }} 字</span>
                </label>
                <!-- Sprint 6.A2 M3.D-fix2 v2(2026-05-18):滑块 min/max 来自 reshape_preview
                     推断的字数区间,防止"50% 重塑度 + 4000 字"这种不匹配组合 -->
                <input
                  type="range"
                  v-model.number="targetChars"
                  :min="charsRangeMin"
                  :max="charsRangeMax"
                  :step="100"
                  class="slider"
                  :disabled="session.phase.value === 'creating'"
                />
                <!-- Sprint 6.A2 M3.D-fix4(2026-05-19):严谨展示推荐区间 —
                     min ── 推荐中心 ── max 的三段式底栏,让用户直观看到边界 -->
                <div v-if="cf.preview.value" class="slider-range-row mono">
                  <span class="range-edge">{{ charsRangeMin.toLocaleString() }}</span>
                  <span class="range-center">
                    推荐 {{ cf.preview.value.chars_center.toLocaleString() }}
                  </span>
                  <span class="range-edge">{{ charsRangeMax.toLocaleString() }}</span>
                </div>
                <p class="field-meta">
                  {{ charsLabel }}
                  <span v-if="cf.preview.value" class="chars-recommend-chip">
                    · {{ cf.preview.value.chars_label }}
                  </span>
                  <span class="chars-disclaimer">
                    · 最终字数会在推荐区间内浮动(±10%)
                  </span>
                </p>
              </div>

              <!-- style -->
              <div class="field">
                <label class="field-label"><span>笔法风格</span></label>
                <div class="style-row">
                  <label
                    v-for="opt in STYLE_OPTIONS"
                    :key="opt.value"
                    class="style-chip"
                    :class="{ 'is-active': style === opt.value }"
                  >
                    <input
                      type="radio"
                      :value="opt.value"
                      v-model="style"
                      class="sr-only"
                      :disabled="session.phase.value === 'creating'"
                    />
                    <span class="style-label">{{ opt.label }}</span>
                    <span class="style-hint">{{ opt.hint }}</span>
                  </label>
                </div>

                <!-- custom 时展开 textarea(transition 平滑) -->
                <div v-if="style === 'custom'" class="custom-style-pane">
                  <label class="field-meta inline-label">
                    描述你想要的语体
                    <span>{{ customStyleHint.length }} / 500 · 至少 10 字</span>
                  </label>
                  <textarea
                    v-model="customStyleHint"
                    class="text-input textarea"
                    placeholder="例:民国武侠白话 / 仿张爱玲世故笔调"
                    maxlength="500"
                    rows="3"
                    :disabled="session.phase.value === 'creating'"
                  ></textarea>
                </div>
              </div>

              <!-- Sprint 6.A2 M3.C(2026-05-18):续写模式选择(快速 vs 灵魂) -->
              <div class="field">
                <label class="field-label"><span>续写模式</span></label>
                <div class="mode-row">
                  <label
                    class="mode-card"
                    :class="{ 'is-active': simulationMode === 'quick' }"
                  >
                    <input
                      type="radio"
                      value="quick"
                      v-model="simulationMode"
                      class="sr-only"
                      :disabled="session.phase.value === 'creating'"
                    />
                    <div class="mode-card-head">
                      <span class="mode-card-icon" aria-hidden="true">⚡</span>
                      <span class="mode-card-title">快速</span>
                    </div>
                    <p class="mode-card-meta">~78 c · 30-60s</p>
                    <p class="mode-card-desc">
                      单 LLM 编排,角色对白基于摘要档案。
                    </p>
                  </label>

                  <label
                    class="mode-card mode-card--evolution"
                    :class="{ 'is-active': simulationMode === 'evolution' }"
                  >
                    <input
                      type="radio"
                      value="evolution"
                      v-model="simulationMode"
                      class="sr-only"
                      :disabled="session.phase.value === 'creating'"
                    />
                    <div class="mode-card-head">
                      <span class="mode-card-icon" aria-hidden="true">🎭</span>
                      <span class="mode-card-title">灵魂续写</span>
                      <span class="mode-card-beta">差异化</span>
                    </div>
                    <p class="mode-card-meta">~400-800 c · 5-15 min</p>
                    <p class="mode-card-desc">
                      多 agent 独立 + 私有记忆 + 读原著相关片段 + 多轮自由对话 + 叙述者合稿。
                      <strong>角色真活</strong>,不是 AI 凭摘要写。
                    </p>
                  </label>
                </div>

                <!-- Sprint 6.A2 M6(2026-05-20):outline-first 长篇生成开关
                     仅 evolution 模式显示;默认开(治本路径) -->
                <label
                  v-if="simulationMode === 'evolution'"
                  class="outline-toggle"
                  :class="{ 'is-active': useOutlineFirst }"
                >
                  <input
                    type="checkbox"
                    v-model="useOutlineFirst"
                    :disabled="session.phase.value === 'creating'"
                  />
                  <div class="outline-toggle-content">
                    <span class="outline-toggle-title">
                      📐 长篇 outline-first(M6 治本)
                    </span>
                    <span class="outline-toggle-desc">
                      创建后先 LLM 生成全篇 outline → <strong>你审核 / 编辑</strong> →
                      批准后按图纸严格生成,治"剧情死循环 / 实体漂移 / 时空撕裂"瑕疵。
                      推荐长篇(≥ 5000 字)开启。
                    </span>
                  </div>
                </label>

                <!-- M6-fix2 智能提示:基于 target_chars 推荐是否开 outline-first -->
                <p
                  v-if="outlineFirstHint"
                  class="outline-hint"
                  :class="`outline-hint--${outlineFirstHint.tone}`"
                >
                  <span class="outline-hint-icon">{{ outlineFirstHint.icon }}</span>
                  <span class="outline-hint-text">{{ outlineFirstHint.text }}</span>
                </p>

                <!-- P2.B(2026-05-24)+ 升级:叙事节奏档位(AI 推断默认 / 用户可改)-->
                <div class="creation-pref-row creation-pref-pacing-block">
                  <div class="creation-pref-pacing-head">
                    <span class="creation-pref-label">叙事节奏</span>
                    <span
                      v-if="pacingInferring"
                      class="pacing-status pacing-status--inferring"
                    >
                      AI 分析原作中…
                    </span>
                    <span
                      v-else-if="inferredPacing && !pacingUserChanged"
                      class="pacing-status pacing-status--ai"
                      :title="inferredPacingReasoning ?? ''"
                    >
                      AI 推断
                    </span>
                    <span
                      v-else-if="inferredPacing && pacingUserChanged"
                      class="pacing-status pacing-status--user"
                    >
                      你已手动改
                    </span>
                  </div>
                  <select
                    v-model="narrativePacing"
                    class="creation-pref-select"
                    :disabled="session.phase.value === 'creating' || pacingInferring"
                    @change="pacingUserChanged = true"
                  >
                    <option value="slow">慢 — 多幕同场景,氛围铺陈(村上/川端类)</option>
                    <option value="standard">标准 — 平衡节奏</option>
                    <option value="fast">紧凑 — 主动制造波折,频换场景(三体类)</option>
                  </select>
                  <p
                    v-if="inferredPacingReasoning && !pacingInferring"
                    class="pacing-reasoning"
                  >
                    {{ inferredPacingReasoning }}
                  </p>
                </div>

                <!-- P2.A(2026-05-24):走向终章开关 -->
                <label class="creation-pref-row creation-pref-checkbox">
                  <input
                    type="checkbox"
                    v-model="withGrandFinale"
                    :disabled="session.phase.value === 'creating'"
                  />
                  <div>
                    <span class="creation-pref-label">走向终章</span>
                    <span class="creation-pref-hint">
                      勾选后本部续作将安排剧情收束(主线收尾 / 关系闭环);默认不勾,便于超长篇续作不被提前"完结"
                    </span>
                  </div>
                </label>

                <!-- 2026-06-02 hotfix:删除 "每章字数" 控件 — 项目级"章节字数区间"卡片已覆盖
                     用户在项目页设置 chapter_size_min/max 区间,所有 sim 复用该区间 -->
              </div>

              <!-- 配额 / 角色提醒 banner -->
              <p v-if="characterCount < 3" class="banner banner-warn">
                需要至少 3 个角色才能续写,当前 {{ characterCount }} 个。
              </p>
              <!-- Sprint C.3:credit 透明计量 — 显预计消耗 + 余额对比 + 不足双 CTA -->
              <CreditEstimate
                v-else
                :estimated="creditEstimate.units"
                :basis="creditEstimate.basis"
                action="simulation"
              />

              <p
                v-if="
                  session.errorMessage.value &&
                  session.errorCode.value !== 'QUOTA_EXCEEDED' &&
                  session.errorCode.value !== 'INSUFFICIENT_CREDITS'
                "
                class="banner banner-error"
              >
                {{ session.errorMessage.value }}
              </p>

              <!-- 诊断:不能提交时给具体原因(banner 之外的细分场景:divergence 太短 / 重塑度越界 / 自定义 hint 太短 / 滚雪球未选前文) -->
              <p
                v-if="submitBlockedReason && !session.errorMessage.value"
                class="block-reason"
              >
                ⓘ {{ submitBlockedReason }}
              </p>

              <div class="actions">
                <button
                  type="button"
                  class="ghost-btn"
                  :disabled="session.phase.value === 'creating'"
                  @click="handleClose"
                >取消</button>
                <button
                  type="submit"
                  class="primary-btn"
                  :disabled="!canSubmit || isSubmitting"
                  :title="submitBlockedReason ?? ''"
                >
                  {{ (session.phase.value === 'creating' || isSubmitting) ? '创建中…' : '✦ 开始续写 →' }}
                </button>
              </div>
            </form>
          </template>

          <!-- ============ running ============ -->
          <template v-else-if="session.phase.value === 'running'">
            <header class="modal-header">
              <!-- Sprint 3.A 修:进行中标题跟 mode 走(末尾态显「续写中…」、中间态「重塑中…」),
                   避免末尾态用户看到"推演中"语义错位(纪律 2 mode-aware 文案) -->
              <h2 class="modal-title">{{ dockHeaderConfig.title }}中…</h2>
              <p class="modal-subtitle">{{ session.stageLabel.value }}</p>
            </header>

            <div class="running-body">
              <div class="progress-wrap">
                <div class="progress-track">
                  <div
                    class="progress-fill"
                    :style="{ width: `${session.progress.value * 100}%` }"
                  ></div>
                </div>
                <p class="progress-text">
                  {{ Math.round(session.progress.value * 100) }}% ·
                  第 {{ session.simulation.value?.current_round ?? 0 }} /
                  {{ session.simulation.value?.rounds_planned ?? 0 }}
                  {{ session.simulation.value?.mode === 'evolution' ? '幕' : '轮' }}
                </p>
              </div>

              <div class="cost-row">
                <span class="cost-label">已消耗</span>
                <span class="cost-value mono">
                  ¥{{ (session.simulation.value?.cost_yuan ?? 0).toFixed(4) }}
                </span>
              </div>

              <!-- 实时事件流(Sprint 1.L)-->
              <div class="event-stream">
                <p class="event-stream-title">实时进度</p>
                <ul v-if="formattedEvents.length" class="event-list">
                  <li
                    v-for="ev in formattedEvents"
                    :key="ev.key"
                    class="event-row"
                    :class="[
                      `tone-${ev.tone}`,
                      `indent-${ev.indent}`,
                    ]"
                  >
                    <span class="event-icon mono">{{ ev.icon }}</span>
                    <span class="event-main">{{ ev.main }}</span>
                    <span v-if="ev.sub" class="event-sub mono">{{ ev.sub }}</span>
                  </li>
                </ul>
                <p v-else class="event-empty">等待 AI 启动…</p>
              </div>

              <p class="hint">
                可以关闭此窗口,推演会在后台继续。
                关闭后可在项目「作品列表」重入查看进度。
              </p>
            </div>

            <div class="actions">
              <button class="ghost-btn" @click="handleClose">关闭(后台继续)</button>
            </div>
          </template>

          <!-- ============ done — 接 NarrativeStream(Sprint 1.I) ============ -->
          <template v-else-if="session.phase.value === 'done' && session.simulation.value">
            <NarrativeStream
              :narrative="session.simulation.value.narrative ?? ''"
              :project-name="projectName"
              :reshape-percent="session.simulation.value.reshape_percent"
              :rounds-planned="session.simulation.value.rounds_planned"
              :cost-yuan="session.simulation.value.cost_yuan"
              :target-chars="session.simulation.value.target_chars"
              @close="handleClose"
            />
          </template>

          <!-- ============ failed / error ============ -->
          <template
            v-else-if="
              session.phase.value === 'failed' ||
              session.phase.value === 'error'
            "
          >
            <header class="modal-header">
              <!-- Sprint 3.A 修:失败标题跟 dock 当前 mode 走(末尾态显"续写失败",
                   不再显写死的"推演失败"),保证 4 态心智一致 -->
              <h2 class="modal-title">{{ dockHeaderConfig.title }}失败</h2>
              <p class="modal-subtitle">{{ session.errorMessage.value || '未知错误' }}</p>
            </header>

            <div class="actions">
              <button class="ghost-btn" @click="handleClose">关闭</button>
              <button class="primary-btn" @click="handleRetry">重试</button>
            </div>
          </template>
        </div>
      </div>
    </transition>
  </Teleport>
</template>

<style scoped>
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(31, 31, 30, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: var(--z-modal-backdrop);
  padding: var(--space-4);
}

.modal-card {
  width: 100%;
  max-width: 560px;
  max-height: calc(100vh - var(--space-8));
  padding: var(--space-8);
  position: relative;
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
  z-index: var(--z-modal);
  overflow-y: auto;
  transition: max-width var(--duration-base) var(--ease-out);
}

/* done 屏放宽,给 NarrativeStream 更舒适的阅读列宽 */
.modal-card.modal-card-wide {
  max-width: 720px;
}

.close-btn {
  position: absolute;
  top: var(--space-3);
  right: var(--space-3);
  width: 32px;
  height: 32px;
  font-size: var(--text-xl);
  line-height: 1;
  color: var(--color-text-muted);
  background: transparent;
  border-radius: var(--radius-full);
  transition: all var(--duration-fast) var(--ease-out);
}
.close-btn:hover:not(:disabled) {
  background: var(--color-surface-hover);
  color: var(--color-text);
}

.modal-header {
  margin-bottom: var(--space-6);
}
.modal-title {
  font-size: var(--text-xl);
  font-weight: 600;
  margin-bottom: var(--space-2);
}
.modal-subtitle {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
}

/* ===== form ===== */
.form {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}
.field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.field-label {
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text);
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}
.field-meta {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  font-weight: 400;
}
.field-value {
  font-family: var(--font-mono);
  font-size: var(--text-sm);
  color: var(--color-accent-text);
  font-weight: 500;
}
.upgrade-hint {
  color: var(--color-text-subtle);
}
.text-input {
  width: 100%;
  padding: var(--space-3) var(--space-4);
  font-size: var(--text-base);
  background: var(--color-surface);
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-md);
  outline: none;
  font-family: inherit;
  transition: border-color var(--duration-fast) var(--ease-out),
              box-shadow var(--duration-fast) var(--ease-out);
}
.textarea {
  resize: vertical;
  line-height: 1.5;
  min-height: 72px;
}
.text-input:focus {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 3px rgba(124, 58, 237, 0.12);
}

/* range slider — 浅色调对齐紫色 token */
.slider {
  width: 100%;
  height: 6px;
  appearance: none;
  -webkit-appearance: none;
  background: var(--color-bg-subtle);
  border-radius: var(--radius-full);
  outline: none;
  cursor: pointer;
}
.slider::-webkit-slider-thumb {
  appearance: none;
  -webkit-appearance: none;
  width: 18px;
  height: 18px;
  background: var(--color-accent);
  border-radius: var(--radius-full);
  cursor: pointer;
  border: 2px solid var(--color-surface);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.15);
}
.slider::-moz-range-thumb {
  width: 18px;
  height: 18px;
  background: var(--color-accent);
  border-radius: var(--radius-full);
  cursor: pointer;
  border: 2px solid var(--color-surface);
}
.slider:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* style 单选 chips */
.style-row {
  display: flex;
  gap: var(--space-2);
}

/* Sprint 6.A2 M3.D-fix2 v2(2026-05-18):字数推荐 chip(在 field-meta 行内) */
.chars-recommend-chip {
  color: var(--color-accent-text);
  font-weight: 500;
}

/* Sprint 6.A2 M3.D-fix4(2026-05-19):字数范围三段式底栏 */
.slider-range-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 4px;
  margin-bottom: var(--space-1);
  font-size: 11px;
  color: var(--color-text-subtle);
}
.slider-range-row .range-edge {
  color: var(--color-text-subtle);
  opacity: 0.7;
}
.slider-range-row .range-center {
  color: var(--color-accent-text);
  font-weight: 500;
  padding: 1px 8px;
  background: var(--color-accent-soft);
  border-radius: var(--radius-sm);
}
.chars-disclaimer {
  color: var(--color-text-subtle);
  opacity: 0.85;
}

/* Sprint 6.A2 M3.C(2026-05-18):续写模式选择卡 */
.mode-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-2);
}
.mode-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: var(--space-3);
  background: var(--color-surface);
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-md);
  cursor: pointer;
  user-select: none;
  transition: all var(--duration-fast) var(--ease-out);
}
.mode-card:hover {
  border-color: var(--color-accent-border);
}
.mode-card.is-active {
  background: var(--color-accent-soft);
  border-color: var(--color-accent);
}
.mode-card-head {
  display: flex;
  align-items: center;
  gap: 6px;
}
.mode-card-icon {
  font-size: var(--text-md);
}
.mode-card-title {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text);
}
.mode-card.is-active .mode-card-title {
  color: var(--color-accent-text);
}
.mode-card-beta {
  margin-left: auto;
  padding: 1px 6px;
  font-size: 10px;
  font-weight: 600;
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border: 1px solid var(--color-accent);
  border-radius: var(--radius-sm);
}
.mode-card.is-active .mode-card-beta {
  background: var(--color-accent);
  color: var(--color-text-on-accent);
}
.mode-card-meta {
  margin: 2px 0 0 0;
  font-size: 11px;
  color: var(--color-text-muted);
  font-family: var(--font-mono);
}
.mode-card-desc {
  margin: 4px 0 0 0;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  line-height: 1.5;
}

/* Sprint 6.A2 M6(2026-05-20):outline-first 长篇生成开关 */
.outline-toggle {
  display: flex;
  gap: var(--space-2);
  padding: var(--space-3);
  margin-top: var(--space-2);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-md);
  cursor: pointer;
  user-select: none;
  transition: all var(--duration-fast) var(--ease-out);
}
.outline-toggle:hover {
  border-color: var(--color-accent-border);
}
.outline-toggle.is-active {
  background: var(--color-accent-soft);
  border-color: var(--color-accent);
}
.outline-toggle input[type="checkbox"] {
  margin-top: 3px;
  cursor: pointer;
}
.outline-toggle-content {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
}
.outline-toggle-title {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text);
}
.outline-toggle.is-active .outline-toggle-title {
  color: var(--color-accent-text);
}
.outline-toggle-desc {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  line-height: 1.5;
}

/* M6-fix2(2026-05-20):outline-first 智能提示 */
.outline-hint {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin: 6px 0 0;
  padding: 8px 12px;
  border-radius: var(--radius-md);
  font-size: var(--text-xs);
  line-height: 1.5;
}

/* P2.A / P2.B(2026-05-24):创作偏好行(节奏档 + 走向终章)*/
.creation-pref-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-2);
  padding: 8px 12px;
  background: var(--surface-1);
  border: 1px solid var(--border-1);
  border-radius: var(--radius-md);
  font-size: var(--text-xs);
}
.creation-pref-checkbox {
  align-items: flex-start;
  cursor: default;
}
.creation-pref-checkbox input[type="checkbox"] {
  margin-top: 3px;
  flex-shrink: 0;
}
.creation-pref-label {
  font-weight: 600;
  color: var(--text-1);
  flex-shrink: 0;
}
.creation-pref-hint {
  display: block;
  margin-top: 2px;
  color: var(--text-3);
  font-size: 11px;
  line-height: 1.5;
}
.creation-pref-select {
  flex: 1;
  padding: 4px 8px;
  border: 1px solid var(--border-2);
  border-radius: var(--radius-sm);
  background: var(--surface-0);
  color: var(--text-1);
  font-size: var(--text-xs);
}
.creation-pref-input {
  width: 90px;
  padding: 4px 8px;
  border: 1px solid var(--border-2);
  border-radius: var(--radius-sm);
  background: var(--surface-0);
  color: var(--text-1);
  font-size: var(--text-xs);
}

/* P2.B 升级:节奏块容器(单独成区,展示 AI 推断 + reasoning)*/
.creation-pref-pacing-block {
  flex-direction: column;
  align-items: stretch;
  gap: 6px;
}
.creation-pref-pacing-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.pacing-status {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 999px;
  font-weight: 500;
}
.pacing-status--inferring {
  background: rgba(99, 102, 241, 0.12);
  color: rgb(79, 70, 229);
}
.pacing-status--ai {
  background: rgba(34, 197, 94, 0.12);
  color: rgb(22, 163, 74);
  cursor: help;
}
.pacing-status--user {
  background: rgba(245, 158, 11, 0.12);
  color: rgb(180, 83, 9);
}
.pacing-reasoning {
  margin: 0;
  padding: 6px 10px;
  font-size: 11px;
  line-height: 1.55;
  color: var(--text-3);
  background: rgba(99, 102, 241, 0.05);
  border-left: none;  /* 遵守章程 2.6:不竖色条 */
  border-radius: var(--radius-sm);
}
.outline-hint-icon {
  flex-shrink: 0;
  font-size: var(--text-sm);
}
.outline-hint-text {
  flex: 1;
}
.outline-hint--warn {
  background: rgba(245, 158, 11, 0.12);
  color: rgb(180, 83, 9);
  border: 1px solid rgba(245, 158, 11, 0.32);
}
.outline-hint--ok {
  background: rgba(16, 185, 129, 0.10);
  color: rgb(5, 122, 85);
  border: 1px solid rgba(16, 185, 129, 0.32);
}
.outline-hint--info {
  background: var(--color-bg-subtle);
  color: var(--color-text-muted);
  border: 1px solid var(--color-border);
}
.mode-card.is-active .mode-card-desc {
  color: var(--color-text);
}
.mode-card-desc strong {
  color: var(--color-accent-text);
  font-weight: 600;
}
.style-chip {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--space-3) var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-md);
  cursor: pointer;
  user-select: none;
  transition: all var(--duration-fast) var(--ease-out);
}
.style-chip:hover {
  border-color: var(--color-accent-border);
}
.style-chip.is-active {
  background: var(--color-accent-soft);
  border-color: var(--color-accent);
}
.style-label {
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text);
}
.style-chip.is-active .style-label {
  color: var(--color-accent-text);
}
.style-hint {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
}

/* custom 笔法展开区 */
.custom-style-pane {
  margin-top: var(--space-3);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.inline-label {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}

/* ===== 滚雪球续写(Sprint 1.O)===== */
.snowball-field {
  padding: var(--space-3) var(--space-4);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  gap: var(--space-3);
}

.snowball-toggle {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  cursor: pointer;
  user-select: none;
}
.snowball-toggle input[type="checkbox"] {
  margin-top: 3px;
  cursor: pointer;
  accent-color: var(--color-accent);
}
.snowball-toggle input[type="checkbox"]:disabled {
  cursor: not-allowed;
}
.snowball-label {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: var(--text-sm);
  color: var(--color-text);
}
/* M7.I(2026-05-20)项目已有可续写产物时的提示 chip — 让用户感知到滚雪球可用 */
.snowball-available-chip {
  display: inline-block;
  margin-top: 2px;
  padding: 1px 8px;
  font-size: 11px;
  font-weight: 500;
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-full);
  width: fit-content;
}
.snowball-hint {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
}

.snowball-empty {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  padding-left: var(--space-6);
  font-style: italic;
}

/* Sprint 3.A 末尾态 — 取代锚点输入的说明卡片 */
.end-mode-note {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  background: var(--color-accent-soft);
  border-radius: var(--radius-md);
}
.end-mode-note-icon {
  font-size: var(--text-lg);
  line-height: 1.4;
  flex-shrink: 0;
}
.end-mode-note-text {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--color-text);
  line-height: 1.6;
}
.end-mode-note-text strong {
  color: var(--color-accent-text);
  font-weight: 600;
}

.snowball-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.snowball-list-title {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  flex-wrap: wrap;
  gap: var(--space-2);
}
.snowball-counter {
  color: var(--color-accent-text);
  font-weight: 500;
}
.snowball-est {
  color: var(--color-text-subtle);
  font-weight: 400;
}

.snowball-items {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 200px;
  overflow-y: auto;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: var(--space-1);
}
.snowball-item {
  border-radius: var(--radius-sm);
  transition: background var(--duration-fast) var(--ease-out);
}
.snowball-item:hover {
  background: var(--color-surface-hover);
}
.snowball-item.is-selected {
  background: var(--color-accent-soft);
}
.snowball-item-label {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  cursor: pointer;
  user-select: none;
  font-size: var(--text-xs);
}
.snowball-item-label input[type="checkbox"] {
  cursor: pointer;
  accent-color: var(--color-accent);
}
.snowball-item-label input[type="checkbox"]:disabled {
  cursor: not-allowed;
  opacity: 0.4;
}
.snowball-item-time {
  flex-shrink: 0;
  color: var(--color-text-subtle);
  font-size: var(--text-xs);
}
.snowball-item-meta {
  flex-shrink: 0;
  color: var(--color-text-subtle);
  font-size: var(--text-xs);
}
.snowball-item-div {
  flex: 1;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.snowball-item.is-selected .snowball-item-div {
  color: var(--color-accent-text);
}

/* 2026-06-01 v2:祖先链锁定 UI */
.snowball-chain-banner {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  margin: var(--space-2) 0;
  background: var(--color-accent-soft);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-md);
  font-size: var(--text-xs);
  color: var(--color-accent-text);
  line-height: 1.5;
}
.snowball-chain-banner strong {
  color: var(--color-accent);
  font-weight: 600;
}
.chain-banner-icon {
  flex-shrink: 0;
}
/* 2026-06-02:姐妹分支提示 banner — 灰色基调 vs 紫色 chain-banner */
.snowball-sibling-banner {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  margin: var(--space-2) 0;
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  line-height: 1.5;
}
.snowball-sibling-banner strong {
  color: var(--color-text);
  font-weight: 600;
}
.snowball-item-gen {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  white-space: nowrap;
}
.snowball-item.is-locked-ancestor {
  background: var(--color-accent-soft);
  opacity: 0.85;
}
.snowball-item.is-locked-ancestor .snowball-item-label {
  /* 鼠标不变手掌:祖先项整行 default cursor */
  cursor: default;
}
.snowball-item.is-disabled-conflict {
  opacity: 0.4;
}
.snowball-item.is-disabled-conflict .snowball-item-label {
  cursor: not-allowed;
}
.snowball-locked-tag {
  flex-shrink: 0;
  font-size: 10px;
  color: var(--color-accent);
  background: rgba(124, 58, 237, 0.1);
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  white-space: nowrap;
  margin-left: auto;
}
/* 2026-06-02:姐妹分支标记 — 灰色,区分于紫色"必读"标签 */
.snowball-sibling-tag {
  flex-shrink: 0;
  font-size: 10px;
  color: var(--color-text-subtle);
  background: var(--color-bg-subtle);
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  white-space: nowrap;
  margin-left: auto;
  cursor: help;
}

/* banners */
.banner {
  padding: var(--space-3) var(--space-4);
  font-size: var(--text-sm);
  border-radius: var(--radius-md);
}
.banner-info {
  color: var(--color-text-muted);
  background: var(--color-bg-subtle);
}
.banner-warn {
  color: var(--color-danger);
  background: var(--color-danger-soft);
}
.banner-error {
  color: var(--color-danger);
  background: var(--color-danger-soft);
}

/* submit 灰按钮的诊断提示(轻量,不抢 banner 的视觉) */
.block-reason {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  text-align: right;
  font-style: italic;
}

/* actions */
.actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  margin-top: var(--space-2);
}
.ghost-btn {
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  background: transparent;
  border-radius: var(--radius-md);
}
.ghost-btn:hover:not(:disabled) {
  color: var(--color-text);
  background: var(--color-surface-hover);
}
.primary-btn {
  padding: var(--space-2) var(--space-5);
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border-radius: var(--radius-md);
  transition: background var(--duration-fast) var(--ease-out);
}
.primary-btn:hover:not(:disabled) {
  background: var(--color-accent-hover);
}
.primary-btn:disabled {
  background: var(--color-text-subtle);
  cursor: not-allowed;
}

/* ===== running 屏 ===== */
.running-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
  padding: var(--space-4) 0;
}
.progress-wrap {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.progress-track {
  width: 100%;
  height: 8px;
  background: var(--color-bg-subtle);
  border-radius: var(--radius-full);
  overflow: hidden;
}
.progress-fill {
  height: 100%;
  background: var(--color-accent);
  border-radius: var(--radius-full);
  transition: width 0.4s var(--ease-out);
}
.progress-text {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  text-align: center;
}

.cost-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: var(--space-3) var(--space-4);
  background: var(--color-bg-subtle);
  border-radius: var(--radius-md);
}
.cost-label {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
}
.cost-value {
  font-size: var(--text-base);
  color: var(--color-text);
  font-weight: 500;
}

.hint {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  text-align: center;
  line-height: 1.6;
}

/* ===== 实时事件流(Sprint 1.L)===== */
.event-stream {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  max-height: 280px;
  overflow-y: auto;
}
.event-stream-title {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  letter-spacing: 0.04em;
  margin-bottom: var(--space-1);
}
.event-empty {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  text-align: center;
  padding: var(--space-2) 0;
}
.event-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  list-style: none;
  /* 倒序展示,最新在上;CSS 不需要倒,JS computed 已倒 */
}
.event-row {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text);
  line-height: 1.6;
  word-break: break-word;
}
.event-row.indent-1 {
  padding-left: var(--space-5);
}
.event-icon {
  flex-shrink: 0;
  width: 12px;
  text-align: center;
  color: var(--color-text-subtle);
}
.event-row.tone-success .event-icon {
  color: var(--color-accent);
}
.event-row.tone-error .event-icon {
  color: var(--color-danger);
}
.event-main {
  flex-shrink: 0;
  color: var(--color-text);
}
.event-row.tone-error .event-main {
  color: var(--color-danger);
}
.event-sub {
  color: var(--color-text-subtle);
  font-size: var(--text-xs);
  flex: 1;
  min-width: 0;
}

/* done 屏自身 — 全部委托给 NarrativeStream(本组件无样式接管) */

/* mono(running 屏 cost 数字 + .progress-text 等共用) */
.mono {
  font-family: var(--font-mono);
}

/* sr-only — radio 隐藏但保持可达 */
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

/* M7.K-fix(2026-05-20)audit 建议预填 banner — 与反事实区视觉同源但色更暖,
   提示用户"这些字段被自动调过"|让用户对 dock 状态有清晰感知 */
.audit-banner {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
  margin: 0 var(--space-5) var(--space-3);
  background: rgba(245, 158, 11, 0.10);    /* 暖橙微透 — 区别于反事实紫色调 */
  border: 1px solid rgba(245, 158, 11, 0.45);
  border-radius: var(--radius-md);
}
.audit-banner-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: var(--space-2);
}
.audit-banner-title {
  font-size: var(--text-sm);
  font-weight: 600;
  color: #B45309;             /* 暖橙深色 — 与 banner 底色呼应 */
}
.audit-banner-issue {
  margin-left: 4px;
  font-weight: 500;
  color: var(--color-text-muted);
}
.audit-banner-close {
  width: 22px;
  height: 22px;
  font-size: var(--text-md);
  color: var(--color-text-muted);
  background: transparent;
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  line-height: 1;
}
.audit-banner-close:hover {
  color: var(--color-text);
  background: rgba(0, 0, 0, 0.04);
}
.audit-banner-list {
  list-style: disc;
  padding-left: var(--space-5);
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.audit-banner-list li {
  font-size: var(--text-xs);
  color: var(--color-text);
  line-height: 1.55;
}
.audit-banner-foot {
  margin: 0;
  font-size: 11px;
  color: var(--color-text-subtle);
  font-style: italic;
}

/* Sprint 2.C+ 反事实摘要区(中间态 / 周期态 dock 顶部)*/
.cf-summary-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
  margin: 0 var(--space-5) var(--space-3);
  background: var(--color-accent-soft);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-md);
}
.cf-summary-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}
.cf-summary-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--color-accent-text);
}
.cf-summary-icon {
  font-size: var(--text-base);
}

/* M7.I(2026-05-20)反事实摘要新增 hint + badge + 空态 */
.cf-count-badge {
  margin-left: 6px;
  padding: 1px 8px;
  font-size: 11px;
  font-weight: 500;
  color: var(--color-accent-text);
  background: var(--color-surface);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-full);
}
.cf-summary-hint {
  margin: 0;
  font-size: 11px;
  color: var(--color-text-muted);
  line-height: 1.5;
}
.cf-summary-empty {
  margin: 0;
  padding: var(--space-2);
  font-size: 11px;
  color: var(--color-text-muted);
  line-height: 1.55;
  background: var(--color-surface);
  border-radius: var(--radius-sm);
  font-style: italic;
}

/* M7.J(2026-05-20)起点锚点字段 — 与反事实区共用底色,层次承接 */
.anchor-field {
  margin-top: var(--space-2);
  padding-top: var(--space-2);
  border-top: 1px dashed var(--color-accent-border);
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.anchor-label {
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--color-accent-text);
}
.anchor-select {
  padding: 6px var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.anchor-select:focus {
  outline: 2px solid var(--color-accent);
  outline-offset: 1px;
}
.anchor-preview {
  margin: 0;
  padding: var(--space-2);
  font-size: 11px;
  color: var(--color-text-muted);
  background: var(--color-accent-soft);
  border-radius: var(--radius-sm);
  line-height: 1.55;
}
.anchor-preview strong {
  color: var(--color-accent-text);
  font-weight: 500;
}
.cf-edit-btn {
  padding: 3px var(--space-2);
  font-size: 11px;
  color: var(--color-accent-text);
  background: var(--color-surface);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.cf-edit-btn:hover {
  background: var(--color-accent);
  color: var(--color-text-on-accent);
}
.cf-summary-list {
  display: flex;
  flex-direction: column;
  gap: 3px;
  list-style: none;
  padding: 0;
  margin: 0;
  max-height: 160px;
  overflow-y: auto;
}
.cf-summary-item {
  font-size: var(--text-xs);
  padding: 4px var(--space-2);
  background: var(--color-surface);
  border-radius: var(--radius-sm);
  transition: opacity var(--duration-fast) var(--ease-out);
}
.cf-summary-item.is-deselected {
  opacity: 0.4;
}
.cf-checkbox-label {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  cursor: pointer;
}
.cf-checkbox-label input[type="checkbox"] {
  margin-top: 2px;
  flex-shrink: 0;
  accent-color: var(--color-accent);
}
.cf-item-text {
  flex: 1;
  display: flex;
  align-items: baseline;
  gap: 6px;
  flex-wrap: wrap;
}
.cf-item-type {
  font-size: 10px;
  padding: 1px 5px;
  border-radius: var(--radius-sm);
  font-weight: 600;
}
.cf-item-type.type-character { color: var(--color-accent-text); background: var(--color-accent-soft); }
.cf-item-type.type-event { color: #B45309; background: rgba(245, 158, 11, 0.15); }
.cf-item-type.type-relationship { color: #0E7490; background: rgba(14, 116, 144, 0.12); }
.cf-item-type.type-world { color: #B45309; background: rgba(245, 158, 11, 0.2); font-weight: 700; }
.cf-item-field {
  color: var(--color-text);
  font-weight: 500;
}
.cf-item-intent {
  color: var(--color-text-muted);
  font-style: italic;
  font-size: 10px;
}
.cf-item-diff {
  color: var(--color-text-subtle);
  font-size: 10px;
}

/* Sprint 2.C 反事实总数 chip(reshape slider 下方提示)*/
.cf-chip-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: 6px var(--space-3);
  background: var(--color-accent-soft);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-sm);
  font-size: var(--text-xs);
  flex-wrap: wrap;
  margin-top: -6px;   /* 紧贴 ReshapeSlider 下方 */
}
.cf-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 500;
  color: var(--color-accent-text);
}
.cf-chip-icon {
  font-size: var(--text-base);
  line-height: 1;
}
.cf-chip-sub {
  font-size: 10px;
  color: var(--color-text-subtle);
}

/* 阶段 3B(2026-06-02):伏笔继承面板 */
.foreshadow-inherit-section {
  margin: var(--space-3) 0;
  padding: var(--space-3) var(--space-4);
  background: #ecfdf5;
  border: 1px solid #6ee7b7;
  border-radius: var(--radius-md);
}
.foreshadow-inherit-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: var(--space-1);
}
.foreshadow-inherit-title {
  color: #065f46;
  font-size: var(--text-sm);
  font-weight: 600;
}
.foreshadow-inherit-meta {
  font-size: var(--text-xs);
  color: #047857;
  font-weight: 500;
}
.foreshadow-inherit-hint {
  margin: 0 0 var(--space-2) 0;
  font-size: var(--text-xs);
  color: #065f46;
  line-height: 1.55;
}
.foreshadow-inherit-hint strong {
  color: #047857;
  font-weight: 600;
}
.foreshadow-inherit-loading,
.foreshadow-inherit-empty {
  padding: var(--space-2);
  font-size: var(--text-xs);
  color: #047857;
  text-align: center;
}
.foreshadow-inherit-list {
  list-style: none;
  padding: 0;
  margin: 0;
  max-height: 320px;
  overflow-y: auto;
  background: var(--color-surface);
  border-radius: var(--radius-sm);
}
.foreshadow-item {
  padding: var(--space-1) var(--space-2);
  border-bottom: 1px solid var(--color-border);
  transition: background var(--duration-fast) var(--ease-out);
}
.foreshadow-item:last-child {
  border-bottom: none;
}
.foreshadow-item.is-checked {
  background: rgba(167, 243, 208, 0.2);
}
.foreshadow-label {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  cursor: default;
  font-size: var(--text-xs);
  line-height: 1.55;
}
.foreshadow-prio {
  flex-shrink: 0;
  padding: 1px var(--space-1);
  font-size: 10px;
  font-weight: 600;
  border-radius: var(--radius-sm);
  white-space: nowrap;
}
.foreshadow-prio.prio-high {
  background: #fef3c7; color: #92400e;
}
.foreshadow-prio.prio-medium {
  background: #f3efff; color: #5b21b6;
}
.foreshadow-prio.prio-low {
  background: var(--color-bg-subtle);
  color: var(--color-text-muted);
}
.foreshadow-content {
  flex: 1;
  color: var(--color-text);
}
</style>
