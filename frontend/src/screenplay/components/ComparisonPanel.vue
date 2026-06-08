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
  type ComparisonResultApi,
  type ModelCandidateApi,
  type ProviderConfigApi,
} from "../api/screenplay-client";
import { useScreenplayStore } from "../stores/screenplay";
import { toast } from "../../composables/useToast";
import { ApiError } from "../../api/client";

const props = defineProps<{ visible: boolean }>();
const emit = defineEmits<{ (e: "close"): void }>();

const store = useScreenplayStore();

type Stage = "config" | "running" | "result";
const stage = ref<Stage>("config");

// =====================================================================
// 配置阶段 state
// =====================================================================

const selectedSceneId = ref<string>("");
const providers = ref<ProviderConfigApi[]>([
  { label: "DeepSeek V3", api_key: "", base_url: "https://api.deepseek.com/v1", model: "deepseek-chat" },
  { label: "OpenAI GPT-4o", api_key: "", base_url: "https://api.openai.com/v1", model: "gpt-4o" },
]);

const PRESETS: Array<Omit<ProviderConfigApi, "api_key">> = [
  { label: "DeepSeek V3", base_url: "https://api.deepseek.com/v1", model: "deepseek-chat" },
  { label: "OpenAI GPT-4o", base_url: "https://api.openai.com/v1", model: "gpt-4o" },
  { label: "Anthropic Claude", base_url: "https://api.anthropic.com/v1", model: "claude-sonnet-4-5" },
  { label: "Qwen Max", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-max" },
  { label: "Moonshot Kimi", base_url: "https://api.moonshot.cn/v1", model: "moonshot-v1-8k" },
];

const STORAGE_KEY = "huimeng_screenplay_compare_providers";

function addProvider(preset?: Omit<ProviderConfigApi, "api_key">) {
  if (providers.value.length >= 5) {
    toast.warning("最多 5 个 provider 同时对比");
    return;
  }
  if (preset) {
    providers.value.push({ ...preset, api_key: "" });
  } else {
    providers.value.push({ label: "", api_key: "", base_url: "", model: "" });
  }
}

function removeProvider(idx: number) {
  if (providers.value.length <= 2) {
    toast.warning("至少需要 2 个 provider 才能对比");
    return;
  }
  providers.value.splice(idx, 1);
}

function loadFromStorage() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed) && parsed.length >= 2) {
      providers.value = parsed.map((p: Record<string, unknown>) => ({
        label: String(p.label || ""),
        api_key: String(p.api_key || ""),
        base_url: String(p.base_url || ""),
        model: String(p.model || ""),
      }));
    }
  } catch {
    // 解析失败默认配置
  }
}

function saveToStorage() {
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

// 验证配置
const isConfigValid = computed(() => {
  if (!selectedSceneId.value) return false;
  if (providers.value.length < 2) return false;
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
  stage.value = "running";
  startElapsedTimer();
  result.value = null;
  try {
    result.value = await compareModels(
      store.screenplayId,
      selectedSceneId.value,
      providers.value,
    );
    stage.value = "result";
    const success = result.value.candidates.filter(c => c.success).length;
    const total = result.value.candidates.length;
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
  (v) => {
    if (v) {
      stage.value = "config";
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
            <p class="cmp-sub">
              一场剧本,N 个 LLM 并行跑 — fidelity 4 维可解释打分,告诉你「为什么 A 比 B 好」
            </p>
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
          <!-- 场景选择 -->
          <div class="cfg-section">
            <label class="cfg-label">目标场景</label>
            <select v-model="selectedSceneId" class="cfg-select">
              <option value="" disabled>请选择一场</option>
              <option v-for="opt in sceneOptions" :key="opt.id" :value="opt.id">
                {{ opt.label }}
              </option>
            </select>
            <p class="cfg-hint">
              选短而对白多的场景效果最明显(角色对峙 / 揭穿 / 告白等)
            </p>
          </div>

          <!-- Provider 配置 -->
          <div class="cfg-section">
            <div class="cfg-label-row">
              <label class="cfg-label">Provider 配置({{ providers.length }}/5)</label>
              <div class="preset-row">
                <span class="preset-label">快速加:</span>
                <button
                  v-for="p in PRESETS"
                  :key="p.label"
                  class="preset-chip"
                  :disabled="providers.length >= 5 || providers.some(x => x.label === p.label)"
                  @click="addProvider(p)"
                >
                  {{ p.label }}
                </button>
              </div>
            </div>
            <p class="cfg-hint">
              每个 provider 需要 4 个字段。API key 仅本地存储(localStorage)+ 单次对比时传给后端,后端不持久化。
            </p>

            <ul class="provider-list">
              <li
                v-for="(p, idx) in providers"
                :key="idx"
                class="provider-row"
              >
                <div class="prow-idx mono">#{{ idx + 1 }}</div>
                <div class="prow-fields">
                  <input
                    v-model="p.label"
                    class="prow-input"
                    placeholder="显示名(DeepSeek V3 / Claude...)"
                  />
                  <input
                    v-model="p.api_key"
                    class="prow-input"
                    type="password"
                    placeholder="API key (sk-xxx...)"
                  />
                  <input
                    v-model="p.base_url"
                    class="prow-input"
                    placeholder="Base URL (https://api.xxx.com/v1)"
                  />
                  <input
                    v-model="p.model"
                    class="prow-input"
                    placeholder="model 名(deepseek-chat / gpt-4o)"
                  />
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

            <button
              class="add-provider-btn"
              :disabled="providers.length >= 5"
              @click="addProvider()"
            >
              + 加 Provider(自填配置)
            </button>
          </div>

          <p class="cost-hint">
            将并行调用 {{ providers.length }} 个 LLM,30-90 秒。
            每个 vendor 单次成本约 ¥0.01-0.10(因模型而异)。失败的 vendor 不影响其他。
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

          <!-- 并排候选列 -->
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
  height: 92vh;
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
}

/* config */
.cfg-section {
  margin-bottom: 20px;
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
@media (max-width: 1080px) {
  .prow-fields {
    grid-template-columns: 1fr 1fr;
  }
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
  overflow: hidden;
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
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: 14px;
  overflow-y: auto;
  padding-right: 4px;
  flex: 1;
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
