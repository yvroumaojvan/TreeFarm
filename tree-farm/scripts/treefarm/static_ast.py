"""
轻量AST辅助分析模块（v4.4新增）
开关控制，默认关闭，--static-ast才启用
作为沙箱动态分析的补充，不增加基础运行负担
"""
import ast
import re
from typing import List, Dict, Any, Set, Tuple, Optional


class LightASTAnalyzer:
    """轻量AST分析器"""

    def __init__(self):
        self.issues = []
        self.decorated_funcs = set()
        self.dynamic_calls = set()

    def analyze_file(self, filepath: str, rel_path: str = "") -> List[Dict[str, Any]]:
        """分析单个文件"""
        self.issues = []
        self.decorated_funcs = set()
        self.dynamic_calls = set()
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                source = f.read()
            tree = ast.parse(source, filename=filepath)
            self._walk_tree(tree, rel_path or filepath, source)
            self._analyze_type_confusion(tree, rel_path or filepath, source)
        except SyntaxError as e:
            self.issues.append({
                "file": rel_path or filepath, "line": e.lineno, "type": "语法错误",
                "severity": "medium", "desc": f"Python语法错误: {e.msg}",
                "code": f"SyntaxError: {e.msg}"
            })
        except Exception as e:
            pass
        return self.issues

    def _walk_tree(self, tree: ast.AST, filepath: str, source: str):
        """遍历AST树"""
        for node in ast.walk(tree):
            # 1. 检测动态代码执行
            if isinstance(node, ast.Call):
                self._check_dynamic_exec(node, filepath, source)
                self._check_ast_sandbox_bypass(node, filepath, source)

            # 2. 检测装饰器注册的函数
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.decorator_list:
                    self.decorated_funcs.add(node.name)
                    for dec in node.decorator_list:
                        dec_name = self._get_decorator_name(dec)
                        if dec_name in ("app.route", "router.post", "router.get",
                                         "app.get", "app.post", "bp.route", "api_view"):
                            self.decorated_funcs.add(node.name)

            # 3. 检测lambda中的危险调用
            if isinstance(node, ast.Lambda):
                self._check_lambda_danger(node, filepath, source)

            # 4. 检测getattr动态导入
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    if node.func.attr == "getattr":
                        self._check_getattr_import(node, filepath, source)

    def _check_dynamic_exec(self, node: ast.Call, filepath: str, source: str):
        """检测动态代码执行：eval、exec、compile"""
        if isinstance(node.func, ast.Name):
            if node.func.id in ("eval", "exec", "compile"):
                severity = "high" if node.func.id in ("eval", "exec") else "medium"
                self.issues.append({
                    "file": filepath, "line": node.lineno, "type": "动态代码执行",
                    "severity": severity,
                    "desc": f"使用{node.func.id}()执行动态代码，可能导致代码注入/RCE",
                    "code": self._get_line_source(source, node.lineno)
                })
                self.dynamic_calls.add(f"{filepath}:{node.lineno}:{node.func.id}")

    def _check_ast_sandbox_bypass(self, node: ast.Call, filepath: str, source: str):
        """检测AST沙箱绕过：lambda __import__、getattr动态导入等"""
        # 检测 __import__ 调用
        if isinstance(node.func, ast.Name) and node.func.id == "__import__":
            self.issues.append({
                "file": filepath, "line": node.lineno, "type": "沙箱绕过",
                "severity": "critical",
                "desc": "直接调用__import__()动态导入模块，可绕过AST沙箱白名单",
                "code": self._get_line_source(source, node.lineno)
            })

        # 检测 getattr(__builtins__, 'eval') 模式
        if isinstance(node.func, ast.Attribute) and node.func.attr == "getattr":
            if len(node.args) >= 2:
                arg1 = node.args[0]
                if isinstance(arg1, ast.Name) and arg1.id in ("__builtins__", "builtins"):
                    self.issues.append({
                        "file": filepath, "line": node.lineno, "type": "沙箱绕过",
                        "severity": "critical",
                        "desc": "getattr(__builtins__, ...)从内置命名空间获取危险函数，可绕过沙箱",
                        "code": self._get_line_source(source, node.lineno)
                    })

    def _check_lambda_danger(self, node: ast.Lambda, filepath: str, source: str):
        """检测lambda中的危险调用：(lambda: __import__('os').system('id'))()"""
        try:
            lambda_source = ast.unparse(node)
            if "__import__" in lambda_source or "eval(" in lambda_source or "exec(" in lambda_source:
                self.issues.append({
                    "file": filepath, "line": node.lineno, "type": "沙箱绕过",
                    "severity": "critical",
                    "desc": "lambda表达式中包含动态导入/执行，是常见的AST沙箱绕过手段",
                    "code": self._get_line_source(source, node.lineno)
                })
        except Exception:
            pass

    def _check_getattr_import(self, node: ast.Call, filepath: str, source: str):
        """检测getattr动态导入危险模块"""
        if len(node.args) >= 2:
            try:
                if isinstance(node.args[1], ast.Constant):
                    attr_name = node.args[1].value
                    if attr_name in ("system", "popen", "eval", "exec", "compile",
                                     "__import__", "load_module"):
                        self.issues.append({
                            "file": filepath, "line": node.lineno, "type": "沙箱绕过",
                            "severity": "high",
                            "desc": f"getattr动态获取危险函数'{attr_name}'，可能绕过静态检测",
                            "code": self._get_line_source(source, node.lineno)
                        })
            except Exception:
                pass

    def _analyze_type_confusion(self, tree: ast.AST, filepath: str, source: str):
        """分析类型混淆：变量在不同分支赋值不同类型"""
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._check_func_type_confusion(node, filepath, source)

    def _check_func_type_confusion(self, func_node: ast.FunctionDef, filepath: str, source: str):
        """检查函数内的类型混淆"""
        var_types = {}  # var_name -> set of types
        for node in ast.walk(func_node):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        vtype = self._infer_value_type(node.value)
                        if vtype:
                            if target.id not in var_types:
                                var_types[target.id] = set()
                            var_types[target.id].add(vtype)

        # 检查是否有变量被赋值了不同类型
        for var_name, types in var_types.items():
            if len(types) >= 2:
                # 排除常见的合法类型转换
                if not (types == {"str", "NoneType"} or types == {"int", "NoneType"}):
                    self.issues.append({
                        "file": filepath, "line": func_node.lineno, "type": "类型混淆",
                        "severity": "low",
                        "desc": f"变量'{var_name}'在函数内被赋值为不同类型{types}，可能导致运行时类型错误",
                        "code": f"def {func_node.name}(...): {var_name} types: {types}"
                    })

    def _infer_value_type(self, node: ast.AST) -> Optional[str]:
        """推断AST节点的值类型"""
        if isinstance(node, ast.Constant):
            if node.value is None:
                return "NoneType"
            return type(node.value).__name__
        if isinstance(node, ast.List):
            return "list"
        if isinstance(node, ast.Dict):
            return "dict"
        if isinstance(node, ast.Tuple):
            return "tuple"
        if isinstance(node, ast.Set):
            return "set"
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in ("str", "int", "float", "list", "dict", "tuple", "set", "bool"):
                    return node.func.id
        if isinstance(node, ast.Name):
            return None  # 变量引用，无法确定类型
        return None

    def _get_decorator_name(self, node: ast.AST) -> str:
        """获取装饰器名称"""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parts = []
            current = node
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
            return ".".join(reversed(parts))
        if isinstance(node, ast.Call):
            return self._get_decorator_name(node.func)
        return "unknown"

    def _get_line_source(self, source: str, lineno: int) -> str:
        """获取指定行的源码"""
        lines = source.split("\n")
        if 0 < lineno <= len(lines):
            return lines[lineno - 1].strip()[:100]
        return ""

    def get_decorated_functions(self) -> Set[str]:
        """获取所有被装饰器注册的函数名（用于减少死代码误报）"""
        return self.decorated_funcs

    def get_dynamic_calls(self) -> Set[str]:
        """获取所有动态调用位置"""
        return self.dynamic_calls
