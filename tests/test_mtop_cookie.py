"""mtop Set-Cookie 解析回归测试。

审计 #20 记录的 bug：原实现把整串 Set-Cookie 按 ``,`` 切分，
而 ``Expires=Wed, 09 Jun 2021 ...`` 这类属性本身就含逗号，
会导致 token 被截断（表现为签名失败、mtop 频繁报 TOKEN 错误）。
"""

from crawler.mtop import _extract_h5_tk_value, _split_token


def test_extract_full_value_with_expires_comma():
    header = (
        "_m_h5_tk=9f3c2b1a_1789012345678; "
        "Path=/; Expires=Wed, 09 Jun 2021 10:18:14 GMT; HttpOnly"
    )
    assert _extract_h5_tk_value(header) == "9f3c2b1a_1789012345678"


def test_extract_from_multiple_cookies_joined():
    """多条 Cookie 被合并成一个头时仍能定位到 _m_h5_tk。"""
    header = "foo=1; Path=/, _m_h5_tk=abc_123; Path=/, bar=2; Path=/"
    assert _extract_h5_tk_value(header) == "abc_123"


def test_extract_returns_empty_when_absent():
    assert _extract_h5_tk_value("foo=1; Path=/") == ""
    assert _extract_h5_tk_value("") == ""
    assert _extract_h5_tk_value(None) == ""


def test_extract_not_affected_by_later_comma_sections():
    """token 之后出现的其他 cookie 属性不会污染取值。"""
    header = "_m_h5_tk=tk_999; Domain=.taobao.com; Expires=Thu, 10 Jun 2021 00:00:00 GMT"
    value = _extract_h5_tk_value(header)
    assert value == "tk_999"
    assert _split_token(value) == "tk"


def test_split_token_handles_both_separators():
    assert _split_token("abc_123") == "abc"
    assert _split_token("abc,123") == "abc"
    assert _split_token("") == ""
