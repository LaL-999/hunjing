-- migration 069: 知识边界系统(story_facts + character_knowledge)
-- SP-3(2026-05-28)— 灵魂续写北极星·信息不对称层(AI 写作最大连贯 bug 源)
--
-- 设计源:
--   AI 写作第一大连贯 bug = "角色用了他还不知道的信息".
--   挪威森林续作里:绿子在阿美寮见过"直子"(直子已死,绿子不可能知道) — 完全是 AI
--   在没有"谁知道什么"约束的情况下,把读者已知信息直接塞给所有角色.
--
--   网恋风云这类故事整个就是靠信息不对称(假身份/谁不知道谁的真名)— 没这层模型
--   根本无法表达"A 还不知道 B 的真实身份".
--
-- 两张表:
--   story_facts          全局事实(项目级,例:"直子在 1969 年自杀")
--   character_knowledge  谁知道什么(N:M 关系,带时间锚 + confidence)
--
-- 与已有的关系:
--   - characters.secret_json(SP-2)= 角色秘密(只属于该角色,他知道自己的秘密)
--   - canonical_entities.locked_status(P1.B)= 实体物理存在(全局事实)
--   - SP-3 是把"谁知道这个事实"建模 — 与上两个正交
--
-- 用途:
--   1. narrator hard_constraints 注入:每个在场角色已知/未知清单
--   2. canonical_guardian 加第 11 维 information_boundary(SP-3.1 留尾)
--   3. 戏剧反讽机制("读者已知、角色未知" = 张力源)

CREATE TABLE IF NOT EXISTS story_facts (
    id                   TEXT PRIMARY KEY,
    project_id           TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    description          TEXT NOT NULL,                  -- 事实本身(例:"直子在 1969 年自杀")
    first_revealed_scene INTEGER,                         -- 在第几幕被揭示;NULL = 从未在文本中明说
    is_sensitive         INTEGER NOT NULL DEFAULT 0,      -- 0/1:敏感秘密(关联角色 secret)还是普通事实
    created_at           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_story_facts_project
    ON story_facts(project_id);


CREATE TABLE IF NOT EXISTS character_knowledge (
    id                  TEXT PRIMARY KEY,
    character_id        TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    fact_id             TEXT NOT NULL REFERENCES story_facts(id) ON DELETE CASCADE,
    -- 时间锚:从第几幕开始知道(NULL = 始终知道,例:角色自己经历的事)
    known_since_scene   INTEGER,
    -- confidence:该角色对这件事的认知状态
    --   suspected:怀疑(知道点风声但不确定)
    --   confirmed:确知(白纸黑字)
    --   wrong:误信(他以为知道,但其实搞错了 — 给"假身份"机制留扩展)
    confidence          TEXT NOT NULL DEFAULT 'confirmed'
                          CHECK (confidence IN ('suspected', 'confirmed', 'wrong')),
    created_at          TEXT NOT NULL,
    UNIQUE(character_id, fact_id)
);

CREATE INDEX IF NOT EXISTS idx_char_knowledge_char
    ON character_knowledge(character_id);

CREATE INDEX IF NOT EXISTS idx_char_knowledge_fact
    ON character_knowledge(fact_id);
