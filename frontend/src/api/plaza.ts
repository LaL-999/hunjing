/**
 * 作品广场 API — 2026-06-25。
 *
 * 后端契约见 backend/app/routers/plaza.py + profile.py。
 * 图片地址(封面 / 头像)后端返相对 URL,渲染时统一过 apiAssetUrl()(拆子域兜底)。
 */
import { api } from "./client";

/** 创作态 —— 与作品来源 project.mode 对齐(initial 态不标原著) */
export type WorkMode = "initial" | "middle" | "end" | "cycle" | "screenplay";

/** 作品来源类型 */
export type PlazaSourceType = "simulation" | "screenplay" | "comic";

/** 广场列表卡片(不含正文) */
export interface PlazaCard {
  id: string;
  title: string;
  summary: string | null;
  mode: WorkMode | string;
  original_title: string | null;
  cover_image_path: string | null;
  cover_gradient: number;
  word_count: number;
  like_count: number;
  read_count: number;
  published_at: string;
  author_id: string;
  author_nickname: string | null;
  author_avatar_url: string | null;
  liked: boolean;
  source_type?: PlazaSourceType;
  is_public?: number;
  allow_download?: number;
}

/** 作品详情落地页(元信息 + 预览 + 权限,不含全文) */
export interface PlazaWorkDetail extends PlazaCard {
  is_owner: boolean;
  can_download: boolean;
  preview: string | null;          // 文本作品的节选预览
  comic_pages?: string[];          // 漫画作品的整页图 URL 数组
}

export interface PlazaListResponse {
  items: PlazaCard[];
  total: number;
  sort: string;
  offset: number;
  limit: number;
}

/** 阅读页:卡片元信息 + 正文 */
export interface PlazaWork extends PlazaCard {
  content: string;
}

/** 可上架的素材(已完成 + 未上架的 simulation) */
export interface PublishableSim {
  sim_id: string;
  project_id: string | null;
  project_name: string | null;
  mode: WorkMode | string;
  original_title: string | null;
  summary: string | null;
  created_at: string;
}

/** 发布权限(所有上架流程通用) */
export interface PublishVisibility {
  is_public?: number;       // 1 公开 / 0 私人,默认 1
  allow_download?: number;  // 1 允许他人下载 / 0 不允许,默认 1
}

export interface PublishPayload extends PublishVisibility {
  sim_id: string;
  title: string;
  summary?: string | null;
  cover_image_path?: string | null;
  cover_gradient?: number;
}

/** 漫画可发布素材 */
export interface PublishableComic {
  comic_id: string;
  name: string;
  page_count: number;
  cover_url: string | null;
  updated_at: string;
}

export interface PublishComicPayload extends PublishVisibility {
  comic_id: string;
  title: string;
  summary?: string | null;
  cover_image_path?: string | null;
  cover_gradient?: number;
}

/** 剧创态可发布素材(v5 item8):一个 novel 的全局剧本 + 分集方案清单 */
export interface PublishableScreenplayPlan {
  plan_id: string;
  scheme_name: string;
  episode_count: number;
  preset: string;
}
export interface PublishableScreenplay {
  novel_id: string;
  novel_title: string;
  has_global: boolean;
  screenplay_id: string | null;
  episode_plans: PublishableScreenplayPlan[];
  created_at: string;
}

export type ScreenplayPublishKind = "global" | "episodes" | "both";

export interface PublishScreenplayPayload extends PublishVisibility {
  novel_id: string;
  kind: ScreenplayPublishKind;
  plan_id?: string | null;
  title: string;
  summary?: string | null;
  cover_image_path?: string | null;
  cover_gradient?: number;
}

export type PlazaSort = "hot" | "new" | "classic";

export const plazaApi = {
  list(sort: PlazaSort = "hot", limit = 24, offset = 0): Promise<PlazaListResponse> {
    return api.get<PlazaListResponse>(
      `/plaza/works?sort=${sort}&limit=${limit}&offset=${offset}`,
    );
  },
  myWorks(): Promise<{ items: PlazaCard[] }> {
    return api.get<{ items: PlazaCard[] }>("/plaza/my-works");
  },
  publishable(): Promise<{ items: PublishableSim[] }> {
    return api.get<{ items: PublishableSim[] }>("/plaza/publishable");
  },
  publishableScreenplays(): Promise<{ items: PublishableScreenplay[] }> {
    return api.get<{ items: PublishableScreenplay[] }>("/plaza/publishable-screenplays");
  },
  publishableComics(): Promise<{ items: PublishableComic[] }> {
    return api.get<{ items: PublishableComic[] }>("/plaza/publishable-comics");
  },
  publishScreenplay(payload: PublishScreenplayPayload): Promise<PlazaCard> {
    return api.post<PlazaCard>("/plaza/publish-screenplay", payload);
  },
  publishComic(payload: PublishComicPayload): Promise<PlazaCard> {
    return api.post<PlazaCard>("/plaza/publish-comic", payload);
  },
  /** 详情落地页(不 +阅读量、不返全文) */
  detail(workId: string): Promise<PlazaWorkDetail> {
    return api.get<PlazaWorkDetail>(`/plaza/works/${workId}`);
  },
  /** 在线阅读(返全文 + 阅读量 +1) */
  read(workId: string): Promise<PlazaWork> {
    return api.get<PlazaWork>(`/plaza/works/${workId}/content`);
  },
  publish(payload: PublishPayload): Promise<PlazaCard> {
    return api.post<PlazaCard>("/plaza/publish", payload);
  },
  /** 改作品权限(公开/私人 + 是否允许下载) */
  setVisibility(workId: string, v: PublishVisibility): Promise<PlazaCard> {
    return api.patch<PlazaCard>(`/plaza/works/${workId}/visibility`, v);
  },
  /** 下载正文 Markdown(权限由后端把关:公开+允许下载 或 作者本人) */
  async download(workId: string, title: string): Promise<void> {
    const { blob } = await api.downloadFile(`/plaza/works/${workId}/download`);
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${(title || "作品").replace(/[\\/:*?"<>|]/g, "_")}.md`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  },
  like(workId: string, liked: boolean): Promise<{ liked: boolean; like_count: number }> {
    return api.post<{ liked: boolean; like_count: number }>(
      `/plaza/works/${workId}/like`,
      { liked },
    );
  },
  unpublish(workId: string): Promise<{ unpublished: boolean }> {
    return api.delete<{ unpublished: boolean }>(`/plaza/works/${workId}`);
  },
  /** 上传封面图 → 返回内部 URL(发布时填 cover_image_path) */
  uploadCover(file: File): Promise<{ cover_image_path: string }> {
    const fd = new FormData();
    fd.append("file", file);
    return api.post<{ cover_image_path: string }>("/plaza/cover", fd);
  },
};

// ========== 用户资料(改昵称 + 头像) ==========

export interface UserProfile {
  id: string;
  email: string | null;
  nickname: string | null;
  avatar_url: string | null;
}

export const profileApi = {
  get(): Promise<UserProfile> {
    return api.get<UserProfile>("/me/profile");
  },
  updateNickname(nickname: string): Promise<UserProfile> {
    return api.patch<UserProfile>("/me/profile", { nickname });
  },
  uploadAvatar(file: File): Promise<UserProfile> {
    const fd = new FormData();
    fd.append("file", file);
    return api.post<UserProfile>("/me/avatar", fd);
  },
};
