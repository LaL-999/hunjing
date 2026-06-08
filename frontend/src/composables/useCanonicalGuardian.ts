/**
 * useCanonicalGuardian — 正典守护者前端状态机(Sprint 2.D 差异化王牌)。
 *
 * 与 useAudit(1.R 自洽守护者同步模式)区别:
 *   - 自洽:LLM 调用 ~10s,同步阻塞 POST 即返完整结果
 *   - 正典:LLM 调用 30-60s(8 维度 + 反事实豁免 + 长产物),async + state 机
 *     POST → state='running' → 2s polling /latest → state='done' / 'failed'
 *
 * UI 维度状态:
 *   idle       从未触发
 *   loading    正在拉 /latest(首次 mount 或刷新)
 *   running    后台 worker 在跑(polling)
 *   done       拿到 issues 列表
 *   failed     LLM / 后端报错
 *   error      本地 POST 失败 / 网络错
 *   not_applicable  mode='initial' 项目(不适用)
 */
import { computed, ref } from "vue";

import { api } from "../api/client";
import {
  ApiError,
  type CanonicalAuditResponse,
  type CanonicalIssue,
} from "../api/types";

export type CanonicalPhase =
  | "idle"
  | "loading"
  | "running"
  | "done"
  | "failed"
  | "error"
  | "not_applicable";

const POLL_INTERVAL_MS = 2000;
// 2026-05-27:9 维度审计(P4 / B5.3 加身体描写 + life_status block)+ 长产物
//   实测 12000+ 字续作 LLM 处理可能到 150-180s,放宽 poll 上限到 200s 留余量。
//   后端 timeout 同步上调到 180s(canonical_guardian_service.py),余 20s 给落库 + 网络。
const MAX_POLL_ATTEMPTS = 100; // 100 × 2s = 200s 上限

export function useCanonicalGuardian(simulationId: string) {
  const phase = ref<CanonicalPhase>("idle");
  const audit = ref<CanonicalAuditResponse | null>(null);
  const errorMessage = ref<string | null>(null);
  const errorCode = ref<string | null>(null);

  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let pollAttempts = 0;

  function clearPoll() {
    if (pollTimer !== null) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    pollAttempts = 0;
  }

  /** 首次 mount / 用户切到面板时调 — 拉 latest 看是否有历史 audit */
  async function loadLatest(): Promise<void> {
    phase.value = "loading";
    errorMessage.value = null;
    errorCode.value = null;
    try {
      const resp = await api.get<CanonicalAuditResponse>(
        `/simulations/${simulationId}/canonical_audit/latest`,
      );
      audit.value = resp;
      if (resp.state === "running") {
        phase.value = "running";
        startPolling();
      } else if (resp.state === "failed") {
        phase.value = "failed";
        errorMessage.value = resp.error_message ?? "审计失败";
      } else {
        phase.value = "done";
      }
    } catch (e) {
      if (e instanceof ApiError && e.code === "CANONICAL_AUDIT_NOT_FOUND") {
        // 没历史 audit — 这是正常起始状态
        phase.value = "idle";
        audit.value = null;
      } else {
        phase.value = "error";
        errorMessage.value = e instanceof ApiError ? e.message : "加载审计失败";
      }
    }
  }

  /** 用户点"开始审计"按钮 — POST trigger,然后开始 polling */
  async function trigger(): Promise<void> {
    phase.value = "running";   // 立刻显 running UI(乐观更新)
    errorMessage.value = null;
    errorCode.value = null;
    try {
      const resp = await api.post<CanonicalAuditResponse>(
        `/simulations/${simulationId}/canonical_audit`,
      );
      audit.value = resp;
      // 后端可能同步返 done(测试模式)或 running(生产 async)
      if (resp.state === "done") {
        phase.value = "done";
      } else if (resp.state === "failed") {
        phase.value = "failed";
        errorMessage.value = resp.error_message ?? "审计失败";
      } else {
        startPolling();
      }
    } catch (e) {
      phase.value = "error";
      if (e instanceof ApiError) {
        errorCode.value = e.code;
        errorMessage.value = e.message;
        if (e.code === "CANONICAL_AUDIT_NOT_APPLICABLE") {
          phase.value = "not_applicable";
        }
      } else {
        errorMessage.value = "触发审计失败,请稍后重试";
      }
    }
  }

  function startPolling() {
    clearPoll();
    pollAttempts = 0;
    pollTimer = setInterval(async () => {
      pollAttempts++;
      if (pollAttempts > MAX_POLL_ATTEMPTS) {
        clearPoll();
        phase.value = "error";
        errorMessage.value = "审计超时,请稍后重试";
        return;
      }
      try {
        const resp = await api.get<CanonicalAuditResponse>(
          `/simulations/${simulationId}/canonical_audit/latest`,
        );
        audit.value = resp;
        if (resp.state === "done") {
          phase.value = "done";
          clearPoll();
        } else if (resp.state === "failed") {
          phase.value = "failed";
          errorMessage.value = resp.error_message ?? "审计失败";
          clearPoll();
        }
        // 仍 running → 继续 poll
      } catch {
        /* 静默重试,直到达 MAX_POLL_ATTEMPTS */
      }
    }, POLL_INTERVAL_MS);
  }

  function unsubscribe() {
    clearPoll();
  }

  function reset() {
    clearPoll();
    phase.value = "idle";
    audit.value = null;
    errorMessage.value = null;
    errorCode.value = null;
  }

  // 派生:按 dimension 分组 issues(给 UI 列表用)
  const issues = computed<CanonicalIssue[]>(() => audit.value?.issues ?? []);

  // 派生:严格符合 vs 真偏离 vs 反事实豁免 — 给顶部统计 chip
  const stats = computed(() => {
    const items = issues.value;
    let strict = 0;
    let realDrift = 0;
    let exempt = 0;
    for (const it of items) {
      if (it.counterfactual_exempt) {
        exempt++;
      } else if (it.severity === "strict_canonical") {
        strict++;
      } else {
        realDrift++;
      }
    }
    return { total: items.length, strict, realDrift, exempt };
  });

  return {
    phase,
    audit,
    issues,
    stats,
    errorMessage,
    errorCode,
    loadLatest,
    trigger,
    unsubscribe,
    reset,
  };
}
