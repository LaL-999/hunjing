/**
 * 作品广场 API — 2026-06-25。
 *
 * 后端契约见 backend/app/routers/plaza.py + profile.py。
 * 图片地址(封面 / 头像)后端返相对 URL,渲染时统一过 apiAssetUrl()(拆子域兜底)。
 */
import { api } from "./client";

/** 创作态 —— 与作品来源 project.mode 对齐(initial 态不标原著) */
export type WorkMode = "initial" | "middle" | "end" | "cycle" | "screenplay";

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
  /** 仅「我的发布」列表带 */
  is_public?: number;
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

export interface PublishPayload {
  sim_id: string;
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
  read(workId: string): Promise<PlazaWork> {
    return api.get<PlazaWork>(`/plaza/works/${workId}`);
  },
  publish(payload: PublishPayload): Promise<PlazaCard> {
    return api.post<PlazaCard>("/plaza/publish", payload);
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
