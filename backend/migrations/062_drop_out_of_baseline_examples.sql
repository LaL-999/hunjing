-- migration 062: 删除 behavior_baseline.out_of_baseline_examples 子字段
--
-- 起源:用户反馈"雷区"和"禁忌"两个字段语义重叠造成困惑;
--      前一版改成 UI 隐藏 + schema 保留,但用户指出"隐藏字段写了内容用户看不到却影响 AI 输出"
--      = 严重违反"透明 AI 协作"价值观。彻底删字段,把已存数据合并到 no_go_list。
--
-- 数据迁移:对每个 character,把 behavior_baseline_json.out_of_baseline_examples
-- 数组内容**追加**到 no_go_list 末尾(不去重 — 用户可在 UI 手动清理),然后从
-- behavior_baseline_json 中 json_remove 该子字段。
--
-- 依赖:SQLite 3.50.4(支持 json_each / json_group_array / json_remove)
-- 测试:SQLite 在 UPDATE 的 SET 子查询里支持 correlated subquery 引用外层别名
--
-- 影响表:characters
--
-- created 2026-05-24 / P0G.2

-- Step 1:把 out_of_baseline_examples 内容追加进 no_go_list
-- 仅处理同时满足两个条件的行:① baseline_json 有效 ② examples 非空数组
UPDATE characters
SET no_go_list = (
  SELECT json_group_array(j.value)
  FROM (
    SELECT je1.value AS value FROM json_each(characters.no_go_list) AS je1
    UNION ALL
    SELECT je2.value AS value FROM json_each(
      json_extract(characters.behavior_baseline_json, '$.out_of_baseline_examples')
    ) AS je2
  ) AS j
)
WHERE behavior_baseline_json IS NOT NULL
  AND json_valid(behavior_baseline_json)
  AND json_extract(behavior_baseline_json, '$.out_of_baseline_examples') IS NOT NULL
  AND json_type(json_extract(behavior_baseline_json, '$.out_of_baseline_examples')) = 'array'
  AND json_array_length(json_extract(behavior_baseline_json, '$.out_of_baseline_examples')) > 0;

-- Step 2:从 behavior_baseline_json 移除 out_of_baseline_examples 子字段
UPDATE characters
SET behavior_baseline_json = json_remove(behavior_baseline_json, '$.out_of_baseline_examples')
WHERE behavior_baseline_json IS NOT NULL
  AND json_valid(behavior_baseline_json)
  AND json_extract(behavior_baseline_json, '$.out_of_baseline_examples') IS NOT NULL;
