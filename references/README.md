# references — 外部参考实现

本目录存放项目开发过程中参考的**第三方实现原文**，仅作对照阅读，**不会被本项目导入或运行**。

## 文件清单

| 文件 | 说明 |
|------|------|
| `cn-scraper-mcp_taobao.py` | 淘宝/天猫搜索的 mtop 签名实现（curl_cffi 伪装 Chrome TLS 指纹 + MTOP HMAC-MD5 签名） |

## 来源

- 原始文件：`https://raw.githubusercontent.com/goesByhc/cn-scraper-mcp/master/src/cn_scraper_mcp/engines/taobao.py`
- 项目仓库：https://github.com/goesByhc/cn-scraper-mcp
- 通过 web_fetch / curl 抓取原文保存，保持字节一致。

## 与本项目的对应关系

本项目在 `crawler/mtop.py` 中借鉴了此实现的关键点：

- `APPKEY = "12574478"`
- 搜索接口 `mtop.taobao.wsearch.appsearch` v1.0，参数 `{"q","search_action","page","n","sversion"}`
- `sign = md5(f"{token}&{t}&{appKey}&{data}")`
- token 取自 `_m_h5_tk` cookie 的 `split("_")[0]`
- 用 `curl_cffi` `Session(impersonate="chrome")` 伪装 Chrome TLS 指纹
- 响应里 `TOKEN` 错误时刷新 token 重试
- 商品列表在 `data["itemsArray"]`

> ⚠️ 请勿修改本文件；修改请改 `crawler/mtop.py`。此文件为只读对照材料。
