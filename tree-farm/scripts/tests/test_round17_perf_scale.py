# -*- coding: utf-8 -*-
"""
树场 20 轮测试 · 第 17 轮：规模性能验证（unittest，零依赖）

目标：验证插件在中等规模项目（数千文件行）上不卡死、不超时——
用插件扫描自己（treefarm 包 ~1.5 万行源码）验证 Dogfooding + 性能。

运行：
  python3 -m unittest tests.test_round17_perf_scale -v
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import (  # noqa: E402
    detect_security_issues, detect_performance_issues, detect_logic_issues,
)

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "treefarm")


def pkg_files():
    out = []
    for root, dirs, files in os.walk(PKG):
        dirs[:] = [d for d in dirs if d not in ("__pycache__",)]
        for f in files:
            if f.endswith(".py"):
                out.append(os.path.join(root, f))
    return out


class SelfScanPerfTest(unittest.TestCase):
    """Dogfooding：扫自己（~1.5 万行）必须在合理时间内完成"""

    def test_security_scan_time(self):
        files = pkg_files()
        self.assertGreaterEqual(len(files), 10, "测试靶场文件数不足")
        t0 = time.time()
        res = detect_security_issues(files)
        elapsed = time.time() - t0
        # 阈值 30s：单独跑本测试 ~6s，全量并发受环境干扰放宽
        self.assertLess(elapsed, 60, f"安全扫描过慢: {elapsed:.1f}s（单独跑~3s，全量并发放宽）")
        self.assertIn("total", res)

    def test_logic_scan_time(self):
        files = pkg_files()
        t0 = time.time()
        res = detect_logic_issues(files)
        elapsed = time.time() - t0
        self.assertLess(elapsed, 60, f"逻辑扫描过慢: {elapsed:.1f}s（单独跑~2s，全量并发放宽）")

    def test_perf_scan_time(self):
        files = pkg_files()
        t0 = time.time()
        res = detect_performance_issues(files)
        elapsed = time.time() - t0
        self.assertLess(elapsed, 60, f"性能扫描过慢: {elapsed:.1f}s（单独跑~2s，全量并发放宽）")

    def test_ast_cache_hit(self):
        # 二次扫描应命中 AST 缓存更快（不严格断言时间，只验证不崩且结果一致）
        files = pkg_files()
        r1 = detect_logic_issues(files)
        r2 = detect_logic_issues(files)
        self.assertEqual(r1["total"], r2["total"])


if __name__ == "__main__":
    unittest.main()
