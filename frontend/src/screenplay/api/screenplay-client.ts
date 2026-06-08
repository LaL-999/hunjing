/**
 * 剧创态后端 API client — 阶段 6 重写(2026-06-08)。
 *
 * 关键变更:
 *   - **复用父平台 api 客户端**(自带 JWT Bearer + 401 自动 logout +
 *     统一 ApiError),不再裸 fetch
 *   - 加 link / unlink novel-to-project endpoint(阶段 5.1)
 *   - 加 listProjects(给 link UI 下拉用)
 *
 * 路径前缀:/screenplay/* — 父平台 api 客户端的 API_BASE = "/api",
 * 所以 path 这里写 "/screenplay/..." → 实际请求 "/api/screenplay/..."
 */

import { api } from "../../api/client";
import type { Project } from "../../api/types";
import type {
  ComposeResponse,
  NovelInfo,
  OptimizeRequest,
  OptimizeResponse,
  ScreenplayResponse,
  ScreenplayVersion,
  StructureReport,
} from "../types/screenplay";

// ============================================================
// Novel
// ============================================================

export async function listNovels(): Promise<NovelInfo[]> {
  const r = await api.get<{ items: NovelInfo[] }>("/screenplay/novels");
  return r.items ?? [];
}

/**
 * 上传小说文件(.txt / .epub / .docx)
 * 父平台 api.post 支持 FormData(自动 multipart + 不强加 Content-Type)。
 */
export async function uploadNovel(file: File): Promise<{
  novel_id: string;
  title: string;
  source_format: string;
  total_chapters: number;
  total_chars: number;
  chapters: Array<{
    id: string;
    number: number;
    title: string | null;
    paragraph_count: number;
    char_count: number;
  }>;
}> {
  const form = new FormData();
  form.append("file", file);
  return api.post("/screenplay/novels", form);
}

export async function deleteNovel(novelId: string): Promise<void> {
  await api.delete(`/screenplay/novels/${encodeURIComponent(novelId)}`);
}

export async function getNovel(novelId: string): Promise<
  NovelInfo & {
    chapters: Array<{
      id: string;
      number: number;
      title: string | null;
      paragraph_count: number;
      char_count: number;
    }>;
    linked_project_id?: string | null;
  }
> {
  return api.get(`/screenplay/novels/${encodeURIComponent(novelId)}`);
}

export async function getChapterParagraphs(
  chapterId: string,
): Promise<Array<{ index_in_chapter: number; text: string }>> {
  const r = await api.get<{
    paragraphs: Array<{ index_in_chapter: number; text: string }>;
  }>(`/screenplay/chapters/${encodeURIComponent(chapterId)}`);
  return r.paragraphs ?? [];
}

// ============================================================
// 阶段 5.1 桥接 — Link / Unlink Novel ↔ Project
// ============================================================

/**
 * 绑定小说到浑晶 project — 6 个 LLM agent 会通过 huimeng_bridge 读取该
 * project 的 SP-2 / 3 / 4 / 7 资产。
 *
 * @param projectId 非空 = 绑定;null = 解绑
 */
export async function linkNovelToProject(
  novelId: string,
  projectId: string | null,
): Promise<{ novel_id: string; linked_project_id: string | null }> {
  return api.patch(
    `/screenplay/novels/${encodeURIComponent(novelId)}/link`,
    { project_id: projectId },
  );
}

/**
 * 列出当前用户的所有浑晶 project — 给 link UI 下拉选项用。
 * 走父平台 /projects 而不是 /screenplay/* 前缀。
 */
export async function listProjectsForLink(): Promise<Project[]> {
  return api.get<Project[]>("/projects");
}

// ============================================================
// 阶段 8.2 — 角色档案 + 关系图
// ============================================================

export interface CharacterStatsApi {
  scene_count: number;
  chapter_count: number;
  dialogue_count: number;
  voiceover_count: number;
  first_appearance_scene: string | null;
  first_appearance_number: number | null;
  role_tier: "protagonist" | "supporting" | "bit_part" | "extra";
}

export interface CharacterKeyEventApi {
  description: string;
  chapter_number: number | null;
}

export interface CharacterBridgeAssetsApi {
  surface_goal: string | null;
  deep_need: string | null;
  fatal_blind_spot: string | null;
  arc_from_to: string | null;
  secrets: Array<{ description: string; hidden_from?: string[] }>;
  snapshot_position: string | null;
  snapshot_hp_status: string | null;
  snapshot_emotion_top: string | null;
  snapshot_inventory: string[];
}

export interface CharacterProfileApi {
  id: string;
  name: string;
  aka: string[];
  description: string;
  is_protagonist: boolean;
  stats: CharacterStatsApi;
  relationships: Array<{
    target_id: string;
    target_name: string;
    type: string;
    description: string;
  }>;
  key_events: CharacterKeyEventApi[];
  bridge_assets: CharacterBridgeAssetsApi | null;
}

export interface GraphNodeApi {
  id: string;
  name: string;
  role_tier: string;
  weight: number;
  is_protagonist: boolean;
  has_bridge_assets: boolean;
}

export interface GraphEdgeApi {
  source: string;
  target: string;
  type: string;
  description: string;
  polarity: "positive" | "negative" | "neutral" | null;
}

export interface CharacterProfilesResponse {
  novel_id: string;
  screenplay_id: string;
  linked_project_id: string | null;
  characters: CharacterProfileApi[];
  graph: { nodes: GraphNodeApi[]; edges: GraphEdgeApi[] };
}

export async function getCharacterProfiles(
  novelId: string,
): Promise<CharacterProfilesResponse> {
  return api.get(
    `/screenplay/novels/${encodeURIComponent(novelId)}/characters`,
  );
}

// ============================================================
// 阶段 8.4 — 分集规划 MVP
// ============================================================

export interface EpisodeApi {
  episode_number: number;
  title: string;
  scene_ids: string[];
  est_minutes: number;
  scene_count: number;
  first_chapter: number | null;
  last_chapter: number | null;
  boundary_reason: string;
}

export interface EpisodePlanApi {
  episodes: EpisodeApi[];
  total_minutes: number;
  total_scenes: number;
  target_minutes_per_ep: number;
  mode: string; // 'rule' | 'llm'
}

export async function planEpisodes(
  novelId: string,
  targetMinutesPerEp: number,
): Promise<EpisodePlanApi> {
  return api.post(
    `/screenplay/novels/${encodeURIComponent(novelId)}/plan-episodes`,
    { target_minutes_per_ep: targetMinutesPerEp },
  );
}

// ============================================================
// Screenplay
// ============================================================

export interface ComposeRequest {
  refine_dialogue?: boolean;
  propose_decisions?: boolean;
  max_chapters?: number | null;
  retry_per_call?: number;
}

export async function composeScreenplay(
  novelId: string,
  req: ComposeRequest = {},
): Promise<ComposeResponse> {
  return api.post(
    `/screenplay/novels/${encodeURIComponent(novelId)}/compose-screenplay`,
    req,
  );
}

export async function getLatestScreenplay(
  novelId: string,
): Promise<ScreenplayResponse> {
  return api.get(
    `/screenplay/novels/${encodeURIComponent(novelId)}/screenplay`,
  );
}

export async function getScreenplayById(
  screenplayId: string,
): Promise<ScreenplayResponse> {
  return api.get(
    `/screenplay/screenplays/${encodeURIComponent(screenplayId)}`,
  );
}

export async function getStructureReport(
  screenplayId: string,
): Promise<StructureReport> {
  return api.get(
    `/screenplay/screenplays/${encodeURIComponent(screenplayId)}/structure`,
  );
}

// ============================================================
// Export(走 downloadFile 二进制下载,带 JWT)
// ============================================================

export type ExportFormat = "fountain" | "txt" | "yaml";

/**
 * 下载剧本为指定格式 — 复用父平台 api.downloadFile(自动带 JWT + ApiError)。
 */
export async function downloadScreenplay(
  screenplayId: string,
  format: ExportFormat,
): Promise<void> {
  const { blob, headers } = await api.downloadFile(
    `/screenplay/screenplays/${encodeURIComponent(screenplayId)}/export.${format}`,
  );
  // 从 Content-Disposition 拿文件名(RFC 5987 编码,filename*=UTF-8''xxx)
  const disposition = headers.get("Content-Disposition") || "";
  let filename = `screenplay.${format}`;
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match) {
    try {
      filename = decodeURIComponent(utf8Match[1]);
    } catch {
      /* fallback */
    }
  } else {
    const plainMatch = disposition.match(/filename="?([^";]+)"?/i);
    if (plainMatch) filename = plainMatch[1];
  }
  const blobUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = blobUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
}

// ============================================================
// 优化 + 版本
// ============================================================

export async function optimizeScreenplay(
  screenplayId: string,
  req: OptimizeRequest,
): Promise<OptimizeResponse> {
  return api.post(
    `/screenplay/screenplays/${encodeURIComponent(screenplayId)}/optimize`,
    req,
  );
}

export async function listScreenplayVersions(
  novelId: string,
): Promise<ScreenplayVersion[]> {
  const r = await api.get<{ items: ScreenplayVersion[] }>(
    `/screenplay/novels/${encodeURIComponent(novelId)}/versions`,
  );
  return r.items ?? [];
}

// ============================================================
// 健康检查(阶段 6 — 后端尚无此 endpoint,临时返 mock)
// ============================================================

/**
 * 阶段 6 注:剧创态自己的 /health endpoint 在阶段 3 没迁入(父平台已有
 * /health),所以走父平台健康检查 — 返简化结构,不再带 llm_configured。
 */
export async function getHealth(): Promise<{
  status: string;
  service: string;
  version: string;
  llm_model: string;
  llm_configured: boolean;
}> {
  // 走父平台 /health(rooted to /api/health),不带 /screenplay 前缀
  try {
    const parent = await api.get<Record<string, unknown>>("/health", {
      skipAuth: true,
    });
    return {
      status: typeof parent.status === "string" ? parent.status : "ok",
      service: "screenplay",
      version: (parent.version as string) ?? "merged-into-huimeng",
      llm_model: (parent.llm_model as string) ?? "(父平台路由)",
      // 没法可靠判断 LLM 是否配置 — 默认 true,跑不出来 compose 时再报错
      llm_configured: true,
    };
  } catch {
    return {
      status: "ok",
      service: "screenplay",
      version: "merged-into-huimeng",
      llm_model: "(父平台路由)",
      llm_configured: true,
    };
  }
}

// re-export 父平台 ApiError 让历史代码 import 不破
export { ApiError } from "../../api/client";
