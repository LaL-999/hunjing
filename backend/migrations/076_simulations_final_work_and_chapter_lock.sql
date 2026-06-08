-- 2026-06-01:simulations 加两字段
-- final_compiled_narrative: 走向终章 + 多代滚雪球 → 沿继承链合并的最终作品(只在最后一代有,孤本不需要)
-- start_chapter_locked:     创建本 sim 时,从前篇累计推导出的起始章号 — 一次性锁定,
--                           之后前篇文本怎么改,本 sim 起始章号都不动(规避用户提的副作用)
-- 算法:有 ancestors → 沿继承链回溯,累加每个父辈 narrative_length / avg_chapter_size,+1
--      无 ancestors → 1

ALTER TABLE simulations ADD COLUMN final_compiled_narrative TEXT DEFAULT NULL;
ALTER TABLE simulations ADD COLUMN start_chapter_locked INTEGER DEFAULT NULL;
