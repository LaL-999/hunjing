/**
 * useAuthorCompass — P3 作者指南针前端状态机(2026-05-26).
 *
 * 状态机:
 *   idle    没记录 — 用户从未触发(404)
 *   loading 正在拉 GET
 *   running 正在跑 POST analyze(同步等 LLM ~20-40s)
 *   ready   已有记录(可能某轨 done / 某轨 failed)
 *   locked  user_locked=true,生成时读 final_compass
 *   error   网络错 / 后端 500
 *
 * 用法:
 *   const compass = useAuthorCompass(projectId)
 *   await compass.load()                                     拉当前
 *   await compass.analyze({ author_name, work_title })        触发双轨制
 *   await compass.save({ final_compass, user_locked: true })  用户改+锁定
 */
import { computed, ref } from "vue";

import { api } from "../api/client";
import {
  ApiError,
  type AnalyzeAuthorCompassRequest,
  type AuthorCompassResponse,
  type UpdateAuthorCompassRequest,
} from "../api/types";

export type CompassPhase =
  | "idle"
  | "loading"
  | "running"
  | "ready"
  | "locked"
  | "error";

function resolvePhase(compass: AuthorCompassResponse | null): CompassPhase {
  if (compass === null) return "idle";
  if (compass.user_locked) return "locked";
  return "ready";
}

export function useAuthorCompass(projectId: string) {
  const phase = ref<CompassPhase>("idle");
  const compass = ref<AuthorCompassResponse | null>(null);
  const errorMessage = ref<string | null>(null);
  const errorCode = ref<string | null>(null);

  const isLocked = computed(() => compass.value?.user_locked === true);

  const hasBothTracksDone = computed(() => {
    const c = compass.value;
    if (c === null) return false;
    return c.external_status === "done" && c.internal_status === "done";
  });

  const hasAnyError = computed(() => {
    const c = compass.value;
    if (c === null) return false;
    return c.external_status === "failed" || c.internal_status === "failed";
  });

  /** 首次 mount / 用户切到面板时调 — 拉当前画像(可能 404) */
  async function load(): Promise<void> {
    phase.value = "loading";
    errorMessage.value = null;
    errorCode.value = null;
    try {
      const resp = await api.get<AuthorCompassResponse>(
        `/projects/${projectId}/author-compass`,
      );
      compass.value = resp;
      phase.value = resolvePhase(resp);
    } catch (e) {
      if (e instanceof ApiError && e.code === "AUTHOR_COMPASS_NOT_FOUND") {
        // 没历史记录 — idle 状态
        compass.value = null;
        phase.value = "idle";
        return;
      }
      handleError(e);
    }
  }

  /** 用户主动触发双轨制 LLM 调研 */
  async function analyze(payload: AnalyzeAuthorCompassRequest): Promise<void> {
    phase.value = "running";
    errorMessage.value = null;
    errorCode.value = null;
    try {
      // 2026-06-06:api.post 只接 1 个泛型(返回类型);老代码误传 2 个被推为 never
      const resp = await api.post<AuthorCompassResponse>(
        `/projects/${projectId}/author-compass/analyze`,
        payload,
      );
      compass.value = resp;
      phase.value = resolvePhase(resp);
    } catch (e) {
      handleError(e);
    }
  }

  /** 用户改字段 / 锁定 */
  async function save(payload: UpdateAuthorCompassRequest): Promise<void> {
    errorMessage.value = null;
    errorCode.value = null;
    try {
      // 2026-06-06:api.put 只接 1 个泛型(返回类型);老代码误传 2 个被推为 never
      const resp = await api.put<AuthorCompassResponse>(
        `/projects/${projectId}/author-compass`,
        payload,
      );
      compass.value = resp;
      phase.value = resolvePhase(resp);
    } catch (e) {
      handleError(e);
      throw e;   // 让 UI 知道保存失败(可显 toast)
    }
  }

  function handleError(e: unknown): void {
    phase.value = "error";
    if (e instanceof ApiError) {
      errorCode.value = e.code;
      errorMessage.value = e.message;
    } else {
      errorMessage.value = String(e);
    }
  }

  return {
    phase,
    compass,
    errorMessage,
    errorCode,
    isLocked,
    hasBothTracksDone,
    hasAnyError,
    load,
    analyze,
    save,
  };
}
