-- migration 043: Outline-First 长篇生成架构(Sprint 6.A2 M6,2026-05-20)
--
-- 用户拍板(2026-05-20):
--   "我选择路径B,但是该有的功能不要少。"
--
-- M6 起源 — Gemini 第三轮评测 M5 产物,新维度根因瑕疵:
--   - 实体属性值覆写(照片背面遗言变了:"对不起" → "替我看看明天的太阳")
--   - 角色物理坐标循环(张凡 3 次"挤进主卧拿相框退回")
--   - 完整动作模板复读(班长"右手攥着已抽出的钥匙串"句重复 5 次)
--   - 空间坐标系撕裂(玄关→主卧无叙述过渡)
--
-- M3-M5 是"生成时打补丁",每代 LLM 升级也只是缓解,不治本。
-- **M6 = 架构性转变:Outline-First 2 阶段流程**:
--   1. LLM 一次性生成全篇 outline(N 幕 × 每幕完整元数据 + key_events + key_props)
--   2. 用户审核 outline(可改 / 加 / 删 / 重排)→ 批准
--   3. 按 outline 逐幕跑(scene_picker 不再自由,location/events/props 全部由 outline 锁定)
--
-- 治本机制:跨幕全局状态在 outline 阶段一次性确定,生成阶段只负责"按图纸把这一幕生成出来"
--
-- created 2026-05-20 / Sprint 6.A2 M6


-- ============================================================
-- 1. simulations 加 use_outline_first 字段
-- ============================================================
-- 新 sim 默认走 outline-first(use_outline_first=1)
-- 老 sim 走原 quick / evolution 路径(use_outline_first=0,向后兼容)
ALTER TABLE simulations ADD COLUMN use_outline_first INTEGER NOT NULL DEFAULT 0;


-- ============================================================
-- 2. simulation_outlines — 整篇 outline 容器
-- ============================================================
-- 每行 = 一个 sim 的 outline 主表
--
-- 状态机:
--   drafting        — LLM 正在生成 outline(非阻塞,异步)
--   awaiting_user   — outline 生成完毕,等待用户审核 / 编辑
--   approved        — 用户已批准,准备启动逐幕生成
--   generating      — 按 outline 跑各幕中
--   done            — 全部幕完成
--   failed          — outline 生成失败 / 用户取消
CREATE TABLE IF NOT EXISTS simulation_outlines (
    id              TEXT PRIMARY KEY,
    simulation_id   TEXT NOT NULL REFERENCES simulations(id) ON DELETE CASCADE,

    state           TEXT NOT NULL DEFAULT 'drafting' CHECK (
        state IN ('drafting', 'awaiting_user', 'approved',
                  'generating', 'done', 'failed')
    ),

    -- 总幕数(等于 outline_scenes 行数;outline 阶段确定后不会变)
    total_scenes_planned INTEGER NOT NULL,

    -- 整篇主题(LLM 给的 < 60 字描述)
    -- 例:"校园悬疑 / 死亡游戏 / 三人小队对抗地府守门人"
    global_theme    TEXT NOT NULL DEFAULT '',

    -- 整篇起承转合走向(LLM 给的 < 300 字大纲)
    -- 例:"起:三人接到任务发现照片 → 承:深入老宅遭遇守门人挑战 →
    --     转:发现照片主人就在镜子里 → 合:救出韩紫雨真相揭露"
    global_arc      TEXT NOT NULL DEFAULT '',

    -- 用户批准时间(NULL = 未批准)
    user_approved_at TEXT,

    -- 失败原因(state=failed 时填)
    error_message   TEXT,

    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,

    -- 一个 sim 最多一个 outline
    UNIQUE (simulation_id)
);

CREATE INDEX IF NOT EXISTS idx_outlines_state
    ON simulation_outlines(state);


-- ============================================================
-- 3. outline_scenes — outline 每一幕
-- ============================================================
-- 每行 = outline 阶段确定的一幕的完整元数据(全局一致性锚点)
--
-- 与 simulation_scenes 区别:
--   - simulation_scenes 是"已生成产物",含完整 narrative_segment
--   - outline_scenes 是"幕的图纸",含 location / events / props / transition
--     生成完毕后通过 generated_simulation_scene_id 关联
CREATE TABLE IF NOT EXISTS outline_scenes (
    id              TEXT PRIMARY KEY,
    outline_id      TEXT NOT NULL REFERENCES simulation_outlines(id) ON DELETE CASCADE,

    -- 幕序号(0-based,严格递增)
    scene_index     INTEGER NOT NULL,

    -- 本幕概要(LLM 给的 1-2 句话,< 200 字)
    -- 例:"张凡进韩紫雨家主卧拿照片,确认照片人物是韩紫雨,守门人挑衅暗示倒影问题"
    scene_summary   TEXT NOT NULL,

    -- 本幕在整篇中的作用(< 50 字)
    -- 例:"推进主线 / 引入伏笔 / 情绪转折 / 反转揭露"
    scene_purpose   TEXT NOT NULL DEFAULT '推进主线',

    -- 锁定 1:本幕物理位置(LLM 决定,用户可改;narrator 不可改)
    location        TEXT NOT NULL,

    -- 锁定 2:时间锚(自由文本如"次日下午""黄昏""三天后")
    time_anchor     TEXT NOT NULL DEFAULT '',

    -- 锁定 3:本幕在场角色 ids(JSON 数组)
    characters_present_json TEXT NOT NULL DEFAULT '[]',

    -- 锁定 4:本幕必须发生的关键事件(JSON 数组,字符串列表)
    -- 例:["张凡拿到银色相框", "确认照片人物是韩紫雨", "守门人提示镜子有问题"]
    -- narrator 必须让本幕完整覆盖这些事件,不许漏
    key_events_json TEXT NOT NULL DEFAULT '[]',

    -- 锁定 5:本幕关键道具的引入 / 属性确立(JSON)
    -- 例:[{"name":"红裙照片","action":"introduced",
    --        "properties":{"颜色":"银色相框","背面文字":"对不起,我没能逃出去"}}]
    -- 道具一旦属性被锁,后续幕不许改其属性值(治瑕疵 1 实体属性覆写)
    key_props_json  TEXT NOT NULL DEFAULT '[]',

    -- 锁定 6:与上幕的物理连接(治瑕疵 4 空间撕裂)
    -- 例:"张凡刚从玄关走到主卧,门虚掩着推开"
    -- 首幕填"sim 起点状态,无需 transition"
    transition_from_last TEXT NOT NULL DEFAULT '',

    -- 用户编辑标记(scene_summary / key_events / location 等任一被改即为 true)
    user_edited     INTEGER NOT NULL DEFAULT 0,

    -- 跑状态机
    state           TEXT NOT NULL DEFAULT 'pending' CHECK (
        state IN ('pending', 'running', 'done', 'failed')
    ),

    -- 关联到 simulation_scenes(generation 完成后 UPDATE)
    generated_simulation_scene_id TEXT REFERENCES simulation_scenes(id) ON DELETE SET NULL,

    -- 失败信息
    error_message   TEXT,

    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,

    -- 同 outline 内 scene_index 唯一
    UNIQUE (outline_id, scene_index)
);

CREATE INDEX IF NOT EXISTS idx_outline_scenes_outline
    ON outline_scenes(outline_id, scene_index);

CREATE INDEX IF NOT EXISTS idx_outline_scenes_state
    ON outline_scenes(outline_id, state);
