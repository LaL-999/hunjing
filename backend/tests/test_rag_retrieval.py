"""Sprint 6.A2 M3.C(2026-05-18)— RAG 召回测试。

覆盖:
  - tokenize 中文 2-gram 切分正确
  - BM25 评分:含 query gram 的 chunk 排前面
  - retrieve_relevant_chunks 空项目 / 空 query / 全部 chunk_text 空 → 返空
  - lazy backfill:NULL chunk_text 被 re-parse + UPDATE
  - min_score 阈值过滤
"""
from __future__ import annotations

import json
import uuid


def _seed_project_with_chunks(
    chunks_texts: list[str | None],
    work_text: str | None = None,
) -> tuple[str, str, str]:
    """造 user + project + upload + job + chunks。
    返回 (user_id, project_id, job_id)。
    chunks_texts[i]=None 模拟 "老数据"(chunk_text NULL,触发 lazy backfill)。
    """
    from app.db import get_connection
    from app.services.project_service import iso_now
    from pathlib import Path
    import os
    import tempfile

    user_id = uuid.uuid4().hex
    pid = uuid.uuid4().hex
    upload_id = uuid.uuid4().hex
    job_id = uuid.uuid4().hex
    now = iso_now()

    # 若给了 work_text(模拟原作整文),写到临时文件让 backfill 能 parse
    storage_path = "/tmp/nonexistent.txt"
    if work_text is not None:
        tmp = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", suffix=".txt", delete=False,
        )
        tmp.write(work_text)
        tmp.close()
        storage_path = tmp.name

    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO users (id, email, plan, created_at, updated_at)
               VALUES (?, ?, 'free', ?, ?)""",
            (user_id, f"{user_id[:8]}@test.local", now, now),
        )
        conn.execute(
            """INSERT INTO projects (id, user_id, name, type, tags, mode,
                                       created_at, updated_at,
                                       world_baseline_json, graph_strength_threshold)
               VALUES (?, ?, 'RAG 测试', 'novel', '[]', 'middle', ?, ?, '{}', 30)""",
            (pid, user_id, now, now),
        )
        conn.execute(
            """INSERT INTO uploads (id, project_id, user_id, filename, storage_path,
                                     mime_type, size_bytes, sha256,
                                     parsed_text_chars, state, uploaded_at)
               VALUES (?, ?, ?, 'test.txt', ?, 'text/plain', 100, ?,
                       1000, 'ready', ?)""",
            (upload_id, pid, user_id, storage_path, f"hash_{upload_id[:8]}", now),
        )
        conn.execute(
            """INSERT INTO graph_extraction_jobs
                (id, project_id, upload_id, user_id, state, is_admin_retag,
                 started_at)
               VALUES (?, ?, ?, ?, 'done', 0, ?)""",
            (job_id, pid, upload_id, user_id, now),
        )
        for idx_0, txt in enumerate(chunks_texts):
            idx_1 = idx_0 + 1
            conn.execute(
                """INSERT INTO extract_chunk_results
                   (job_id, chunk_index, chunk_text_hash, graph_json,
                    tokens_input, tokens_output, completed_at, chunk_text)
                   VALUES (?, ?, 'hash', '{}', 0, 0, ?, ?)""",
                (job_id, idx_1, now, txt),
            )
        conn.commit()
    finally:
        conn.close()
    return user_id, pid, job_id


# ============================================================
# Tokenize
# ============================================================

def test_tokenize_chinese_bigrams():
    """中文 2-gram tokenize 应丢标点 + 提取连续 2 字组合。"""
    from app.services.rag_retrieval import _tokenize
    grams = _tokenize("林黛玉,泪洒潇湘馆。")
    # 去掉标点后是 "林黛玉泪洒潇湘馆";2-gram = 林黛/黛玉/玉泪/泪洒/洒潇/潇湘/湘馆
    assert "林黛" in grams
    assert "黛玉" in grams
    assert "潇湘" in grams
    assert "湘馆" in grams
    # 标点不应出现
    assert "," not in "".join(grams)


def test_tokenize_empty_input():
    from app.services.rag_retrieval import _tokenize
    assert _tokenize("") == []
    assert _tokenize("   \n") == []
    assert _tokenize("。") == []   # 全标点


# ============================================================
# retrieve_relevant_chunks 基础
# ============================================================

def test_retrieve_empty_project_returns_empty():
    from app.db import get_connection
    from app.services.rag_retrieval import retrieve_relevant_chunks
    conn = get_connection()
    try:
        # 空 project_id
        results = retrieve_relevant_chunks(
            conn, "no-such-project", "林黛玉", top_k=3,
        )
        assert results == []
    finally:
        conn.close()


def test_retrieve_empty_query_returns_empty():
    from app.db import get_connection
    from app.services.rag_retrieval import retrieve_relevant_chunks
    _, pid, _ = _seed_project_with_chunks(["林黛玉在潇湘馆中"])
    conn = get_connection()
    try:
        assert retrieve_relevant_chunks(conn, pid, "", top_k=3) == []
        assert retrieve_relevant_chunks(conn, pid, "   ", top_k=3) == []
    finally:
        conn.close()


def test_retrieve_relevance_ordering(monkeypatch):
    """3 chunks,query '林黛玉' → 含'林黛玉'的 chunk 排第一。"""
    from app.db import get_connection
    from app.services.rag_retrieval import retrieve_relevant_chunks
    _, pid, _ = _seed_project_with_chunks([
        "贾母在荣禧堂喝茶,问起家务事。",            # 无 林黛玉
        "林黛玉独自坐在潇湘馆中,夜深咳嗽。",       # 含林黛玉 + 潇湘馆
        "宝玉对袭人说着家常。",                     # 无林黛玉
    ])
    conn = get_connection()
    try:
        results = retrieve_relevant_chunks(
            conn, pid, "林黛玉 潇湘馆", top_k=3, min_score=0.0,
        )
        # 至少召回 1 个
        assert len(results) >= 1
        # 第一个应是含"林黛玉"的 chunk
        assert "林黛玉" in results[0].text
    finally:
        conn.close()


def test_retrieve_skips_null_chunk_text_when_no_backfill_source():
    """chunk_text 全 NULL 且无 upload_text → 返空(不抛)。"""
    from app.db import get_connection
    from app.services.rag_retrieval import retrieve_relevant_chunks
    # storage_path 指向不存在的文件 → backfill 必然失败 → 全空 → 返空召回
    _, pid, _ = _seed_project_with_chunks([None, None])
    conn = get_connection()
    try:
        results = retrieve_relevant_chunks(conn, pid, "查询", top_k=3)
        assert results == []
    finally:
        conn.close()


def test_lazy_backfill_fills_chunk_text_from_upload():
    """老数据 chunk_text NULL,upload 文件可访问 → backfill 填回 + 召回成功。"""
    from app.db import get_connection
    from app.services.rag_retrieval import retrieve_relevant_chunks
    work_text = "林黛玉走进潇湘馆,看见竹影摇曳。\n\n她坐下,忍不住咳嗽。"
    _, pid, job_id = _seed_project_with_chunks([None], work_text=work_text)
    conn = get_connection()
    try:
        results = retrieve_relevant_chunks(
            conn, pid, "林黛玉 潇湘馆 咳嗽", top_k=3, min_score=0.0,
        )
        assert len(results) == 1
        assert "林黛玉" in results[0].text

        # 验证 chunk_text 已被 backfill 落库
        row = conn.execute(
            "SELECT chunk_text FROM extract_chunk_results WHERE job_id=?",
            (job_id,),
        ).fetchone()
        assert row["chunk_text"] is not None
        assert "林黛玉" in row["chunk_text"]
    finally:
        conn.close()


def test_retrieve_min_score_filter():
    """min_score 阈值过滤 — 高阈值时无 chunk 通过 → 返空。"""
    from app.db import get_connection
    from app.services.rag_retrieval import retrieve_relevant_chunks
    _, pid, _ = _seed_project_with_chunks([
        "完全无关的内容,讲的是星空和宇宙。",
    ])
    conn = get_connection()
    try:
        # 极高 min_score → 过滤所有
        results = retrieve_relevant_chunks(
            conn, pid, "查询完全不匹配", top_k=3, min_score=999.0,
        )
        assert results == []
    finally:
        conn.close()


def test_retrieve_snippet_max_chars_truncates():
    """长 chunk 被截到 snippet_max_chars + '...'。"""
    from app.db import get_connection
    from app.services.rag_retrieval import retrieve_relevant_chunks
    long_chunk = "林黛玉" + ("某些内容" * 500)   # 4000+ 字
    _, pid, _ = _seed_project_with_chunks([long_chunk])
    conn = get_connection()
    try:
        results = retrieve_relevant_chunks(
            conn, pid, "林黛玉", top_k=1, snippet_max_chars=200, min_score=0.0,
        )
        assert len(results) == 1
        assert len(results[0].text) <= 220   # 200 + "..." + 安全余量
        assert results[0].text.endswith("...")
    finally:
        conn.close()
