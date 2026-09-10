# -*- coding: utf-8 -*-
"""
树场 v4.9.14 第3轮自测回归测试套件（unittest，零依赖）

覆盖（第3轮主题：ReDoS 检测失效根因修复）：
  根因：v4.9.2 加的「跳过所有含 r" 的行」一刀切——Python 正则几乎全带 raw 前缀
  （re.compile(r"...")），导致定义形态 ReDoS 检测（v4.1/v4.2 的 6.7/6.12 规则）
  从未真正生效。修复：跳过范围收窄为「规则库 pattern 键」形态（pattern: r"…"），
  普通 raw 字符串行恢复正常检测。

  - re.compile(r"(a+)+") / re.match(r"(a+)+") → 必报（嵌套量词灾难性回溯）
  - def_regex.py 原样（r"^(a+)+$" + pattern.match）→ 必报
  - 安全对照 r"^[a-z]+$" → 零误报
  - 非捕获组 (?:ab)* → 零误报
  - 规则库形态 {"pattern": r"os\\.system(...)"} → 跳过（防工具自我误报）
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402


def py_sec(content):
    fd, path = tempfile.mkstemp(suffix=".py", prefix="tf_v4914_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    try:
        return detect_security_issues([path], root=os.path.dirname(path))["issues"]
    finally:
        os.unlink(path)


def redos_of(issues):
    return [i for i in issues if i["type"] == "ReDoS"]


class RedosDefinitionTest(unittest.TestCase):
    """定义形态 ReDoS（raw 字符串正则）应恢复命中。"""

    def test_compile_nested_quant_reported(self):
        issues = py_sec('''\
import re
x = re.compile(r"(a+)+")
''')
        self.assertEqual(len(redos_of(issues)), 1, issues)

    def test_match_nested_quant_reported(self):
        issues = py_sec('''\
import re
re.match(r"(a+)+", user_input)
''')
        self.assertEqual(len(redos_of(issues)), 1, issues)

    def test_def_regex_full_shape_reported(self):
        # 金标准第1轮考场4.2 原样：定义形态 + 后续 .match 使用
        issues = py_sec('''\
import re
pattern = re.compile(r"^(a+)+$")
def validate(input_str: str) -> bool:
    return bool(pattern.match(input_str))
''')
        self.assertEqual(len(redos_of(issues)), 1, issues)

    def test_sub_nested_quant_reported(self):
        # v4.9.16 覆盖补全：re.sub 同样吃灾难性回溯 pattern
        issues = py_sec('''\
import re
out = re.sub(r"(a+)+", "x", text)
''')
        self.assertEqual(len(redos_of(issues)), 1, issues)

    def test_finditer_nested_quant_reported(self):
        issues = py_sec('''\
import re
for m in re.finditer(r"(a+)+", text):
    pass
''')
        self.assertEqual(len(redos_of(issues)), 1, issues)

    def test_split_nested_quant_reported(self):
        issues = py_sec('''\
import re
parts = re.split(r"(a+)+", text)
''')
        self.assertEqual(len(redos_of(issues)), 1, issues)

    def test_sub_safe_pattern_not_reported(self):
        issues = py_sec('''\
import re
out = re.sub(r"^[a-z]+$", "x", text)
''')
        self.assertEqual([], redos_of(issues), issues)


class RedosNoFalsePositiveTest(unittest.TestCase):
    """安全正则 / 规则库形态应零误报。"""

    def test_safe_pattern_not_reported(self):
        issues = py_sec('''\
import re
p = re.compile(r"^[a-z]+$")
''')
        self.assertEqual([], redos_of(issues), issues)

    def test_non_capture_group_not_reported(self):
        issues = py_sec('''\
import re
p = re.compile(r"(?:ab)*")
''')
        self.assertEqual([], redos_of(issues), issues)

    def test_rule_library_shape_skipped(self):
        # 工具自身的规则库（pattern 键）不能被当成漏洞报自己
        issues = py_sec('''\
RULES = [
    {"pattern": r"os\\\\.system\\\\(\\\\$CMD\\\\)", "name": "cmd"},
]
''')
        self.assertEqual([], redos_of(issues), issues)
        # 并且整行被规则库豁免后，其余安全行也不应误报
        self.assertEqual(0, len(issues), issues)


if __name__ == "__main__":
    unittest.main()