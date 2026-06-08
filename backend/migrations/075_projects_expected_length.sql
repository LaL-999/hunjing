-- 2026-06-01:projects 加 expected_length 字段(short/medium/long)
-- 用途:让 narrator 按篇幅调"细节颗粒度"
--   short  短篇/章节级:每段细致,微表情/动作/声响齐全(当前风格,适合高潮章)
--   medium 中篇(默认):核心场景细致,过场段落简略(七分密度)
--   long   长篇:转折/高潮细致,常规场景松弛(五分密度,避免长读疲劳)
-- 治"Gemini 指出'每秒放慢镜头特写,长篇读者会疲劳'"问题

ALTER TABLE projects ADD COLUMN expected_length TEXT DEFAULT 'medium'
    CHECK (expected_length IN ('short', 'medium', 'long'));
