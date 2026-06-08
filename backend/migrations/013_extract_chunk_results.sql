-- migration 013: extract_chunk_results
-- 抽图谱断点续抽支持(Sprint 2.B+):
--   每个 chunk 抽完立刻 INSERT 一行,backend 重启 / 用户断网时不丢已抽的部分
--
-- resume 流程:
--   1. 用户点"继续抽取" → POST /api/extract_jobs/{id}/resume
--   2. worker 启动 → SELECT chunk_index, chunk_text_hash FROM extract_chunk_results WHERE job_id=?
--   3. 重新切当前文件,逐块对比 hash:
--        - hash 一致 → 跳过(已完成)
--        - hash 不一致(用户中途换了文件)→ 自动 fallback 到完全重抽
--   4. 从未完成的最小 chunk_index 开始抽
--   5. 全部完成后 → 走原有 generating_characters / inferring_meta / saving 流程
--
-- reset 流程:
--   用户点"重新开始" → DELETE FROM extract_chunk_results WHERE job_id=? + 改 job state='failed'
--   下次 trigger 走全新 job,新 job_id,旧 chunk_results 已被 CASCADE 删
--
-- 设计选择:
--   * graph_json TEXT 而非分字段 — chunk 输出结构是 {entities:[],relations:[]},
--     字段化得不偿失(总共也就几十块,SQLite 单元 1KB-100KB 完全 OK)
--   * 不存 chunk 原文 — 重启时按相同切片算法重新切,只用 hash 校验一致性
--   * PRIMARY KEY (job_id, chunk_index) — 防同 job 同块重复 insert(worker 重试场景)
--   * FK ON DELETE CASCADE — job 删除时自动清空 chunk_results
-- created 2026-05-11 / Sprint 2.B+

CREATE TABLE IF NOT EXISTS extract_chunk_results (
    job_id            TEXT NOT NULL REFERENCES graph_extraction_jobs(id) ON DELETE CASCADE,
    chunk_index       INTEGER NOT NULL,           -- 0-based,与 split_text_into_chunks 输出顺序对齐
    chunk_text_hash   TEXT NOT NULL,              -- sha256(chunk_text) 前 16 字符,防原文变了
    graph_json        TEXT NOT NULL,              -- {"entities":[...],"relations":[...]} 单块 LLM 输出
    tokens_input      INTEGER NOT NULL DEFAULT 0,
    tokens_output     INTEGER NOT NULL DEFAULT 0,
    completed_at      TEXT NOT NULL,
    PRIMARY KEY (job_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_extract_chunk_results_job ON extract_chunk_results(job_id);
