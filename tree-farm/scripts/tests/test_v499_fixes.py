# -*- coding: utf-8 -*-
r"""
树场 v4.9.9 扣子复测修复回归测试套件（unittest，零依赖）

背景：扣子AI v4.9.8 金标准复测报告（迄今最靠谱第三方报告，逐项本地实锤属实）
暴露 4 个缺陷，本轮全部修复：
  1. ReDoS：common.py _symbols_uncached 声明形态正则 (?:\s*[*&]+\s*|\s+)+ 无界量词
     在 glibc features.h 风格（宏定义+续行）上灾难性回溯（1200 组 6.4s vs 新逻辑 0.4s，
     8 倍输入 → 30 倍耗时）。修复：逐行 splitlines 匹配 + 量词收紧 {0,4}。
  2. 跨文件路径遍历漏报：污点源正则漏了裸 request.get()（原只认
     request.args/request.query/self.request 形态）。修复：补 request 直接 .get(。
  3. 竞态漏报：全局共享变量 counter += 1（无 self./dict 形态）不报；
     _has_lock_in_scope 在 visit 后 _anc 栈空恒失效。修复：_is_module_global +
     _func_of 行号范围判定 + 竞态并入 security 层（真并发才报）。
  4. meta refresh 转义引号变体漏报：url=" 处用 \" 转义时原正则不认。
     修复：url\s*=\s*(?:\\*["']\s*)*\+ 兼容转义引号连排。

运行：
  python3 -m unittest tests.test_v499_fixes -v
"""
import os
import shutil
import signal
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402
from treefarm.common import FileCache  # noqa: E402


def project_issues(files):
    """多文件目录跑安全检测，返回全部 issues。files: {文件名: 内容}"""
    d = tempfile.mkdtemp(prefix="treefarm_v499_")
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


def types_of(issues, t):
    return [i for i in issues if i["type"] == t]


def glibc_style_header(n_macros: int, tokens: int = 40) -> str:
    """构造 glibc features.h 风格输入：n 组「宏定义 + 长 token 串 + 续行」。
    旧无界正则在 1200 组 / 40 token 上 6.4s（ReDoS），新逻辑逐行扫描 <0.5s。"""
    parts = []
    for i in range(n_macros):
        parts.append("#define __GLIBC_USE_%d(X) " % i + " ".join(["x"] * tokens) + " \\")
        parts.append("  " + " ".join(["y"] * tokens))
    return "\n".join(parts)


class ReDoSTest(unittest.TestCase):
    """修复1：声明形态正则逐行扫描 + 量词收紧，glibc 风格头文件不再卡死。"""

    def _symbols_with_timeout(self, path, timeout=6):
        """带硬超时的 symbols()：超时视为 ReDoS 回归（fail）。"""
        def _handler(signum, frame):
            raise AssertionError("ReDoS 回归：symbols() 超时 %.0fs 未返回" % timeout)
        old = signal.signal(signal.SIGALRM, _handler)
        signal.alarm(timeout)
        t0 = time.time()
        try:
            result = FileCache().symbols(path)
            return result, time.time() - t0
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old)

    def test_glibc_style_header_under_3s(self):
        # 1200 组 / 40 token：旧无界正则 6.4s，新逻辑逐行 <0.5s
        d = tempfile.mkdtemp(prefix="tf_redos_")
        path = os.path.join(d, "features.h")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(glibc_style_header(1200))
            syms, dt = self._symbols_with_timeout(path)
            self.assertLess(dt, 3.0, "声明形态符号提取应 <3s，实测 %.2fs" % dt)
            self.assertIsInstance(syms, list)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_huge_header_still_fast(self):
        # 4000 组：规模翻 3 倍仍应保持线性（旧正则二次方爆炸必挂）
        d = tempfile.mkdtemp(prefix="tf_redos_")
        path = os.path.join(d, "big.h")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(glibc_style_header(4000))
            syms, dt = self._symbols_with_timeout(path)
            self.assertLess(dt, 5.0, "4000 组应 <5s，实测 %.2fs" % dt)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class CrossFilePathTraversalTest(unittest.TestCase):
    """修复2：裸 request.get() 也是用户输入源 → 跨文件路径遍历应报。"""

    def test_bare_request_get_cross_file_traversal(self):
        issues = project_issues({
            "a.py": '''\
from b import read_srv_file


def handler(request):
    filename = request.get("filename", "")
    return read_srv_file(filename)
''',
            "b.py": '''\
def read_srv_file(name):
    with open("/srv/data/" + name) as f:
        return f.read()
''',
        })
        trav = types_of(issues, "路径遍历")
        self.assertEqual(len(trav), 1, issues)
        self.assertIn("跨文件", trav[0]["desc"], trav[0])


class RaceConditionTest(unittest.TestCase):
    """修复3：全局共享变量无锁自增（真并发）→ security 层应报；异步线程池降级不报。"""

    def test_global_counter_race_reported_in_security(self):
        issues = project_issues({
            "race.py": '''\
import threading

counter = 0


def worker():
    global counter
    for _ in range(1000):
        counter += 1


threads = [threading.Thread(target=worker) for _ in range(10)]
for t in threads:
    t.start()
for t in threads:
    t.join()
print(counter)
''',
        })
        races = types_of(issues, "竞态条件")
        self.assertEqual(len(races), 1, issues)
        self.assertEqual(races[0]["severity"], "high", races[0])

    def test_async_executor_not_race(self):
        # tornado ioloop 场景：asyncio + ThreadPoolExecutor offload → 降级不报
        issues = project_issues({
            "ioloop.py": '''\
import asyncio
from concurrent.futures import ThreadPoolExecutor


class IOLoop:
    def __init__(self):
        self._executor = ThreadPoolExecutor(1)
        self._next_timeout = 0

    async def add_timeout(self, sec):
        self._next_timeout += sec
        await asyncio.sleep(0)
        return self._next_timeout
''',
        })
        self.assertEqual([], types_of(issues, "竞态条件"), issues)


class MetaRefreshEscapedQuoteTest(unittest.TestCase):
    """修复4：meta refresh url 前用 \" 转义引号（\"+target）也应报开放重定向。"""

    def test_escaped_quote_variant(self):
        issues = project_issues({
            "redirect_v2.py": '''\
def redirect_page_v2(target):
    html = '<meta http-equiv="refresh" content="0;url=\\\\"' + target + '\\\\">'
    return html
''',
        })
        redir = types_of(issues, "开放重定向")
        self.assertEqual(len(redir), 1, issues)

    def test_plain_quote_still_reported(self):
        # 常规形态（v4.9.7 已修）回归：不能因本次改动退化
        issues = project_issues({
            "redirect.py": '''\
def redirect_page(target):
    html = '<meta http-equiv="refresh" content="0;url=" + target + ">">'
    return html
''',
        })
        self.assertEqual(len(types_of(issues, "开放重定向")), 1, issues)


if __name__ == "__main__":
    unittest.main()
