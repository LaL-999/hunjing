/**
 * useCounterfactuals — 反事实变量 + 重塑度三维度状态(Sprint 2.C / 2.C+)。
 *
 * 数据流:
 *   1. ProjectGraphView mount 时 reload() → GET /projects/{id}/counterfactuals
 *   2. 用户在 NodeEditDrawer 改字段保存 → PATCH 自动 record → 父调 reload()
 *   3. 用户拖 ReshapeSlider → debounce 300ms → previewAt(percent) →
 *      GET /projects/{id}/reshape_preview → 三维度 + affected ids 实时刷新
 *   4. 用户在 CounterfactualWorkbench 点撤销 → revert(id) → reload() + 提示父刷 graph
 *
 * 给跨组件共享一个 project 的反事实状态用,所以 instance 内状态由 projectId 锁定。
 * 不同 projectId 调 .bind(newId) 会清空重拉。
 */
import { computed, ref } from "vue";

import { api } from "../api/client";
import {
  ApiError,
  type CounterfactualChange,
  type CounterfactualOverview,
  type CreateCounterfactualRequest,
  type CreateWorldCounterfactualRequest,
  type ReshapePreview,
  type RevertCounterfactualResult,
} from "../api/types";

export function useCounterfactuals(initialProjectId?: string) {
  const projectId = ref<string | null>(initialProjectId ?? null);

  const overview = ref<CounterfactualOverview | null>(null);
  const loading = ref(false);
  const errorMessage = ref<string | null>(null);

  const preview = ref<ReshapePreview | null>(null);
  const previewLoading = ref(false);

  // 2.C+ "本次推演用哪些反事实"勾选 state
  // null = 用全部 active(默认);Set = 用户显式选的子集
  const selectedIds = ref<Set<string> | null>(null);

  // 滑块拖动会发多请求 — 用 debounce 节流;不用 AbortController 是因为 api.client
  // 当前不暴露 signal 参数,即便 abort 也不会取消 fetch(只是丢弃结果)。debounce 300ms
  // 已能把"快速拖滑块"压成一次请求;真有需要再扩 api 加 signal 支持。
  let previewDebounceTimer: ReturnType<typeof setTimeout> | null = null;

  // ============================================================
  // 公开派生
  // ============================================================

  const totalActive = computed(() => overview.value?.total_active ?? 0);
  const items = computed<CounterfactualChange[]>(() => overview.value?.items ?? []);
  const byType = computed(() =>
    overview.value?.by_type ?? { character: 0, event: 0, relationship: 0 },
  );

  /** 当前 reshape % 下,角色数是否超出上限(给 ReshapeSlider 红色警告) */
  const isOverCharacterLimit = computed(() => {
    if (!preview.value) return false;
    return preview.value.current_touched_count > preview.value.max_touched_characters;
  });

  /** 推演触发前可用 — 直接拿当前 preview 的 affected ids 给 3D 图谱高亮 */
  const affectedNodeIds = computed<string[]>(
    () => preview.value?.current_affected_node_ids ?? [],
  );

  // ============================================================
  // 切项目
  // ============================================================

  function bind(newProjectId: string) {
    if (projectId.value === newProjectId) return;
    projectId.value = newProjectId;
    overview.value = null;
    preview.value = null;
    errorMessage.value = null;
  }

  // ============================================================
  // reload (active list)
  // ============================================================

  async function reload(): Promise<void> {
    const pid = projectId.value;
    if (!pid) return;
    loading.value = true;
    errorMessage.value = null;
    try {
      overview.value = await api.get<CounterfactualOverview>(
        `/projects/${pid}/counterfactuals`,
      );
    } catch (e) {
      errorMessage.value =
        e instanceof ApiError ? e.message : "加载反事实失败";
      overview.value = null;
    } finally {
      loading.value = false;
    }
  }

  // ============================================================
  // preview at reshape_percent (debounced)
  // ============================================================

  async function previewAt(reshapePercent: number, immediate = false): Promise<void> {
    const pid = projectId.value;
    if (!pid) return;

    // 取消还在 debounce 队列里的上一次请求(已发出的无法 cancel,见上面注释)
    if (previewDebounceTimer !== null) {
      clearTimeout(previewDebounceTimer);
      previewDebounceTimer = null;
    }

    const doFetch = async () => {
      previewLoading.value = true;
      try {
        preview.value = await api.get<ReshapePreview>(
          `/projects/${pid}/reshape_preview?reshape_percent=${reshapePercent}`,
        );
      } catch (e) {
        // ApiError NETWORK_ERROR / abort 静默(用户在快速拖滑块时是预期)
        if (e instanceof ApiError && e.code !== "NETWORK_ERROR") {
          errorMessage.value = `预览三维失败:${e.message}`;
        }
      } finally {
        previewLoading.value = false;
      }
    };

    if (immediate) {
      await doFetch();
    } else {
      // 滑块拖动场景 debounce 300ms
      previewDebounceTimer = setTimeout(() => {
        previewDebounceTimer = null;
        void doFetch();
      }, 300);
    }
  }

  // ============================================================
  // revert
  // ============================================================

  async function revert(changeId: string): Promise<RevertCounterfactualResult> {
    const result = await api.post<RevertCounterfactualResult>(
      `/counterfactuals/${changeId}/revert`,
    );
    // reload 让 overview 反映 active 变化
    await reload();
    return result;
  }

  // ============================================================
  // 2.C+ 显式创建(Workbench 用) — character/event/relationship + world
  // ============================================================

  async function createChange(
    req: CreateCounterfactualRequest,
  ): Promise<CounterfactualChange> {
    const pid = projectId.value;
    if (!pid) throw new Error("useCounterfactuals 未 bind 项目");
    const cf = await api.post<CounterfactualChange>(
      `/projects/${pid}/counterfactuals`,
      req,
    );
    await reload();
    return cf;
  }

  async function createWorldChange(
    req: CreateWorldCounterfactualRequest,
  ): Promise<CounterfactualChange> {
    const pid = projectId.value;
    if (!pid) throw new Error("useCounterfactuals 未 bind 项目");
    const cf = await api.post<CounterfactualChange>(
      `/projects/${pid}/counterfactuals/world`,
      req,
    );
    await reload();
    return cf;
  }

  // ============================================================
  // 2.C+ "本次推演选反事实子集" state 管理(给 SimulationDock 勾选用)
  // ============================================================

  /** 初始化为"全部 active 都选"(默认行为)— SimulationDock 打开时调 */
  function initSelectionAsAllActive() {
    if (!overview.value) {
      selectedIds.value = null;   // 还没加载,等 reload 完再说
      return;
    }
    selectedIds.value = new Set(overview.value.items.map((c) => c.id));
  }

  /** 切换某反事实的"是否本次用" */
  function toggleSelected(id: string) {
    if (selectedIds.value === null) {
      // 当前 null = 全部用;先具化为 set 再剔除
      if (overview.value) {
        selectedIds.value = new Set(overview.value.items.map((c) => c.id));
      } else {
        selectedIds.value = new Set();
      }
    }
    if (selectedIds.value.has(id)) {
      selectedIds.value.delete(id);
    } else {
      selectedIds.value.add(id);
    }
    // 触发响应式(Set mutation 不自动触发)
    selectedIds.value = new Set(selectedIds.value);
  }

  function isSelected(id: string): boolean {
    if (selectedIds.value === null) return true;   // 默认全选
    return selectedIds.value.has(id);
  }

  /** 给 CreateSimulationRequest 用 — null 表"用全部",array 表显式子集 */
  function getSelectedIdsForRequest(): string[] | null {
    if (selectedIds.value === null) return null;
    return Array.from(selectedIds.value);
  }

  /** 重置选择回"全部 active"(默认) */
  function resetSelection() {
    selectedIds.value = null;
  }

  // ============================================================
  // 工具:把 old/new value 解析回真值(quotes/no_go_list 是 JSON 数组)
  // ============================================================

  function parseValue(field: string, raw: string | null): unknown {
    if (raw === null || raw === undefined) return null;
    if (["quotes", "no_go_list", "participants"].includes(field)) {
      try {
        return JSON.parse(raw);
      } catch {
        return raw;
      }
    }
    return raw;
  }

  return {
    projectId,
    overview,
    items,
    totalActive,
    byType,
    loading,
    errorMessage,
    preview,
    previewLoading,
    affectedNodeIds,
    isOverCharacterLimit,
    selectedIds,
    bind,
    reload,
    previewAt,
    revert,
    createChange,
    createWorldChange,
    initSelectionAsAllActive,
    toggleSelected,
    isSelected,
    getSelectedIdsForRequest,
    resetSelection,
    parseValue,
  };
}
