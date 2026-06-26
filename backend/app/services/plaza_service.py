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
             word_count, like_count, read_count, is_public, published_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,1,?,?)""",
        (
            work_id, user_id, "simulation", inp.sim_id, project_id, title, summary,
            mode, original_title, inp.cover_image_path, grad, content,
            word_count, now, now,
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


def read_work(conn, work_id: str, viewer_id: str) -> dict:
    """在线阅读:返回正文 + 元信息,并阅读量 +1。"""
    meta = get_work_meta(conn, work_id, viewer_id)
    row = fetch_one(conn, "SELECT content, is_public FROM published_works WHERE id=?", (work_id,))
    if row is None or row["is_public"] != 1:
        raise ResourceNotFoundOrForbidden("published_work", work_id)
    execute(conn, "UPDATE published_works SET read_count = read_count + 1 WHERE id=?", (work_id,))
    meta["read_count"] = (meta.get("read_count") or 0) + 1
    meta["content"] = row["content"]
    return meta


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
