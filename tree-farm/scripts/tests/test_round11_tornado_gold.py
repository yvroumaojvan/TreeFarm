# -*- coding: utf-8 -*-
"""
树场 20 轮测试 · 第 11 轮：tornado 金标准回归（BugsInPy，unittest）

靶场：/storage/emulated/0/豆包的工作区/benchmark_bugs/buggy_1~7
（BugsInPy tornado 7 个真实 bug 的植入文件）

战绩基线（r11 实测）：
  结构型命中 5/7：bug1 API契约(high) / bug3 逻辑(中) / bug5 逻辑(低) /
                  bug6 逻辑(低) / bug7 逻辑(中)
  语义型 2/7（bug2 HTTP chunked 条件 / bug4 Range 边界）需思维树兜底——诚实预期

运行：
  python3 -m unittest tests.test_round11_tornado_gold -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tree_farm as tf  # noqa: E402

BENCH_DIR = "/storage/emulated/0/豆包的工作区/benchmark_bugs"


def has_benchmark():
    return os.path.isdir(BENCH_DIR) and any(
        os.path.isdir(os.path.join(BENCH_DIR, f"buggy_{i}")) for i in range(1, 8))


@unittest.skipUnless(has_benchmark(), "benchmark_bugs 靶场不存在，跳过")
class TornadoGoldStandardTest(unittest.TestCase):
    def _logic_issues(self, bug_no):
        d = os.path.join(BENCH_DIR, f"buggy_{bug_no}")
        files = [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".py")]
        return tf.detect_logic_issues(files)["issues"]

    def test_bug1_api_contract(self):
        # tornado#1：set_nodelay 委托 self.stream 但同类委托 self.ws_connection
        types = [i["type"] for i in self._logic_issues(1)]
        self.assertIn("API契约", types, f"bug1 契约检测未命中: {types}")

    def test_bug3_logic_hit(self):
        self.assertTrue(len(self._logic_issues(3)) >= 1, "bug3 应有逻辑命中")

    def test_bug5_logic_hit(self):
        self.assertTrue(len(self._logic_issues(5)) >= 1, "bug5 应有逻辑命中")

    def test_bug6_logic_hit(self):
        self.assertTrue(len(self._logic_issues(6)) >= 1, "bug6 应有逻辑命中")

    def test_bug7_logic_hit(self):
        self.assertTrue(len(self._logic_issues(7)) >= 1, "bug7 应有逻辑命中")

    def test_bug1_fix_suggestion(self):
        # 契约命中必须带修复建议
        issues = self._logic_issues(1)
        contract = [i for i in issues if i["type"] == "API契约"]
        self.assertTrue(contract and contract[0].get("fix"),
                        f"bug1 缺修复建议: {contract}")

    def test_bug2_semantic_hit(self):
        # tornado#2（50轮R1 新增能力）：Transfer-Encoding chunked 判定语义缺陷
        types = [i["type"] for i in self._logic_issues(2)]
        self.assertIn("疑似语义缺陷", types, f"bug2 语义检测未命中: {types}")

    def test_bug4_semantic_hit(self):
        # tornado#4（50轮R1 新增能力）：Range 负偏移/start>=end 语义缺陷
        types = [i["type"] for i in self._logic_issues(4)]
        self.assertIn("疑似语义缺陷", types, f"bug4 语义检测未命中: {types}")


if __name__ == "__main__":
    unittest.main()
