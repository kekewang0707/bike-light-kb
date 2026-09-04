#!/usr/bin/env python3
"""生成依赖锁定文件 requirements.lock.txt。

用途
----
`requirements.txt` 只声明带上下限的**直接依赖**；`requirements.lock.txt` 记录
整个环境（含传递依赖）的**精确版本**，用于可复现部署。

用法（必须在已装好依赖的 venv 中执行）::

    venv/bin/python scripts/gen_requirements_lock.py            # 生成 lock
    venv/bin/python scripts/gen_requirements_lock.py --check    # 只校验是否满足约束

--check 会逐个比对 lock 中的版本是否落在 requirements.txt 声明的区间内，
返回非 0 表示「lock 已过期，需要重新生成」，适合放进 CI。
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "requirements.txt"
LOCK = ROOT / "requirements.lock.txt"

# 环境与打包工具不进 lock
EXCLUDED = {"pip", "setuptools", "wheel", "distribute", "pkg_resources", "pkg-resources"}

# 行首：包名 + 后续的版本约束表达式
_NAME_RE = re.compile(r"^([A-Za-z0-9._-]+)\s*(.*)$")
# 约束：">=1.0" / "<2.0" / "~=6.2.2" 等（逗号分隔的多段都在这里展开）
_SPEC_RE = re.compile(r"(>=|<=|==|~=|!=|>|<)\s*([0-9][0-9A-Za-z.*+]*)")


def parse_requirements(path: Path):
    """解析 requirements.txt，返回 {包名: [(操作符, 版本), ...]}。

    支持一行多个约束（``pkg>=1.0,<2.0``）与行内注释。
    """
    constraints = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        m = _NAME_RE.match(line)
        if not m:
            continue
        key = m.group(1).lower().replace("_", "-")
        specs = _SPEC_RE.findall(m.group(2))
        if specs:
            constraints.setdefault(key, []).extend(specs)
    return constraints


def read_lock(path: Path):
    """解析 lock 文件，返回 {规范化包名: 版本字符串}。"""
    locked = {}
    if not path.exists():
        return locked
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        name, version = line.split("==", 1)
        locked[name.lower().replace("_", "-")] = version.strip()
    return locked


def freeze() -> dict:
    """执行 pip freeze，返回 {规范化包名: 版本}（排除打包工具）。"""
    out = subprocess.run(
        [sys.executable, "-m", "pip", "freeze", "--exclude-editable"],
        capture_output=True, text=True, check=True,
    ).stdout
    result = {}
    for line in out.splitlines():
        line = line.strip()
        if "==" not in line:
            continue
        name, version = line.split("==", 1)
        if name.lower() in EXCLUDED:
            continue
        result[name.lower().replace("_", "-")] = version.strip()
    return result


def _version_tuple(version: str):
    """把 '2.0.51' 转成可比较的 (2, 0, 51)，非数字段按 0 处理。"""
    parts = []
    for chunk in version.split("."):
        m = re.match(r"^(\d+)", chunk)
        parts.append(int(m.group(1)) if m else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def satisfies(version: str, op: str, bound: str) -> bool:
    """判断 version 是否满足单个约束（仅支持 ==/!=/>=/<=/>/<，~= 按兼容发布处理）。"""
    v, b = _version_tuple(version), _version_tuple(bound)
    if op == "==":
        return v == b
    if op == "!=":
        return v != b
    if op == ">=":
        return v >= b
    if op == "<=":
        return v <= b
    if op == ">":
        return v > b
    if op == "<":
        return v < b
    if op == "~=":
        # ~=X.Y.Z 等价于 >=X.Y.Z, <X.(Y+1).0
        upper = (b[0], b[1] + 1, 0)
        return b <= v < upper
    return True


def check() -> int:
    """校验 lock 中的版本是否全部满足 requirements.txt 的区间约束。"""
    constraints = parse_requirements(REQUIREMENTS)
    locked = read_lock(LOCK)
    if not locked:
        print("❌ requirements.lock.txt 不存在或为空，请先执行生成")
        return 1

    problems = []
    for name, specs in constraints.items():
        if name not in locked:
            problems.append(f"  · {name}: lock 中缺失")
            continue
        for op, bound in specs:
            if not satisfies(locked[name], op, bound):
                problems.append(
                    f"  · {name}=={locked[name]} 不满足 {op}{bound}"
                )

    if problems:
        print(f"❌ lock 与 requirements.txt 不一致（{len(problems)} 项）：")
        print("\n".join(problems))
        print("\n处理：重新生成 lock —— venv/bin/python scripts/gen_requirements_lock.py")
        return 1

    print(f"✅ lock 校验通过：{len(locked)} 个包，全部满足 requirements.txt 约束")
    return 0


def generate() -> int:
    packages = freeze()
    lines = "\n".join(f"{n}=={v}" for n, v in sorted(packages.items()))
    header = (
        "# 自行车灯电商知识库 — 依赖锁定文件（精确版本，含传递依赖）\n"
        "#\n"
        "# 由 scripts/gen_requirements_lock.py 自动生成，请勿手工编辑。\n"
        "#\n"
        "# 重新生成：\n"
        "#   python -m venv venv\n"
        "#   venv/bin/pip install -r requirements.txt\n"
        "#   venv/bin/python scripts/gen_requirements_lock.py\n"
        "#\n"
        "# 校验（CI 用）：\n"
        "#   venv/bin/python scripts/gen_requirements_lock.py --check\n"
        "#\n"
        f"# 共 {len(packages)} 个包\n\n"
    )
    LOCK.write_text(header + lines + "\n", encoding="utf-8")
    print(f"✅ 已生成 {LOCK.relative_to(ROOT)}：{len(packages)} 个包")
    return check()


def main() -> int:
    parser = argparse.ArgumentParser(description="生成/校验 requirements.lock.txt")
    parser.add_argument(
        "--check", action="store_true", help="只校验 lock 是否满足约束，不写入"
    )
    args = parser.parse_args()
    return check() if args.check else generate()


if __name__ == "__main__":
    raise SystemExit(main())
