"""作品广场(社区发布)服务 — 2026-06-25。

用户把自己创作的作品(目前支持 simulation 文本作品)上架到广场:
  - 发布时**快照**正文(冻结,不受源改动影响)+ 派生 mode / 原著名
  - 列表支持三种排序:hot(热度,带时间衰减 + 新作保护)/ new / classic
  - 在线阅读自动 +1 阅读量;点赞去规范化 like_count + 明细表防重复

设计要点:
  - 正文从源 simulation.narrative 取(后端权威,杜绝伪造)
  - mode / original_title 由所属 project 派生(初始态 original_title=None)
  - 排序在 Python 算 hot_score(平台规模小,候选全取后排序足够)
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from app.db import execute, fetch_all, fetch_one
from app.services.project_service import ResourceNotFoundOrForbidden, iso_now

# 默认封面渐变数量(前端 1-9 与之对应)
_GRADIENT_COUNT = 9
# 新作保护窗口(小时):此窗口内 hot_score 额外加权,避免被老作品埋
_FRESH_HOURS = 48
_FRESH_BOOST = 1.5


class PlazaError(Exception):
    """作品广场业务异常(带 code 供路由映射 HTTP)。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass
class PublishInput:
    sim_id: str
    title: str
    summary: Optional[str] = None
    cover_image_path: Optional[str] = None
    cover_gradient: int = 1
    is_public: int = 1        # v5:1 公开(上广场)/ 0 私人(仅作者可见)
    allow_download: int = 1   # v5:公开时是否允许他人下载正文


@dataclass
class PublishScreenplayInput:
    """剧创态发布(v5 item8)。kind ∈ global|episodes|both。"""
    novel_id: str
    kind: str = "global"                 # global(全局剧本)/ episodes(分集方案)/ both
    plan_id: Optional[str] = None        # kind=episodes/both 时指定分集方案
    title: str = ""
    summary: Optional[str] = None
    cover_image_path: Optional[str] = None
    cover_gradient: int = 1
    is_public: int = 1
    allow_download: int = 1


@dataclass
class PublishComicInput:
    """漫创态发布(v5)—— content 存已排版整页 PNG 的稳定 URL 数组(JSON)。"""
    comic_id: str
    title: str = ""
    summary: Optional[str] = None
    cover_image_path: Optional[str] = None
    cover_gradient: int = 1
    is_public: int = 1
    allow_download: int = 1


def _norm_bool(v) -> int:
    """归一化 0/1(接受 bool / int / '0'/'1')。"""
    try:
        return 1 if int(v) != 0 else 0
    except (TypeError, ValueError):
        return 1 if v else 0


def _excerpt(text: str, n: int = 80) -> str:
    """从正文取前 n 字做简介(去 markdown 噪音 + 多余空白)。"""
    import re
    raw = text or ""
    # 去行首 markdown 标记(# 标题 / > 引用 / - * 列表),避免简介里出现"# "
    cleaned = re.sub(r"(?m)^\s*(#{1,6}|>|[-*+])\s+", "", raw)
    flat = " ".join(cleaned.split())
    return flat[:n]


def publish_work(conn, user_id: str, inp: PublishInput) -> dict:
    """把用户的一个 simulation 作品上架到广场(快照正文)。

    校验 sim 归属;正文取 sim.narrative;mode / 原著名由 project 派生。
    """
    title = (inp.title or "").strip()
    if not title:
        raise PlazaError("TITLE_REQUIRED", "作品名不能为空")
    if len(title) > 60:
        raise PlazaError("TITLE_TOO_LONG", "作品名最多 60 字")

    # 1. 校验 simulation 归属 + 取正文
    sim = fetch_one(
        conn,
        "SELECT * FROM simulations WHERE id=? AND user_id=?",
        (inp.sim_id, user_id),
    )
    if sim is None:
        raise PlazaError("SOURCE_NOT_FOUND", "找不到该作品或无权发布")
    if sim["state"] != "done":
        raise PlazaError("SOURCE_NOT_DONE", "只能上架已完成的作品")
    content = sim["narrative"] or ""
    if not content.strip():
        raise PlazaError("EMPTY_CONTENT", "作品正文为空,无法上架")

    # 2. 派生 mode / 原著名(由所属 project)
    project_id = sim["project_id"]
    proj = fetch_one(conn, "SELECT * FROM projects WHERE id=?", (project_id,))
    mode = proj["mode"] if proj else "initial"
    # 初始态 = 原创世界,不标原著;其余态用项目名作原著名
    original_title = None if mode == "initial" else (proj["name"] if proj else None)

    grad = inp.cover_gradient if 1 <= inp.cover_gradient <= _GRADIENT_COUNT else 1
    word_count = len(content)
    summary = (inp.summary or "").strip() or _excerpt(content)
    now = iso_now()
    work_id = str(uuid.uuid4())

    execute(
        conn,
        """INSERT INTO published_works
            (id, user_id, source_type, source_id, project_id, title, summary,
             mode, original_title, cover_image_path, cover_gradient, content,
             word_count, like_count, read_count, is_public, allow_download,
             published_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,?,?,?,?)""",
        (
            work_id, user_id, "simulation", inp.sim_id, project_id, title, summary,
            mode, original_title, inp.cover_image_path, grad, content,
            word_count, _norm_bool(inp.is_public), _norm_bool(inp.allow_download),
            now, now,
        ),
    )
    return get_work_meta(conn, work_id, viewer_id=user_id)


def _render_screenplay_content(
    conn, user_id: str, inp: "PublishScreenplayInput",
) -> tuple[str, str]:
    """把剧创态素材渲染成可发布纯文本(item8)。

    返回 (content, source_id)。全部走 plaza 的 conn + JOIN sp_novels 校验归属,
    渲染复用剧创态既有 exporter(纯函数,吃 dict)。
    """
    import json as _json

    kind = inp.kind if inp.kind in ("global", "episodes", "both") else "global"
    parts: list[tuple[str, str]] = []
    screenplay_dict: Optional[dict] = None
    source_id = inp.novel_id

    # ---- 全局剧本(sp_screenplays.yaml_text)----
    if kind in ("global", "both"):
        sp_row = fetch_one(
            conn,
            """SELECT sc.id AS screenplay_id, sc.yaml_text
                 FROM sp_screenplays sc
                 JOIN sp_novels n ON sc.novel_id = n.id
                WHERE sc.novel_id = ? AND n.user_id = ?
                ORDER BY sc.created_at DESC LIMIT 1""",
            (inp.novel_id, user_id),
        )
        if sp_row is None or not (sp_row["yaml_text"] or "").strip():
            raise PlazaError("SOURCE_NOT_DONE", "该剧本还没有生成「全局剧本」,无法发布全局版本")
        try:
            import yaml as _yaml
            screenplay_dict = _yaml.safe_load(sp_row["yaml_text"]) or {}
        except Exception:  # noqa: BLE001
            screenplay_dict = {}
        try:
            from app.screenplay.services import screenplay_exporter
            global_txt = screenplay_exporter.export_to_txt(screenplay_dict)
        except Exception as e:  # noqa: BLE001
            raise PlazaError("RENDER_FAILED", f"全局剧本渲染失败:{str(e)[:120]}")
        parts.append(("# 全局剧本", global_txt.strip()))
        if kind == "global":
            source_id = sp_row["screenplay_id"]

    # ---- 分集方案(sp_episode_plans.plan_json)----
    if kind in ("episodes", "both"):
        if not inp.plan_id:
            raise PlazaError("PLAN_REQUIRED", "请选择要发布的分集方案")
        plan_row = fetch_one(
            conn,
            """SELECT p.* FROM sp_episode_plans p
                 JOIN sp_novels n ON p.novel_id = n.id
                WHERE p.id = ? AND p.novel_id = ? AND n.user_id = ?""",
            (inp.plan_id, inp.novel_id, user_id),
        )
        if plan_row is None:
            raise PlazaError("SOURCE_NOT_FOUND", "找不到该分集方案或无权发布")
        try:
            plan_data = _json.loads(plan_row["plan_json"])
        except Exception:  # noqa: BLE001
            plan_data = {}
        plan_summary = {
            "scheme_name": plan_row["scheme_name"],
            "preset": plan_row["preset"],
            "target_minutes": plan_row["target_minutes"],
            "recommended_perspective": plan_row["recommended_perspective"],
            "episode_count": plan_row["episode_count"],
            "scene_count": plan_row["scene_count"],
            "created_at": plan_row["created_at"],
        }
        # 若尚未取到剧本(episodes-only),尝试取一份给 full 模式;取不到降级 outline
        if screenplay_dict is None:
            sp2 = fetch_one(
                conn,
                """SELECT sc.yaml_text FROM sp_screenplays sc
                     JOIN sp_novels n ON sc.novel_id = n.id
                    WHERE sc.novel_id = ? AND n.user_id = ?
                    ORDER BY sc.created_at DESC LIMIT 1""",
                (inp.novel_id, user_id),
            )
            if sp2 and (sp2["yaml_text"] or "").strip():
                try:
                    import yaml as _yaml
                    screenplay_dict = _yaml.safe_load(sp2["yaml_text"]) or {}
                except Exception:  # noqa: BLE001
                    screenplay_dict = None
        ep_mode = "full" if screenplay_dict else "outline"
        try:
            from app.screenplay.services import episode_plan_exporter
            ep_txt = episode_plan_exporter.export_to_txt(
                plan_summary, plan_data, mode=ep_mode, screenplay_dict=screenplay_dict,
            )
        except Exception as e:  # noqa: BLE001
            raise PlazaError("RENDER_FAILED", f"分集方案渲染失败:{str(e)[:120]}")
        parts.append(("# 分集方案", ep_txt.strip()))
        if kind == "episodes":
            source_id = inp.plan_id

    if not parts:
        raise PlazaError("EMPTY_CONTENT", "没有可发布的剧本内容")

    if len(parts) == 1:
        content = parts[0][1]
    else:
        content = "\n\n".join(f"{head}\n\n{body}" for head, body in parts)

    if not content.strip():
        raise PlazaError("EMPTY_CONTENT", "剧本正文为空,无法上架")
    return content, source_id


def publish_screenplay(conn, user_id: str, inp: "PublishScreenplayInput") -> dict:
    """把剧创态作品(全局 / 分集 / 两者)上架到广场(item8)。"""
    title = (inp.title or "").strip()
    if not title:
        raise PlazaError("TITLE_REQUIRED", "作品名不能为空")
    if len(title) > 60:
        raise PlazaError("TITLE_TOO_LONG", "作品名最多 60 字")

    novel = fetch_one(
        conn,
        "SELECT id, title FROM sp_novels WHERE id=? AND user_id=?",
        (inp.novel_id, user_id),
    )
    if novel is None:
        raise PlazaError("SOURCE_NOT_FOUND", "找不到该剧本或无权发布")

    content, source_id = _render_screenplay_content(conn, user_id, inp)

    grad = inp.cover_gradient if 1 <= inp.cover_gradient <= _GRADIENT_COUNT else 1
    word_count = len(content)
    summary = (inp.summary or "").strip() or _excerpt(content)
    original_title = novel["title"]
    now = iso_now()
    work_id = str(uuid.uuid4())

    execute(
        conn,
        """INSERT INTO published_works
            (id, user_id, source_type, source_id, project_id, title, summary,
             mode, original_title, cover_image_path, cover_gradient, content,
             word_count, like_count, read_count, is_public, allow_download,
             published_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,?,?,?,?)""",
        (
            work_id, user_id, "screenplay", source_id, None, title, summary,
            "screenplay", original_title, inp.cover_image_path, grad, content,
            word_count, _norm_bool(inp.is_public), _norm_bool(inp.allow_download),
            now, now,
        ),
    )
    return get_work_meta(conn, work_id, viewer_id=user_id)


def _row_to_card(row, liked: bool = False) -> dict:
    """published_works 行 → 列表卡片 dict(不含正文)。"""
    return {
        "id": row["id"],
        "title": row["title"],
        "summary": row["summary"],
        "mode": row["mode"],
        "original_title": row["original_title"],
        "cover_image_path": row["cover_image_path"],
        "cover_gradient": row["cover_gradient"],
        "word_count": row["word_count"],
        "like_count": row["like_count"],
        "read_count": row["read_count"],
        "published_at": row["published_at"],
        "author_id": row["user_id"],
        "author_nickname": row["nickname"] if "nickname" in row.keys() else None,
        "author_avatar_url": row["avatar_url"] if "avatar_url" in row.keys() else None,
        "source_type": row["source_type"] if "source_type" in row.keys() else "simulation",
        "is_public": row["is_public"] if "is_public" in row.keys() else 1,
        "allow_download": row["allow_download"] if "allow_download" in row.keys() else 1,
        "liked": liked,
    }


def _hot_score(row, now_ts: float) -> float:
    """热度分 = (赞 + 阅读*0.2 + 1) / (龄小时 + 2)^1.5,新作(<48h)再 ×1.5。

    老作品随时间自然下沉(不霸榜);新作有曝光窗口(不被埋)。
    """
    from datetime import datetime

    try:
        pub = datetime.fromisoformat(str(row["published_at"])).timestamp()
    except Exception:  # noqa: BLE001
        pub = now_ts
    # 防御(2026-06-25):like_count/read_count 理论 NOT NULL,但旧数据/边界值兜底成 0,
    # 任何类型异常都不让热度分崩掉(否则 sorted 整条 500,广场首页白屏)。
    try:
        likes = float(row["like_count"] or 0)
        reads = float(row["read_count"] or 0)
    except Exception:  # noqa: BLE001
        likes = reads = 0.0
    age_hours = max(0.0, (now_ts - pub) / 3600.0)
    base = (likes + reads * 0.2 + 1) / ((age_hours + 2) ** 1.5)
    if age_hours < _FRESH_HOURS:
        base *= _FRESH_BOOST
    return base


def list_works(
    conn, viewer_id: str, sort: str = "hot", limit: int = 24, offset: int = 0,
) -> dict:
    """列出广场公开作品。sort ∈ hot|new|classic。带 viewer 的 liked 标记。"""
    rows = fetch_all(
        conn,
        """SELECT w.*, u.nickname AS nickname, u.avatar_url AS avatar_url
           FROM published_works w
           LEFT JOIN users u ON w.user_id = u.id
           WHERE w.is_public = 1""",
    )
    if sort == "new":
        rows = sorted(rows, key=lambda r: r["published_at"], reverse=True)
    elif sort == "classic":
        rows = sorted(rows, key=lambda r: (r["like_count"], r["published_at"]), reverse=True)
    else:  # hot
        # 防御(2026-06-25):热度排序任何异常都降级到"最新"序,绝不让 /plaza/works?sort=hot
        # 抛 500(被 nginx 错误页伪装成"服务暂时不可用",广场首页直接打不开)。
        try:
            from datetime import datetime, timezone
            now_ts = datetime.now(timezone.utc).timestamp()
            rows = sorted(rows, key=lambda r: _hot_score(r, now_ts), reverse=True)
        except Exception:  # noqa: BLE001
            rows = sorted(rows, key=lambda r: r["published_at"], reverse=True)

    total = len(rows)
    page = rows[offset:offset + limit]
    # 该 viewer 点过赞的集合
    liked_ids: set = set()
    if page:
        ids = [r["id"] for r in page]
        ph = ",".join("?" * len(ids))
        lk = fetch_all(
            conn,
            f"SELECT work_id FROM published_work_likes WHERE user_id=? AND work_id IN ({ph})",
            (viewer_id, *ids),
        )
        liked_ids = {r["work_id"] for r in lk}
    return {
        "items": [_row_to_card(r, liked=r["id"] in liked_ids) for r in page],
        "total": total,
        "sort": sort,
        "offset": offset,
        "limit": limit,
    }


def list_my_works(conn, user_id: str) -> list[dict]:
    """用户自己发布的作品(含已下架),按时间倒序。"""
    rows = fetch_all(
        conn,
        """SELECT w.*, u.nickname AS nickname, u.avatar_url AS avatar_url
           FROM published_works w LEFT JOIN users u ON w.user_id=u.id
           WHERE w.user_id=? ORDER BY w.published_at DESC""",
        (user_id,),
    )
    return [{**_row_to_card(r), "is_public": r["is_public"]} for r in rows]


def get_work_meta(conn, work_id: str, viewer_id: str) -> dict:
    """取单作品卡片元信息(不 +阅读,不含正文)。"""
    row = fetch_one(
        conn,
        """SELECT w.*, u.nickname AS nickname, u.avatar_url AS avatar_url
           FROM published_works w LEFT JOIN users u ON w.user_id=u.id
           WHERE w.id=?""",
        (work_id,),
    )
    if row is None:
        raise ResourceNotFoundOrForbidden("published_work", work_id)
    liked = fetch_one(
        conn,
        "SELECT 1 FROM published_work_likes WHERE work_id=? AND user_id=?",
        (work_id, viewer_id),
    ) is not None
    return _row_to_card(row, liked=liked)


def get_work_detail(conn, work_id: str, viewer_id: str) -> dict:
    """作品详情落地页数据(v5)—— 元信息 + 预览节选 + 权限,**不 +阅读量、不返全文**。

    私人作品(is_public=0)仅作者可见详情;其余人 404。
    """
    row = fetch_one(
        conn,
        """SELECT w.*, u.nickname AS nickname, u.avatar_url AS avatar_url
           FROM published_works w LEFT JOIN users u ON w.user_id=u.id
           WHERE w.id=?""",
        (work_id,),
    )
    if row is None:
        raise ResourceNotFoundOrForbidden("published_work", work_id)
    is_owner = (row["user_id"] == viewer_id)
    if row["is_public"] != 1 and not is_owner:
        raise ResourceNotFoundOrForbidden("published_work", work_id)

    liked = fetch_one(
        conn,
        "SELECT 1 FROM published_work_likes WHERE work_id=? AND user_id=?",
        (work_id, viewer_id),
    ) is not None
    meta = _row_to_card(row, liked=liked)
    meta["is_owner"] = is_owner
    allow_dl = meta.get("allow_download", 1)
    meta["can_download"] = bool(is_owner or (row["is_public"] == 1 and allow_dl == 1))

    # 预览:漫画返回页图 URL 数组;文本返回节选。
    if meta.get("source_type") == "comic":
        try:
            import json as _json
            pages = _json.loads(row["content"] or "[]")
            meta["comic_pages"] = pages if isinstance(pages, list) else []
        except Exception:  # noqa: BLE001
            meta["comic_pages"] = []
        meta["preview"] = None
    else:
        meta["preview"] = _excerpt(row["content"] or "", 320)
    return meta


def read_work(conn, work_id: str, viewer_id: str) -> dict:
    """在线阅读:返回正文 + 元信息,并阅读量 +1。私人作品仅作者可读。"""
    meta = get_work_meta(conn, work_id, viewer_id)
    row = fetch_one(
        conn,
        "SELECT content, is_public, user_id FROM published_works WHERE id=?",
        (work_id,),
    )
    if row is None or (row["is_public"] != 1 and row["user_id"] != viewer_id):
        raise ResourceNotFoundOrForbidden("published_work", work_id)
    execute(conn, "UPDATE published_works SET read_count = read_count + 1 WHERE id=?", (work_id,))
    meta["read_count"] = (meta.get("read_count") or 0) + 1
    meta["content"] = row["content"]
    return meta


def get_work_for_download(conn, work_id: str, viewer_id: str) -> dict:
    """下载正文(不 +阅读量)。权限:作者本人,或 公开 + 允许下载。

    返回 {title, content, source_type, mode}。无权则 raise。
    """
    row = fetch_one(
        conn,
        "SELECT title, content, source_type, mode, is_public, allow_download, user_id "
        "FROM published_works WHERE id=?",
        (work_id,),
    )
    if row is None:
        raise ResourceNotFoundOrForbidden("published_work", work_id)
    is_owner = (row["user_id"] == viewer_id)
    allow_dl = row["allow_download"] if "allow_download" in row.keys() else 1
    can = is_owner or (row["is_public"] == 1 and allow_dl == 1)
    if not can:
        raise PlazaError("DOWNLOAD_FORBIDDEN", "该作品不允许下载")
    return {
        "title": row["title"],
        "content": row["content"] or "",
        "source_type": row["source_type"],
        "mode": row["mode"],
    }


def set_work_visibility(
    conn, work_id: str, user_id: str,
    is_public: Optional[int] = None, allow_download: Optional[int] = None,
) -> dict:
    """作者改作品权限(公开/私人 + 是否允许下载)。"""
    row = fetch_one(conn, "SELECT user_id FROM published_works WHERE id=?", (work_id,))
    if row is None or row["user_id"] != user_id:
        raise ResourceNotFoundOrForbidden("published_work", work_id)
    sets, args = [], []
    if is_public is not None:
        sets.append("is_public=?")
        args.append(_norm_bool(is_public))
    if allow_download is not None:
        sets.append("allow_download=?")
        args.append(_norm_bool(allow_download))
    if sets:
        sets.append("updated_at=?")
        args.append(iso_now())
        args.append(work_id)
        execute(conn, f"UPDATE published_works SET {', '.join(sets)} WHERE id=?", tuple(args))
    return get_work_meta(conn, work_id, viewer_id=user_id)


def _comment_to_dict(row, viewer_id: str) -> dict:
    return {
        "id": row["id"],
        "work_id": row["work_id"],
        "content": row["content"],
        "created_at": row["created_at"],
        "author_id": row["user_id"],
        "author_nickname": row["nickname"] if "nickname" in row.keys() else None,
        "author_avatar_url": row["avatar_url"] if "avatar_url" in row.keys() else None,
        "is_mine": row["user_id"] == viewer_id,
    }


def list_comments(conn, work_id: str, viewer_id: str) -> list[dict]:
    """列出作品评论(新→旧)。作品必须存在且(公开 或 viewer 是作者)。"""
    w = fetch_one(conn, "SELECT is_public, user_id FROM published_works WHERE id=?", (work_id,))
    if w is None or (w["is_public"] != 1 and w["user_id"] != viewer_id):
        raise ResourceNotFoundOrForbidden("published_work", work_id)
    rows = fetch_all(
        conn,
        """SELECT c.*, u.nickname AS nickname, u.avatar_url AS avatar_url
           FROM published_work_comments c LEFT JOIN users u ON c.user_id=u.id
           WHERE c.work_id=? ORDER BY c.created_at DESC""",
        (work_id,),
    )
    return [_comment_to_dict(r, viewer_id) for r in rows]


def add_comment(conn, work_id: str, user_id: str, content: str) -> dict:
    """发表评论。作品必须存在且(公开 或 本人作品)。content 1-1000 字。"""
    text = (content or "").strip()
    if not text:
        raise PlazaError("COMMENT_EMPTY", "评论内容不能为空")
    if len(text) > 1000:
        raise PlazaError("COMMENT_TOO_LONG", "评论最多 1000 字")
    w = fetch_one(conn, "SELECT is_public, user_id FROM published_works WHERE id=?", (work_id,))
    if w is None or (w["is_public"] != 1 and w["user_id"] != user_id):
        raise ResourceNotFoundOrForbidden("published_work", work_id)
    cid = str(uuid.uuid4())
    now = iso_now()
    execute(
        conn,
        "INSERT INTO published_work_comments (id, work_id, user_id, content, created_at) VALUES (?,?,?,?,?)",
        (cid, work_id, user_id, text, now),
    )
    row = fetch_one(
        conn,
        """SELECT c.*, u.nickname AS nickname, u.avatar_url AS avatar_url
           FROM published_work_comments c LEFT JOIN users u ON c.user_id=u.id
           WHERE c.id=?""",
        (cid,),
    )
    return _comment_to_dict(row, user_id)


def delete_comment(conn, comment_id: str, user_id: str) -> None:
    """删评论。权限:评论人本人,或该作品的作者。"""
    row = fetch_one(
        conn,
        """SELECT c.user_id AS commenter, w.user_id AS work_owner
           FROM published_work_comments c
           JOIN published_works w ON c.work_id = w.id
           WHERE c.id=?""",
        (comment_id,),
    )
    if row is None:
        raise ResourceNotFoundOrForbidden("published_work_comment", comment_id)
    if user_id != row["commenter"] and user_id != row["work_owner"]:
        raise ResourceNotFoundOrForbidden("published_work_comment", comment_id)
    execute(conn, "DELETE FROM published_work_comments WHERE id=?", (comment_id,))


def set_like(conn, work_id: str, user_id: str, liked: bool) -> dict:
    """点赞 / 取消点赞(幂等)。返回 {liked, like_count}。"""
    exists = fetch_one(conn, "SELECT 1 FROM published_works WHERE id=?", (work_id,))
    if exists is None:
        raise ResourceNotFoundOrForbidden("published_work", work_id)
    has = fetch_one(
        conn,
        "SELECT 1 FROM published_work_likes WHERE work_id=? AND user_id=?",
        (work_id, user_id),
    ) is not None
    if liked and not has:
        execute(
            conn,
            "INSERT INTO published_work_likes (work_id, user_id, created_at) VALUES (?,?,?)",
            (work_id, user_id, iso_now()),
        )
        execute(conn, "UPDATE published_works SET like_count = like_count + 1 WHERE id=?", (work_id,))
    elif not liked and has:
        execute(
            conn,
            "DELETE FROM published_work_likes WHERE work_id=? AND user_id=?",
            (work_id, user_id),
        )
        execute(
            conn,
            "UPDATE published_works SET like_count = MAX(0, like_count - 1) WHERE id=?",
            (work_id,),
        )
    row = fetch_one(conn, "SELECT like_count FROM published_works WHERE id=?", (work_id,))
    return {"liked": liked, "like_count": row["like_count"] if row else 0}


def unpublish(conn, work_id: str, user_id: str) -> None:
    """作者下架自己的作品(置 is_public=0,保留数据)。"""
    row = fetch_one(conn, "SELECT user_id FROM published_works WHERE id=?", (work_id,))
    if row is None or row["user_id"] != user_id:
        raise ResourceNotFoundOrForbidden("published_work", work_id)
    execute(
        conn,
        "UPDATE published_works SET is_public=0, updated_at=? WHERE id=?",
        (iso_now(), work_id),
    )


def publish_comic(conn, user_id: str, inp: "PublishComicInput") -> dict:
    """把已完成的漫画(state=done + 整页已排版)上架到广场(v5)。

    content 存已排版整页 PNG 的稳定 URL 数组(/api/comic-composed/<id>/page_N.png,
    这些是本地永久文件,非 vendor 临时链;panels_json 里的 vendor URL 会过期,不用)。
    """
    title = (inp.title or "").strip()
    if not title:
        raise PlazaError("TITLE_REQUIRED", "作品名不能为空")
    if len(title) > 60:
        raise PlazaError("TITLE_TOO_LONG", "作品名最多 60 字")

    comic = fetch_one(
        conn,
        "SELECT id, name, state FROM comic_projects WHERE id=? AND user_id=?",
        (inp.comic_id, user_id),
    )
    if comic is None:
        raise PlazaError("SOURCE_NOT_FOUND", "找不到该漫画或无权发布")
    if comic["state"] != "done":
        raise PlazaError("SOURCE_NOT_DONE", "只能上架已完成的漫画")

    pages = fetch_all(
        conn,
        """SELECT page_index, composed_url FROM comic_pages
           WHERE comic_id=? AND composed_url IS NOT NULL AND composed_url <> ''
           ORDER BY page_index ASC""",
        (inp.comic_id,),
    )
    page_urls = [p["composed_url"] for p in pages]
    if not page_urls:
        raise PlazaError("EMPTY_CONTENT", "漫画还没有排版好的整页,无法上架")

    import json as _json
    content = _json.dumps(page_urls, ensure_ascii=False)
    grad = inp.cover_gradient if 1 <= inp.cover_gradient <= _GRADIENT_COUNT else 1
    # 封面:用户未上传则用第一页
    cover = inp.cover_image_path or page_urls[0]
    summary = (inp.summary or "").strip() or f"漫画作品 · 共 {len(page_urls)} 页"
    now = iso_now()
    work_id = str(uuid.uuid4())

    execute(
        conn,
        """INSERT INTO published_works
            (id, user_id, source_type, source_id, project_id, title, summary,
             mode, original_title, cover_image_path, cover_gradient, content,
             word_count, like_count, read_count, is_public, allow_download,
             published_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,?,?,?,?)""",
        (
            work_id, user_id, "comic", inp.comic_id, None, title, summary,
            "cycle", None, cover, grad, content,
            len(page_urls),   # word_count 复用为"页数",前端按 source_type 显"N 页"
            _norm_bool(inp.is_public), _norm_bool(inp.allow_download),
            now, now,
        ),
    )
    return get_work_meta(conn, work_id, viewer_id=user_id)


def list_publishable_comics(conn, user_id: str) -> list[dict]:
    """用户可上架的漫画:state=done 且有 ≥1 张已排版整页。"""
    rows = fetch_all(
        conn,
        """SELECT c.id, c.name, c.updated_at,
                  (SELECT COUNT(*) FROM comic_pages p
                    WHERE p.comic_id=c.id AND p.composed_url IS NOT NULL AND p.composed_url<>'') AS page_count,
                  (SELECT composed_url FROM comic_pages p2
                    WHERE p2.comic_id=c.id AND p2.composed_url IS NOT NULL AND p2.composed_url<>''
                    ORDER BY p2.page_index ASC LIMIT 1) AS cover_url
           FROM comic_projects c
           WHERE c.user_id=? AND c.state='done'
           ORDER BY c.updated_at DESC LIMIT 100""",
        (user_id,),
    )
    out = []
    for r in rows:
        if not r["page_count"]:
            continue   # 没有排版好的页 → 不可上架
        out.append({
            "comic_id": r["id"],
            "name": r["name"],
            "page_count": r["page_count"],
            "cover_url": r["cover_url"],
            "updated_at": r["updated_at"],
        })
    return out


def list_publishable_sims(conn, user_id: str) -> list[dict]:
    """用户可上架的素材:已 done 且有正文、尚未上架的 simulation。"""
    rows = fetch_all(
        conn,
        """SELECT s.id, s.project_id, s.narrative_summary, s.created_at,
                  p.name AS project_name, p.mode AS mode
           FROM simulations s
           LEFT JOIN projects p ON s.project_id = p.id
           WHERE s.user_id=? AND s.state='done'
             AND s.narrative IS NOT NULL AND s.narrative <> ''
             AND s.id NOT IN (
                 SELECT source_id FROM published_works
                 WHERE user_id=? AND source_type='simulation' AND source_id IS NOT NULL
             )
           ORDER BY s.created_at DESC LIMIT 100""",
        (user_id, user_id),
    )
    out = []
    for r in rows:
        mode = r["mode"] or "initial"
        out.append({
            "sim_id": r["id"],
            "project_id": r["project_id"],
            "project_name": r["project_name"],
            "mode": mode,
            "original_title": None if mode == "initial" else r["project_name"],
            "summary": r["narrative_summary"],
            "created_at": r["created_at"],
        })
    return out


def list_publishable_screenplays(conn, user_id: str) -> list[dict]:
    """用户可上架的剧创态素材(item8):有「全局剧本」或「分集方案」的 novel。

    每个 novel 返回:是否有全局剧本 + 该 novel 下的分集方案列表(供前端选 全局/分集/both)。
    异常隔离:剧创态表缺失 / 查询异常时返 [](不阻断广场发布主流程)。
    """
    try:
        novels = fetch_all(
            conn,
            """SELECT id, title, uploaded_at FROM sp_novels
                WHERE user_id = ? ORDER BY uploaded_at DESC LIMIT 100""",
            (user_id,),
        )
    except Exception:  # noqa: BLE001 —— 剧创态未初始化等
        return []

    out: list[dict] = []
    for n in novels:
        try:
            sp = fetch_one(
                conn,
                """SELECT id FROM sp_screenplays
                    WHERE novel_id = ? AND yaml_text <> ''
                    ORDER BY created_at DESC LIMIT 1""",
                (n["id"],),
            )
            plans = fetch_all(
                conn,
                """SELECT id, scheme_name, episode_count, preset, created_at
                    FROM sp_episode_plans WHERE novel_id = ?
                    ORDER BY created_at DESC""",
                (n["id"],),
            )
        except Exception:  # noqa: BLE001
            continue

        has_global = sp is not None
        episode_plans = [
            {
                "plan_id": p["id"],
                "scheme_name": p["scheme_name"],
                "episode_count": p["episode_count"],
                "preset": p["preset"],
            }
            for p in plans
        ]
        if not has_global and not episode_plans:
            continue   # 该 novel 没有任何可发布内容,跳过

        # 该 novel 已上架的剧本(去重提示用,不强制拦)
        out.append({
            "novel_id": n["id"],
            "novel_title": n["title"],
            "has_global": has_global,
            "screenplay_id": sp["id"] if sp else None,
            "episode_plans": episode_plans,
            "created_at": n["uploaded_at"],
        })
    return out
