# -*- coding: utf-8 -*-
"""
树场 50 轮测试 · R40：误报治理 III（unittest）

运行：
  python3 -m unittest tests.test_round44_fp3 -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402


def make_file(content: str, suffix=".py") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="tf_r44_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def issues_of(content, suffix=".py"):
    path = make_file(content, suffix)
    try:
        return detect_security_issues([path])["issues"]
    finally:
        os.unlink(path)


class SafePatternsIIITest(unittest.TestCase):
    """更多安全写法必须零误报"""

    def test_redis_client(self):
        self.assertEqual([], issues_of('''\
import redis


def connect():
    r = redis.Redis(host="127.0.0.1", port=6379, db=0)
    return r
'''), "redis 误报")

    def test_telegram_bot(self):
        self.assertEqual([], issues_of('''\
import telegram


def send(msg):
    bot = telegram.Bot(token=os.environ["TG_TOKEN"])
    bot.send_message(chat_id=12345, text=msg)
'''), "telegram bot 误报")

    def test_django_login(self):
        self.assertEqual([], issues_of('''\
from django.contrib.auth import authenticate


def login(request):
    user = authenticate(request, username=request.POST["u"],
                        password=request.POST["p"])
    return user
'''), "django authenticate 误报")

    def test_fastapi_param(self):
        self.assertEqual([], issues_of('''\
from fastapi import FastAPI
from fastapi.params import Query

app = FastAPI()


@app.get("/search")
def search(q: str = Query(..., min_length=1)):
    return {"q": q}
'''), "fastapi 误报")

    def test_sqlalchemy_text(self):
        self.assertEqual([], issues_of('''\
from sqlalchemy import text


def count(engine):
    with engine.connect() as conn:
        return conn.execute(text("SELECT COUNT(*) FROM users")).scalar()
'''), "sqlalchemy text 常量误报")

    def test_pytest_fixture(self):
        self.assertEqual([], issues_of('''\
import pytest


@pytest.fixture
def db():
    return {"conn": None}


def test_query(db):
    assert db["conn"] is None
'''), "pytest fixture 误报")


if __name__ == "__main__":
    unittest.main()