/**
 * useRefineSession — 角色对焦的状态机 + API 封装。
 *
 * 状态:
 *   idle        初始
 *   loading     POST /refine 中(LLM 调用 5-15s)
 *   reviewing   收到 refinements,用户逐个审阅
 *   done        全部处理完(或主动跳过)
 *   error       LLM 失败 / 角色不足 / 配额超限
 *
 * 业务行为:
 *   start()           触发 refine
 *   action(type)      当前 refinement accept/reject/edit → 自动 next,到底部转 done
 *   skipRemaining()   剩余批量 skip → done
 *   reset()           回 idle(modal 关闭时调)
 *
 * 错误分类(errorCode):
 *   TOO_FEW_CHARACTERS   角色数 < 3(422)— 父组件提示
 *   QUOTA_EXCEEDED       配额超限(429)— 父组件 catch 后弹 UpgradeModal
 *   LLM_UNAVAILABLE      LLM 调用失败(503)
 *   LLM_OUTPUT_INVALID   LLM 输出非合法 JSON(503)
 *   其他                 message 直接显示
 */
import { computed, ref } from "vue";

import { api } from "../api/client";
import {
  ApiError,
  type ActionRefinementResponse,
  type RefinementItem,
  type RefineSessionResponse,
  type RefineSessionStats,
  type SuggestionPayload,
} from "../api/types";

export type RefinePhase = "idle" | "loading" | "reviewing" | "done" | "error";

export function useRefineSession(getProjectId: () => string) {
  const phase = ref<RefinePhase>("idle");
  const sessionId = ref<string | null>(null);
  const refinements = ref<RefinementItem[]>([]);
  const stats = ref<RefineSessionStats | null>(null);
  const currentIndex = ref(0);
  const errorMessage = ref<string | null>(null);
  const errorCode = ref<string | null>(null);
  /** 配额超限时携带 ApiError.detail,父组件可传给 UpgradeModal */
  const errorDetail = ref<unknown>(null);
  const actionInFlight = ref(false);

  // 序列 token(2026-05-23 修补):× 中途取消时 reset() 会 ++,start() 用 await 前后
  // 的 mySeq 比对来丢弃已作废的 LLM 响应。无此 token 时,用户取消后 LLM 返回会把
  // phase 写成 reviewing,污染下次开 modal 的状态。
  let _startSeq = 0;

  const currentRefinement = computed(
    () => refinements.value[currentIndex.value] ?? null,
  );
  const totalCount = computed(() => refinements.value.length);
  const acceptedCount = computed(
    () => refinements.value.filter((r) => r.status === "accepted").length,
  );
  const rejectedCount = computed(
    () => refinements.value.filter((r) => r.status === "rejected").length,
  );

  /** 对焦完后哪些角色被改了,父组件需 reload character 数据 */
  const refinedCharacterIds = computed(() =>
    Array.from(
      new Set(
        refinements.value
          .filter(
            (r) => r.status === "accepted" || r.status === "edited",
          )
          .map((r) => r.character_id),
      ),
    ),
  );

  async function start() {
    const mySeq = ++_startSeq;
    phase.value = "loading";
    errorMessage.value = null;
    errorCode.value = null;
    errorDetail.value = null;
    sessionId.value = null;
    refinements.value = [];
    stats.value = null;
    currentIndex.value = 0;

    try {
      const resp = await api.post<RefineSessionResponse>(
        `/projects/${getProjectId()}/refine`,
      );
      // 中途取消(reset 已被调用 → seq 已增):静默丢弃响应,不污染 phase
      if (mySeq !== _startSeq) return;
      sessionId.value = resp.session_id;
      refinements.value = resp.refinements;
      stats.value = resp.stats;

      if (resp.refinements.length === 0) {
        // LLM 没出建议(可能全被兜底过滤),直接到 done
        phase.value = "done";
      } else {
        phase.value = "reviewing";
      }
    } catch (e) {
      // 同上 — 中途取消的错误也吞掉(用户已不需要看到错误屏)
      if (mySeq !== _startSeq) return;
      phase.value = "error";
      if (e instanceof ApiError) {
        errorCode.value = e.code;
        errorDetail.value = e.detail;
        if (e.code === "TOO_FEW_CHARACTERS") {
          errorMessage.value = "至少需要 3 个角色才能对焦";
        } else if (
          e.code === "QUOTA_EXCEEDED" ||
          e.code === "INSUFFICIENT_CREDITS"
        ) {
          errorMessage.value = e.message;
        } else if (e.code === "LLM_UNAVAILABLE") {
          errorMessage.value = "AI 服务暂时不可用,请稍后重试";
        } else if (e.code === "LLM_OUTPUT_INVALID") {
          errorMessage.value = "AI 输出不合法,请稍后重试或跳过对焦";
        } else {
          errorMessage.value = e.message;
        }
      } else {
        errorMessage.value = "调用 AI 失败,请稍后重试";
      }
    }
  }

  async function action(
    type: "accept" | "reject" | "edit",
    user_edit?: SuggestionPayload,
  ) {
    const r = currentRefinement.value;
    if (!r || actionInFlight.value) return;

    actionInFlight.value = true;
    try {
      const resp = await api.post<ActionRefinementResponse>(
        `/refinements/${r.id}/action`,
        { action: type, user_edit },
      );
      r.status = resp.status;
    } catch (e) {
      // 单条失败不阻塞:本地标 reject 让用户继续
      r.status = "rejected";
      if (import.meta.env.DEV) {
        console.warn("refinement action 失败:", e);
      }
    } finally {
      actionInFlight.value = false;
    }

    currentIndex.value++;
    if (currentIndex.value >= refinements.value.length) {
      phase.value = "done";
    }
  }

  async function skipRemaining() {
    if (!sessionId.value) {
      phase.value = "done";
      return;
    }
    try {
      await api.post(`/refine_sessions/${sessionId.value}/skip`, {
        reason: "user_skipped",
      });
    } catch {
      /* 静默,前端已经切到 done */
    }
    refinements.value.forEach((r) => {
      if (r.status === "pending") r.status = "skipped";
    });
    phase.value = "done";
  }

  function reset() {
    // 让所有 in-flight start() 在 await 后通过 seq 比对自我丢弃响应
    _startSeq++;
    phase.value = "idle";
    sessionId.value = null;
    refinements.value = [];
    stats.value = null;
    currentIndex.value = 0;
    errorMessage.value = null;
    errorCode.value = null;
    errorDetail.value = null;
    actionInFlight.value = false;
  }

  return {
    // state
    phase,
    sessionId,
    refinements,
    stats,
    currentIndex,
    errorMessage,
    errorCode,
    errorDetail,
    actionInFlight,
    // derived
    currentRefinement,
    totalCount,
    acceptedCount,
    rejectedCount,
    refinedCharacterIds,
    // actions
    start,
    action,
    skipRemaining,
    reset,
  };
}
