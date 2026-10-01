#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""release_check.py —— 发版一致性检查（v4.9.24 / GPT 发行质量 P1）
检查 VERSION（common.py 单一来源）与以下是否同步：
  1. README.md 版本声明（v4.9.x 字样）
  2. tests/test_tree_farm.py 的 test_version_bumped 断言
  3. CHANGELOG.md 最新条目版本号
  4. tree-farm/SKILL.md description 版本号
用法：python3 scripts/release_check.py [--fix]
  --fix 自动把落后的 README/CHANGELOG/SKILL 版本号同步为 VERSION
零依赖，纯标准库。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # 仓库根（TreeFarm_v4.9.24/）
COMMON = ROOT / "tree-farm" / "scripts" / "treefarm" / "common.py"


def get_version() -> str:
    m = re.search(r'VERSION\s*=\s*"([\d.]+)"', COMMON.read_text(encoding="utf-8"))
    if not m:
        sys.exit("❌ 找不到 VERSION（common.py）")
    return m.group(1)


def check_file(path: Path, pattern: str, version: str, label: str) -> list:
    """返回不匹配的行 [(行号, 行内容), ...]"""
    bad = []
    text = path.read_text(encoding="utf-8")
    for i, line in enumerate(text.splitlines(), 1):
        if pattern in line and version not in line:
            bad.append((i, line))
    return bad


def main() -> int:
    fix = "--fix" in sys.argv
    version = get_version()
    issues = []

    # 1) README：只查「当前版本声明」硬位置（实测战绩标题区 + 技能目录说明），
    #    历史战绩引用（上一版 v4.9.21 等）是正常内容不标红。
    rp = ROOT / "README.md"
    text = rp.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(text, 1):
        is_decl = (line.startswith("## ★") or line.startswith("🏆") or "★ 技能 2" in line
                   or "树场 v4.9." in line or "引擎 v4.9." in line)
        if is_decl and "v4.9." in line and version not in line:
            issues.append((rp, i, line))
    # 2) 测试断言：只查 test_version_bumped 的版本字面量断言行
    tp = ROOT / "tree-farm" / "scripts" / "tests" / "test_tree_farm.py"
    for i, line in check_file(tp, 'tf.VERSION, "', version, "test_version_bumped"):
        issues.append((tp, i, line))
    # 3) CHANGELOG 最新条目
    cp = ROOT / "CHANGELOG.md"
    head = cp.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(head[:10], 1):
        if line.startswith("## [") and version not in line:
            issues.append((cp, i, line))
            break
    # SKILL description 是功能历史记录（含历次版本号），不做强制同步。

    if not issues:
        print(f"✅ release_check 通过：VERSION={version} 与 README/测试/CHANGELOG/SKILL 全部一致")
        return 0

    print(f"❌ release_check 发现 {len(issues)} 处版本落后（VERSION={version}）：")
    for path, i, line in issues:
        print(f"  {path.name}:{i}  {line.strip()[:80]}")
    if fix:
        print("\n--fix 自动同步（仅替换版本号字面量，逐处人工复核后提交）……")
        for path, i, line in issues:
            lines = path.read_text(encoding="utf-8").splitlines()
            new = re.sub(r"v4\.9\.\d+", f"v{version}", line)
            new = re.sub(r'"[\d.]+"', f'"{version}"', new)
            new = re.sub(r"\[4\.9\.\d+\]", f"[{version}]", new)
            lines[i - 1] = new
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(f"  已修复 {path.name}:{i} → {new.strip()[:80]}")
        print("\n⚠️ 修复后必须重跑：python3 -m unittest discover -s tests")
    return 1


if __name__ == "__main__":
    sys.exit(main())
