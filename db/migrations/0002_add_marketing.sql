-- 迁移 0002：为 products 表新增 marketing 列
-- 背景：将商品卡营销/榜单/热度等附加信息从 specs(JSONB) 剥离到独立列，
--       避免污染规格参数，影响 M3 规格归一化与 M5 向量检索。
-- 应用：已在 models/base.py init_db() 中通过 ALTER TABLE ... ADD COLUMN IF NOT EXISTS
--       自动补齐；此文件仅供手动/审计参考。

ALTER TABLE products ADD COLUMN IF NOT EXISTS marketing JSONB;
