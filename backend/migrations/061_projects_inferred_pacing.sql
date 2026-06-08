-- migration 061: projects 加 inferred_pacing 4 字段(AI 自动推断叙事节奏)
--
-- 起源:用户反馈 P2.B 节奏档位让用户手填不合理 — 应该 AI 根据原作分析自动决定,用户可改。
--
-- 字段:
--   inferred_pacing                TEXT NULL  — AI 推断结果('slow' / 'standard' / 'fast')
--                                              NULL = 尚未推断(首次创建续作时懒触发)
--   inferred_pacing_reasoning      TEXT NULL  — 给用户看的解释(为什么这个档位)
--   inferred_pacing_metrics_json   TEXT NULL  — 原始指标(段落均长 / 对白比 / 场景跨度)
--   inferred_pacing_at             TEXT NULL  — 推断完成时间(ISO 8601)
--
-- 触发逻辑(simulation_service.create_simulation 懒触发):
--   1. 项目有 ≥ 1 个 ready upload
--   2. inferred_pacing 为 NULL
--   3. → 同步跑 pacing_inferer(2-3s)+ 写入这 4 列
--   4. 失败 fallback 'standard' + reasoning="AI 推断失败,默认标准节奏"
--
-- 普适性:任何作品风格都需要 — 节奏档与文风正交
--
-- created 2026-05-24 / P2.B 升级 — AI 推断 + 用户可改

ALTER TABLE projects ADD COLUMN inferred_pacing TEXT;
ALTER TABLE projects ADD COLUMN inferred_pacing_reasoning TEXT;
ALTER TABLE projects ADD COLUMN inferred_pacing_metrics_json TEXT;
ALTER TABLE projects ADD COLUMN inferred_pacing_at TEXT;
