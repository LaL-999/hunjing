-- 作品广场 · 作品权限扩展(v5,2026-07-02)—— 加 allow_download
--
-- 背景:用户希望上架时设「作品权限」:私人(is_public=0,仅作者可见)/ 公开(is_public=1);
--   公开作品可再控制是否允许其他用户下载正文(allow_download)。
--   is_public 已有(migration 089);此处补 allow_download。
--
-- 语义:
--   allow_download=1 且 is_public=1 → 任意用户可在详情页下载 Markdown
--   allow_download=0 → 仅作者本人可下载(他人只能在线阅读)
--   作者本人永远可下载 / 阅读自己的作品(含私人)
--
-- 幂等:ALTER ADD COLUMN 非幂等,_auto_apply_migrations 捕获 "duplicate column name" 跳过。

ALTER TABLE published_works ADD COLUMN allow_download INTEGER NOT NULL DEFAULT 1;
