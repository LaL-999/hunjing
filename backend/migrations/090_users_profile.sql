-- 090:用户资料 — 昵称 + 头像(2026-06-25)
-- 单独成文件:ALTER ADD COLUMN 在已有该列时会 raise → 整文件被 runner 跳过;
-- 拆开后不会拖累 089 的 plaza 表(吸取 088 前的教训)。
-- runner 对 "duplicate column" 自动跳过,故重跑安全。

ALTER TABLE users ADD COLUMN nickname TEXT;
ALTER TABLE users ADD COLUMN avatar_url TEXT;
