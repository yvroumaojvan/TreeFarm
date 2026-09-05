# -*- coding: utf-8 -*-
"""
树场 v4.9.5 跨文件污点检测测试套件（unittest，零依赖）

背景：豆包复测报告（不可信正文）里唯一有价值线索——「跨文件污点漏报」
（a.py 收 request 输入 → b.py execute SQL）。本地实锤 v4.9.4 确实 0 检出。

第1轮实现（analysis.py）：
  第一阶段：各文件收集 import 模块映射 + 函数参数表 + 强污点实参跨模块调用
  第二阶段：按模块名定位被调文件，把强污点实参映射到被调函数参数，
            参数直接流入危险 sink（SQL/命令/路径/SSRF/redirect）才报，
            带「需人工确认」标签 + 来源描述；参数化/列表Popen/静态豁免。

运行：
  python3 -m unittest discover -s scripts/tests -v
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402


def project_issues(files):
    """多文件目录跑安全检测，返回全部 issues。files: {文件名: 内容}"""
    d = tempfile.mkdtemp(prefix="treefarm_v495_")
    try:
        paths = []
        for name, content in files.items():
            p = os.path.join(d, name)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
            paths.append(p)
        return detect_security_issues(paths, root=d)["issues"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def cross_issues(files):
    """只看跨文件污点命中的条目（desc 含「跨文件」）。"""
    return [i for i in project_issues(files) if "跨文件" in i["desc"]]


class CrossFileSqlTest(unittest.TestCase):
    """跨文件 SQL 注入：位置参数 / 关键字参数 / 无导入不报。"""

    def test_cross_file_sql_positional(self):
        issues = cross_issues({
            "a.py": '''\
import b


def handler(request):
    user_input = request.args.get("q")
    b.exec_sql(user_input)
''',
            "b.py": '''\
import sqlite3


def exec_sql(sql):
    conn = sqlite3.connect("db.sqlite")
    conn.cursor().execute(sql)
''',
        })
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["type"], "SQL注入")
        self.assertIn("跨文件", issues[0]["desc"])
        self.assertIn("a.py", issues[0]["desc"])  # 来源可追溯
        self.assertEqual(issues[0]["file"], "b.py")

    def test_cross_file_sql_keyword_arg(self):
        issues = cross_issues({
            "a.py": '''\
import b


def handler(request):
    user_input = request.form.get("q")
    b.exec_sql(sql=user_input)
''',
            "b.py": '''\
def exec_sql(sql):
    conn.execute(sql)
''',
        })
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["type"], "SQL注入")
        self.assertIn("跨文件", issues[0]["desc"])

    def test_import_as_alias(self):
        issues = cross_issues({
            "a.py": '''\
import b as db


def handler(request):
    db.exec_sql(request.args.get("q"))
''',
            "b.py": '''\
def exec_sql(sql):
    conn.execute(sql)
''',
        })
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["type"], "SQL注入")

    def test_no_import_means_no_cross_report(self):
        # 没有 import b：跨文件无目标，不应出现「跨文件」条目
        issues = cross_issues({
            "a.py": '''\
def handler(request):
    user_input = request.args.get("q")
    exec_sql(user_input)
''',
            "b.py": '''\
def exec_sql(sql):
    conn.execute(sql)
''',
        })
        self.assertEqual(len(issues), 0)

    def test_stdlib_module_not_mapped(self):
        # os 是标准库，映射不到项目文件：不应产生「跨文件」条目
        issues = cross_issues({
            "a.py": '''\
import os


def handler(request):
    user_input = request.args.get("q")
    os.system(user_input)
''',
        })
        self.assertEqual(len(issues), 0)


class CrossFileOtherSinksTest(unittest.TestCase):
    """跨文件命令注入 / 路径遍历 / SSRF。"""

    def test_cross_file_cmd_injection(self):
        issues = cross_issues({
            "a.py": '''\
import b


def handler(request):
    user_input = request.args.get("cmd")
    b.run_cmd(user_input)
''',
            "b.py": '''\
import os


def run_cmd(cmd):
    os.system(cmd)
''',
        })
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["type"], "命令注入")

    def test_cross_file_path_traversal(self):
        issues = cross_issues({
            "a.py": '''\
import b


def handler(request):
    user_input = request.args.get("f")
    b.read_file(user_input)
''',
            "b.py": '''\
def read_file(path):
    with open(path) as f:
        return f.read()
''',
        })
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["type"], "路径遍历")

    def test_cross_file_ssrf(self):
        issues = cross_issues({
            "a.py": '''\
import b


def handler(request):
    user_input = request.args.get("url")
    b.fetch(user_input)
''',
            "b.py": '''\
import requests


def fetch(url):
    return requests.get(url)
''',
        })
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["type"], "SSRF")


class CrossFileExemptTest(unittest.TestCase):
    """安全形态豁免：参数化绑定 / 列表 Popen / 无强污点。"""

    def test_parametrized_query_exempt(self):
        # 注入参数落在绑定参数位（execute 第二参）→ 参数化安全，不报
        issues = cross_issues({
            "a.py": '''\
import b


def handler(request):
    user_input = request.args.get("q")
    b.exec_sql("SELECT * FROM t WHERE id = ?", user_input)
''',
            "b.py": '''\
def exec_sql(sql, params):
    conn.execute(sql, params)
''',
        })
        self.assertEqual(len(issues), 0)

    def test_popen_list_exempt_but_string_reported(self):
        # 列表形态 Popen([cmd]) 豁免；字符串单参形态必报
        files = {
            "a.py": '''\
import b


def handler(request):
    b.run1(request.args.get("a"))
    b.run2(request.args.get("b"))
''',
            "b.py": '''\
import subprocess


def run1(cmd):
    subprocess.Popen([cmd])


def run2(cmd):
    subprocess.Popen(cmd)
''',
        }
        issues = cross_issues(files)
        self.assertEqual(len(issues), 1)
        # 命中的是 run2 的字符串单参形态；run1 的列表形态被豁免
        self.assertEqual(issues[0]["code"], "subprocess.Popen(cmd)")

    def test_plain_param_no_user_source(self):
        # 调用方无用户输入源（强污点），仅传普通变量 → 不报
        issues = cross_issues({
            "a.py": '''\
import b


def main():
    data = "SELECT * FROM t"
    b.exec_sql(data)
''',
            "b.py": '''\
def exec_sql(sql):
    conn.execute(sql)
''',
        })
        self.assertEqual(len(issues), 0)


class CrossFileTwoHopTest(unittest.TestCase):
    """第2轮：两层透传链（a→b→c）+ 目标文件内拼接传播。"""

    def test_two_hop_chain_reported_at_sink_file(self):
        # a.py 污点 → b.transmit(sql) 原样透传 → c.exec_sql(sql) → 命中 c.py
        issues = cross_issues({
            "a.py": '''\
import b


def handler(request):
    user_input = request.args.get("q")
    b.transmit(user_input)
''',
            "b.py": '''\
import c


def transmit(sql):
    c.exec_sql(sql)
''',
            "c.py": '''\
def exec_sql(sql):
    conn.execute(sql)
''',
        })
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["file"], "c.py")
        self.assertEqual(issues[0]["type"], "SQL注入")
        self.assertIn("a.py", issues[0]["desc"])  # 追溯到源头

    def test_concat_propagation_in_callee(self):
        # b.py 内拼接传播：q = "..." + sql → execute(q) 命中
        issues = cross_issues({
            "a.py": '''\
import b


def handler(request):
    user_input = request.args.get("q")
    b.exec_sql(user_input)
''',
            "b.py": '''\
def exec_sql(sql):
    q = "SELECT * FROM t WHERE x = '" + sql + "'"
    conn.execute(q)
''',
        })
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["type"], "SQL注入")
        self.assertEqual(issues[0]["file"], "b.py")

    def test_two_hop_chain_with_safe_parametrized_sink(self):
        # 三层里最终 sink 是参数化（execute(sql, params)）→ 不报
        issues = cross_issues({
            "a.py": '''\
import b


def handler(request):
    user_input = request.args.get("q")
    b.transmit(user_input)
''',
            "b.py": '''\
import c


def transmit(sql):
    c.exec_sql("SELECT * FROM t WHERE id = ?", sql)
''',
            "c.py": '''\
def exec_sql(sql, params):
    conn.execute(sql, params)
''',
        })
        self.assertEqual(len(issues), 0)


class CrossFileDedupAndExemptTest(unittest.TestCase):
    """第3轮：单文件层与跨文件层联动去重 + 豁免组合。"""

    def test_concat_sink_dedup_prefers_cross_file(self):
        # b.py 单文件层会报「param 拼接升级 concat」跨行数据流；跨文件层也命中同一行
        # → 去重后只留 1 条，且是证据链更完整的跨文件条目（带来源）
        issues = project_issues({
            "a.py": '''\
import b


def handler(request):
    user_input = request.args.get("q")
    b.exec_sql(user_input)
''',
            "b.py": '''\
def exec_sql(sql):
    q = "SELECT * FROM t WHERE x = '" + sql + "'"
    conn.execute(q)
''',
        })
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["type"], "SQL注入")
        self.assertIn("跨文件", issues[0]["desc"])
        self.assertIn("a.py", issues[0]["desc"])

    def test_two_injections_same_sink_line_reported_once(self):
        # 两个调用方都注入同一 sink 行 → 只报 1 条（防重复刷屏）
        issues = project_issues({
            "a.py": '''\
import b


def h1(request):
    b.exec_sql(request.args.get("q1"))


def h2(request):
    b.exec_sql(request.form.get("q2"))
''',
            "b.py": '''\
def exec_sql(sql):
    conn.execute(sql)
''',
        })
        sql_hits = [i for i in issues if i["type"] == "SQL注入"]
        self.assertEqual(len(sql_hits), 1)

    def test_injected_param_not_in_sink_literal(self):
        # 注入参数与静态 SQL 字面量无关（execute("SELECT 1")）→ 不报
        issues = project_issues({
            "a.py": '''\
import b


def handler(request):
    b.exec_sql(request.args.get("q"))
''',
            "b.py": '''\
def exec_sql(sql):
    conn.execute("SELECT 1")
''',
        })
        self.assertEqual(len(issues), 0)

    def test_popen_list_across_two_hops_exempt(self):
        # 两层透传链 + 列表形态 Popen → 豁免不报
        issues = cross_issues({
            "a.py": '''\
import b


def handler(request):
    b.transmit(request.args.get("c"))
''',
            "b.py": '''\
import c


def transmit(cmd):
    c.run(cmd)
''',
            "c.py": '''\
import subprocess


def run(cmd):
    subprocess.Popen([cmd])
''',
        })
        self.assertEqual(len(issues), 0)

    def test_parametrized_two_hops_exempt(self):
        # 两层透传链 + 参数化 sink → 豁免不报
        issues = cross_issues({
            "a.py": '''\
import b


def handler(request):
    b.transmit(request.args.get("q"))
''',
            "b.py": '''\
import c


def transmit(sql):
    c.exec_sql("SELECT * FROM t WHERE id = ?", sql)
''',
            "c.py": '''\
def exec_sql(sql, params):
    conn.execute(sql, params)
''',
        })
        self.assertEqual(len(issues), 0)


class CrossFileRound4Test(unittest.TestCase):
    """第4轮：from 导入形态 / ORM 白名单 / 包路径 / 循环引用。"""

    def test_from_import_direct_call(self):
        # from b import exec_sql; exec_sql(user_input) → 命中
        issues = cross_issues({
            "a.py": '''\
from b import exec_sql


def handler(request):
    user_input = request.args.get("q")
    exec_sql(user_input)
''',
            "b.py": '''\
def exec_sql(sql):
    conn.execute(sql)
''',
        })
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["type"], "SQL注入")

    def test_from_import_alias_call(self):
        # from b import exec_sql as run; run(user_input) → 命中
        issues = cross_issues({
            "a.py": '''\
from b import exec_sql as run


def handler(request):
    run(request.args.get("q"))
''',
            "b.py": '''\
def exec_sql(sql):
    conn.execute(sql)
''',
        })
        self.assertEqual(len(issues), 1)

    def test_from_import_two_hops(self):
        # a: from b import f; b 里 from c import exec_sql 透传 → c 命中
        issues = cross_issues({
            "a.py": '''\
from b import transmit


def handler(request):
    transmit(request.args.get("q"))
''',
            "b.py": '''\
from c import exec_sql


def transmit(sql):
    exec_sql(sql)
''',
            "c.py": '''\
def exec_sql(sql):
    conn.execute(sql)
''',
        })
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["file"], "c.py")

    def test_orm_query_exempt(self):
        # session.query(user_input) 是 ORM 对象查询，不是 SQL 字符串拼接 → 不报
        issues = cross_issues({
            "a.py": '''\
import b


def handler(request):
    b.lookup(request.args.get("name"))
''',
            "b.py": '''\
def lookup(name):
    return session.query(User).filter(User.name == name).all()
''',
        })
        self.assertEqual(len(issues), 0)

    def test_package_path_import(self):
        # import pkg.mod; pkg.mod.func(user_input) → 命中（多级模块）
        issues = cross_issues({
            "a.py": '''\
import pkg.mod


def handler(request):
    pkg.mod.exec_sql(request.args.get("q"))
''',
            "pkg/__init__.py": "",
            "pkg/mod.py": '''\
def exec_sql(sql):
    conn.execute(sql)
''',
        })
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["file"], "pkg/mod.py")

    def test_cyclic_import_no_infinite_loop(self):
        # a import b / b import a 循环引用 + 污点传递 → 不死循环，正确命中 1 条
        issues = cross_issues({
            "a.py": '''\
import b


def handler(request):
    user_input = request.args.get("q")
    b.exec_sql(user_input)
''',
            "b.py": '''\
import a


def exec_sql(sql):
    conn.execute(sql)


def back(x):
    return a.handler({"q": x})
''',
        })
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["type"], "SQL注入")


class CrossFileRobustnessTest(unittest.TestCase):
    """第5轮：健壮性边界——空文件/非py/超多import/中文变量名，不崩不误报。"""

    def test_empty_and_nonpy_files_no_crash(self):
        issues = cross_issues({
            "a.py": "",
            "b.py": "# 只有注释\n",
            "c.js": "var x = 1;",
            "d.txt": "hello",
        })
        self.assertEqual(len(issues), 0)

    def test_many_stdlib_imports_no_crash(self):
        # 大量标准库 import（都映射不到项目文件）+ 调用 → 不崩、不产生跨文件误报
        issues = cross_issues({
            "a.py": '''\
import os
import sys
import re
import json
import time
import math
import random
import shutil
import hashlib
import sqlite3
import subprocess
import collections
import functools
import itertools


def handler(request):
    user_input = request.args.get("q")
    os.system(user_input)
    subprocess.call(user_input)
    shutil.copy(user_input, "/tmp/x")
    sqlite3.connect(user_input)
''',
        })
        # 标准库模块映射不到项目文件：不应出现「跨文件」条目
        self.assertEqual(len(issues), 0)

    def test_unicode_var_names_no_crash(self):
        # 中文变量名污点链，不崩
        issues = cross_issues({
            "a.py": '''\
import b


def handler(request):
    用户输入 = request.args.get("q")
    b.查询(用户输入)
''',
            "b.py": '''\
def 查询(sql):
    conn.execute(sql)
''',
        })
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["type"], "SQL注入")


if __name__ == "__main__":
    unittest.main()
