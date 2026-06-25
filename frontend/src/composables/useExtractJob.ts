/**
 * useExtractJob — 自动图谱抽取前端状态机 + SSE 实时事件流(Sprint 2.B + SSE polish)。
 *
 * 设计镜像 useSimulation 1.L:
 *   1. POST /uploads/{id}/extract → 拿 job_id + 状态 detail
 *   2. 非终态 → POST /auth/sse_token 拿短期 token → new EventSource(token-in-URL,
 *      浏览器 EventSource API 不支持自定义 header)
 *   3. SSE 推送 11 种事件(snapshot / state_change / chunk_done / profile_done / done / error 等)
 *   4. SSE 异常 → fallback 2s 轮询(降级保活,会合成 polling_tick 事件让 UI 不空白)
 *   5. 终态(done / error)→ 关 SSE,补拉一次 GET /extract_jobs/{id}(同步 detail)
 *
 * UI 维度状态:
 *   idle      初始
 *   creating  POST /extract 中(< 1s)
 *   running   queued/extracting_graph/generating_characters/inferring_meta/saving 任一
 *   done      state=done
 *   failed    state=failed
 *   error     本地 POST 失败 / 网络错
 *
 * 错误码处理:
 *   QUOTA_EXCEEDED          配额超限,父弹 UpgradeModal
 *   UPLOAD_NOT_EXTRACTABLE  upload 状态非 parsed
 *
 * SSE 失败降级(2025-05 修):
 *   polling fallback 不再只更新 detail,而是用前后帧差分合成 polling_tick 事件
 *   推到 events.value,让 UI 即使 SSE 不工作也能可视化进度。同时 toast.info
 *   一次告知用户当前是降级模式。
 */
import { ref } from "vue";

import { api } from "../api/client";
import {
  ApiError,
  type ExtractEvent,
  type ExtractJobResponse,
} from "../api/types";
import { useQuotaStore } from "../stores/quota";
import { toast } from "./useToast";

const POLL_INTERVAL_MS = 2000;
const MAX_EVENTS_KEPT = 100;
const TERMINAL_STATES = new Set<ExtractJobResponse["state"]>(["done", "failed"]);

export type ExtractJobPhase =
  | "idle"
  | "creating"
  | "running"
  | "pending_review"   // Sprint 6.A2 FOCUS.10(2026-05-22):人机协同审核暂停
  | "done"
  | "failed"
  | "error";

/** Sprint 6.A2 FOCUS.10:审核阶段后端推过来的待审 PERSON 数据 */
export interface PendingReviewPerson {
  name: string;
  description: string;
  aliases: string[];
  text_occurrences: number;
}

export function useExtractJob() {
  const phase = ref<ExtractJobPhase>("idle");
  const detail = ref<ExtractJobResponse | null>(null);
  const events = ref<ExtractEvent[]>([]);
  const errorMessage = ref<string | null>(null);
  const errorCode = ref<string | null>(null);
  const errorDetail = ref<unknown>(null);
  // Sprint 6.A2 FOCUS.10(2026-05-22):审核暂停时填充,供 modal 渲染
  const pendingReviewPersons = ref<PendingReviewPerson[]>([]);
  const pendingMissedPersons = ref<string[]>([]);

  let eventSource: EventSource | null = null;
  let fallbackTimer: ReturnType<typeof setInterval> | null = null;
  // polling fallback 差分基线:上一次拉到的 detail 快照,用于合成 polling_tick
  let prevPollDetail: ExtractJobResponse | null = null;
  // SSE 降级 toast 只提示一次(避免每次 polling tick 都骚扰)
  let fallbackToastShown = false;

  // ============================================================
  // 内部
  // ============================================================

  function pushEvent(ev: ExtractEvent) {
    ev._ts = Date.now();
    events.value.push(ev);
    if (events.value.length > MAX_EVENTS_KEPT) {
      events.value.splice(0, events.value.length - MAX_EVENTS_KEPT);
    }
  }

  /** 把 SSE 事件应用到 detail.value 上(增量更新,客户端无需等再 GET)。*/
  function applyEventToDetail(ev: ExtractEvent) {
    const cur = detail.value;
    if (!cur) {
      // snapshot 事件第一帧:用整个 payload 当 detail 初始
      // 2026-06-06:cur 在 if(!cur) 分支已 narrow 到 null,原 cur?.X 永远 undefined
      // 直接用默认值,TS 不再报 'Property X does not exist on never' 假错
      if (ev.kind === "snapshot") {
        detail.value = {
          id: ev.id ?? "",
          upload_id: "",       // snapshot 不带 upload_id;无 cur 时只能空串
          project_id: "",
          state: (ev.state ?? "queued") as ExtractJobResponse["state"],
          is_admin_retag: ev.is_admin_retag ?? false,
          inferred_type: null,
          inferred_custom_type_name: null,
          inferred_tags: [],
          characters_count: ev.characters_count ?? 0,
          relationships_count: ev.relationships_count ?? 0,
          events_count: ev.events_count ?? 0,
          skipped_count: ev.skipped_count ?? 0,
          tokens_input: ev.tokens_input ?? 0,
          tokens_output: ev.tokens_output ?? 0,
          cost_yuan: ev.cost_yuan ?? 0,
          error_message: ev.error_message ?? null,
          started_at: new Date().toISOString(),
          completed_at: null,
          // SSE snapshot 不带 runtime 派生字段;后续 GET 拉到时会覆盖
          is_alive: true,   // SSE 连上了 → 假定 worker 活跃
          resumable: false,
          completed_chunks_count: 0,
        };
      }
      return;
    }
    // 已有 detail → 增量更新
    if (ev.kind === "state_change" && ev.state) {
      cur.state = ev.state as ExtractJobResponse["state"];
    } else if (ev.kind === "extract_graph_done") {
      // 计数留给后续 save_done / done 覆写最终值;这里只是过程指示
    } else if (ev.kind === "meta_inferred") {
      if (ev.inferred_type !== undefined) cur.inferred_type = ev.inferred_type;
      if (ev.inferred_custom_type_name !== undefined) {
        cur.inferred_custom_type_name = ev.inferred_custom_type_name;
      }
      if (ev.inferred_tags) cur.inferred_tags = ev.inferred_tags;
    } else if (ev.kind === "entities_pending_review") {
      // Sprint 6.A2 FOCUS.10(2026-05-22):后端 worker 已停在 review 阶段
      // 把 persons + missed 存起来,phase 切到 pending_review,前端弹 modal
      const persons = (ev as unknown as { persons?: PendingReviewPerson[] }).persons ?? [];
      const missed = ev.missed_persons ?? [];
      pendingReviewPersons.value = persons;
      pendingMissedPersons.value = missed;
      cur.state = "entities_pending_review" as ExtractJobResponse["state"];
      phase.value = "pending_review";
    } else if (ev.kind === "save_done" || ev.kind === "done") {
      if (ev.characters_count !== undefined) cur.characters_count = ev.characters_count;
      if (ev.relationships_count !== undefined) cur.relationships_count = ev.relationships_count;
      if (ev.events_count !== undefined) cur.events_count = ev.events_count;
      if (ev.skipped_count !== undefined) cur.skipped_count = ev.skipped_count;
      if (ev.cost_yuan !== undefined) cur.cost_yuan = ev.cost_yuan;
      if (ev.kind === "done") {
        cur.state = "done";
        cur.completed_at = new Date().toISOString();
        phase.value = "done";
        // 2026-06-25:抽取 done 时后端按真实 token 扣了 credit,刷新侧栏「本月余额」。
        // applyEvent 同时承接 SSE done 与 polling 派生的 done,两条路径都覆盖。
        void useQuotaStore().refresh();
      }
    } else if (ev.kind === "error") {
      cur.state = "failed";
      cur.error_message = ev.message ?? "抽取失败";
      cur.completed_at = new Date().toISOString();
      phase.value = "failed";
      errorMessage.value = cur.error_message;
    }
    // 实时 token 累计(SSE chunk_done / profile_done 事件会带)
    if (ev.tokens_input !== undefined && ev.tokens_input > cur.tokens_input) {
      cur.tokens_input = ev.tokens_input;
    }
    if (ev.tokens_output !== undefined && ev.tokens_output > cur.tokens_output) {
      cur.tokens_output = ev.tokens_output;
    }
  }

  function closeStream() {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    if (fallbackTimer) {
      clearInterval(fallbackTimer);
      fallbackTimer = null;
    }
  }

  /** SSE 失败降级:2s 轮询,**并合成事件推到 events** 让 UI 可视化。
   *
   * 合成策略(避免 100 条容量被秒填满):
   *   - 第一次拉到 detail → 推 snapshot + state_change(让 UI 立刻有内容)
   *   - 后续 tick:state 变化 → state_change;tokens 增长 ≥ 1 → polling_tick
   *   - characters_count 从 0 变非 0 → save_done
   *   - 进入 done → done;进入 failed → error
   */
  function startFallbackPolling(jobId: string) {
    if (fallbackTimer) return;
    prevPollDetail = null;

    if (!fallbackToastShown) {
      fallbackToastShown = true;
      toast.info("实时进度暂不可用,改用 2 秒轮询(进度有 1-2 秒延迟)", 4000);
    }

    fallbackTimer = setInterval(async () => {
      try {
        const resp = await api.get<ExtractJobResponse>(
          `/extract_jobs/${jobId}`,
        );
        synthesizeEventsFromDiff(prevPollDetail, resp);
        detail.value = resp;
        prevPollDetail = { ...resp, inferred_tags: [...resp.inferred_tags] };

        if (resp.state === "done") {
          phase.value = "done";
          closeStream();
        } else if (resp.state === "failed") {
          phase.value = "failed";
          errorMessage.value = resp.error_message ?? "抽取失败";
          closeStream();
        }
      } catch (e) {
        if (e instanceof ApiError && e.code === "NOT_FOUND") {
          phase.value = "error";
          errorMessage.value = "抽取任务不存在(可能被删除)";
          closeStream();
        }
      }
    }, POLL_INTERVAL_MS);
  }

  /** 比较前后两帧 detail,合成 polling 模式专用事件推入 events.value。
   *
   * prev=null:第一次,推 snapshot + 当前 state_change(让 UI 不空白)
   * 否则:差分推 state_change / polling_tick / save_done / done / error
   */
  function synthesizeEventsFromDiff(
    prev: ExtractJobResponse | null,
    cur: ExtractJobResponse,
  ): void {
    if (prev === null) {
      // 第一次降级 polling 拉到 detail → 让 UI 立刻看到状态
      pushEvent({
        kind: "snapshot",
        id: cur.id,
        state: cur.state,
        characters_count: cur.characters_count,
        relationships_count: cur.relationships_count,
        events_count: cur.events_count,
        skipped_count: cur.skipped_count,
        tokens_input: cur.tokens_input,
        tokens_output: cur.tokens_output,
        cost_yuan: cur.cost_yuan,
        is_admin_retag: cur.is_admin_retag,
      });
      pushEvent({ kind: "state_change", state: cur.state });
      return;
    }
    // state 推进
    if (prev.state !== cur.state) {
      pushEvent({ kind: "state_change", state: cur.state });
    }
    // tokens 增长(中间过程的进度信号)
    const tokensIncreased =
      cur.tokens_input > prev.tokens_input ||
      cur.tokens_output > prev.tokens_output;
    if (tokensIncreased && cur.state !== "done" && cur.state !== "failed") {
      pushEvent({
        kind: "polling_tick",
        state: cur.state,
        tokens_input: cur.tokens_input,
        tokens_output: cur.tokens_output,
      });
    }
    // 计数变化(save 阶段后会一次性写入)
    const countsChanged =
      cur.characters_count !== prev.characters_count ||
      cur.relationships_count !== prev.relationships_count ||
      cur.events_count !== prev.events_count;
    if (countsChanged && cur.characters_count > 0) {
      pushEvent({
        kind: "save_done",
        characters_count: cur.characters_count,
        relationships_count: cur.relationships_count,
        events_count: cur.events_count,
        skipped_count: cur.skipped_count,
      });
    }
    // meta 推断完成(inferred_type / tags 出现)
    if (
      prev.inferred_type === null &&
      cur.inferred_type !== null
    ) {
      pushEvent({
        kind: "meta_inferred",
        inferred_type: cur.inferred_type,
        inferred_custom_type_name: cur.inferred_custom_type_name,
        inferred_tags: cur.inferred_tags,
      });
    }
    // 终态
    if (prev.state !== "done" && cur.state === "done") {
      pushEvent({
        kind: "done",
        characters_count: cur.characters_count,
        relationships_count: cur.relationships_count,
        events_count: cur.events_count,
        skipped_count: cur.skipped_count,
        cost_yuan: cur.cost_yuan,
      });
    } else if (prev.state !== "failed" && cur.state === "failed") {
      pushEvent({
        kind: "error",
        message: cur.error_message ?? "抽取失败",
      });
    }
  }

  async function openStream(jobId: string) {
    closeStream();
    events.value = [];
    fallbackToastShown = false;

    // 1. 拿短期 SSE token
    let sseToken: string;
    try {
      const tokResp = await api.post<{ token: string; expires_in: number }>(
        "/auth/sse_token",
      );
      sseToken = tokResp.token;
    } catch (e) {
      if (import.meta.env.DEV) {
        console.warn("[useExtractJob] SSE token 失败,降级 polling", e);
      }
      startFallbackPolling(jobId);
      return;
    }

    // 2. 创建 EventSource
    // ⚠️ 必须带 VITE_API_BASE 前缀:壳化/拆子域部署时前端在 app.子域、后端在 api.子域,
    // 裸相对 /api 会打到 app 子域(只有静态文件 → 兜底返 index.html,非 event-stream → SSE 挂)。
    // 与 client.ts 的 API_BASE 取值口径一致。
    const apiBase = (import.meta.env.VITE_API_BASE as string | undefined) || "";
    const url = `${apiBase}/api/extract_jobs/${jobId}/stream?token=${encodeURIComponent(
      sseToken,
    )}`;
    const es = new EventSource(url);
    eventSource = es;

    es.onmessage = (e) => {
      let payload: ExtractEvent;
      try {
        payload = JSON.parse(e.data);
      } catch {
        return;
      }
      pushEvent(payload);
      applyEventToDetail(payload);

      // P0L.3(2026-05-24):主角缺位警告 → toast 提醒(用户可能没盯着进度区)
      if (payload.kind === "missed_protagonist_warning") {
        const missed = payload.missed_persons ?? [];
        if (missed.length > 0) {
          const preview = missed.slice(0, 3).join(" / ");
          const more = missed.length > 3 ? `(共 ${missed.length} 个)` : "";
          toast.warning(
            `⚠ 疑似主角漏抽:${preview}${more} — 请到「主角面板」手动补充`,
            8000,
          );
        }
      }

      // 终态:关连接;done 时补拉一次完整 detail(snapshot 不带完整 inferred_tags 等)
      if (payload.kind === "done" || payload.kind === "error") {
        closeStream();
        if (payload.kind === "done") {
          void api
            .get<ExtractJobResponse>(`/extract_jobs/${jobId}`)
            .then((d) => {
              detail.value = d;
            })
            .catch(() => {
              /* 静默,SSE 已经填了 detail */
            });
        }
      }
    };

    es.onerror = () => {
      // SSE 失败可能是网络抖动 / 鉴权过期 / 后端重启,降级 polling 兜底
      closeStream();
      if (import.meta.env.DEV) {
        console.warn("[useExtractJob] SSE 异常,降级 polling");
      }
      startFallbackPolling(jobId);
    };
  }

  // ============================================================
  // 公开 API
  // ============================================================

  async function start(uploadId: string): Promise<void> {
    phase.value = "creating";
    errorMessage.value = null;
    errorCode.value = null;
    errorDetail.value = null;
    detail.value = null;
    events.value = [];
    fallbackToastShown = false;
    try {
      const resp = await api.post<ExtractJobResponse>(
        `/uploads/${uploadId}/extract`,
      );
      detail.value = resp;
      if (TERMINAL_STATES.has(resp.state)) {
        // 测试同步模式:已 done / failed
        phase.value = resp.state === "done" ? "done" : "failed";
        if (resp.state === "failed") {
          errorMessage.value = resp.error_message ?? "抽取失败";
        }
      } else {
        phase.value = "running";
        await openStream(resp.id);
      }
    } catch (e) {
      phase.value = "error";
      if (e instanceof ApiError) {
        errorCode.value = e.code;
        errorDetail.value = e.detail;
        if (e.code === "UPLOAD_NOT_EXTRACTABLE") {
          errorMessage.value = "该文件已抽取完成或正在抽取中";
        } else if (
          e.code === "QUOTA_EXCEEDED" ||
          e.code === "INSUFFICIENT_CREDITS"
        ) {
          // Sprint C.1:credit 不足或资源容量超限,父组件接 errorCode 弹 UpgradeModal
          errorMessage.value = e.message;
        } else {
          errorMessage.value = e.message;
        }
      } else {
        errorMessage.value = "触发抽取失败,请稍后重试";
      }
    }
  }

  /** 切到详情页时订阅(从 jobId 拉一次 + 启动 SSE 或 polling)*/
  async function subscribe(jobId: string) {
    try {
      const resp = await api.get<ExtractJobResponse>(`/extract_jobs/${jobId}`);
      detail.value = resp;
      if (resp.state === "done") {
        phase.value = "done";
        return;
      }
      if (resp.state === "failed") {
        phase.value = "failed";
        errorMessage.value = resp.error_message ?? "抽取失败";
        return;
      }
      phase.value = "running";
      await openStream(jobId);
    } catch (e) {
      phase.value = "error";
      errorMessage.value =
        e instanceof ApiError ? e.message : "加载抽取任务失败";
    }
  }

  function unsubscribe() {
    closeStream();
  }

  function reset() {
    closeStream();
    phase.value = "idle";
    detail.value = null;
    events.value = [];
    errorMessage.value = null;
    errorCode.value = null;
    errorDetail.value = null;
    prevPollDetail = null;
    fallbackToastShown = false;
  }

  /** 用户主动取消 / 解除僵尸态(POST /api/extract_jobs/{id}/reset)。
   *
   * 后端 reset 行为:job state→failed + upload state→parsed + 保留 chunk_results。
   * 本地副作用:关 SSE/polling + phase=failed + detail.state='failed'。
   * 不退配额(LLM 已付,退不回)。
   */
  async function cancel(jobId: string): Promise<void> {
    try {
      const resp = await api.post<ExtractJobResponse>(
        `/extract_jobs/${jobId}/reset`,
      );
      detail.value = resp;
      phase.value = "failed";
      errorMessage.value = resp.error_message ?? "已取消";
      closeStream();
    } catch (e) {
      // 已 done/failed → 409 EXTRACT_NOT_RESETTABLE,直接拉一次最新 detail 同步前端
      if (e instanceof ApiError && e.code === "EXTRACT_NOT_RESETTABLE") {
        try {
          const d = await api.get<ExtractJobResponse>(`/extract_jobs/${jobId}`);
          detail.value = d;
          phase.value = d.state === "done" ? "done" : "failed";
        } catch {
          /* 静默 */
        }
        return;
      }
      throw e;
    }
  }

  /** 用户从断点继续抽取(POST /api/extract_jobs/{id}/resume)。
   *
   * 前置:job state='failed' + chunk_results 不空(由 detail.resumable 推断)。
   * 流程:POST resume → 后端起新 worker(同 job_id)→ 本地启 SSE 监听该 job。
   * 不扣新配额(同一 job 的延续)。
   */
  async function resume(jobId: string): Promise<void> {
    try {
      const resp = await api.post<ExtractJobResponse>(
        `/extract_jobs/${jobId}/resume`,
      );
      detail.value = resp;
      events.value = [];
      errorMessage.value = null;
      errorCode.value = null;
      errorDetail.value = null;
      prevPollDetail = null;
      fallbackToastShown = false;
      if (TERMINAL_STATES.has(resp.state)) {
        phase.value = resp.state === "done" ? "done" : "failed";
      } else {
        phase.value = "running";
        await openStream(jobId);
      }
    } catch (e) {
      phase.value = "error";
      if (e instanceof ApiError) {
        errorCode.value = e.code;
        errorDetail.value = e.detail;
        errorMessage.value =
          e.code === "EXTRACT_NOT_RESUMABLE"
            ? "无可恢复的断点(可能已完成 / 没抽过任何块)"
            : e.message;
      } else {
        errorMessage.value = "继续抽取失败,请稍后重试";
      }
      throw e;
    }
  }

  return {
    phase,
    detail,
    events,
    errorMessage,
    errorCode,
    errorDetail,
    start,
    subscribe,
    unsubscribe,
    reset,
    cancel,
    resume,
    // Sprint 6.A2 FOCUS.10(2026-05-22):审核阶段暴露给父组件
    pendingReviewPersons,
    pendingMissedPersons,
  };
}


// ============================================================
// formatExtractEvent — 给 UI 渲染用的格式化助手(镜像 formatSimulationEvent)
// ============================================================

export interface FormattedExtractEvent {
  /** Vue v-for 唯一 key */
  key: string;
  /** 缩进级别 0/1 — 阶段是 0,阶段内子事件是 1 */
  indent: number;
  /** 行首图标(✦ ▸ ✓ ✗ · —) */
  icon: string;
  /** 主文案 */
  main: string;
  /** 副文案(灰,可空) */
  sub: string;
  /** 状态色 — FOCUS.9 加 warning 给"漏抽提示"用 */
  tone: "normal" | "success" | "error" | "warning";
}

const STATE_LABEL: Record<string, string> = {
  queued: "排队中",
  extracting_graph: "AI 拆人物 / 关系",
  generating_characters: "为主角生成档案",
  inferring_meta: "识别作品类型 / 题材",
  saving: "保存到项目",
  done: "完成",
  failed: "失败",
};

/** 把 backend chunk_failed 的技术错误转成用户友好短文案。
 *
 * 镜像 backend extract_service._humanize_error 的 pattern 匹配,
 * 但只处理 chunk 级常见失败(JSON 截断 / 超时 / API 限流)。
 */
function humanizeChunkError(raw: string): string {
  const lower = raw.toLowerCase();
  if (lower.includes("llmjsonparsefailed") || lower.includes("json")) {
    return "AI 输出 JSON 截断";
  }
  if (lower.includes("timeout") || lower.includes("超时")) {
    return "AI 响应超时";
  }
  if (lower.includes("ratelimit") || lower.includes("rate_limit") || lower.includes("限流")) {
    return "AI 服务限流";
  }
  if (lower.includes("llmcallfailed")) {
    return "AI 调用失败";
  }
  // 兜底:截到 30 字
  return raw.length > 30 ? raw.slice(0, 30) + "…" : raw;
}

export function formatExtractEvent(
  ev: ExtractEvent,
  idx: number,
): FormattedExtractEvent | null {
  const baseKey = `${ev._ts ?? Date.now()}-${idx}`;

  switch (ev.kind) {
    case "snapshot":
      // snapshot 一般不显(SSE 模式);但 polling fallback 第一次会推一个,
      // 此时显一个"已连接"提示,让用户立刻看到平台没卡死
      return {
        key: baseKey,
        indent: 0,
        icon: "·",
        main: `已就绪 — ${STATE_LABEL[ev.state ?? ""] ?? ev.state ?? ""}`,
        sub: "",
        tone: "normal",
      };
    case "state_change":
      return {
        key: baseKey,
        indent: 0,
        icon: "▸",
        main: STATE_LABEL[ev.state ?? ""] ?? ev.state ?? "",
        sub: "",
        tone: "normal",
      };
    case "extract_graph_start":
      return {
        key: baseKey,
        indent: 1,
        icon: "·",
        // FOCUS.9(2026-05-22):文案改为用户视角 — "拆分原作"而非技术词"块"
        main: `已切成 ${ev.total_chunks ?? "?"} 段(总 ${(ev.total_chars ?? 0).toLocaleString()} 字)`,
        sub: "开始逐段扫人物 / 关系 / 事件…",
        tone: "normal",
      };
    case "chunk_done": {
      // FOCUS.9(2026-05-22):"第 N/9 块完成 tokens X→Y" → "第 N/9 段:扫出 P 角色 / R 关系"
      const persons = ev.chunk_persons_count ?? 0;
      const rels = ev.chunk_relations_count ?? 0;
      const tokIn = (ev.tokens_input ?? 0).toLocaleString();
      const tokOut = (ev.tokens_output ?? 0).toLocaleString();
      const hasChunkStats = ev.chunk_persons_count !== undefined;
      return {
        key: baseKey,
        indent: 1,
        icon: "✓",
        main: `第 ${ev.chunk_index} / ${ev.total_chunks} 段扫完`
          + (hasChunkStats ? ` — ${persons} 角色 / ${rels} 关系` : ""),
        sub: `累计 tokens ${tokIn} → ${tokOut}`,
        tone: "success",
      };
    }
    case "chunk_failed":
      return {
        key: baseKey,
        indent: 1,
        icon: "✗",
        main: `第 ${ev.chunk_index} / ${ev.total_chunks} 段跳过(其它段继续)`,
        sub: ev.message ? humanizeChunkError(ev.message) : "AI 输出格式异常",
        tone: "error",
      };
    case "chunk_skipped":
      // 断点续抽:命中已完成块 → 免抽,显恢复提示
      return {
        key: baseKey,
        indent: 1,
        icon: "↻",
        main: `第 ${ev.chunk_index} / ${ev.total_chunks} 段已恢复(免再抽)`,
        sub: "",
        tone: "success",
      };
    case "extract_graph_done": {
      // FOCUS.9(2026-05-22):若漏抽检测发现 candidate,在 sub 行告警
      const missed = ev.missed_persons ?? [];
      const missedHint = missed.length > 0
        ? `⚠ 可能漏抽:${missed.slice(0, 5).join(" / ")}${missed.length > 5 ? "…" : ""}(抽完后可在主角面板手动添加)`
        : "下一步:为主要角色生成详细档案";
      return {
        key: baseKey,
        indent: 1,
        icon: "✓",
        main: `跨段去重完成 — ${ev.entities_count ?? 0} 角色 / ${ev.relations_count ?? 0} 关系`,
        sub: missedHint,
        tone: missed.length > 0 ? "warning" : "success",
      };
    }
    case "entities_pending_review": {
      // FOCUS.10(2026-05-22):暂停在用户审核阶段
      const personCount = (ev.persons ?? []).length;
      const missed = ev.missed_persons ?? [];
      return {
        key: baseKey,
        indent: 0,
        icon: "⏸",
        main: `等用户审核 ${personCount} 位角色`,
        sub: missed.length > 0
          ? `⚠ 可能漏抽:${missed.slice(0, 3).join(" / ")}${missed.length > 3 ? "…" : ""}`
          : "在弹窗里审核 + / − 角色后,点「通过」继续",
        tone: "warning",
      };
    }
    case "characters_start":
      return {
        key: baseKey,
        indent: 1,
        icon: "·",
        main: `开始为 ${ev.total_profiles ?? 0} 位主要角色写档案`,
        sub: (ev.names ?? []).join(" / "),
        tone: "normal",
      };
    case "profile_start":
      return {
        key: baseKey,
        indent: 1,
        icon: "▸",
        main: `${ev.character_name} — 生成详细档案(${ev.profile_index} / ${ev.total_profiles})`,
        sub: "",
        tone: "normal",
      };
    case "profile_done":
      return {
        key: baseKey,
        indent: 1,
        icon: "✓",
        main: `${ev.character_name} 档案完成`,
        sub: "",
        tone: "success",
      };
    case "profile_failed":
      return {
        key: baseKey,
        indent: 1,
        icon: "✗",
        main: `${ev.character_name} 档案失败,降级用 entity`,
        sub: "",
        tone: "error",
      };
    case "life_status_done":
      // P0K.1(2026-05-24):生命状态判定进度 — 保持 SSE 通道活跃 + UI 可视化
      // 状态友好文案
      // eslint-disable-next-line no-case-declarations
      const statusLabel = ({
        alive: "在世",
        deceased: "已逝",
        in_facility: "在异地",
        absent: "离开主线",
        unknown: "状态未知",
      }[(ev.life_status ?? "alive") as string] ?? ev.life_status ?? "");
      return {
        key: baseKey,
        indent: 1,
        icon: "✓",
        main: `${ev.name ?? "?"} 生命状态判定:${statusLabel}(${ev.progress ?? 0} / ${ev.total ?? 0})`,
        sub: "",
        tone: ev.life_status === "deceased" ? "warning" : "success",
      };
    case "gender_audit_done": {
      // P0M.1(2026-05-24):性别审计进度 — 保持 SSE 通道活跃 + UI 可视化
      const verdictLabel: Record<string, string> = {
        correct: "性别正确",
        wrong: "性别已修正",
        no_evidence: "无原文证据",
        skipped: "跳过(无触发词)",
        error: "审计失败",
      };
      const v = ev.verdict ?? "skipped";
      return {
        key: baseKey,
        indent: 1,
        icon: v === "wrong" ? "✦" : "✓",
        main: `${ev.name ?? "?"} 性别审计:${verdictLabel[v] ?? v}(${ev.progress ?? 0} / ${ev.total ?? 0})`,
        sub: "",
        tone: v === "wrong" ? "warning" : "success",
      };
    }
    case "missed_protagonist_warning": {
      // P0L.3(2026-05-24):主角缺位 post-check 警告
      const missedList = ev.missed_persons ?? [];
      const previewNames = missedList.slice(0, 3).join(" / ");
      const moreCount = missedList.length > 3 ? `+${missedList.length - 3} 个` : "";
      return {
        key: baseKey,
        indent: 0,
        icon: "⚠",
        main: `疑似主角漏抽(第三人称作品):${previewNames}${moreCount}`,
        sub: "可在「主角面板」手动添加这些角色,确保 AI 续写不漏",
        tone: "warning",
      };
    }
    case "minimal_batch_start":
      return {
        key: baseKey,
        indent: 0,
        icon: "▸",
        main: `批量补全配角档案 — 共 ${ev.total_remaining_persons ?? 0} 人 / ${ev.total_batches ?? 0} 批`,
        sub: "",
        tone: "normal",
      };
    case "minimal_batch_done":
      return {
        key: baseKey,
        indent: 1,
        icon: "✓",
        main: `第 ${ev.batch_index} / ${ev.total_batches} 批完成 — 补 ${ev.persons_filled ?? 0} 个配角`,
        sub: `tokens ${(ev.tokens_input ?? 0).toLocaleString()} → ${(ev.tokens_output ?? 0).toLocaleString()}`,
        tone: "success",
      };
    case "minimal_batch_failed":
      return {
        key: baseKey,
        indent: 1,
        icon: "✗",
        main: `第 ${ev.batch_index} / ${ev.total_batches} 批失败,该批 ${ev.persons_count ?? 0} 配角降级 identity-only`,
        sub: ev.message ? humanizeChunkError(ev.message) : "AI 调用失败",
        tone: "error",
      };
    case "meta_inferred":
      return {
        key: baseKey,
        indent: 1,
        icon: "✓",
        main: `识别 ${ev.inferred_custom_type_name || ev.inferred_type || "?"}`,
        sub: (ev.inferred_tags ?? []).join(" / "),
        tone: "success",
      };
    case "save_done":
      return {
        key: baseKey,
        indent: 1,
        icon: "✓",
        main: `落库 ${ev.characters_count} 角色 / ${ev.relationships_count} 关系 / ${ev.events_count} 事件`,
        sub: ev.skipped_count ? `跳过 ${ev.skipped_count}(项目内同名保留)` : "",
        tone: "success",
      };
    case "done":
      return {
        key: baseKey,
        indent: 0,
        icon: "✦",
        main: `抽取完成 — 总计 ${ev.characters_count} 角色 / ${ev.relationships_count} 关系 / ${ev.events_count} 事件`,
        sub: ev.cost_yuan !== undefined ? `成本 ¥${ev.cost_yuan.toFixed(4)}` : "",
        tone: "success",
      };
    case "error":
      return {
        key: baseKey,
        indent: 0,
        icon: "✗",
        main: ev.message ?? "抽取失败",
        sub: "",
        tone: "error",
      };
    case "polling_tick":
      // 降级 polling 模式合成的"心跳":显当前阶段 + 已用 tokens
      return {
        key: baseKey,
        indent: 1,
        icon: "⋯",
        main: STATE_LABEL[ev.state ?? ""] ?? ev.state ?? "处理中",
        sub: `tokens ${(ev.tokens_input ?? 0).toLocaleString()} → ${(ev.tokens_output ?? 0).toLocaleString()}`,
        tone: "normal",
      };
    default:
      return null;
  }
}
