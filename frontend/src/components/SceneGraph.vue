<script setup lang="ts">
/**
 * SceneGraph — Sprint 6.A2 M2(2026-05-18)场景图谱
 *
 * 产品意图(对齐用户拍板"M2 场景识别 + 角色亲疏地图"):
 *   - 展示 AI 从 build_graph LOCATION 实体聚合的项目场所
 *   - 每个场所:名字 + 出现 chunk 数 + 描述 + "常客"角色 chip(按共现 + 主角优先)
 *   - 顶部可折叠(列表长不撑屏);卡片点击展开看常客
 *   - M3 续写时此数据驱动 scene_picker — 现在 UI 只是"让用户感知"
 *
 * 适用 mode:仅 middle / cycle / end(初始态无 extract 数据 → 无 LOCATION 实体)
 *
 * 父组件:ProjectView(传 projectId)
 */
import { computed, ref, watch } from "vue";

import { api } from "../api/client";
import {
  ApiError,
  type MergeProjectScenesRequest,
  type ProjectScene,
  type SceneRegular,
  type SceneRegularsResponse,
  type CreateProjectSceneRequest,
  type UpdateProjectSceneRequest,
} from "../api/types";
import { toast } from "../composables/useToast";
import Icon from "./Icon.vue";

const props = withDefaults(
  defineProps<{
    projectId: string;
    // INIT.6(2026-05-21):初始态时显示"+ 新建场景"按钮(默认 false,extract 模式自动入库)
    allowCreate?: boolean;
  }>(),
  { allowCreate: false },
);

// UI 优化(2026-05-21 十一轮):手风琴联动 — 展开时 emit,父组件折叠另一个 section
const emit = defineEmits<{
  (e: "expanded"): void;
}>();

// UI 优化(2026-05-21 三轮):默认折叠 — 同 ProtagonistWall,进入页面时默认收起
const sectionCollapsed = ref(true);
const scenes = ref<ProjectScene[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);

// UI 优化(2026-05-21 十一轮):同时只能展开 1 张场景卡(手风琴)— 从 Set 改为单值
const expandedSceneName = ref<string | null>(null);
// 每个 scene 的"常客"懒加载缓存:scene_name → SceneRegular[]
const regularsCache = ref<Map<string, SceneRegular[]>>(new Map());
const loadingRegulars = ref<Set<string>>(new Set());

// UI 优化(2026-05-21 三轮):场景卡分页(每页 12 个)
const SCENE_PAGE_SIZE = 12;
const scenePage = ref(1);
const sceneTotalPages = computed(
  () => Math.max(1, Math.ceil(scenes.value.length / SCENE_PAGE_SIZE)),
);
const scenesPaged = computed(() => {
  const start = (scenePage.value - 1) * SCENE_PAGE_SIZE;
  return scenes.value.slice(start, start + SCENE_PAGE_SIZE);
});
watch(
  () => scenes.value.length,
  () => {
    if (scenePage.value > sceneTotalPages.value) {
      scenePage.value = sceneTotalPages.value;
    }
  },
);
function goToScenePage(p: number) {
  if (p < 1 || p > sceneTotalPages.value) return;
  scenePage.value = p;
  // 翻页时折叠展开的卡片
  expandedSceneName.value = null;
}

async function loadScenes() {
  loading.value = true;
  error.value = null;
  try {
    scenes.value = await api.get<ProjectScene[]>(
      `/projects/${props.projectId}/scenes`,
    );
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : "加载场景图谱失败";
    scenes.value = [];
  } finally {
    loading.value = false;
  }
}

async function loadRegulars(sceneName: string) {
  if (regularsCache.value.has(sceneName)) return;
  if (loadingRegulars.value.has(sceneName)) return;
  loadingRegulars.value.add(sceneName);
  loadingRegulars.value = new Set(loadingRegulars.value);   // trigger reactive
  try {
    const resp = await api.get<SceneRegularsResponse>(
      `/projects/${props.projectId}/scenes/${encodeURIComponent(sceneName)}/regulars?top_n=8`,
    );
    const m = new Map(regularsCache.value);
    m.set(sceneName, resp.regulars);
    regularsCache.value = m;
  } catch (e) {
    // Bug #4 修(2026-05-21):区分 404(真无常客)vs 网络错(应重试)
    //   - 404:写空数组 + 缓存(用户下次点同场景不再请求)
    //   - 其他错:**不缓存**(让下次重试),只记 warning
    if (e instanceof ApiError && e.status === 404) {
      const m = new Map(regularsCache.value);
      m.set(sceneName, []);
      regularsCache.value = m;
    } else {
      // 网络抖动 / 5xx → 不写缓存,下次自动重试
      if (import.meta.env.DEV) {
        console.warn(`loadRegulars failed for ${sceneName}, will retry on next click:`, e);
      }
    }
  } finally {
    loadingRegulars.value.delete(sceneName);
    loadingRegulars.value = new Set(loadingRegulars.value);
  }
}

// M8.C(2026-05-21):编辑 / 删除 / 合并
const editingSceneId = ref<string | null>(null);
const editDraft = ref<{ name: string; description: string; aliasesText: string }>({
  name: "", description: "", aliasesText: "",
});
const mergeFromSceneId = ref<string | null>(null);    // 合并源(选中此场景 → 准备合并)
const operating = ref(false);

function startEditScene(scene: ProjectScene) {
  editingSceneId.value = scene.id;
  editDraft.value = {
    name: scene.name,
    description: scene.description,
    aliasesText: scene.aliases.join("、"),    // 用顿号分隔便于编辑
  };
}

function cancelEditScene() {
  editingSceneId.value = null;
}

async function saveEditScene(sceneId: string) {
  if (operating.value) return;
  const aliases = editDraft.value.aliasesText
    .split(/[、,，;；\s]+/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
    .slice(0, 10);
  const body: UpdateProjectSceneRequest = {
    name: editDraft.value.name.trim(),
    description: editDraft.value.description.trim(),
    aliases,
  };
  operating.value = true;
  try {
    await api.patch<ProjectScene>(
      `/projects/${props.projectId}/scenes/${sceneId}`,
      body,
    );
    await loadScenes();
    editingSceneId.value = null;
    toast.success("已保存");
  } catch (e) {
    toast.error(e instanceof ApiError ? `保存失败:${e.message}` : "保存失败");
  } finally {
    operating.value = false;
  }
}

async function handleDeleteScene(scene: ProjectScene) {
  if (operating.value) return;
  // 隐晦提示:仅红色按钮 + 一行 toast 提示,无确认弹窗(对齐用户拍板"专业人很少犯错")
  operating.value = true;
  try {
    await api.delete(
      `/projects/${props.projectId}/scenes/${scene.id}`,
    );
    await loadScenes();
    toast.success(`已删除「${scene.name}」`);
  } catch (e) {
    toast.error(e instanceof ApiError ? `删除失败:${e.message}` : "删除失败");
  } finally {
    operating.value = false;
  }
}

/** 触发"准备合并"状态:点 ⊕ 按钮 → mark this scene as source → 提示用户选 target */
function startMerge(scene: ProjectScene) {
  if (mergeFromSceneId.value === scene.id) {
    // 再次点同一个 = 取消合并模式
    mergeFromSceneId.value = null;
    return;
  }
  mergeFromSceneId.value = scene.id;
  toast.info(`合并模式:点击其他场景作为合并目标(再次点击「${scene.name}」取消)`, 5000);
}

async function confirmMerge(targetScene: ProjectScene) {
  if (!mergeFromSceneId.value || operating.value) return;
  if (mergeFromSceneId.value === targetScene.id) {
    mergeFromSceneId.value = null;
    return;
  }
  const sourceId = mergeFromSceneId.value;
  const sourceScene = scenes.value.find((s) => s.id === sourceId);
  const body: MergeProjectScenesRequest = {
    source_scene_id: sourceId,
    target_scene_id: targetScene.id,
  };
  operating.value = true;
  try {
    await api.post<ProjectScene>(
      `/projects/${props.projectId}/scenes/merge`,
      body,
    );
    await loadScenes();
    toast.success(`已合并「${sourceScene?.name ?? "?"}」到「${targetScene.name}」`);
    mergeFromSceneId.value = null;
  } catch (e) {
    toast.error(e instanceof ApiError ? `合并失败:${e.message}` : "合并失败");
  } finally {
    operating.value = false;
  }
}

async function toggleSceneExpand(sceneName: string) {
  // UI 优化(2026-05-21 十一轮):同时只展开 1 张,点别的卡自动换
  if (expandedSceneName.value === sceneName) {
    expandedSceneName.value = null;
  } else {
    expandedSceneName.value = sceneName;
    await loadRegulars(sceneName);
  }
}

const isEmpty = computed(() => !loading.value && scenes.value.length === 0);

// INIT.6(2026-05-21):初始态新建场景 inline 表单
const newSceneName = ref<string>("");
const newSceneDesc = ref<string>("");
const creating = ref<boolean>(false);
async function createSceneInline() {
  const name = newSceneName.value.trim();
  if (!name || creating.value) return;
  creating.value = true;
  try {
    const body: CreateProjectSceneRequest = {
      name,
      description: newSceneDesc.value.trim(),
      aliases: [],
    };
    const created = await api.post<ProjectScene>(
      `/projects/${props.projectId}/scenes`,
      body,
    );
    // 加到顶部 + 重置表单
    scenes.value = [created, ...scenes.value];
    newSceneName.value = "";
    newSceneDesc.value = "";
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : "创建场景失败");
  } finally {
    creating.value = false;
  }
}

// Bug 修复(2026-05-22):同 ProtagonistWall — 改 watch immediate 让切项目稳健重 load
watch(() => props.projectId, loadScenes, { immediate: true });

/**
 * UI 优化(2026-05-21 十一轮)— 手风琴联动:
 *   - toggleSection 切换 sectionCollapsed,展开时 emit('expanded')
 *   - 父组件接收 expanded 事件后调另一个 section 的 collapseSection() 把它折叠
 */
function toggleSection() {
  sectionCollapsed.value = !sectionCollapsed.value;
  if (!sectionCollapsed.value) {
    emit("expanded");
  }
}
function collapseSection() {
  sectionCollapsed.value = true;
}
defineExpose({ collapseSection });
</script>

<template>
  <section class="scene-graph" :aria-busy="loading">
    <header class="sg-header">
      <button
        type="button"
        class="collapse-btn"
        :aria-expanded="!sectionCollapsed"
        :aria-label="sectionCollapsed ? '展开场景图谱' : '折叠场景图谱'"
        @click="toggleSection"
      >{{ sectionCollapsed ? "▸" : "▾" }}</button>

      <div class="sg-title-block">
        <h3 class="sg-title">
          <Icon name="scene" :size="18" class="title-icon" />
          场景图谱
          <span class="title-count mono" v-if="!loading">
            {{ scenes.length }}
          </span>
        </h3>
        <!-- UI 优化(2026-05-21 三轮):删除 sg-sub 说明文案 — 主界面状态卡已说明 -->

      </div>
    </header>

    <template v-if="!sectionCollapsed">
      <div v-if="loading" class="state">加载场景图谱中…</div>

      <div v-else-if="error" class="state state-error">
        {{ error }}
        <button class="ghost-btn" @click="loadScenes">重试</button>
      </div>

      <!--
        INIT.6 bugfix(2026-05-21):empty state 仅在非 allowCreate 模式显示。
        allowCreate 模式下,创建表单本身就是引导,不需要额外 empty 文案。
      -->
      <div v-else-if="!allowCreate && isEmpty" class="state state-empty">
        <p class="empty-title">还没识别出场所</p>
        <p class="empty-hint">
          抽完图谱后,AI 会从 LOCATION 实体里聚合出场所。
          若你刚抽完图谱仍看不到,可能 build_graph 输出未识别出地点 — 重抽一次再看。
        </p>
      </div>

      <!-- INIT.6(2026-05-21):初始态新建场景 inline 表单(永远显示在 allowCreate 模式)-->
      <div v-if="allowCreate && !loading && !error" class="scene-create-form">
        <input
          v-model="newSceneName"
          type="text"
          placeholder="场景名(如:破庙)"
          maxlength="30"
          class="scene-create-input"
          :disabled="creating"
          @keydown.enter.prevent="createSceneInline"
        />
        <input
          v-model="newSceneDesc"
          type="text"
          placeholder="描述(可选,如:三人深夜投宿之地)"
          maxlength="200"
          class="scene-create-input scene-create-input--desc"
          :disabled="creating"
          @keydown.enter.prevent="createSceneInline"
        />
        <button
          type="button"
          class="scene-create-btn"
          :disabled="creating || !newSceneName.trim()"
          @click="createSceneInline"
        >{{ creating ? "创建中…" : "+ 新建场景" }}</button>
      </div>

      <!--
        INIT.6 bugfix(2026-05-21):场景列表与创建表单原本互斥(v-else),
        导致 allowCreate 模式下永远看不到已存的场景列表。
        改为独立 v-if:有场景就显示列表,与创建表单并存。
      -->
      <ul v-if="!loading && !error && !isEmpty" class="scene-grid">
        <li
          v-for="scene in scenesPaged"
          :key="scene.id"
          class="scene-card"
          :class="{
            'is-expanded': expandedSceneName === scene.name,
            'is-merge-source': mergeFromSceneId === scene.id,
            'is-merge-target-candidate': mergeFromSceneId && mergeFromSceneId !== scene.id,
            'is-editing': editingSceneId === scene.id,
            'is-sequel': !!scene.origin_simulation_id,
          }"
        >
          <!-- M8.C 编辑态(全幕表单)-->
          <div v-if="editingSceneId === scene.id" class="scene-edit-form">
            <label class="scene-edit-label">
              场景名
              <input
                v-model="editDraft.name"
                type="text"
                maxlength="30"
                class="scene-edit-input"
                :disabled="operating"
              />
            </label>
            <label class="scene-edit-label">
              描述
              <textarea
                v-model="editDraft.description"
                maxlength="500"
                rows="2"
                class="scene-edit-input"
                :disabled="operating"
              />
            </label>
            <label class="scene-edit-label">
              别名(用顿号 / 逗号分隔,最多 10 个)
              <input
                v-model="editDraft.aliasesText"
                type="text"
                class="scene-edit-input"
                :disabled="operating"
              />
            </label>
            <div class="scene-edit-actions">
              <button
                type="button"
                class="ghost-btn ghost-btn--small"
                :disabled="operating"
                @click="cancelEditScene"
              >取消</button>
              <button
                type="button"
                class="primary-btn primary-btn--small"
                :disabled="operating"
                @click="saveEditScene(scene.id)"
              >{{ operating ? "保存中…" : "保存" }}</button>
            </div>
          </div>

          <!-- 折叠/展开态 -->
          <template v-else>
            <button
              type="button"
              class="scene-head"
              :aria-expanded="expandedSceneName === scene.name"
              @click="
                mergeFromSceneId && mergeFromSceneId !== scene.id
                  ? confirmMerge(scene)
                  : toggleSceneExpand(scene.name)
              "
            >
              <span class="scene-name">{{ scene.name }}</span>
              <!-- M8.C origin badge:续作生成的场景显紫色 chip -->
              <span
                v-if="scene.origin_simulation_id"
                class="origin-badge origin-badge--sequel"
                title="该场景由 AI 在续作中生成(M8.A 反向入库)"
              >续作生成</span>
              <!--
                FOCUS.12(2026-05-22):文案修正 — 老版"X 章"在语义上是虚的
                后端字段是 appearance_chunk_count(在 N 个文本切片 / chunk 中出现)
                chunk ≠ 章(默认 25000 字/块,小说一章可能跨多 chunk 或反之)
                改为"X 段"对齐真实语义。
                count=0:用户手动加(无原文 chunk 出场)→ 显"手动建"
              -->
              <span
                v-if="scene.appearance_chunk_count > 0"
                class="scene-count mono"
                :title="`在原作的 ${scene.appearance_chunk_count} 个文本段(每段 ~25000 字)中出现`"
              >{{ scene.appearance_chunk_count }} 段</span>
              <span
                v-else
                class="scene-count mono"
                title="用户手动新建,无原文出场段"
              >手动建</span>
              <span class="scene-expand-icon" aria-hidden="true">
                {{ expandedSceneName === scene.name ? "▾" : "▸" }}
              </span>
            </button>

            <!-- M8.C 操作按钮组(✎ 编辑 / ⊕ 合并 / × 删除) -->
            <div class="scene-card-actions">
              <button
                type="button"
                class="card-action-btn"
                :disabled="operating || mergeFromSceneId !== null"
                title="编辑名字 / 描述 / 别名"
                @click.stop="startEditScene(scene)"
              >✎ 编辑</button>
              <!--
                INIT.6 fix(2026-05-21):初始态(allowCreate=true)隐藏合并按钮 —
                合并是用于"AI 抽出多场景同义去重"场景,初始态用户自己手动建,不需要合并。
                中间/末尾态(allowCreate=false)保留合并能力。
              -->
              <button
                v-if="!allowCreate"
                type="button"
                class="card-action-btn"
                :class="{ 'card-action-btn--active': mergeFromSceneId === scene.id }"
                :disabled="operating || scenes.length < 2"
                :title="mergeFromSceneId === scene.id
                  ? '点击其他场景作为合并目标 / 再次点此取消'
                  : '把此场景合并到另一个场景(同义场景去重)'"
                @click.stop="startMerge(scene)"
              >⊕ 合并</button>
              <button
                type="button"
                class="card-action-btn card-action-btn--danger"
                :disabled="operating || mergeFromSceneId !== null"
                title="删除此场景"
                @click.stop="handleDeleteScene(scene)"
              >× 删除</button>
            </div>

            <p v-if="scene.description" class="scene-desc">
              {{ scene.description.length > 80
                ? scene.description.slice(0, 80) + "…"
                : scene.description }}
            </p>
            <p v-if="scene.aliases.length > 0" class="scene-aliases">
              <span class="aliases-label">别名:</span>
              {{ scene.aliases.join(" / ") }}
            </p>
          </template>

          <div v-if="expandedSceneName === scene.name" class="scene-expansion">
            <h5 class="exp-title">常客角色</h5>
            <div
              v-if="loadingRegulars.has(scene.name)"
              class="exp-loading"
            >加载中…</div>
            <div
              v-else-if="(regularsCache.get(scene.name) ?? []).length === 0"
              class="exp-empty"
            >无共现角色数据</div>
            <ul v-else class="regulars-list">
              <li
                v-for="reg in (regularsCache.get(scene.name) ?? [])"
                :key="reg.character_id"
                class="regular-chip"
                :class="{ 'is-protagonist': reg.is_protagonist }"
                :title="`和此场景在原作 ${reg.co_occurrence_count} 个文本段共现` + (reg.is_protagonist ? '(主角)' : '')"
              >
                <span v-if="reg.is_protagonist" aria-hidden="true">⭐</span>
                <span>{{ reg.character_name }}</span>
                <span class="reg-count mono">{{ reg.co_occurrence_count }}</span>
              </li>
            </ul>
          </div>
        </li>
      </ul>

      <!--
        UI 优化(2026-05-21 三轮):场景卡分页器 — 与 ProtagonistWall 样式一致
        仅在 ≥ 2 页时显示;边界按钮 disabled,当前页高亮
      -->
      <nav
        v-if="!isEmpty && sceneTotalPages > 1"
        class="sg-pager"
        aria-label="场景分页"
      >
        <button
          type="button"
          class="sg-pager-btn"
          :disabled="scenePage === 1"
          aria-label="上一页"
          @click="goToScenePage(scenePage - 1)"
        >‹</button>
        <button
          v-for="p in sceneTotalPages"
          :key="p"
          type="button"
          class="sg-pager-btn sg-pager-num"
          :class="{ 'is-current': p === scenePage }"
          :aria-current="p === scenePage ? 'page' : undefined"
          @click="goToScenePage(p)"
        >{{ p }}</button>
        <button
          type="button"
          class="sg-pager-btn"
          :disabled="scenePage === sceneTotalPages"
          aria-label="下一页"
          @click="goToScenePage(scenePage + 1)"
        >›</button>
      </nav>
    </template>
  </section>
</template>

<style scoped>
.scene-graph {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
}

.sg-header {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
}

.collapse-btn {
  flex-shrink: 0;
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  cursor: pointer;
  margin-top: 2px;
}
.collapse-btn:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}

.sg-title-block {
  flex: 1;
  min-width: 0;
}
.sg-title {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--color-text);
}
.title-icon { font-size: var(--text-lg); }
.title-count {
  padding: 2px 8px;
  font-size: var(--text-xs);
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-radius: var(--radius-sm);
  margin-left: 4px;
}
/* 代码屎山清理(2026-05-21):.sg-sub 已删 — sg-sub 文案已删 */


.state {
  padding: var(--space-5);
  text-align: center;
  color: var(--color-text-muted);
  font-size: var(--text-sm);
}
.state-error { color: var(--color-danger); }
.ghost-btn {
  margin-left: var(--space-2);
  padding: 2px 8px;
  font-size: var(--text-xs);
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.state-empty .empty-title {
  font-size: var(--text-md);
  color: var(--color-text);
  margin: 0 0 var(--space-2) 0;
}
.state-empty .empty-hint {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  line-height: 1.6;
  margin: 0;
  max-width: 480px;
  margin-left: auto;
  margin-right: auto;
}

/* ===== 场景卡片网格 ===== */
.scene-grid {
  list-style: none;
  padding: 0;
  margin: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: var(--space-2);
}

.scene-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: var(--space-2) var(--space-3);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  transition: border-color var(--duration-fast) var(--ease-out);
}
.scene-card.is-expanded {
  grid-column: 1 / -1;
  background: var(--color-surface);
  border-color: var(--color-accent);
}

/* M8.C(2026-05-21)合并模式视觉 */
.scene-card.is-merge-source {
  border-color: rgb(245, 158, 11);
  background: rgba(245, 158, 11, 0.06);
  box-shadow: 0 0 0 2px rgba(245, 158, 11, 0.2);
}
.scene-card.is-merge-target-candidate {
  cursor: copy;
  border-style: dashed;
}
.scene-card.is-merge-target-candidate:hover {
  border-color: rgb(245, 158, 11);
  background: rgba(245, 158, 11, 0.03);
}

/* M8.C 续作生成 badge */
.origin-badge {
  flex-shrink: 0;
  padding: 1px var(--space-2);
  font-size: var(--text-xs);
  border-radius: var(--radius-sm);
  white-space: nowrap;
  font-weight: 500;
}
.origin-badge--sequel {
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border: 1px solid var(--color-accent-border);
}
.scene-card.is-sequel {
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
}

/* M8.C 操作按钮组(右上角隐式 — 卡片 hover 时显)*/
.scene-card-actions {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
  margin-top: 2px;
  opacity: 0.45;
  transition: opacity var(--duration-fast) var(--ease-out);
}
.scene-card:hover .scene-card-actions {
  opacity: 1;
}
.card-action-btn {
  padding: 2px var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.card-action-btn:hover:not(:disabled) {
  color: var(--color-text);
  background: var(--color-bg-subtle);
  border-color: var(--color-border-strong);
}
.card-action-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.card-action-btn--active {
  color: rgb(245, 158, 11);
  background: rgba(245, 158, 11, 0.08);
  border-color: rgb(245, 158, 11);
}
.card-action-btn--danger {
  color: var(--color-danger);
  border-color: var(--color-danger-soft);
}
.card-action-btn--danger:hover:not(:disabled) {
  background: var(--color-danger-soft);
  color: var(--color-danger);
}

/* M8.C 编辑态表单 */
.scene-card.is-editing {
  background: var(--color-surface);
  border-color: var(--color-accent);
}
.scene-edit-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.scene-edit-label {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.scene-edit-input {
  width: 100%;
  padding: 4px var(--space-2);
  font-size: var(--text-sm);
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  font-family: inherit;
}
.scene-edit-input:focus {
  outline: none;
  border-color: var(--color-accent);
}
.scene-edit-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  margin-top: 2px;
}
.primary-btn--small {
  padding: 4px var(--space-3);
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border: 1px solid var(--color-accent);
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.primary-btn--small:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.scene-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  background: transparent;
  border: none;
  cursor: pointer;
  text-align: left;
  padding: 0;
  color: var(--color-text);
}
.scene-name {
  flex: 1;
  font-size: var(--text-sm);
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.scene-count {
  flex-shrink: 0;
  padding: 2px 6px;
  font-size: 10px;
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-radius: var(--radius-sm);
}
.scene-expand-icon {
  flex-shrink: 0;
  color: var(--color-text-muted);
  font-size: 11px;
}

.scene-desc {
  margin: 0;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  line-height: 1.5;
}
.scene-aliases {
  margin: 0;
  font-size: 10px;
  color: var(--color-text-subtle);
}
.aliases-label {
  color: var(--color-text-muted);
  margin-right: 4px;
}

/* ===== 卡片展开区 ===== */
.scene-expansion {
  margin-top: var(--space-2);
  padding-top: var(--space-2);
  border-top: 1px dashed var(--color-border);
}
.exp-title {
  margin: 0 0 6px 0;
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--color-text-muted);
}
.exp-loading, .exp-empty {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  font-style: italic;
}

.regulars-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.regular-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  font-size: var(--text-xs);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  color: var(--color-text);
}
.regular-chip.is-protagonist {
  background: var(--color-accent-soft);
  border-color: var(--color-accent);
  color: var(--color-accent-text);
  font-weight: 500;
}
.reg-count {
  font-size: 10px;
  color: var(--color-text-muted);
  margin-left: 2px;
}
.regular-chip.is-protagonist .reg-count {
  color: var(--color-accent-text);
}

/* INIT.6(2026-05-21):初始态新建场景表单 */
.scene-create-form {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-3);
  padding: var(--space-3);
  background: var(--color-bg-subtle);
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-md);
}
.scene-create-input {
  flex: 1;
  min-width: 0;
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-sm);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  color: var(--color-text);
  transition: border-color var(--duration-fast) var(--ease-out);
}
.scene-create-input--desc {
  flex: 2;
}
.scene-create-input:focus {
  outline: none;
  border-color: var(--color-accent-border);
}
.scene-create-btn {
  flex-shrink: 0;
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-sm);
  font-weight: 500;
  color: white;
  background: var(--color-accent);
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: opacity var(--duration-fast) var(--ease-out);
}
.scene-create-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.scene-create-btn:hover:not(:disabled) {
  background: var(--color-accent-hover);
}

/* ========== UI 优化(2026-05-21 三轮):场景分页器 — 与主角分页器同款 ========== */
.sg-pager {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 4px;
  margin-top: var(--space-4);
  padding: 4px 0;
}
.sg-pager-btn {
  min-width: 26px;
  height: 26px;
  padding: 0 6px;
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--color-text-muted);
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  line-height: 1;
}
.sg-pager-btn:hover:not(:disabled):not(.is-current) {
  background: var(--color-surface-hover);
  color: var(--color-text);
  border-color: var(--color-border-strong);
}
.sg-pager-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}
.sg-pager-btn.is-current {
  background: var(--color-accent);
  color: white;
  border-color: var(--color-accent);
  font-weight: 600;
  cursor: default;
}
.sg-pager-num {
  font-family: var(--font-mono, monospace);
}
</style>
