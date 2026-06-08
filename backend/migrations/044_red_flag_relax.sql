-- migration 044: 红旗词典降门槛(Sprint 6.A2 M6-fix7-post,2026-05-20)
--
-- 背景:用户实测反映"很多内容其实都不是很严重但是被拦截了,导致很高的用户流失率"。
-- 决策:只对'非常敏感的国家政治问题 + 真实可执行的爆炸物/枪支制造教程 + CSAM + 兽交极端类'
--       保留 block;允许隐晦成人内容(降 block→warn);恐怖组织名(国际题材常用)降 warn;
--       4 字 substring 'self_kill_method' 过宽,降 warn。
--
-- 修改方式:UPDATE 现存 DB 中匹配 pattern 的行,severity block→warn。
-- 幂等:WHERE severity='block' AND pattern=... 保证不重复 update(已经是 warn 不变)。
--
-- 注意:本 migration 仅改 severity,不删除 / 禁用任何 entry — warn 级别可以保留法务 trail。
--       red_flag_filter.py 当前 warn 默认 skip,与 block 等效不阻断;未来 v2 warn 可改 log-only。
--
-- 同步更新:backend/seed/red_flag_seed.json 对应 entries 的 severity 已改 warn(给新装 DB 用);
--           本 migration 修存量 DB 状态。

-- ============ political(1 条降级) ============

UPDATE red_flag_dictionary
    SET severity = 'warn',
        note = '敏感政治 — 历史指涉,文学常作背景提及;M6-fix7-post 降 block→warn 防误杀'
    WHERE pattern = '六四屠杀' AND severity = 'block';


-- ============ violence(7 条降级 — 恐怖组织名 / 人体炸弹 / 自杀方法宽 substring) ============

UPDATE red_flag_dictionary
    SET severity = 'warn',
        note = '恐怖暴力 — 恐怖组织名;国际题材 / 罪案小说常用,M6-fix7-post 降 block→warn'
    WHERE pattern = 'ISIS' AND severity = 'block';

UPDATE red_flag_dictionary
    SET severity = 'warn',
        note = '恐怖暴力 — ISIS 中文名;同上,降 warn'
    WHERE pattern = '伊斯兰国' AND severity = 'block';

UPDATE red_flag_dictionary
    SET severity = 'warn',
        note = '恐怖暴力 — 恐怖组织名;国际题材常用,降 warn'
    WHERE pattern = '基地组织' AND severity = 'block';

UPDATE red_flag_dictionary
    SET severity = 'warn',
        note = '恐怖暴力 — 东突厥斯坦伊斯兰运动;降 warn'
    WHERE pattern = '东伊运' AND severity = 'block';

UPDATE red_flag_dictionary
    SET severity = 'warn',
        note = '恐怖暴力 — 恐怖主义自称;文学常用反派身份,降 warn'
    WHERE pattern = '圣战分子' AND severity = 'block';

UPDATE red_flag_dictionary
    SET severity = 'warn',
        note = '恐怖暴力 — 完整术语;惊悚 / 战争小说常见,降 warn'
    WHERE pattern = '人体炸弹' AND severity = 'block';

UPDATE red_flag_dictionary
    SET severity = 'warn',
        note = '恐怖暴力 — 4 字 substring 过宽,常误杀''考虑自杀方法''类正常剧情;降 warn,严教程留''怎么自杀最快''/''上吊教程'''
    WHERE pattern = '自杀方法' AND severity = 'block';


-- ============ sexual(3 条降级 — 黑暗题材成人内容) ============

UPDATE red_flag_dictionary
    SET severity = 'warn',
        note = '成人题材 — 悬疑 / 罪案 / 黑暗言情常见情节;M6-fix7-post 降 block→warn 允许隐晦成人内容'
    WHERE pattern = '迷奸' AND severity = 'block';

UPDATE red_flag_dictionary
    SET severity = 'warn',
        note = '成人题材 — 同上,降 warn'
    WHERE pattern = '轮奸' AND severity = 'block';

UPDATE red_flag_dictionary
    SET severity = 'warn',
        note = '成人题材 — 黑暗设定常见,降 warn'
    WHERE pattern = '性奴' AND severity = 'block';


-- ============ 自检 / 不变项 ============
-- 保留 block 不变:
--   political: 颠覆国家政权 / 煽动颠覆 / 推翻共产党 / 反共复辟 / 藏独 / 疆独 / 台独 / 港独 / 法轮功
--   violence:  自制炸药 / 炸药配方 / TATP / ANFO / 如何制造炸弹 / 如何制作枪支 / 怎么自杀最快 / 上吊教程
--   sexual:    幼女性 / 萝莉色情 / 童交 / 儿童色情 / 未成年性交 / 强奸幼女 / 兽交 / 人兽性交
--   privacy:   18 位身份证号(block)
-- 保留 warn 不变:
--   sexual: 禽兽
--   privacy: 手机号 / 16/19 位银行卡号
