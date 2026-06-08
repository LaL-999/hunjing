<script setup lang="ts">
/**
 * 分集规划面板 — 阶段 8.4 MVP(2026-06-08)。
 *
 * UX:
 *   - 全屏 modal,顶部:目标单集时长滑块(0.5 - 30 分钟)+ 重新规划 button
 *   - 中部:集卡列表,每集显示集号 / 标题 / 时长 / 场景数 / 边界原因
 *   - 展开集卡可见 scene_ids
 *
 * MVP 限制(留口子给后期 LLM 增强):
 *   - 规则版无 logline / cliffhanger
 *   - 标题为"第 N 集 · 首场摘要前 14 字"
 *   - 没有"导出分集 Word/MD"(后期补)
 */
import { computed, ref, watch } from "vue";

import {
  planEpisodes,
  type EpisodePlanApi,
} from "../api/screenplay-client";
import { toast } from "../../composables/useToast";

const props = defineProps<{
  novelId: string;
  visible: boolean;
}>();

const emit = defineEmits<{
  (e: "close"): void;
}>();

const targetMinutes = ref<number>(3.0);
const plan = ref<EpisodePlanApi | null>(null);
const loading = ref(false);
const expandedEp = ref<number>(-1);  // 哪一集展开 scene_ids

const BOUNDARY_LABEL: Record<string, string> = {
  chapter_change: "章节切换",
  strong_transition: "强转场",
  target_met: "目标时长达标",
  target_overflow: "时长超限强切",
  end_of_screenplay: "剧本结束",
  remainder: "末尾残余",
};

async function load() {
  loading.value = true;
  try {
    plan.value = await planEpisodes(props.novelId, targetMinutes.value);
  } catch (e) {
    plan.value = null;
    toast.error("规划失败:" + (e instanceof Error ? e.message : String(e)));
  } finally {
    loading.value = false;
  }
}

watch(
  () => [props.visible, props.novelId],
  ([v]) => {
    if (v) {
      expandedEp.value = -1;
      load();
    }
  },
);

function reload() {
  expandedEp.value = -1;
  load();
}

function toggleEp(num: number) {
  expandedEp.value = expandedEp.value === num ? -1 : num;
}
</script>

<template>
  <Teleport to="body">
    <div v-if="visible" class="epp-overlay" @click.self="emit('close')">
      <div class="epp-panel screenplay-module">
        <header class="epp-hdr">
          <div>
            <h2 class="literary-heading">分集规划</h2>
            <p class="epp-sub">把剧本切成"集"— 短剧 2-3 分钟 / 长剧 8-12 分钟</p>
          </div>
          <button class="epp-close-btn" @click="emit('close')" title="关闭">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </header>

        <!-- 控制条 -->
        <section class="ctrl-row">
          <div class="ctrl-slider-wrap">
            <label class="ctrl-label">
              目标单集时长
              <span class="ctrl-value">{{ targetMinutes.toFixed(1) }} 分钟</span>
            </label>
            <input
              v-model.number="targetMinutes"
              type="range"
              min="0.5"
              max="15"
              step="0.5"
              class="ctrl-slider"
            />
            <div class="ctrl-presets">
              <button
                v-for="p in [{ v: 1.5, l: '短剧 1.5' }, { v: 3, l: '短剧 3' }, { v: 8, l: '长剧 8' }, { v: 12, l: '长剧 12' }]"
                :key="p.v"
                class="preset-btn"
                :class="{ active: Math.abs(targetMinutes - p.v) < 0.1 }"
                @click="targetMinutes = p.v"
              >
                {{ p.l }}
              </button>
            </div>
          </div>
          <button class="reload-btn" @click="reload" :disabled="loading">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="23 4 23 10 17 10" />
              <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
            </svg>
            {{ loading ? "规划中..." : "重新规划" }}
          </button>
        </section>

        <!-- 主体 -->
        <section class="epp-body">
          <div v-if="loading" class="state-msg">正在按目标时长贪心切集…</div>
          <template v-else-if="plan">
            <!-- 概览条 -->
            <div class="overview">
              <span class="ov-item">
                共 <strong>{{ plan.episodes.length }}</strong> 集
              </span>
              <span class="ov-sep">·</span>
              <span class="ov-item">
                总时长 <strong>{{ plan.total_minutes.toFixed(1) }}</strong> 分钟
              </span>
              <span class="ov-sep">·</span>
              <span class="ov-item">{{ plan.total_scenes }} 场总计</span>
              <span class="ov-sep">·</span>
              <span class="ov-item ov-mode">{{ plan.mode === "rule" ? "规则版" : "LLM 增强版" }}</span>
            </div>

            <!-- 集列表 -->
            <ul v-if="plan.episodes.length > 0" class="ep-list">
              <li
                v-for="ep in plan.episodes"
                :key="ep.episode_number"
                class="ep-card"
                :class="{ expanded: expandedEp === ep.episode_number }"
              >
                <div class="ep-hdr" @click="toggleEp(ep.episode_number)">
                  <div class="ep-num mono">第 {{ ep.episode_number }} 集</div>
                  <div class="ep-title literary">{{ ep.title.replace(/^第 \d+ 集 · /, "") || "(无副标题)" }}</div>
                  <div class="ep-meta">
                    <span class="meta-pill meta-duration">
                      {{ ep.est_minutes.toFixed(1) }} 分钟
                    </span>
                    <span class="meta-pill meta-scenes">{{ ep.scene_count }} 场</span>
                    <span
                      v-if="ep.first_chapter !== null"
                      class="meta-pill meta-chapter"
                    >
                      第 {{ ep.first_chapter }}{{ ep.last_chapter !== ep.first_chapter ? `-${ep.last_chapter}` : "" }} 章
                    </span>
                    <span class="meta-pill meta-boundary">
                      切于 {{ BOUNDARY_LABEL[ep.boundary_reason] || ep.boundary_reason }}
                    </span>
                  </div>
                  <svg
                    class="ep-chev"
                    :class="{ open: expandedEp === ep.episode_number }"
                    width="14" height="14" viewBox="0 0 24 24" fill="none"
                    stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"
                  >
                    <polyline points="6 9 12 15 18 9" />
                  </svg>
                </div>

                <div v-if="expandedEp === ep.episode_number" class="ep-scenes">
                  <div class="es-title">场景列表</div>
                  <ol class="scene-list">
                    <li
                      v-for="sid in ep.scene_ids"
                      :key="sid"
                      class="scene-id mono"
                    >
                      {{ sid }}
                    </li>
                  </ol>
                </div>
              </li>
            </ul>

            <p v-else class="empty">无可分集的场景</p>
          </template>
          <div v-else class="state-msg state-msg--err">无可显示数据</div>
        </section>

        <!-- 底部备注 -->
        <footer class="epp-ftr">
          <span class="ftr-note">
            ⓘ MVP 规则版 — logline / cliffhanger 在 LLM 增强阶段添加。
            集尾边界优先选 章节 / 强转场 / 目标时长。
          </span>
        </footer>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.epp-overlay {
  position: fixed;
  inset: 0;
  background: rgba(20, 16, 12, 0.55);
  backdrop-filter: blur(5px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 200;
}
.epp-panel {
  background: var(--bg);
  border-radius: var(--radius-lg);
  width: 90vw;
  max-width: 960px;
  height: 88vh;
  display: flex;
  flex-direction: column;
  box-shadow: var(--shadow-lg);
  overflow: hidden;
  color: var(--text);
}

.epp-hdr {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding: 18px 24px 14px;
  border-bottom: 1px solid var(--border-soft);
}
.epp-hdr h2 {
  margin: 0 0 4px;
  font-size: 20px;
  font-weight: 500;
  color: var(--text-strong);
}
.epp-sub {
  margin: 0;
  font-size: 12px;
  color: var(--text-muted);
}
.epp-close-btn {
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
}
.epp-close-btn:hover {
  color: var(--text);
  background: var(--hover-bg);
}

/* 控制条 */
.ctrl-row {
  display: flex;
  gap: 16px;
  padding: 14px 24px;
  border-bottom: 1px solid var(--border-soft);
  align-items: flex-end;
}
.ctrl-slider-wrap {
  flex: 1;
}
.ctrl-label {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  font-size: 12px;
  color: var(--text-muted);
  margin-bottom: 6px;
}
.ctrl-value {
  font-family: var(--font-mono);
  color: var(--accent-text);
  font-weight: 600;
}
.ctrl-slider {
  width: 100%;
  accent-color: var(--accent);
}
.ctrl-presets {
  display: flex;
  gap: 6px;
  margin-top: 8px;
}
.preset-btn {
  font-size: 10.5px;
  padding: 2px 9px;
  border: 1px solid var(--border);
  background: var(--card-bg);
  color: var(--text-muted);
  border-radius: var(--radius-sm);
  cursor: pointer;
  letter-spacing: 0.04em;
}
.preset-btn:hover {
  background: var(--hover-bg);
  color: var(--text);
}
.preset-btn.active {
  background: var(--accent-soft);
  color: var(--accent-text);
  border-color: var(--accent);
}
.reload-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  background: var(--accent);
  color: white;
  border: none;
  border-radius: var(--radius-md);
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
}
.reload-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.epp-body {
  flex: 1;
  padding: 16px 24px;
  overflow-y: auto;
}

.overview {
  display: flex;
  gap: 8px;
  align-items: baseline;
  padding-bottom: 14px;
  font-size: 12px;
  color: var(--text-muted);
  border-bottom: 1px solid var(--border-soft);
  margin-bottom: 14px;
}
.ov-item strong {
  color: var(--text-strong);
  font-weight: 600;
  font-size: 13px;
}
.ov-sep {
  color: var(--border);
}
.ov-mode {
  margin-left: auto;
  font-style: italic;
}

/* episode list */
.ep-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ep-card {
  border: 1px solid var(--border-soft);
  border-radius: var(--radius-md);
  background: var(--card-bg);
  overflow: hidden;
  transition: border-color 150ms;
}
.ep-card:hover {
  border-color: var(--accent);
}
.ep-card.expanded {
  border-color: var(--accent);
  background: var(--accent-soft);
}
.ep-hdr {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  cursor: pointer;
}
.ep-num {
  font-size: 11.5px;
  color: var(--accent-text);
  font-weight: 600;
  letter-spacing: 0.06em;
  flex-shrink: 0;
}
.ep-title {
  font-size: 14px;
  color: var(--text-strong);
  font-weight: 500;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ep-meta {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}
.meta-pill {
  font-size: 10.5px;
  padding: 1px 7px;
  border-radius: var(--radius-sm);
  letter-spacing: 0.04em;
  background: var(--code-bg);
  color: var(--text-muted);
}
.meta-duration {
  color: var(--accent-text);
  background: var(--accent-soft);
  font-family: var(--font-mono);
}
.meta-boundary {
  font-style: italic;
}
.ep-chev {
  color: var(--text-muted);
  transition: transform 200ms;
}
.ep-chev.open {
  transform: rotate(180deg);
}

.ep-scenes {
  padding: 0 14px 12px;
  border-top: 1px solid var(--border-soft);
}
.es-title {
  font-size: 10.5px;
  font-weight: 600;
  color: var(--text-muted);
  letter-spacing: 0.08em;
  margin: 10px 0 6px;
}
.scene-list {
  margin: 0;
  padding: 0 0 0 24px;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(110px, 1fr));
  gap: 4px;
  list-style: decimal;
}
.scene-id {
  font-size: 11px;
  color: var(--text);
}

.mono {
  font-family: var(--font-mono);
}

.empty,
.state-msg {
  text-align: center;
  padding: 40px 0;
  color: var(--text-muted);
  font-style: italic;
}
.state-msg--err {
  color: var(--danger);
}

.epp-ftr {
  padding: 10px 24px;
  border-top: 1px solid var(--border-soft);
  background: var(--bg-deep);
}
.ftr-note {
  font-size: 11px;
  color: var(--text-muted);
  font-style: italic;
}
</style>
