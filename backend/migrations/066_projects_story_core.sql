-- migration 066: projects 加故事内核三件套(SP-1,2026-05-28)
--
-- 灵魂续写北极星:让 LLM 拿到"故事方向",不再因为没终点而提前泄气
-- 三件套:
--   core_dramatic_question — 一句话脊柱(整本书围着它转)
--     例:"线上交付的真心,扛不扛得住线下的真相"(网恋风云)
--     例:"为爱救赎之路,能不能熬过自我毁灭"(挪威森林)
--   theme — 主题(独立字段,不再混入 world_baseline.tone)
--     例:"自由与责任的撕扯" / "孤独中相互取暖"
--   ending_direction — 终点情绪 / 走向(粗略落点)
--     例:"哀而不伤,留一抹希望" / "悲剧收束,救赎落空"
--
-- 与已有正交字段说明(防语义重叠):
--   - world_baseline.tone:基调情绪色温(↔ theme:故事核心命题,正交)
--   - simulations.with_grand_finale:结构开关(↔ ending_direction:情绪终点,正交)
--   - inferred_pacing:节奏档位(完全正交)
--
-- 默认 NULL — 老项目无影响;新项目用户可填 / LLM 从原作抽取后回填.

ALTER TABLE projects ADD COLUMN core_dramatic_question TEXT;
ALTER TABLE projects ADD COLUMN theme TEXT;
ALTER TABLE projects ADD COLUMN ending_direction TEXT;
