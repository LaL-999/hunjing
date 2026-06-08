-- migration 059: characters 加 life_status + status_note(P1.B 治"已死角色复活"瑕疵)
--
-- 起源:Gemini 评测发现"灵魂续写模式"中直子(原作末尾死亡)与绿子在小林书店阁楼共处。
-- 原作时间线里,直子应在阿美寮疗养院(后续自杀),与主角异地。
-- canonical_entities 是 sim 级"已见过"实体锁定;但**项目永久角色状态**(死/活/在哪)
-- 才是续写不能违反的硬约束。
--
-- 字段设计:
--   life_status:
--     'alive'      默认 — 角色自由出场
--     'deceased'   已死 — 任何场景不允许出现(铁律)
--     'in_facility' 在特定地点("阿美寮疗养院")— 除非主角去到该地否则不应出现
--     'absent'     暂时离开("去德国")— 类似 in_facility,软约束
--     'unknown'    未知 — 同 alive
--
--   status_note:文字补充说明,例:"在阿美寮疗养院" / "去德国深造" / "1969年自杀身亡"
--     — 给 LLM 看的人物状态注释,精确度更高
--
-- 普适性:任何作品(三体 / 红楼梦 / 武侠)都需要这套字段 — 死人不能复活、流放的不能在场
--
-- created 2026-05-24 / P1.B

ALTER TABLE characters ADD COLUMN life_status TEXT NOT NULL DEFAULT 'alive';
ALTER TABLE characters ADD COLUMN status_note TEXT NOT NULL DEFAULT '';
