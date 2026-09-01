#!/usr/bin/env python3
"""淘宝扫码登录 / Cookie 请求命令行工具。

用法::
    # 1) 扫码登录并保存 Cookie（弹出浏览器，扫码后自动保存/关窗结束）
    python scripts/taobao_login.py login

    # 2) 读取 Cookie 并请求淘宝（校验登录态、或抓取带登录态内容）
    python scripts/taobao_login.py request --url "https://main.m.taobao.com/" --method GET
    python scripts/taobao_login.py request --url "https://h5api.m.taobao.com/..." -X POST -d '{"k":1}'

    # 3) 查看本地保存的登录态
    python scripts/taobao_login.py status
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# 让脚本可从项目根导入 crawler 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.taobao_auth import (  # noqa: E402
    login_and_save,
    request,
    load_cookies,
    cookie_header,
    check_login,
)
from crawler.mtop import search as mtop_search, parse_items  # noqa: E402


def cmd_login(_args: argparse.Namespace) -> int:
    """扫码登录并保存 Cookie。"""
    cookies = asyncio.run(login_and_save())
    if cookies:
        print(f"✅ 登录成功，已保存 {len(cookies)} 个 Cookie。")
        return 0
    print("⚠️  未检测到有效登录。请重试并确保扫码完成。")
    return 1


def cmd_request(args: argparse.Namespace) -> int:
    """读取 Cookie 并发起带登录态的请求。"""
    resp = asyncio.run(
        request(
            args.url,
            method=args.method,
            headers=(
                dict(kv.split("=", 1) for kv in args.header.split(","))
                if args.header else None
            ),
            json_body=None,
            data=args.data,
        )
    )
    print(f"HTTP {resp.status_code}")
    print(resp.text[:3000])
    return 0 if resp.status_code < 400 else 1


def cmd_search(args: argparse.Namespace) -> int:
    """调用淘宝搜索 mtop 接口（带签名）。"""
    data = asyncio.run(mtop_search(args.q, page=args.page))

    print(f"== mtop 搜索: {args.q!r}  page={args.page} ==")
    ret = data.get("ret")
    print("ret:", ret)
    items = parse_items(data, limit=20)
    if items:
        print(f"返回条目数: {len(items)}")
        for it in items:
            print(f"  - {str(it['title'])[:40]}  ¥{it['price']}  销量{it['sales']}  店:{it['shop']}")
    else:
        import json as _json
        print("未解析到商品列表，原始 data:", _json.dumps(data.get("data", {}), ensure_ascii=False)[:800])
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    """查看本地保存的登录态。"""
    from crawler.cookie_manager import CookieManager

    mgr = CookieManager(platform="taobao")
    cookies = load_cookies(mgr)
    if not cookies:
        print("不存在或已过期的有效期 Cookie，请先运行: python scripts/taobao_login.py login")
        return 1

    valid = asyncio.run(check_login(mgr))
    print(f"Cookie 数量: {len(cookies)}")
    print(f"登录态: {'✅ 有效' if valid else '⚠️  不完整/可能失效'}")
    print(f"保存文件: {mgr.cookie_file}")
    print(f"剩余天数: {mgr.days_until_expiry()}")
    print("Cookie 头(前 120 字符):", cookie_header(cookies)[:120])
    return 0 if valid else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="淘宝扫码登录 / Cookie 请求工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="扫码登录并保存 Cookie")

    req = sub.add_parser("request", help="读取 Cookie 并请求淘宝")
    req.add_argument("--url", "-u", required=True, help="目标 URL")
    req.add_argument("--method", "-X", default="GET", help="HTTP 方法，默认 GET")
    req.add_argument("--data", "-d", default=None, help="要发送的原始请求体（仅 POST/PUT）")
    req.add_argument("--header", "-H", default=None, help="额外 HTTP 头，如 'k=v, k2=v2'")

    sub.add_parser("status", help="查看本地保存的登录态")

    srch = sub.add_parser("search", help="调用淘宝搜索 mtop 接口（带签名）")
    srch.add_argument("-q", "--query", dest="q", required=True, help="搜索关键词")
    srch.add_argument("-p", "--page", type=int, default=1, help="页码，默认 1")

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "login":
        return cmd_login(args)
    if args.command == "request":
        return cmd_request(args)
    if args.command == "search":
        return cmd_search(args)
    if args.command == "status":
        return cmd_status(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
