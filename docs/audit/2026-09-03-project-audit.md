# 项目审查报告 — bike-light-kb（自行车灯电商知识库）

> 审查日期：2026-09-03
> 审查方式：superpowers 流程（Brainstorm 确认范围 → 3 个并行 subagent 分别负责「代码质量/Bug」「架构与规划契合度」「安全与合规」→ 主代理综合）
> 审查范围：已实现的 M1(models) / M2(crawler) / M3(analysis)，及 scripts / app.py / config / ui / knowledge_base
> 方法：只读（py_compile 全量校验、git 只读检查、源码 grep/精读），未修改任何文件

---

## 一、总体结论

| 维度 | 评分 | 一句话结论 |
|------|------|-----------|
| 代码质量 / Bug | 🟡 中 | 39/39 文件可编译，结构清晰；但存在 1 个 UTC 时区腐蚀路径、1 个静默数据错误、2 处"文档承诺但实为死代码"的功能 |
| 架构与规划契合 | 🟡 中 | M1 完全对齐；M2 缺 `proxy_pool`/`get_price_history`、`parser` 内联；M3 用 LangChain 替换了计划的 `LLMClient`；**M4 代码已存在但未被计划标注**；M5–M8 缺失 |
| 安全与合规 | 🔴 偏弱 | **硬编码数据库口令、淘宝 Cookie 文件 644 无权限保护、`.env` 含真实密钥且 644、图片下载无 SSRF 防护、系统性规避反爬（ToS 高风险）** |
| 测试 | 🔴 缺失 | 全仓库无任何测试（无 `tests/`、无 `test_*.py`、无 pytest 配置） |

**最该先处理的三件事（P0）**：① 删除硬编码 DB 口令并改必填校验；② 给淘宝 Cookie / `.env` 加 `chmod 600` 并轮换已暴露的 API Key；③ 修复 `review_date` 的 naive datetime → UTC 写入（已在污染历史数据）。

---

## 二、合并发现清单（按严重度）

| # | 严重度 | 维度 | 位置 | 问题 | 建议 |
|---|--------|------|------|------|------|
| 1 | 🔴 P0 | 安全 | `config/settings.py:12`、`docker-compose.yml:11`、`.env.example:7` | DB 口令硬编码为 `bikelight123`，`.env` 缺失时静默回退为生产口令 | 默认值改空串 + 启动校验；compose 改 `${BKL_DB_PASSWORD:?required}` |
| 2 | 🔴 P0 | 安全 | `.env`（实测 644）、`data/cookies/taobao_cookies.json`（644） | 真实 API Key 与淘宝登录态 Cookie 明文落盘、同机可读 | `chmod 600 .env`、`chmod 600` cookie 文件 + 目录 `700`；轮换 Key |
| 3 | 🔴 P0 | 安全 | `crawler/cookie_manager.py:436` | Cookie 写入后无 `os.chmod(0o600)`，含 `cookie2/_tb_token_/unb` 等同"免密登录凭据" | 写入后 `os.chmod(path,0o600)`、目录 `0o700` |
| 4 | 🔴 High | 代码 | `crawler/taobao_spider.py:348-349` → `models/review.py:84-87` | `datetime.fromisoformat(rateDate)` 产生 **naive datetime**，直接写入 `DateTime(timezone=True)` 列 → Postgres 按 session TZ 解释，**时区腐蚀 + 违反 CLAUDE.md UTC 约定** | 解析为 aware UTC（用 `_utcnow` 风格）后再写 |
| 5 | 🔴 High | 代码 | `crawler/taobao_spider.py:530` | `orig_raw = psi.get("originPrice") or raw.get("price")`：无划线价时回退为当前价，`original_price` 恒等于 `price`，**静默错误数据** | 无划线价时返回 `None`，不要回退到 `price` |
| 6 | 🔴 High | 代码 | `crawler/taobao_spider.py:83` vs `engine.py:159-162` | `_captcha_triggered` 永远只被置 `False`，识别分支与上报为**死代码**，文档承诺的反爬安全机制实为 no-op | 检测命中时置 `True` 并贯通 engine；或明确删除该机制 |
| 7 | 🔴 High | 代码 | `crawler/engine.py:152` | `Checkpoint(f"crawl_{started_at...}")` 用每次运行的随机 task_id → **崩溃重启无法续爬**（计划承诺的特性失效） | 用稳定 task_id（如按 keyword） |
| 8 | 🟠 High | 合规 | `crawler/mtop.py:114,62-64,46`、`anti_detect.py:32,77` | 系统性伪装 Chrome TLS、逆向 mtop 签名、注入 stealth 脚本，**违反淘宝 ToS**，存在《反不正当竞争法》/非法获取计算机信息系统数据敞口 | 评估改用淘宝开放平台/联盟官方 API；自用研究需加免责声明 + 限速 + 遵从 robots，禁商用转售 |
| 9 | 🟠 P1 | 架构/数据 | `crawler/taobao_spider.py:514,621-630`、`pipeline.py:160-163` | 近期新增的 `card_extra`（营销USP/榜单/热度）被**合并进 `products.specs` JSONB**，而计划规定 `specs` 仅存规格参数；将污染 M3 规格归一化与 M5 ChromaDB 索引 | 将 `card_extra` 移入独立 `products.marketing` JSONB 列，或至少在计划中补文档并更新模型；榜单建议提升为 `HotRanking` 记录（表已存在） |
| 10 | 🟠 P1 | 安全 | `crawler/downloader.py:223,191` | 图片下载 URL 直接来自网络响应、`follow_redirects=True`、无 scheme/host 白名单 → **SSRF** 风险 | 强制 `https` + 域名白名单（`*.alicdn.com` 等）+ 限制跳转 |
| 11 | 🟠 P1 | 安全 | `downloader.py:236,241` | 下载内容无大小上限，整段 `resp.content` 入内存 → 内存/磁盘打爆 | 流式写入 + `max_bytes` 上限 |
| 12 | 🟠 P1 | 安全 | `docker-compose.yml:12-13,28-29` | PG(5433) / ChromaDB(8000) 映射到宿主所有网卡，ChromaDB 无鉴权 | 绑定 `127.0.0.1:` |
| 13 | 🟠 P1 | 安全 | `config/settings.py:19,22` | `deepseek_api_key`/`dashscope_api_key` 为普通 `str`，`repr`/`traceback`/pydantic 错误会打印完整 Key | 改 `pydantic.SecretStr` |
| 14 | 🟡 P2 | 安全 | `requirements.txt:2-43` | 依赖几乎全为 `>=` 无上限、无 hash 锁定 → 构建不可复现、易被投毒 | `pip-compile` 生成锁文件 + `--require-hashes` |
| 15 | 🟡 P2 | 合规 | `crawler/pipeline.py:292`、`models/review.py:73` | `user_name`（昵称，伪匿名标识）入库且无保留期/删除机制，PIPL 下仍属个人信息 | 入库前哈希或丢弃，设定保留期 |
| 16 | 🟡 Medium | 代码 | `app.py:118,140` | `datetime.now()` 缺 `timezone`（违反 UTC 约定） | 改 `datetime.now(timezone.utc)` |
| 17 | 🟡 Medium | 代码 | `app.py:53,65,119`、`engine.py:211`、`pipeline.py:78`、`pipeline.py:286-308` | 多处 `except Exception` / `except: pass` 吞掉 DB/价格快照/校验错误，部分 rollback 对已 commit 行无效 | 改带 loguru 日志的处理；删除误导性的 rollback |
| 18 | 🟡 Medium | 架构 | M2/M3 计划缺口 | `parser.py`（逻辑内联）、`proxy_pool.py`、`get_price_history()`、`spec_normalizer.py` + `spec_normalize` prompt 均未实现 | 实现或自计划/规格中删除 |
| 19 | 🟡 Low | 代码 | `crawler/schemas.py:45-52` (`SkuData`)、`ProductDetail.skus` | 定义但 0 处引用，**死代码** | 删除或接入解析 |
| 20 | 🟡 Low | 代码 | `mtop.py:129-141`、`review_analyzer.py:81`、`engine.py:159` | `Set-Cookie` 按 `,` 拼接脆弱；信号量在 `__init__` 绑循环；captcha 检查读了外层 `spider` 变量 | 逐项加固 |

---

## 三、架构与规划契合度（摘要）

- **M1 完全对齐**：9 张表 + 列结构 + `db/init.sql` 全部一致。
- **M2 部分对齐**：`CrawlerEngine`/`BaseSpider`/`TaobaoSpider`/`DataPipeline`/`ImageDownloader` 到位；缺 `parser.py`、`proxy_pool.py`、`get_price_history()`；多出计划未列的 `mtop.py`、`taobao_auth.py`（属合理演进，但应补文档）。
- **M3 设计偏离**：计划要求 `LLMClient.chat/chat_batch`，实际用 LangChain `ChatOpenAI` + `with_structured_output`（功能等价，但类名契约未满足）；缺 `spec_normalizer.py`。
- **M4 已存在却未在"计划"中标注**：`analysis/image/*.py` 约 1538 行（PaddleOCR→easyocr），与计划说的 M4 一致但计划声称"仅 M1–M3 已实现"，自相矛盾。
- **M5/M6/M7/M8 缺失**：仅空 `__init__.py` 或目录不存在 → 计划的全链路（KB→看板）目前无法演示，是最大的进度缺口。
- **依赖清单噪声**：`requirements.txt` 含不存在的 `chromadb-client`（应是 `chromadb`）、未使用的 `selenium`。

---

## 四、做得好的地方（保留）

- 39/39 文件 `py_compile` 通过；全项目统一用 `loguru`、`Decimal` 做价格运算、`get_session()` 生成器配 `finally: close()`。
- **无 SQL 注入**：全部走 SQLAlchemy 参数化 / `?` 占位符；无 f-string 拼接 SQL。
- **无不安全反序列化/命令注入**：无 `eval/exec/pickle.loads/yaml.load`；`subprocess` 用静态参数且非 `shell=True`。
- **日志不含敏感值**：未发现落盘完整网络响应或打印 token。
- **.gitignore 到位**：历史从未提交 `.env`/`cookies`（已用 `git log/ls-files` 核验）；图片下载有 Content-Type 白名单。
- `ReviewAnalyzer` 具备 function_calling→json_mode 回退与重试；`_extract_items_from_json` 对多形态 JSON 鲁棒。

---

## 五、修复路线（建议优先级）

**P0（立即，安全/数据正确）**
1. 删除硬编码 DB 口令默认值 + 启动校验；compose 改必填环境变量。
2. `chmod 600 .env` 与 `data/cookies/*`；Cookie 写入后 `os.chmod(0o600)`；轮换 `.env` 中已暴露的 API Key。
3. 修复 `review_date` naive→aware UTC 写入（防止继续污染历史数据）。

**P1（本迭代内）**
4. 修 `original_price` 回退 bug（无划线价置 `None`）。
5. 落实 captcha 检测 / 续爬 checkpoint（否则删除该承诺）。
6. 图片下载加 https+域名白名单+大小上限；PG/Chroma 端口绑定 127.0.0.1。
7. API Key 改 `SecretStr`；`card_extra` 移出 `specs`（独立列或 HotRanking 表）。

**P2（改进项）**
8. 依赖锁定 + hash；`user_name` 哈希/丢弃 + 保留期。
9. 补充 `tests/`（pipeline 校验、价格解析、CRUD upsert 至少）。
10. 清理死代码（`SkuData`/吞错分支）、补全 `parser`/`proxy_pool`/`spec_normalizer` 或自计划删除。
11. 补 robots 遵从 / 限速 / 免责声明，评估改用淘宝官方 API（合规）。

---

## 五之二、修复进度（2026-09-04 更新）

**P0 / P1 已全部闭环**（详见 `.workbuddy/memory/2026-09-04.md`）：硬编码口令、`chmod 600`、
`review_date` UTC、`original_price` 回退 bug、captcha + checkpoint 打通、图片下载 SSRF 白名单与大小上限、
端口绑定 `127.0.0.1`、API Key 改 `SecretStr`、`card_extra` 移出 `specs` 并提升榜单到 `hot_rankings` 表。

**P2 本轮已闭环：**

| # | 项 | 落地 |
|---|---|---|
| 8a | 依赖锁定 | `requirements.txt` 全部加上下限、删除不存在的 `chromadb-client` 与未使用的 `selenium`；新增 `requirements.lock.txt`（167 包精确版本）+ `scripts/gen_requirements_lock.py`（`--check` 供 CI 校验 lock 是否过期）。带哈希的锁需 `pip-compile --generate-hashes`，因含 torch/easyocr 未在本次生成，已在 lock 头部注明 |
| 8b | `user_name` 脱敏 + 保留期 | 新增 `crawler/pii.py`：`hash_user_name()`（HMAC-SHA256 + pepper）与 `anonymize_expired_reviews()`；`DataPipeline.save_reviews` **不再写明文昵称**，只写 `user_name_hash`；`scripts/anonymize_pii.py` 支持 `--days/--dry-run`，建议挂 cron；`scripts/seed_test_data.py` 同步改用哈希 |
| 9 | 测试 | 从 0 → **60 个用例全绿**（`pytest.ini` 启用 `asyncio_mode=auto`）；新增 `test_pii.py`(14)、`test_compliance.py`(15)、`test_mtop_cookie.py`(5) |
| 10 | 死代码 / 计划缺口 | `SkuData` 已删除；`pipeline` 去掉对已提交行无效的误导性 rollback，静默 `except` 全部改为 `logger` 记录；`parser.py`/`proxy_pool.py`/`spec_normalizer.py`/`get_price_history()` 的取舍写入 **`docs/design-deviations.md`**（代理池因合规主动放弃） |
| 11 | robots / 限速 / 免责 | 新增 `crawler/compliance.py`：`RobotsChecker`（RFC 9309，4xx 放行 / 5xx 拒绝 / 网络异常放行并告警 / 按域名缓存 / 暴露 Crawl-delay）+ `RateLimiter`（令牌桶，async + sync）；`CrawlerEngine._guard()` 作为**唯一对外请求闸门**接入搜索与详情阶段；新增根目录 `DISCLAIMER.md`，Streamlit 页脚同步声明 |

**顺带修掉的隐藏 bug（本轮发现）**

- `crawler/downloader.py:30` 存在 `zheimport ipaddress` 语法损坏（前轮编辑残留），
  整个 `crawler` 包无法导入 → 已修正为 `import ipaddress`，并加 `compileall` 全量校验。
- `crawler/mtop.py::_refresh_from_headers` 按 `,` 切分 Set-Cookie，
  遇到 `Expires=Wed, 09 Jun ...` 会截断 `_m_h5_tk` → 改用 `_extract_h5_tk_value()` 正则，
  并补回归测试。
- `crawler/engine.py` Phase 2 用了 Phase 1 循环残留的外层 `spider` 变量判断验证码
  （多平台时会误判）→ `all_briefs` 改为 `(spider, brief)` 二元组。
- `app.py` 两处 `datetime.now()` 无时区、健康检查吞异常 → 改 UTC 时间 + `loguru` 记录。

**仍未处理（需业务决策）**

- 评估改用淘宝开放平台 / 联盟官方 API（彻底解决 ToS 风险）——当前仍依赖逆向 mtop。
- M5–M8（知识库检索 / 看板 / 创作助手 / 调度）尚未开工，全链路暂不可演示。
- 带哈希的依赖锁（`--require-hashes`）与镜像源审计。

---

## 六、附：本次审查范围与方法

- 子代理 A（代码质量/Bug）：全量 `py_compile`、CLAUDE.md 约定核查、死代码/异常处理/bug 模式扫描、测试覆盖核查。
- 子代理 B（架构/规划）：精读 `功能实施计划.md` 与 `plans/*.md`，逐组件比对 `models/`、`crawler/`、`analysis/` 实际代码。
- 子代理 C（安全/合规）：密钥/口令/`git` 只读检查、SQL 注入、反序列化、SSRF、反爬合规、依赖风险。
- 全程只读，未改动任何源码或提交。

> 注：本报告第 9 项（card_extra 污染 specs）涉及本次会话早些时候新增的字段映射改动；建议后续优先将其从 `specs` 剥离，避免影响 M3 规格归一化与 M5 向量检索。
