# -*- coding: utf-8 -*-
"""
树场 v4.5 性能/逻辑检测测试套件（unittest，零依赖）

覆盖：新 AST 检测器对 13 类“深 bug”的检出，以及 8 类正常代码的零误报。
运行：
  python3 -m unittest discover -s scripts/tests -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_logic_issues, detect_performance_issues  # noqa: E402


def make_py_file(content: str) -> str:
    """造一个含目标代码的临时 .py 文件，返回路径。"""
    fd, path = tempfile.mkstemp(suffix=".py", prefix="treefarm_perf_logic_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ============ 深 bug 靶子（应被检出） ============

BUGGY = '''\
# -*- coding: utf-8 -*-
import json
import os
import re
import threading


def perf_nested_loop_search(items, targets):
    found = []
    for t in targets:
        for item in items:
            if item in targets:
                found.append(item)
    return found


def perf_string_concat(n):
    s = ""
    for i in range(n):
        s += f"row-{i}"
    return s


def perf_string_concat2(n):
    s = ""
    for i in range(n):
        s = s + str(i)
    return s


def perf_regex_in_loop(lines):
    out = []
    for line in lines:
        pat = re.compile(r"^\\d+")
        if pat.match(line):
            out.append(line)
    return out


def perf_n_plus_one(users, db):
    names = []
    for u in users:
        row = db.query(f"SELECT name FROM t WHERE id={u}")
        names.append(row)
    return names


def perf_deep_recursion(x):
    return perf_deep_recursion(x) + 1


def perf_fib_no_cache(n):
    if n <= 1:
        return n
    return perf_fib_no_cache(n - 1) + perf_fib_no_cache(n - 2)


def logic_mutable_default(items=[]):
    items.append(1)
    return items


def logic_divide_zero(a, b):
    return a / b


def logic_off_by_one(xs):
    total = 0
    for i in range(len(xs)):
        total += xs[i + 1]
    return total


def logic_race():
    counter = {"n": 0}
    def worker():
        for _ in range(1000):
            counter["n"] += 1
    ts = [threading.Thread(target=worker) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return counter["n"]


def logic_toctou(path):
    if os.path.exists(path):
        with open(path) as f:
            return f.read()
    return None


def logic_is_literal(x):
    return x is 5


def logic_str_ge(role):
    return role >= "admin"
'''


class TestPerfDetectionDeep(unittest.TestCase):
    """深 bug 靶子：性能检测应抓齐所有类型。"""

    @classmethod
    def setUpClass(cls):
        cls.path = make_py_file(BUGGY)
        cls.result = detect_performance_issues([cls.path], root=os.path.dirname(cls.path))

    @classmethod
    def tearDownClass(cls):
        os.remove(cls.path)

    def _types(self):
        return {i["type"] for i in self.result["issues"]}

    def test_nested_loop_linear_search(self):
        self.assertIn("循环内线性查找", self._types())

    def test_string_concat_in_loop(self):
        self.assertIn("循环内字符串拼接", self._types())

    def test_regex_compile_in_loop(self):
        self.assertIn("循环内正则编译", self._types())

    def test_n_plus_one(self):
        self.assertIn("N+1 查询", self._types())

    def test_recursion_no_base(self):
        self.assertIn("递归无终止", self._types())

    def test_recursion_no_cache_fib(self):
        self.assertIn("递归无缓存", self._types())

    def test_all_perf_issues_have_contract(self):
        for i in self.result["issues"]:
            for k in ("file", "line", "type", "severity", "desc", "code"):
                self.assertIn(k, i)


class TestLogicDetectionDeep(unittest.TestCase):
    """深 bug 靶子：逻辑检测应抓齐所有类型。"""

    @classmethod
    def setUpClass(cls):
        cls.path = make_py_file(BUGGY)
        cls.result = detect_logic_issues([cls.path], root=os.path.dirname(cls.path))

    @classmethod
    def tearDownClass(cls):
        os.remove(cls.path)

    def _types(self):
        return {i["type"] for i in self.result["issues"]}

    def test_mutable_default(self):
        self.assertIn("可变默认参数", self._types())

    def test_divide_by_zero(self):
        self.assertIn("除零风险", self._types())

    def test_off_by_one_boundary(self):
        self.assertIn("边界条件", self._types())

    def test_race_condition_dict_counter(self):
        self.assertIn("竞态条件", self._types())

    def test_toctou_in_if(self):
        self.assertIn("TOCTOU 竞争", self._types())

    def test_is_literal_comparison(self):
        self.assertIn("比较运算符错误", self._types())

    def test_all_logic_issues_have_contract(self):
        for i in self.result["issues"]:
            for k in ("file", "line", "type", "severity", "desc", "code"):
                self.assertIn(k, i)


# ============ 正常代码对照组（应零误报） ============

CLEAN = '''\
# -*- coding: utf-8 -*-
import json
import os
import threading


def good_int_accumulate(n):
    total = 0
    for i in range(n):
        total += i
    return total


def good_join(n):
    parts = []
    for i in range(n):
        parts.append(str(i))
    return ",".join(parts)


def good_set_lookup(items, x):
    s = set(items)
    return x in s


def good_json_get(raw):
    d = json.loads(raw)
    return d.get("key", 0)


def good_division(denom):
    if denom != 0:
        return 100 / denom
    return 0


def good_recursion_with_base(n):
    if n <= 1:
        return 1
    return n * good_recursion_with_base(n - 1)


def good_path_comment(url):
    return url.split("/")[-1]


def good_lock():
    lock = threading.Lock()
    n = 0
    def worker():
        nonlocal n
        for _ in range(10):
            with lock:
                n += 1
    ts = [threading.Thread(target=worker) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return n
'''


class TestPerfNoFalsePositive(unittest.TestCase):
    """正常代码：性能检测应零误报。"""

    @classmethod
    def setUpClass(cls):
        cls.path = make_py_file(CLEAN)
        cls.result = detect_performance_issues([cls.path], root=os.path.dirname(cls.path))

    @classmethod
    def tearDownClass(cls):
        os.remove(cls.path)

    def test_zero_false_positive(self):
        self.assertEqual(self.result["total"], 0, self.result["issues"])


class TestLogicNoFalsePositive(unittest.TestCase):
    """正常代码：逻辑检测应零误报。"""

    @classmethod
    def setUpClass(cls):
        cls.path = make_py_file(CLEAN)
        cls.result = detect_logic_issues([cls.path], root=os.path.dirname(cls.path))

    @classmethod
    def tearDownClass(cls):
        os.remove(cls.path)

    def test_zero_false_positive(self):
        self.assertEqual(self.result["total"], 0, self.result["issues"])


class TestSingleFileScan(unittest.TestCase):
    """v4.5 修复：直接传单个 .py 文件（而非目录）也应被扫描到（os.walk 对文件返回空）。"""

    def test_scan_finds_single_file(self):
        from treefarm.common import scan
        path = make_py_file(CLEAN)
        try:
            result = scan(path)
            self.assertIn(path, result["tree"], "单文件应进 tree 列表")
            self.assertEqual(result["tree"], [path])
        finally:
            os.remove(path)

    def test_detect_on_single_file_path(self):
        """检测器直接收单文件路径（root=文件所在目录）也能跑。"""
        path = make_py_file(CLEAN)
        try:
            res = detect_performance_issues([path], root=os.path.dirname(path))
            self.assertEqual(res["total"], 0, res["issues"])
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
