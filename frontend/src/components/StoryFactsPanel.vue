<script setup lang="ts">
/**
 * StoryFactsPanel — SP-3(2026-05-28).
 *
 * 知识边界:录入项目级事实 + 标记每个角色"知道 / 不知道".
 * 治 AI 续作最大 bug — 角色用了他不该知道的信息.
 *
 * Props:
 *   projectId   string
 *   characters  Character[]   项目角色全集(用于"标 known" 多选)
 *
 * 行为:
 *   - 加载时 GET /projects/{pid}/story_facts?include_knowledge=true 拉所有 fact + known_by
 *   - 顶部 "+ 录入事实" 折叠 form:description / first_revealed_scene / is_sensitive
 *   - 每条 fact 卡片:展开后列项目角色,toggle 切换 known
 *   - 删 fact 走 useConfirm(项目铁律,禁原生 confirm)
 */
import { onMounted, ref, watch } from "vue";

import { api, ApiError } from "../api/client";
import type {
  Character,
  CreateStoryFactRequest,
  StoryFact,
  StoryFactsResponse,
} from "../api/types";
import { confirm } from "../composables/useConfirm";
import { toast } from "../composables/useToast";
import Icon from "./Icon.vue";

const props = defineProps<{
  projectId: string;
  characters: Character[];
  /** hotfix(2026-06-01):嵌入到外层 wrapper(已有标题/描述)时,隐藏 panel 自己的标题/描述
   *  避免重复 — header-actions(覆盖现有 / AI 推断 / 录入事实) 仍保留 */
  embedded?: boolean;
}>();

const facts = ref<StoryFact[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);

// 新事实 form state
const showCreateForm = ref(false);
const newDesc = ref("");
const newFirstScene = ref<string>("");  // 字符串便于空 input,提交时 parse
const newIsSensitive = ref(false);
const creating = ref(false);

// 哪条 fact 展开了"角色 known 矩阵"
const expandedFactId = ref<string | null>(null);

// SP-3.1(2026-05-29):AI 推断知识边界
const inferring = ref(false);
const inferOverwrite = ref(false);

async function inferKnowledgeBoundaries() {
  if (inferring.value) return;
  // hotfix(2026-06-01):
  // - 不勾"覆盖现有" = 增量追加(默认):在原有事实基础上加新的,去重
  // - 勾"覆盖现有" = 清空重建:用户主动选,会丢已有内容,需要二次确认
  if (inferOverwrite.value && facts.value.length > 0) {
    const ok = await confirm({
      title: "确认覆盖现有事实",
      message: `当前已有 ${facts.value.length} 条事实.\n继续会清空全部已有事实并重新生成.\n确定要这么做吗?`,
      danger: true,
      confirmLabel: "清空并重建",
    });
    if (!ok) return;
  }
  inferring.value = true;
  try {
    const resp = await api.post<{
      applied: boolean;
      facts_created: number;
      knowledge_created: number;
      skipped_reason: string | null;
      reasoning: string;
      raw_inferred_facts_count: number;
      raw_inferred_knowledge_count: number;
    }>(
      `/projects/${props.projectId}/infer/knowledge_boundaries?overwrite=${inferOverwrite.value}`,
      {},
    );
    const r = resp.reasoning;
    if (!resp.applied) {
      toast.warning(
        resp.skipped_reason || r || "AI 未推断出任何新事实",
        9000,
      );
    } else if (inferOverwrite.value) {
      toast.success(
        `已清空并重建 ✓ 新生成 ${resp.facts_created} 条事实 / ${resp.knowledge_created} 条角色认知` +
        (r ? `\n${r.slice(0, 180)}${r.length > 180 ? "…" : ""}` : ""),
        9000,
      );
    } else {
      toast.success(
        `已追加 ✓ 新增 ${resp.facts_created} 条事实 / ${resp.knowledge_created} 条角色认知(原有内容保留)` +
        (r ? `\n${r.slice(0, 180)}${r.length > 180 ? "…" : ""}` : ""),
        9000,
      );
    }
    await load();
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : "AI 推断失败");
  } finally {
    inferring.value = false;
  }
}

async function load() {
  if (!props.projectId) return;
  loading.value = true;
  error.value = null;
  try {
    const resp = await api.get<StoryFactsResponse>(
      `/projects/${props.projectId}/story_facts?include_knowledge=true`,
    );
    facts.value = resp.facts;
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : "加载事实失败";
  } finally {
    loading.value = false;
  }
}

onMounted(load);
watch(() => props.projectId, load);

// 2026-06-02 cleanup:characterMap 未使用

async function createFact() {
  const desc = newDesc.value.trim();
  if (!desc || creating.value) return;
  creating.value = true;
  try {
    const body: CreateStoryFactRequest = {
      description: desc,
      is_sensitive: newIsSensitive.value,
    };
    const sceneN = newFirstScene.value.trim();
    if (sceneN) {
      const n = parseInt(sceneN, 10);
      if (Number.isFinite(n) && n >= 0) body.first_revealed_scene = n;
    }
    await api.post<StoryFact>(
      `/projects/${props.projectId}/story_facts`,
      body,
    );
    // 重置 form
    newDesc.value = "";
    newFirstScene.value = "";
    newIsSensitive.value = false;
    showCreateForm.value = false;
    toast.success("事实已录入");
    await load();
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : "录入失败");
  } finally {
    creating.value = false;
  }
}

async function deleteFact(f: StoryFact) {
  const ok = await confirm({
    title: "删除事实",
    message: `确认删除 "${f.description.slice(0, 40)}${f.description.length > 40 ? "…" : ""}"?\n所有已标"知道此事实"的角色记录也会清除.`,
    danger: true,
  });
  if (!ok) return;
  try {
    await api.delete(`/story_facts/${f.id}`);
    toast.success("已删除");
    await load();
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : "删除失败");
  }
}

/** 该角色对该事实的当前状态:null 不知道 / Knowledge entry 已知 */
function knownEntryFor(f: StoryFact, charId: string) {
  return (f.known_by ?? []).find((k) => k.character_id === charId) ?? null;
}

async function toggleKnown(f: StoryFact, charId: string) {
  const cur = knownEntryFor(f, charId);
  try {
    if (cur) {
      // 已 known → 撤销
      await api.delete(`/story_facts/${f.id}/known_by/${charId}`);
    } else {
      // 未 known → 标 confirmed,known_since_scene 取 first_revealed_scene 兜底
      await api.post(`/story_facts/${f.id}/known_by/${charId}`, {
        known_since_scene: f.first_revealed_scene,
        confidence: "confirmed",
      });
    }
    await load();
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : "切换失败");
  }
}

function toggleExpand(fid: string) {
  expandedFactId.value = expandedFactId.value === fid ? null : fid;
}

defineExpose({ reload: load });
</script>

<template>
  <section class="story-facts-panel">
    <header class="panel-header" :class="{ 'panel-header--embedded': props.embedded }">
      <div v-if="!props.embedded" class="header-title">
        <Icon name="book" :size="16" class="header-icon" />
        <h3 class="title">知识边界</h3>
      </div>
      <p v-if="!props.embedded" class="subtitle">
        谁知道哪条事实.续作 AI 用这表防"渡边在第 1 章就知道直子已死"类穿帮 bug.
      </p>
      <div class="header-actions">
        <label
          v-if="facts.length > 0"
          class="infer-overwrite-label"
          :title="`勾选 = 清空现有 ${facts.length} 条事实后全部重新生成;\n不勾(默认) = 在现有基础上追加新的(去重)`"
        >
          <input
            v-model="inferOverwrite"
            type="checkbox"
            :disabled="inferring"
          />
          <span>清空重建</span>
        </label>
        <button
          class="infer-btn"
          :disabled="inferring"
          :title="`让 AI 推断关键事实清单 + 每个角色 known 矩阵\n(初始态基于你填的事件/关系/角色;中末尾态基于已上传原作)`"
          @click="inferKnowledgeBoundaries"
        >
          <Icon name="sparkles" :size="14" />
          <span>{{ inferring ? "AI 推断中…" : "AI 推断" }}</span>
        </button>
        <button
          class="create-toggle"
          :class="{ active: showCreateForm }"
          @click="showCreateForm = !showCreateForm"
        >
          <Icon name="plus" :size="14" />
          <span>{{ showCreateForm ? "取消" : "录入事实" }}</span>
        </button>
      </div>
    </header>

    <!-- 新事实 form -->
    <transition name="fade-fast">
      <div v-if="showCreateForm" class="create-form">
        <label class="form-label">
          事实描述
          <textarea
            v-model="newDesc"
            class="form-textarea"
            rows="2"
            maxlength="500"
            placeholder="例:直子已经自杀"
            :disabled="creating"
            @keydown.enter.exact.prevent="createFact"
          />
        </label>
        <div class="form-row">
          <label class="form-label form-label-small">
            首次揭示幕(可选)
            <input
              v-model="newFirstScene"
              type="number"
              min="0"
              max="999"
              class="form-input"
              :disabled="creating"
            />
          </label>
          <label class="form-checkbox">
            <input v-model="newIsSensitive" type="checkbox" :disabled="creating" />
            敏感事实(谁知道格外重要)
          </label>
        </div>
        <div class="form-actions">
          <button
            class="primary-btn"
            :disabled="!newDesc.trim() || creating"
            @click="createFact"
          >{{ creating ? "录入中…" : "保存事实" }}</button>
        </div>
      </div>
    </transition>

    <div v-if="error" class="err">{{ error }}</div>
    <div v-if="loading && facts.length === 0" class="empty">加载中…</div>

    <ul v-if="facts.length > 0" class="fact-list">
      <li
        v-for="f in facts"
        :key="f.id"
        class="fact-card"
        :class="{ expanded: expandedFactId === f.id, sensitive: f.is_sensitive }"
      >
        <div class="fact-row" @click="toggleExpand(f.id)">
          <span class="fact-desc">{{ f.description }}</span>
          <span v-if="f.first_revealed_scene !== null" class="fact-scene">
            第 {{ f.first_revealed_scene + 1 }} 幕揭示
          </span>
          <span v-if="f.is_sensitive" class="sensitive-tag">敏感</span>
          <span class="known-count">
            <Icon name="key" :size="12" />
            {{ (f.known_by ?? []).length }} / {{ characters.length }}
          </span>
          <button
            class="del-btn"
            :title="`删除事实`"
            @click.stop="deleteFact(f)"
          >
            <Icon name="trash" :size="13" />
          </button>
        </div>

        <transition name="expand">
          <div v-if="expandedFactId === f.id" class="char-matrix">
            <p class="matrix-hint">点击切换:谁知道这条事实</p>
            <div class="char-grid">
              <button
                v-for="c in characters"
                :key="c.id"
                class="char-chip"
                :class="{ known: knownEntryFor(f, c.id) !== null }"
                :title="
                  knownEntryFor(f, c.id)
                    ? `知道(第 ${(knownEntryFor(f, c.id)!.known_since_scene ?? 0) + 1} 幕起)`
                    : `不知道`
                "
                @click="toggleKnown(f, c.id)"
              >
                <span class="chip-name">{{ c.name }}</span>
                <Icon
                  v-if="knownEntryFor(f, c.id) !== null"
                  name="key"
                  :size="11"
                  class="chip-icon"
                />
              </button>
            </div>
          </div>
        </transition>
      </li>
    </ul>

    <div v-else-if="!loading" class="empty">
      还没有录入任何事实.点上方"录入事实"开始建知识边界库.
    </div>
  </section>
</template>

<style scoped>
.story-facts-panel {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-5) var(--space-6);
  margin-bottom: var(--space-5);
}

.panel-header {
  position: relative;
  margin-bottom: var(--space-4);
}
.header-title {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-1);
}
.header-icon { color: var(--color-text-muted); }
.title {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--color-text);
  margin: 0;
}
.subtitle {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  margin: 0 0 var(--space-3);
  line-height: 1.6;
}
.header-actions {
  position: absolute;
  top: 0;
  right: 0;
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}
/* hotfix(2026-06-01):embedded 模式下,panel header 无 title/subtitle,
   actions 从 absolute 改为 inline,放在 panel 顶部一行 */
.panel-header--embedded {
  margin-bottom: var(--space-3);
}
.panel-header--embedded .header-actions {
  position: static;
  justify-content: flex-end;
}
.create-toggle {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  cursor: pointer;
}
.create-toggle:hover {
  color: var(--color-text);
  background: var(--color-surface-hover);
}
.create-toggle.active {
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-color: var(--color-accent-border);
}

/* SP-3.1(2026-05-29):AI 推断按钮 */
.infer-btn {
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
.infer-btn:hover:not(:disabled) {
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border-color: var(--color-accent);
}
.infer-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.infer-overwrite-label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  cursor: pointer;
  user-select: none;
  padding: 0 var(--space-1);
}
.infer-overwrite-label input { margin: 0; }

.create-form {
  background: var(--color-surface-sunken);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: var(--space-3);
  margin-bottom: var(--space-3);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.form-label {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.form-label-small { font-size: var(--text-xs); }
.form-row {
  display: flex;
  gap: var(--space-4);
  align-items: flex-start;
}
.form-checkbox {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  font-size: var(--text-sm);
  color: var(--color-text-muted);
}
.form-textarea {
  width: 100%;
  padding: var(--space-2);
  font-size: var(--text-sm);
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  outline: none;
  resize: vertical;
}
.form-textarea:focus { border-color: var(--color-accent); }
.form-input {
  width: 80px;
  padding: var(--space-1) var(--space-2);
  font-size: var(--text-sm);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
}
.form-actions {
  display: flex;
  justify-content: flex-end;
}
.primary-btn {
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-sm);
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border: 0;
  border-radius: var(--radius-sm);
}
.primary-btn:hover:not(:disabled) { background: var(--color-accent-hover); }
.primary-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.err {
  color: var(--color-danger);
  background: var(--color-danger-soft);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
}
.empty {
  text-align: center;
  padding: var(--space-6);
  color: var(--color-text-subtle);
  font-size: var(--text-sm);
}

.fact-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.fact-card {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  margin-bottom: var(--space-2);
  background: var(--color-surface);
  transition: border-color var(--duration-fast) var(--ease-out);
}
.fact-card.sensitive {
  background: rgba(217, 119, 6, 0.03);
}
.fact-card.expanded {
  border-color: var(--color-accent-border);
}

.fact-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3);
}
.fact-desc {
  flex: 1;
  font-size: var(--text-sm);
  color: var(--color-text);
}
.fact-scene,
.sensitive-tag,
.known-count {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  flex-shrink: 0;
}
.sensitive-tag {
  color: #92400e;
  background: #fef3c7;
  padding: 1px var(--space-2);
  border-radius: var(--radius-sm);
}
.known-count {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  font-variant-numeric: tabular-nums;
}
.del-btn {
  width: 26px;
  height: 26px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: 0;
  border-radius: var(--radius-sm);
  color: var(--color-text-subtle);
}
.del-btn:hover {
  color: var(--color-danger);
  background: var(--color-surface-hover);
}

.char-matrix {
  padding: 0 var(--space-3) var(--space-3);
  border-top: 1px dashed var(--color-border);
}
.matrix-hint {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  margin: var(--space-2) 0;
}
.char-grid {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}
.char-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: var(--space-1) var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-full);
  transition: all var(--duration-fast) var(--ease-out);
}
.char-chip:hover {
  color: var(--color-text);
  background: var(--color-surface-hover);
}
.char-chip.known {
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-color: var(--color-accent-border);
}
.chip-icon { opacity: 0.8; }

/* 折叠展开过渡 */
.expand-enter-active,
.expand-leave-active {
  transition: opacity var(--duration-fast) var(--ease-out);
}
.expand-enter-from,
.expand-leave-to { opacity: 0; }

.fade-fast-enter-active,
.fade-fast-leave-active {
  transition: opacity var(--duration-fast) var(--ease-out);
}
.fade-fast-enter-from,
.fade-fast-leave-to { opacity: 0; }
</style>
