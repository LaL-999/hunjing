-- migration 072: relationships 加正负极性(polarity)
-- SP-7(2026-05-28)— 关系强度加正负 + 3D 时间轴渲染
--
-- 起因:
--   relationships.strength 是 5 级量表(strong/moderately_strong/moderate/
--   moderately_weak/weak),无法区分**好/坏关系强度** — "强烈仇恨"和"强烈喜爱"
--   在系统里目前没法区分,3D 图谱只能按强度过滤,无法表达对立关系.
--
-- 解法:
--   加 `polarity` 字段(positive / negative / neutral / None),与 strength 正交:
--   - "强烈喜爱" = strength=strong + polarity=positive
--   - "强烈仇恨" = strength=strong + polarity=negative
--   - "同事关系" = strength=moderate + polarity=neutral
--   - 老关系 default = None(向后兼容,前端按 neutral 渲染)
--
-- 与已有字段正交:
--   strength       = 关系紧密度(0-100 派生 / 5 级量表)
--   polarity       = 正/负/中性(本 sprint 新)
--   type           = freeform 关系类型(暗恋/敌对/血缘/师徒)
--   phases         = 时间锚阶段(关系演化,migration 036)
--
-- 用户/LLM 填法:
--   - SP-7.1:build_graph LLM 抽取关系时同时输出 polarity
--   - 用户在前端关系编辑面板可手动指定
--
-- 默认 NULL — 老数据不影响;新关系建议显式标 polarity.

ALTER TABLE relationships ADD COLUMN polarity TEXT
    CHECK (polarity IN ('positive', 'negative', 'neutral'));
