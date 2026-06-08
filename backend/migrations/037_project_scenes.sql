-- migration 037: project_scenes 表(Sprint 6.A2 M2,2026-05-18)
--
-- 产品意图(用户拍板"M2 场景识别 + 角色亲疏地图"):
--   把 build_graph.md 已抽出的 LOCATION 实体(在 extract_chunk_results.graph_json
--   .entities 里)聚合落库,作为前三态项目的"场所图谱"。
--
-- 与漫画态 027 scenes 表的区别:
--   027 scenes.comic_id 绑死漫画态(给导演 Agent #6 拼 prompt 用)
--   本表 project_id 绑前三态项目(用于多 agent 仿真 M3 时 scene_picker)
--   字段语义一致,可独立演化;漫画态 scenes 不动
--
-- 数据来源:
--   1. extract_chunk_results.graph_json.entities 中 type='LOCATION' 实体
--      → name / aliases / description 已现成
--   2. character_affinity 服务运行时再算"该 scene 哪些角色常出现"
--      (用 chunk 共现矩阵,不存到本表 — 因为是派生数据)
--
-- 反事实联动:scenes 字段可被用户编辑(name / description),走通用 PATCH;
--   counterfactual_changes 跟踪暂不接入(YAGNI,M3 续写消费时再决策)
--
-- created 2026-05-18 / Sprint 6.A2 M2

CREATE TABLE IF NOT EXISTS project_scenes (
    id                  TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,

    -- 场所名(从 LOCATION 实体的 name 取)— 项目内唯一
    name                TEXT NOT NULL,
    -- LLM 抽时给的别名(JSON 数组,如 ["大观园", "园子"])
    aliases_json        TEXT NOT NULL DEFAULT '[]',
    -- LLM 抽时给的简短描述(可能为空)
    description         TEXT NOT NULL DEFAULT '',

    -- 派生统计(scene_extractor 落库时填,供 UI 快速展示)
    -- 该场所在多少 chunk 中出现(衡量重要性)
    appearance_chunk_count   INTEGER NOT NULL DEFAULT 0,

    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,

    -- 同项目下场所名唯一(防 LLM 多 chunk 抽出重复)
    UNIQUE (project_id, name)
);

CREATE INDEX IF NOT EXISTS idx_project_scenes_project
    ON project_scenes(project_id, appearance_chunk_count DESC);
