-- migration 019: simulations.original_tail_excerpt
-- Sprint 3.A 末尾态(end mode)— "不动原作正文,从末尾接着写"。
--
-- 末尾态创建推演时,后端从 project 的最近 ready upload 取末尾 ~2000 字原文,
-- 缓存到 simulations.original_tail_excerpt。run_simulation 时:
--   - director user prompt 注入"原作末段(必读 — 接续以下情节)"
--   - composer user prompt 注入相同末段(语体强一致 — 模仿原作笔法 / 节奏 / 用词)
--
-- 为何缓存而非每次 re-parse:
--   1. 原文件可能被用户删除(uploads.delete),sim 仍要可重跑
--   2. 缓存时刻锚定"用户当时看到的是哪段",防原文中途换版本导致语义漂移
--   3. parse_file 对 epub/docx 不便宜(每次几十 ms),run_simulation 已是长链路
--
-- 老 sim(initial / middle 态)兼容:NULL 即可;run_simulation 末尾态分支只在
-- project.mode == 'end' 时读取 + 注入,其它态完全旁路。
--
-- created 2026-05-11 / Sprint 3.A

ALTER TABLE simulations ADD COLUMN original_tail_excerpt TEXT;
