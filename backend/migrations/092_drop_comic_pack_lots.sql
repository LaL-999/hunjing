-- 漫画包机制彻底下线(v5,2026-07-02)—— DROP comic_pack_lots
--
-- 背景:
--   ECON-2(2026-05-27)曾把漫创态改成"单买漫画包 ¥30/次"。v5(item4)漫创态解锁重构后,
--   漫画包机制整体废弃:创建漫画不再扣包、失败/取消不再退包、准入改由 BYOK/订阅闸门把关。
--   相关代码(credit 路由 comic_pack 端点 / credit_service 四个 pack 函数 / credit_cron 过期任务 /
--   quota_service.enforce_comic_count_quota / insights funnel+balance / test_comic_pack.py)已一并移除。
--
-- 因此 comic_pack_lots 表已无任何读写方,DROP 之,让平台 schema 干净。
--
-- 幂等:DROP TABLE IF EXISTS 天然幂等;auto-migration runner 每次启动重跑无副作用。
-- 注:历史创建该表的旧 migration 不动(保留迁徙历史);本文件在其后执行,最终态为"已删除"。

DROP TABLE IF EXISTS comic_pack_lots;
