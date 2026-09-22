# -*- coding: utf-8 -*-
"""
树场 35 轮测试 · R23：误报治理扩展 II（unittest）

运行：
  python3 -m unittest tests.test_round37_fp2 -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402


def make_file(content: str, suffix=".py") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="tf_r37_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def issues_of(content, suffix=".py"):
    path = make_file(content, suffix)
    try:
        return detect_security_issues([path])["issues"]
    finally:
        os.unlink(path)


class FrameworkSafeTest(unittest.TestCase):
    """框架安全写法必须零误报"""

    def test_flask_param_query(self):
        self.assertEqual([], issues_of('''\
from flask import Flask, request
import sqlite3

app = Flask(__name__)


@app.route("/u")
def u():
    uid = request.args.get("id")
    conn = sqlite3.connect("db.sqlite")
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = ?", (uid,))
    return "ok"
'''), f"Flask 参数化误报: {issues_of('''from flask import Flask, request\nimport sqlite3\napp = Flask(__name__)\n@app.route(\"/u\")\ndef u():\n    uid = request.args.get(\"id\")\n    cur = sqlite3.connect(\"db.sqlite\").cursor()\n    cur.execute(\"SELECT * FROM users WHERE id = ?\", (uid,))\n    return \"ok\"\n''')}")

    def test_orm_filter(self):
        self.assertEqual([], issues_of('''\
from sqlalchemy.orm import sessionmaker


def find(session, name):
    return session.query(User).filter(User.name == name).all()
'''), "ORM 误报")

    def test_awssdk_ok(self):
        self.assertEqual([], issues_of('''\
import boto3


def upload(bucket, key, body):
    s3 = boto3.client("s3")
    return s3.put_object(Bucket=bucket, Key=key, Body=body)
'''), "AWS SDK 误报")

    def test_httpx_timeout(self):
        self.assertEqual([], issues_of('''\
import httpx


def check():
    return httpx.get("https://api.github.com/zen", timeout=5)
'''), "httpx 固定 URL 误报")

    def test_subprocess_const(self):
        self.assertEqual([], issues_of('''\
import subprocess


def backup():
    return subprocess.run(["tar", "czf", "/tmp/bk.tar", "/data"], shell=False)
'''), "subprocess 常量误报")


class EnvironmentSafeTest(unittest.TestCase):
    def test_env_import_os(self):
        # os.environ 取值不是硬编码
        self.assertEqual([], issues_of('''\
import os


def get(key):
    return os.environ.get(key, "default")
'''), "环境变量误报")

    def test_config_from_env(self):
        self.assertEqual([], issues_of('''\
import os

DB_PASSWORD = os.getenv("DB_PASSWORD")
API_KEY = os.environ.get("GOOGLE_API_KEY", "")
'''), "配置环境变量误报")


if __name__ == "__main__":
    unittest.main()