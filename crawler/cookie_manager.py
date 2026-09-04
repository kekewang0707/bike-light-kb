"""Cookie/Session 持久化管理 + 本地 Chrome Cookie 导入。

淘宝登录态 Cookie 有效期约 30 天。支持两种来源：

1. 持久化文件 (data/cookies/taobao_cookies.json)：爬虫运行时自动保存/加载
2. 本地 Chrome Cookie DB：首次运行时从 Chrome 导入，无需手动导出

Usage::

    from crawler.cookie_manager import CookieManager

    mgr = CookieManager()

    # 优先从保存文件加载
    cookies = mgr.load()

    # 如果文件不存在，尝试从本地 Chrome 导入
    if not cookies:
        cookies = mgr.load_from_chrome()
"""

import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from loguru import logger


# ============================================================================
# Chrome Cookie 读取器
# ============================================================================

class ChromeCookieReader:
    """从本地 Chrome 的加密 Cookie 数据库中读取指定域名的 Cookie。

    支持 macOS、Linux、Windows 三平台。

    macOS 实现：
    - Chrome Cookie 使用 AES-128-CBC 加密
    - 解密密钥存储于 macOS Keychain 中
    - 通过 `security` CLI 获取密钥后解密

    Usage::

        reader = ChromeCookieReader()
        cookies = reader.extract("jd.com")
        # → [{"name":"pin","value":"...","domain":".jd.com",...}, ...]
    """

    # Chrome Cookie DB 路径（按平台）
    _CHROME_COOKIE_PATHS = {
        "darwin": "~/Library/Application Support/Google/Chrome/Default/Cookies",
        "linux": "~/.config/google-chrome/Default/Cookies",
        "win32": "~/AppData/Local/Google/Chrome/User Data/Default/Cookies",
    }

    def __init__(self, chrome_path: Optional[str] = None):
        """
        参数:
            chrome_path: Chrome Cookie DB 路径。不传则自动检测。
        """
        if chrome_path:
            self._cookie_db_path = Path(chrome_path)
        else:
            import sys
            default = self._CHROME_COOKIE_PATHS.get(sys.platform, "")
            self._cookie_db_path = Path(default).expanduser()

    # ------------------------------------------------------------------
    # 公共方法
    # ------------------------------------------------------------------

    def extract(self, domain: str) -> List[dict]:
        """从 Chrome Cookie DB 提取指定域名的 Cookie。

        参数:
            domain: 目标域名，如 "jd.com"。会匹配 *.jd.com 和 jd.com。

        返回:
            Playwright 兼容的 Cookie 字典列表。失败返回空列表。
        """
        if not self._cookie_db_path.exists():
            logger.warning(f"Chrome Cookie 文件不存在: {self._cookie_db_path}")
            return []

        # 复制 DB 到临时文件（Chrome 运行时会锁定原始文件）
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            shutil.copy2(self._cookie_db_path, tmp_path)
            return self._read_cookies(tmp_path, domain)
        except sqlite3.Error as e:
            logger.error(f"读取 Chrome Cookie DB 失败: {e}")
            return []
        except Exception as e:
            logger.error(f"Chrome Cookie 提取异常: {e}")
            return []
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _read_cookies(self, db_path: str, domain: str) -> List[dict]:
        """从 SQLite DB 读取并解密 Cookie。"""
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        # 查询匹配域名的 Cookie
        host_key_pattern = f"%{domain}%"
        rows = conn.execute(
            "SELECT host_key, name, encrypted_value, path, expires_utc, "
            "is_secure, is_httponly "
            "FROM cookies "
            "WHERE host_key LIKE ?",
            (host_key_pattern,),
        ).fetchall()

        cookies = []
        for row in rows:
            encrypted = row["encrypted_value"]
            if not encrypted:
                continue

            decrypted = self._decrypt(encrypted)
            if decrypted is None:
                continue

            expires = None
            if row["expires_utc"]:
                try:
                    # Chrome 使用微秒时间戳（从 1601-01-01 开始）
                    chrome_epoch = 11644473600
                    ts = row["expires_utc"] / 1000000 - chrome_epoch
                    from datetime import datetime, timezone
                    expires = ts if ts > 0 else None
                except (ValueError, OverflowError):
                    pass

            # expires 必须是数字（Playwright 要求 float/int），None → -1
            if expires is None or not isinstance(expires, (int, float)):
                expires = -1

            cookies.append({
                "name": row["name"],
                "value": decrypted,
                "domain": row["host_key"],
                "path": row["path"] or "/",
                "expires": expires,
                "httpOnly": bool(row["is_httponly"]),
                "secure": bool(row["is_secure"]),
            })

        conn.close()
        logger.info(f"从 Chrome 提取了 {len(cookies)} 个 {domain} Cookie")
        return cookies

    def _decrypt(self, encrypted_value: bytes) -> Optional[str]:
        """解密 Chrome 的加密 Cookie 值。

        macOS: 使用 Keychain 中的密钥，AES-128-CBC 解密。
        Linux: 使用 PBKDF2 派生密钥（需要 keyring 密码）。
        Windows: 使用 DPAPI (cryptography 库自动处理)。
        """
        import sys

        if sys.platform == "darwin":
            return self._decrypt_macos(encrypted_value)
        elif sys.platform == "linux":
            return self._decrypt_linux(encrypted_value)
        elif sys.platform == "win32":
            return self._decrypt_windows(encrypted_value)
        else:
            logger.warning(f"不支持的平台: {sys.platform}")
            return None

    def _decrypt_macos(self, encrypted_value: bytes) -> Optional[str]:
        """macOS: 从 Keychain 获取 Chrome Safe Storage 密钥后 AES 解密。"""
        try:
            # 从 Keychain 获取密钥（service name = "Chrome Safe Storage"）
            result = subprocess.run(
                [
                    "security", "find-generic-password",
                    "-s", "Chrome Safe Storage",
                    "-wa", "Chrome",
                ],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                logger.warning(f"无法从 Keychain 获取 Chrome 密钥: {result.stderr.strip()}")
                return None

            key = result.stdout.strip().encode("utf-8")
            return self._aes_decrypt(encrypted_value, key)

        except FileNotFoundError:
            logger.warning("security 命令不可用（非 macOS 系统？）")
            return None
        except Exception as e:
            logger.error(f"macOS Keychain 解密失败: {e}")
            return None

    def _decrypt_linux(self, encrypted_value: bytes) -> Optional[str]:
        """Linux: 使用 PBKDF2 派生密钥解密。

        Chrome on Linux 使用固定密码 "peanuts" 或 keyring 密码。
        """
        try:
            from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
            from cryptography.hazmat.primitives import hashes

            # Linux Chrome 默认使用 "peanuts" 作为 keyring 密码
            # 或者从 GNOME keyring / KWallet 获取
            password = b"peanuts"
            salt = b"saltysalt"
            iterations = 1
            key_length = 16

            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA1(),
                length=key_length,
                salt=salt,
                iterations=iterations,
            )
            key = kdf.derive(password)
            return self._aes_decrypt(encrypted_value, key)

        except ImportError:
            logger.warning("Linux 解密需要 cryptography 库")
            return None
        except Exception as e:
            logger.error(f"Linux Cookie 解密失败: {e}")
            return None

    def _decrypt_windows(self, encrypted_value: bytes) -> Optional[str]:
        """Windows: 使用 DPAPI 解密。"""
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            import ctypes
            import ctypes.wintypes

            # Windows DPAPI 解密需要调用 CryptUnprotectData
            # 简化实现：跳过（主要面向 macOS 开发）
            logger.warning("Windows Chrome Cookie 解密暂未实现")
            return None
        except Exception as e:
            logger.error(f"Windows Cookie 解密失败: {e}")
            return None

    @staticmethod
    def _aes_decrypt(encrypted_value: bytes, key: bytes) -> Optional[str]:
        """解密 Chrome Cookie 值。

        Chrome 加密格式随版本变化：
        - v10/v11 前缀: AES-256-GCM，12 字节 nonce，末尾 16 字节 auth tag
        - 无前缀 (旧版): AES-128-CBC，16 字节 IV，PKCS7 填充
        """
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.backends import default_backend
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

            # v10/v11 格式: AES-GCM（密钥长度自适应）
            if encrypted_value.startswith(b"v10") or encrypted_value.startswith(b"v11"):
                data = encrypted_value[3:]  # 去除 "v10"/"v11"

                if len(data) < 28:  # 12 nonce + 至少 16 tag
                    return None

                nonce = data[:12]
                ciphertext = data[12:]

                # 尝试多种密钥派生方式（兼容不同 Chrome 版本）
                plaintext = None
                errors_list = []

                # 方式 1: 直接用 key (AES-128-GCM, Chrome 旧版 v10)
                try:
                    aesgcm = AESGCM(key)
                    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
                except Exception as e:
                    errors_list.append(f"direct: {e}")

                # 方式 2: PBKDF2 派生 256-bit key (AES-256-GCM)
                if plaintext is None:
                    try:
                        kdf = PBKDF2HMAC(
                            algorithm=hashes.SHA1(),
                            length=32,
                            salt=b"saltysalt",
                            iterations=1,
                            backend=default_backend(),
                        )
                        derived_key = kdf.derive(key)
                        aesgcm = AESGCM(derived_key)
                        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
                    except Exception as e:
                        errors_list.append(f"pbkdf2-256: {e}")

                # 方式 3: PBKDF2 派生 128-bit key
                if plaintext is None:
                    try:
                        kdf = PBKDF2HMAC(
                            algorithm=hashes.SHA1(),
                            length=16,
                            salt=b"saltysalt",
                            iterations=1,
                            backend=default_backend(),
                        )
                        derived_key = kdf.derive(key)
                        aesgcm = AESGCM(derived_key)
                        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
                    except Exception as e:
                        errors_list.append(f"pbkdf2-128: {e}")

                # 方式 4: PBKDF2 with iterations=1003 (Chrome 某些版本)
                if plaintext is None:
                    try:
                        kdf = PBKDF2HMAC(
                            algorithm=hashes.SHA1(),
                            length=16,
                            salt=b"saltysalt",
                            iterations=1003,
                            backend=default_backend(),
                        )
                        derived_key = kdf.derive(key)
                        aesgcm = AESGCM(derived_key)
                        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
                    except Exception as e:
                        errors_list.append(f"pbkdf2-1003: {e}")

                if plaintext is None:
                    logger.debug(f"AES-GCM 解密失败: {errors_list}")
                    return None

                return plaintext.decode("utf-8", errors="replace")

            # 旧格式: AES-128-CBC
            else:
                data = encrypted_value

                if len(data) < 16:
                    return None

                iv = data[:16]
                ciphertext = data[16:]

                cipher = Cipher(
                    algorithms.AES(key),
                    modes.CBC(iv),
                    backend=default_backend(),
                )
                decryptor = cipher.decryptor()
                plaintext = decryptor.update(ciphertext) + decryptor.finalize()

                # 去除 PKCS7 填充
                pad_len = plaintext[-1]
                if pad_len > 16:
                    return None
                plaintext = plaintext[:-pad_len]

                return plaintext.decode("utf-8", errors="replace")

        except ImportError:
            logger.warning("AES 解密需要 cryptography 库: pip install cryptography")
            return None
        except Exception as e:
            logger.debug(f"AES 解密失败: {e}")
            return None


# ============================================================================
# Cookie 管理器
# ============================================================================

class CookieManager:
    """Cookie 持久化管理器。

    支持两种加载方式（优先级从高到低）：
    1. load() — 从保存的 JSON 文件加载
    2. load_from_chrome() — 从本地 Chrome Cookie DB 导入
    """

    def __init__(
        self,
        platform: str = "taobao",
        cookie_dir: str = "data/cookies",
        expiry_days: int = 30,
        warn_days: int = 3,
    ):
        """
        参数:
            platform: 平台标识 (taobao/pdd)
            cookie_dir: Cookie 文件存储目录
            expiry_days: Cookie 有效期（天）
            warn_days: 提前多少天警告过期
        """
        self.platform = platform
        self.cookie_dir = Path(cookie_dir)
        self.cookie_file = self.cookie_dir / f"{platform}_cookies.json"
        self.expiry_days = expiry_days
        self.warn_days = warn_days

    # ------------------------------------------------------------------
    # 保存 / 加载
    # ------------------------------------------------------------------

    def save(self, cookies: List[dict]):
        """将 Playwright cookies 序列化到文件。

        参数:
            cookies: Playwright context.cookies() 返回的 Cookie 列表
        """
        self.cookie_dir.mkdir(parents=True, exist_ok=True)

        data = {
            "platform": self.platform,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "expiry_days": self.expiry_days,
            "cookies": cookies,
        }

        with open(self.cookie_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

        # 安全：Cookie 等同账号免密登录凭据，限制为仅属主可读写（0600）
        try:
            os.chmod(self.cookie_file, 0o600)
            os.chmod(self.cookie_dir, 0o700)
        except OSError as e:
            logger.warning(f"无法设置 Cookie 文件权限（{self.cookie_file}）: {e}")

        logger.info(f"已保存 {len(cookies)} 个 {self.platform} Cookie → {self.cookie_file}")

    def load(self) -> Optional[List[dict]]:
        """加载已保存的 Cookie。

        返回:
            Cookie 列表，如果文件不存在或格式错误返回 None
        """
        if not self.cookie_file.exists():
            logger.debug(f"Cookie 文件不存在: {self.cookie_file}")
            return None

        try:
            with open(self.cookie_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # 兼容两种格式：
            # - 新格式: {"cookies": [...], "saved_at": "..."}
            # - 旧格式: [...]（纯 cookie 列表）
            if isinstance(data, list):
                cookies = data
                saved_at = "unknown"
            else:
                cookies = data.get("cookies", [])
                saved_at = data.get("saved_at", "unknown")

            age_days = self._age_days(data.get("saved_at") if isinstance(data, dict) else None)
            logger.info(
                f"已加载 {len(cookies)} 个 {self.platform} Cookie "
                f"(保存于 {saved_at}，距今 {age_days:.1f} 天)"
            )

            if self.is_expired(data if isinstance(data, dict) else None):
                logger.warning(f"{self.platform} Cookie 已过期（{age_days:.1f} 天前保存）")
                return None

            return cookies

        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Cookie 文件解析失败: {e}")
            return None

    def load_from_chrome(self, domain: str = "taobao.com") -> Optional[List[dict]]:
        """从本地 Chrome 导入 Cookie 并持久化。

        首次调用时从 Chrome Cookie DB 提取指定域名的 Cookie，
        转换为 Playwright 格式后保存到 JSON 文件。
        之后调用 load() 即可从文件加载。

        参数:
            domain: 目标域名，默认 "taobao.com"

        返回:
            Playwright 兼容的 Cookie 列表。失败返回 None。
        """
        reader = ChromeCookieReader()
        raw_cookies = reader.extract(domain)

        if not raw_cookies:
            logger.warning(f"无法从 Chrome 提取 {domain} Cookie")
            return None

        # 转换为 Playwright 格式（适配域名和 sameSite）
        pw_cookies = []
        for c in raw_cookies:
            expires = c.get("expires")
            if expires is None or not isinstance(expires, (int, float)):
                expires = -1

            pw_cookies.append({
                "name": c["name"],
                "value": c["value"],
                "domain": c["domain"],
                "path": c.get("path", "/"),
                "expires": expires,
                "httpOnly": c.get("httpOnly", False),
                "secure": c.get("secure", False),
                "sameSite": "Lax",
            })

        # 持久化
        self.save(pw_cookies)
        return pw_cookies

    # ------------------------------------------------------------------
    # 过期检测
    # ------------------------------------------------------------------

    def is_expired(self, data: dict = None) -> bool:
        """检查 Cookie 是否已过期。

        参数:
            data: load() 返回的数据，为 None 时从文件读取

        返回:
            True 表示已过期或即将过期
        """
        if data is None:
            if not self.cookie_file.exists():
                return True
            try:
                with open(self.cookie_file, "r") as f:
                    data = json.load(f)
            except Exception:
                return True

        saved_at = data.get("saved_at") if isinstance(data, dict) else None
        age_days = self._age_days(saved_at)
        return age_days > (self.expiry_days - self.warn_days)

    def days_until_expiry(self) -> Optional[float]:
        """距离过期还有多少天。

        返回:
            剩余天数，-1 表示已过期，None 表示文件不存在
        """
        if not self.cookie_file.exists():
            return None
        try:
            with open(self.cookie_file, "r") as f:
                data = json.load(f)
            saved_at = data.get("saved_at") if isinstance(data, dict) else None
            age_days = self._age_days(saved_at)
            return self.expiry_days - age_days
        except Exception:
            return None

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _age_days(self, saved_at_str: Optional[str]) -> float:
        """计算 Cookie 从保存到现在的天数。"""
        if not saved_at_str:
            return float("inf")
        try:
            saved_at = datetime.fromisoformat(saved_at_str)
            now = datetime.now(timezone.utc)
            return (now - saved_at).total_seconds() / 86400
        except (ValueError, TypeError):
            return float("inf")
