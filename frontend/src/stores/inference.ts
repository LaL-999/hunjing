/**
 * Inference Pinia store — 一键灌满北极星的全局推断状态(2026-06-01).
 *
 * 痛点:
 *   原本 ProjectView 本地 ref 持有 inference state + SSE,切换项目时 ProjectView
 *   销毁 → SSE 连接断 → 切回来看不到"在跑"的状态,用户以为失败再点一次,后端跑双份.
 *
 * 设计:
 *   - state + SSE 调用全部上提到此 store(store 不随路由销毁,App 级生命周期)
 *   - state 包含 `projectId` 标识"当前是哪个项目在跑"
 *   - ProjectView 用 computed 判断 `store.projectId === props.id` 决定是否显示进度面板
 *   - SSE event 持续写入 store,即便 ProjectView 已 unmount
 *   - 结束后保留 report + history 一段时间,4s 自动清空,与原 inline 行为一致
 *
 * 单例约束:
 *   同一时刻只允许 1 个项目在跑(start 时若已 loading,直接 return 拒绝)
 *   原因:LLM 调用并发会爆,且 UI 也只设计了一个 banner
 */
import { defineStore } from "pinia";
import { computed, ref } from "vue";

import { api } from "../api/client";

export interface StageProgressState {
  stage: string;
  label: string;
  status: "running" | "done" | "failed";
  reasoning?: string;
  error?: string;
}

export interface AllBoardsReport {
  story_core: { applied: boolean; reasoning: string } | null;
  narrative_view: { applied: boolean; reasoning: string; focus_name: string } | null;
  knowledge_boundaries: {
    applied: boolean;
    facts_created: number;
    knowledge_created: number;
    skipped_reason: string | null;
    reasoning: string;
  } | null;
  character_drivers: Array<{
    character_id: string;
    character_name: string;
    applied: boolean;
    reasoning: string;
  }>;
  stats: {
    stages_attempted: number;
    stages_succeeded: number;
    failures: string[];
  };
}

export interface InferenceStartOptions {
  includeDrivers: boolean;
  forceKnowledge: boolean;
  /** 推断结束后(成功 / 失败 / 致命异常),回调到调用者 — 调用者负责 reload / toast */
  onFinish: (result: {
    report: AllBoardsReport | null;
    forceKnowledge: boolean;
    fatalError: string | null;
  }) => void;
}

export const useInferenceStore = defineStore("inference", () => {
  // 哪个项目正在跑;null = 没有任何项目在跑
  const projectId = ref<string | null>(null);
  const loading = ref(false);

  // 进度状态
  const totalStages = ref(0);
  const currentIndex = ref(0);
  const currentLabel = ref("");
  const stageHistory = ref<StageProgressState[]>([]);

  // 完成后保留一段时间让用户回看
  let clearTimerId: number | null = null;

  function _resetProgress() {
    totalStages.value = 0;
    currentIndex.value = 0;
    currentLabel.value = "";
    stageHistory.value = [];
  }

  function _clearAll() {
    projectId.value = null;
    _resetProgress();
  }

  /** 启动北极星推断;同一时刻只允许 1 个项目在跑 */
  async function startAllBoardsInference(
    pid: string,
    opts: InferenceStartOptions,
  ): Promise<void> {
    if (loading.value) {
      // 已有项目在跑 — 拒绝(防双跑导致后端 LLM 双份调用)
      return;
    }
    // 清除上次保留的 history(立即重置,而不是等 4s timer)
    if (clearTimerId !== null) {
      clearTimeout(clearTimerId);
      clearTimerId = null;
    }
    _resetProgress();
    projectId.value = pid;
    loading.value = true;

    const params = new URLSearchParams({
      include_drivers: String(opts.includeDrivers),
      force_knowledge: String(opts.forceKnowledge),
    });
    let report: AllBoardsReport | null = null;
    let fatalError: string | null = null;

    await api.streamSSE(
      `/projects/${pid}/infer/all_boards/stream?${params.toString()}`,
      {},
      {
        onEvent: ({ event, data }) => {
          if (event === "plan") {
            totalStages.value = Number(data.total_stages ?? 0);
            return;
          }
          if (event === "stage_start") {
            currentIndex.value = Number(data.stage_index ?? 0);
            currentLabel.value = String(data.label ?? data.stage ?? "");
            stageHistory.value.push({
              stage: String(data.stage),
              label: String(data.label ?? data.stage),
              status: "running",
            });
            return;
          }
          if (event === "stage_done") {
            const stageName = String(data.stage);
            const item = stageHistory.value.find(
              (s) => s.stage === stageName && s.status === "running",
            );
            if (item) {
              item.status = "done";
              const r = String(data.reasoning ?? "");
              if (r) item.reasoning = r.slice(0, 100);
            }
            return;
          }
          if (event === "stage_failed") {
            const stageName = String(data.stage);
            const item = stageHistory.value.find(
              (s) => s.stage === stageName && s.status === "running",
            );
            if (item) {
              item.status = "failed";
              item.error = String(data.error ?? "");
            }
            return;
          }
          if (event === "report") {
            report = data.report as AllBoardsReport;
            return;
          }
          if (event === "fatal") {
            fatalError = String(data.error ?? "未知错误");
            return;
          }
        },
        onError: (e) => {
          fatalError = e.message;
        },
      },
    );

    // SSE 流结束 — 通知调用者(回调内做 toast / reload)
    loading.value = false;
    try {
      opts.onFinish({
        report,
        forceKnowledge: opts.forceKnowledge,
        fatalError,
      });
    } catch {
      // 回调自爆不影响 store 收尾
    }
    // 保留 history 4 秒让用户回看,然后清
    clearTimerId = window.setTimeout(() => {
      if (!loading.value) _clearAll();
      clearTimerId = null;
    }, 4000);
  }

  /** 当前推断是否正在某项目跑(用于 ProjectView 判断"我要不要显示进度面板") */
  function isRunningForProject(pid: string): boolean {
    return loading.value && projectId.value === pid;
  }

  /** 即便 loading=false 也可能 history 还在(完成后 4s 保留)— 用于显示汇总 */
  function hasHistoryForProject(pid: string): boolean {
    return projectId.value === pid && stageHistory.value.length > 0;
  }

  return {
    // 只读 state(直接暴露 ref,vue 自动 unref)
    projectId,
    loading,
    totalStages,
    currentIndex,
    currentLabel,
    stageHistory,
    // computed helpers
    isRunningForProject,
    hasHistoryForProject,
    // 进度百分比(给进度条用)
    progressPercent: computed(() => {
      if (totalStages.value <= 0) return 0;
      return Math.min(
        100,
        Math.round((currentIndex.value / totalStages.value) * 100),
      );
    }),
    // action
    startAllBoardsInference,
  };
});
