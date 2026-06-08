-- migration 036: relationship_phases — 关系时间轴(Sprint 6.A2 M1,2026-05-18)
--
-- 产品背景:用户洞察 "关系会随剧情演化 (暗恋→情侣→仇敌),AI 不能傻到以一成不变
-- 的关系续写"。原 relationships 表 type 是单一静态值,丢失时间维度。
--
-- 设计:relationship 主表保留(向后兼容);加新表 relationship_phases 存阶段列表;
-- 每条关系可有 N 个阶段,phase_index 排序;relationships.current_phase_id 指向
-- "当前阶段"(无 phases 时为 NULL,fallback 到 relationships.type)。
--
-- 老数据兼容(下次跑 init_db 时自动迁移):
--   旧关系 (无 phase) → 续写时仍读 relationships.type 单标签(不强迫升级)
--   新关系 (有 phase) → 续写时按时间锚点选对应阶段类型
--   用户可手动加 phase → 老关系自动获得时间维度
--
-- 反事实联动:phase 可被反事实变更(删 phase / 改 type / 改触发事件) — 续写时反事实
-- 改了的 phase 在 director 拼 prompt 时按变更后版本注入。
--
-- created 2026-05-18 / Sprint 6.A2 M1

CREATE TABLE IF NOT EXISTS relationship_phases (
    id                  TEXT PRIMARY KEY,
    relationship_id     TEXT NOT NULL REFERENCES relationships(id) ON DELETE CASCADE,
    phase_index         INTEGER NOT NULL,    -- 0-based,按时间顺序

    -- 阶段类型(对齐 relationships.type 的 enum)
    type                TEXT NOT NULL
                          CHECK (type IN ('亲属','敌对','朋友','情侣','师徒','同事','其他')),
    -- 阶段强度(对齐 VALID_RELATIONSHIP_STRENGTHS)
    strength            TEXT NOT NULL DEFAULT 'moderate'
                          CHECK (strength IN ('strong','moderately_strong','moderate',
                                              'moderately_weak','weak')),

    -- 时间锚点(自由文本,如 "第 1 章" / "T1" / "贾母去世前" — 用户决定锚点形式)
    -- start = phase 起点;end = phase 终点(null = 持续到现在,即"最新 phase")
    start_anchor        TEXT,
    end_anchor          TEXT,

    -- 可选:触发该 phase 转变的事件(FK events.id,SET NULL on delete)
    trigger_event_id    TEXT REFERENCES events(id) ON DELETE SET NULL,

    -- 用户备注 / 阶段描述(为什么从 A 变 B)
    notes               TEXT NOT NULL DEFAULT '',

    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,

    -- 同一关系内 phase_index 唯一
    UNIQUE (relationship_id, phase_index)
);

CREATE INDEX IF NOT EXISTS idx_relationship_phases_rel
    ON relationship_phases(relationship_id, phase_index);


-- 给 relationships 表加 current_phase_id 字段(指向"当前生效"的 phase)
-- NULL = 老数据 / 无 phases / 走旧 relationships.type fallback
-- 非 NULL = 续写时优先用此 phase 的 type / strength,忽略 relationships.type
ALTER TABLE relationships ADD COLUMN current_phase_id TEXT
    REFERENCES relationship_phases(id) ON DELETE SET NULL;
