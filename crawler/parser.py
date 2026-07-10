"""通用解析工具函数 — 价格格式化、数字提取、HTML 清理。

这些工具函数被各平台爬虫和 DataPipeline 共用，避免重复实现。
"""

import re
import html as html_mod
from decimal import Decimal, InvalidOperation
from typing import Optional


# ============================================================================
# 价格解析
# ============================================================================

def parse_price(raw: str) -> Optional[Decimal]:
    """从各种格式的价格字符串中提取 Decimal 值。

    支持格式:
    - "¥89.00"
    - "89.00元"
    - "89"
    - "8,999.00"
    - " 89.00 "（带空格）
    - "" 或 "暂无报价" → None

    返回:
        Decimal 值，无法解析时返回 None

    >>> parse_price("¥89.00")
    Decimal('89.00')
    >>> parse_price("暂无报价")
    None
    """
    if not raw or not isinstance(raw, str):
        return None

    # 去除空白、货币符号、中文
    cleaned = raw.strip()
    cleaned = re.sub(r'[¥￥$€£\s]', '', cleaned)
    cleaned = re.sub(r'[元块]', '', cleaned)
    # 去除千位分隔逗号
    cleaned = cleaned.replace(',', '')

    if not cleaned:
        return None

    try:
        return Decimal(cleaned)
    except InvalidOperation:
        # 尝试只提取数字和小数点
        match = re.search(r'[\d.]+', cleaned)
        if match:
            try:
                return Decimal(match.group())
            except InvalidOperation:
                pass
    return None


# ============================================================================
# 数字提取
# ============================================================================

def parse_int(raw: str) -> Optional[int]:
    """从字符串中提取整数。

    处理常见格式:
    - "2.3万+" → 23000
    - "5000+" → 5000
    - "3,120" → 3120
    - "" → None

    >>> parse_int("2.3万+")
    23000
    >>> parse_int("3,120")
    3120
    """
    if not raw or not isinstance(raw, str):
        return None

    raw = raw.strip()

    # 万单位转换
    wan_match = re.search(r'([\d.]+)\s*万', raw)
    if wan_match:
        return int(float(wan_match.group(1)) * 10000)

    # 千位分隔逗号
    cleaned = raw.replace(',', '').replace('+', '').strip()

    match = re.search(r'\d+', cleaned)
    if match:
        return int(match.group())

    return None


def parse_good_rate(raw: str) -> Optional[Decimal]:
    """解析好评率。

    支持格式:
    - "97%" → Decimal('97.00')
    - "98.5%" → Decimal('98.50')
    - "4.9分" → Decimal('98.00')  # 5分制转百分制
    - "" → None
    """
    if not raw or not isinstance(raw, str):
        return None

    raw = raw.strip()

    # 百分制
    pct_match = re.search(r'([\d.]+)\s*%', raw)
    if pct_match:
        return Decimal(pct_match.group(1))

    # 5分制转百分制
    score_match = re.search(r'([\d.]+)\s*分', raw)
    if score_match:
        score = Decimal(score_match.group(1))
        if score <= 5:
            return score * 20

    return None


# ============================================================================
# HTML 清理
# ============================================================================

def clean_html(raw: str) -> str:
    """去除 HTML 标签和实体，返回纯文本。

    >>> clean_html('<span>800流明</span>')
    '800流明'
    >>> clean_html('防水&amp;防尘')
    '防水&防尘'
    """
    if not raw:
        return ""

    # 去除 HTML 标签
    text = re.sub(r'<[^>]+>', '', raw)
    # 解码 HTML 实体
    text = html_mod.unescape(text)
    # 去除多余空白
    text = re.sub(r'\s+', ' ', text).strip()

    return text


def extract_text_from_html(html_str: str, selector_hint: str = None) -> str:
    """从 HTML 片段中提取文本内容。

    这是一个兜底方法 — 当 BeautifulSoup 因某些原因不可用时使用。
    正常情况下推荐用 BeautifulSoup 直接解析。
    """
    return clean_html(html_str)


# ============================================================================
# 规格参数解析
# ============================================================================

def parse_specs_from_kv_pairs(pairs: list[tuple[str, str]]) -> dict:
    """将 (键, 值) 对列表转为规格字典。

    参数:
        pairs: [("流明", "800"), ("防水等级", "IPX6"), ...]

    返回:
        {"流明": "800", "防水等级": "IPX6", ...}

    会自动处理：
    - 值为空或"暂无"的跳过
    - 单位分离（"800流明" → 值"800"，但保留原样在值中）
    """
    specs = {}
    for key, value in pairs:
        key = key.strip().rstrip("：:")
        value = value.strip()
        if not key or not value or value in ("暂无", "-", "--", "无"):
            continue
        specs[key] = value
    return specs


# ============================================================================
# URL 处理
# ============================================================================

def normalize_jd_url(url: str, sku_id: str = None) -> str:
    """标准化京东商品链接。

    去除追踪参数，统一为 https://item.jd.com/{skuId}.html 格式。
    """
    if not url:
        return ""

    # 提取 skuId
    if not sku_id:
        match = re.search(r'/(\d+)\.html', url)
        if match:
            sku_id = match.group(1)

    if sku_id:
        return f"https://item.jd.com/{sku_id}.html"

    return url


def extract_jd_sku_id(url: str) -> Optional[str]:
    """从京东 URL 中提取商品 skuId。

    >>> extract_jd_sku_id("https://item.jd.com/100012345.html")
    '100012345'
    """
    if not url:
        return None
    match = re.search(r'/(\d+)\.html', url)
    return match.group(1) if match else None
