# -*- coding: utf-8 -*-
"""
树场 20 轮测试 · 第 8 轮：结构分析（死代码/循环依赖/复杂度/架构/技术债）（unittest）

目标：测结构分析能力（detect_dead_code / detect_circular_dependencies /
calculate_complexity_any / detect_architecture_layers / calculate_debt /
detect_duplicates）的短板。

运行：
  python3 -m unittest tests.test_round8_structure -v
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tree_farm as tf  # noqa: E402


def make_project(files: dict) -> str:
    d = tempfile.mkdtemp(prefix="treefarm_r8_")
    for name, content in files.items():
        p = os.path.join(d, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
    return d


def tree_files_of(d: str):
    return [os.path.join(d, f) for f in os.listdir(d) if f.endswith((".py", ".js"))]


class DeadCodeTest(unittest.TestCase):
    def test_unused_function(self):
        d = make_project({"app.py": '''\
def used():
    return 1


def never_called():
    return 2


def main():
    return used()


if __name__ == "__main__":
    main()
'''})
        try:
            res = tf.detect_dead_code(tree_files_of(d))
            dead = {f["name"] for f in res["dead_functions"]}
            self.assertIn("never_called", dead, f"死函数漏报: {dead}")
            self.assertNotIn("used", dead, f"活函数误报: {dead}")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_unused_class(self):
        d = make_project({"app.py": '''\
class Used:
    pass


class NeverUsed:
    pass


def main():
    u = Used()
    return u


main()
'''})
        try:
            res = tf.detect_dead_code(tree_files_of(d))
            dead_classes = {c["name"] for c in res["dead_classes"]}
            self.assertIn("NeverUsed", dead_classes, f"死类漏报: {dead_classes}")
            self.assertNotIn("Used", dead_classes, f"活类误报: {dead_classes}")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_js_dead_function(self):
        d = make_project({"app.js": '''\
function used() {
    return 1;
}

function neverCalled() {
    return 2;
}

console.log(used());
'''})
        try:
            res = tf.detect_dead_code(tree_files_of(d))
            dead = {f["name"] for f in res["dead_functions"]}
            self.assertIn("neverCalled", dead, f"JS 死函数漏报: {dead}")
            self.assertNotIn("used", dead, f"JS 活函数误报: {dead}")
        finally:
            shutil.rmtree(d, ignore_errors=True)


class CircularDepsIntegrationTest(unittest.TestCase):
    def test_direct_cycle_cli(self):
        # 集成：主类跑 circular-deps 不崩且能报环
        d = make_project({
            "a.py": "import b\n\ndef fa():\n    return 1\n",
            "b.py": "import a\n\ndef fb():\n    return 2\n",
        })
        try:
            tf_inst = tf.TreeFarm(d)
            tf_inst.plant()
            out = tf_inst.circular_deps()
            # 输出用模块名（a → b → a），不带 .py 后缀
            self.assertTrue("发现 1 个循环依赖" in out or "a  → b  → a" in out,
                            f"循环依赖未报: {out[:200]}")
        finally:
            shutil.rmtree(d, ignore_errors=True)


class ComplexityTest(unittest.TestCase):
    def test_complexity_values(self):
        d = make_project({"app.py": '''\
def simple():
    return 1


def complex_fn(x):
    if x > 0:
        if x > 10:
            return "big"
        return "mid"
    return "small"
'''})
        try:
            res = tf.calculate_complexity_any(os.path.join(d, "app.py"))
            # functions 是 (name, complexity, lineno, kind) 元组列表
            funcs = {f[0]: f[1] for f in res["functions"]}
            self.assertLessEqual(funcs["simple"], 2, f"simple 复杂度异常: {funcs}")
            self.assertGreaterEqual(funcs["complex_fn"], 3, f"complex_fn 复杂度异常: {funcs}")
        finally:
            shutil.rmtree(d, ignore_errors=True)


class ArchitectureLayersTest(unittest.TestCase):
    def test_layer_detection(self):
        d = make_project({
            "routes.py": "from flask import Blueprint\nbp = Blueprint('main', __name__)\n",
            "models.py": "from sqlalchemy import Column\nclass User(Base):\n    id = Column(Integer, primary_key=True)\n",
            "utils.py": "import json\ndef load_config():\n    return json.load(open('config.json'))\n",
        })
        try:
            res = tf.detect_architecture_layers(tree_files_of(d), None, {})
            layers = res.get("layers", {})
            self.assertTrue(len(layers) > 0, f"架构分层为空: {layers}")
        finally:
            shutil.rmtree(d, ignore_errors=True)


class DebtIntegrationTest(unittest.TestCase):
    def test_debt_report_cli(self):
        d = make_project({"app.py": '''\
def dead_one():
    return 1


def dead_two():
    return 2


def main():
    return 3


main()
'''})
        try:
            tf_inst = tf.TreeFarm(d)
            tf_inst.plant()
            out = tf_inst.debt()
            self.assertTrue("技术债务" in out or "总评" in out or "grade" in out.lower(),
                            f"技术债输出异常: {out[:200]}")
        finally:
            shutil.rmtree(d, ignore_errors=True)


class DuplicateTest(unittest.TestCase):
    def test_duplicate_files(self):
        d = make_project({
            "a.py": "def process(data):\n    out = []\n    for item in data:\n        out.append(item * 2)\n    return out\n",
            "b.py": "def process(data):\n    out = []\n    for item in data:\n        out.append(item * 2)\n    return out\n",
        })
        try:
            pairs, n = tf.detect_duplicates(tree_files_of(d), threshold=0.8)
            self.assertTrue(len(pairs) >= 1, f"重复文件漏报: {pairs}")
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
