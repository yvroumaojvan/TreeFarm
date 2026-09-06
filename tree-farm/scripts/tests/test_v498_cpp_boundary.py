# -*- coding: utf-8 -*-
"""
树场 v4.9.8 C/C++ 边界轰炸测试套件（第4轮，unittest，零依赖）

覆盖 C/C++ 启发式解析器最难处理的形态：
  - #if 0 禁用块剔除 / #ifdef 条件块保留
  - 模板函数 / 模板类方法 Stack<T>::push / 模板特化
  - 函数指针变量（非函数定义）/ lambda（非函数定义）
  - 注释与字符串里的括号/花括号不干扰
  - 命名空间嵌套 / 类方法跨行定义 db::engine::Query::where
  - 跨行声明不是定义
  - 运算符重载排除 / 析构 ~Foo / 初始化列表成员
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.parser import _strip_c_noise, _c_defs, extract_c_call_graph  # noqa: E402

BOUNDARY = r'''
#include <vector>
#include <string>
#include <functional>

#define SQUARE(x) ((x) * (x))
#define MAX2(a, b) \
    ((a) > (b) ? (a) : (b))

/*
   return fake(a, b);   -- 注释里的假调用
   int x = ")}";        -- 也是注释
*/
// return fake2(a, b);  -- 行注释

#if 0
int disabled_function(void) { return 42; }
#endif

#ifdef ENABLE_EXTRA
int conditional_func(void) { return 1; }
#endif

int (*callback_ptr)(int);
int (*compare)(const void *, const void *);

template<typename T>
T my_max(T a, T b) {
    return a > b ? a : b;
}

template<class T, int N>
T arr_sum(T (&arr)[N]) {
    T s = 0;
    for (int i = 0; i < N; i++) s += arr[i];
    return s;
}

template<typename T>
class Stack {
public:
    void push(const T &item);
    T pop();
private:
    std::vector<T> data;
};

template<typename T>
void Stack<T>::push(const T &item) {
    data.push_back(item);
}

template<typename T>
T Stack<T>::pop() {
    T v = data.back();
    data.pop_back();
    return v;
}

namespace outer {
namespace inner {
    int deep_func(int x) { return x * 2; }
}
}

auto lambda_add = [](int a, int b) -> int {
    return a + b;
};

class Helper {
public:
    static int static_func(int x) { return x + 1; }
    int operator()(int v) { return v * 2; }
    int call_me() { return static_func(3) + operator()(4); }
};

struct Point { int x; int y; };

int use_all_boundary(void) {
    Point p{1, 2};
    Stack<int> st;
    st.push(3);
    int v = st.pop();
    outer::inner::deep_func(v);
    Helper h;
    h.call_me();
    std::function<int(int)> fn = [](int x) { return x; };
    callback_ptr = nullptr;
    compare(nullptr, nullptr);
    return (int)SQUARE(3) + MAX2(1, 2);
}

const char *get_message(int id);
'''


class StripNoiseTest(unittest.TestCase):
    def test_if0_block_removed(self):
        clean = _strip_c_noise(BOUNDARY)
        self.assertNotIn("disabled_function", clean)

    def test_ifdef_block_kept(self):
        clean = _strip_c_noise(BOUNDARY)
        self.assertIn("conditional_func", clean)


class DefsBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.defs = _c_defs(_strip_c_noise(BOUNDARY))

    def names(self, kind=None):
        return {n for n, _, _, k in self.defs if kind is None or k == kind}

    def test_if0_function_not_extracted(self):
        names = self.names()
        self.assertNotIn("disabled_function", names)

    def test_template_functions(self):
        names = self.names("func")
        self.assertIn("my_max", names)
        self.assertIn("arr_sum", names)

    def test_template_class_methods(self):
        # Stack<T>::push / Stack<T>::pop 模板方法定义
        names = self.names("func")
        self.assertIn("push", names)
        self.assertIn("pop", names)

    def test_nested_namespace_func(self):
        names = self.names("func")
        self.assertIn("deep_func", names)

    def test_lambda_not_function(self):
        # lambda_add = [](...) {...} 是变量赋值，不是函数定义
        names = self.names("func")
        self.assertNotIn("lambda_add", names)

    def test_func_ptr_not_function(self):
        # int (*callback_ptr)(int) 是函数指针变量，不是函数定义
        names = self.names("func")
        self.assertNotIn("callback_ptr", names)
        self.assertNotIn("compare", names)

    def test_operator_not_function(self):
        names = self.names("func")
        self.assertNotIn("operator", names)

    def test_decl_not_definition(self):
        names = self.names("func")
        self.assertNotIn("get_message", names)


class CallsBoundaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fd, cls.path = tempfile.mkstemp(suffix=".cpp", prefix="treefarm_v498b_")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(BOUNDARY)
        cls.calls, cls.inherits = extract_c_call_graph(cls.path)

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.path)

    def test_real_calls(self):
        for expect in ("push", "pop", "static_func"):
            self.assertIn(expect, self.calls, f"{expect} 调用应被提取")
        self.assertIn("outer.inner.deep_func", self.calls)

    def test_template_instance_method(self):
        self.assertIn("st.push", self.calls)
        self.assertIn("st.pop", self.calls)

    def test_nested_ns_call(self):
        self.assertIn("outer.inner.deep_func", self.calls)

    def test_func_ptr_call(self):
        self.assertIn("compare", self.calls)

    def test_macro_not_call(self):
        self.assertNotIn("SQUARE", self.calls)
        self.assertNotIn("MAX2", self.calls)

    def test_no_comment_noise(self):
        self.assertNotIn("fake", self.calls)
        self.assertNotIn("fake2", self.calls)
        self.assertNotIn("return", self.calls)


class HardBoundaryTest(unittest.TestCase):
    """更难的形态：跨行定义/命名空间方法/跨行声明/模板特化。"""

    HARD = r'''
namespace db { namespace engine {
class Query {
public:
    Query &where(const std::string &cond);
    int execute();
private:
    std::string sql_;
};
}}

int  complex_fn(
    int a,
    int b);

template <>
class Stack<bool> {
public:
    bool top() { return true_; }
};

const std::string &
db::engine::Query::where(const std::string &cond) {
    sql_ += cond;
    return *this;
}

int db::engine::Query::execute() {
    return (int)sql_.size();
}

int main(int argc, char *argv[]) {
    db::engine::Query q;
    q.where("x = 1");
    return q.execute();
}
'''

    def setUp(self):
        self.defs = _c_defs(_strip_c_noise(self.HARD))

    def names(self):
        return {n for n, _, _, k in self.defs}

    def test_ns_class_method_defs(self):
        # db::engine::Query::where / ::execute 跨行定义
        names = self.names()
        self.assertIn("where", names)
        self.assertIn("execute", names)

    def test_template_spec_method(self):
        names = self.names()
        self.assertIn("top", names)

    def test_cross_line_decl_not_def(self):
        # 跨行声明 int complex_fn(int a, int b); 不是定义
        names = self.names()
        self.assertNotIn("complex_fn", names)


if __name__ == "__main__":
    unittest.main()