# -*- coding: utf-8 -*-
"""
树场机制 v3.0 测试套件（unittest，零依赖，保持项目零依赖卖点）

运行：
  python3 -m unittest discover -s tests -v
  或直接 python3 tests/test_tree_farm.py

覆盖：基因提取（多语言/函数级/继承/相对导入）、小虫子验证、语义搜索（归一化/索引）、
GeneBank（SQLite 增删改查/迁移/恢复）、垃圾箱滑动窗口熔断、Session、增量扫描
（新增/修改/删除/重命名）、TreeFarm 全流程、配置定位。
"""
import gc
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tree_farm as tf  # noqa: E402


def make_project(files: dict) -> str:
    """在临时目录造一个小项目。files: {相对路径: 内容}"""
    root = tempfile.mkdtemp(prefix="treefarm_test_")
    for rel, content in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    return root


class TestNormalizeGene(unittest.TestCase):
    def test_v2_old_format_upgrade(self):
        g = tf.normalize_gene({"source": "/x/a.py", "target": "b",
                               "relation": "import", "kind": "strong",
                               "confidence": 1.0, "verified": True, "ts": 100.0})
        self.assertEqual(g["source"], "/x/a.py")
        self.assertEqual(g["kind"], "strong")
        self.assertTrue(g["verified"])
        self.assertEqual(g["first_seen"], 100.0)
        self.assertIn("symbol", g)          # 缺字段补默认
        self.assertIn("direction", g)
        self.assertIn("last_verified", g)

    def test_weak_kind_defaults(self):
        g = tf.normalize_gene({"source": "/x/a.py", "target": "b", "kind": "weak"})
        self.assertEqual(g["kind"], "weak")
        self.assertFalse(g["verified"])     # weak 默认未验证
        self.assertLessEqual(g["confidence"], 0.5)

    def test_stable_id_deterministic(self):
        a = tf.stable_id("/x/a.py", "b", "sym", "call")
        b = tf.stable_id("/x/a.py", "b", "sym", "call")
        self.assertEqual(a, b)
        self.assertNotEqual(a, tf.stable_id("/x/a.py", "c", "sym", "call"))

    def test_old_id_kept(self):
        g = tf.normalize_gene({"id": "g_custom", "source": "a", "target": "b"})
        self.assertEqual(g["id"], "g_custom")


class TestExtractGenes(unittest.TestCase):
    def test_python_imports(self):
        root = make_project({"a.py": "import os\nimport json as j\nfrom utils import helpers\n"})
        found = tf.extract_genes(os.path.join(root, "a.py"))
        targets = {t for t, _ in found}
        self.assertIn("os", targets)
        self.assertIn("json", targets)
        self.assertIn("utils", targets)

    def test_python_relative_import(self):
        root = make_project({"a.py": "from . import x\nfrom ..pkg import y\n"})
        found = tf.extract_genes(os.path.join(root, "a.py"))
        targets = {t for t, _ in found}
        self.assertIn(".", targets)        # from . import x
        self.assertIn("..pkg", targets)    # from ..pkg import y

    def test_python_syntax_error_returns_empty(self):
        root = make_project({"bad.py": "def broken(:\n  pass\n"})
        self.assertEqual(tf.extract_genes(os.path.join(root, "bad.py")), [])

    def test_js_require_and_dynamic_import(self):
        root = make_project({"a.js": "const x = require('./utils');\nimport('./lazy').then(m => m.run());\n"})
        found = tf.extract_genes(os.path.join(root, "a.js"))
        targets = {t for t, _ in found}
        self.assertIn("./utils", targets)
        self.assertIn("./lazy", targets)   # 动态 import()（v3 新增）

    def test_java_and_go(self):
        root = make_project({"A.java": "import com.example.utils.Helper;\n",
                             "b.go": "import (\n  \"fmt\"\n  \"os\"\n)\n"})
        j = tf.extract_genes(os.path.join(root, "A.java"))
        self.assertIn("com.example.utils.Helper", {t for t, _ in j})
        g = tf.extract_genes(os.path.join(root, "b.go"))
        go_targets = {t for t, _ in g}
        self.assertIn("fmt", go_targets)
        self.assertIn("os", go_targets)


class TestExtractCallGraph(unittest.TestCase):
    def test_function_calls(self):
        root = make_project({"a.py": """
import utils.helpers

def run():
    utils.helpers.greet("hi")
    local_call()
    obj.method()
"""})
        calls, inherits = tf.extract_call_graph(os.path.join(root, "a.py"))
        self.assertIn("utils.helpers.greet", calls)
        self.assertIn("local_call", calls)
        self.assertIn("obj.method", calls)
        self.assertEqual(inherits, [])

    def test_class_inheritance(self):
        root = make_project({"a.py": """
from base import Base

class Child(Base):
    pass

class Grand(Child):
    pass
"""})
        calls, inherits = tf.extract_call_graph(os.path.join(root, "a.py"))
        self.assertIn("Base", inherits)
        self.assertIn("Child", inherits)


class TestVerifyCandidate(unittest.TestCase):
    def test_python_import_hit(self):
        root = make_project({"a.py": "import utils.helpers\nutils.helpers.greet()\n"})
        self.assertTrue(tf.verify_candidate(os.path.join(root, "a.py"), "utils.helpers"))

    def test_python_attribute_hit(self):
        root = make_project({"a.py": "x = config.THEME\n"})
        self.assertTrue(tf.verify_candidate(os.path.join(root, "a.py"), "config"))

    def test_python_miss(self):
        root = make_project({"a.py": "import os\n"})
        self.assertFalse(tf.verify_candidate(os.path.join(root, "a.py"), "no.such"))

    def test_non_python_text(self):
        root = make_project({"a.java": "Helper.run();\n"})
        self.assertTrue(tf.verify_candidate(os.path.join(root, "a.java"), "com.Helper"))


class TestSemanticSearch(unittest.TestCase):
    def test_identifier_normalization(self):
        self.assertEqual(tf.normalize_identifier("UserAuth"), "user_auth")
        self.assertEqual(tf.normalize_identifier("userAuth"), "user_auth")
        self.assertEqual(tf.normalize_identifier("user-auth"), "user_auth")

    def test_exact_symbol_hit(self):
        root = make_project({"a.py": "def greet():\n    return 1\n",
                             "b.py": "def other():\n    return 2\n"})
        idx = tf.SymbolIndex({"greet": [os.path.join(root, "a.py")]})
        hits = tf.semantic_search("greet", [], idx)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][1], 1.0)

    def test_normalized_symbol_hit(self):
        root = make_project({"a.py": "def UserAuth():\n    pass\n"})
        idx = tf.SymbolIndex({"UserAuth": [os.path.join(root, "a.py")]})
        hits = tf.semantic_search("user_auth", [], idx)
        self.assertEqual(len(hits), 1)

    def test_content_index_hit(self):
        root = make_project({"a.py": "def handle_payment():\n    return 'paid'\n",
                             "b.py": "def unrelated():\n    return 0\n"})
        idx = tf.SymbolIndex({})
        content = {os.path.join(root, "a.py"): tf._gram_hashes("def handle_payment"),
                   os.path.join(root, "b.py"): tf._gram_hashes("def unrelated")}
        hits = tf.semantic_search("payment", [os.path.join(root, "a.py"),
                                              os.path.join(root, "b.py")], idx, content)
        self.assertTrue(hits)
        self.assertEqual(hits[0][0], os.path.join(root, "a.py"))


class TestGeneBank(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="treefarm_bank_")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _g(self, source, target, rel="import", kind="strong"):
        return {"source": source, "target": target, "relation": rel,
                "kind": kind, "confidence": 1.0, "verified": True, "ts": 1.0}

    def test_add_dedup(self):
        bank = tf.GeneBank(self.root)
        self.assertTrue(bank.add(self._g("a.py", "b")))
        self.assertFalse(bank.add(self._g("a.py", "b")))   # 去重
        self.assertEqual(len(bank.all_genes()), 1)

    def test_eat(self):
        bank = tf.GeneBank(self.root)
        bank.add(self._g("a.py", "b"))
        self.assertTrue(bank.eat("a.py", "b"))
        self.assertFalse(bank.eat("a.py", "b"))
        self.assertEqual(len(bank.all_genes()), 0)

    def test_persistence_reopen(self):
        bank = tf.GeneBank(self.root)
        bank.add(self._g("a.py", "b"))
        bank2 = tf.GeneBank(self.root)
        self.assertEqual(len(bank2.all_genes()), 1)

    def test_close_releases_connection(self):
        """修复：close() 后连接释放，再次使用应抛错而非泄漏"""
        bank = tf.GeneBank(self.root)
        bank.add(self._g("a.py", "b"))
        bank.close()
        with self.assertRaises(sqlite3.ProgrammingError):
            bank.all_genes()

    def test_del_closes_without_warning(self):
        """修复：未显式 close 时 __del__ 兜底关闭，不产生 ResourceWarning"""
        bank = tf.GeneBank(self.root)
        bank.add(self._g("a.py", "b"))
        del bank
        gc.collect()

    def test_drop_file_cascade(self):
        bank = tf.GeneBank(self.root)
        # a.py 依赖 b.py（路径级），c.py 依赖 utils.b（模块名级）
        bank.add(self._g("/p/a.py", "/p/b.py", "call"))
        bank.add(self._g("/p/c.py", "utils.b", "import"))
        n = bank.drop_file("/p/b.py", ["utils.b"])
        self.assertEqual(n, 2)           # 两条都该被级联回收
        self.assertEqual(len(bank.all_genes()), 0)

    def test_rename_source_and_target(self):
        bank = tf.GeneBank(self.root)
        bank.add(self._g("/p/a.py", "/p/b.py"))
        n = bank.rename_source("/p/a.py", "/p/a2.py")
        self.assertEqual(n, 1)
        g = bank.all_genes()[0]
        self.assertEqual(g["source"], "/p/a2.py")   # source 迁移
        self.assertEqual(g["target"], "/p/b.py")

    def test_json_v2_migration(self):
        """v2 JSON 格式 → SQLite 自动迁移"""
        store = os.path.join(self.root, ".tree_farm")
        os.makedirs(store)
        old = [{"source": "/x/a.py", "target": "b", "relation": "import",
                "kind": "strong", "confidence": 1.0, "verified": True, "ts": 1.0}]
        with open(os.path.join(store, "gene_bank.json"), "w", encoding="utf-8") as f:
            import json
            json.dump({"schema_version": 2, "genes": old}, f)
        bank = tf.GeneBank(self.root)
        self.assertEqual(len(bank.all_genes()), 1)
        self.assertFalse(os.path.isfile(os.path.join(store, "gene_bank.json")))
        self.assertTrue(os.path.isfile(os.path.join(store, "gene_bank.json.bak")))

    def test_db_lost_recover_from_bak(self):
        """db 丢失但 .bak 还在 → 自动恢复（数据不丢）"""
        store = os.path.join(self.root, ".tree_farm")
        os.makedirs(store)
        import json
        old = [{"source": "/x/a.py", "target": "b", "relation": "import",
                "kind": "strong", "confidence": 1.0, "verified": True, "ts": 1.0}]
        with open(os.path.join(store, "gene_bank.json"), "w", encoding="utf-8") as f:
            json.dump({"schema_version": 2, "genes": old}, f)
        bank = tf.GeneBank(self.root)   # 迁移 → .bak
        bank.conn.close()
        os.remove(os.path.join(store, tf.DB_FILE))   # 模拟 db 丢失
        bank2 = tf.GeneBank(self.root)  # 应从 .bak 恢复
        self.assertEqual(len(bank2.all_genes()), 1)


class TestTrashBin(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="treefarm_trash_")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_sliding_window_fuse(self):
        """滑动窗口：最近 3 次里 ≥2 次失败 → 熔断"""
        bank = tf.GeneBank(self.root)
        tb = tf.TrashBin(bank)
        self.assertFalse(tb.record("/p/a.py", True, "假1"))
        self.assertTrue(tb.record("/p/a.py", True, "假2"))   # 2 次失败 → 熔断
        self.assertIn("/p/a.py", tb.trash())
        self.assertFalse(tb.is_empty())

    def test_success_recovery_in_window(self):
        """失败2次后成功1次：窗口滑动，3 次里只有 2 次失败但仍触发？
        v3 定义：最近 3 次中 ≥2 次失败即熔断。失败+成功+失败 = 2/3 → 熔断"""
        bank = tf.GeneBank(self.root)
        tb = tf.TrashBin(bank)
        tb.record("/p/a.py", True, "假1")
        tb.record("/p/a.py", False)               # 成功
        self.assertFalse(tb.trash())              # 1/2 未达阈值
        self.assertTrue(tb.record("/p/a.py", True, "假3"))  # 2/3 → 熔断

    def test_reset(self):
        bank = tf.GeneBank(self.root)
        tb = tf.TrashBin(bank)
        tb.record("/p/a.py", True)
        tb.record("/p/a.py", True)
        tb.reset("/p/a.py")
        self.assertTrue(tb.is_empty())


class TestSession(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="treefarm_session_")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_round_and_converge(self):
        bank = tf.GeneBank(self.root)
        s = tf.Session(bank)
        self.assertEqual(s.data["round"], 1)
        s.add_bird()
        s.add_bird()
        self.assertTrue(s.converged())       # 2 只 ≤ 阈值
        s.add_bird()
        self.assertFalse(s.converged())      # 3 只 > 阈值
        s.next_round()
        self.assertEqual(s.data["round"], 2)
        self.assertEqual(s.data["birds_this_round"], 0)

    def test_llm_switch_persist(self):
        bank = tf.GeneBank(self.root)
        s = tf.Session(bank)
        s.set_llm(False)
        s2 = tf.Session(bank)
        self.assertFalse(s2.data["llm_enabled"])

    def test_weak_pending_not_shared_between_sessions(self):
        """修复：DEFAULTS 浅拷贝会让 weak_pending 字典跨实例共享"""
        bank = tf.GeneBank(self.root)
        s1 = tf.Session(bank)
        s1.data["weak_pending"]["a|b"] = 1
        s2 = tf.Session(bank)
        self.assertEqual(s2.data["weak_pending"], {})   # 深拷贝：互不污染
        self.assertEqual(tf.Session.DEFAULTS["weak_pending"], {})  # 类默认值也干净


class TestIncrementalScan(unittest.TestCase):
    """真增量更新：新增/修改/删除/重命名"""

    def setUp(self):
        self.project = make_project({
            "main.py": "import utils.helpers\nimport utils.math_utils\nfrom utils.helpers import greet\n\ndef run():\n    greet()\n    math_utils.add(1, 2)\n",
            "utils/__init__.py": "",
            "utils/helpers.py": "def greet():\n    return 'hi'\n",
            "utils/math_utils.py": "def add(a, b):\n    return a + b\n",
        })

    def tearDown(self):
        shutil.rmtree(self.project, ignore_errors=True)

    def _run(self):
        farm = tf.TreeFarm(self.project)
        is_new, stat = farm.plant()
        return farm, is_new, stat

    def test_build_then_idle(self):
        farm, is_new, stat = self._run()
        self.assertTrue(is_new)
        self.assertGreater(stat["added"]["import"], 0)
        self.assertGreater(stat["added"]["call"], 0)   # 函数级基因已入库
        farm2, is_new2, stat2 = self._run()
        self.assertFalse(is_new2)
        self.assertEqual(stat2["added"]["import"] + stat2["added"]["call"], 0)  # 无变更

    def test_modify_no_new_coupling(self):
        """改动文件但没引入新耦合 → 新增 0（v3 统计口径：重建已有基因不算新增）"""
        self._run()
        with open(os.path.join(self.project, "main.py"), "a", encoding="utf-8") as f:
            f.write("\nimport utils.helpers as h2\n")   # 重复依赖，target 相同
        farm, _, stat = self._run()
        self.assertEqual(stat["added"]["import"] + stat["added"]["call"], 0)
        self.assertEqual(stat["eaten"], 0)

    def test_modify_new_import(self):
        """真正引入新依赖 → 只算新基因"""
        self._run()
        with open(os.path.join(self.project, "main.py"), "a", encoding="utf-8") as f:
            f.write("\nimport xml.etree.ElementTree\n")   # 新 target（stdlib 也会入库）
        farm, _, stat = self._run()
        self.assertEqual(stat["added"]["import"] + stat["added"]["call"], 1)

    def test_delete_file_cascade(self):
        farm, _, _ = self._run()
        os.remove(os.path.join(self.project, "utils/helpers.py"))
        farm2, _, stat = self._run()
        self.assertGreater(stat["eaten"], 0)   # main→helpers 的引用被级联回收
        # helpers 的基因（作为 source 或 target）应全部消失
        for g in farm2.bank.all_genes():
            self.assertNotIn("helpers.py", g["source"])
            self.assertNotIn("helpers.py", str(g["target"]))

    def test_rename_detection(self):
        farm, _, _ = self._run()
        os.rename(os.path.join(self.project, "utils/math_utils.py"),
                  os.path.join(self.project, "utils/calc.py"))
        farm2, _, stat = self._run()
        self.assertGreater(stat["renamed"], 0)
        # 引用旧路径/旧模块名的源文件已重扫，call 基因指向新路径
        found = [g for g in farm2.bank.all_genes()
                 if g["relation"] == "call" and "calc.py" in str(g["target"])]
        self.assertTrue(found, "重命名后 call 基因应指向新路径 calc.py")


class TestPlantLifecycle(unittest.TestCase):
    def test_weed_classification(self):
        root = make_project({"a.py": "x=1", "c.json": "{}", "d.png": "\x89PNG"})
        farm = tf.TreeFarm(root)
        farm.plant()
        self.assertEqual(len(farm.scanned["tree"]), 1)
        self.assertEqual(len(farm.scanned["weed"]), 2)

    def test_small_tree_index(self):
        root = make_project({"a.py": "import b\n", "b.py": "import c\n", "c.py": "pass\n"})
        farm = tf.TreeFarm(root)
        farm.plant()
        out, inn = farm.small_tree.related(os.path.join(root, "a.py"))
        self.assertTrue(out)                     # a 引用 b
        self.assertFalse(inn)                    # 没人引用 a


class TestConfigLocation(unittest.TestCase):
    def test_project_config_found(self):
        root = make_project({".treefarm.json": '{"api_key": "sk-test", "base_url": "https://x/v1", "model": "m"}'})
        cfg = tf.load_config(root)
        self.assertTrue(cfg)
        self.assertEqual(cfg["api_key"], "sk-test")

    def test_project_config_preferred_over_cwd(self):
        root = make_project({".treefarm.json": '{"api_key": "sk-project"}'})
        old_cwd = os.getcwd()
        try:
            os.chdir(root)   # cwd 有同名文件，但 root 优先
        finally:
            os.chdir(old_cwd)
        cfg = tf.load_config(root)
        self.assertEqual(cfg["api_key"], "sk-project")


# ========== 代码分析（v3.1，移植自融合版 v2.2 的测试 + 新增回归用例） ==========

TEST_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_data")


class TestDeadCodeDetection(unittest.TestCase):
    """死代码检测"""

    def setUp(self):
        self.test_file = os.path.join(TEST_DATA_DIR, "test_dead_code.py")
        self.tree_files = [self.test_file]

    def test_returns_dict(self):
        result = tf.detect_dead_code(self.tree_files)
        self.assertIsInstance(result, dict)
        for key in ("dead_functions", "dead_classes", "total_functions", "total_classes", "dead_ratio"):
            self.assertIn(key, result)

    def test_finds_dead_function(self):
        names = [d["name"] for d in tf.detect_dead_code(self.tree_files)["dead_functions"]]
        self.assertIn("dead_function", names)

    def test_finds_dead_class(self):
        names = [d["name"] for d in tf.detect_dead_code(self.tree_files)["dead_classes"]]
        self.assertIn("DeadClass", names)

    def test_excludes_used_function(self):
        names = [d["name"] for d in tf.detect_dead_code(self.tree_files)["dead_functions"]]
        self.assertNotIn("used_function", names)

    def test_excludes_used_class(self):
        names = [d["name"] for d in tf.detect_dead_code(self.tree_files)["dead_classes"]]
        self.assertNotIn("UsedClass", names)

    def test_excludes_main(self):
        names = [d["name"] for d in tf.detect_dead_code(self.tree_files)["dead_functions"]]
        self.assertNotIn("main", names)

    def test_ratio_is_float_in_range(self):
        ratio = tf.detect_dead_code(self.tree_files)["dead_ratio"]
        self.assertIsInstance(ratio, float)
        self.assertGreaterEqual(ratio, 0.0)
        self.assertLessEqual(ratio, 1.0)

    def test_attribute_base_class_not_dead(self):
        """修复：class Foo(utils.Base) 的 Attribute 基类也要算被继承（不再误判死类）"""
        root = make_project({
            "a.py": "import utils\nclass Base:\n    pass\n"
                    "class Sub(utils.Base):\n    pass\n"
                    "class Plain(Base):\n    pass\n",
        })
        try:
            result = tf.detect_dead_code([os.path.join(root, "a.py")])
            dead = [d["name"] for d in result["dead_classes"]]
            self.assertNotIn("Base", dead)      # Name 基类：被 Plain 继承
            self.assertNotIn("utils", dead)     # Attribute 基类根名不能当类
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_deep_inheritance_chain_not_dead(self):
        """Attribute 基类本身被继承时不应误判为死类（MySvc 未实例化才应判死）"""
        root = make_project({
            "pkg.py": "class ServiceBase:\n    pass\n",
            "b.py": "import pkg\nclass MySvc(pkg.ServiceBase):\n    pass\n",
        })
        try:
            result = tf.detect_dead_code([os.path.join(root, "pkg.py"),
                                          os.path.join(root, "b.py")])
            dead = [d["name"] for d in result["dead_classes"]]
            self.assertNotIn("ServiceBase", dead)   # 修复生效：Attribute 基类不算死
            self.assertIn("MySvc", dead)            # 未被实例化，仍应判死
        finally:
            shutil.rmtree(root, ignore_errors=True)


class TestComplexityAnalysis(unittest.TestCase):
    """代码复杂度分析"""

    def setUp(self):
        self.test_file = os.path.join(TEST_DATA_DIR, "test_dead_code.py")

    def test_returns_dict(self):
        result = tf.calculate_complexity(self.test_file)
        self.assertIsInstance(result, dict)
        for key in ("file", "total_lines", "total_functions", "avg_complexity", "max_complexity", "functions"):
            self.assertIn(key, result)

    def test_non_python_returns_none(self):
        self.assertIsNone(tf.calculate_complexity("test.js"))

    def test_finds_functions(self):
        result = tf.calculate_complexity(self.test_file)
        self.assertGreater(result["total_functions"], 0)
        self.assertGreater(len(result["functions"]), 0)

    def test_complex_function_higher(self):
        comps = {name: c for name, c, _, _ in tf.calculate_complexity(self.test_file)["functions"]}
        self.assertGreater(comps["complex_function"], comps["used_function"])

    def test_complexity_at_least_1(self):
        for _, comp, _, _ in tf.calculate_complexity(self.test_file)["functions"]:
            self.assertGreaterEqual(comp, 1)

    def test_sorted_desc(self):
        comps = [c for _, c, _, _ in tf.calculate_complexity(self.test_file)["functions"]]
        self.assertEqual(comps, sorted(comps, reverse=True))


class TestArchitectureDetection(unittest.TestCase):
    """架构分层识别"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="treefarm_arch_")
        for rel in ("utils/helpers.py", "services/user_service.py", "models/user.py",
                    "controllers/user_controller.py", "main.py"):
            path = os.path.join(self.test_dir, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write("# test\n")
        self.tree_files = [os.path.join(self.test_dir, rel) for rel in
                           ("utils/helpers.py", "services/user_service.py", "models/user.py",
                            "controllers/user_controller.py", "main.py")]

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_returns_dict(self):
        result = tf.detect_architecture_layers(self.tree_files, None, {})
        for key in ("layers", "layer_count", "has_clear_layers"):
            self.assertIn(key, result)

    def test_has_all_layers(self):
        layers = tf.detect_architecture_layers(self.tree_files, None, {})["layers"]
        for key in ("util", "business", "data", "presentation"):
            self.assertIn(key, layers)

    def test_util_layer(self):
        files = tf.detect_architecture_layers(self.tree_files, None, {})["layers"]["util"]
        self.assertTrue(any("helpers" in f for f in files))

    def test_business_layer(self):
        files = tf.detect_architecture_layers(self.tree_files, None, {})["layers"]["business"]
        self.assertTrue(any("service" in f for f in files))

    def test_data_layer(self):
        files = tf.detect_architecture_layers(self.tree_files, None, {})["layers"]["data"]
        self.assertTrue(any("user.py" in f for f in files))

    def test_presentation_layer(self):
        files = tf.detect_architecture_layers(self.tree_files, None, {})["layers"]["presentation"]
        self.assertTrue(any("controller" in f for f in files))

    def test_storage_path_not_pollute_data_layer(self):
        """回归测试（v3.1 修复）：传 root 后按相对路径匹配，
        路径里的 storage 字样不得污染分层，utils 文件必须归工具层。"""
        fake_storage = os.path.join(tempfile.mkdtemp(prefix="storage_emulated_0_"), "proj")
        os.makedirs(os.path.join(fake_storage, "utils"), exist_ok=True)
        with open(os.path.join(fake_storage, "utils", "helpers.py"), "w") as f:
            f.write("def h(): pass\n")
        with open(os.path.join(fake_storage, "main.py"), "w") as f:
            f.write("def main(): pass\n")
        files = [os.path.join(fake_storage, "utils", "helpers.py"),
                 os.path.join(fake_storage, "main.py")]
        result = tf.detect_architecture_layers(files, None, {}, root=fake_storage)
        self.assertIn(os.path.join(fake_storage, "utils", "helpers.py"),
                      result["layers"]["util"])
        self.assertNotIn(os.path.join(fake_storage, "utils", "helpers.py"),
                         result["layers"]["data"])
        shutil.rmtree(fake_storage)

    def test_mixed_language_layers(self):
        """跨语言架构分层（v3.6 回归测试）：JS/Java/Go 文件与 Python 一样按
        目录/命名关键词分层，语言无关。"""
        root = make_project({"utils/helpers.js": "function fmt() {}\n",
                             "web/app.js": "const x = fmt();\n",
                             "services/UserService.java": "public class UserService {}\n",
                             "models/User.java": "public class User {}\n",
                             "repository/user_repo.go": "package main\n",
                             "main.py": "def main(): pass\n"})
        tree_files = [os.path.join(root, rel) for rel in
                      ("utils/helpers.js", "web/app.js", "services/UserService.java",
                       "models/User.java", "repository/user_repo.go", "main.py")]
        result = tf.detect_architecture_layers(tree_files, None, {}, root=root)
        layers = result["layers"]
        # JS 文件：utils → 工具层，web → 表现层
        self.assertIn(os.path.join(root, "utils", "helpers.js"), layers["util"])
        self.assertIn(os.path.join(root, "web", "app.js"), layers["presentation"])
        # Java 文件：service → 业务层，model → 数据层
        self.assertIn(os.path.join(root, "services", "UserService.java"), layers["business"])
        self.assertIn(os.path.join(root, "models", "User.java"), layers["data"])
        # Go 文件：repository → 数据层
        self.assertIn(os.path.join(root, "repository", "user_repo.go"), layers["data"])


class TestCircularDeps(unittest.TestCase):
    """循环依赖检测（v3.1 新增测试）"""

    def test_detect_cycle(self):
        root = make_project({"a.py": "import b\n", "b.py": "import a\n", "c.py": "pass\n"})
        farm = tf.TreeFarm(root)
        farm.plant()
        result = tf.detect_circular_dependencies(farm.bank, farm.module_map)
        self.assertTrue(result["has_cycle"])
        # 环里应同时含 a 和 b（模块名）
        flat = [mod for cycle in result["cycles"] for mod in cycle]
        self.assertIn("a", flat)
        self.assertIn("b", flat)

    def test_no_cycle(self):
        root = make_project({"a.py": "import b\n", "b.py": "import c\n", "c.py": "pass\n"})
        farm = tf.TreeFarm(root)
        farm.plant()
        result = tf.detect_circular_dependencies(farm.bank, farm.module_map)
        self.assertFalse(result["has_cycle"])

    def test_two_node_cycle_reported_once(self):
        root = make_project({"a.py": "import b\n", "b.py": "import a\n"})
        farm = tf.TreeFarm(root)
        farm.plant()
        result = tf.detect_circular_dependencies(farm.bank, farm.module_map)
        self.assertEqual(len(result["cycles"]), 1)  # a↔b 只报一个环，不去重成两个


class TestImpactAnalysis(unittest.TestCase):
    """影响分析（v3.1 新增测试）"""

    def test_import_chain_impact(self):
        root = make_project({"lib.py": "def f(): pass\n",
                             "a.py": "import lib\n",
                             "b.py": "import a\n"})
        farm = tf.TreeFarm(root)
        farm.plant()
        result = tf.impact_analysis(farm.bank, farm.module_map, os.path.join(root, "lib.py"))
        impacted = [os.path.basename(f) for f, _ in result["impacted_files"]]
        self.assertIn("a.py", impacted)   # a 依赖 lib
        self.assertIn("b.py", impacted)   # b 依赖 a → 间接影响

    def test_call_gene_impact(self):
        """call 基因的影响分析：符号 = 被调用符号（调用点），
        即"改 target.py 的 helper 会影响到 caller.py 中调用 helper 的地方"。"""
        root = make_project({"target.py": "def helper(): pass\n",
                             "caller.py": "def run():\n    helper()\n"})
        farm = tf.TreeFarm(root)
        farm.plant()
        result = tf.impact_analysis(farm.bank, farm.module_map, os.path.join(root, "target.py"))
        impacted = [os.path.basename(f) for f, _ in result["impacted_files"]]
        self.assertIn("caller.py", impacted)
        # impacted_functions 的符号 = caller 调用的目标符号（调用点）
        funcs = [func for _, func, _ in result["impacted_functions"]]
        self.assertIn("helper", funcs)

    def test_leaf_no_impact(self):
        root = make_project({"leaf.py": "def f(): pass\n"})
        farm = tf.TreeFarm(root)
        farm.plant()
        result = tf.impact_analysis(farm.bank, farm.module_map, os.path.join(root, "leaf.py"))
        self.assertEqual(result["total_impacted"], 0)

    def test_cross_language_impact_js_target(self):
        """跨语言影响分析（v3.6 回归测试）：JS 文件定义符号，
        Python / Java / JS 三方调用它 → 修改该 JS 文件会波及所有语言调用方。"""
        root = make_project({"utils/helpers.js": "function fmt(x) { return 'v' + x; }\n",
                             "web/app.js": "const out = fmt(1);\n",
                             "Main.java": "public class Main { void run() { fmt(2); } }\n",
                             "main.py": "def run():\n    fmt(3)\n"})
        farm = tf.TreeFarm(root)
        farm.plant()
        result = tf.impact_analysis(farm.bank, farm.module_map,
                                    os.path.join(root, "utils", "helpers.js"))
        impacted = sorted(os.path.basename(f) for f, _ in result["impacted_files"])
        self.assertIn("app.js", impacted)     # JS 调用方
        self.assertIn("Main.java", impacted)  # Java 调用方
        self.assertIn("main.py", impacted)    # Python 调用方

    def test_cross_language_impact_java_target(self):
        """跨语言影响分析：Java 文件定义的方法被 Python 调用 → 反向波及。"""
        root = make_project({"Helpers.java": "public class Helpers {\n"
                                             "  public static String fmt(String x) { return x; }\n}\n",
                             "main.py": "def run():\n    fmt('x')\n"})
        farm = tf.TreeFarm(root)
        farm.plant()
        result = tf.impact_analysis(farm.bank, farm.module_map,
                                    os.path.join(root, "Helpers.java"))
        impacted = [os.path.basename(f) for f, _ in result["impacted_files"]]
        self.assertIn("main.py", impacted)


# ========== v3.2 新增：FileCache / 批量事务 / 忽略规则 / TOML 配置 / 进度回调 ==========

class TestFileCache(unittest.TestCase):
    """FileCache：内容/AST/符号缓存 + 文件变更自动失效"""

    def test_text_cached_and_invalidated_on_change(self):
        root = make_project({"a.py": "x = 1\n"})
        p = os.path.join(root, "a.py")
        cache = tf.FileCache()
        first = cache.text(p)
        self.assertEqual(first, "x = 1\n")
        self.assertEqual(cache.text(p), first)          # 命中缓存
        with open(p, "w", encoding="utf-8") as f:       # 改内容（长度变 → 键变）
            f.write("x = 123456\n")
        self.assertEqual(cache.text(p), "x = 123456\n")  # 缓存自动失效

    def test_ast_cached(self):
        root = make_project({"a.py": "def foo(): pass\n"})
        p = os.path.join(root, "a.py")
        cache = tf.FileCache()
        tree = cache.get_ast(p)
        self.assertIsNotNone(tree)
        self.assertIs(cache.get_ast(p), tree)                # 同一对象 = 命中缓存

    def test_symbols_cached(self):
        root = make_project({"a.py": "def foo():\n    pass\n\nclass Bar:\n    pass\n"})
        cache = tf.FileCache()
        self.assertEqual(cache.symbols(os.path.join(root, "a.py")), ["Bar", "foo"])

    def test_extract_symbols_uses_cache(self):
        root = make_project({"a.py": "def foo(): pass\n"})
        p = os.path.join(root, "a.py")
        self.assertEqual(tf.extract_symbols(p), ["foo"])
        self.assertEqual(tf.extract_symbols(p), ["foo"])  # 重复调用结果一致

    def test_missing_file_empty(self):
        cache = tf.FileCache()
        self.assertEqual(cache.text("/no/such/file.py"), "")
        self.assertIsNone(cache.get_ast("/no/such/file.py"))
        self.assertEqual(cache.symbols("/no/such/file.py"), [])


class TestBatchContext(unittest.TestCase):
    """GeneBank.batch()：批量事务提交 / 异常回滚"""

    def test_batch_commits_on_exit(self):
        root = make_project({"a.py": "import os\n"})
        bank = tf.GeneBank(root)
        try:
            bank.add({"source": "/x/a.py", "target": "os", "relation": "import",
                      "kind": "strong", "verified": True})
            self.assertEqual(bank.gene_count(), 1)       # 非批量：立即落盘
            with bank.batch():
                bank.add({"source": "/x/a.py", "target": "sys", "relation": "import",
                          "kind": "strong", "verified": True})
                self.assertEqual(bank.gene_count(), 2)   # 批量内可见
            bank2 = tf.GeneBank(root)                     # 新连接验证已持久化
            try:
                self.assertEqual(bank2.gene_count(), 2)
            finally:
                bank2.close()
        finally:
            bank.close()

    def test_batch_rollback_on_exception(self):
        root = make_project({"a.py": "import os\n"})
        bank = tf.GeneBank(root)
        try:
            with self.assertRaises(RuntimeError):
                with bank.batch():
                    bank.add({"source": "/x/a.py", "target": "sys", "relation": "import",
                              "kind": "strong", "verified": True})
                    raise RuntimeError("boom")
            self.assertEqual(bank.gene_count(), 0)       # 异常 → 回滚
        finally:
            bank.close()


class TestIgnoreRules(unittest.TestCase):
    """忽略规则（.gitignore 风格简化版）"""

    def test_seg_match(self):
        self.assertTrue(tf._seg_match("vendor", "vendor"))
        self.assertTrue(tf._seg_match("build", "b*"))
        self.assertTrue(tf._seg_match("x.py", "*.py"))
        self.assertFalse(tf._seg_match("a/b.py", "*.py"))  # 不跨 / 段

    def test_matches_ignore_basename(self):
        self.assertTrue(tf._matches_ignore("src/vendor.js", ["*.js"]))
        self.assertFalse(tf._matches_ignore("src/main.py", ["*.js"]))

    def test_matches_ignore_path_patterns(self):
        self.assertTrue(tf._matches_ignore("a/b/vendor/x.py", ["**/vendor/**"]))
        self.assertTrue(tf._matches_ignore("vendor/x.py", ["**/vendor/**"]))
        self.assertFalse(tf._matches_ignore("myvendor/x.py", ["**/vendor/**"]))  # 不是 vendor 目录
        self.assertTrue(tf._matches_ignore("node_modules/pkg/index.js", ["node_modules/**"]))
        self.assertTrue(tf._matches_ignore("dist/out.js", ["dist/"]))

    def test_comment_and_negation_ignored(self):
        self.assertFalse(tf._matches_ignore("a.py", ["# comment", "!keep.py"]))

    def test_scan_applies_ignore(self):
        root = make_project({
            "main.py": "import os\n",
            "vendor/lib.py": "import sys\n",
            "generated/out.py": "import json\n",
            "notes.txt": "hello",
        })
        res = tf.scan(root, ignore_patterns=["**/vendor/**"], ignore_dirs=["generated"])
        trees = {os.path.basename(f) for f in res["tree"]}
        self.assertEqual(trees, {"main.py"})


class TestTomlConfig(unittest.TestCase):
    """极简 TOML 解析 + load_config"""

    def test_parse_toml(self):
        data = tf._parse_toml("""
        # 注释
        api_key = "sk-123"
        count = 3
        enabled = true

        [scan]
        ignore = ["**/vendor/**", "*.min.js"]
        ignore_dirs = ["build"]
        """)
        self.assertEqual(data["api_key"], "sk-123")
        self.assertEqual(data["count"], 3)
        self.assertTrue(data["enabled"])
        self.assertEqual(data["scan"]["ignore"], ["**/vendor/**", "*.min.js"])
        self.assertEqual(data["scan"]["ignore_dirs"], ["build"])

    def test_load_config_toml(self):
        root = make_project({".treefarm.toml": '[scan]\nignore = ["**/vendor/**"]\n'})
        cfg = tf.load_config(root)
        self.assertEqual(cfg["scan"]["ignore"], ["**/vendor/**"])

    def test_toml_preferred_over_json(self):
        root = make_project({
            ".treefarm.toml": 'api_key = "sk-toml"\n',
            ".treefarm.json": '{"api_key": "sk-json"}',
        })
        cfg = tf.load_config(root)
        self.assertEqual(cfg["api_key"], "sk-toml")


class TestPlantProgress(unittest.TestCase):
    """plant 进度回调 + brief_data 结构化输出"""

    def test_progress_callback_called_per_file(self):
        root = make_project({"a.py": "import os\n", "b.py": "import sys\n"})
        farm = tf.TreeFarm(root)
        calls = []
        farm.plant(progress=lambda i, total, f: calls.append((i, total, os.path.basename(f))))
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1], 2)                # total = 文件数
        self.assertEqual({c[2] for c in calls}, {"a.py", "b.py"})

    def test_brief_data_structure(self):
        root = make_project({"a.py": "import os\ndef f(): pass\n"})
        farm = tf.TreeFarm(root)
        farm.plant()
        d = farm.brief_data()
        self.assertEqual(d["tool"], "tree_farm")
        self.assertEqual(d["version"], tf.VERSION)
        self.assertIn("round", d)
        self.assertEqual(len(d["trees"]), 1)
        self.assertIn("out", d["trees"][0])             # Python 文件有引用列表
        self.assertIn("weeds", d)
        self.assertIn("trash", d)


# ========== v3.3 新增：JS/TS 函数级分析 / 多符号基因 / 调用图 / 重复代码 ==========

class TestJsCallGraph(unittest.TestCase):
    """JS/TS 轻量函数级分析（零依赖解析器）"""

    def test_defs_and_calls(self):
        root = make_project({"a.js": """
function greet(name) { return "Hello " + name; }
const formatPrice = (p) => { return "$" + p; };
class Logger { log(msg) { console.log(msg); } }
""", "b.js": """
import { greet, formatPrice } from "./a.js";
function run() {
  greet("x");
  formatPrice(1);
}
"""})
        defs, calls = tf.extract_js_call_graph(os.path.join(root, "a.js"))
        self.assertIn("greet", defs)
        self.assertIn("formatPrice", defs)      # 箭头函数
        self.assertIn("Logger", defs)
        self.assertIn("log", defs)              # 类方法
        _, calls2 = tf.extract_js_call_graph(os.path.join(root, "b.js"))
        self.assertIn("greet", calls2)
        self.assertIn("formatPrice", calls2)
        self.assertNotIn("run", calls2)         # 定义行不是调用

    def test_strings_and_comments_stripped(self):
        root = make_project({"a.js": """
// if (fake) { fake(); }
const s = "function fake2() { fake2(); }";
function real() { real(); }
"""})
        defs, calls = tf.extract_js_call_graph(os.path.join(root, "a.js"))
        self.assertIn("real", defs)
        self.assertNotIn("fake", calls)         # 注释/字符串里的不被当调用
        self.assertNotIn("fake2", defs)


class TestMultiSymbolGenes(unittest.TestCase):
    """v3.3：同一文件对多条不同符号的 call 基因都能入库"""

    def test_js_multi_symbol_call_genes(self):
        root = make_project({
            "utils.js": "function greet(){}\nexport const fmt = (x) => x;\n",
            "main.js": "import {greet} from './utils.js';\nfunction r(){ greet(); fmt(1); }\n",
        })
        farm = tf.TreeFarm(root)
        farm.plant()
        calls = [g for g in farm.bank.all_genes() if g["relation"] == "call"]
        syms = {g["symbol"] for g in calls}
        self.assertIn("greet", syms)
        self.assertIn("fmt", syms)              # 两个符号都入库，不再被去重吃掉


class TestCallGraph(unittest.TestCase):
    """Mermaid 调用图"""

    def test_project_graph_output(self):
        root = make_project({"a.py": "import b\n", "b.py": "def f(): pass\n"})
        farm = tf.TreeFarm(root)
        farm.plant()
        out = farm.call_graph()
        self.assertIn("graph LR", out)
        self.assertIn("a.py", out)
        self.assertIn("b.py", out)

    def test_file_graph_symbol_level(self):
        root = make_project({"a.py": "def f(): pass\n", "b.py": "from a import f\ndef r(): f()\n"})
        farm = tf.TreeFarm(root)
        farm.plant()
        out = farm.call_graph(os.path.join(root, "b.py"))
        self.assertIn("graph LR", out)
        self.assertIn("a.py", out)              # b.py 的调用指向 a.py

    def test_mermaid_node_escaping(self):
        node = tf._mermaid_node('x"y')
        self.assertIn('\\"', node)              # 引号被转义


class TestDuplicates(unittest.TestCase):
    """重复代码检测"""

    def test_detects_identical_files(self):
        root = make_project({"a.py": "def f():\n    return 42\n",
                             "b.py": "def f():\n    return 42\n",
                             "c.py": "def g():\n    return 1\n"})
        farm = tf.TreeFarm(root)
        farm.plant()
        out = farm.duplicates()
        self.assertIn("a.py", out)
        self.assertIn("b.py", out)
        self.assertIn("100%", out)

    def test_no_duplicates(self):
        root = make_project({"a.py": "def f():\n    return 42\n",
                             "b.py": "class X:\n    pass\n"})
        farm = tf.TreeFarm(root)
        farm.plant()
        out = farm.duplicates()
        self.assertIn("没有发现", out)


class TestJavaCallGraph(unittest.TestCase):
    """Java 函数级分析（v3.4 新增，零依赖轻量解析器）"""

    def test_calls_and_defs(self):
        root = make_project({"App.java": """
public class App {
    private Helper helper;
    public void onCreate(Bundle b) {
        helper.run("x");
        Arrays.asList(1, 2);
        new Widget().show();
    }
    private String getName(int id) throws IOException {
        return helper.fetch(id);
    }
}
"""})
        calls, inherits = tf.extract_java_call_graph(
            os.path.join(root, "App.java"))
        self.assertIn("helper.run", calls)
        self.assertIn("Arrays.asList", calls)
        self.assertIn("show", calls)            # new Widget().show() 里的 show
        self.assertIn("helper.fetch", calls)
        self.assertNotIn("onCreate", calls)     # 方法定义不是调用
        self.assertNotIn("getName", calls)
        self.assertEqual(inherits, [])

    def test_inheritance(self):
        root = make_project({"A.java": """
class A<T> extends Base<T> implements I1, I2 {
    void m() {}
}
interface Sub extends Sup1, Sup2 {
    void x();
}
enum Color implements Named {
    RED;
}
record Point(int x) implements Shape {}
"""})
        _, inherits = tf.extract_java_call_graph(os.path.join(root, "A.java"))
        for base in ("Base", "I1", "I2", "Sup1", "Sup2", "Named", "Shape"):
            self.assertIn(base, inherits)
        self.assertNotIn("Object", inherits)    # 默认基类不统计

    def test_noise_stripped(self):
        root = make_project({"Noisy.java": """
public class Noisy {
    // fake() should not appear
    /* also fake2() */
    String s = "fake3() and fake4()";
    String t = \"\"\"
        fake5() here
    \"\"\";
    void real() {
        other.call();
    }
}
"""})
        calls, _ = tf.extract_java_call_graph(os.path.join(root, "Noisy.java"))
        self.assertIn("other.call", calls)
        self.assertNotIn("real", calls)         # 定义前有语句（; 结尾）也正确识别
        for i in range(1, 6):
            self.assertNotIn(f"fake{i}", calls)

    def test_control_flow_not_defs(self):
        root = make_project({"Flow.java": """
public class Flow {
    void loop(int n) {
        if (n > 0) work();
        while (n-- > 0) step();
        for (int i = 0; i < n; i++) tick(i);
        try { risky(); } catch (Exception e) { log(e); }
        synchronized (this) { lock(); }
    }
}
"""})
        calls, _ = tf.extract_java_call_graph(os.path.join(root, "Flow.java"))
        for name in ("work", "step", "tick", "risky", "log", "lock"):
            self.assertIn(name, calls)
        self.assertNotIn("loop", calls)         # 方法定义不是调用

    def test_interface_abstract_methods(self):
        root = make_project({"S.java": """
interface Service {
    void run();
    default String name() { return "s"; }
}
class Impl<T> implements Service, AutoCloseable {
    @Override
    public void run() {
        List<String> items = getItems();
        items.stream().filter(x -> x.length() > 2).count();
    }
    private List<String> getItems() { return null; }
}
"""})
        calls, _ = tf.extract_java_call_graph(os.path.join(root, "S.java"))
        self.assertIn("getItems", calls)
        self.assertNotIn("run", calls)          # 接口抽象方法/实现方法都是定义
        self.assertNotIn("name", calls)


class TestJSParserEdges(unittest.TestCase):
    """JS 解析器边界加固（v3.4）"""

    def test_single_param_arrow(self):
        root = make_project({"a.js": "const f = x => x * 2;\n"
                                     "const g = (a, b) => a + b;\n"})
        defs, _ = tf.extract_js_call_graph(os.path.join(root, "a.js"))
        self.assertIn("f", defs)                # v3.4：单参数无括号箭头函数
        self.assertIn("g", defs)

    def test_template_string_fake_calls(self):
        root = make_project({"a.js": """
const s = `hello ${fmt()} and ${obj.method()}`;
function real() { real(); }
"""})
        _, calls = tf.extract_js_call_graph(os.path.join(root, "a.js"))
        self.assertNotIn("fmt", calls)          # 模板字符串里的调用不是真调用
        self.assertNotIn("obj.method", calls)

    def test_nested_arrows_and_chain(self):
        root = make_project({"a.js": """
const f = (a) => (b) => a(b);
a().b().c();
(function() { init(); })();
"""})
        defs, calls = tf.extract_js_call_graph(os.path.join(root, "a.js"))
        self.assertIn("f", defs)
        self.assertIn("a", calls)               # 链式调用
        self.assertIn("init", calls)            # IIFE 里的调用

    def test_object_and_class_methods(self):
        root = make_project({"a.js": """
const obj = {
  foo(x) { return x; },
  bar() { return 1; }
};
class C {
  static make() { return 1; }
  run() { return 2; }
}
"""})
        defs, calls = tf.extract_js_call_graph(os.path.join(root, "a.js"))
        for name in ("foo", "bar", "make", "run"):
            self.assertIn(name, defs)
        self.assertNotIn("foo", calls)          # 方法定义不是调用


class TestJavaDeepGenes(unittest.TestCase):
    """v3.4：Java 跨文件 call/inherit 基因入库（端到端）"""

    def test_java_call_and_inherit_genes(self):
        root = make_project({
            "base/BaseActivity.java": "package base;\npublic class BaseActivity {}\n",
            "util/Helper.java": "package util;\n"
            "public class Helper { public void run() {} }\n",
            "App.java": """
import base.BaseActivity;
import util.Helper;
public class App extends BaseActivity {
    private Helper helper;
    public void onCreate() {
        helper.run();
    }
}
""",
        })
        farm = tf.TreeFarm(root)
        farm.plant()
        genes = farm.bank.all_genes()
        calls = [g for g in genes if g["relation"] == "call"]
        inherits = [g for g in genes if g["relation"] == "inherit"]
        self.assertTrue(any(g["symbol"] == "helper.run" for g in calls),
                        f"应有跨文件 call 基因 helper.run，实际: {[g['symbol'] for g in calls]}")
        self.assertTrue(any("BaseActivity" in g["target"] for g in inherits),
                        f"应有 inherit 基因指向 BaseActivity，"
                        f"实际: {[g['target'] for g in inherits]}")


class TestGoCallGraph(unittest.TestCase):
    """Go 函数级分析（v3.5 新增，零依赖轻量解析器）"""

    def test_funcs_methods_and_embedding(self):
        root = make_project({"main.go": """
package main

import (
    "fmt"
    "strings"
)

type Greeter struct {
    Base
    *Logger
    name string
}
type Base struct{}
type Logger struct{}

func (l *Logger) Log(msg string) {
    fmt.Println("[" + msg + "]")
}
func NewGreeter(name string) *Greeter {
    return &Greeter{name: name}
}
func (g *Greeter) Hello() {
    g.Log("hi")
    fmt.Println(strings.ToUpper(g.name))
    go g.Log("async")
    defer g.Log("bye")
}
func main() {
    g := NewGreeter("world")
    g.Hello()
}
"""})
        path = os.path.join(root, "main.go")
        calls, inherits = tf.extract_go_call_graph(path)
        for name in ("fmt.Println", "strings.ToUpper", "NewGreeter", "g.Hello"):
            self.assertIn(name, calls)
        self.assertNotIn("Log", calls)          # 方法定义行不是调用
        self.assertNotIn("main", calls)
        for base in ("Base", "Logger"):
            self.assertIn(base, inherits)       # struct 嵌入 → inherit 基因

    def test_generics_and_interface(self):
        root = make_project({"g.go": """
package p
type Container[T any] struct{}
type Reader interface {
    Read(p []byte) (n int, err error)
}
func Map[T any, U any](xs []T, f func(T) U) []U { return nil }
func use() {
    var r Reader
    r.Read(nil)
    nums := Map[int, string](nil, func(x int) string { return "" })
    _ = nums
}
"""})
        calls, _ = tf.extract_go_call_graph(os.path.join(root, "g.go"))
        self.assertIn("Map", calls)             # 泛型调用 Map[int, string]( 也被识别
        self.assertNotIn("Read", calls)         # 接口方法声明不是调用
        self.assertNotIn("Map[int, string]", calls)

    def test_noise_stripped(self):
        root = make_project({"n.go": """
package n
// fake() should not appear
/* also fake2() */
const s = "fake3() here"
const t = `fake4() here`
func real() { helper() }
"""})
        calls, _ = tf.extract_go_call_graph(os.path.join(root, "n.go"))
        self.assertIn("helper", calls)
        for i in range(1, 5):
            self.assertNotIn(f"fake{i}", calls)

    def test_no_embedding_for_regular_fields(self):
        root = make_project({"e.go": """
package e
type Point struct {
    X int
    Y int `json:"y"`
}
"""})
        _, inherits = tf.extract_go_call_graph(os.path.join(root, "e.go"))
        self.assertEqual(inherits, [])          # X/Y 是普通字段不是嵌入

    def test_go_symbols(self):
        root = make_project({"s.go": """
package s
func plain() {}
func (r *T) method() {}
func Generic[T any]() {}
type MyStruct struct{}
type MyIface interface{}
"""})
        syms = tf.extract_symbols(os.path.join(root, "s.go"))
        for name in ("plain", "method", "Generic", "MyStruct", "MyIface"):
            self.assertIn(name, syms)


class TestRustCallGraph(unittest.TestCase):
    """Rust 函数级分析（v3.7 新增，零依赖轻量解析器）"""

    def test_funcs_methods_generics_and_trait_inherit(self):
        root = make_project({"lib.rs": """
use std::collections::HashMap;

pub struct Config {
    pub name: String,
}

trait Runnable: Sync + Send {
    fn run(&self);
}

pub trait Advanced: Runnable {
    fn boost(&self);
}

fn compute<T: Clone>(x: T) -> T {
    let mut h = HashMap::new();
    h.insert(x.clone(), 1);
    x
}

impl Config {
    pub fn new(name: String) -> Self {
        Self { name }
    }
    fn get_name(&self) -> &str {
        &self.name
    }
}

pub fn main() {
    let c = Config::new(String::from("x"));
    println!("{}", c.get_name());
    compute(1);
}
"""})
        path = os.path.join(root, "lib.rs")
        calls, inherits = tf.extract_rust_call_graph(path)
        for name in ("new", "compute", "from"):
            self.assertIn(name, calls)          # 关联函数/方法调用
        self.assertIn("c.get_name", calls)      # 方法属性链调用
        self.assertNotIn("main", calls)         # 函数定义不是调用
        self.assertNotIn("get_name", calls)     # 方法定义不是调用
        self.assertNotIn("println", calls)      # 宏调用排除
        self.assertNotIn("HashMap", calls)      # HashMap:: 后跟 :: 不是调用
        self.assertEqual(inherits, ["Runnable"])  # Sync/Send 标准库过滤，Runnable 保留

    def test_noise_stripped(self):
        root = make_project({"n.rs": """
// fake() should not appear
/* also fake2() */
const S: &str = "fake3() here";
fn real() {
    let r = r#"fn fake4() { helper(0) }"#;
    let ch = 'x';
    let life: &'a str = "ok";
    helper();
}
fn fake5() {}
"""})
        path = os.path.join(root, "n.rs")
        calls, _ = tf.extract_rust_call_graph(path)
        self.assertIn("helper", calls)
        for i in range(1, 5):
            self.assertNotIn(f"fake{i}", calls)  # 注释/字符串/raw string/字符里的噪音
        defs = [n for n, _, _, k in tf._rust_defs(tf._strip_rust_noise(
            open(path, encoding="utf-8").read())) if k == "func"]
        self.assertIn("fake5", defs)            # 真函数定义保留

    def test_lifetimes_unsafe_async_and_where(self):
        root = make_project({"w.rs": """
unsafe fn raw_ptr<'a, T>(p: &'a T) -> *const T { p }
async fn fetch<'a>(u: &'a str) -> String { String::new() }
fn process<T>(xs: Vec<T>) -> usize where T: Clone { xs.len() }
fn user() {
    unsafe { raw_ptr(&1) };
    let fut = fetch("http://x");
    let n = process(vec![1, 2, 3]);
    let _ = (fut, n);
}
"""})
        path = os.path.join(root, "w.rs")
        calls, _ = tf.extract_rust_call_graph(path)
        self.assertIn("raw_ptr", calls)
        self.assertIn("fetch", calls)
        self.assertIn("process", calls)
        defs = [n for n, _, _, k in tf._rust_defs(tf._strip_rust_noise(
            open(path, encoding="utf-8").read())) if k == "func"]
        self.assertIn("raw_ptr", defs)
        self.assertIn("fetch", defs)
        self.assertIn("process", defs)

    def test_rust_symbols(self):
        root = make_project({"s.rs": """
fn plain() {}
fn generic<T: Clone>(x: T) {}
struct MyStruct {}
enum MyEnum { A, B }
trait MyTrait {}
impl MyStruct {
    fn method(&self) {}
}
"""})
        syms = tf.extract_symbols(os.path.join(root, "s.rs"))
        for name in ("plain", "generic", "MyStruct", "MyEnum", "MyTrait", "method"):
            self.assertIn(name, syms)

    def test_self_method_calls_recorded(self):
        root = make_project({"m.rs": """
struct S;
impl S {
    fn a(&self) { self.b(); self.storage.write(); }
    fn b(&self) {}
}
"""})
        calls, _ = tf.extract_rust_call_graph(os.path.join(root, "m.rs"))
        # self.b( / self.storage.write( → 叶子名记录，否则死代码检测会误判
        self.assertIn("b", calls)
        self.assertIn("write", calls)
        self.assertNotIn("a", calls)            # 定义不是调用


class TestRustDeepGenes(unittest.TestCase):
    """v3.7：Rust 跨文件 call/inherit 基因入库（端到端）"""

    def test_rust_call_and_inherit_genes(self):
        root = make_project({
            "util.rs": "pub fn Helper() -> u32 { 1 }\n"
            "pub fn NewApp() -> u32 { 2 }\n"
            "pub trait Base { fn base(&self); }\n",
            "app.rs": """
pub struct App;

impl Base for App {
    fn base(&self) {}
}

pub fn run() {
    let _ = Helper();
    let _ = NewApp();
}
""",
        })
        farm = tf.TreeFarm(root)
        farm.plant()
        genes = farm.bank.all_genes()
        calls = [g for g in genes if g["relation"] == "call"]
        self.assertTrue(any(g["symbol"] == "Helper" for g in calls),
                        f"应有跨文件 call 基因 Helper，实际: {[g['symbol'] for g in calls]}")
        self.assertTrue(any(g["symbol"] == "NewApp" for g in calls),
                        f"应有跨文件 call 基因 NewApp，实际: {[g['symbol'] for g in calls]}")


class TestGoDeepGenes(unittest.TestCase):
    """v3.5：Go 跨文件 call/inherit 基因入库（端到端）"""

    def test_go_call_and_inherit_genes(self):
        root = make_project({
            "util.go": "package main\n"
            "func Helper() string { return \"x\" }\n"
            "type Base struct{}\n",
            "app.go": """
package main
type App struct {
    Base
}
func NewApp() *App { return &App{} }
func (a *App) Run() {
    _ = Helper()
}
""",
        })
        farm = tf.TreeFarm(root)
        farm.plant()
        genes = farm.bank.all_genes()
        calls = [g for g in genes if g["relation"] == "call"]
        inherits = [g for g in genes if g["relation"] == "inherit"]
        self.assertTrue(any(g["symbol"] == "Helper" for g in calls),
                        f"应有跨文件 call 基因 Helper，实际: {[g['symbol'] for g in calls]}")
        self.assertTrue(any(g["symbol"] == "Base" for g in inherits),
                        f"应有 inherit 基因指向 Base，实际: {[g['symbol'] for g in inherits]}")


class TestJavaParserEdges(unittest.TestCase):
    """Java 解析器边界加固（v3.5：匿名内部类 / 泛型方法 / 可变参数 / switch 表达式等）"""

    def test_anonymous_inner_class(self):
        root = make_project({"A.java": """
public class A {
    void run() {
        Runnable r = new Runnable() {
            @Override
            public void run() {
                helper();
            }
        };
    }
    void helper() {}
}
"""})
        calls, _ = tf.extract_java_call_graph(os.path.join(root, "A.java"))
        self.assertIn("helper", calls)          # 匿名内部类里的调用
        self.assertNotIn("Runnable", calls)     # new Runnable() 实例化不算调用

    def test_generic_methods_varargs_switch_expr(self):
        root = make_project({"B.java": """
public class B {
    <T> T first(List<T> xs) { return xs.get(0); }
    void many(String... args) { }
    int pick(int x) {
        return switch (x) {
            case 1 -> 10;
            case 2 -> 20;
            default -> 0;
        };
    }
}
"""})
        calls, _ = tf.extract_java_call_graph(os.path.join(root, "B.java"))
        self.assertNotIn("first", calls)        # 泛型方法定义
        self.assertNotIn("many", calls)         # 可变参数定义
        self.assertNotIn("pick", calls)         # switch 表达式所在方法定义

    def test_enum_with_method_and_constructor(self):
        root = make_project({"Color.java": """
public enum Color {
    RED(1), GREEN(2);
    private final int code;
    Color(int code) { this.code = code; }
    public int code() { return code; }
    public static Color of(int c) { return RED; }
}
"""})
        calls, _ = tf.extract_java_call_graph(os.path.join(root, "Color.java"))
        self.assertNotIn("Color", calls)        # 构造器定义不是调用
        self.assertNotIn("of", calls)


class TestDeadCodeMultiLang(unittest.TestCase):
    """v3.5：死代码检测扩展到 JS/Java/Go"""

    def test_js_dead_and_alive(self):
        root = make_project({"a.js": """
function used() { return 1; }
function deadFn() { return 2; }
class UsedClass {}
class DeadClass {}
function run() { used(); new UsedClass(); }
"""})
        r = tf.detect_dead_code([os.path.join(root, "a.js")])
        dead_funcs = [d["name"] for d in r["dead_functions"]]
        dead_classes = [d["name"] for d in r["dead_classes"]]
        self.assertIn("deadFn", dead_funcs)
        self.assertNotIn("used", dead_funcs)
        self.assertNotIn("run", dead_funcs)     # 入口函数豁免
        self.assertIn("DeadClass", dead_classes)
        self.assertNotIn("UsedClass", dead_classes)

    def test_java_dead_and_alive(self):
        root = make_project({"App.java": """
public class App {
    public void run() { helper(); }
    public void helper() { }
    public void deadMethod() { }
    public static void main(String[] args) { new App().run(); }
}
"""})
        r = tf.detect_dead_code([os.path.join(root, "App.java")])
        dead_funcs = [d["name"] for d in r["dead_functions"]]
        self.assertIn("deadMethod", dead_funcs)
        self.assertNotIn("helper", dead_funcs)
        self.assertNotIn("main", dead_funcs)    # 入口豁免

    def test_go_dead_and_alive(self):
        root = make_project({"main.go": """
package main
type Greeter struct{ Base }
type Base struct{}
func (g *Greeter) Hello() { g.log() }
func (g *Greeter) log() {}
func unusedFn() {}
func main() { g := &Greeter{}; g.Hello() }
"""})
        r = tf.detect_dead_code([os.path.join(root, "main.go")])
        dead_funcs = [d["name"] for d in r["dead_functions"]]
        dead_classes = [d["name"] for d in r["dead_classes"]]
        self.assertIn("unusedFn", dead_funcs)
        self.assertNotIn("log", dead_funcs)     # g.log() 被调用
        self.assertNotIn("Base", dead_classes)  # 被嵌入 → 不算死
        self.assertNotIn("Greeter", dead_classes)  # &Greeter{} 实例化

    def test_rust_dead_and_alive(self):
        root = make_project({"main.rs": """
struct Greeter;
struct UnusedStruct;
trait Speak { fn speak(&self); }
impl Speak for Greeter { fn speak(&self) {} }
fn greet(g: &Greeter) { g.speak(); }
fn unused_fn() {}
fn main() {
    let g = Greeter;
    greet(&g);
}
"""})
        r = tf.detect_dead_code([os.path.join(root, "main.rs")])
        dead_funcs = [d["name"] for d in r["dead_functions"]]
        dead_classes = [d["name"] for d in r["dead_classes"]]
        self.assertIn("unused_fn", dead_funcs)
        self.assertNotIn("greet", dead_funcs)     # greet(&g) 被调用
        self.assertNotIn("speak", dead_funcs)     # g.speak() 调用 → 不算死
        self.assertNotIn("Greeter", dead_classes)  # let g = Greeter 实例化
        self.assertNotIn("Speak", dead_classes)    # impl Speak for Greeter → 算使用
        self.assertIn("UnusedStruct", dead_classes)  # 完全没用 → 死类

    def test_python_still_works(self):
        root = make_project({"a.py": "def f():\n    pass\ndef g():\n    f()\n"})
        r = tf.detect_dead_code([os.path.join(root, "a.py")])
        dead = [d["name"] for d in r["dead_functions"]]
        self.assertIn("g", dead)                # g 调用 f，g 自身没人调
        self.assertNotIn("f", dead)


class TestComplexityAny(unittest.TestCase):
    """v3.5：复杂度分析扩展到 JS/Java/Go"""

    def test_js_complexity(self):
        root = make_project({"a.js": """
function complex(a, b) {
  if (a) { return 1; }
  for (let i = 0; i < b; i++) { if (a && b) { continue; } }
  switch (a) { case 1: break; default: break; }
  return a ? b : 0;
}
function simple() { return 42; }
"""})
        r = tf.calculate_complexity_any(os.path.join(root, "a.js"))
        comps = {n: c for n, c, _, _ in r["functions"]}
        self.assertGreater(comps["complex"], comps["simple"])
        self.assertGreaterEqual(comps["complex"], 5)
        self.assertEqual(comps["simple"], 1)

    def test_go_complexity(self):
        root = make_project({"a.go": """
func complex(a int, b int) int {
  if a > 0 { return 1 }
  for i := 0; i < b; i++ { switch a { case 1: break; default: break } }
  if a < 0 && b > 0 { return 2 }
  return 0
}
"""})
        r = tf.calculate_complexity_any(os.path.join(root, "a.go"))
        comps = {n: c for n, c, _, _ in r["functions"]}
        self.assertGreaterEqual(comps["complex"], 4)

    def test_rust_complexity(self):
        root = make_project({"a.rs": """
fn complex(a: bool, b: u32) -> u32 {
    if a { return 1; }
    for i in 0..b { if a && b > 0 { continue; } }
    match a {
        true => 1,
        false => { if b > 2 { return 3; } 0 }
    }
    let r = foo()?;
    r
}
"""})
        r = tf.calculate_complexity_any(os.path.join(root, "a.rs"))
        comps = {n: c for n, c, _, _ in r["functions"]}
        self.assertGreaterEqual(comps["complex"], 4)   # if+for+match+&&+? 计数
        self.assertGreater(comps["complex"], 1)

    def test_java_complexity_and_unsupported(self):
        root = make_project({"A.java": "class A { void m() { if (x) y(); } }"})
        r = tf.calculate_complexity_any(os.path.join(root, "A.java"))
        self.assertIsNotNone(r)
        self.assertGreaterEqual(r["max_complexity"], 1)
        self.assertIsNone(tf.calculate_complexity_any("x.rb"))   # 不支持的语言

    def test_python_complexity_unchanged(self):
        self.assertIsNone(tf.calculate_complexity_any("test.js"))  # 兼容旧行为


class TestCodeSmells(unittest.TestCase):
    """v3.7：代码异味检测（--smells）"""

    def test_python_long_function_and_params(self):
        root = make_project({"bad.py": """
def do_many_things(a, b, c, d, e, f, g):
    x = a + 1
    for i in range(10):
        if x > 3:
            for j in range(5):
                if i == j:
                    for k in range(2):
                        y = 42
    return x
""" + "\n".join("    x = %d  # 填行数" % i for i in range(60)) + "\n"})
        r = tf.detect_code_smells([os.path.join(root, "bad.py")], root=root)
        types = {s["type"] for s in r["smells"]}
        self.assertIn("long_parameter_list", types)   # 7 个参数 > 5
        self.assertIn("long_function", types)          # 60+ 行 > 50
        self.assertIn("deep_nesting", types)           # for→if→for→if→for 5 层 > 4

    def test_duplicate_condition_and_magic_number(self):
        root = make_project({"c.py": """
def check(v):
    if v == 1:
        return 0
    elif v == 1:
        return 1
    return -1

def calc(n):
    for i in range(10):
        n += 42
    return n
"""})
        r = tf.detect_code_smells([os.path.join(root, "c.py")], root=root)
        types = {s["type"] for s in r["smells"]}
        self.assertIn("duplicate_condition", types)    # v == 1 出现两次
        self.assertIn("magic_number", types)           # 42
        # range(10) 的 10 不报、1/-1/3 等常见数字不报
        for s in r["smells"]:
            if s["type"] == "magic_number":
                self.assertEqual(s["detail"], "魔法数字: 42")

    def test_heuristic_params_all_langs(self):
        root = make_project({
            "a.js": "function handler(a, b, c, d, e, f, g, h) { return a; }\n",
            "A.java": "class A { void m(int a, int b, int c, int d, int e, int f) { } }\n",
            "g.go": "func F(a, b, c, d, e, f, g int) {}\n",
            "r.rs": "fn f(a: i32, b: i32, c: i32, d: i32, e: i32, f: i32) {}\n",
        })
        files = [os.path.join(root, n) for n in ("a.js", "A.java", "g.go", "r.rs")]
        r = tf.detect_code_smells(files, root=root)
        params = [s for s in r["smells"] if s["type"] == "long_parameter_list"]
        self.assertGreaterEqual(len(params), 4)        # 4 语言都能检出

    def test_test_file_magic_number_skipped(self):
        root = make_project({"test_util.py": "def t():\n    return 424242\n"})
        r = tf.detect_code_smells([os.path.join(root, "test_util.py")], root=root)
        self.assertFalse(any(s["type"] == "magic_number" for s in r["smells"]))

    def test_god_class(self):
        root = make_project({"Big.java": "class Big {\n" + "\n".join(
            "    void m%d() { }" % i for i in range(25)) + "\n}\n"})
        r = tf.detect_code_smells([os.path.join(root, "Big.java")], root=root)
        self.assertTrue(any(s["type"] == "god_class" for s in r["smells"]))

    def test_cli_smells_command(self):
        root = make_project({"a.py": "def f(x, y, z, w, v, u):\n    return x\n"})
        farm = tf.TreeFarm(root)
        farm.plant()
        out = farm.smells()
        self.assertIn("代码异味检测报告", out)
        self.assertIn("长参数列表", out)


class TestRepl(unittest.TestCase):
    """v3.7：交互式 REPL 模式（--repl）"""

    def test_repl_dispatch_smells_debt(self):
        from treefarm.cli import _repl_dispatch
        root = make_project({"a.py": "def f(x, y, z, w, v, u):\n    return x\n"})
        farm = tf.TreeFarm(root)
        farm.plant()
        self.assertTrue(_repl_dispatch(farm, root, "smells", []))
        self.assertTrue(_repl_dispatch(farm, root, "debt", []))
        self.assertTrue(_repl_dispatch(farm, root, "help", []))
        self.assertTrue(_repl_dispatch(farm, root, "help", ["smells"]))
        self.assertFalse(_repl_dispatch(farm, root, "exit", []))   # exit → False

    def test_repl_subprocess_pipe(self):
        root = make_project({"a.py": "def f(x, y, z, w, v, u):\n    return x\n",
                             "b.py": "import a\n"})
        script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "tree_farm.py")
        proc = subprocess.run(
            [sys.executable, script, root, "--repl"],
            input="help\nsmells\nbad-command\nbrief\nexit\n",
            capture_output=True, text=True, timeout=60)
        self.assertIn("树场 REPL", proc.stdout)
        self.assertIn("代码异味检测报告", proc.stdout)
        self.assertIn("长参数列表", proc.stdout)     # 6 个参数 > 5
        self.assertIn("未知命令: bad-command", proc.stdout)
        self.assertIn("树场简报", proc.stdout)        # brief 输出
        self.assertIn("已退出", proc.stdout)

    def test_repl_supports_analysis_commands(self):
        from treefarm.cli import _repl_dispatch
        root = make_project({"a.py": "def f():\n    pass\n", "b.py": "from a import f\n"})
        farm = tf.TreeFarm(root)
        farm.plant()
        for cmd in ("dead-code", "circular-deps", "architecture", "graph",
                    "duplicates", "duplicates-func", "trash", "complexity"):
            self.assertTrue(_repl_dispatch(farm, root, cmd, []))


class TestCallGraphJSON(unittest.TestCase):
    """v3.5：调用图 JSON 结构化输出"""

    def test_json_data_structure(self):
        root = make_project({"a.py": "import b\n", "b.py": "def f(): pass\n"})
        farm = tf.TreeFarm(root)
        farm.plant()
        data = farm.call_graph_data()
        self.assertIn("nodes", data)
        self.assertIn("edges", data)
        labels = [n["label"] for n in data["nodes"]]
        self.assertIn("a.py", labels)
        self.assertIn("b.py", labels)
        rels = {e["relation"] for e in data["edges"]}
        self.assertTrue(rels)

    def test_json_file_level(self):
        root = make_project({"a.py": "def f(): pass\n", "b.py": "from a import f\ndef r(): f()\n"})
        farm = tf.TreeFarm(root)
        farm.plant()
        data = farm.call_graph_data(os.path.join(root, "b.py"))
        self.assertTrue(any("a.py" in n["label"] for n in data["nodes"]))


class TestDuplicatesFunc(unittest.TestCase):
    """v3.5：函数级重复代码检测"""

    def test_detects_duplicate_functions(self):
        root = make_project({"a.js": """
function compute(x) {
  const y = x * 2;
  const z = y + 1;
  return z - 3;
}
function other() { return 1; }
""",
                             "b.js": """
function compute2(x) {
  const y = x * 2;
  const z = y + 1;
  return z - 3;
}
"""})
        farm = tf.TreeFarm(root)
        farm.plant()
        out = farm.duplicates_func()
        self.assertIn("compute", out)
        self.assertIn("compute2", out)
        self.assertIn("%", out)                 # 函数体高度相似（函数名不同，非 100%）

    def test_no_duplicate_functions(self):
        root = make_project({"a.js": "function a() { return 1; }\n",
                             "b.js": "function b() { return 2; }\n"})
        farm = tf.TreeFarm(root)
        farm.plant()
        out = farm.duplicates_func()
        self.assertIn("没有发现", out)

    def test_bucket_optimization_finds_adjacent_sizes(self):
        """v3.5 分桶优化：大小相近的函数（相邻桶）仍能查出重复"""
        root = make_project({"a.py": "def f(x):\n    y = x * 2 + 1\n    z = y - 3\n    return z\n",
                             "b.py": "def g(x):\n    y = x * 2 + 1\n    z = y - 3\n    return z\n"
                                     "def h(x):\n    y = x * 2 + 1\n    z = y - 3\n    return z\n"
                                     "def k(x):\n    return x + 100\n"})
        farm = tf.TreeFarm(root)
        farm.plant()
        out = farm.duplicates_func()
        self.assertIn("f", out)                 # a.py:f 与 b.py:g/h 重复
        self.assertIn("g", out)
        self.assertIn("h", out)


class TestTechnicalDebt(unittest.TestCase):
    """v3.5：技术债务评估"""

    def test_healthy_project(self):
        root = make_project({"a.py": "def f():\n    return 1\n",
                             "b.py": "def g():\n    return 2\n"})
        farm = tf.TreeFarm(root)
        farm.plant()
        out = farm.debt()
        self.assertIn("技术债务分", out)
        self.assertIn("等级", out)

    def test_dead_code_suggestions(self):
        root = make_project({"a.py": "def dead1():\n    return 1\n"
                                     "def dead2():\n    return 2\n"
                                     "def dead3():\n    return 3\n"
                                     "def dead4():\n    return 4\n"
                                     "def dead5():\n    return 5\n"
                                     "def used():\n    return 6\n"})
        farm = tf.TreeFarm(root)
        farm.plant()
        out = farm.debt()
        self.assertIn("死代码", out)

    def test_scores_are_bounded(self):
        root = make_project({"a.py": "def f():\n    return 1\n"})
        farm = tf.TreeFarm(root)
        farm.plant()
        out = farm.debt()
        # 总分是 0~100 整数，格式 "【综合技术债务分】 N/100"
        import re as _re
        m = _re.search(r"【综合技术债务分】 (\d+)/100", out)
        self.assertIsNotNone(m)
        self.assertLessEqual(int(m.group(1)), 100)


class TestModuleStructure(unittest.TestCase):
    """v3.6：单文件拆分多模块后的结构回归测试"""

    def test_entry_re_exports_all_public_symbols(self):
        """入口 tree_farm.py 必须聚合导出全部公共符号（旧 import tree_farm 用法不变）"""
        for name in ("TreeFarm", "GeneBank", "TrashBin", "Session", "WeedIndex",
                     "SmallTree", "LLMClient", "FileCache", "SymbolIndex",
                     "extract_genes", "extract_js_call_graph", "extract_java_call_graph",
                     "extract_go_call_graph", "impact_analysis", "detect_dead_code",
                     "detect_architecture_layers", "calculate_complexity_any",
                     "semantic_search", "normalize_identifier", "scan", "load_config",
                     "stable_id", "normalize_gene", "VERSION", "SCHEMA_VERSION"):
            self.assertTrue(hasattr(tf, name), f"入口缺符号: {name}")

    def test_submodules_importable(self):
        """treefarm 包的每个子模块都能独立导入"""
        from treefarm import analysis, cli, common, config, core, parser, storage  # noqa: F401
        self.assertTrue(callable(common.scan))
        self.assertTrue(callable(parser.extract_genes))
        self.assertTrue(callable(storage.GeneBank))
        self.assertTrue(callable(config.load_config))
        self.assertTrue(callable(analysis.impact_analysis))
        self.assertTrue(callable(core.TreeFarm))
        self.assertTrue(callable(cli.main))

    def test_entry_class_is_core_class(self):
        """tf.TreeFarm 与 treefarm.core.TreeFarm 是同一个类（非重复实现）"""
        from treefarm.core import TreeFarm as CoreTreeFarm
        self.assertIs(tf.TreeFarm, CoreTreeFarm)

    def test_version_bumped(self):
        self.assertEqual(tf.VERSION, "4.9.6")


if __name__ == "__main__":
    unittest.main(verbosity=2)
