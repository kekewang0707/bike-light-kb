#!/usr/bin/env python3
"""淘宝搜索 mtop 接口调试脚本。

相比 `taobao_login.py search`，本脚本把接口的"原始真相"全部摊开，
便于排查：
  - 反爬 / TOKEN 错误（ret 里出现 TOKEN 字样，或返回 HTML 拦截页）
  - 签名是否生效（打印最终带签名的完整请求 URL）
  - 字段名是否变化（打印 itemsArray 第一条商品的全部原始键）
  - 解析为什么为空（itemsArray 为空时，转储 data 原始内容）

用法::
    python scripts/debug_search.py "自行车灯" 1
    python scripts/debug_search.py "自行车灯" 1 --raw     # 额外转储整段 data 原文
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.mtop import (  # noqa: E402
    MtopClient,
    MTOP_BASE,
    DEFAULT_APP_KEY,
    parse_items,
)

API = "mtop.taobao.wsearch.appsearch"
VERSION = "1.0"
N = 1


def _rule(title: str) -> None:
    print("\n" + "=" * 64)
    print(title)
    print("=" * 64)


async def debug(q: str, page: int, raw: bool) -> int:
    # 1) 初始化客户端（无 Cookie 会直接抛 RuntimeError，这里友好提示）
    try:
        client = MtopClient()
    except RuntimeError as e:
        print(f"⚠️  {e}")
        return 1

    data = {
        "q": q,
        "search_action": "initiative",
        "page": str(page),
        "n": str(N),
        "sversion": "9.9.9",
    }

    _rule("请求构造")
    print("网关:", f"{MTOP_BASE}{API}/{VERSION}/")
    print("appKey:", DEFAULT_APP_KEY)
    print("data 参数:", json.dumps(data, ensure_ascii=False))

    # 2) 发起调用（拿回 JSON + 原始响应，便于看 URL / 状态码 / Set-Cookie）
    j, resp = await client.call(API, VERSION, data=data)

    _rule("响应概览")
    if resp is not None:
        print("HTTP 状态:", resp.status_code)
        print("最终请求 URL(含签名参数):")
        print("  ", resp.url)
        sc = resp.headers.get("set-cookie", "") or ""
        print("Set-Cookie 含 _m_h5_tk:", "_m_h5_tk" in sc)
    else:
        print("resp: None（请求失败，见下方 error）")

    # 3) 顶层结构
    if "error" in j:
        _rule("请求异常")
        print("error:", j["error"])
        return 1

    print("ret:", j.get("ret"))
    print("顶层键:", list(j.keys()))
    data_part = j.get("data", {}) or {}
    print("data 键:", list(data_part.keys()))
    arr = data_part.get("itemsArray", []) or []
    print("itemsArray 长度:", len(arr))

    # 4) 第一条商品的全部原始字段
    if arr:
        _rule("第一条商品原始字段 (itemsArray[0])")
        print(json.dumps(arr[0], ensure_ascii=False, indent=2)[:5000])
    else:
        _rule("itemsArray 为空 —— 转储 data 原文排查")
        print(json.dumps(data_part, ensure_ascii=False, indent=2)[:5000])
        return 0

    # 5) 归一化输出对照
    _rule("parse_items() 归一化输出 (前 20 条)")
    for it in parse_items(j, limit=20):
        print(json.dumps(it, ensure_ascii=False))

    # 6) 可选：整段 data 原文
    if raw:
        _rule("data 原文 (--raw)")
        print(json.dumps(data_part, ensure_ascii=False, indent=2)[:8000])

    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="淘宝搜索 mtop 接口调试")
    p.add_argument("query", help="搜索关键词")
    p.add_argument("page", type=int, nargs="?", default=1, help="页码，默认 1")
    p.add_argument("--raw", action="store_true", help="额外转储整段 data 原文")
    args = p.parse_args()
    try:
        return asyncio.run(debug(args.query, args.page, args.raw))
    except KeyboardInterrupt:
        print("\n已中断")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
