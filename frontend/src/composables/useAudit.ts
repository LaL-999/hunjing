/**
 * useAudit — 自洽守护者前端状态机(Sprint 1.R)。
 *
 * 状态:
 *   idle       初始,或没诊断过
 *   loading    POST /audit 中(LLM 5-15s)
 *   ready      已有诊断结果展示
 *   error      调用失败 / LLM 输出非法
 *
 * 业务行为:
 *   start(simId)        触发新诊断(扣 cost,~0.05 元;不扣配额)
 *   loadLatest(simId)   静默拉最新一条(用户回 detail 页恢复结果);无则保持 idle
 *   reset()             清状态(modal 关 / 切 sim 时调)
 *
 * 错误码(errorCode):
 *   SIMULATION_NOT_AUDITABLE   422 sim 不是 done 状态
 *   LLM_UNAVAILABLE            503 LLM 调用失败
 *   LLM_OUTPUT_INVALID         503 LLM 输出非合法 JSON
 *   NOT_FOUND                  404 sim 跨用户访问
 *   其他                       message 直接显示
 */
import { ref } from "vue";

import { api } from "../api/client";
import {
  ApiError,
  type AcceptAuditIssueResponse,
  type AuditResponse,
} from "../api/types";

/**
 * 命名规范统一(2026-06-02 / P0Y):AuditPhase 与 useCanonicalGuardian.phase 对齐用 "done"
 * 历史:之前用 "ready",但 useCanonicalGuardian 用 "done",B6.1 因混用踩坑
 * 统一规则:**完成状态都叫 "done"** — 与后端 simulation state 一致
 */
export type AuditPhase = "idle" | "loading" | "done" | "error";

/** M7.C(2026-05-20)采纳操作结果 — 给 UI 显 toast / 高亮已采纳卡片用 */
export interface AcceptResult {
  ok: boolean;
  issueIdx: number;
  /** 仅 ok=true 时填,含 operations_applied / operations_skipped */
  response?: AcceptAuditIssueResponse;
  /** ok=false 时的错误 — 用户文案 */
  errorMessage?: string;
  /** ok=false 时的错误码 — 给前端逻辑判定(如 ISSUE_NO_PAYLOAD 显示"去修") */
  errorCode?: string;
}

export function useAudit() {
  const phase = ref<AuditPhase>("idle");
  const audit = ref<AuditResponse | null>(null);
  const errorMessage = ref<string | null>(null);
  const errorCode = ref<string | null>(null);
  /** M7.C:正在采纳中的 issue_idx 集合(防连点) */
  const acceptingIssues = ref<Set<number>>(new Set());

  /**
   * Sprint 6.A2 polish(2026-05-22):start sequence token
   * 用户 loading 中点 × 取消 → reset() 自增 _startSeq,旧 in-flight start() 完成时
   * 发现 mySeq !== _startSeq,丢弃结果不污染 audit ref
   * (LLM 在 backend 仍跑完,但前端"放弃"结果,允许用户重新启动)
   */
  let _startSeq = 0;

  async function start(simId: string) {
    const mySeq = ++_startSeq;
    phase.value = "loading";
    errorMessage.value = null;
    errorCode.value = null;
    try {
      const resp = await api.post<AuditResponse>(
        `/simulations/${simId}/audit`,
      );
      // 已被 reset()/ 新 start() 取代,丢弃本次结果
      if (mySeq !== _startSeq) return;
      audit.value = resp;
      phase.value = "done";
    } catch (e) {
      if (mySeq !== _startSeq) return;
      phase.value = "error";
      audit.value = null;
      if (e instanceof ApiError) {
        errorCode.value = e.code;
        if (e.code === "SIMULATION_NOT_AUDITABLE") {
          errorMessage.value = "只能诊断已完成的推演";
        } else if (
          e.code === "LLM_UNAVAILABLE" ||
          e.code === "LLM_OUTPUT_INVALID"
        ) {
          errorMessage.value = "AI 守护者暂时不可用,稍后重试";
        } else {
          errorMessage.value = e.message;
        }
      } else {
        errorMessage.value = "诊断失败,请稍后重试";
      }
    }
  }

  /**
   * 静默拉最新诊断(用户进 detail 页时调,恢复上次结果)。
   * 没诊断过(404)→ 静默保持 idle,不报错给用户。
   */
  async function loadLatest(simId: string) {
    try {
      const resp = await api.get<AuditResponse>(
        `/simulations/${simId}/audit/latest`,
      );
      audit.value = resp;
      phase.value = "done";
    } catch (e) {
      // 404 = 没诊断过 — 不算错误,静默
      if (e instanceof ApiError && e.code === "NO_AUDIT_YET") {
        audit.value = null;
        phase.value = "idle";
      } else {
        // 其他错误也别影响主页面阅读 — 静默,等用户主动点诊断
        audit.value = null;
        phase.value = "idle";
      }
    }
  }

  /**
   * M7.C(2026-05-20)采纳某 issue 的结构化建议,直接应用到对应字段(character / event / relationship)。
   *
   * 成功 → 把本地 audit.value.issues[issueIdx].accepted_at 标记 ISO 时间,UI 自动切到"已采纳"态
   * 失败 → 返回 errorCode 给调用方决定 UX(ISSUE_NO_PAYLOAD 引导用户点"去修";其它显示 toast)
   *
   * Returns AcceptResult — 调用方可基于 ok / errorCode 显示 toast 或弹"详细差异"框
   */
  async function acceptIssue(issueIdx: number): Promise<AcceptResult> {
    if (!audit.value) {
      return {
        ok: false, issueIdx,
        errorMessage: "诊断结果未加载",
        errorCode: "AUDIT_NOT_LOADED",
      };
    }
    if (acceptingIssues.value.has(issueIdx)) {
      return {
        ok: false, issueIdx,
        errorMessage: "正在采纳中,请稍候",
        errorCode: "IN_FLIGHT",
      };
    }
    acceptingIssues.value.add(issueIdx);
    try {
      const resp = await api.post<AcceptAuditIssueResponse>(
        `/audits/${audit.value.id}/issues/${issueIdx}/accept`,
      );
      // 同步标本地 issue.accepted_at — UI 立即切已采纳态
      const issues = audit.value.issues;
      if (issues[issueIdx]) {
        issues[issueIdx] = { ...issues[issueIdx], accepted_at: resp.accepted_at };
      }
      return { ok: true, issueIdx, response: resp };
    } catch (e) {
      if (e instanceof ApiError) {
        let msg = e.message;
        if (e.code === "ISSUE_NO_PAYLOAD") {
          msg = "AI 未给出可直接应用的建议,请点「去修」手动编辑";
        } else if (e.code === "ISSUE_ALREADY_ACCEPTED") {
          msg = "该建议已经采纳过了";
        } else if (e.code === "ISSUE_NOT_USER_FIXABLE") {
          msg = "该类型的问题需要在重新生成时调整,不支持采纳";
        } else if (e.code === "ISSUE_ALL_OPS_SKIPPED") {
          msg = "AI 给出的所有修改都不可应用(可能字段已填),请点「去修」手动编辑";
        } else if (e.code === "SUBJECT_NOT_FOUND") {
          msg = "建议要改的对象已被删除,无法采纳";
        }
        return { ok: false, issueIdx, errorMessage: msg, errorCode: e.code };
      }
      return {
        ok: false, issueIdx,
        errorMessage: "采纳失败,请稍后重试",
        errorCode: "UNKNOWN",
      };
    } finally {
      acceptingIssues.value.delete(issueIdx);
    }
  }

  function isAccepting(issueIdx: number): boolean {
    return acceptingIssues.value.has(issueIdx);
  }

  function reset() {
    // 自增 sequence 让 in-flight start() 完成时丢弃结果(防"幽灵 audit 弹回")
    _startSeq++;
    phase.value = "idle";
    audit.value = null;
    errorMessage.value = null;
    errorCode.value = null;
    acceptingIssues.value = new Set();
  }

  return {
    phase,
    audit,
    errorMessage,
    errorCode,
    start,
    loadLatest,
    acceptIssue,
    isAccepting,
    reset,
  };
}
