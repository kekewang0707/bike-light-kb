# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

自行车灯电商知识库 — 数据驱动的选品决策与内容创作平台，专注于自行车灯品类。从电商平台（主要为淘宝）采集数据，通过 LLM 对评价做语义分析，最终在 Streamlit 看板中呈现洞察。

**技术栈**: Python 3, Streamlit, PostgreSQL 16, ChromaDB, Playwright, LangChain + DeepSeek API, curl_cffi（mtop 接口伪装 Chrome TLS 指纹）

## 常用命令

```bash
# 启动基础设施
docker compose up -d                 # 启动 PostgreSQL（端口 5433）+ ChromaDB（端口 8000）

# 启动应用
streamlit run app.py                 # 启动 Streamlit 看板

# 灌入测试数据
python scripts/seed_test_data.py     # 向数据库写入示例商品、评价、热榜数据

# 运行爬虫（通过 Python REPL 或脚本）
python -c "
import asyncio
from crawler.engine import CrawlerEngine
async def main():
    engine = CrawlerEngine()
    report = await engine.run(keywords=['自行车灯'], max_products_per_keyword=20)
    print(report)
asyncio.run(main())
"

# 淘宝扫码登录 / Cookie / mtop 搜索（见「淘宝登录与 mtop 逆向」）
python scripts/taobao_login.py login                 # 一次性扫码登录，保存 Cookie 到 data/cookies/taobao_cookies.json
python scripts/taobao_login.py status                # 查看登录态 / 剩余有效期
python scripts/taobao_login.py request -u "https://main.m.taobao.com/" -X GET   # 带 Cookie 的普通请求
python scripts/taobao_login.py search -q "自行车灯" -p 1                          # 带签名调用 mtop 搜索接口

# 测试（pytest + pytest-asyncio，asyncio_mode=auto）
venv/bin/python -m pytest                              # 全量
venv/bin/python -m pytest tests/test_pii.py -v         # 单个模块

# 依赖锁定
venv/bin/python scripts/gen_requirements_lock.py       # 生成 requirements.lock.txt 并校验
venv/bin/python scripts/gen_requirements_lock.py --check   # CI 用：只校验 lock 是否过期

# PII 保留期清理（建议每周一次，可挂 cron）
venv/bin/python scripts/anonymize_pii.py --dry-run     # 预览将被匿名化的行数
venv/bin/python scripts/anonymize_pii.py --days 365    # 正式执行
```

## 架构

项目按 8 个模块分阶段推进（详见 `plans/` 目录）。M1~M3 已实现，M4~M8 处于规划阶段。

### 数据流

```
爬虫 (Playwright) → DataPipeline → PostgreSQL
                                    ↓
                            ReviewAnalyzer (LLM) → review_analysis (JSONB)
                                    ↓
                            ChromaDB 向量库 (M5 规划中)
                                    ↓
                            Streamlit UI (M6/M7 规划中)
```

### 逐层说明

**`models/` — M1：数据模型层（SQLAlchemy ORM）**

围绕 `Product` 表共 9 张表。关键设计决策：
- `platform + platform_id` 唯一约束，用于多次采集自动去重
- `specs`、`sku_specs` 及分析结果字段均使用 JSONB — 不同品牌的车灯规格字段各异（流明、电池、防水等级等）
- `price` 使用 `DECIMAL(10,2)`，严禁 float
- 所有时间字段均带时区，统一 UTC 存储
- `CRUDBase[T]` 为泛型 CRUD 工具类，使用 `CRUDBase(Product)` 即可获得标准增删改查方法。所有方法要求显式传入 `Session`，由调用方控制事务边界
- 数据库连接 URL 在 `models/base.py` 中根据 `config/settings.py` 拼装

**`crawler/` — M2：爬虫引擎**

- `CrawlerEngine` 为顶层调度器：搜索 → 详情 → 评价 → 入库管线 → 图片下载
- `BaseSpider` 定义抽象接口：`search()`、`get_detail()`、`get_reviews()`、`get_price_history()`
- `TaobaoSpider` 是当前爬虫实现（`CrawlerEngine` 默认使用它），使用 Playwright CDP 模式做反反爬。优先走移动端 API 路径，失败时降级为完整浏览器渲染
- `DataPipeline` 负责清洗、校验、去重、入库 — 是爬虫到数据库的唯一写入路径
- `retry.py` 提供 `with_retry()` 重试装饰器和 `Checkpoint` 断点续爬机制
- `ImageDownloader` 在采集阶段收集所有图片 URL，最后批量统一下载

**`analysis/` — M3：评价分析引擎**

- `ReviewAnalyzer` 通过 LangChain 调用 DeepSeek API（`ChatOpenAI` 指向 `api.deepseek.com/v1`），从评价文本中提取结构化知识
- 提取内容：卖点、使用场景、痛点、用户画像、评价摘要
- 使用 `with_structured_output(method="function_calling")` 绑定 Pydantic schema；function calling 失败时回退到 `json_mode`，最多重试 3 次
- `BatchRunner` 封装 `ReviewAnalyzer.analyze_batch()`，支持可配置并发
- 分析结果写入 `review_analysis` 表（JSONB 列，结构对应 `analysis/schemas.py` 中的 Pydantic 模型）

**`config/settings.py` — 配置**

Pydantic Settings，环境变量前缀 `BKL_`，从 `.env` 文件读取。关键配置项：`db_*`（Postgres 连接）、`chroma_*`、`deepseek_api_key`、`crawler_headless`、`crawler_max_products`。

**`crawler/taobao_auth.py` — 淘宝扫码登录 + Cookie 读写（一次性登录）**

- `login_and_save()`：用 **PC 桌面端** Playwright（非持久化，**不生成 `browser_taobao_profile`**）打开 `login.taobao.com/member/login.jhtml`，扫码后检测登录态 Cookie（`cookie2` + `tracknick/unb/lgc`），保存到 `data/cookies/taobao_cookies.json`，然后关闭浏览器。没登录则**不覆盖旧 Cookie**。
- `load_cookies()` / `cookie_dict()` / `cookie_header()`：读取 Cookie 的三种形态（列表 / `{name:value}`字典 / `"name=value;..."`头），供逆向拼参数或请求用。
- `request()`：用保存的 Cookie 发**普通** httpx 请求（移动 UA，适合页面/非签名接口）。无 Cookie 会抛 `RuntimeError` 提示先登录。
- `check_login()`：本地校验登录态（文件存在未过期 + 含登录标记）。
- 依赖 `CookieManager`（`cookie_manager.py`）做持久化，30 天有效期。

**`crawler/mtop.py` — mtop 签名接口（逆向调用，核心）**

参考第三方实现 `references/cn-scraper-mcp_taobao.py`（只读对照，勿改）。调用淘宝 mtop 网关 `https://h5api.m.taobao.com/h5/{api}/{version}/`：
- **`MtopClient`**：`curl_cffi Session(impersonate="chrome")` 伪装 Chrome TLS 指纹（绕过反爬的关键），把登录 Cookie 注入会话。
- 签名：`sign = md5(f"{token}&{t}&{appKey}&{data}")`，`token` 取 `_m_h5_tk` cookie 的 `split("_")[0]`；`appKey=12574478`；`data` 为精简 JSON。
- `call()`：`asyncio.to_thread` 包同步 curl_cffi（curl_cffi 是同步库）；`TOKEN` 错误时刷新 token 重试。
- `search()`：封装搜索接口 **`mtop.taobao.wsearch.appsearch` v1.0**，参数 `{"q","search_action":"initiative","page","n","sversion"}`。
- `parse_items()`：从 `data["itemsArray"]` 解析 `{title,price,sales,id,shop,url}`。
- ⚠️ **baixia 反爬风险**：直接脚本直连可能被阿里 Baxia 拦；真实登录态 + 住宅 IP 成功率更高，兜底用 `TaobaoSpider._search_mobile`（浏览器拦截 mtop 响应）。

**`references/` — 外部参考实现**（只读对照，不被导入；含来源 `references/README.md`）

**`scripts/taobao_login.py`** — 上述能力的命令行入口：`login` / `status` / `request` / `search`。

> ⚠️ 敏感数据：登录 Cookie 与浏览器 profile 已加入 `.gitignore`（`data/cookies/`、`data/browser_taobao_profile/` 等），勿提交。

**`app.py` — Streamlit 入口**

当前为骨架页面：数据库健康检查、数据概览统计、后续页面的占位按钮。

### 规划中的模块（尚未构建）

| 模块 | 目录 | 用途 |
|--------|-----------|---------|
| M4 | `analysis/image/` | 图片 OCR + Qwen-VL 多模态描述、CLIP 向量化 |
| M5 | `knowledge_base/` | ChromaDB 向量存储，支持评价+图片的语义搜索 |
| M6 | `ui/dashboard/` | 选品决策看板，含趋势分析、机会发现 |
| M7 | `ui/creator/` | AI 文案助手，生成商品标题、卖点洞察 |
| M8 | `scheduler/` | 定时调度，自动执行爬虫+分析流水线 |

## 关键约定

- **时间处理**：统一使用 `datetime.now(timezone.utc)`，通过各模块内的 `_utcnow()` 辅助函数获取。禁止使用不带时区的 naive datetime
- **会话管理**：`get_session()` 是生成器函数 — 使用 `session = next(get_session())` 获取，务必在 `finally` 块中 `session.close()`。CRUD 方法内部会 commit；调用方仍需在异常时 rollback
- **异步边界**：爬虫和分析模块使用 `asyncio`；Streamlit 应用为同步模式
- **日志**：全项目统一使用 `loguru`（非标准库 `logging`）
- **数据校验**：`crawler/pipeline.py` 中的 `DataValidator` 负责入库前校验 — 商品（价格范围、平台白名单、标题长度）、评价（评分 1-5、内容长度）
- **去重策略**：商品按 `(platform, platform_id)` 去重，评价按 `platform_review_id` 去重
- **计划偏差基准**：`功能实施计划.md` / `plans/*.md` 是历史决策留痕；当实现与之不一致时，**以 `docs/design-deviations.md` 为准**（含 `parser.py`、`proxy_pool.py`、`spec_normalizer.py` 的取舍理由）
- **合规底线（不可绕过）**：
  - 所有对外请求必须过 `CrawlerEngine._guard()`（robots 检查 + 令牌桶限速），新增蜘蛛也不例外
  - 用户昵称等个人信息**禁止明文入库**，统一走 `crawler/pii.py::hash_user_name()` 写 `user_name_hash`
  - 详见根目录 `DISCLAIMER.md`；相关开关集中在 `config/settings.py` 的「隐私 / 合规」段
