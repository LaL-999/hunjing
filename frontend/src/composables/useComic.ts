/**
 * useComic — D.9 Sprint 2.B 漫画态状态管理 composable
 *
 * 类比 useSimulation 模式,但漫画态没有 SSE(Sprint 2.A 是同步执行 LLM,2.B 也是)。
 * 用 polling 看 state 推进(每 2s 拉一次 /api/comics/{id},直到 state 进入"等待用户"
 * 或终态)。
 *
 * 用法:
 *   const { comic, loading, error, load, createComic, uploadReferences, voteStyle, cancel }
 *     = useComic(() => comicId);  // 接 getter 防 view 实例复用 bug(执行纪律 1)
 */
import { computed, ref, watch } from "vue";

import { api } from "../api/client";
import { ApiError } from "../api/types";
import type {
  Comic,
  ComicCancelResponse,
  ComicCancelRefund,
  ComicExportResult,
  ComicPageRecord,
  ComicSource,
  ComicState,
  ReadyUploadItem,
} from "../api/types";

// ============================================================
// Polling 间隔:state 推进中每 2s 拉一次
// ============================================================

const POLL_INTERVAL_MS = 2000;

/** state 是否"worker 推进中"(轮询的依据) */
function isInProgress(state: ComicState): boolean {
  return (
    state === "scripting"
    || state === "extracting_visuals"
    || state === "style_analyzing"
    || state === "character_anchoring"
    || state === "designing"
    || state === "generating"
    || state === "composing"
  );
}

/** state 是否终态(停轮询) */
function isTerminal(state: ComicState): boolean {
  return state === "done" || state === "failed" || state === "cancelled";
}

// ============================================================
// composable
// ============================================================

export function useComic(getComicId: () => string | null) {
  const comic = ref<Comic | null>(null);
  const loading = ref(false);
  const error = ref<string | null>(null);

  let pollTimer: ReturnType<typeof setTimeout> | null = null;

  function clearPollTimer() {
    if (pollTimer) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  function schedulePoll() {
    clearPollTimer();
    pollTimer = setTimeout(() => {
      void load();
    }, POLL_INTERVAL_MS);
  }

  async function load(): Promise<void> {
    const id = getComicId();
    if (!id) {
      comic.value = null;
      clearPollTimer();
      return;
    }
    // P-6 修复(2026-05-23):stale-while-revalidate — 首次加载才显 loading,
    // 切 comic / 刷新时保留旧 comic 数据无缝替换,避免 v-if 闪
    const isFirstLoad = comic.value === null;
    if (isFirstLoad) loading.value = true;
    try {
      const data = await api.get<Comic>(`/comics/${id}`);
      comic.value = data;
      error.value = null;
      // Sprint 5.x bug fix(2026-05-14):polling 触发条件去 is_alive 依赖
      // 根因:router 内 sync agent(_agent_scripter / _agent_visual_assets_extractor /
      //       _agent_style_director_v2 / _agent_character_anchor)未注册 _RUNNING_COMICS
      //       → is_alive=False → polling 永不启动 → 用户看到 dialog "请稍候..." 不变
      //       → 误以为网卡;退出再进时刚好 generating 阶段(已注册)才看到推进
      // 修法:只看 state in_progress,zombie 检测让 connection 错误自然停 polling
      if (isInProgress(data.state)) {
        schedulePoll();
      } else {
        clearPollTimer();
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        comic.value = null;
        error.value = "漫画不存在或不属于你";
      } else {
        error.value = e instanceof ApiError ? e.message : "加载失败";
      }
      clearPollTimer();
    } finally {
      if (loading.value) loading.value = false;
    }
  }

  watch(getComicId, () => {
    void load();
  }, { immediate: true });

  // ============================================================
  // Actions
  // ============================================================

  /** 创建漫画 — 返回新 comic.id 或 throw */
  async function createComic(name: string, source: ComicSource): Promise<Comic> {
    return api.post<Comic>("/comics", { name, source });
  }

  /**
   * 上传参考图 → 触发画风定调员
   *
   * Sprint 5.4.1(2026-05-13):接受 0 张(跳过模式)或 3 张(有图模式)
   *   - 3 张:Qwen-VL 提 DNA + DeepSeek 综合
   *   - 0 张:跳过 Qwen-VL,DeepSeek 仅从剧本题材推画风
   */
  async function uploadReferences(imageUrls: string[]): Promise<void> {
    const id = getComicId();
    if (!id) throw new Error("comic id 未指定");
    if (imageUrls.length !== 0 && imageUrls.length !== 3) {
      throw new Error("image_urls 必须是 0 张(跳过模式)或 3 张(有图模式)");
    }
    loading.value = true;
    error.value = null;
    try {
      const updated = await api.post<Comic>(
        `/comics/${id}/upload_references`,
        { image_urls: imageUrls },
      );
      comic.value = updated;
    } catch (e) {
      if (e instanceof ApiError) {
        const detail = (e.detail as { message?: string } | null)?.message;
        error.value = detail || e.message;
      } else {
        error.value = "提交失败";
      }
      throw e;
    } finally {
      loading.value = false;
    }
  }

  /**
   * Sprint 5.10+(2026-05-14):重试 Stage 3 出新的 3 张候选(不取消漫画)。
   *
   * 用户场景:dialog 显示"3 张全失败",过去只能"取消漫画 → 重新上传参考图"
   * 重走 30-60s 全流程。现在只重画 3 张图(~10-30s),省 Qwen-VL DNA + DeepSeek 综合时间。
   *
   * 后端:POST /api/comics/{id}/retry_style_candidates
   *   - 409 INVALID_STATE_FOR_RETRY:state ≠ style_voting
   *   - 503 RETRY_STILL_FAILED:重试仍全失败(error details 在 detail.errors)
   */
  async function retryStyleCandidates(): Promise<void> {
    const id = getComicId();
    if (!id) throw new Error("comic id 未指定");
    loading.value = true;
    error.value = null;
    try {
      const updated = await api.post<Comic>(
        `/comics/${id}/retry_style_candidates`, {},
      );
      comic.value = updated;
    } catch (e) {
      if (e instanceof ApiError) {
        const detail = (e.detail as { message?: string } | null)?.message;
        error.value = detail || e.message;
      } else {
        error.value = "重试失败";
      }
      throw e;
    } finally {
      loading.value = false;
    }
  }

  /** 3 选 1 投票 → 触发角色锚定员(Sprint 2.B+ 七修:5 → 3) */
  async function voteStyle(selectedIndex: number): Promise<void> {
    const id = getComicId();
    if (!id) throw new Error("comic id 未指定");
    if (selectedIndex < 1 || selectedIndex > 3) {
      throw new Error("selected_index 必须在 1-3");
    }
    loading.value = true;
    error.value = null;
    try {
      const updated = await api.post<Comic>(
        `/comics/${id}/vote_style`,
        { selected_index: selectedIndex },
      );
      comic.value = updated;
    } catch (e) {
      if (e instanceof ApiError) {
        const detail = (e.detail as { message?: string } | null)?.message;
        error.value = detail || e.message;
      } else {
        error.value = "投票失败";
      }
      throw e;
    } finally {
      loading.value = false;
    }
  }

  /**
   * 取消漫画 — Sprint 2.B+ 四修返回退款信息
   *
   * 后端响应结构变 {comic, refund},refund 含 phase / units / label。
   * 返回 refund 供调用方 toast 用户(按档显示"全额/半额/未退回")。
   */
  async function cancel(): Promise<ComicCancelRefund> {
    const id = getComicId();
    if (!id) throw new Error("comic id 未指定");
    try {
      const resp = await api.post<ComicCancelResponse>(
        `/comics/${id}/cancel`, {},
      );
      comic.value = resp.comic;
      return resp.refund;
    } catch (e) {
      error.value = e instanceof ApiError ? e.message : "取消失败";
      throw e;
    }
  }

  /** 删除漫画(成功后清空 comic) */
  async function remove(): Promise<void> {
    const id = getComicId();
    if (!id) throw new Error("comic id 未指定");
    await api.delete(`/comics/${id}`);
    comic.value = null;
    clearPollTimer();
  }

  /**
   * 拉漫画所有 page(供网格预览 / 阅读器复用)— Sprint 4.A 从 ComicProjectView 内联抽出
   *
   * 不更新 composable 内部 state,纯返回数据;失败抛 ApiError(调用方自决 toast / 兜底)。
   * comic.state ≠ 'done' 时后端可能返回空数组,前端容错;不在此层判定 state。
   */
  async function fetchComicPages(): Promise<ComicPageRecord[]> {
    const id = getComicId();
    if (!id) throw new Error("comic id 未指定");
    return api.get<ComicPageRecord[]>(`/comics/${id}/pages`);
  }

  /**
   * Sprint 4.D(2026-05-13):导出整本漫画为 PDF / ZIP。
   *
   * 后端响应 header X-Missing-Pages 含 typesetter 失败的页号(如 "2,4"),
   * 调用方可据此 toast 提示用户(不阻塞下载,部分页缺失仍返回有效 PDF/ZIP)。
   *
   * filename 取自当前 comic.name(已经在后端 sanitize);未加载时用 "comic" 兜底。
   * 调用方负责触发 <a download> 点击 + URL.revokeObjectURL 清理。
   *
   * raises ApiError(422 COMIC_NOT_EXPORTABLE / EXPORT_NOTHING_TO_DO / 401 / 404)
   */
  async function exportComic(format: "pdf" | "zip"): Promise<ComicExportResult> {
    const id = getComicId();
    if (!id) throw new Error("comic id 未指定");
    const ext = format === "pdf" ? "pdf" : "zip";
    const { blob, headers } = await api.downloadFile(`/comics/${id}/export.${ext}`);
    const baseName = comic.value?.name || "comic";
    // 后端 sanitize 已删非法字符,前端仍兜底一次(防 manual 注入)
    const safeName = baseName.replace(/[<>:"/\\|?*\x00-\x1f]/g, "").trim() || "comic";
    const filename = `${safeName}.${ext}`;
    const missingHeader = headers.get("X-Missing-Pages");
    const missingPages = missingHeader
      ? missingHeader.split(",").map((s) => parseInt(s, 10)).filter((n) => !isNaN(n))
      : [];
    return { blob, filename, missingPages };
  }

  /**
   * Sprint 4.D:拉当前用户所有 ready uploads(供 CreateComicModal external tab)。
   *
   * 不挂任何 project_id —— 全局跨项目查询(漫画态用户视角不感知"项目"概念)。
   * 失败抛 ApiError;空列表合法(用户无 ready upload → modal 显引导)。
   */
  async function fetchReadyUploads(): Promise<ReadyUploadItem[]> {
    return api.get<ReadyUploadItem[]>("/uploads/ready");
  }

  // ============================================================
  // 派生 state(给 view 用)
  // ============================================================

  const isWaitingUser = computed<boolean>(() => {
    const s = comic.value?.state;
    return s === "style_uploading" || s === "style_voting";
  });

  const isRunning = computed<boolean>(() => {
    const s = comic.value?.state;
    return s ? isInProgress(s) : false;
  });

  const isTerminalState = computed<boolean>(() => {
    const s = comic.value?.state;
    return s ? isTerminal(s) : false;
  });

  /**
   * Sprint 5.x bug fix(2026-05-14):显式强制 polling(独立于 load() 内的 state 判断)。
   *
   * 适用场景:dialog 期间 router 内 sync agent(scripter/extracting/style/anchor)
   * 串行执行 30-60s,初始拉到的 state 可能还是 queued / style_uploading(in_progress=False
   * → load 内部不 schedulePoll → polling 永不启动)。
   *
   * 这里**用独立 setInterval 强制 polling**,不依赖 load() 内的 state 判断。
   * 每 POLL_INTERVAL_MS 拉一次,直到 stopPolling 被调或 dispose。
   *
   * 调用约定:
   *   - handleUploadSubmit / handleVoteSubmit 入口:startPolling()
   *   - finally 块:stopPolling()(立即停;state 进入 in_progress 后 load 内部
   *     schedulePoll 会接管自动 polling 链)
   */
  let forcePollTimer: ReturnType<typeof setInterval> | null = null;

  function startPolling() {
    if (forcePollTimer) return;   // 已在 polling
    // 立刻拉一次 + 启动定时
    void load();
    forcePollTimer = setInterval(() => { void load(); }, POLL_INTERVAL_MS);
  }

  function stopPolling() {
    if (forcePollTimer) {
      clearInterval(forcePollTimer);
      forcePollTimer = null;
    }
  }

  // 清理(view 卸载时调)
  function dispose() {
    clearPollTimer();
    stopPolling();   // Sprint 5.x:也清强制 polling timer
  }

  return {
    comic,
    loading,
    error,
    isWaitingUser,
    isRunning,
    isTerminalState,
    load,
    createComic,
    uploadReferences,
    retryStyleCandidates,
    voteStyle,
    cancel,
    remove,
    fetchComicPages,
    exportComic,
    startPolling,
    stopPolling,
    fetchReadyUploads,
    dispose,
  };
}
