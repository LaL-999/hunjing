-- migration 046: 关系类型 type CHECK 由枚举改为长度上限
-- Sprint 6.A2 M7.G(2026-05-20)
--
-- 起源:DB 旧 CHECK 限定 type ∈ ('亲属','敌对','朋友','情侣','师徒','同事','其他') 7 种,
-- 但用户实测发现关系类型不够,角色味道偏离;且 LLM prompt build_graph.md v3 列了 11 种
-- (含'主仆/参与/位于/拥有/提及'),实际抽取时这些非预设值会被 DB CHECK 拒绝(silent fail)。
--
-- 本 migration 释放 type 的枚举约束,改为"trim 后 1-20 字非空字符串":
--   - relationships.type 表
--   - relationship_phases.type 表(对齐,phase 也是关系类型)
--   - 前端给 ~25 种常用预设 + 用户自定义入口
--   - LLM 抽取可输出 prompt 列表外的关系名
--   - 后端 schemas.RelationshipType: Literal → Annotated[str](Python 层软兜底)
--
-- 同时保留 036 加的 current_phase_id 字段,不丢老数据。
--
-- 幂等:每次跑都先 backup → drop → create → restore。数据零丢失。
-- 副作用:用户已存的"其他"类型(legacy 兜底)保留原值;无需迁移描述字段。
--
-- ⚠ SQLite 注:本 script 用 executescript 跑,会自动 COMMIT 当前事务再执行 PRAGMA,
--    所以 PRAGMA foreign_keys=OFF 在此处生效(避免 DROP TABLE 时被 phase 表 FK 拦)。
-- created 2026-05-20

PRAGMA foreign_keys=OFF;

-- ========== relationships 表 rebuild ==========

-- 1. 备份现有数据(含 036 加的 current_phase_id)
DROP TABLE IF EXISTS _relationships_046_backup;
CREATE TABLE _relationships_046_backup AS SELECT * FROM relationships;

-- 2. 删旧表(无论是 005 的 7-CHECK 还是 046 已迁过的新 schema)
DROP TABLE relationships;

-- 3. 建新表(type 仅长度 CHECK,保留 current_phase_id)
CREATE TABLE relationships (
    id                  TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source_id           TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    target_id           TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    -- 自由 type:trim 后 1-20 字;软白名单在 Python 层(schemas + LLM normalize)
    type                TEXT NOT NULL CHECK (length(trim(type)) BETWEEN 1 AND 20),
    description         TEXT NOT NULL DEFAULT '',
    color               TEXT,
    strength            TEXT NOT NULL DEFAULT 'moderate',
    -- M1(036)指向当前生效 phase;NULL = 走 type fallback
    current_phase_id    TEXT,
    created_at          TEXT NOT NULL
);

-- 4. 灌回数据(显式列名,兼容老 backup 可能缺 current_phase_id / strength 字段)
INSERT INTO relationships
    (id, project_id, source_id, target_id, type, description, color, strength,
     current_phase_id, created_at)
SELECT id, project_id, source_id, target_id, type, description, color,
       COALESCE(strength, 'moderate') AS strength,
       current_phase_id, created_at
FROM _relationships_046_backup;

-- 5. 清理 backup
DROP TABLE _relationships_046_backup;

-- 6. 重建索引
CREATE INDEX IF NOT EXISTS idx_relationships_project ON relationships(project_id);


-- ========== relationship_phases 表 rebuild(去 type CHECK 枚举)==========

-- 1. 备份
DROP TABLE IF EXISTS _relationship_phases_046_backup;
CREATE TABLE _relationship_phases_046_backup AS SELECT * FROM relationship_phases;

-- 2. drop 旧表
DROP TABLE relationship_phases;

-- 3. 建新表(同结构,但 type CHECK 改长度 + strength CHECK 保留)
CREATE TABLE relationship_phases (
    id                  TEXT PRIMARY KEY,
    relationship_id     TEXT NOT NULL REFERENCES relationships(id) ON DELETE CASCADE,
    phase_index         INTEGER NOT NULL,
    -- 自由 type(对齐 relationships.type 新规则)
    type                TEXT NOT NULL CHECK (length(trim(type)) BETWEEN 1 AND 20),
    strength            TEXT NOT NULL DEFAULT 'moderate'
                          CHECK (strength IN ('strong','moderately_strong','moderate',
                                              'moderately_weak','weak')),
    start_anchor        TEXT,
    end_anchor          TEXT,
    trigger_event_id    TEXT REFERENCES events(id) ON DELETE SET NULL,
    notes               TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE (relationship_id, phase_index)
);

-- 4. 灌回数据
INSERT INTO relationship_phases
    (id, relationship_id, phase_index, type, strength, start_anchor, end_anchor,
     trigger_event_id, notes, created_at, updated_at)
SELECT id, relationship_id, phase_index, type, strength, start_anchor, end_anchor,
       trigger_event_id, notes, created_at, updated_at
FROM _relationship_phases_046_backup;

-- 5. 清理 backup
DROP TABLE _relationship_phases_046_backup;

-- 6. 重建索引
CREATE INDEX IF NOT EXISTS idx_relationship_phases_rel
    ON relationship_phases(relationship_id, phase_index);

PRAGMA foreign_keys=ON;
