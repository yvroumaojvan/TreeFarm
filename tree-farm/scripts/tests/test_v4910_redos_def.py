# -*- coding: utf-8 -*-
r"""
树场 v4.9.10 扣子复测修复回归测试套件 —— 定义形态正则 ReDoS（unittest，零依赖）

背景：扣子AI v4.9.9 金标准复测报告（A 级 + 重大缺陷标注）暴露——v4.9.9 只修了
声明形态正则（features.h 场景，构造靶场通过），但 common.py 定义形态正则
  [^;{}]* + (?:\s*:\s*[^{;]+)?
仍在 glibc tgmath.h 类"纯宏头文件"上灾难性回溯：
  - /usr/include 全扫 120s 卡死（exit 124，卡 1/4764）
  - tgmath.h 单文件 symbols 9.57s（仅产出 6 个符号）
根因：无界 [^;{}]* 在 (?m) 下跨行吞宏续行块；纯宏区无 ;{} 分隔时整块被吞，
再叠加 _Generic 的 default: 触发无界冒号组 → 全失败路径灾难性回溯。
更糟：宏续行块把真实函数定义吞进参数区，连符号都漏报（real_fn 提取不到）。

修复（v4.9.10）：定义形态正则单行化 + 量词有界（与 v4.9.9 声明正则同思路）：
  [^;{}\n]{0,512} 参数列表单行有界、冒号组 [ \t]*:[ \t]*[^{;\n]{0,128}、
  后缀关键词 (?:[ \t]+(?:const|...)){0,4}、名字与 ( 间收紧 [ \t]*。
取舍：参数列表罕见跨行写法（K&R/超长参数）会漏报符号，.h 原型由声明正则兜底。

运行：
  python3 -m unittest tests.test_v4910_redos_def -v
"""
import os
import shutil
import signal
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_logic_issues  # noqa: E402
from treefarm.common import FileCache  # noqa: E402


def tgmath_style_header(n_macros: int) -> str:
    r"""构造 glibc tgmath.h 风格输入：n 组「宏定义 + _Generic + 冒号 + 跨行续行」。
    纯宏区无 ;{} 分隔 → 旧无界 [^;{}]* 吞整块 + 冒号组无界回溯，逐组叠加爆炸。
    _Generic 的 float:/default: 正是喂给 (?:\s*:\s*[^{;]+)? 的触发器。"""
    parts = []
    for i in range(n_macros):
        parts.append("#define TGMATH_%d(X) __MATH_TG (TGMath, (X), sqrt, sqrtf, sqrtl, sqrt) \\" % i)
        parts.append("  __MATH_TG_F (TGMath, X, Fct, Fcf, Fcl, Fctf, __MATH_TG_##Fct) \\")
        parts.append("  _Generic ((X), float: Fcf, double: Fcf, long double: Fcl, \\")
        parts.append("            default: Fctf) \\")
        parts.append("  __TGMATH_REAL (X, (double) (X), sqrtf (X), sqrtl (X))")
    return "\n".join(parts)


class DefRegexReDoSTest(unittest.TestCase):
    """v4.9.10 修复：定义形态正则单行化 + 量词有界，tgmath.h 类头文件不再卡死、不漏报。"""

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

    def _tmp(self, name, content):
        d = tempfile.mkdtemp(prefix="tf_redos_def_")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        path = os.path.join(d, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_tgmath_style_pure_macro_under_3s(self):
        # 600 组纯宏 + _Generic：修复前 15.45s（repro 实测），修复后应 <3s
        path = self._tmp("tgmath.h", tgmath_style_header(600))
        syms, dt = self._symbols_with_timeout(path)
        self.assertLess(dt, 3.0, "tgmath 风格纯宏头文件应 <3s，实测 %.2fs" % dt)
        self.assertIsInstance(syms, list)

    def test_tgmath_style_huge_still_linear(self):
        # 2400 组（4 倍）：旧无界正则是二次方爆炸必挂，新逻辑应保持线性
        path = self._tmp("big_tgmath.h", tgmath_style_header(2400))
        syms, dt = self._symbols_with_timeout(path, timeout=8)
        self.assertLess(dt, 5.0, "2400 组应 <5s，实测 %.2fs" % dt)

    def test_real_function_defs_not_suppressed(self):
        # 修复前宏续行块把真实函数吞进参数区 → 漏报；修复后必须提取（.h 定义形态）
        src = (
            "#include <stdio.h>\n"
            "#define DEFAULT_NAME \"guest\"\n"
            "int real_fn(int a) { return a * 2; }\n"
            "void noop() {}\n"
            "int main(int argc, char **argv) { return 0; }\n"
        )
        path = self._tmp("real.h", src)
        syms, dt = self._symbols_with_timeout(path)
        for expect in ("real_fn", "noop", "main"):
            self.assertIn(expect, syms, "漏报 %s！符号=%s" % (expect, syms))

    def test_cpp_ctor_init_list_and_member_body(self):
        # C++ 单行：构造器初始化列表、虚析构、成员函数体、命名空间外定义都要能提取
        src = (
            "struct Point { int x, y; };\n"
            "class Widget {\n"
            "public:\n"
            "    Widget() : x_(1), y_(2) {}\n"
            "    Widget(int a) : x_(a), y_(a * 2) {}\n"
            "    virtual ~Widget() {}\n"
            "    void draw() const override;\n"
            "private:\n"
            "    int x_, y_;\n"
            "};\n"
            "void Widget::draw() const { }\n"
        )
        path = self._tmp("widget.cpp", src)
        syms, dt = self._symbols_with_timeout(path)
        for expect in ("Point", "Widget", "draw"):
            self.assertIn(expect, syms, "漏报 %s！符号=%s" % (expect, syms))

    def test_macro_only_file_zero_symbols_fast(self):
        # 纯宏头文件 0 符号是预期，关键是快——符号缺失不应触发灾难性回溯
        path = self._tmp("pure_macros.h", tgmath_style_header(1200))
        syms, dt = self._symbols_with_timeout(path)
        self.assertLess(dt, 3.0, "纯宏头文件应 <3s，实测 %.2fs" % dt)


class LogicFPReliefTest(unittest.TestCase):
    """v4.9.10 修复：tornado 6.1 干净源码回归的 2 条 logic 泛化误报（扣子源码核实）：
      - websocket.py:587 send_error() 有 if self.stream is None 保护 → API契约规则
        未识别保护分支 → 误报。修：方法对 self.stream 有空值比较即豁免。
      - http1connection.py:725 del headers["Content-Encoding"] 在 if headers.get(...)
        条件确认后才 del → 字典键规则未识别条件删除 → 误报。
        修：del 位于含同 key get 的真值条件分支内即豁免。
    关键：豁免只针对「有保护」的写法，真 bug（裸 get→del / 无保护 stream 委托）必须仍报。
    """

    def _logic_issues(self, src):
        d = tempfile.mkdtemp(prefix="tf_v4910_fp_")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        path = os.path.join(d, "app.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(src)
        return [i["type"] for i in detect_logic_issues([path], root=d)["issues"]]

    def test_guarded_del_not_reported(self):
        # tornado http1connection.py:725 惯用法：if d.get(k): del d[k] → 不报
        src = (
            "def drop_gzip(headers):\n"
            "    if headers.get(\"Content-Encoding\") == \"gzip\":\n"
            "        del headers[\"Content-Encoding\"]\n"
        )
        types = self._logic_issues(src)
        self.assertNotIn("字典键不存在访问", types, "保护分支内的 get→del 不应误报")

    def test_unguarded_del_still_reported(self):
        # tornado#3 真 bug：裸 get 后 del 同一 key → 必须仍报
        src = (
            "def fetch(cache, key):\n"
            "    cache.get(key)\n"
            "    del cache[key]\n"
        )
        types = self._logic_issues(src)
        self.assertIn("字典键不存在访问", types, "裸 get→del 真 bug 漏报！")

    def test_guarded_stream_delegate_not_reported(self):
        # tornado websocket.py send_error：if self.stream is None 保护 → API契约不报
        src = (
            "class WS:\n"
            "    def __init__(self):\n"
            "        self.stream = None\n"
            "        self.ws_connection = None\n"
            "    def write_message(self, x):\n"
            "        return self.ws_connection.write_message(x)\n"
            "    def close(self):\n"
            "        return self.ws_connection.close()\n"
            "    def ping(self, x):\n"
            "        return self.ws_connection.ping(x)\n"
            "    def send_error(self, x):\n"
            "        if self.stream is None:\n"
            "            return super().send_error(x)\n"
            "        return self.stream.write(x)\n"
        )
        types = self._logic_issues(src)
        self.assertNotIn("API契约", types, "有 stream None 保护的方法不应误报 API契约")

    def test_unguarded_stream_delegate_still_reported(self):
        # tornado#1 真 bug：set_nodelay 无保护直接 self.stream → 必须仍报
        src = (
            "class WS:\n"
            "    def __init__(self):\n"
            "        self.stream = None\n"
            "        self.ws_connection = None\n"
            "    def write_message(self, x):\n"
            "        return self.ws_connection.write_message(x)\n"
            "    def close(self):\n"
            "        return self.ws_connection.close()\n"
            "    def ping(self, x):\n"
            "        return self.ws_connection.ping(x)\n"
            "    def set_nodelay(self, x):\n"
            "        return self.stream.set_nodelay(x)\n"
        )
        types = self._logic_issues(src)
        self.assertIn("API契约", types, "无保护 stream 委托真 bug 漏报！")

    def test_mixed_guard_only_relieves_protected_method(self):
        # 同一类里：send_error（有保护）不报、set_nodelay（无保护）仍报——按方法粒度豁免
        src = (
            "class WS:\n"
            "    def __init__(self):\n"
            "        self.stream = None\n"
            "        self.ws_connection = None\n"
            "    def write_message(self, x):\n"
            "        return self.ws_connection.write_message(x)\n"
            "    def close(self):\n"
            "        return self.ws_connection.close()\n"
            "    def ping(self, x):\n"
            "        return self.ws_connection.ping(x)\n"
            "    def send_error(self, x):\n"
            "        if self.stream is None:\n"
            "            return super().send_error(x)\n"
            "        return self.stream.write(x)\n"
            "    def set_nodelay(self, x):\n"
            "        return self.stream.set_nodelay(x)\n"
        )
        types = self._logic_issues(src)
        self.assertIn("API契约", types, "无保护 stream 委托仍应报")
        self.assertEqual(types.count("API契约"), 1, "只应报 set_nodelay 一条，实际 %s" % types)

    def test_assert_stream_delegate_still_reported(self):
        # v4.9.17 回归修复（扣子复测发现）：tornado#1 真实写法
        # `assert self.stream is not None` 是防御性断言，恰恰暴露 stream 可能为 None，
        # 不属于 if 空值保护惯用法 → API契约 必须仍报（此前被 _has_stream_none_guard 误豁免）
        src = (
            "class WS:\n"
            "    def __init__(self):\n"
            "        self.stream = None\n"
            "        self.ws_connection = None\n"
            "    def write_message(self, x):\n"
            "        return self.ws_connection.write_message(x)\n"
            "    def close(self):\n"
            "        return self.ws_connection.close()\n"
            "    def ping(self, x):\n"
            "        return self.ws_connection.ping(x)\n"
            "    def set_nodelay(self, x):\n"
            "        assert self.stream is not None\n"
            "        return self.stream.set_nodelay(x)\n"
        )
        types = self._logic_issues(src)
        self.assertIn("API契约", types, "assert 防御性断言不应豁免 API契约（tornado#1 回归）")

    def test_assert_and_if_mixed_only_relieves_if_guarded(self):
        # 同一类里：send_error（if 保护）不报、set_nodelay（assert 防御断言）仍报——只豁免 if 惯用法
        src = (
            "class WS:\n"
            "    def __init__(self):\n"
            "        self.stream = None\n"
            "        self.ws_connection = None\n"
            "    def write_message(self, x):\n"
            "        return self.ws_connection.write_message(x)\n"
            "    def close(self):\n"
            "        return self.ws_connection.close()\n"
            "    def ping(self, x):\n"
            "        return self.ws_connection.ping(x)\n"
            "    def send_error(self, x):\n"
            "        if self.stream is None:\n"
            "            return super().send_error(x)\n"
            "        return self.stream.write(x)\n"
            "    def set_nodelay(self, x):\n"
            "        assert self.stream is not None\n"
            "        return self.stream.set_nodelay(x)\n"
        )
        types = self._logic_issues(src)
        self.assertIn("API契约", types, "assert 防御断言场景 API契约 应报")
        self.assertEqual(types.count("API契约"), 1, "只应报 set_nodelay 一条，实际 %s" % types)


if __name__ == "__main__":
    unittest.main()
