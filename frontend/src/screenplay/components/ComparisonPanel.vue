<script setup lang="ts">
/**
 * 多模型对比 — 阶段 8.5 完整 UI(2026-06-08)。
 *
 * 3 阶段:
 *   - config:选目标 scene + 配 2-5 个 provider(label / api_key / base_url / model)
 *   - running:并行调多个 LLM,spinner + 计时
 *   - result:并排候选卡 + 4 维条形 + elements 列表 + 推荐金标 + JSON 导出
 *
 * 配置持久化:每个用户配过的 providers 存 localStorage(api_key 也存,本地敏感)。
 * 下次打开 modal 默认填上次的配置 — 减少摩擦,而不是每次输 4 个 vendor 密钥。
 */
import { computed, onMounted, ref, watch } from "vue";

import {
  compareModels,
  type CompareMode,
  type ComparisonResultApi,
  type ModelCandidateApi,
  type ProviderConfigApi,
} from "../api/screenplay-client";
import ComparisonRadarChart from "./ComparisonRadarChart.vue";
import { useScreenplayStore } from "../stores/screenplay";
import { useBYOKStore } from "../../stores/byok";
import { toast } from "../../composables/useToast";
import { track } from "../../composables/useAnalytics";
import { ApiError } from "../../api/client";

const props = defineProps<{ visible: boolean }>();
const emit = defineEmits<{ (e: "close"): void }>();

const store = useScreenplayStore();
const byok = useBYOKStore();

type Stage = "config" | "running" | "result";
const stage = ref<Stage>("config");

// =====================================================================
// 2026-06-09:运行模式 — byok / platform
// =====================================================================
const mode = ref<CompareMode>(byok.isActive.value ? "byok" : "platform");

/** BYOK 解锁弹窗触发(切到 byok 模式但用户没开通时显示) */
const showBYOKPrompt = ref(false);

async function tryToggleMode(target: CompareMode) {
  if (target === mode.value) return;
  if (target === "byok") {
    // 2026-06-09 bug fix:点击瞬间实时校验,不信任 store 缓存。
    // 根因:watch(visible) 的 `await refreshStatus()` 是异步的,面板渲染
    // 后用户立刻点 tab 时,isActive 可能还是 stale 的 false(创始人/刚激活
    // 订阅的用户会被误判)。这里点击时若缓存显示未开通,先拉一次最新状态,
    // 确认后端也说没开通才弹提示 —— 杜绝时序竞态。
    if (!byok.isActive.value) {
      await byok.refreshStatus();
    }
    if (!byok.isActive.value) {
      showBYOKPrompt.value = true;
      return;
    }
  }
  mode.value = target;
  // 切到 platform 时清空可能历史填的 api_key(避免后端误认)
  if (target === "platform") {
    providers.value = providers.value.map(p => ({ ...p, api_key: "" }));
  } else if (target === "byok") {
    // 切回 byok 模式 — 尝试从 localStorage 恢复历史配置
    loadFromStorage();
  }
}

function closeBYOKPrompt() {
  showBYOKPrompt.value = false;
}

function goToBYOKActivation() {
  closeBYOKPrompt();
  // 关闭对比 modal,触发自携密钥解锁弹窗(全局事件总线 / 直接跳转)
  emit("close");
  // 用 location 触发 sidebar 的自携密钥菜单 — 让用户点击触发 unlock modal
  // 实际平台已有 BYOKUnlockModal,但触发需 sidebar 菜单点击;最简方案:toast 引导
  toast.info("请点击右下角用户菜单 → 自携密钥 → 开通");
}

// =====================================================================
// 配置阶段 state
// =====================================================================

const selectedSceneId = ref<string>("");

// 2026-06-09:5 个预设 vendor + 每家多个主流模型可下拉选
// label / base_url 固定(厂商身份),model 用户从下拉选
interface VendorPreset {
  label: string;        // 厂商身份(锁定不可编辑)
  base_url: string;     // 厂商 API endpoint
  models: string[];     // 该厂商主流模型(下拉)
  default_model: string;
}

const PRESETS: VendorPreset[] = [
  {
    label: "DeepSeek V3",
    base_url: "https://api.deepseek.com/v1",
    models: ["deepseek-chat", "deepseek-reasoner"],
    default_model: "deepseek-chat",
  },
  {
    label: "OpenAI GPT-4o",
    base_url: "https://api.openai.com/v1",
    models: ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o3-mini"],
    default_model: "gpt-4o",
  },
  {
    label: "Anthropic Claude",
    base_url: "https://api.anthropic.com/v1",
    models: ["claude-sonnet-4-5", "claude-opus-4-1", "claude-haiku-4"],
    default_model: "claude-sonnet-4-5",
  },
  {
    label: "Qwen Max",
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    models: ["qwen-max", "qwen-plus", "qwen-turbo"],
    default_model: "qwen-max",
  },
  {
    label: "Moonshot Kimi",
    base_url: "https://api.moonshot.cn/v1",
    models: ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
    default_model: "moonshot-v1-8k",
  },
];

/** 给定 label 找该厂商可用 model 列表 — provider-row 下拉用 */
function modelsForLabel(label: string): string[] {
  return PRESETS.find(p => p.label === label)?.models ?? [];
}

// 2026-06-09:默认选中 DeepSeek V3 + Qwen Max + Moonshot Kimi 三个(用户拍板)
const DEFAULT_PRESET_LABELS = ["DeepSeek V3", "Qwen Max", "Moonshot Kimi"];

function buildDefaultProviders(): ProviderConfigApi[] {
  return PRESETS
    .filter(p => DEFAULT_PRESET_LABELS.includes(p.label))
    .map(p => ({
      label: p.label,
      base_url: p.base_url,
      model: p.default_model,
      api_key: "",
    }));
}

const providers = ref<ProviderConfigApi[]>(buildDefaultProviders());

const STORAGE_KEY = "huimeng_screenplay_compare_providers";

/** 2026-06-09:加 provider — 只允许从预设里挑(不再支持自填) */
function addProviderFromPreset(preset: VendorPreset) {
  if (providers.value.length >= 5) {
    toast.warning("最多挑 5 个模型同时对比");
    return;
  }
  if (providers.value.some(p => p.label === preset.label)) {
    toast.info(`${preset.label} 已经在列表里了`);
    return;
  }
  providers.value.push({
    label: preset.label,
    base_url: preset.base_url,
    model: preset.default_model,
    api_key: "",
  });
}

function removeProvider(idx: number) {
  if (providers.value.length <= 2) {
    // 2026-06-09:剩 2 个就到底线了,弹友好拒绝
    toast.warning("再少一个就没法对比啦,先从「快速加」里挑一个再来移除");
    return;
  }
  providers.value.splice(idx, 1);
}

function loadFromStorage() {
  // 2026-06-09:只在 byok 模式下尝试 load 历史配置(平台模式只用预设)
  if (mode.value !== "byok") return;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed) || parsed.length < 2) return;
    // 2026-06-09:sanitize — 只接受 PRESETS 里的 vendor,过滤老版自填
    // model 也要在该 vendor 支持列表内,否则用 default_model
    const sanitized: ProviderConfigApi[] = [];
    for (const raw of parsed) {
      const label = String((raw as Record<string, unknown>).label || "");
      const preset = PRESETS.find(p => p.label === label);
      if (!preset) continue;  // 丢弃旧版自定义 vendor
      const rawModel = String((raw as Record<string, unknown>).model || "");
      const model = preset.models.includes(rawModel) ? rawModel : preset.default_model;
      sanitized.push({
        label: preset.label,
        base_url: preset.base_url,    // 用预设的 base_url(防用户老版乱填)
        model,
        api_key: String((raw as Record<string, unknown>).api_key || ""),
      });
    }
    if (sanitized.length >= 2) {
      providers.value = sanitized.slice(0, 5);
    }
    // 不足 2 个:保留默认值,不动 providers
  } catch {
    // 解析失败保留默认
  }
}

function saveToStorage() {
  // 2026-06-09:只在 byok 模式下持久化(平台模式无敏感数据可存)
  if (mode.value !== "byok") return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(providers.value));
  } catch {
    // localStorage 满了 / 隐私模式 — 忽略
  }
}

// 场景列表
const scenes = computed(() => store.screenplay?.scenes ?? []);
const sceneOptions = computed(() => {
  return scenes.value.map(s => ({
    id: s.id,
    label: `${s.id} · ${(s.summary || "").slice(0, 24) || "(无摘要)"}`,
  }));
});

// 验证配置 — 2026-06-09:platform 模式下不要求 api_key / base_url
const isConfigValid = computed(() => {
  if (!selectedSceneId.value) return false;
  if (providers.value.length < 2) return false;
  if (mode.value === "platform") {
    // 只要求 label + model
    return providers.value.every(p => p.label && p.model);
  }
  // byok 模式 — 4 字段全填
  return providers.value.every(
    p => p.label && p.api_key && p.base_url && p.model,
  );
});

// =====================================================================
// 运行阶段 state
// =====================================================================

const elapsedSec = ref(0);
let elapsedTimer: number | null = null;

function startElapsedTimer() {
  elapsedSec.value = 0;
  elapsedTimer = window.setInterval(() => {
    elapsedSec.value += 1;
  }, 1000);
}

function stopElapsedTimer() {
  if (elapsedTimer !== null) {
    window.clearInterval(elapsedTimer);
    elapsedTimer = null;
  }
}

const progressLabel = computed(() => {
  const t = elapsedSec.value;
  if (t < 10) return `${providers.value.length} 个 vendor 并行读场景中…`;
  if (t < 30) return "推敲 elements、归属对白…";
  if (t < 60) return "评估保真度 4 维…";
  return "即将就绪,请耐心(网络慢时 90 秒内)";
});

// =====================================================================
// 结果阶段 state
// =====================================================================

const result = ref<ComparisonResultApi | null>(null);

const sortedCandidates = computed<ModelCandidateApi[]>(() => {
  if (!result.value) return [];
  // 推荐的放第一,然后按 overall 降序
  return [...result.value.candidates].sort((a, b) => {
    if (a.provider_label === result.value!.recommended_label) return -1;
    if (b.provider_label === result.value!.recommended_label) return 1;
    if (!a.scores && !b.scores) return 0;
    if (!a.scores) return 1;
    if (!b.scores) return -1;
    return b.scores.overall - a.scores.overall;
  });
});

// =====================================================================
// 操作
// =====================================================================

async function handleStart() {
  if (!isConfigValid.value) return;
  if (!store.screenplayId) {
    toast.error("剧本未加载");
    return;
  }
  saveToStorage();
  // 2026-06-09 埋点 — 多模型对比触发(N vendors / scene / mode 维度可下钻)
  track("model_compare_run", {
    mode: "screenplay",
    meta: {
      n_providers: providers.value.length,
      providers: providers.value.map(p => p.label),
      scene_id: selectedSceneId.value,
      compare_mode: mode.value,   // byok / platform
    },
  });
  stage.value = "running";
  startElapsedTimer();
  result.value = null;
  try {
    result.value = await compareModels(
      store.screenplayId,
      selectedSceneId.value,
      providers.value,
      mode.value,
    );
    stage.value = "result";
    const success = result.value.candidates.filter(c => c.success).length;
    const total = result.value.candidates.length;
    // 2026-06-09 埋点 — 对比完成,记录推荐 vendor(收口"哪家模型最强")
    track("model_compare_winner", {
      mode: "screenplay",
      meta: {
        recommended: result.value.recommended_label ?? null,
        success_count: success,
        total_count: total,
      },
    });
    if (success === total) {
      toast.success(`${total} 个 vendor 全部成功`);
    } else if (success > 0) {
      toast.warning(`${success}/${total} 个 vendor 成功,${total - success} 失败`);
    } else {
      toast.error("所有 vendor 都失败,请检查 API key / base_url");
    }
  } catch (e) {
    stage.value = "config";
    if (e instanceof ApiError) {
      toast.error(`对比失败:${e.detail && typeof e.detail === "object" && "message" in e.detail ? (e.detail as { message: string }).message : e.message}`);
    } else {
      toast.error("对比失败:" + (e instanceof Error ? e.message : String(e)));
    }
  } finally {
    stopElapsedTimer();
  }
}

function handleBackToConfig() {
  stage.value = "config";
  result.value = null;
}

function handleClose() {
  stopElapsedTimer();
  emit("close");
}

function exportCandidateJson(c: ModelCandidateApi) {
  const data = {
    provider_label: c.provider_label,
    model: c.model,
    scene_id: result.value?.scene_id || "",
    success: c.success,
    elements: c.elements,
    scores: c.scores,
    usage: c.usage,
    duration_ms: c.duration_ms,
    exported_at: new Date().toISOString(),
  };
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `compare-${c.provider_label.replace(/\W+/g, "_")}-${result.value?.scene_id}.json`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  toast.success(`已导出 ${c.provider_label} 候选`);
}

// =====================================================================
// 生命周期
// =====================================================================

onMounted(() => {
  loadFromStorage();
});

watch(
  () => props.visible,
  async (v) => {
    if (v) {
      stage.value = "config";
      // 2026-06-09:每次打开主动 refresh BYOK 状态(防 store 缓存过期)
      // 后端 byok_service 新加了"创始人自动激活",但 store 在登录时拉了一次就不再拉,
      // 切创始人账号 / 新激活订阅后,这里要主动拉新状态
      await byok.refreshStatus();
      // 刷完根据最新 BYOK 状态决定默认模式
      mode.value = byok.isActive.value ? "byok" : "platform";
      loadFromStorage();
      // 默认选第一个 scene
      if (!selectedSceneId.value && scenes.value.length > 0) {
        selectedSceneId.value = scenes.value[0].id;
      }
    } else {
      stopElapsedTimer();
    }
  },
);

// =====================================================================
// 视觉辅助:4 维评分维度名 → 中文
// =====================================================================

const DIM_LABEL: Record<string, string> = {
  action_density: "动作密度",
  character_alignment: "角色对齐",
  dialogue_coverage: "对白覆盖",
  decision_completeness: "决策完整",
};

const ELEMENT_TYPE_LABEL: Record<string, string> = {
  action: "动作",
  dialogue: "对白",
  voiceover: "V.O.",
  parenthetical: "提示",
};
</script>

<template>
  <Teleport to="body">
    <div v-if="visible" class="cmp-overlay" @click.self="handleClose">
      <div class="cmp-panel screenplay-module">
        <!-- 顶栏 -->
        <header class="cmp-hdr">
          <div>
            <h2 class="literary-heading">多模型对比</h2>
            <!-- 2026-06-09:文案简化 -->
            <p class="cmp-sub">让几个 AI 同时改写同一场,看谁写得更好</p>
          </div>
          <button class="cmp-close-btn" @click="handleClose" title="关闭">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </header>

        <!-- ============== CONFIG STAGE ============== -->
        <section v-if="stage === 'config'" class="cmp-body">
          <!-- 2026-06-09:运行模式切换 — BYOK vs 平台默认 -->
          <div class="mode-switcher">
            <button
              type="button"
              class="mode-tab"
              :class="{ active: mode === 'platform' }"
              @click="tryToggleMode('platform')"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" stroke-width="1.6" stroke-linecap="round">
                <path d="M9 12l2 2 4-4" />
                <circle cx="12" cy="12" r="10" />
              </svg>
              用平台模型
              <span class="mode-tab-sub">消耗你的配额</span>
            </button>
            <button
              type="button"
              class="mode-tab"
              :class="{ active: mode === 'byok', locked: !byok.isActive.value }"
              @click="tryToggleMode('byok')"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" stroke-width="1.6" stroke-linecap="round">
                <path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4" />
              </svg>
              用我自己的 API key
              <span class="mode-tab-sub">
                {{ byok.isActive.value ? "自携密钥已开通" : "需开通自携密钥" }}
              </span>
            </button>
          </div>

          <!-- 场景选择 -->
          <div class="cfg-section">
            <label class="cfg-label">目标场景</label>
            <select v-model="selectedSceneId" class="cfg-select">
              <option value="" disabled>请选择一场</option>
              <option v-for="opt in sceneOptions" :key="opt.id" :value="opt.id">
                {{ opt.label }}
              </option>
            </select>
          </div>

          <!-- Provider 配置 -->
          <div class="cfg-section cfg-section--grow">
            <div class="cfg-label-row">
              <label class="cfg-label">
                {{ mode === 'platform' ? '挑几个模型' : 'Provider 配置' }}
                <span class="mono cfg-count">{{ providers.length }} / 5</span>
              </label>
              <div class="preset-row">
                <span class="preset-label">快速加:</span>
                <button
                  v-for="p in PRESETS"
                  :key="p.label"
                  class="preset-chip"
                  :disabled="providers.length >= 5 || providers.some(x => x.label === p.label)"
                  @click="addProviderFromPreset(p)"
                >
                  {{ p.label }}
                </button>
              </div>
            </div>

            <ul class="provider-list">
              <li
                v-for="(p, idx) in providers"
                :key="idx"
                class="provider-row"
                :class="{ 'provider-row--platform': mode === 'platform' }"
              >
                <div class="prow-idx mono">#{{ idx + 1 }}</div>
                <div class="prow-fields" :class="{ 'prow-fields--platform': mode === 'platform' }">
                  <!-- 2026-06-09:厂商 label 锁住,不可编辑 -->
                  <div class="prow-vendor">{{ p.label }}</div>
                  <!-- 2026-06-09:platform 模式隐藏 api_key / base_url 两列 -->
                  <template v-if="mode === 'byok'">
                    <input
                      v-model="p.api_key"
                      class="prow-input"
                      type="password"
                      placeholder=""
                    />
                    <input
                      v-model="p.base_url"
                      class="prow-input"
                      placeholder=""
                    />
                  </template>
                  <!-- 2026-06-09:model 改下拉,只在该厂商支持的主流模型里选 -->
                  <select v-model="p.model" class="prow-input prow-select">
                    <option
                      v-for="m in modelsForLabel(p.label)"
                      :key="m"
                      :value="m"
                    >
                      {{ m }}
                    </option>
                  </select>
                </div>
                <button
                  class="prow-del-btn"
                  :disabled="providers.length <= 2"
                  @click="removeProvider(idx)"
                  title="移除"
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                       stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
                    <line x1="5" y1="12" x2="19" y2="12" />
                  </svg>
                </button>
              </li>
            </ul>

            <!-- 2026-06-09:删 "+ 加 Provider" 自填入口 — 用户只能从「快速加」选 -->
          </div>

          <!-- 2026-06-09:文案改为用户语言 -->
          <p class="cost-hint">
            {{ mode === 'platform'
              ? `将让 ${providers.length} 个模型同时写,预计 30-90 秒。失败的模型不影响其他。`
              : `将让 ${providers.length} 个模型同时写,预计 30-90 秒。失败的不影响其他,API 密钥仅在浏览器存,不上传服务器。`
            }}
          </p>

          <div class="cfg-ftr">
            <button class="btn-cancel" @click="handleClose">取消</button>
            <button
              class="btn-primary"
              :disabled="!isConfigValid"
              @click="handleStart"
            >
              开始对比
            </button>
          </div>
        </section>

        <!-- ============== RUNNING STAGE ============== -->
        <section v-else-if="stage === 'running'" class="cmp-running">
          <svg class="spin" width="48" height="48" viewBox="0 0 24 24" fill="none"
               stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <path d="M21 12a9 9 0 11-6.219-8.56" />
          </svg>
          <div class="running-title literary-heading">{{ providers.length }} 个模型并行赛跑中</div>
          <div class="running-label">{{ progressLabel }}</div>
          <div class="running-meta">
            已用时 {{ elapsedSec }}s · 请勿关闭窗口
          </div>
        </section>

        <!-- ============== RESULT STAGE ============== -->
        <section v-else-if="stage === 'result' && result" class="cmp-result">
          <!-- 顶部概览 + 操作 -->
          <div class="result-ctrl">
            <div class="result-summary">
              <span class="rs-scene mono">{{ result.scene_id }}</span>
              <span class="rs-sep">·</span>
              <span class="rs-stat">
                {{ result.candidates.filter(c => c.success).length }}/{{ result.candidates.length }} 成功
              </span>
              <span v-if="result.recommended_label" class="rs-sep">·</span>
              <span v-if="result.recommended_label" class="rs-recommended">
                🏆 推荐:{{ result.recommended_label }}
              </span>
            </div>
            <button class="back-btn" @click="handleBackToConfig">
              ← 重新配置
            </button>
          </div>

          <!-- ===== 顶部排行榜:0.5 秒抓眼球「哪个赢了」===== -->
          <div class="leaderboard">
            <div
              v-for="(c, i) in sortedCandidates.filter(x => x.success && x.scores)"
              :key="`lb-${c.provider_label}`"
              class="lb-row"
              :class="{ recommended: c.provider_label === result.recommended_label }"
            >
              <span class="lb-rank mono">#{{ i + 1 }}</span>
              <span class="lb-label">{{ c.provider_label }}</span>
              <span v-if="c.provider_label === result.recommended_label" class="lb-crown">🏆</span>
              <div class="lb-bar-wrap">
                <div
                  class="lb-bar"
                  :style="`width: ${Math.min(100, (c.scores?.overall ?? 0) * 100)}%`"
                ></div>
              </div>
              <span class="lb-num mono">{{ ((c.scores?.overall ?? 0) * 100).toFixed(0) }}</span>
            </div>
            <div
              v-for="c in result.candidates.filter(x => !x.success)"
              :key="`lb-fail-${c.provider_label}`"
              class="lb-row lb-row--failed"
            >
              <span class="lb-rank mono">×</span>
              <span class="lb-label">{{ c.provider_label }}</span>
              <span class="lb-err">{{ (c.error_message || "失败").slice(0, 40) }}</span>
            </div>
          </div>

          <!-- ===== 雷达图:为什么赢(2 秒看出形状)===== -->
          <div
            v-if="result.candidates.filter(c => c.success).length >= 1"
            class="radar-section"
          >
            <ComparisonRadarChart
              :candidates="result.candidates"
              :recommended-label="result.recommended_label"
              :size="380"
            />
          </div>

          <!-- ===== 细节卡片:深入对比 ===== -->
          <h3 class="section-title">逐个候选 · 细节</h3>
          <div class="cand-grid">
            <article
              v-for="c in sortedCandidates"
              :key="c.provider_label"
              class="cand-card"
              :class="{
                recommended: c.provider_label === result.recommended_label,
                failed: !c.success,
              }"
            >
              <!-- 卡头 -->
              <header class="cc-hdr">
                <div class="cc-title-row">
                  <h3 class="cc-title">{{ c.provider_label }}</h3>
                  <span v-if="c.provider_label === result.recommended_label" class="rec-badge">
                    🏆 推荐
                  </span>
                </div>
                <div class="cc-meta">
                  <span class="mono">{{ c.model }}</span>
                  <span class="cc-meta-sep">·</span>
                  <span>{{ (c.duration_ms / 1000).toFixed(1) }}s</span>
                  <span v-if="c.usage.input_tokens" class="cc-meta-sep">·</span>
                  <span v-if="c.usage.input_tokens">
                    {{ c.usage.input_tokens + (c.usage.output_tokens || 0) }} tokens
                  </span>
                </div>
              </header>

              <!-- 失败:错误信息 -->
              <div v-if="!c.success" class="cc-error">
                <div class="err-icon">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none"
                       stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                    <circle cx="12" cy="12" r="10" />
                    <line x1="12" y1="8" x2="12" y2="12" />
                    <line x1="12" y1="16" x2="12.01" y2="16" />
                  </svg>
                </div>
                <div class="err-msg">{{ c.error_message || "调用失败" }}</div>
              </div>

              <!-- 成功:评分 + elements -->
              <template v-else-if="c.scores">
                <!-- 4 维评分(条形) -->
                <div class="cc-scores">
                  <div class="overall-row">
                    <span class="overall-label">综合</span>
                    <div class="overall-bar-wrap">
                      <div
                        class="overall-bar"
                        :style="`width: ${Math.min(100, c.scores.overall * 100)}%`"
                      ></div>
                    </div>
                    <span class="overall-num mono">{{ (c.scores.overall * 100).toFixed(0) }}</span>
                  </div>

                  <ul class="dim-list">
                    <li v-for="dim in (['action_density', 'character_alignment', 'dialogue_coverage', 'decision_completeness'] as const)" :key="dim" class="dim-row">
                      <span class="dim-label">{{ DIM_LABEL[dim] }}</span>
                      <div class="dim-bar-wrap">
                        <div
                          class="dim-bar"
                          :style="`width: ${Math.min(100, c.scores[dim] * 100)}%`"
                        ></div>
                      </div>
                      <span class="dim-num mono">{{ c.scores[dim].toFixed(2) }}</span>
                    </li>
                  </ul>

                  <div class="ec-row">
                    <span class="ec-pill">{{ c.scores.elements_count }} elements</span>
                    <span v-if="c.scores.dialogue_count > 0" class="ec-pill">
                      {{ c.scores.dialogue_count }} 对白
                    </span>
                    <span v-if="c.scores.voiceover_count > 0" class="ec-pill">
                      {{ c.scores.voiceover_count }} V.O.
                    </span>
                  </div>
                </div>

                <!-- elements 列表 -->
                <div class="cc-elements">
                  <div class="ce-title">抽取的 elements</div>
                  <ul class="el-list">
                    <li
                      v-for="(el, i) in c.elements"
                      :key="i"
                      class="el-row"
                      :class="`el-${el.type}`"
                    >
                      <span class="el-type">{{ ELEMENT_TYPE_LABEL[el.type] || el.type }}</span>
                      <span v-if="el.character_name" class="el-char">
                        {{ el.character_name }}
                      </span>
                      <span class="el-text">{{ el.text }}</span>
                    </li>
                  </ul>
                </div>

                <!-- 导出 -->
                <footer class="cc-ftr">
                  <button
                    class="export-btn"
                    @click="exportCandidateJson(c)"
                    title="导出为 JSON,后续可手动 import 或 由 LLM 通过 user_instruction 引用"
                  >
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
                         stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                      <polyline points="7 10 12 15 17 10" />
                      <line x1="12" y1="15" x2="12" y2="3" />
                    </svg>
                    导出此候选
                  </button>
                </footer>
              </template>
            </article>
          </div>
        </section>
      </div>
    </div>

    <!-- 2026-06-09:非自携用户尝试切到 byok 模式 → 提示开通(嵌套 modal)-->
    <div v-if="showBYOKPrompt" class="byok-prompt-overlay" @click.self="closeBYOKPrompt">
      <div class="byok-prompt screenplay-module">
        <h3 class="byok-prompt-title">使用自己的 API key 需要开通自携密钥</h3>
        <p class="byok-prompt-desc">
          自携密钥让你用自己的 OpenAI / DeepSeek / Claude / Qwen 等账号跑模型,
          不占用平台配额,适合重度用户。月费透明、随时停用。
        </p>
        <div class="byok-prompt-ftr">
          <button class="btn-cancel" @click="closeBYOKPrompt">暂不开通</button>
          <button class="btn-primary" @click="goToBYOKActivation">前往开通</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.cmp-overlay {
  position: fixed;
  inset: 0;
  background: rgba(20, 16, 12, 0.55);
  backdrop-filter: blur(5px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 200;
}
.cmp-panel {
  background: var(--bg);
  border-radius: var(--radius-lg);
  width: 96vw;
  max-width: 1400px;
  /* 2026-06-09 bug fix:固定 height: 92vh → 自适应内容高度 + 上限封顶。
   * 根因:config 阶段只有 3 个模型,内容短,但面板强撑 92vh,footer
   * 跟在内容后,下方一大片空白。改 auto 后:短内容 → 面板贴合内容,
   * footer 自然到底;result 阶段内容高 → 撞 max-height,cmp-body 内部滚。*/
  height: auto;
  max-height: 92vh;
  display: flex;
  flex-direction: column;
  box-shadow: var(--shadow-lg);
  overflow: hidden;
  color: var(--text);
}

.cmp-hdr {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding: 18px 24px 14px;
  border-bottom: 1px solid var(--border-soft);
  flex-shrink: 0;
}
.cmp-hdr h2 {
  margin: 0 0 4px;
  font-size: 22px;
  font-weight: 500;
  color: var(--text-strong);
}
.cmp-sub {
  margin: 0;
  font-size: 12px;
  color: var(--text-muted);
  line-height: 1.5;
}
.cmp-close-btn {
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  width: 32px;
  height: 32px;
  cursor: pointer;
  color: var(--text-muted);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 150ms;
}
.cmp-close-btn:hover {
  color: var(--text);
  background: var(--hover-bg);
}

.cmp-body {
  flex: 1;
  padding: 20px 24px;
  overflow-y: auto;
  /* 2026-06-09:用 flex column 让内部 section 撑开,治"底部留白"bug */
  display: flex;
  flex-direction: column;
}

/* 2026-06-09:运行模式切换 */
.mode-switcher {
  display: flex;
  gap: 8px;
  margin-bottom: 18px;
  padding: 4px;
  background: var(--bg-deep);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
}
.mode-tab {
  flex: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 8px 14px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  font-size: 12.5px;
  color: var(--text-muted);
  cursor: pointer;
  transition: all var(--transition-fast);
  flex-wrap: wrap;
}
.mode-tab.active {
  background: var(--card-bg);
  border-color: var(--border);
  color: var(--accent-text);
  box-shadow: var(--shadow-sm);
}
.mode-tab:hover:not(.active) {
  color: var(--text);
}
.mode-tab.locked {
  opacity: 0.7;
}
.mode-tab-sub {
  font-size: 10px;
  color: var(--text-subtle);
  letter-spacing: 0.04em;
  width: 100%;
  margin-top: 2px;
  font-weight: 400;
}
.mode-tab.active .mode-tab-sub {
  color: var(--accent-text);
}

/* config */
.cfg-section {
  margin-bottom: 20px;
}
.cfg-section--grow {
  /* 2026-06-09 v2:不再强撑剩余空间(撑开导致 5 行时 focus 边框溢出);
   * 改用自然高度 + cmp-body overflow-y 已能滚 */
  display: flex;
  flex-direction: column;
}
.cfg-count {
  font-weight: 400;
  color: var(--text-muted);
  font-size: 11px;
  margin-left: 6px;
}
.cfg-label,
.cfg-label-row label {
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: var(--text);
  margin-bottom: 6px;
  letter-spacing: 0.04em;
}
.cfg-label-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 6px;
}
.cfg-select {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--card-bg);
  color: var(--text);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
}
.cfg-select:focus {
  outline: none;
  border-color: var(--accent);
}
.cfg-hint {
  font-size: 11px;
  color: var(--text-muted);
  margin: 4px 0 0;
  font-style: italic;
  line-height: 1.5;
}

.preset-row {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  align-items: center;
}
.preset-label {
  font-size: 11px;
  color: var(--text-muted);
}
.preset-chip {
  font-size: 10.5px;
  padding: 2px 8px;
  border: 1px solid var(--border);
  background: var(--card-bg);
  color: var(--text-muted);
  border-radius: var(--radius-sm);
  cursor: pointer;
  letter-spacing: 0.04em;
}
.preset-chip:hover:not(:disabled) {
  background: var(--accent-soft);
  color: var(--accent-text);
  border-color: var(--accent);
}
.preset-chip:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.provider-list {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  /* 2026-06-09 v2:防 5 行内容溢出 + focus border 泄漏 */
  position: relative;
  isolation: isolate;
}
.provider-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--border-soft);
  border-radius: var(--radius-md);
  background: var(--card-bg);
}
.prow-idx {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--accent-text);
  font-weight: 600;
  width: 22px;
}
.prow-fields {
  flex: 1;
  display: grid;
  grid-template-columns: 1fr 1fr 1fr 1fr;
  gap: 6px;
}
/* 2026-06-09:platform 模式只 2 列(vendor / model)*/
.prow-fields--platform {
  grid-template-columns: 1fr 1fr;
}
@media (max-width: 1080px) {
  .prow-fields {
    grid-template-columns: 1fr 1fr;
  }
}
/* 2026-06-09:vendor label 显示为只读 chip(不是 input)*/
.prow-vendor {
  display: inline-flex;
  align-items: center;
  padding: 6px 9px;
  font-size: 12.5px;
  color: var(--text);
  font-weight: 500;
  background: var(--bg-deep);
  border: 1px solid var(--border-soft);
  border-radius: var(--radius-sm);
  letter-spacing: 0.02em;
  cursor: default;
  user-select: none;
}
/* 2026-06-09:model 下拉跟其他 input 视觉一致 */
.prow-select {
  cursor: pointer;
  appearance: auto;
}
.prow-input {
  padding: 6px 9px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg);
  color: var(--text);
  font-size: 11.5px;
  font-family: inherit;
}
.prow-input:focus {
  outline: none;
  border-color: var(--accent);
}
.prow-input::placeholder {
  color: var(--text-muted);
  font-size: 10.5px;
}
.prow-del-btn {
  flex-shrink: 0;
  background: transparent;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  color: var(--text-muted);
  transition: all 150ms;
}
.prow-del-btn:hover:not(:disabled) {
  color: var(--danger);
  border-color: var(--danger);
}
.prow-del-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.add-provider-btn {
  margin-top: 8px;
  padding: 7px 14px;
  background: transparent;
  border: 1px dashed var(--border);
  border-radius: var(--radius-md);
  color: var(--text-muted);
  font-size: 11.5px;
  cursor: pointer;
  font-family: inherit;
  width: 100%;
  letter-spacing: 0.04em;
}
.add-provider-btn:hover:not(:disabled) {
  background: var(--accent-soft);
  color: var(--accent-text);
  border-color: var(--accent);
  border-style: solid;
}
.add-provider-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

/* 2026-06-09:BYOK 提示嵌套 modal */
.byok-prompt-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9999;
}
.byok-prompt {
  width: 100%;
  max-width: 420px;
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 24px;
  box-shadow: var(--shadow-lg);
}
.byok-prompt-title {
  margin: 0 0 10px;
  font-size: 16px;
  font-weight: 600;
  color: var(--text);
  font-family: var(--font-serif);
}
.byok-prompt-desc {
  margin: 0 0 18px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-muted);
}
.byok-prompt-ftr {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.cost-hint {
  font-size: 11px;
  color: var(--text-muted);
  font-style: italic;
  padding-top: 8px;
  margin: 12px 0 0;
  border-top: 1px dashed var(--border-soft);
  line-height: 1.6;
}

.cfg-ftr {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
  padding: 14px 24px;
  border-top: 1px solid var(--border-soft);
  background: var(--bg-deep);
  margin: 16px -24px -20px;
}
.btn-cancel,
.btn-primary {
  padding: 8px 18px;
  border-radius: var(--radius-md);
  font-size: 12.5px;
  font-weight: 500;
  cursor: pointer;
  letter-spacing: 0.04em;
  font-family: inherit;
  transition: all 150ms;
}
.btn-cancel {
  background: var(--card-bg);
  border: 1px solid var(--border);
  color: var(--text);
}
.btn-cancel:hover {
  background: var(--hover-bg);
}
.btn-primary {
  background: var(--accent);
  color: white;
  border: none;
}
.btn-primary:hover:not(:disabled) {
  background: var(--accent-hover);
}
.btn-primary:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

/* running */
.cmp-running {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  padding: 60px 20px;
}
.spin {
  animation: spin 1.4s linear infinite;
  color: var(--accent);
}
@keyframes spin {
  to { transform: rotate(360deg); }
}
.running-title {
  font-size: 22px;
  font-weight: 500;
  color: var(--text-strong);
}
.running-label {
  font-size: 13px;
  color: var(--text);
}
.running-meta {
  font-size: 11.5px;
  color: var(--text-muted);
  font-style: italic;
}

/* result */
.cmp-result {
  flex: 1;
  display: flex;
  flex-direction: column;
  padding: 16px 24px;
  overflow-y: auto;
}

/* === 顶部排行榜 === */
.leaderboard {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 18px;
  padding: 12px 14px;
  background: var(--bg-deep);
  border: 1px solid var(--border-soft);
  border-radius: var(--radius-md);
}
.lb-row {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12.5px;
  padding: 4px 0;
}
.lb-row.recommended {
  font-weight: 500;
}
.lb-row--failed {
  opacity: 0.5;
}
.lb-rank {
  width: 26px;
  text-align: center;
  color: var(--text-muted);
  font-size: 11px;
  font-weight: 600;
}
.lb-row.recommended .lb-rank {
  color: var(--accent);
  font-weight: 700;
}
.lb-row--failed .lb-rank {
  color: var(--danger);
}
.lb-label {
  flex: 0 0 auto;
  min-width: 140px;
  max-width: 200px;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.lb-crown {
  font-size: 14px;
  flex-shrink: 0;
}
.lb-bar-wrap {
  flex: 1;
  height: 10px;
  background: var(--bg);
  border-radius: 5px;
  overflow: hidden;
  min-width: 80px;
}
.lb-bar {
  height: 100%;
  background: linear-gradient(90deg, var(--accent), var(--accent-hover));
  border-radius: 5px;
  transition: width 700ms ease-out;
}
.lb-row:not(.recommended) .lb-bar {
  opacity: 0.6;
}
.lb-num {
  font-size: 13px;
  font-weight: 700;
  color: var(--accent-text);
  min-width: 30px;
  text-align: right;
}
.lb-err {
  flex: 1;
  font-size: 11px;
  color: var(--danger);
  font-style: italic;
  word-break: break-all;
}

/* === 雷达 section === */
.radar-section {
  display: flex;
  justify-content: center;
  margin-bottom: 18px;
}

/* === 二级标题 === */
.section-title {
  margin: 4px 0 12px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
  letter-spacing: 0.04em;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--border-soft);
}
.result-ctrl {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border-soft);
  margin-bottom: 14px;
  flex-shrink: 0;
}
.result-summary {
  display: flex;
  gap: 8px;
  align-items: baseline;
  font-size: 12px;
  color: var(--text-muted);
  flex-wrap: wrap;
}
.rs-scene {
  color: var(--text);
  font-weight: 600;
  font-size: 13px;
}
.rs-sep {
  color: var(--border);
}
.rs-recommended {
  color: var(--accent-text);
  font-weight: 500;
}
.back-btn {
  background: transparent;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 5px 12px;
  font-size: 11.5px;
  color: var(--text-muted);
  cursor: pointer;
  font-family: inherit;
}
.back-btn:hover {
  color: var(--accent);
  border-color: var(--accent);
}

.cand-grid {
  /* 三层叠加后 cmp-result 自己 overflow-y,这里不再独占滚动 */
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: 14px;
}

.cand-card {
  border: 1px solid var(--border-soft);
  border-radius: var(--radius-md);
  background: var(--card-bg);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.cand-card.recommended {
  border: 2px solid var(--accent);
  background: var(--accent-soft);
  box-shadow: 0 4px 14px rgba(139, 92, 246, 0.18);
}
.cand-card.failed {
  background: var(--danger-soft);
  border-color: var(--danger);
}

.cc-hdr {
  padding: 12px 14px 8px;
  border-bottom: 1px solid var(--border-soft);
}
.cc-title-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 6px;
}
.cc-title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-strong);
}
.rec-badge {
  font-size: 10.5px;
  padding: 1px 7px;
  background: var(--accent);
  color: white;
  border-radius: var(--radius-sm);
  font-weight: 500;
  letter-spacing: 0.04em;
}
.cc-meta {
  margin-top: 4px;
  font-size: 11px;
  color: var(--text-muted);
  display: flex;
  gap: 5px;
  flex-wrap: wrap;
}
.cc-meta-sep {
  color: var(--border);
}

/* error */
.cc-error {
  padding: 20px 14px;
  display: flex;
  gap: 10px;
  align-items: flex-start;
}
.err-icon {
  color: var(--danger);
  flex-shrink: 0;
  margin-top: 2px;
}
.err-msg {
  font-size: 12px;
  color: var(--danger);
  line-height: 1.5;
  word-break: break-all;
}

/* scores */
.cc-scores {
  padding: 12px 14px;
  border-bottom: 1px solid var(--border-soft);
}
.overall-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}
.overall-label {
  font-size: 12px;
  font-weight: 600;
  width: 40px;
  flex-shrink: 0;
}
.overall-bar-wrap {
  flex: 1;
  height: 10px;
  background: var(--bg-deep);
  border-radius: 5px;
  overflow: hidden;
}
.overall-bar {
  height: 100%;
  background: linear-gradient(90deg, var(--accent), var(--accent-hover));
  border-radius: 5px;
  transition: width 600ms ease-out;
}
.overall-num {
  font-size: 14px;
  font-weight: 600;
  color: var(--accent-text);
  min-width: 24px;
  text-align: right;
}

.dim-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.dim-row {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 10.5px;
}
.dim-label {
  width: 56px;
  color: var(--text-muted);
  flex-shrink: 0;
}
.dim-bar-wrap {
  flex: 1;
  height: 5px;
  background: var(--bg-deep);
  border-radius: 3px;
  overflow: hidden;
}
.dim-bar {
  height: 100%;
  background: var(--accent);
  opacity: 0.6;
  border-radius: 3px;
  transition: width 600ms ease-out;
}
.dim-num {
  font-size: 10px;
  color: var(--text);
  min-width: 28px;
  text-align: right;
}

.ec-row {
  display: flex;
  gap: 4px;
  margin-top: 10px;
  flex-wrap: wrap;
}
.ec-pill {
  font-size: 10px;
  padding: 1px 6px;
  background: var(--bg);
  color: var(--text-muted);
  border-radius: var(--radius-sm);
  letter-spacing: 0.04em;
  border: 1px solid var(--border-soft);
}

/* elements */
.cc-elements {
  flex: 1;
  padding: 10px 14px;
  overflow-y: auto;
  max-height: 240px;
}
.ce-title {
  font-size: 10.5px;
  font-weight: 600;
  color: var(--text-muted);
  letter-spacing: 0.08em;
  margin-bottom: 6px;
}
.el-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.el-row {
  display: flex;
  gap: 5px;
  font-size: 11px;
  line-height: 1.5;
  padding: 3px 6px;
  border-radius: var(--radius-sm);
  background: var(--bg);
}
.el-action {
  border-left: 2px solid var(--accent);
  padding-left: 8px;
}
.el-dialogue {
  border-left: 2px solid #5d8aa8;
  padding-left: 8px;
}
.el-voiceover {
  border-left: 2px solid #a86d4e;
  padding-left: 8px;
  font-style: italic;
}
.el-parenthetical {
  border-left: 2px solid var(--text-muted);
  padding-left: 8px;
  color: var(--text-muted);
}
.el-type {
  font-size: 9.5px;
  padding: 0 4px;
  background: var(--code-bg);
  color: var(--text-muted);
  border-radius: var(--radius-sm);
  flex-shrink: 0;
  align-self: flex-start;
  margin-top: 2px;
  font-family: var(--font-mono);
}
.el-char {
  font-weight: 600;
  color: var(--text-strong);
  flex-shrink: 0;
}
.el-text {
  flex: 1;
  color: var(--text);
  word-break: break-word;
}

.cc-ftr {
  padding: 8px 14px;
  border-top: 1px solid var(--border-soft);
  background: var(--bg-deep);
}
.export-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  background: transparent;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 4px 10px;
  font-size: 11px;
  color: var(--text-muted);
  cursor: pointer;
  font-family: inherit;
  transition: all 150ms;
}
.export-btn:hover {
  color: var(--accent);
  border-color: var(--accent);
}

.mono {
  font-family: var(--font-mono);
}
</style>
