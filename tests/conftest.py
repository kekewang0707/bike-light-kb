"""pytest 全局配置。

在导入任何项目模块之前注入测试用环境变量，
避免 config/settings.py 因 BKL_DB_PASSWORD 缺失而抛 RuntimeError。
"""
import os

os.environ.setdefault("BKL_DB_PASSWORD", "test_password_for_unit_tests")
os.environ.setdefault("BKL_DB_USER", "bikelight")
os.environ.setdefault("BKL_DB_HOST", "localhost")
os.environ.setdefault("BKL_DB_PORT", "5433")
os.environ.setdefault("BKL_DB_NAME", "bikelight_kb_test")
