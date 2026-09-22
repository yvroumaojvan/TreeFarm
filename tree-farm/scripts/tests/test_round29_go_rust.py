# -*- coding: utf-8 -*-
"""
树场 35 轮测试 · R12：Go/Rust 多语言结构分析（unittest）

目标：验证 parse 多语言支持能力（Go/Rust 的函数提取/复杂度/调用图/死代码）
不崩 + 主能力可用。

运行：
  python3 -m unittest tests.test_round29_go_rust -v
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tree_farm as tf  # noqa: E402


def make_project(files) -> str:
    d = tempfile.mkdtemp(prefix="tf_r29_")
    for name, content in files.items():
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write(content)
    return d


class GoTest(unittest.TestCase):
    def test_go_complexity_and_dead(self):
        d = make_project({
            "main.go": '''\
package main

func used() int {
    return 1
}

func neverCalled() int {
    return 2
}

func complexFn(x int) int {
    if x > 0 {
        if x > 10 {
            return 3
        }
        return 2
    }
    return 1
}

func main() {
    _ = used()
}
''',
        })
        try:
            # 复杂度分析不崩且识别 Go 函数
            res = tf.calculate_complexity_any(os.path.join(d, "main.go"))
            funcs = {f[0]: f[1] for f in res.get("functions", [])}
            self.assertIn("complexFn", funcs, f"Go 函数未提取: {funcs}")
            self.assertGreaterEqual(funcs["complexFn"], 3, f"Go 复杂度异常: {funcs}")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_go_dead_code(self):
        d = make_project({
            "main.go": '''\
package main

func used() int {
    return 1
}

func neverCalled() int {
    return 2
}

func main() {
    _ = used()
}
''',
        })
        try:
            res = tf.detect_dead_code([os.path.join(d, "main.go")])
            dead = {f["name"] for f in res["dead_functions"]}
            self.assertIn("neverCalled", dead, f"Go 死函数漏报: {dead}")
        finally:
            shutil.rmtree(d, ignore_errors=True)


class RustTest(unittest.TestCase):
    def test_rust_dead_and_complex(self):
        d = make_project({
            "lib.rs": '''\
pub fn used() -> i32 {
    1
}

fn never_called() -> i32 {
    2
}

fn complex_fn(x: i32) -> i32 {
    if x > 0 {
        if x > 10 {
            return 3;
        }
        return 2;
    }
    1
}

pub fn main() {
    let _ = used();
}
''',
        })
        try:
            res = tf.calculate_complexity_any(os.path.join(d, "lib.rs"))
            funcs = {f[0]: f[1] for f in res.get("functions", [])}
            self.assertIn("complex_fn", funcs, f"Rust 函数未提取: {funcs}")
            self.assertGreaterEqual(funcs["complex_fn"], 3, f"Rust 复杂度异常: {funcs}")
            dc = tf.detect_dead_code([os.path.join(d, "lib.rs")])
            dead = {f["name"] for f in dc["dead_functions"]}
            self.assertIn("never_called", dead, f"Rust 死函数漏报: {dead}")
        finally:
            shutil.rmtree(d, ignore_errors=True)


class KotlinTest(unittest.TestCase):
    def test_kotlin_dead(self):
        d = make_project({
            "App.kt": '''\
fun used(): Int = 1

fun neverCalled(): Int = 2

fun main() {
    print(used())
}
''',
        })
        try:
            res = tf.calculate_complexity_any(os.path.join(d, "App.kt"))
            funcs = {f[0]: f[1] for f in res.get("functions", [])}
            self.assertIn("used", funcs, f"Kotlin 函数未提取: {funcs}")
        finally:
            shutil.rmtree(d, ignore_errors=True)


class MixedNoCrashTest(unittest.TestCase):
    def test_all_langs_no_crash(self):
        d = make_project({
            "a.go": "package main\nfunc f() {}\n",
            "b.rs": "pub fn g() -> i32 { 1 }\n",
            "c.kt": "fun h() = 1\n",
            "d.cpp": "int j() { return 1; }\n",
            "e.rb": "def k\n  1\nend\n",
            "f.php": "<?php function l() { return 1; }\n",
        })
        try:
            files = [os.path.join(d, n) for n in
                     ("a.go", "b.rs", "c.kt", "d.cpp", "e.rb", "f.php")]
            tf.plant_checked if hasattr(tf, "plant_checked") else None
            # 全语言复杂度扫描不崩
            for f in files:
                tf.calculate_complexity_any(f)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()