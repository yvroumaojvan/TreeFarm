# -*- coding: utf-8 -*-
"""
树场 v4.9.15 第4轮自测回归测试套件（unittest，零依赖）

覆盖（第4轮主题：Dogfooding——树场扫自己，消除规则库自误报）：
  Dogfooding 用 v4.9.14 扫自身源码，实锤 3 处自误报，本轮修复：
  1. XSS 规则库元组定义行 (r'''\|[\s]*safe\b''', "XSS:...") 被 XSS 检测器自报
  2. XXE 规则库元组定义行 (r'''(no_network|load_dtd|...)=False''', "XXE:...")
     被 XXE 检测器自报
  修复：行级循环增加「规则库元组形态跳过」（含三引号 raw + 逗号 + desc 字符串，
  且非 re. 调用才跳），与既有 pattern 键跳过并列。
  回归保护：用户代码 re.compile/re.sub 的 raw 正则（含 re.）仍正常检测。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402


def py_sec(content):
    fd, path = tempfile.mkstemp(suffix=".py", prefix="tf_v4915_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    try:
        return detect_security_issues([path], root=os.path.dirname(path))["issues"]
    finally:
        os.unlink(path)


def types_of(issues, t):
    return [i for i in issues if i["type"] == t]


class RuleLibraryNoSelfReportTest(unittest.TestCase):
    """规则库元组定义行（含敏感关键词）不能被对应检测器自报。"""

    def test_xss_rule_tuple_not_self_reported(self):
        issues = py_sec('''\
XSS_PATTERNS = [
    (r\'\'\'\\|[\\s]*safe\\b\'\'\', "XSS：Jinja2 |safe 过滤器输出未转义内容"),
    (r\'\'\'\\.innerHTML\\s*=\\s*\'\'\', "XSS：innerHTML 直接写入"),
]
''')
        self.assertEqual([], types_of(issues, "XSS跨站脚本"), issues)
        self.assertEqual(0, len(issues), issues)

    def test_xxe_rule_tuple_not_self_reported(self):
        issues = py_sec('''\
XXE_PATTERNS = [
    (r\'\'\'(no_network|load_dtd|resolve_entities)\\s*=\\s*False\'\'\', "XXE：禁用安全防护"),
]
''')
        self.assertEqual([], types_of(issues, "XXE"), issues)
        self.assertEqual(0, len(issues), issues)

    def test_rule_dict_shape_still_skipped(self):
        # v4.9.14 的规则 dict 形态跳过保持有效
        issues = py_sec('''\
RULES = [
    {"pattern": r"os\\\\.system\\\\(\\\\$CMD\\\\)", "name": "cmd"},
]
''')
        self.assertEqual(0, len(issues), issues)


class UserRegexStillDetectedTest(unittest.TestCase):
    """回归保护：真实用户代码（re. 调用）不受规则库豁免影响。"""

    def test_compile_nested_quant_still_reported(self):
        issues = py_sec('''\
import re
x = re.compile(r"(a+)+")
''')
        self.assertEqual(1, len(types_of(issues, "ReDoS")), issues)

    def test_search_with_extra_args_still_reported(self):
        # re.search 带额外参数（有逗号）仍应检测，不被规则库豁免误伤
        issues = py_sec('''\
import re
out = re.search(r"(a+)+", text, re.IGNORECASE)
''')
        self.assertEqual(1, len(types_of(issues, "ReDoS")), issues)


if __name__ == "__main__":
    unittest.main()