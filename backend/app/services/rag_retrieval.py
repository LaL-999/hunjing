"""Sprint 6.A2 M3.C(2026-05-18)— RAG 召回(零依赖 BM25 中文友好版)。

灵魂续写 mode='evolution' 每幕调用,给 agent 喂"剧情相关的原著 chunk 片段"。

设计:
  - **零新依赖**:不引 jieba / sentence-transformers / FAISS
  - **中文友好**:用字符 2-gram(适应中文无空格分词)+ 高频字过滤
  - **BM25 评分**:经典信息检索公式,k1=1.5, b=0.75 默认参数
  - **lazy backfill**:首次访问某 project 时 chunk_text 若空 → re-parse upload + split + UPDATE

接口:
  retrieve_relevant_chunks(project_id, query, top_k=3, max_chars=2000)
    → list[ChunkSnippet]:相关度倒序 top_k 个 chunk 摘要(每条限长)

调用方:
  agent_evolution_engine._run_scene_dialogue 每幕开始时调,把召回 chunk 注入 agent prompt。
"""
from __future__ import annotations

import logging
import math
import re
import sqlite3
from dataclasses import dataclass
from typing import Optional

from app.db import execute, fetch_all, fetch_one

logger = logging.getLogger(__name__)


# ============================================================
# 配置
# ============================================================

# n-gram 大小(2 = 双字 gram,中文 sweet spot)
NGRAM_SIZE = 2
# BM25 参数(经典 k1 / b)
BM25_K1 = 1.5
BM25_B = 0.75
# 召回上限 — query 字符数(超长截断)
MAX_QUERY_CHARS = 500
# 单 chunk 返给 caller 的字符上限(防 LLM context 爆)
DEFAULT_SNIPPET_MAX_CHARS = 700
# 过滤掉过短 / 过常见的 n-gram(降噪)
STOP_NGRAMS = frozenset([
    "的的", "了了", "和的", ", ", "。。",
    # 极常见中文双字组合(可选,大多数会被 IDF 自然降权)
])
# 单 chunk 最小相关度 score 阈值(过滤纯随机匹配)
MIN_SCORE_THRESHOLD = 0.5


@dataclass
class ChunkSnippet:
    """召回的单条 chunk 片段。"""
    job_id: str
    chunk_index: int
    score: float
    text: str            # 限长到 DEFAULT_SNIPPET_MAX_CHARS
    upload_id: str       # 给 UI 展示"来自哪个文件"用


# ============================================================
# 文本 → n-gram 集合
# ============================================================

def _tokenize(text: str) -> list[str]:
    """中文友好 tokenize:
       1. 去掉空白 / 标点(只保留中文字符 + 英数)
       2. 滑窗取 NGRAM_SIZE 字 gram
       3. 过滤 stop_ngrams
    """
    if not text:
        return []
    # 只保留中文 + 英数(字符串单元)
    cleaned = re.sub(r"[^一-鿿\w]+", "", text)
    if len(cleaned) < NGRAM_SIZE:
        return []
    grams: list[str] = []
    for i in range(len(cleaned) - NGRAM_SIZE + 1):
        g = cleaned[i:i + NGRAM_SIZE]
        if g in STOP_NGRAMS:
            continue
        grams.append(g)
    return grams


# ============================================================
# 拉项目所有 chunks(含 lazy backfill)
# ============================================================

def _ensure_chunk_text_backfilled(
    conn: sqlite3.Connection, project_id: str,
) -> None:
    """老数据 chunk_text 为 NULL 时,lazy 回填:
       re-parse 该 upload 文件 + split_text_into_chunks + UPDATE chunk_text。

    幂等:已填的不动。失败时 log + 跳过(让 RAG 用空 chunk_text 也能返回空召回)。
    """
    # 找需要 backfill 的 job ids
    job_rows = fetch_all(
        conn,
        """SELECT DISTINCT gej.id AS job_id, gej.upload_id
           FROM graph_extraction_jobs gej
           JOIN extract_chunk_results ecr ON ecr.job_id = gej.id
           WHERE gej.project_id=? AND ecr.chunk_text IS NULL""",
        (project_id,),
    )
    if not job_rows:
        return   # 已全填好

    # 拉对应 upload 实际文本
    for jr in job_rows:
        job_id = jr["job_id"]
        upload_id = jr["upload_id"]
        upload_row = fetch_one(
            conn,
            "SELECT storage_path, mime_type FROM uploads WHERE id=?",
            (upload_id,),
        )
        if upload_row is None:
            logger.warning(
                f"RAG backfill: upload {upload_id} 不存在,跳过 job {job_id}"
            )
            continue

        # 解析 + split
        try:
            from app.services import file_parser
            from app.services.llm_extract import split_text_into_chunks
            from pathlib import Path

            text = file_parser.parse_file(
                Path(upload_row["storage_path"]), upload_row["mime_type"],
            )
            # 防御:parse_file 可能返 ParseResult 或 str,统一取字符串
            if hasattr(text, "text"):
                text = text.text
            chunks = split_text_into_chunks(str(text))
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"RAG backfill: parse upload {upload_id} 失败: {e}"
            )
            continue

        # 给每个 chunk_index UPDATE chunk_text
        # chunk_index 1-based(对齐 extract_service 写库时的 done 索引)
        for idx_0, chunk in enumerate(chunks):
            idx_1 = idx_0 + 1
            try:
                execute(
                    conn,
                    """UPDATE extract_chunk_results
                       SET chunk_text=?
                       WHERE job_id=? AND chunk_index=? AND chunk_text IS NULL""",
                    (chunk, job_id, idx_1),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"RAG backfill UPDATE failed job={job_id} idx={idx_1}: {e}"
                )

        conn.commit()


def _load_project_chunks(
    conn: sqlite3.Connection, project_id: str,
) -> list[dict]:
    """拉项目所有 chunks 的 {job_id, chunk_index, chunk_text, upload_id}。

    chunk_text 为 NULL 的(已尝试 backfill 仍失败)→ 跳过,不进召回池。
    """
    rows = fetch_all(
        conn,
        """SELECT gej.id AS job_id, gej.upload_id,
                  ecr.chunk_index, ecr.chunk_text
           FROM extract_chunk_results ecr
           JOIN graph_extraction_jobs gej ON gej.id = ecr.job_id
           WHERE gej.project_id=? AND ecr.chunk_text IS NOT NULL
                 AND ecr.chunk_text != ''
           ORDER BY gej.id, ecr.chunk_index""",
        (project_id,),
    )
    return [dict(r) for r in rows]


# ============================================================
# BM25 评分
# ============================================================

def _score_chunks(
    query_grams: list[str], chunks: list[dict],
) -> list[tuple[dict, float]]:
    """BM25 评分 chunks,返回 [(chunk, score), ...] 倒序排。

    经典 BM25:
      idf(q) = log((N - df + 0.5) / (df + 0.5) + 1)
      score(doc) = Σ idf(q) × (tf(q,doc) × (k1+1)) / (tf(q,doc) + k1 × (1 - b + b × |doc|/avgdl))
    """
    if not query_grams or not chunks:
        return []

    # 预处理:每 chunk tokenize + 长度 + tf
    chunk_grams: list[list[str]] = []
    chunk_tfs: list[dict[str, int]] = []
    chunk_lens: list[int] = []
    for c in chunks:
        grams = _tokenize(c["chunk_text"] or "")
        chunk_grams.append(grams)
        chunk_lens.append(len(grams))
        tf: dict[str, int] = {}
        for g in grams:
            tf[g] = tf.get(g, 0) + 1
        chunk_tfs.append(tf)

    N = len(chunks)
    avgdl = sum(chunk_lens) / N if N > 0 else 1
    if avgdl == 0:
        return []

    # query 去重 gram(评分时每独立 gram 算一次贡献)
    query_unique_grams = list(set(query_grams))

    # 每 unique gram 的 df
    df: dict[str, int] = {}
    for g in query_unique_grams:
        df[g] = sum(1 for tf in chunk_tfs if g in tf)

    scores: list[float] = []
    for i, c in enumerate(chunks):
        s = 0.0
        for g in query_unique_grams:
            tf = chunk_tfs[i].get(g, 0)
            if tf == 0:
                continue
            d = df[g]
            if d == 0:
                continue
            idf = math.log((N - d + 0.5) / (d + 0.5) + 1)
            doc_len = chunk_lens[i]
            denom = tf + BM25_K1 * (1 - BM25_B + BM25_B * doc_len / avgdl)
            s += idf * (tf * (BM25_K1 + 1)) / denom
        scores.append(s)

    paired = list(zip(chunks, scores))
    paired.sort(key=lambda x: x[1], reverse=True)
    return paired


# ============================================================
# 公共入口
# ============================================================

def retrieve_relevant_chunks(
    conn: sqlite3.Connection,
    project_id: str,
    query: str,
    *,
    top_k: int = 3,
    snippet_max_chars: int = DEFAULT_SNIPPET_MAX_CHARS,
    min_score: float = MIN_SCORE_THRESHOLD,
) -> list[ChunkSnippet]:
    """从项目所有 chunk 中召回与 query 最相关的 top_k 个。

    Args:
      project_id: 项目 id
      query:      查询文本(场景名 + 在场角色名 + 当前剧情末尾片段)
      top_k:      返回上限
      snippet_max_chars: 每条 chunk 截取长度上限(防 LLM context 爆)
      min_score:  低于此分数的 chunk 视为不相关,过滤

    Returns:
      List[ChunkSnippet],按 score 倒序;空列表 = 项目没有可用 chunk_text /
      没人匹配到 query。

    永不抛异常:失败降级返空列表,主流程不受影响。
    """
    if not query or not query.strip():
        return []
    if top_k <= 0:
        return []

    query = query[:MAX_QUERY_CHARS]

    try:
        _ensure_chunk_text_backfilled(conn, project_id)
        chunks = _load_project_chunks(conn, project_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"RAG: load chunks failed for project {project_id}: {e}")
        return []

    if not chunks:
        return []

    query_grams = _tokenize(query)
    if not query_grams:
        return []

    paired = _score_chunks(query_grams, chunks)

    results: list[ChunkSnippet] = []
    for chunk, score in paired[: top_k * 2]:   # 多取一点,过滤后凑齐 top_k
        if score < min_score:
            continue
        text = chunk["chunk_text"] or ""
        if len(text) > snippet_max_chars:
            text = text[:snippet_max_chars] + "..."
        results.append(ChunkSnippet(
            job_id=chunk["job_id"],
            chunk_index=int(chunk["chunk_index"]),
            score=round(score, 3),
            text=text,
            upload_id=chunk["upload_id"],
        ))
        if len(results) >= top_k:
            break

    return results
