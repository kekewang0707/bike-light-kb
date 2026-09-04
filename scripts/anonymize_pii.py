#!/usr/bin/env python3
"""PII 保留期清理 — 匿名化超期评价中的用户昵称哈希。

合规要求（PIPL）：个人信息应设定保留期，超期后删除或匿名化。
本脚本只清空 ``reviews.user_name_hash`` / ``reviews.user_name``，
评价正文、评分、晒图等分析价值高的字段全部保留。

Usage::

    # 预览将被清理的行数（不写库）
    python scripts/anonymize_pii.py --dry-run

    # 按默认保留期（BKL_DATA_RETENTION_DAYS，默认 365 天）清理
    python scripts/anonymize_pii.py

    # 指定保留期
    python scripts/anonymize_pii.py --days 180

建议用 cron / 系统定时器每周执行一次::

    0 3 * * 1  cd /path/to/bike-light-kb && venv/bin/python scripts/anonymize_pii.py
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger  # noqa: E402

from config.settings import settings  # noqa: E402
from crawler.pii import anonymize_expired_reviews  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="匿名化超期评价的用户昵称哈希")
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help=f"保留期天数（默认取 BKL_DATA_RETENTION_DAYS，当前 {settings.data_retention_days}）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只统计将要清理的行数，不修改数据库",
    )
    args = parser.parse_args()

    try:
        affected = anonymize_expired_reviews(
            retention_days=args.days, dry_run=args.dry_run
        )
    except Exception as e:  # noqa: BLE001 - CLI 顶层兜底，异常需回显给用户
        logger.error(f"PII 清理失败: {e}")
        return 1

    if args.dry_run:
        logger.info(f"dry-run 完成：{affected} 条评价将在正式执行时被匿名化")
    else:
        logger.info(f"完成：已匿名化 {affected} 条评价")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
