<!--
  BYOKConfigView.vue — 自携密钥 独立配置页

  路由:/byok-config(必须登录 + 必须已激活)
  入口:左下角"自携密钥"→ Unlock Modal → "前往配置"

  内容:
    1. 顶部状态卡(月卡到期日 / 配置数)
    2. 预设 provider 网格(DeepSeek / Qwen / GLM / Doubao / Kimi / 自定义)
    3. 编辑抽屉(选 provider 后展开,填 key + 模型名)
    4. 配置列表(已配置的 provider,每行支持 设默认 / 测试 / 删除)
-->

<template>
  <div class="byok-page">
    <!-- 顶部状态 -->
    <header class="page-header">
      <div class="title-block">
        <h1 class="page-title">自携密钥配置</h1>
        <p class="page-sub">
          已激活状态下,平台所有 LLM 调用走你设置的 API key,不消耗平台 credit。
        </p>
      </div>
      <div class="status-pills">
        <div v-if="status?.has_active_subscription" class="status-pill status-pill--active">
          <span class="dot dot--active"></span>
          <span>已激活 · 到期 {{ formatDate(status.active_subscription_expires_at) }}</span>
        </div>
        <div v-else class="status-pill status-pill--inactive">
          <span class="dot dot--inactive"></span>
          <span>未激活 — 请先在左下角"自携密钥"输入激活码</span>
        </div>
      </div>
    </header>

    <!-- Provider 选择网格 -->
    <section class="provider-section">
      <h2 class="section-title">选择 / 添加 模型</h2>
      <div class="provider-grid">
        <button
          v-for="preset in BYOK_PROVIDERS"
          :key="preset.id"
          class="provider-card"
          :class="{ 'is-selected': selectedPresetId === preset.id }"
          @click="selectPreset(preset.id)"
        >
          <div class="provider-label">{{ preset.label }}</div>
          <div class="provider-desc">{{ preset.description }}</div>
          <div v-if="hasConfig(preset.id)" class="provider-badge">已配</div>
        </button>
      </div>
    </section>

    <!-- 编辑表单(选定 provider 后显示) -->
    <section v-if="selectedPreset" class="edit-section">
      <h2 class="section-title">
        配置 {{ selectedPreset.label }}
        <a
          v-if="selectedPreset.apply_url"
          :href="selectedPreset.apply_url"
          target="_blank"
          rel="noopener noreferrer"
          class="apply-link"
        >→ 申请 key</a>
      </h2>

      <div class="form-grid">
        <!-- base_url(custom 必填,其他只读) -->
        <div class="form-row">
          <label class="form-label">接口地址(Base URL)</label>
          <input
            v-model="formBaseUrl"
            type="text"
            placeholder="https://..."
            class="text-input"
            :readonly="!isCustom"
          />
          <p v-if="!isCustom" class="form-hint">{{ selectedPreset.label }} 的官方地址 — 我们已填好,只读</p>
        </div>

        <!-- model_name -->
        <div class="form-row">
          <label class="form-label">模型名(Model Name)</label>
          <input
            v-model="formModelName"
            type="text"
            placeholder="如 deepseek-chat / glm-4-plus"
            class="text-input"
            list="model-suggestions"
          />
          <datalist id="model-suggestions">
            <option
              v-for="m in selectedPreset.model_suggestions"
              :key="m"
              :value="m"
            />
          </datalist>
          <p v-if="!isCustom && selectedPreset.default_model" class="form-hint">
            默认 <code>{{ selectedPreset.default_model }}</code>;可改成其他模型
          </p>
        </div>

        <!-- API Key(密码框 + 显示切换) -->
        <div class="form-row">
          <label class="form-label">API Key</label>
          <div class="key-input-wrap">
            <input
              v-model="formApiKey"
              :type="showKey ? 'text' : 'password'"
              placeholder="sk-..."
              class="text-input key-input"
            />
            <button
              type="button"
              class="key-toggle"
              :aria-label="showKey ? '隐藏 key' : '显示 key'"
              @click="showKey = !showKey"
            >{{ showKey ? "隐藏" : "显示" }}</button>
          </div>
          <p class="form-hint">key 经 AES 加密后存储,平台后端只在调用 LLM 时解密,不会日志输出</p>
        </div>

        <!-- 备注 + 默认 -->
        <div class="form-row form-row--inline">
          <div class="inline-field">
            <label class="form-label">备注(可选)</label>
            <input
              v-model="formDisplayName"
              type="text"
              placeholder="如 工作号 / 备用号"
              class="text-input"
              maxlength="50"
            />
          </div>
          <label class="checkbox-row">
            <input v-model="formIsDefault" type="checkbox" />
            <span>设为默认 — 启用 BYOK 时优先使用此模型</span>
          </label>
        </div>

        <div class="form-actions">
          <button
            class="btn btn--primary"
            :disabled="!canSave || saving"
            @click="handleSave"
          >
            {{ saving ? "保存中…" : "保存配置" }}
          </button>
          <button class="btn btn--ghost" @click="resetForm">清空</button>
        </div>
      </div>
    </section>

    <!-- 已配置列表 -->
    <section v-if="configs.length > 0" class="configs-section">
      <h2 class="section-title">我的配置({{ configs.length }})</h2>
      <div class="configs-list">
        <div
          v-for="cfg in configs"
          :key="cfg.id"
          class="config-row"
          :class="{ 'is-default': cfg.is_default }"
        >
          <div class="config-main">
            <div class="config-line-1">
              <span class="config-provider">{{ providerLabel(cfg.provider) }}</span>
              <span class="config-model">{{ cfg.model_name }}</span>
              <span v-if="cfg.is_default" class="config-default-tag">默认</span>
              <span
                v-if="cfg.last_test_ok === true"
                class="config-test-tag config-test-tag--ok"
                title="上次连通性测试通过"
              >● 已验证</span>
              <span
                v-else-if="cfg.last_test_ok === false"
                class="config-test-tag config-test-tag--fail"
                :title="cfg.last_test_error ?? '连通性测试失败'"
              >● 失败</span>
            </div>
            <div class="config-line-2">
              <span class="config-mask">{{ cfg.api_key_mask }}</span>
              <span v-if="cfg.display_name" class="config-note">— {{ cfg.display_name }}</span>
            </div>
          </div>
          <div class="config-actions">
            <button
              v-if="!cfg.is_default"
              class="row-btn"
              @click="handleSetDefault(cfg.id)"
              :disabled="rowOpInProgress === cfg.id"
            >设为默认</button>
            <button
              class="row-btn"
              @click="handleTest(cfg.id)"
              :disabled="rowOpInProgress === cfg.id"
            >{{ rowOpInProgress === cfg.id ? "测试中…" : "测试" }}</button>
            <button
              class="row-btn row-btn--danger"
              @click="handleDelete(cfg.id)"
              :disabled="rowOpInProgress === cfg.id"
            >删除</button>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";

import { BYOK_PROVIDERS, findProviderPreset } from "../constants/byok-providers";
import { useBYOKStore } from "../stores/byok";
import { useAuthStore } from "../stores/auth";
import { toast } from "../composables/useToast";
import { confirm as confirmDialog } from "../composables/useConfirm";
import { ApiError } from "../api/client";
import type { BYOKConfigUpsertRequest, BYOKProviderId } from "../api/types";

const byok = useBYOKStore();
const auth = useAuthStore();
const router = useRouter();

const status = computed(() => byok.status);
const configs = computed(() => byok.configs);

const selectedPresetId = ref<BYOKProviderId | "">("");
const formBaseUrl = ref("");
const formModelName = ref("");
const formApiKey = ref("");
const formDisplayName = ref("");
const formIsDefault = ref(false);
const showKey = ref(false);
const saving = ref(false);
const rowOpInProgress = ref<string | null>(null);

const selectedPreset = computed(() => {
  if (!selectedPresetId.value) return null;
  return findProviderPreset(selectedPresetId.value) ?? null;
});

const isCustom = computed(() => selectedPresetId.value === "custom");

const canSave = computed(() => {
  if (!selectedPreset.value) return false;
  if (!formBaseUrl.value.trim()) return false;
  if (!formModelName.value.trim()) return false;
  if (!formApiKey.value.trim()) return false;
  return true;
});

function hasConfig(provider: string): boolean {
  return configs.value.some((c) => c.provider === provider);
}

function providerLabel(provider: string): string {
  return findProviderPreset(provider)?.label ?? provider;
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function selectPreset(id: BYOKProviderId) {
  selectedPresetId.value = id;
  const preset = findProviderPreset(id);
  if (!preset) return;

  // 看是否已有该 provider 的配置 → 加载到表单(只 base_url + model_name,key 不回填)
  const existing = configs.value.find((c) => c.provider === id);
  if (existing) {
    formBaseUrl.value = existing.base_url;
    formModelName.value = existing.model_name;
    formDisplayName.value = existing.display_name ?? "";
    formIsDefault.value = existing.is_default;
    formApiKey.value = "";  // 安全 — 不回显密文,要用户重填
  } else {
    formBaseUrl.value = preset.base_url;
    formModelName.value = preset.default_model;
    formDisplayName.value = "";
    formIsDefault.value = configs.value.length === 0;  // 第一条自动设默认
    formApiKey.value = "";
  }
}

function resetForm() {
  selectedPresetId.value = "";
  formBaseUrl.value = "";
  formModelName.value = "";
  formApiKey.value = "";
  formDisplayName.value = "";
  formIsDefault.value = false;
  showKey.value = false;
}

async function handleSave() {
  if (!canSave.value || !selectedPresetId.value || saving.value) return;
  saving.value = true;
  try {
    const req: BYOKConfigUpsertRequest = {
      provider: selectedPresetId.value as BYOKProviderId,
      display_name: formDisplayName.value || null,
      base_url: formBaseUrl.value.trim(),
      model_name: formModelName.value.trim(),
      api_key: formApiKey.value.trim(),
      is_default: formIsDefault.value,
    };
    await byok.upsertConfig(req);
    toast.success("配置已保存");
    resetForm();
  } catch (e) {
    toast.error(e instanceof ApiError ? `保存失败:${e.message}` : "保存失败");
  } finally {
    saving.value = false;
  }
}

async function handleSetDefault(configId: string) {
  rowOpInProgress.value = configId;
  try {
    await byok.setDefault(configId);
    toast.success("已设为默认");
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : "设置默认失败");
  } finally {
    rowOpInProgress.value = null;
  }
}

async function handleTest(configId: string) {
  rowOpInProgress.value = configId;
  try {
    const resp = await byok.testConfig(configId);
    if (resp.ok) {
      toast.success(`连通性测试通过 · ${resp.latency_ms ?? "-"}ms`);
    } else {
      toast.error(`测试失败:${resp.error ?? "未知错误"}`);
    }
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : "测试失败");
  } finally {
    rowOpInProgress.value = null;
  }
}

async function handleDelete(configId: string) {
  const cfg = configs.value.find((c) => c.id === configId);
  if (!cfg) return;
  const ok = await confirmDialog({
    title: `删除 ${providerLabel(cfg.provider)} 配置?`,
    message: "本机加密的 API key 会被清除,无法恢复。",
    danger: true,
    confirmLabel: "删除",
  });
  if (!ok) return;
  rowOpInProgress.value = configId;
  try {
    await byok.deleteConfig(configId);
    toast.success("已删除");
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : "删除失败");
  } finally {
    rowOpInProgress.value = null;
  }
}

onMounted(async () => {
  if (!auth.isAuthed) {
    toast.warning("请先登录");
    router.push("/");
    return;
  }
  await Promise.all([byok.refreshStatus(), byok.fetchConfigs()]);
});

// 未激活也允许进配置页(看 / 配 / 测试),但状态 banner 提示
watch(() => byok.status, (s) => {
  if (s && !s.has_active_subscription && !s.has_unused_subscription && configs.value.length === 0) {
    // 啥都没有,跳回首页
    toast.info("还未开通自携密钥");
  }
}, { immediate: false });
</script>

<style scoped>
.byok-page {
  max-width: 960px;
  margin: 0 auto;
  padding: var(--space-6) var(--space-5);
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
}

.page-header {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.page-title {
  font-size: var(--text-xl);
  font-weight: 700;
  color: var(--color-text-primary);
  margin: 0;
}

.page-sub {
  color: var(--color-text-secondary);
  margin: 0;
  font-size: var(--text-sm);
  line-height: 1.6;
}

.status-pills {
  display: flex;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.status-pill {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: 6px 12px;
  border-radius: 999px;
  font-size: 12px;
  border: 1px solid var(--color-border);
}

.status-pill--active {
  background: rgba(16, 185, 129, 0.08);
  border-color: rgba(16, 185, 129, 0.3);
  color: #047857;
}

.status-pill--inactive {
  background: var(--color-bg-subtle);
  color: var(--color-text-secondary);
}

.dot {
  width: 8px; height: 8px;
  border-radius: 50%;
  display: inline-block;
}

.dot--active { background: #10b981; }
.dot--inactive { background: var(--color-text-muted); }

.section-title {
  font-size: var(--text-base);
  font-weight: 600;
  margin: 0 0 var(--space-3) 0;
  color: var(--color-text-primary);
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.apply-link {
  font-size: 12px;
  color: var(--color-accent);
  text-decoration: none;
  font-weight: 400;
}

.apply-link:hover { text-decoration: underline; }

.provider-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: var(--space-3);
}

.provider-card {
  position: relative;
  text-align: left;
  padding: var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}

.provider-card:hover {
  border-color: var(--color-accent);
  background: var(--color-surface-hover);
}

.provider-card.is-selected {
  border-color: var(--color-accent);
  background: rgba(124, 58, 237, 0.04);
  box-shadow: 0 0 0 2px rgba(124, 58, 237, 0.15);
}

.provider-label {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text-primary);
}

.provider-desc {
  font-size: 12px;
  color: var(--color-text-secondary);
  margin-top: 4px;
  line-height: 1.5;
}

.provider-badge {
  position: absolute;
  top: var(--space-2);
  right: var(--space-2);
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 10px;
}

.edit-section {
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: var(--space-5);
}

.form-grid {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.form-row { display: flex; flex-direction: column; gap: var(--space-2); }

.form-row--inline {
  flex-direction: row;
  align-items: flex-end;
  gap: var(--space-4);
}

.inline-field {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.checkbox-row {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  cursor: pointer;
  font-size: 13px;
  color: var(--color-text-secondary);
  padding: var(--space-2) 0;
}

.form-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--color-text-secondary);
}

.text-input {
  width: 100%;
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-text-primary);
  font-size: var(--text-sm);
}

.text-input:focus {
  outline: none;
  border-color: var(--color-accent);
  box-shadow: 0 0 0 2px rgba(124, 58, 237, 0.15);
}

.text-input[readonly] {
  background: var(--color-bg-subtle);
  color: var(--color-text-muted);
  cursor: default;
}

.form-hint {
  font-size: 11px;
  color: var(--color-text-muted);
  margin: 0;
}

.form-hint code {
  background: var(--color-bg-subtle);
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 11px;
}

.key-input-wrap {
  display: flex;
  gap: var(--space-2);
}

.key-input { flex: 1; font-family: var(--font-mono, monospace); }

.key-toggle {
  padding: 6px 10px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  font-size: 12px;
  color: var(--color-text-secondary);
  cursor: pointer;
}

.key-toggle:hover { background: var(--color-surface-hover); }

.form-actions {
  display: flex;
  gap: var(--space-3);
  margin-top: var(--space-2);
}

.btn {
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-sm);
  font-size: var(--text-sm);
  font-weight: 500;
  cursor: pointer;
  border: 1px solid transparent;
  transition: all var(--duration-fast) var(--ease-out);
}

.btn:disabled { opacity: 0.5; cursor: not-allowed; }

.btn--primary {
  background: var(--color-accent);
  color: var(--color-text-on-accent);
}

.btn--primary:hover:not(:disabled) { filter: brightness(1.1); }

.btn--ghost {
  background: transparent;
  color: var(--color-text-primary);
  border-color: var(--color-border);
}

.btn--ghost:hover:not(:disabled) { background: var(--color-surface-hover); }

.configs-section {}

.configs-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.config-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.config-row.is-default {
  border-color: var(--color-accent);
  background: rgba(124, 58, 237, 0.03);
}

.config-main { flex: 1; min-width: 0; }

.config-line-1 {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-sm);
  margin-bottom: 4px;
}

.config-provider {
  font-weight: 600;
  color: var(--color-text-primary);
}

.config-model {
  font-family: var(--font-mono, monospace);
  font-size: 12px;
  background: var(--color-bg-subtle);
  padding: 2px 6px;
  border-radius: 3px;
  color: var(--color-text-secondary);
}

.config-default-tag {
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  padding: 2px 6px;
  border-radius: 3px;
  font-size: 10px;
}

.config-test-tag {
  font-size: 11px;
  padding: 2px 6px;
  border-radius: 3px;
}

.config-test-tag--ok {
  color: #047857;
  background: rgba(16, 185, 129, 0.1);
}

.config-test-tag--fail {
  color: #b91c1c;
  background: rgba(220, 38, 38, 0.1);
}

.config-line-2 {
  font-size: 12px;
  color: var(--color-text-secondary);
}

.config-mask {
  font-family: var(--font-mono, monospace);
}

.config-note { margin-left: var(--space-2); }

.config-actions {
  display: flex;
  gap: var(--space-2);
}

.row-btn {
  padding: 6px 10px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  font-size: 12px;
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}

.row-btn:hover:not(:disabled) {
  background: var(--color-surface-hover);
  color: var(--color-text-primary);
}

.row-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.row-btn--danger:hover:not(:disabled) {
  color: #dc2626;
  border-color: #dc2626;
}
</style>
