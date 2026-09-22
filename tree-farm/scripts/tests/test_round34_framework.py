# -*- coding: utf-8 -*-
"""
树场 35 轮测试 · R18：Web 框架安全模式（Flask/Django）（unittest）

运行：
  python3 -m unittest tests.test_round34_framework -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402


def make_file(content: str, suffix=".py") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="tf_r34_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def issues_of(content, itype=None):
    path = make_file(content)
    try:
        all_issues = detect_security_issues([path])["issues"]
        if itype:
            return [i for i in all_issues if i["type"] == itype]
        return all_issues
    finally:
        os.unlink(path)


class FlaskTest(unittest.TestCase):
    def test_flask_route_sql(self):
        # Flask 路由内 SQL 拼接（request.args 源）
        issues = issues_of('''\
from flask import Flask, request
import sqlite3

app = Flask(__name__)


@app.route("/user")
def user():
    uid = request.args.get("id")
    conn = sqlite3.connect("db.sqlite")
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = " + uid)
    return "ok"
''', "SQL注入")
        self.assertTrue(len(issues) >= 1, f"Flask 路由 SQL 漏报: {issues}")

    def test_flask_env_cmd(self):
        issues = issues_of('''\
import os
from flask import Flask, request

app = Flask(__name__)


@app.route("/cmd")
def cmd():
    os.system(request.args.get("c"))
    return "done"
''', "命令注入")
        self.assertTrue(len(issues) >= 1, f"Flask 命令注入漏报: {issues}")

    def test_flask_safe_render(self):
        # 安全：render_template（文件模板）不报
        issues = issues_of('''\
from flask import Flask, render_template, request

app = Flask(__name__)


@app.route("/g")
def g():
    return render_template("page.html", name=request.args.get("n"))
''', "模板注入")
        self.assertEqual([], issues, f"render_template 误报: {issues}")

    def test_flask_debug_pattern(self):
        issues = issues_of('''\
from flask import Flask

app = Flask(__name__)
app.run(debug=True)
''', "调试模式")
        self.assertTrue(len(issues) >= 1, f"Flask debug 漏报: {issues}")


class DjangoTest(unittest.TestCase):
    def test_django_raw_sql(self):
        issues = issues_of('''\
from django.db import connection
from django.http import HttpResponse


def search(request):
    q = request.GET.get("q")
    cursor = connection.cursor()
    cursor.execute("SELECT * FROM items WHERE name LIKE '%" + q + "%'")
    return HttpResponse(cursor.fetchall())
''', "SQL注入")
        self.assertTrue(len(issues) >= 1, f"Django RAW SQL 漏报: {issues}")

    def test_django_xss(self):
        issues = issues_of('''\
from django.http import HttpResponse


def page(request):
    return HttpResponse("<h1>" + request.GET.get("t") + "</h1>")
''', "XSS跨站脚本")
        self.assertTrue(len(issues) >= 1, f"Django XSS 漏报: {issues}")

    def test_django_orm_safe(self):
        # 安全：ORM 参数化不报
        issues = issues_of('''\
from django.db import models


def find(request):
    name = request.GET.get("n")
    return models.Item.objects.filter(name=name)
''', "SQL注入")
        self.assertEqual([], issues, f"ORM 误报: {issues}")


if __name__ == "__main__":
    unittest.main()