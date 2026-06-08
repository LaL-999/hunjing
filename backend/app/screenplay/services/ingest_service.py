"""小说摄入服务 — 解析 + 落库。

接 parser 的 ParsedNovel,展平成 SQLite 三表(sp_novels / sp_chapters / sp_paragraphs)。

阶段 3.5(2026-06-08):全部接口加 user_id 参数,数据按用户隔离。
  - persist_novel 必填 user_id,写入 sp_novels.user_id
  - get/list/delete 必填 user_id,SQL WHERE user_id = ? 过滤
  - get_chapter_paragraphs 通过 JOIN sp_chapters → sp_novels 校验 user_id
  - 用户 A 无法 GET/DELETE 用户 B 的 novel(返 None / False = 视为不存在)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.screenplay.db.connection import get_connection
from app.screenplay.parsers import ParsedNovel


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


def persist_novel(
    parsed: ParsedNovel,
    source_filename: str,
    user_id: str,
) -> dict:
    """把解析结果存进 SQLite,返摄入摘要 dict。

    Args:
        parsed: 解析器输出
        source_filename: 原始上传文件名(只存档,不参与解析)
        user_id: 父平台 users.id,写入 sp_novels.user_id

    Returns:
        {
          "novel_id": str, "title": str, "source_format": str,
          "total_chapters": int, "total_chars": int,
          "chapters": [{"id": str, "number": int, "title": str|None,
                        "paragraph_count": int, "char_count": int}, ...]
        }
    """
    novel_id = _new_id()
    now = _now_iso()

    conn = get_connection()
    try:
        # 1. novel 行
        conn.execute(
            """INSERT INTO sp_novels
               (id, user_id, title, source_format, source_filename,
                total_chars, total_chapters, uploaded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                novel_id,
                user_id,
                parsed.title,
                parsed.source_format,
                source_filename,
                parsed.total_chars,
                parsed.total_chapters,
                now,
            ),
        )

        # 2. 每章 + 段落(章节 / 段落通过 novel_id 间接限定到 user)
        chapter_summaries: list[dict] = []
        for ch in parsed.chapters:
            chapter_id = _new_id()
            conn.execute(
                """INSERT INTO sp_chapters
                   (id, novel_id, number, title, paragraph_count, char_count)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    chapter_id,
                    novel_id,
                    ch.number,
                    ch.title,
                    ch.paragraph_count,
                    ch.char_count,
                ),
            )

            para_rows = [
                (_new_id(), chapter_id, i + 1, text)
                for i, text in enumerate(ch.paragraphs)
            ]
            conn.executemany(
                """INSERT INTO sp_paragraphs
                   (id, chapter_id, index_in_chapter, text)
                   VALUES (?, ?, ?, ?)""",
                para_rows,
            )

            chapter_summaries.append({
                "id": chapter_id,
                "number": ch.number,
                "title": ch.title,
                "paragraph_count": ch.paragraph_count,
                "char_count": ch.char_count,
            })

        conn.commit()
    finally:
        conn.close()

    return {
        "novel_id": novel_id,
        "title": parsed.title,
        "source_format": parsed.source_format,
        "total_chapters": parsed.total_chapters,
        "total_chars": parsed.total_chars,
        "chapters": chapter_summaries,
    }


def delete_novel(novel_id: str, user_id: str) -> bool:
    """删除小说(必须是当前用户拥有的)— chapters / paragraphs / story_bible /
    screenplays 走 ON DELETE CASCADE 自动清。

    Returns:
        True 删除成功 / False 不存在或不属于该用户
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM sp_novels WHERE id = ? AND user_id = ?",
            (novel_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def list_novels(user_id: str) -> list[dict]:
    """当前用户上传过的所有小说(按上传时间倒序)。

    2026-06-08 bug fix:返回 linked_project_id 让前端书架直接显示绑定状态,
    避免之前的 N+1 问题(每本小说额外发 1 个 getNovel 请求只为读这一字段)。
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT id, title, source_format, source_filename,
                      total_chars, total_chapters, uploaded_at,
                      linked_project_id
                 FROM sp_novels
                WHERE user_id = ?
             ORDER BY uploaded_at DESC""",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_novel(novel_id: str, user_id: str) -> dict | None:
    """单本详情 + 章节列表(不含段落正文)。

    返 None 当 novel 不存在 OR 不属于当前用户(隔离 = 无知,不是"403 forbidden")
    """
    conn = get_connection()
    try:
        novel_row = conn.execute(
            "SELECT * FROM sp_novels WHERE id = ? AND user_id = ?",
            (novel_id, user_id),
        ).fetchone()
        if novel_row is None:
            return None

        chapter_rows = conn.execute(
            """SELECT id, number, title, paragraph_count, char_count
                 FROM sp_chapters
                WHERE novel_id = ?
             ORDER BY number""",
            (novel_id,),
        ).fetchall()

        out = dict(novel_row)
        out["chapters"] = [dict(r) for r in chapter_rows]
        return out
    finally:
        conn.close()


def get_chapter_paragraphs(chapter_id: str, user_id: str) -> list[dict] | None:
    """单章全部段落正文(校验 chapter→novel→user 链)。

    返 None 当 chapter 不存在 OR 章节所属 novel 不属于当前用户。
    """
    conn = get_connection()
    try:
        chapter = conn.execute(
            """SELECT c.id
                 FROM sp_chapters c
                 JOIN sp_novels n ON n.id = c.novel_id
                WHERE c.id = ? AND n.user_id = ?""",
            (chapter_id, user_id),
        ).fetchone()
        if chapter is None:
            return None
        rows = conn.execute(
            """SELECT index_in_chapter, text
                 FROM sp_paragraphs
                WHERE chapter_id = ?
             ORDER BY index_in_chapter""",
            (chapter_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def is_novel_owned_by_user(novel_id: str, user_id: str) -> bool:
    """轻量级 helper:查 novel 是否属于该用户(给 service 层做权限校验用)。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM sp_novels WHERE id = ? AND user_id = ?",
            (novel_id, user_id),
        ).fetchone()
        return row is not None
    finally:
        conn.close()
