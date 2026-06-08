<script setup lang="ts">
/**
 * 版本树 diff 面板 — 阶段 8.3 第 3b 张牌(2026-06-08)。
 *
 * UX:
 *   - 全屏 modal,左右两栏分别选版本(默认 left=当前 / right=parent)
 *   - 中间:change_log 逐条高亮 + action 色块(added 绿 / removed 红 /
 *     modified 紫 / split & merged 黄)
 *   - 右下角"回滚到此版"按钮 — 一键切到 left 版本
 *
 * 数据:版本树字段(parent_screenplay_id + optimization_log.change_log)
 * 已经在 store.versions 里(列表 API 返完整 optimization_log)。
 */
import { computed, ref, watch } from "vue";

import { useScreenplayStore } from "../stores/screenplay";
import type {
  ChangeLogEntry,
  ScreenplayVersion,
} from "../types/screenplay";
import { confirm } from "../../composables/useConfirm";
import { toast } from "../../composables/useToast";

const props = defineProps<{
  visible: boolean;
}>();

const emit = defineEmits<{
  (e: "close"): void;
}>();

const store = useScreenplayStore();

const versions = computed<ScreenplayVersion[]>(() =>
  // 倒序:最新在前,用户最自然
  [...store.versions].reverse(),
);

// 默认 left = 当前版本,right = 当前的 parent
const leftId = ref<string>("");
const rightId = ref<string>("");

watch(
  () => [props.visible, store.screenplayId, store.versions.length],
  ([v]) => {
    if (!v) return;
    // 当前版本作为 left
    leftId.value = store.screenplayId || "";
    // parent 作为 right(若有)
    const current = store.versions.find(s => s.id === store.screenplayId);
    if (current?.parent_screenplay_id) {
      rightId.value = current.parent_screenplay_id;
    } else {
      // 没有 parent → 找一个不同于 current 的最新版
      const other = versions.value.find(s => s.id !== store.screenplayId);
      rightId.value = other?.id || store.screenplayId || "";
    }
  },
  { immediate: true },
);

const leftVersion = computed(() => store.versions.find(v => v.id === leftId.value));
const rightVersion = computed(() => store.versions.find(v => v.id === rightId.value));

const isSameVersion = computed(() => leftId.value === rightId.value);

const ACTION_LABEL: Record<string, string> = {
  modified: "修改",
  added: "新增",
  removed: "删除",
  split: "拆分",
  merged: "合并",
};

const ACTION_COLOR: Record<string, string> = {
  modified: "var(--accent)",
  added: "var(--success)",
  removed: "var(--danger)",
  split: "var(--warning)",
  merged: "var(--warning)",
};

function originLabel(origin: string, sceneCount: number): string {
  if (origin === "initial") return `初稿 · ${sceneCount} 场`;
  if (origin === "full_screenplay") return `整本重排 · ${sceneCount} 场`;
  if (origin.startsWith("single_scene_")) {
    const sceneId = origin.replace("single_scene_", "");
    const num = sceneId.match(/scene_(\d+)/)?.[1];
    return num ? `单场精修 · 第 ${parseInt(num)} 场` : "单场精修";
  }
  return `${origin} · ${sceneCount} 场`;
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString("zh-CN", {
    month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });
}

// left 的 change_log(展示"从 right 走到 left 改了什么")
const leftChangeLog = computed<ChangeLogEntry[]>(() => {
  return leftVersion.value?.optimization_log?.change_log ?? [];
});

const reasoning = computed(() => leftVersion.value?.optimization_log?.reasoning ?? "");

const submitting = ref(false);

async function handleRollback() {
  if (!leftVersion.value) return;
  const ok = await confirm({
    title: "回滚到此版本?",
    message: `当前剧本将切到 「${originLabel(leftVersion.value.origin, leftVersion.value.scene_count)}」(${formatTime(leftVersion.value.created_at)})。\n之后修改的版本都保留在版本树里,你随时可以切回来。`,
    confirmLabel: "回滚",
  });
  if (!ok) return;
  submitting.value = true;
  try {
    await store.switchToVersion(leftId.value);
    toast.success("已切到目标版本");
    emit("close");
  } catch (e) {
    toast.error("回滚失败:" + (e instanceof Error ? e.message : String(e)));
  } finally {
    submitting.value = false;
  }
}

function swap() {
  const t = leftId.value;
  leftId.value = rightId.value;
  rightId.value = t;
}
</script>

<template>
  <Teleport to="body">
    <div v-if="visible" class="vdp-overlay" @click.self="emit('close')">
      <div class="vdp-panel screenplay-module">
        <!-- 顶栏 -->
        <header class="vdp-hdr">
          <div>
            <h2 class="literary-heading">版本对比 / 回滚</h2>
            <p class="vdp-sub">
              选两个版本并排对比 change_log,看清 AI 改了什么,需要时一键回滚
            </p>
          </div>
          <button class="vdp-close-btn" @click="emit('close')" title="关闭">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </header>

        <!-- 两栏版本选择器 -->
        <section class="picker-row">
          <div class="picker">
            <label>左侧版本</label>
            <select v-model="leftId" class="picker-select">
              <option
                v-for="v in versions"
                :key="`l-${v.id}`"
                :value="v.id"
              >
                {{ originLabel(v.origin, v.scene_count) }} · {{ formatTime(v.created_at) }}
              </option>
            </select>
            <span v-if="leftId === store.screenplayId" class="cur-tag">当前</span>
          </div>

          <button class="swap-btn" @click="swap" title="交换两侧">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="17 1 21 5 17 9" />
              <path d="M3 11V9a4 4 0 0 1 4-4h14" />
              <polyline points="7 23 3 19 7 15" />
              <path d="M21 13v2a4 4 0 0 1-4 4H3" />
            </svg>
          </button>

          <div class="picker">
            <label>右侧版本</label>
            <select v-model="rightId" class="picker-select">
              <option
                v-for="v in versions"
                :key="`r-${v.id}`"
                :value="v.id"
              >
                {{ originLabel(v.origin, v.scene_count) }} · {{ formatTime(v.created_at) }}
              </option>
            </select>
            <span v-if="rightId === store.screenplayId" class="cur-tag">当前</span>
          </div>
        </section>

        <!-- 对比内容 -->
        <section class="diff-body">
          <div v-if="isSameVersion" class="diff-msg">
            两侧选了同一个版本 — 请选不同版本来对比
          </div>

          <div v-else-if="!leftVersion || !rightVersion" class="diff-msg">
            版本数据加载中…
          </div>

          <template v-else>
            <!-- 两版概览(左右栏卡片) -->
            <div class="versions-grid">
              <div class="ver-card ver-left">
                <div class="ver-card-hdr">
                  <span class="ver-label">左</span>
                  <span class="ver-name">{{ originLabel(leftVersion.origin, leftVersion.scene_count) }}</span>
                </div>
                <div class="ver-card-meta">
                  <span>{{ formatTime(leftVersion.created_at) }}</span>
                  <span class="meta-sep">·</span>
                  <span>{{ leftVersion.change_count }} 处改动</span>
                </div>
              </div>
              <div class="ver-card ver-right">
                <div class="ver-card-hdr">
                  <span class="ver-label">右</span>
                  <span class="ver-name">{{ originLabel(rightVersion.origin, rightVersion.scene_count) }}</span>
                </div>
                <div class="ver-card-meta">
                  <span>{{ formatTime(rightVersion.created_at) }}</span>
                  <span class="meta-sep">·</span>
                  <span>{{ rightVersion.change_count }} 处改动</span>
                </div>
              </div>
            </div>

            <!-- 左版的 change_log(展示「从右版到左版」改了什么)-->
            <div class="changes-section">
              <div class="cs-title">
                <span>左侧版本 vs 它的 parent 改了哪些场</span>
                <span class="cs-count">{{ leftChangeLog.length }} 处</span>
              </div>

              <div v-if="reasoning" class="reasoning-block">
                <span class="rb-label">AI 思路</span>
                <span class="rb-text">{{ reasoning }}</span>
              </div>

              <ul v-if="leftChangeLog.length > 0" class="change-list">
                <li
                  v-for="(c, i) in leftChangeLog"
                  :key="`c-${i}`"
                  class="change-row"
                  :style="`border-left-color: ${ACTION_COLOR[c.action] || 'var(--border)'}`"
                >
                  <div class="cr-hdr">
                    <span
                      class="action-tag"
                      :style="`background: ${ACTION_COLOR[c.action]}20; color: ${ACTION_COLOR[c.action]}`"
                    >
                      {{ ACTION_LABEL[c.action] || c.action }}
                    </span>
                    <span class="scene-id mono">{{ c.scene_id }}</span>
                    <span
                      v-if="c.original_scene_id && c.original_scene_id !== c.scene_id"
                      class="orig-id mono"
                    >
                      ← {{ c.original_scene_id }}
                    </span>
                  </div>
                  <p class="cr-summary">{{ c.summary }}</p>
                  <p v-if="c.addresses_diagnostic" class="cr-diag">
                    针对:{{ c.addresses_diagnostic }}
                  </p>
                  <p v-if="c.details" class="cr-details">{{ c.details }}</p>
                </li>
              </ul>

              <p v-else class="empty-list">
                此版本是「初稿」或未携带 change_log — 没有可显示的逐场对比
              </p>
            </div>
          </template>
        </section>

        <!-- 底部:回滚按钮 -->
        <footer class="vdp-ftr">
          <span class="ftr-hint">
            「回滚」会切到左侧版本 — 其他版本仍保留在版本树里
          </span>
          <button
            class="rollback-btn"
            :disabled="!leftVersion || leftId === store.screenplayId || submitting"
            @click="handleRollback"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="1 4 1 10 7 10" />
              <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
            </svg>
            {{ submitting ? "切换中..." : leftId === store.screenplayId ? "当前已是此版" : "回滚到左侧版本" }}
          </button>
        </footer>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.vdp-overlay {
  position: fixed;
  inset: 0;
  background: rgba(20, 16, 12, 0.55);
  backdrop-filter: blur(5px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 200;
}
.vdp-panel {
  background: var(--bg);
  border-radius: var(--radius-lg);
  width: 92vw;
  max-width: 1200px;
  height: 88vh;
  display: flex;
  flex-direction: column;
  box-shadow: var(--shadow-lg);
  overflow: hidden;
  color: var(--text);
}

/* 顶栏 */
.vdp-hdr {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding: 18px 24px 14px;
  border-bottom: 1px solid var(--border-soft);
}
.vdp-hdr h2 {
  margin: 0 0 4px;
  font-size: 20px;
  font-weight: 500;
  color: var(--text-strong);
}
.vdp-sub {
  margin: 0;
  font-size: 12px;
  color: var(--text-muted);
}
.vdp-close-btn {
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
.vdp-close-btn:hover {
  color: var(--text);
  background: var(--hover-bg);
}

/* picker row */
.picker-row {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  gap: 16px;
  padding: 16px 24px;
  border-bottom: 1px solid var(--border-soft);
  align-items: end;
}
.picker {
  display: flex;
  flex-direction: column;
  gap: 4px;
  position: relative;
}
.picker label {
  font-size: 11.5px;
  color: var(--text-muted);
  letter-spacing: 0.06em;
}
.picker-select {
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--card-bg);
  color: var(--text);
  font-size: 12.5px;
  font-family: inherit;
  cursor: pointer;
  transition: border-color 150ms;
}
.picker-select:focus {
  outline: none;
  border-color: var(--accent);
}
.cur-tag {
  position: absolute;
  top: -3px;
  right: 0;
  font-size: 10px;
  color: var(--accent-text);
  background: var(--accent-soft);
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  letter-spacing: 0.04em;
}
.swap-btn {
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  width: 36px;
  height: 36px;
  cursor: pointer;
  color: var(--text-muted);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 150ms;
}
.swap-btn:hover {
  border-color: var(--accent);
  color: var(--accent);
}

/* diff body */
.diff-body {
  flex: 1;
  padding: 20px 24px;
  overflow-y: auto;
}
.diff-msg {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--text-muted);
  font-style: italic;
  font-size: 13px;
}

.versions-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-bottom: 20px;
}
.ver-card {
  padding: 12px 14px;
  border-radius: var(--radius-md);
  border: 1px solid var(--border);
}
.ver-left {
  border-left: 3px solid var(--accent);
  background: var(--accent-soft);
}
.ver-right {
  border-left: 3px solid #c39657;
  background: #fdf6e9;
}
.ver-card-hdr {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 4px;
}
.ver-label {
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.12em;
  color: var(--text-muted);
  padding: 1px 6px;
  background: var(--card-bg);
  border-radius: var(--radius-sm);
}
.ver-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-strong);
}
.ver-card-meta {
  font-size: 11px;
  color: var(--text-muted);
  display: flex;
  gap: 6px;
}
.meta-sep {
  color: var(--border);
}

/* changes */
.changes-section {
  margin-top: 8px;
}
.cs-title {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border-soft);
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text);
}
.cs-count {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--accent-text);
}
.reasoning-block {
  display: flex;
  gap: 8px;
  padding: 8px 12px;
  background: var(--accent-soft);
  border-radius: var(--radius-sm);
  margin-bottom: 12px;
  font-size: 12px;
  line-height: 1.55;
}
.rb-label {
  flex-shrink: 0;
  color: var(--accent-text);
  font-weight: 600;
  font-size: 10.5px;
  padding-top: 2px;
}
.rb-text {
  color: var(--text);
  flex: 1;
}

.change-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.change-row {
  padding: 10px 12px;
  background: var(--card-bg);
  border: 1px solid var(--border-soft);
  border-left: 3px solid var(--border);
  border-radius: var(--radius-sm);
}
.cr-hdr {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 4px;
}
.action-tag {
  font-size: 10.5px;
  padding: 1px 8px;
  border-radius: var(--radius-sm);
  font-weight: 500;
}
.scene-id {
  font-size: 11px;
  color: var(--text);
  font-weight: 500;
}
.orig-id {
  font-size: 10.5px;
  color: var(--text-muted);
}
.mono {
  font-family: var(--font-mono);
}
.cr-summary {
  margin: 0 0 4px;
  font-size: 12.5px;
  color: var(--text);
  line-height: 1.55;
}
.cr-diag {
  margin: 4px 0 0;
  font-size: 11px;
  color: var(--text-muted);
  font-style: italic;
}
.cr-details {
  margin: 6px 0 0;
  font-size: 11.5px;
  color: var(--text-muted);
  line-height: 1.5;
  padding-left: 12px;
  border-left: 2px solid var(--border-soft);
}
.empty-list {
  text-align: center;
  padding: 24px 0;
  color: var(--text-muted);
  font-style: italic;
  font-size: 12.5px;
}

/* footer */
.vdp-ftr {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 24px;
  border-top: 1px solid var(--border-soft);
  background: var(--bg-deep);
}
.ftr-hint {
  font-size: 11.5px;
  color: var(--text-muted);
  font-style: italic;
}
.rollback-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 18px;
  border-radius: var(--radius-md);
  background: var(--accent);
  color: white;
  border: none;
  font-size: 12.5px;
  font-weight: 500;
  letter-spacing: 0.04em;
  cursor: pointer;
  transition: all 150ms;
}
.rollback-btn:hover:not(:disabled) {
  background: var(--accent-hover);
}
.rollback-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
</style>
