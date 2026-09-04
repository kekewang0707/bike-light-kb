# 设计演进与偏差记录

> 用途：记录「原实施计划承诺」与「当前实现」之间的差异及原因。
> 计划文档（`功能实施计划.md` / `plans/*.md`）保持原样作为历史决策留痕，
> 一切以本文件为准。对应 2026-09-03 审查报告第 18 项。

## 结论速览

| 计划中的条目 | 当前状态 | 决策 |
|-------------|---------|------|
| `crawler/parser.py` | 未实现（逻辑内联在 `taobao_spider.py`） | **从计划中删除**，理由见下 |
| `crawler/proxy_pool.py` | 未实现 | **主动放弃**（合规原因） |
| `BaseSpider.get_price_history()` | 以 `DataPipeline.save_price_snapshot()` 替代 | **接口改名**，语义收窄 |
| `analysis/spec_normalizer.py` + `spec_normalize` prompt | 未实现 | **延后到 M5**（检索需求出现时再做） |
| `analysis/llm_client.py::LLMClient` | 用 LangChain `ChatOpenAI` 实现 | **保留**，仅类名契约不同 |
| `crawler/mtop.py` / `crawler/taobao_auth.py` | 计划未列，实际存在 | **采纳为正式设计** |
| `analysis/image/*`（M4） | 计划未标注但已实现 | **补标为 M4 已完成** |

## 逐条理由

### 1. `parser.py` — 不再拆分为独立模块

计划设想的通用解析工具（价格格式化、数字提取、HTML 清理）实际都高度绑定淘宝的
字段结构：`_parse_price` 要处理 `priceShowWithIcon`、`_extract_rank_no` 要认识
`"热销第3名"` 这类文案。拆出去后每个函数仍只有淘宝一个调用方，反而增加跳转成本。

**当前落地**：`crawler/taobao_spider.py` 中的 `_parse_card_fields` / `_extract_rank_no` /
`_parse_int` 等私有函数，已在 `tests/test_spider_card_fields.py` 中覆盖。
**将来如果出现第二个平台（pdd）且共用解析逻辑，再抽取为 `parser.py`。**

### 2. `proxy_pool.py` — 主动放弃

代理 IP 轮换的目的只有一个：规避目标平台的封禁策略。这与本项目的
[合规底线](../../DISCLAIMER.md) 直接冲突——一旦做了 IP 轮换，就很难主张
"仅个人学习研究、不对平台造成压力"。

**替代方案**：`crawler/compliance.py` 的令牌桶限速 + `engine.py` 的商品间随机延迟
（15~35 秒）+ robots 遵从，把速率压到人肉浏览的量级。

### 3. `get_price_history()` → `save_price_snapshot()`

原接口假设平台会返回历史价格序列，实测淘宝并不稳定提供。
改为**每日快照自累积**：每次采集调用 `DataPipeline.save_price_snapshot()`，
按 `(product_id, 日期)` upsert 到 `price_histories` 表，跑满一段时间即可自行绘出价格曲线。

### 4. `spec_normalizer.py` — 延后

规格归一化（"800 流明" vs "800LM" vs "最大亮度800"）真正被消费的时机是
**M5 的向量检索 / 结构化筛选**。在 M5 开工前做，缺少需求校验，容易做成自嗨的正则集合。
当前规格参数原样存入 `products.specs` (JSONB)，等到 M5 检索侧需要时再实现。

### 5. `LLMClient` 用 LangChain 实现

计划要求自研 `LLMClient.chat/chat_batch`，实际使用
`langchain_openai.ChatOpenAI` + `with_structured_output()`，
能力等价且省去一层自维护（重试、结构化输出、function calling 回退）。
`analysis/llm_client.py` 仍是对外唯一入口，替换实现不影响调用方。

### 6. M4（图片分析）状态纠正

`analysis/image/`（OCR / Qwen-VL 多模态 / CLIP 向量化）已完成约 1500 行，
但 `功能实施计划.md` 仍写"仅 M1–M3 已实现"。以本文件为准：**M4 已完成主体**。
M5–M8（知识库检索、看板、创作助手、调度）尚未开工。
