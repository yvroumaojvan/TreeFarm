# -*- coding: utf-8 -*-
"""
树场 v4.9.8 C/C++ 函数级分析测试套件（unittest，零依赖）

覆盖（第1轮）：
  - 函数/方法定义提取：基本形态/指针返回/类内联/构造(初始化列表)/析构/命名空间/
    override/模板-占位[第4轮补]/运算符重载排除/宏调用排除/纯虚声明排除
  - 调用图：真实调用（obj.method / ns::func / 嵌套）/ 排除声明/析构/初始化列表/
    控制流关键字/宏
  - 类继承：class X : public Y
  - 圈复杂度(_count_complexity)：if/for/while/switch/case/&&/||/三元
  - 死代码检测：C 函数被调用不算死代码，未被调用算死代码

运行：
  python3 -m unittest discover -s scripts/tests -v
"""
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_dead_code  # noqa: E402
from treefarm.parser import (_count_complexity, _strip_c_noise, _c_defs,
                             extract_c_call_graph, _func_body_end)  # noqa: E402

CPP_SAMPLE = r'''
#include <stdio.h>
#include <string.h>

#define MAX(a, b) ((a) > (b) ? (a) : (b))

static int helper(int x) {
    return x * 2;
}

int add(int a, int b) {
    return a + b;
}

int *get_ptr(void) {
    static int v = 0;
    return &v;
}

class Base {
public:
    virtual void run() = 0;
    virtual ~Base() {}
};

class Worker : public Base {
public:
    Worker() : count_(0) {}
    ~Worker() {}
    void run() override {
        execute_task();
    }
    void execute_task() {
        count_ += 1;
    }
private:
    int count_ = 0;
};

namespace myns {
    int ns_func(int x) { return x + 1; }
}

int use_all(void) {
    int a = add(1, helper(2));
    int *p = get_ptr();
    Worker w;
    w.run();
    w.execute_task();
    myns::ns_func(a);
    printf("%d %d", a, *p);
    return MAX(3, 4);
}

int unused_function(void) {
    int i = 0;
    for (i = 0; i < 10; i++) {
        if (i % 2 == 0) continue;
    }
    return i;
}

int declaration_only(int x);
'''


class DefsExtractionTest(unittest.TestCase):
    """_c_defs 定义提取准确性。"""

    def setUp(self):
        self.defs = _c_defs(_strip_c_noise(CPP_SAMPLE))

    def names(self, kind=None):
        return {n for n, _, _, k in self.defs if kind is None or k == kind}

    def test_basic_functions(self):
        names = self.names("func")
        for expect in ("add", "helper", "get_ptr", "use_all", "unused_function",
                       "execute_task", "run", "ns_func"):
            self.assertIn(expect, names, f"{expect} 应被提取为函数")

    def test_constructor_destructor(self):
        names = self.names("func")
        self.assertIn("Worker", names)     # Worker::Worker()
        self.assertIn("~Worker", names)    # Worker::~Worker()
        self.assertIn("~Base", names)      # virtual ~Base() {}

    def test_classes(self):
        classes = self.names("class")
        self.assertIn("Base", classes)
        self.assertIn("Worker", classes)

    def test_declaration_not_definition(self):
        # declaration_only(...); 是 .h 原型 → 不算定义
        names = self.names("func")
        self.assertNotIn("declaration_only", names)

    def test_macro_not_function(self):
        # MAX(a,b) 宏调用不是函数定义
        names = self.names("func")
        self.assertNotIn("MAX", names)

    def test_no_control_flow(self):
        names = self.names("func")
        for kw in ("if", "for", "while", "switch"):
            self.assertNotIn(kw, names, f"{kw} 控制流不能当函数")


class CallsExtractionTest(unittest.TestCase):
    """extract_c_call_graph 调用图。"""

    def setUp(self):
        self.calls, self.inherits = extract_c_call_graph(
            self._make_file(CPP_SAMPLE))

    @staticmethod
    def _make_file(content):
        fd, path = tempfile.mkstemp(suffix=".cpp", prefix="treefarm_v498_")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def tearDown(self):
        pass

    def test_real_calls_present(self):
        for expect in ("add", "helper", "get_ptr", "execute_task", "printf"):
            self.assertIn(expect, self.calls, f"{expect} 真实调用应被提取")

    def test_obj_method_call(self):
        self.assertIn("w.run", self.calls)
        self.assertIn("w.execute_task", self.calls)

    def test_ns_call(self):
        self.assertIn("myns.ns_func", self.calls)

    def test_decl_not_call(self):
        self.assertNotIn("declaration_only", self.calls)
        self.assertNotIn("run", self.calls)   # 纯虚声明行

    def test_macro_not_call(self):
        self.assertNotIn("MAX", self.calls)

    def test_keyword_not_call(self):
        for kw in ("if", "for", "while", "sizeof"):
            self.assertNotIn(kw, self.calls)

    def test_inherits(self):
        self.assertEqual(["Base"], self.inherits)


class ComplexityTest(unittest.TestCase):
    """圈复杂度 C/C++。"""

    def test_simple(self):
        self.assertEqual(1, _count_complexity("int f() { return 0; }", "c"))

    def test_if_for_and(self):
        body = "if (a) { for(;;) if (b&&c) {} }"
        self.assertEqual(1 + 2 + 1 + 1, _count_complexity(body, "cpp"))

    def test_switch_case(self):
        body = "switch (x) { case 1: break; case 2: break; default: break; }"
        # 1(基础) + 1(switch) + 2(case) = 4（McCabe：default 不计分支）
        self.assertEqual(4, _count_complexity(body, "cpp"))

    def test_ternary(self):
        body = "int m = a ? b : c;"
        self.assertEqual(2, _count_complexity(body, "c"))


class DeadCodeCTest(unittest.TestCase):
    """死代码检测：C/C++ 接入。"""

    def test_unused_function_detected(self):
        d = tempfile.mkdtemp(prefix="treefarm_v498_")
        try:
            path = os.path.join(d, "sample.c")
            with open(path, "w", encoding="utf-8") as f:
                f.write(CPP_SAMPLE)
            res = detect_dead_code([path])
            funcs = {d["name"] for d in res["dead_functions"]}
            self.assertIn("unused_function", funcs, f"未被调用的函数应是死代码: {funcs}")
            self.assertNotIn("add", funcs, "被调用的 add 不应是死代码")
            self.assertNotIn("get_ptr", funcs, "被调用的 get_ptr 不应是死代码")
            # 类被使用（Worker w; 实例化）→ 构造/析构免报死代码
            self.assertNotIn("Worker", funcs, "实例化的类构造不该报死代码")
            self.assertNotIn("~Worker", funcs, "被使用类的析构不该报死代码")
            # use_all 无人调用 → 按 C 语义确是死代码（入口白名单外）
            self.assertIn("use_all", funcs, "无人调用应报死代码候选")
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def test_static_function_high_confidence(self):
        # v4.9.8：C static 函数无人调用 = 高置信度死代码（文件私有）
        d = tempfile.mkdtemp(prefix="treefarm_v498_")
        try:
            path = os.path.join(d, "priv.c")
            with open(path, "w", encoding="utf-8") as f:
                f.write('''\
static int orphan_impl(int q) {
    return q * q;
}

int used_func(void) {
    return 42;
}

int main(int argc, char **argv) {
    return used_func();
}
''')
            res = detect_dead_code([path])
            by_name = {d["name"]: d for d in res["dead_functions"]}
            self.assertIn("orphan_impl", by_name)
            self.assertEqual("high", by_name["orphan_impl"]["confidence"])
            self.assertNotIn("used_func", by_name)
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def test_detects_functions_total(self):
        d = tempfile.mkdtemp(prefix="treefarm_v498_")
        try:
            path = os.path.join(d, "sample.c")
            with open(path, "w", encoding="utf-8") as f:
                f.write(CPP_SAMPLE)
            res = detect_dead_code([path])
            self.assertGreaterEqual(res["total_functions"], 8, res)
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)


class ComplexityIntegrationTest(unittest.TestCase):
    """复杂度 CLI 全链路：C/C++ 函数级复杂度接入。"""

    def test_complex_file_max(self):
        from treefarm.analysis import calculate_complexity_any
        d = tempfile.mkdtemp(prefix="treefarm_v498_")
        try:
            path = os.path.join(d, "cmp.c")
            with open(path, "w", encoding="utf-8") as f:
                f.write('''\
int complex_math(int x) {
    int t = 0;
    for (int i = 0; i < x; i++) {
        if (i % 2 == 0) t += 1;
        else if (i % 3 == 0) t -= 1;
        else t ^= i;
    }
    switch (x % 4) {
        case 0: t += 10; break;
        case 1: t += 20; break;
        default: t += 30;
    }
    return t;
}
''')
            res = calculate_complexity_any(path)
            self.assertIsNotNone(res)
            self.assertEqual(1, res["total_functions"])
            self.assertEqual(7, res["max_complexity"], res)  # for+if+elseif+and+switch+2case = 7
            fn = res["functions"][0]
            self.assertEqual("complex_math", fn[0])
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)


class CrossFileGeneTest(unittest.TestCase):
    """跨文件函数级基因：main.c 调 math_utils.c 的 add → call 基因入库。"""

    def test_cross_file_call_gene(self):
        import shutil
        import sqlite3
        from treefarm.core import TreeFarm
        d = tempfile.mkdtemp(prefix="treefarm_v498_")
        try:
            os.makedirs(os.path.join(d, "src"))
            with open(os.path.join(d, "src", "math_utils.c"), "w") as f:
                f.write('''\
int add(int a, int b) { return a + b; }
int unused_math(void) { return 0; }
''')
            with open(os.path.join(d, "src", "main.c"), "w") as f:
                f.write('''\
extern int add(int a, int b);
int main(void) { return add(1, 2); }
''')
            farm = TreeFarm(d)
            ok, stats = farm.plant()
            self.assertTrue(ok, "冷启动建库应成功")
            self.assertGreater(stats["added"]["call"], 0,
                               f"跨文件 call 基因应入库: {stats}")
            db_path = os.path.join(d, ".tree_farm", "tree_farm.db")
            conn = sqlite3.connect(db_path)
            rows = conn.execute(
                "SELECT source,target,symbol,relation FROM genes WHERE relation='call'").fetchall()
            conn.close()
            syms = {(os.path.basename(s), os.path.basename(t), sy)
                    for s, t, sy, _ in rows}
            self.assertIn(("main.c", "math_utils.c", "add"), syms,
                          f"main.c→math_utils.c add 应入库: {syms}")
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()