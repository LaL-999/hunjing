<script setup lang="ts">
/**
 * ProjectUploadsPanel — 项目作品文件上传 + 抽图谱触发 / 取消 / 继续面板
 * (Sprint 2.A + 2.B + 2.B+ 断点续抽)。
 *
 * 中间态主路径:
 *   1. 上传 .txt / .epub / .docx → 后端解析 + 红旗扫描 → uploads.state='parsed'
 *   2. 用户点「✦ AI 抽图谱」→ POST /uploads/{id}/extract → 后台 LLM 抽人物/关系/事件
 *      → 落项目 + 回填 type/tags → uploads.state='ready'
 *   3. 父组件检测到 ready 后切换 ProjectView 显示 read-only 抽出结果
 *
 * 三态 UX(2.B+):
 *   - 主动抽取中(本会话):事件流面板 + 头部「取消」按钮
 *   - 僵尸态(backend 重启 / 用户断网):「继续抽取」+「重新开始」两个按钮
 *   - 可恢复失败(reset 后):同上
 *   - 不可恢复失败(LLM 全炸):「重新抽取」按钮
 *   - 进项目时若有 extracting 且 worker 活着 → 自动 SSE 订阅恢复进度可视
 *
 * Props:
 *   projectId  string
 *
 * Emits:
 *   uploaded         新文件上传成功(父可据此提示)
 *   extract-done     抽取完成(父需 reload 项目数据,展示新 characters/relationships/events)
 */
import { computed, onMounted, onUnmounted, ref, watch } from "vue";

import { api } from "../api/client";
import {
  ApiError,
  type ExtractJobResponse,
  type QuotaExceededDetail,
  type UploadResponse,
  type UploadRedFlagDetail,
} from "../api/types";
import { confirm as confirmDialog } from "../composables/useConfirm";
import { toast } from "../composables/useToast";
import Icon from "./Icon.vue";
import EntityReviewModal from "./EntityReviewModal.vue";
import {
  formatExtractEvent,
  useExtractJob,
  type FormattedExtractEvent,
} from "../composables/useExtractJob";
import { useAddonModal } from "../composables/useAddonModal";
import { useUpgradeModal } from "../composables/useUpgradeModal";

const props = defineProps<{
  projectId: string;
}>();

const emit = defineEmits<{
  (e: "uploaded", upload: UploadResponse): void;
  (e: "extract-done", job: ExtractJobResponse): void;
  /**
   * Sprint 6.A2 FOCUS.5(2026-05-22):upload 被删除 — 父组件需要
   * reload 全部(characters / relationships / events / scenes / project meta),
   * 因为后端 delete_upload 已级联清除所有衍生数据。
   */
  (e: "deleted", uploadId: string): void;
}>();

const uploads = ref<UploadResponse[]>([]);
/** upload_id → 该 upload 最新的一条 extract job(给三态 UX 决定按钮用) */
const latestJobByUpload = ref<Record<string, ExtractJobResponse>>({});
const loading = ref(false);
/**
 * Sprint 6.A2 polish(2026-05-22):首次加载完成闸门
 * 切 tab / 切项目时本组件被重新 mount,uploads 初始为空 → 模板 v-if="!hasUploads"
 * 会立即显示"上传文件入口" drop-zone(空态)→ fetch 完成后才切到 uploads-list,中间闪一帧。
 * 解决:drop-zone 改为 `v-if="initialLoadDone && !hasUploads"` —— fetch 真正完成才允许判定"空"。
 */
const initialLoadDone = ref(false);
const uploading = ref(false);
const fileInputRef = ref<HTMLInputElement | null>(null);

const ACCEPT = ".txt,.epub,.docx";

// 2.B 抽取任务状态(单 active job — 同 upload 同时只能 1 个)
const extractJob = useExtractJob();
const extractingUploadId = ref<string | null>(null);
const upgradeModal = useUpgradeModal();
const addonModal = useAddonModal();

// 抽取阶段 → 用户可读文案(顶部显当前 state)
// Sprint 6.A2 FOCUS.10(2026-05-22):migration 056 加 entities_pending_review state
const EXTRACT_STATE_LABEL: Record<ExtractJobResponse["state"], string> = {
  queued: "排队中",
  extracting_graph: "AI 在拆人物 / 关系…",
  entities_pending_review: "等你确认实体合并…",
  generating_characters: "AI 在为主角写档案…",
  inferring_meta: "AI 在识别作品类型 / 题材…",
  saving: "保存到项目中…",
  done: "完成",
  failed: "失败",
};

/** 实时事件流(SSE 推过来的)— 倒序最新在上,最多 30 条 */
const formattedExtractEvents = computed<FormattedExtractEvent[]>(() => {
  const out: FormattedExtractEvent[] = [];
  const arr = extractJob.events.value;
  for (let i = arr.length - 1; i >= 0 && out.length < 30; i--) {
    const f = formatExtractEvent(arr[i], i);
    if (f) out.push(f);
  }
  return out;
});

// ============================================================
// 拉列表 + 拉 jobs(三态 UX 数据基础)
// ============================================================

async function loadUploads(): Promise<void> {
  // Sprint 6.A2 polish(2026-05-22):stale-while-revalidate + initialLoadDone 闸门
  // - 首次加载:loading=true 显示"加载中…"占位(模板 L634)
  // - refetch:保留旧 uploads,reactive swap 平滑替换,避免列表"消失再出现"
  // - 失败时保留旧数据(用户不丢上下文,只 toast 提示)
  const isFirstLoad = !initialLoadDone.value;
  if (isFirstLoad) loading.value = true;
  try {
    uploads.value = await api.get<UploadResponse[]>(
      `/projects/${props.projectId}/uploads`,
    );
  } catch (e) {
    if (e instanceof ApiError) {
      toast.error("无法加载作品文件,请刷新重试");
    }
    // 不清空 uploads(保留旧数据);只首次加载失败时安全清(此时 uploads 本来就空)
    if (isFirstLoad) uploads.value = [];
  } finally {
    if (isFirstLoad) loading.value = false;
    initialLoadDone.value = true;
  }
}

async function loadLatestJobs(): Promise<void> {
  try {
    const jobs = await api.get<ExtractJobResponse[]>(
      `/projects/${props.projectId}/extract_jobs`,
    );
    // jobs 已按 started_at DESC 排序 → 第一次见到某 upload_id 即最新
    const map: Record<string, ExtractJobResponse> = {};
    for (const j of jobs) {
      if (!(j.upload_id in map)) {
        map[j.upload_id] = j;
      }
    }
    latestJobByUpload.value = map;
  } catch (e) {
    // jobs 拉失败不阻塞主流程,只是三态信息缺失
    if (e instanceof ApiError) {
      if (import.meta.env.DEV) {
        console.warn("[ProjectUploadsPanel] 拉 extract_jobs 失败,三态 UX 降级", e);
      }
    }
  }
}

async function reloadAll(): Promise<void> {
  await Promise.all([loadUploads(), loadLatestJobs()]);
  // 自动订阅:若有 upload.state='extracting' 且 worker 活着 → SSE 接进度
  await maybeAutoResubscribe();
}

/** 进项目时:若有任意 upload 状态是 extracting + 对应 job 活着 → 自动 SSE 订阅,
 * 让用户立刻看到当前进度而不需手动操作。
 */
async function maybeAutoResubscribe(): Promise<void> {
  if (extractingUploadId.value) return;   // 已在订阅另一个,不抢
  for (const u of uploads.value) {
    if (u.state !== "extracting") continue;
    const job = latestJobByUpload.value[u.id];
    if (!job || !job.is_alive) continue;
    if (["queued", "extracting_graph", "generating_characters",
         "inferring_meta", "saving"].includes(job.state)) {
      extractingUploadId.value = u.id;
      await extractJob.subscribe(job.id);
      return;
    }
  }
}

onMounted(reloadAll);

// ============================================================
// 上传
// ============================================================

function triggerFilePicker() {
  fileInputRef.value?.click();
}

async function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  // 重置 input 让用户能再选同一个文件(浏览器默认相同 file 不触发 change)
  input.value = "";
  if (!file) return;
  await uploadFile(file);
}

async function onDrop(e: DragEvent) {
  e.preventDefault();
  const file = e.dataTransfer?.files?.[0];
  if (!file) return;
  await uploadFile(file);
}

function onDragOver(e: DragEvent) {
  e.preventDefault();
}

async function uploadFile(file: File) {
  if (uploading.value) {
    toast.info("正在上传上一个文件,请稍候");
    return;
  }
  uploading.value = true;
  try {
    const fd = new FormData();
    fd.append("file", file);
    const result = await api.post<UploadResponse>(
      `/projects/${props.projectId}/uploads`,
      fd,
    );
    uploads.value = [result, ...uploads.value];
    toast.success(`已上传《${result.filename}》— 解析 ${result.parsed_text_chars ?? 0} 字`);
    emit("uploaded", result);
  } catch (e) {
    if (e instanceof ApiError) {
      handleUploadError(e);
    } else {
      toast.error("上传失败:网络异常");
    }
  } finally {
    uploading.value = false;
  }
}

const RED_FLAG_CATEGORY_LABEL: Record<string, string> = {
  political: "敏感政治内容",
  violence: "恐怖暴力内容",
  sexual: "极端色情内容",
  privacy: "隐私信息(身份证 / 银行卡 等)",
};

function handleUploadError(e: ApiError) {
  const code = e.code;
  if (code === "UPLOAD_FORMAT_REJECTED") {
    toast.warning(e.message, 5000);
  } else if (code === "UPLOAD_DUPLICATE") {
    toast.info("这个文件你已经上传过了(同内容不重复存)", 4000);
  } else if (code === "PROJECT_ALREADY_HAS_UPLOAD") {
    // Sprint 6.A2 M7.E(2026-05-20)— 通常 UI 已隐藏 dropzone 不会到这,
    // 但旧 tab 没刷新撞到时仍要给用户清晰文案 + 引导
    toast.warning(
      "本项目已有作品,一个项目只能装一部作品。" +
      "想替换 → 点已有文件旁的「↻ 重新上传」;想换世界 → 新建项目",
      7000,
    );
  } else if (code === "UPLOAD_PARSE_FAILED") {
    toast.error("文件解析失败,可换格式或重试", 6000);
  } else if (code === "UPLOAD_RED_FLAG") {
    const detail = e.detail as UploadRedFlagDetail | null;
    const label = detail?.category
      ? RED_FLAG_CATEGORY_LABEL[detail.category] ?? "敏感内容"
      : "敏感内容";
    toast.error(
      `内容含${label},无法上传。如认为是误判,请改一改原文再试。`,
      6500,
    );
  } else {
    toast.error("上传失败,请稍后再试");
  }
}

// ============================================================
// 抽图谱(Sprint 2.B)+ 取消 / 继续(Sprint 2.B+)
// ============================================================

async function handleExtract(upload: UploadResponse) {
  if (extractingUploadId.value && extractingUploadId.value !== upload.id) {
    toast.info("正在抽取另一个文件,请等当前任务完成");
    return;
  }
  // 已 ready 的文件:确认重抽(扣 1 次 continuation 配额)
  if (upload.state === "ready") {
    const ok = await confirmDialog({
      title: `重新抽取《${upload.filename}》的图谱?`,
      message:
        "项目里已有同名角色将保留(不会被覆盖),只新增 AI 这次发现的新角色。会扣 1 次本月推演配额。",
      danger: false,
      confirmLabel: "重抽",
    });
    if (!ok) return;
  }
  extractingUploadId.value = upload.id;
  await extractJob.start(upload.id);

  if (extractJob.phase.value === "error") {
    // Sprint C.3:credit 不足 → 加购(更贴当下);资源容量类 → 升档
    if (extractJob.errorCode.value === "INSUFFICIENT_CREDITS") {
      addonModal.open();
    } else if (extractJob.errorCode.value === "QUOTA_EXCEEDED") {
      upgradeModal.open(extractJob.errorDetail.value as QuotaExceededDetail);
    } else {
      toast.error("AI 抽取启动失败,请稍后再试");
    }
    extractingUploadId.value = null;
    return;
  }

  if (extractJob.phase.value === "done") {
    handleExtractDone();
  } else if (extractJob.phase.value === "failed") {
    handleExtractFailed();
  }
  // running 态由 SSE 驱动,下面的 watch 监听 phase 变化触发终态处理
}

/** 用户主动取消当前抽取(也用于解除僵尸态)。 */
async function handleCancel(upload: UploadResponse) {
  const job = latestJobByUpload.value[upload.id];
  if (!job) {
    toast.warning("找不到对应的抽取任务");
    return;
  }
  const ok = await confirmDialog({
    title: `取消《${upload.filename}》的抽取?`,
    message:
      "已抽完的部分会保留 — 后续可点「继续抽取」从断点恢复,不浪费已花的成本。\n" +
      "本次配额已扣,不退回(LLM 已为已抽部分付费)。",
    danger: false,
    confirmLabel: "取消抽取",
  });
  if (!ok) return;
  try {
    await extractJob.cancel(job.id);
    toast.success("已取消;可点「继续抽取」从断点恢复");
    extractingUploadId.value = null;
    await reloadAll();
  } catch (e) {
    toast.error("取消失败,请稍后再试");
  }
}

/** 用户从断点继续抽取(2.B+)。 */
async function handleResume(upload: UploadResponse) {
  const job = latestJobByUpload.value[upload.id];
  // 2026-06-05:校验对齐按钮显示规则 — 只要有 chunks 就放行
  // (老逻辑只看 job.resumable,但 backend 在 state≠failed 时把 resumable 算成 false,
  //  导致 UI 显示「继续抽取」按钮但点了被拒绝的死锁)
  if (!job || job.completed_chunks_count === 0) {
    toast.warning("当前没有可恢复的断点,请走「重新开始」");
    return;
  }
  if (extractingUploadId.value && extractingUploadId.value !== upload.id) {
    toast.info("正在抽取另一个文件,请等当前任务完成");
    return;
  }
  extractingUploadId.value = upload.id;
  try {
    await extractJob.resume(job.id);
    toast.info(
      `从第 ${job.completed_chunks_count + 1} 块继续 — 已恢复 ${job.completed_chunks_count} 块成本`,
      4000,
    );
  } catch (e) {
    if (e instanceof ApiError) {
      toast.error("继续抽取失败,请稍后再试");
    }
    extractingUploadId.value = null;
    return;
  }

  if (extractJob.phase.value === "done") {
    handleExtractDone();
  } else if (extractJob.phase.value === "failed") {
    handleExtractFailed();
  }
}

/** 重新开始 = 先 reset 旧 job(若僵尸 / failed)+ 触发全新 trigger(扣新配额)。 */
async function handleRestart(upload: UploadResponse) {
  const job = latestJobByUpload.value[upload.id];
  const ok = await confirmDialog({
    title: `重新开始抽取《${upload.filename}》?`,
    message:
      "会丢弃当前任务的所有进度从头开始,**扣 1 次新配额**。\n" +
      "如果之前抽过一部分,建议改用「继续抽取」节省成本。",
    danger: false,
    confirmLabel: "重新开始",
  });
  if (!ok) return;

  // 1. 若旧 job 还在 extracting_*,先 reset(后端会拒已 done/failed,我们不管)
  if (job && ["queued", "extracting_graph", "generating_characters",
              "inferring_meta", "saving"].includes(job.state)) {
    try {
      await extractJob.cancel(job.id);
    } catch (e) {
      if (import.meta.env.DEV) {
        console.warn("[ProjectUploadsPanel] 旧 job reset 失败(继续触发新)", e);
      }
    }
  }
  // 2. 等 upload state 翻回 parsed(reset 后端做了)
  await reloadAll();
  // 3. 触发全新抽取
  const fresh = uploads.value.find((u) => u.id === upload.id);
  if (!fresh) {
    toast.error("作品文件已不存在");
    return;
  }
  if (fresh.state !== "parsed") {
    toast.error("当前状态暂不能重新抽取,请稍后重试");
    return;
  }
  await handleExtract(fresh);
}

function handleExtractDone() {
  if (extractJob.detail.value) {
    emit("extract-done", extractJob.detail.value);
    toast.success(
      `图谱抽取完成 — ${extractJob.detail.value.characters_count} 角色 / ` +
        `${extractJob.detail.value.relationships_count} 关系 / ` +
        `${extractJob.detail.value.events_count} 事件`,
      5000,
    );
  }
  extractingUploadId.value = null;
  void reloadAll();
}

function handleExtractFailed() {
  toast.error("AI 抽取失败,请稍后重试");
  extractingUploadId.value = null;
  void reloadAll();
}

// 监听 SSE 驱动的 phase 变化 → 触发终态副作用
watch(
  () => extractJob.phase.value,
  (newPhase, oldPhase) => {
    if (oldPhase !== "running") return;   // 只关心 running → done/failed 转
    if (newPhase === "done") handleExtractDone();
    else if (newPhase === "failed") handleExtractFailed();
  },
);

onUnmounted(() => {
  extractJob.unsubscribe();
});

// 2026-06-02 hotfix:重入保护,防快速双击触发两次 confirmDialog
const deletingUploadIds = ref<Set<string>>(new Set());

async function handleDelete(upload: UploadResponse) {
  if (deletingUploadIds.value.has(upload.id)) return;
  deletingUploadIds.value.add(upload.id);
  try {
    const ok = await confirmDialog({
      title: `删除《${upload.filename}》?`,
      message: "服务器上的文件 + 解析记录都会被清除,无法恢复。",
      danger: true,
      confirmLabel: "删除",
    });
    if (!ok) return;
    try {
      await api.delete(`/uploads/${upload.id}`);
      uploads.value = uploads.value.filter((u) => u.id !== upload.id);
      delete latestJobByUpload.value[upload.id];
      toast.success("已删除");
      // FOCUS.5(2026-05-22):通知父组件 reload — 后端已级联清角色/关系/事件/场景/项目元数据,
      // 前端必须刷新对应 state,否则旧的"图谱已就绪"banner / 角色卡 / 场景图谱不会消失
      emit("deleted", upload.id);
    } catch (e) {
      toast.error(
        e instanceof ApiError ? `删除失败:${e.message}` : "删除失败",
      );
    }
  } finally {
    deletingUploadIds.value.delete(upload.id);
  }
}


// ============================================================
// 派生 — 三态 UX 按钮决策
// ============================================================

type UploadActionGroup =
  | "first_extract"      // parsed,首次:[✦ AI 抽图谱]
  | "active_running"     // 本会话主动抽取中:无操作按钮(头部有取消)
  | "zombie_resumable"   // extracting/failed 但 worker 死,有 chunks:[继续] [重新开始]
  | "zombie_no_resume"   // extracting/failed 且无 chunks:[重新抽取]
  | "live_other_upload"  // 本会话在抽别的 upload:不显操作
  | "ready"              // 已就绪:[重抽]
  | "rejected_or_failed_other"  // 兜底:[重新抽取]
  ;

function uploadActionGroup(u: UploadResponse): UploadActionGroup {
  if (extractingUploadId.value === u.id) return "active_running";
  if (extractingUploadId.value && extractingUploadId.value !== u.id) {
    return "live_other_upload";
  }

  const job = latestJobByUpload.value[u.id];

  // Bug 修复(2026-05-22):用户主动取消后,后端 reset_extract_job 把
  // upload.state 回滚到 'parsed'(保留 chunk_results 允许 resume),
  // 但 job state='failed' + resumable=true。
  // 老逻辑直接返回 "first_extract" → 显示"AI 抽图谱"按钮 → 用户找不到"继续抽取"入口。
  // 修正:parsed 状态也要检查是否有 resumable 断点
  if (u.state === "parsed") {
    if (job && job.resumable) return "zombie_resumable";
    if (job && job.completed_chunks_count > 0) return "zombie_resumable";
    return "first_extract";
  }
  if (u.state === "ready") return "ready";

  if (u.state === "extracting") {
    // 进项目自动 resubscribe 应该已经把 extractingUploadId 设了 — 走到这里说明 worker 死
    if (job && job.resumable) return "zombie_resumable";
    if (job && job.completed_chunks_count > 0) return "zombie_resumable";
    return "zombie_no_resume";
  }

  if (u.state === "failed") {
    if (job && job.resumable) return "zombie_resumable";
    // 2026-06-05:对称兜底(与 extracting 分支一致)— 后端 resumable 字段可能滞后,
    // 但 completed_chunks_count > 0 已经是事实,直接给"继续抽取"入口
    if (job && job.completed_chunks_count > 0) return "zombie_resumable";
    return "zombie_no_resume";
  }

  return "rejected_or_failed_other";
}

const hasUploads = computed(() => uploads.value.length > 0);

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day} ${hh}:${mm}`;
}

const STATE_LABEL: Record<UploadResponse["state"], string> = {
  uploaded: "已接收",
  parsed: "已解析",
  rejected: "已拒",
  extracting: "抽图谱中",
  ready: "可用",
  failed: "失败",
};

/** 给僵尸 / 失败状态用的更精准 chip 文案 */
function refinedStateLabel(u: UploadResponse): string {
  const job = latestJobByUpload.value[u.id];
  if (u.state === "extracting" && extractingUploadId.value === u.id) {
    return "AI 抽取中";
  }
  if (u.state === "extracting" && job && !job.is_alive) {
    return "已中断";
  }
  if (u.state === "failed" && job?.resumable) {
    return "已中断";
  }
  return STATE_LABEL[u.state];
}
</script>

<template>
  <section class="uploads-section">
    <header class="section-header">
      <h2 class="section-title">
        作品文件
        <span class="count">{{ uploads.length }}</span>
      </h2>
      <p class="section-hint">
        上传 .txt / .epub / .docx(≤ 100MB)
      </p>
    </header>

    <!-- 拖拽 + 点击上传区 — Sprint 6.A2 M7.E(2026-05-20):已有 upload 时整组隐藏,
         避免用户误传第二本不同作品(项目即作品世界,产品定位级保护)。
         用户想换作品 → 必须新建项目(详见下方 lock-hint 文案)
         Sprint 6.A2 polish(2026-05-22):加 initialLoadDone 闸门 — 切 tab/切项目 mount 瞬间
         uploads 必空,直接显 drop-zone 会让用户看到"空项目"假态闪一帧,fetch 完才切到真实
         uploads-list。加闸门后 mount → 显"加载中…"(L634)→ fetch 完一次性 swap,无闪 -->
    <template v-if="initialLoadDone && !hasUploads">
    <div
      class="drop-zone"
      :class="{ 'is-uploading': uploading }"
      @click="triggerFilePicker"
      @drop="onDrop"
      @dragover="onDragOver"
    >
      <input
        ref="fileInputRef"
        type="file"
        :accept="ACCEPT"
        class="hidden-input"
        @change="onFileChange"
      />
      <template v-if="uploading">
        <!-- 2.C+ polish: emoji → 现代化 inline SVG(转圈加载,accent 色) -->
        <svg
          class="drop-icon drop-icon-spin"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
        >
          <path d="M21 12a9 9 0 1 1-6.219-8.56" />
        </svg>
        <p class="drop-main">上传中…</p>
        <p class="drop-hint">正在解析 + 红旗扫描,通常 &lt; 1 秒</p>
      </template>
      <template v-else>
        <!-- 现代化 inline SVG:云 + 向上箭头(上传语义),accent 色 line-icon -->
        <svg
          class="drop-icon"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="1.6"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
        >
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
          <polyline points="17 8 12 3 7 8" />
          <line x1="12" y1="3" x2="12" y2="15" />
        </svg>
        <p class="drop-main">点击或拖文件到这里</p>
        <p class="drop-hint">支持 .txt / .epub / .docx,最大 100MB</p>
      </template>
    </div>

    <!-- M7.E(2026-05-20)首次上传锁定提示 — 让用户决策前明确"一项目=一作品"
         UI 优化(2026-05-22):lock-hint 由大块 banner 压缩为单行紧凑文本(emoji → SVG icon),
         视觉重量大幅降低,不再喧宾夺主 -->
    <p class="lock-hint">
      <Icon name="lock" :size="13" class="lock-icon" />
      <span>
        <strong>一个项目 = 一个作品世界</strong> · 提交后锁定为这部作品,无法再装其他 ·
        想换作品请<strong>新建项目</strong>
      </span>
    </p>
    </template>

    <!-- 已上传列表
         UI 优化(2026-05-22):删除"还没有上传任何作品文件"独立 empty hint —
         标题"作品文件 0" + 大上传区已足以表达 empty 语义,该 hint 纯冗余 -->
    <div v-if="loading" class="state-msg">加载中…</div>
    <ul v-else-if="hasUploads" class="uploads-list">
      <li
        v-for="u in uploads"
        :key="u.id"
        class="upload-row"
      >
        <div class="upload-main">
          <span class="upload-filename" :title="u.filename">
            {{ u.filename }}
          </span>
          <span class="upload-meta mono">
            {{ formatSize(u.size_bytes) }}
            · {{ u.parsed_text_chars ?? 0 }} 字
            · {{ formatTime(u.uploaded_at) }}
          </span>
          <!-- 当前正在为该 upload 抽图谱 → 显完整事件流(SSE 实时推送)-->
          <div
            v-if="extractingUploadId === u.id && extractJob.detail.value"
            class="extract-stream"
          >
            <div class="stream-header">
              <span class="progress-dot"></span>
              <span class="progress-text">
                {{ EXTRACT_STATE_LABEL[extractJob.detail.value.state] }}
              </span>
              <span
                v-if="extractJob.detail.value.cost_yuan > 0"
                class="stream-cost mono"
              >¥{{ extractJob.detail.value.cost_yuan.toFixed(4) }}</span>
              <button
                v-if="!['done', 'failed'].includes(extractJob.detail.value.state)"
                type="button"
                class="stream-cancel-btn"
                @click="handleCancel(u)"
              >取消</button>
            </div>
            <ul v-if="formattedExtractEvents.length" class="event-list">
              <li
                v-for="ev in formattedExtractEvents"
                :key="ev.key"
                class="event-row"
                :class="[`tone-${ev.tone}`, `indent-${ev.indent}`]"
              >
                <span class="event-icon mono">{{ ev.icon }}</span>
                <span class="event-main">{{ ev.main }}</span>
                <span v-if="ev.sub" class="event-sub mono">{{ ev.sub }}</span>
              </li>
            </ul>
            <p v-else class="event-empty">等待 AI 启动…</p>
          </div>
          <!-- 僵尸 / 可恢复失败:加一行可恢复块数提示(让用户知道恢复价值) -->
          <p
            v-else-if="
              ['zombie_resumable', 'zombie_no_resume'].includes(uploadActionGroup(u))
              && latestJobByUpload[u.id]
            "
            class="zombie-hint"
          >
            <span class="zombie-icon">⚠</span>
            <span v-if="latestJobByUpload[u.id].completed_chunks_count > 0">
              已抽 {{ latestJobByUpload[u.id].completed_chunks_count }} 块,可点「继续抽取」从断点恢复(免重抽这部分成本)
            </span>
            <span v-else>
              任务已中断,可重新抽取
            </span>
          </p>
        </div>
        <div class="upload-side">
          <!-- 三态 UX:按 uploadActionGroup 出按钮 -->
          <template v-if="uploadActionGroup(u) === 'first_extract'">
            <button
              type="button"
              class="extract-btn"
              :disabled="extractingUploadId !== null"
              title="调 AI 抽人物 / 关系 / 事件,自动落项目"
              @click="handleExtract(u)"
            >
              <Icon name="spark" :size="13" class="extract-spark" /> AI 抽图谱
            </button>
          </template>

          <template v-else-if="uploadActionGroup(u) === 'ready'">
            <button
              type="button"
              class="extract-btn"
              :disabled="extractingUploadId !== null"
              title="重新抽图谱(扣 1 次推演配额)"
              @click="handleExtract(u)"
            >
              <Icon name="spark" :size="13" class="extract-spark" /> 重抽
            </button>
          </template>

          <template v-else-if="uploadActionGroup(u) === 'active_running'">
            <!-- 头部已有取消按钮,这里只显 chip -->
          </template>

          <template v-else-if="uploadActionGroup(u) === 'zombie_resumable'">
            <button
              type="button"
              class="extract-btn"
              :disabled="extractingUploadId !== null"
              title="从已抽完的最后一块继续 — 不扣新配额"
              @click="handleResume(u)"
            >
              <Icon name="spark" :size="13" class="extract-spark" /> 继续抽取
            </button>
            <button
              type="button"
              class="restart-btn"
              :disabled="extractingUploadId !== null"
              title="丢弃所有进度从头开始 — 扣 1 次新配额"
              @click="handleRestart(u)"
            >
              <Icon name="rotate_ccw" :size="13" />
              重新开始
            </button>
          </template>

          <template v-else-if="uploadActionGroup(u) === 'zombie_no_resume'">
            <button
              type="button"
              class="extract-btn"
              :disabled="extractingUploadId !== null"
              title="重新抽取(扣 1 次新配额)"
              @click="handleRestart(u)"
            >
              <span class="extract-spark">↻</span> 重新抽取
            </button>
          </template>

          <template v-else-if="uploadActionGroup(u) === 'live_other_upload'">
            <span class="muted-hint">等当前抽取完</span>
          </template>

          <span
            class="state-chip"
            :class="`state-${u.state}`"
          >{{ refinedStateLabel(u) }}</span>
          <button
            type="button"
            class="del-btn"
            aria-label="删除"
            title="删除该文件"
            @click="handleDelete(u)"
          >×</button>
        </div>
      </li>
    </ul>

    <!--
      Sprint 6.A2 FOCUS.10(2026-05-22):人机协同审核 Modal
      当 worker 跑完 graph 阶段停在 entities_pending_review,phase 切到 pending_review →
      弹 modal 让用户审核 entities。点"通过"后 modal 调 approve_entities 端点,
      后端续跑 → SSE 推 profile 等事件 → phase 自然回到 running → done。
    -->
    <EntityReviewModal
      v-if="extractJob.detail.value"
      :open="extractJob.phase.value === 'pending_review'"
      :job-id="extractJob.detail.value.id"
      :persons="extractJob.pendingReviewPersons.value"
      :missed-persons="extractJob.pendingMissedPersons.value"
      @approved="extractJob.phase.value = 'running'"
      @close="extractJob.phase.value = 'running'"
    />
  </section>
</template>

<style scoped>
.uploads-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.section-header {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-bottom: var(--space-1);
}
.section-title {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--color-text);
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  letter-spacing: -0.01em;
}
.count {
  font-size: var(--text-sm);
  color: var(--color-text-subtle);
  font-weight: 400;
}
.section-hint {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
}

/* 拖拽区 */
.drop-zone {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-1);
  padding: var(--space-6) var(--space-5);
  background: var(--color-bg-subtle);
  border: 1px dashed var(--color-border-strong);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
  text-align: center;
}
.drop-zone:hover {
  background: var(--color-accent-soft);
  border-color: var(--color-accent);
  border-style: solid;
}
.drop-zone.is-uploading {
  cursor: wait;
  opacity: 0.7;
}
/* 2.C+ polish: inline SVG drop icon — 替代 emoji,accent 色 line-icon */
.drop-icon {
  width: 32px;
  height: 32px;
  color: var(--color-accent);
  margin-bottom: var(--space-1);
  transition: transform var(--duration-base) var(--ease-out);
}
.drop-zone:hover .drop-icon {
  transform: translateY(-2px);
}
.drop-icon-spin {
  animation: drop-spin 1s linear infinite;
}
@keyframes drop-spin {
  to { transform: rotate(360deg); }
}
.drop-main {
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text);
}
.drop-hint {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
}
.hidden-input {
  display: none;
}

/* Sprint 6.A2 M7.E(2026-05-20)+ UI 优化(2026-05-22)首次上传锁定提示
   2026-05-22:从大块 banner(border-left 紫线 + sm 字号 + 多行)压缩为
   单行紧凑文本(xs 字号 + 弱化色 + 无 border-left,只留淡灰底)*/
.lock-hint {
  margin: var(--space-2) 0 0;
  padding: var(--space-2) var(--space-3);
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  background: var(--color-bg-subtle);
  border-radius: var(--radius-sm);
  line-height: 1.5;
}
.lock-icon {
  flex-shrink: 0;
  color: var(--color-text-subtle);
}
.lock-hint strong {
  color: var(--color-text);
  font-weight: 500;
}

/* 状态消息(loading) — UI 优化(2026-05-22)删除 state-empty(对应"还没上传"hint 已删) */
.state-msg {
  text-align: center;
  padding: var(--space-3);
  color: var(--color-text-subtle);
  font-size: var(--text-sm);
}

/* 上传列表 */
.uploads-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  list-style: none;
  padding: 0;
  margin: 0;
}
.upload-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}
.upload-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}
.upload-filename {
  font-size: var(--text-sm);
  color: var(--color-text);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.upload-meta {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
}
.upload-side {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
}

/* Sprint 2.B:抽图谱按钮 + 进度行 */
.extract-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px var(--space-3);
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.extract-btn:hover:not(:disabled) {
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  border-color: var(--color-accent);
}
.extract-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.extract-spark {
  font-size: var(--text-sm);
  line-height: 1;
}

/* 2.B+ 重新开始按钮(灰色副选项) */
.restart-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px var(--space-3);
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--color-text-muted);
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.restart-btn:hover:not(:disabled) {
  color: var(--color-text);
  background: var(--color-bg-subtle);
  border-color: var(--color-border-strong);
}
.restart-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* 2.B+ 取消按钮(stream header 内) */
.stream-cancel-btn {
  margin-left: auto;
  padding: 2px 10px;
  font-size: 11px;
  color: var(--color-text-muted);
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.stream-cancel-btn:hover {
  color: var(--color-danger);
  border-color: var(--color-danger);
  background: var(--color-danger-soft);
}

/* 僵尸 / 可恢复提示 */
.zombie-hint {
  margin-top: var(--space-1);
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--text-xs);
  color: #B45309;   /* warm amber — 中性提示色,不像 danger 那样吓人 */
  background: rgba(245, 158, 11, 0.08);
  padding: 4px var(--space-2);
  border-radius: var(--radius-sm);
}
.zombie-icon {
  font-size: var(--text-sm);
  line-height: 1;
  flex-shrink: 0;
}

.muted-hint {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  font-style: italic;
}

/* 事件流容器 */
.extract-stream {
  margin-top: var(--space-3);
  padding: var(--space-3);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-md);
  border-left-width: 3px;
}

.stream-header {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-accent-text);
  margin-bottom: var(--space-2);
  padding-bottom: var(--space-2);
  border-bottom: 1px dashed var(--color-border);
}
.progress-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-accent);
  animation: extract-pulse 1.4s ease-in-out infinite;
  flex-shrink: 0;
}
@keyframes extract-pulse {
  0%, 100% { opacity: 0.3; transform: scale(0.85); }
  50%      { opacity: 1;   transform: scale(1.1); }
}
.progress-text {
  font-weight: 500;
}
.stream-cost {
  color: var(--color-text-subtle);
  font-size: 11px;
  margin-left: var(--space-2);
}

/* 事件列表 */
.event-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  list-style: none;
  padding: 0;
  margin: 0;
  max-height: 240px;
  overflow-y: auto;
}
.event-row {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  font-size: var(--text-xs);
  line-height: 1.5;
  color: var(--color-text);
}
.event-row.indent-1 {
  padding-left: var(--space-4);
}
.event-icon {
  flex-shrink: 0;
  width: 14px;
  text-align: center;
  font-size: 10px;
}
.event-main {
  /* Sprint 3.A polish:flex 1 + min-width 0 让 main 优先获得空间;
     原写法只有 flex: 1,sub 是 flex-shrink: 0,长 sub(如 characters_start
     的 24 个角色名列表)会把 main 挤到极窄 → break-word 让中文每字独占一行
     (图一暴露:"共 24 位主角" 被竖向拆字) */
  flex: 1 1 auto;
  min-width: 0;
  word-break: break-word;
}
.event-sub {
  /* sub 不再 flex-shrink: 0,改为允许收缩 + ellipsis 截断,
     最多占一半宽度让 main 至少有一半呼吸空间 */
  flex: 0 1 auto;
  min-width: 0;
  max-width: 50%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--color-text-subtle);
  font-size: 10px;
}
.event-row.tone-success .event-icon {
  color: #16A34A;
}
.event-row.tone-error .event-icon {
  color: var(--color-danger);
}
.event-row.tone-error .event-main {
  color: var(--color-danger);
}
/* FOCUS.9(2026-05-22):warning tone — 漏抽提示 */
.event-row.tone-warning .event-icon {
  color: var(--color-warning);
}
.event-row.tone-warning .event-main {
  color: var(--color-text);
}
.event-row.tone-warning .event-sub {
  color: var(--color-warning);
  font-weight: 500;
}
.event-empty {
  text-align: center;
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  padding: var(--space-2);
}

.state-chip {
  padding: 2px var(--space-2);
  font-size: var(--text-xs);
  border-radius: var(--radius-sm);
  background: var(--color-bg-subtle);
  color: var(--color-text-muted);
}
.state-parsed { color: var(--color-accent-text); background: var(--color-accent-soft); }
.state-ready  { color: #16A34A; background: rgba(22, 163, 74, 0.1); }
.state-extracting { color: #B45309; background: rgba(245, 158, 11, 0.12); }
.state-failed,
.state-rejected { color: var(--color-danger); background: var(--color-danger-soft); }

.del-btn {
  width: 28px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: var(--text-lg);
  line-height: 1;
  color: var(--color-text-subtle);
  background: transparent;
  border-radius: var(--radius-full);
  transition: all var(--duration-fast) var(--ease-out);
  cursor: pointer;
}
.del-btn:hover {
  color: var(--color-danger);
  background: var(--color-danger-soft);
}
</style>
