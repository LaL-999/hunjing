<script setup lang="ts">
/**
 * RelationshipsTimelineSection — Sprint 6.A2 M1+(2026-05-18)
 *
 * 产品意图(修补 M1 落地时的可用性遗漏):
 *   - 用户在 CharacterFocusModal 点"🕒 加 phase 保留演化"后,toast 提示成功但
 *     用户找不到时间线在哪里看 / 改 / 删 → 这个 section 就是入口
 *   - 列出项目所有 relationships(每条显:角色 A → 角色 B + 当前关系类型 + 阶段数)
 *   - 点卡片展开:挂载 RelationshipTimeline 组件,可加阶段 / 切 current / 删
 *
 * 适用 mode:全 3 态(初始/中间/末尾)— 关系演化机制 3 态通用(对齐 6.A1 决策)
 *
 * 父组件:ProjectView(传 projectId)
 */
import { computed, ref, watch } from "vue";

import { api } from "../api/client";
import {
  ApiError,
  type Character,
  type Relationship,
  type RelationshipPolarity,
} from "../api/types";
import { toast } from "../composables/useToast";
import RelationshipTimeline from "./RelationshipTimeline.vue";

const props = defineProps<{
  projectId: string;
}>();

const characters = ref<Character[]>([]);
const relationships = ref<Relationship[]>([]);
const phaseCounts = ref<Map<string, number>>(new Map());
const loading = ref(false);
const error = ref<string | null>(null);
const expandedId = ref<string | null>(null);

const charById = computed(() => {
  const m = new Map<string, Character>();
  for (const c of characters.value) m.set(c.id, c);
  return m;
});

async function loadAll() {
  loading.value = true;
  error.value = null;
  try {
    const [chars, rels] = await Promise.all([
      api.get<Character[]>(`/projects/${props.projectId}/characters`),
      api.get<Relationship[]>(`/projects/${props.projectId}/relationships`),
    ]);
    characters.value = chars;
    relationships.value = rels;
    // 拉每条关系的 phases 数(并发,失败的关系默认 1 — 假定有 phase[0])
    const counts = new Map<string, number>();
    await Promise.all(
      rels.map(async (r) => {
        try {
          const phases = await api.get<unknown[]>(
            `/relationships/${r.id}/phases`,
          );
          counts.set(r.id, phases.length);
        } catch {
          counts.set(r.id, 0);
        }
      }),
    );
    phaseCounts.value = counts;
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : "加载关系列表失败";
  } finally {
    loading.value = false;
  }
}

function toggleExpand(relId: string) {
  expandedId.value = expandedId.value === relId ? null : relId;
}

function onPhasesChanged(relId: string) {
  // phase 增删改后重新拉(更新计数 + current_phase_id)
  void loadAll();
  expandedId.value = relId;   // 保持展开同一条
}

// SP-7(2026-05-28):polarity 循环切换 null → positive → negative → neutral → null
// 用 dropdown 太重,4 状态循环最快;chip 文字 + 色块组合
function polarityLabel(p: RelationshipPolarity | null | undefined): string {
  if (p === "positive") return "正面";
  if (p === "negative") return "对立";
  if (p === "neutral") return "中性";
  return "未标";
}
function polarityCycle(
  cur: RelationshipPolarity | null | undefined,
): RelationshipPolarity | null {
  if (cur === null || cur === undefined) return "positive";
  if (cur === "positive") return "negative";
  if (cur === "negative") return "neutral";
  return null;  // neutral → 清空
}
const polaritySaving = ref<Set<string>>(new Set());
async function togglePolarity(rel: Relationship, e: MouseEvent) {
  e.stopPropagation();   // 防触发 rel-header 的 expand toggle
  if (polaritySaving.value.has(rel.id)) return;
  polaritySaving.value.add(rel.id);
  const next = polarityCycle(rel.polarity);
  try {
    await api.patch<Relationship>(`/relationships/${rel.id}`, {
      polarity: next,
    });
    // 本地更新避免 reload 闪
    rel.polarity = next;
  } catch (err) {
    toast.error(err instanceof ApiError ? err.message : "切换极性失败");
  } finally {
    polaritySaving.value.delete(rel.id);
  }
}

// SP-7.1(2026-05-30):AI 一次性推断所有关系 polarity
const polarityInferring = ref(false);
const polarityOverwrite = ref(false);
async function inferAllPolarity() {
  if (polarityInferring.value) return;
  polarityInferring.value = true;
  try {
    const resp = await api.post<{
      applied: boolean;
      updated_count: number;
      skipped_count: number;
      no_match_count: number;
      reasoning: string;
      raw_decisions_count: number;
    }>(
      `/projects/${props.projectId}/infer/relationship_polarity?overwrite=${polarityOverwrite.value}`,
      {},
    );
    const r = resp.reasoning;
    if (resp.updated_count === 0 && resp.skipped_count === 0) {
      toast.warning(
        `AI 未能成功推断 polarity${resp.no_match_count > 0 ? `(${resp.no_match_count} 条无法匹配)` : ""}`,
        7000,
      );
    } else {
      toast.success(
        `AI 推断完成 ✓ 更新 ${resp.updated_count} 条 / 跳过 ${resp.skipped_count} 条` +
        (r ? `\n${r.slice(0, 180)}${r.length > 180 ? "…" : ""}` : ""),
        9000,
      );
      await loadAll();
    }
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : "AI 推断失败");
  } finally {
    polarityInferring.value = false;
  }
}

const isEmpty = computed(
  () => !loading.value && relationships.value.length === 0,
);

// Bug 修复(2026-05-22):同 SimulationsListPanel — 改 watch projectId immediate 让切项目重 load
watch(() => props.projectId, loadAll, { immediate: true });
</script>

<template>
  <section class="rels-tl-section" :aria-busy="loading">
    <header class="sec-header">
      <div class="sec-header-row">
        <h3 class="sec-title">
          <span class="sec-icon" aria-hidden="true">🕒</span>
          关系时间轴
          <span v-if="!loading" class="sec-count mono">
            {{ relationships.length }} 条关系
          </span>
        </h3>
        <!-- SP-7.1(2026-05-30):AI 一键推断所有 polarity -->
        <div v-if="relationships.length > 0" class="sec-actions">
          <label class="polarity-overwrite-label" :title="`勾选后覆盖你手动 chip 切过的极性`">
            <input
              v-model="polarityOverwrite"
              type="checkbox"
              :disabled="polarityInferring"
            />
            <span>覆盖已填</span>
          </label>
          <button
            type="button"
            class="polarity-infer-btn"
            :disabled="polarityInferring"
            :title="`AI 一次推断所有关系的正负极性\n(初始态用 type+description;中末尾态用原作互动)`"
            @click="inferAllPolarity"
          >
            <span class="infer-icon">✨</span>
            <span>{{ polarityInferring ? "AI 推断中…" : "AI 推断 polarity" }}</span>
          </button>
        </div>
      </div>
      <p class="sec-sub">
        每条关系可有多个阶段(暗恋→情侣→仇敌)。续写时 AI 按"当前阶段"扮演 agent —
        反事实 / 角色对焦演化提示加的 phase 也在这里查看 / 调整。
      </p>
    </header>

    <!-- Loading -->
    <div v-if="loading" class="state">加载中…</div>

    <!-- Error -->
    <div v-else-if="error" class="state state-error">
      {{ error }}
      <button class="ghost-btn" @click="loadAll">重试</button>
    </div>

    <!-- Empty -->
    <div v-else-if="isEmpty" class="state state-empty">
      <p>项目还没有关系(关系卡片在「关系」section 创建)。</p>
    </div>

    <!-- 关系列表 -->
    <ul v-else class="rel-list">
      <li
        v-for="rel in relationships"
        :key="rel.id"
        class="rel-item"
        :class="{ 'is-expanded': expandedId === rel.id }"
      >
        <button
          type="button"
          class="rel-header"
          :aria-expanded="expandedId === rel.id"
          @click="toggleExpand(rel.id)"
        >
          <span class="rel-pair">
            <span class="rel-char">
              {{ charById.get(rel.source_id)?.name ?? "?" }}
            </span>
            <span class="rel-arrow" aria-hidden="true">→</span>
            <span class="rel-char">
              {{ charById.get(rel.target_id)?.name ?? "?" }}
            </span>
          </span>
          <span class="rel-type-chip">{{ rel.type }}</span>
          <!-- SP-7(2026-05-28):极性 chip,点击循环切换 -->
          <button
            type="button"
            class="rel-polarity-chip"
            :class="[`polarity-${rel.polarity ?? 'unset'}`]"
            :disabled="polaritySaving.has(rel.id)"
            :title="`点击切换:${polarityLabel(rel.polarity)} → ${polarityLabel(polarityCycle(rel.polarity))}`"
            @click="togglePolarity(rel, $event)"
          >{{ polarityLabel(rel.polarity) }}</button>
          <span class="rel-phase-count mono">
            {{ phaseCounts.get(rel.id) ?? "?" }} 阶段
          </span>
          <span class="expand-icon" aria-hidden="true">
            {{ expandedId === rel.id ? "▾" : "▸" }}
          </span>
        </button>

        <!-- 展开 → 时间轴组件 -->
        <div v-if="expandedId === rel.id" class="rel-expansion">
          <RelationshipTimeline
            :relationship-id="rel.id"
            :current-phase-id="rel.current_phase_id"
            @phases-changed="onPhasesChanged(rel.id)"
          />
        </div>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.rels-tl-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
}

.sec-header {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.sec-header-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
}
.sec-actions {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
}
.polarity-overwrite-label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  cursor: pointer;
  user-select: none;
}
.polarity-overwrite-label input { margin: 0; }
.polarity-infer-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.polarity-infer-btn:hover:not(:disabled) {
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border-color: var(--color-accent);
}
.polarity-infer-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.infer-icon { font-size: var(--text-sm); line-height: 1; }
.sec-title {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--color-text);
}
.sec-icon { font-size: var(--text-md); }
.sec-count {
  padding: 2px 8px;
  font-size: var(--text-xs);
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-radius: var(--radius-sm);
  margin-left: 4px;
}
.sec-sub {
  margin: 0;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  line-height: 1.6;
}

.state {
  padding: var(--space-4);
  text-align: center;
  color: var(--color-text-muted);
  font-size: var(--text-sm);
}
.state-error { color: var(--color-danger); }
.state-empty { color: var(--color-text-subtle); }

.rel-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.rel-item {
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  overflow: hidden;
  transition: border-color var(--duration-fast) var(--ease-out);
}
.rel-item.is-expanded {
  border-color: var(--color-accent);
}

.rel-header {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  padding: var(--space-2) var(--space-3);
  background: transparent;
  border: none;
  cursor: pointer;
  text-align: left;
  font-size: var(--text-sm);
  color: var(--color-text);
  transition: background var(--duration-fast) var(--ease-out);
}
.rel-header:hover {
  background: var(--color-surface-hover);
}

.rel-pair {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 500;
  min-width: 0;
  flex: 1;
}
.rel-char {
  color: var(--color-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 120px;
}
.rel-arrow {
  color: var(--color-text-subtle);
  flex-shrink: 0;
}

.rel-type-chip {
  flex-shrink: 0;
  padding: 2px 8px;
  font-size: var(--text-xs);
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-radius: var(--radius-sm);
}

/* SP-7(2026-05-28):极性 chip 4 态色 */
.rel-polarity-chip {
  flex-shrink: 0;
  padding: 2px 8px;
  font-size: var(--text-xs);
  font-weight: 500;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: opacity var(--duration-fast) var(--ease-out);
}
.rel-polarity-chip:hover { opacity: 0.85; }
.rel-polarity-chip:disabled { opacity: 0.5; cursor: not-allowed; }
.polarity-unset {
  color: var(--color-text-subtle);
  background: var(--color-bg-subtle);
  border-color: var(--color-border);
}
.polarity-positive {
  color: #15803d;
  background: #dcfce7;
  border-color: #86efac;
}
.polarity-negative {
  color: #b91c1c;
  background: #fee2e2;
  border-color: #fca5a5;
}
.polarity-neutral {
  color: var(--color-text-muted);
  background: var(--color-surface-sunken);
  border-color: var(--color-border-strong);
}

.rel-phase-count {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--color-text-muted);
}

.expand-icon {
  flex-shrink: 0;
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

.rel-expansion {
  padding: var(--space-3);
  background: var(--color-surface);
  border-top: 1px solid var(--color-border);
}

.ghost-btn {
  margin-left: var(--space-2);
  padding: 2px 8px;
  font-size: var(--text-xs);
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
}
</style>
