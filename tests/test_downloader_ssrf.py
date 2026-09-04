"""测试图片下载器的 SSRF 防护（URL 白名单 + 内网拦截）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.downloader import ImageDownloader


def test_safe_alicdn_url():
    assert ImageDownloader._is_safe_image_url("https://img.alicdn.com/x.jpg") is True
    assert ImageDownloader._is_safe_image_url("http://gd2.alicdn.com/y.png") is True
    assert ImageDownloader._is_safe_image_url("https://g-search1.alicdn.com/z.webp") is True


def test_reject_non_http_schemes():
    assert ImageDownloader._is_safe_image_url("file:///etc/passwd") is False
    assert ImageDownloader._is_safe_image_url("ftp://example.com/x.jpg") is False


def test_reject_localhost_and_private_ips():
    assert ImageDownloader._is_safe_image_url("http://localhost/x.jpg") is False
    assert ImageDownloader._is_safe_image_url("http://127.0.0.1/x.jpg") is False
    assert ImageDownloader._is_safe_image_url("http://10.0.0.5/x.jpg") is False
    assert ImageDownloader._is_safe_image_url("http://192.168.1.1/x.jpg") is False
    assert ImageDownloader._is_safe_image_url("http://172.16.5.5/x.jpg") is False
    assert ImageDownloader._is_safe_image_url("http://169.254.169.254/latest/meta-data/") is False


def test_reject_untrusted_domain():
    assert ImageDownloader._is_safe_image_url("https://evil.com/x.jpg") is False
    assert ImageDownloader._is_safe_image_url("https://images.google.com/x.jpg") is False


def test_reject_malformed():
    assert ImageDownloader._is_safe_image_url("") is False
    assert ImageDownloader._is_safe_image_url(None) is False
    assert ImageDownloader._is_safe_image_url("not a url") is False
