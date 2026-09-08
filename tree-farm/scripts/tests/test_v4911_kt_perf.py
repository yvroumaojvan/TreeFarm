# -*- coding: utf-8 -*-
"""
树场 v4.9.11 测试套件（第 8 轮，unittest，零依赖）

覆盖 3DGS 实战暴露的三大漏报修复：
  1. Kotlin 支持（.kt 进 CODE_EXTS / 符号提取 / 基因提取）
  2. HTML 内联 JS 参与性能检测（_extract_inline_js + #script 命中）
  3. 复杂度嗅探（嵌套循环 + 集合访问 → 疑似 O(n²)；单层循环不报；嵌套组去重）
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import (detect_performance_issues,  # noqa: E402
                               _extract_inline_js, _scan_text_perf_issues)
from treefarm.common import CODE_EXTS, classify  # noqa: E402
from treefarm.parser import _kotlin_defs, _strip_kotlin_noise  # noqa: E402


def perf_issues(content, suffix=".java"):
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="treefarm_v4911_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    try:
        return detect_performance_issues([path])["issues"]
    finally:
        os.unlink(path)


class KotlinSupportTest(unittest.TestCase):
    def test_kt_in_code_exts(self):
        self.assertIn(".kt", CODE_EXTS)
        self.assertEqual(classify("a/b/Foo.kt"), "tree")

    def test_kotlin_defs_fun_and_class(self):
        text = """
package com.demo

class Foo {
    fun process(a: Int): Int {
        return a + 1
    }
}

fun helper(name: String): String = name.trim()

val onDone = { x: Int -> x * 2 }

object Registry {
    fun get() = 1
}
"""
        defs = _kotlin_defs(_strip_kotlin_noise(text))
        names = [d[0] for d in defs]
        self.assertIn("Foo", names)
        self.assertIn("process", names)
        self.assertIn("helper", names)
        self.assertIn("onDone", names)
        self.assertIn("Registry", names)
        self.assertIn("get", names)


class ComplexitySniffTest(unittest.TestCase):
    def test_nested_loop_collection_reported(self):
        issues = perf_issues('''\
class Grid {
    void filter(List<Point> pts) {
        for (int i = 0; i < pts.size(); i++) {
            for (int j = 0; j < pts.size(); j++) {
                float d = pts.get(j).dist(pts.get(i));
            }
        }
    }
}
''')
        types = {i["type"] for i in issues}
        self.assertIn("疑似O(n²)嵌套循环+集合访问", types, issues)

    def test_single_loop_not_reported(self):
        issues = perf_issues('''\
class Plain {
    void sum(List<Integer> xs) {
        int s = 0;
        for (int i = 0; i < xs.size(); i++) {
            s += xs.get(i);
        }
    }
}
''')
        self.assertEqual([], issues, issues)

    def test_nested_group_dedup_single_issue(self):
        # 3DGS 离群过滤形态：外层 for + dx/dy/dz 三重固定循环 + 桶内 for
        issues = perf_issues('''\
class Out {
    void filter(List<G> gs) {
        for (int i = 0; i < gs.size(); i++) {
            for (int dx = -1; dx <= 1; dx++) {
                for (int dy = -1; dy <= 1; dy++) {
                    for (int dz = -1; dz <= 1; dz++) {
                        for (int j : bucket) {
                            float d = gs.get(j).x - gs.get(i).x;
                        }
                    }
                }
            }
        }
    }
}
''')
        # 同一嵌套组只报最外层 1 条
        self.assertEqual(1, len(issues), issues)
        self.assertEqual("疑似O(n²)嵌套循环+集合访问", issues[0]["type"])


class HtmlInlineJsTest(unittest.TestCase):
    def test_extract_inline_js(self):
        js = _extract_inline_js([
            "<html><body>",
            "<script>var a = 1;</script>",
            "<script src=\"x.js\"></script>",
            "<script>function f(){ return a; }</script>",
            "</body></html>",
        ])
        self.assertEqual(2, len(js))  # 只提取内联，跳过 src 外链
        self.assertIn("var a = 1;", js[0])

    def test_html_inline_js_nested_loop_reported(self):
        issues = perf_issues('''\
<!DOCTYPE html>
<html>
<body>
<script>
function load(total) {
  var pos = new Float32Array(total * 3);
  for (var i = 0; i < total; i++) {
    for (var j = 0; j < total; j++) {
      pos[i * 3] = j * 1.0;
    }
  }
}
</script>
</body>
</html>
''', suffix=".html")
        self.assertTrue(any("#script" in i["file"] for i in issues),
                        "HTML 内联 JS 应参与检测: " + str(issues))
        types = {i["type"] for i in issues}
        self.assertIn("疑似O(n²)嵌套循环+集合访问", types, issues)


class ScanTextPerfSanityTest(unittest.TestCase):
    def test_scan_text_perf_issues_direct(self):
        lines = [
            "for (int i = 0; i < n; i++) {",
            "    for (int j = 0; j < n; j++) {",
            "        arr[i] = arr[j] + 1;",
            "    }",
            "}",
            "return;",
        ]
        issues = _scan_text_perf_issues(lines, "fake.java")
        self.assertEqual(1, len(issues), issues)


if __name__ == "__main__":
    unittest.main()
