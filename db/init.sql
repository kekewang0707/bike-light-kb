-- ============================================================================
-- 自行车灯电商知识库 — 数据库初始化脚本
-- 注意：此脚本与 models/ 下的 SQLAlchemy ORM 模型保持同步。
--       ORM 可通过 models.init_db() 自动建表，此脚本供手动初始化参考。
-- ============================================================================

-- ============================================================================
-- 1. 商品主表
-- ============================================================================
CREATE TABLE IF NOT EXISTS products (
    id              BIGSERIAL PRIMARY KEY,
    platform        VARCHAR(20)  NOT NULL DEFAULT 'taobao',   -- taobao / pdd
    platform_id     VARCHAR(100) NOT NULL,                    -- 平台商品ID
    name            VARCHAR(500) NOT NULL,                    -- 商品标题
    brand           VARCHAR(200),                             -- 品牌
    shop_name       VARCHAR(300),                             -- 店铺名称
    category        VARCHAR(200),                             -- 品类
    product_url     VARCHAR(1000),                            -- 商品链接
    main_image_url  VARCHAR(1000),                            -- 主图URL
    price           DECIMAL(10, 2),                           -- 当前售价
    original_price  DECIMAL(10, 2),                           -- 原价/划线价
    sales_volume    INTEGER DEFAULT 0,                        -- 销量
    comment_count   INTEGER DEFAULT 0,                        -- 评价总数
    good_rate       DECIMAL(5, 2),                            -- 好评率(%)
    specs           JSONB,                                    -- 规格参数
    marketing       JSONB,                                    -- 营销/榜单附加信息(从specs剥离)
    crawled_at      TIMESTAMPTZ,                              -- 最后采集时间
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_product_platform_id UNIQUE (platform, platform_id)
);

-- ============================================================================
-- 2. 商品图片
-- ============================================================================
CREATE TABLE IF NOT EXISTS product_images (
    id              BIGSERIAL PRIMARY KEY,
    product_id      BIGINT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    image_type      VARCHAR(20) NOT NULL DEFAULT 'main',      -- main / detail / video_cover
    image_url       VARCHAR(1000),                            -- 原始URL
    local_path      VARCHAR(500),                             -- 本地存储路径
    sort_order      INTEGER DEFAULT 0
);

-- ============================================================================
-- 3. 商品SKU
-- ============================================================================
CREATE TABLE IF NOT EXISTS product_skus (
    id              BIGSERIAL PRIMARY KEY,
    product_id      BIGINT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    sku_name        VARCHAR(300),                             -- 如 "黑色-800流明-续航8小时"
    price           DECIMAL(10, 2),
    stock           INTEGER,
    sku_specs       JSONB                                     -- {"颜色":"黑色","流明":800}
);

-- ============================================================================
-- 4. 评价主表
-- ============================================================================
CREATE TABLE IF NOT EXISTS reviews (
    id                  BIGSERIAL PRIMARY KEY,
    product_id          BIGINT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    platform_review_id  VARCHAR(100),                         -- 平台评价ID
    rating              INTEGER NOT NULL,                     -- 1-5 星
    content             TEXT,                                 -- 评价原文
    user_name           VARCHAR(200),
    user_name_hash      VARCHAR(64),                          -- 用户昵称盐值SHA-256哈希(明文已丢弃)
    user_level          VARCHAR(50),                          -- plus会员 / 普通用户
    buy_date            TIMESTAMPTZ,
    review_date         TIMESTAMPTZ,
    likes               INTEGER DEFAULT 0,
    reply_content       TEXT,                                 -- 卖家回复
    crawled_at          TIMESTAMPTZ
);

-- ============================================================================
-- 5. 评价晒图
-- ============================================================================
CREATE TABLE IF NOT EXISTS review_images (
    id              BIGSERIAL PRIMARY KEY,
    review_id       BIGINT NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    image_url       VARCHAR(1000),
    local_path      VARCHAR(500)
);

-- ============================================================================
-- 6. 价格变动记录
-- ============================================================================
CREATE TABLE IF NOT EXISTS price_history (
    id              BIGSERIAL PRIMARY KEY,
    product_id      BIGINT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    price           DECIMAL(10, 2) NOT NULL,
    snapshot_date   DATE NOT NULL
);

-- ============================================================================
-- 7. 热榜快照
-- ============================================================================
CREATE TABLE IF NOT EXISTS hot_rankings (
    id              BIGSERIAL PRIMARY KEY,
    product_id      BIGINT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    ranking         INTEGER,
    list_name       VARCHAR(200),                             -- "自行车灯热销榜"
    period_type     VARCHAR(50),                              -- daily / weekly
    fetched_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- 8. 评价语义分析结果
-- ============================================================================
CREATE TABLE IF NOT EXISTS review_analysis (
    id              BIGSERIAL PRIMARY KEY,
    review_id       BIGINT UNIQUE NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    selling_points  JSONB,                                    -- [{"aspect":"亮度高","mention":"...","sentiment":"positive"}]
    use_scenes      JSONB,                                    -- [{"scene":"夜骑山路","detail":"...","confidence":0.9}]
    pain_points     JSONB,                                    -- [{"aspect":"续航短","mention":"...","severity":"high"}]
    user_profile    JSONB,                                    -- {"rider_type":"通勤","bike_type":"山地车",...}
    summary         TEXT,
    analyzed_at     TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- 9. 图片分析结果
-- ============================================================================
CREATE TABLE IF NOT EXISTS image_analysis (
    id              BIGSERIAL PRIMARY KEY,
    image_id        BIGINT UNIQUE NOT NULL REFERENCES product_images(id) ON DELETE CASCADE,
    ocr_text        TEXT,                                     -- OCR 提取的文字
    description     TEXT,                                     -- 多模态描述
    style_tags      JSONB,                                    -- ["极简","运动风","科技感"]
    clip_vector_id  VARCHAR(100),                             -- ChromaDB 中 CLIP 向量 ID
    analyzed_at     TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- 索引
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_products_platform      ON products(platform);
CREATE INDEX IF NOT EXISTS idx_products_brand         ON products(brand);
CREATE INDEX IF NOT EXISTS idx_products_category      ON products(category);
CREATE INDEX IF NOT EXISTS idx_products_price         ON products(price);
CREATE INDEX IF NOT EXISTS idx_products_sales         ON products(sales_volume DESC);
CREATE INDEX IF NOT EXISTS idx_products_updated       ON products(updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_product_images_product ON product_images(product_id);

CREATE INDEX IF NOT EXISTS idx_reviews_product        ON reviews(product_id);
CREATE INDEX IF NOT EXISTS idx_reviews_rating         ON reviews(rating);
CREATE INDEX IF NOT EXISTS idx_reviews_date           ON reviews(review_date DESC);

CREATE INDEX IF NOT EXISTS idx_price_history_product  ON price_history(product_id);
CREATE INDEX IF NOT EXISTS idx_price_history_date     ON price_history(snapshot_date);

CREATE INDEX IF NOT EXISTS idx_hot_rankings_product   ON hot_rankings(product_id);
CREATE INDEX IF NOT EXISTS idx_hot_rankings_list      ON hot_rankings(list_name);
CREATE INDEX IF NOT EXISTS idx_hot_rankings_fetched   ON hot_rankings(fetched_at DESC);
