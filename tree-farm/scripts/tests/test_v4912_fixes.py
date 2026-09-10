# -*- coding: utf-8 -*-
"""
树场 v4.9.12 第1轮自测修复回归测试套件（unittest，零依赖）

覆盖（v4.9.11 金标准测试驱动，三大实锤修复）：
  1. Python 嵌套循环复杂度嗅探缺口（v4.9.11 复杂度嗅探只覆盖文本语言
     Kotlin/JS/C，Python AST 路径漏掉）：
     - 嵌套循环 + 下标访问 → 报「疑似O(n²)嵌套循环+集合访问」
     - 深度 3 也只报最外层一条（去重）
     - 单层循环 + 下标 → 零误报
     - 嵌套但无下标/集合方法 → 零误报
  2. C: strcpy 第二参是 #define 宏名 → 不报（v4.9.11 误报实锤：
     strcpy(dst, MAX_PATH) 被报缓冲区溢出）
     - strcpy(dst, src_var) 动态 → 仍报
  3. C: 硬编码密钥变量名集合扩展（char *secret = 长串 → 报；
     短值 <8 字符 → 不报）
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_performance_issues, detect_security_issues  # noqa: E402


def py_perf(content):
    fd, path = tempfile.mkstemp(suffix=".py", prefix="tf_v4912perf_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    try:
        return detect_performance_issues([path], root=os.path.dirname(path))["issues"]
    finally:
        os.unlink(path)


def c_issues(content):
    fd, path = tempfile.mkstemp(suffix=".c", prefix="tf_v4912c_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    try:
        return detect_security_issues([path])["issues"]
    finally:
        os.unlink(path)


def types_of(issues, t):
    return [i for i in issues if i["type"] == t]


class PythonNestedLoopSniffTest(unittest.TestCase):
    """修复1：Python AST 路径补嵌套循环复杂度嗅探。"""

    def test_nested_loop_index_reported(self):
        issues = py_perf('''\
def process(items):
    total = 0
    for i in range(len(items)):
        for j in range(len(items)):
            total += items[i] * items[j]
    return total
''')
        hits = types_of(issues, "疑似O(n²)嵌套循环+集合访问")
        self.assertEqual(len(hits), 1, issues)
        self.assertEqual(hits[0]["severity"], "medium", hits[0])

    def test_deep_nested_only_outer_reported(self):
        # 深度3：只报最外层一条（去重）
        issues = py_perf('''\
def process(a):
    total = 0
    for i in range(len(a)):
        for j in range(len(a)):
            for k in range(len(a)):
                total += a[i] + a[j] + a[k]
    return total
''')
        self.assertEqual(len(types_of(issues, "疑似O(n²)嵌套循环+集合访问")), 1, issues)

    def test_single_loop_index_no_false_positive(self):
        issues = py_perf('''\
def walk(a):
    total = 0
    for i in range(len(a)):
        total += a[i]
    return total
''')
        self.assertEqual([], types_of(issues, "疑似O(n²)嵌套循环+集合访问"), issues)

    def test_nested_loop_without_access_no_false_positive(self):
        # 嵌套但无下标/集合方法：迭代变量直接使用 → 不报
        issues = py_perf('''\
def prod(xs, ys):
    total = 0
    for x in xs:
        for y in ys:
            total += x * y
    return total
''')
        self.assertEqual([], types_of(issues, "疑似O(n²)嵌套循环+集合访问"), issues)

    def test_dict_values_method_not_reported_too_noisy(self):
        # 只有常量下标/普通方法调用（非集合查找）→ 不算集合访问
        issues = py_perf('''\
def render(data):
    out = []
    for i in range(len(data)):
        for j in range(len(data)):
            out.append(data[i].upper())
    return out
''')
        hits = types_of(issues, "疑似O(n²)嵌套循环+集合访问")
        self.assertEqual(len(hits), 1, issues)  # data[i] 下标命中，upper() 不算


class PythonCompSniffTest(unittest.TestCase):
    """第2轮：推导式（ListComp/SetComp/DictComp/GeneratorExp）纳入迭代上下文。"""

    def test_double_listcomp_reported(self):
        issues = py_perf('''\
def grid(m, n):
    return [[a * m[i] + b * n[j] for j in range(len(n))] for i in range(len(m))]
''')
        hits = types_of(issues, "疑似O(n²)嵌套循环+集合访问")
        self.assertEqual(len(hits), 1, issues)

    def test_single_listcomp_no_false_positive(self):
        issues = py_perf('''\
def flatten(rows):
    return [x for row in rows for x in row]
''')
        self.assertEqual([], types_of(issues, "疑似O(n²)嵌套循环+集合访问"), issues)

    def test_listcomp_in_for_only_outer_reported(self):
        # for 循环体内嵌 listcomp（有下标访问）：只报 for 最外层 1 条
        issues = py_perf('''\
def proc(a):
    out = []
    for i in range(len(a)):
        out += [a[i] * k for k in range(len(a))]
    return out
''')
        self.assertEqual(len(types_of(issues, "疑似O(n²)嵌套循环+集合访问")), 1, issues)

    def test_dictcomp_nested_reported(self):
        issues = py_perf('''\
def index(m):
    return {i: [j for j in range(len(m)) if m[j]] for i in range(len(m))}
''')
        self.assertEqual(len(types_of(issues, "疑似O(n²)嵌套循环+集合访问")), 1, issues)


class CStrcpyMacroConstantTest(unittest.TestCase):
    """修复2：strcpy 第二参是 #define 宏名 → 视为常量不报。"""

    def test_strcpy_macro_not_reported(self):
        issues = c_issues('''\
#include <string.h>
#define MAX_PATH "/data/tmp"
void init(char *dst) {
    strcpy(dst, MAX_PATH);
}
''')
        self.assertFalse(any("strcpy" in i["desc"] for i in issues), issues)

    def test_strcpy_dynamic_still_reported(self):
        issues = c_issues('''\
#include <string.h>
void copy(char *dst, const char *src) {
    strcpy(dst, src);
}
''')
        self.assertTrue(any("strcpy" in i["desc"] for i in issues), issues)


class CHardcodedSecretTest(unittest.TestCase):
    """修复3：硬编码密钥变量名集合扩展（v4.9.11 只认 api_key/password 等）。"""

    def test_long_secret_reported(self):
        issues = c_issues('''\
const char *secret = "A1B2C3D4E5F60718293A4B5C6D7E8F901";
int main(void) { return 0; }
''')
        self.assertTrue(any(i["type"] == "硬编码凭据" for i in issues), issues)

    def test_short_value_not_reported(self):
        issues = c_issues('''\
const char *secret = "abc";
int main(void) { return 0; }
''')
        self.assertFalse(any(i["type"] == "硬编码凭据" for i in issues), issues)

    def test_original_api_key_still_reported(self):
        # 原规则回归：api_key + sk- 前缀仍报
        issues = c_issues('''\
const char *api_key = "sk-abcdefghijklmnop1234567890";
int main(void) { return 0; }
''')
        self.assertTrue(any(i["type"] == "硬编码凭据" for i in issues), issues)


if __name__ == "__main__":
    unittest.main()