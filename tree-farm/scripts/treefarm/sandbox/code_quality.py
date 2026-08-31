# -*- coding: utf-8 -*-
"""树场小沙箱 —— 轻量级代码质量分析器（v3.9 新增，对标 SonarQube）。

零依赖实现，不依赖 SonarQube/radon 等第三方库。
功能：
  1. 圈复杂度分析（函数级，基于分支节点计数）
  2. 代码重复检测（n-gram 相似度）
  3. 代码异味检测（长函数、长参数、嵌套过深、魔法数字、大类）
  4. 技术债务评估（基于异味数量和严重程度估算修复时间）
  5. 与沙箱结合：静态分析质量 + 动态验证功能

设计原则：极致轻量化，手机可运行，只实现最常用的质量度量。
"""

import ast
import re
from typing import Any, Dict, List, Optional, Tuple


class CodeIssue:
    """代码问题。"""

    def __init__(self, issue_type: str, description: str, severity: str,
                 lineno: int, end_lineno: int = 0,
                 fix_suggestion: str = ""):
        self.issue_type = issue_type
        self.description = description
        self.severity = severity  # high / medium / low
        self.lineno = lineno
        self.end_lineno = end_lineno or lineno
        self.fix_suggestion = fix_suggestion

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.issue_type,
            "description": self.description,
            "severity": self.severity,
            "lineno": self.lineno,
            "end_lineno": self.end_lineno,
            "fix_suggestion": self.fix_suggestion,
        }


class CodeQualityAnalyzer:
    """轻量级代码质量分析器。

    用法：
        analyzer = CodeQualityAnalyzer()
        report = analyzer.analyze(code)
        print(report['complexity'])
        print(report['issues'])
    """

    # 复杂度阈值
    COMPLEXITY_THRESHOLDS = {
        "low": 10,      # 1-10 低风险
        "medium": 20,   # 11-20 中等风险
        "high": 50,     # 21-50 高风险
        # >50 极高风险
    }

    # 异味阈值
    LONG_FUNCTION_LINES = 50
    LONG_PARAMS = 5
    DEEP_NESTING = 4
    LARGE_CLASS_LINES = 300
    LARGE_CLASS_METHODS = 20
    MAGIC_NUMBER_MIN = 2  # 排除 0, 1

    def __init__(self):
        pass

    def analyze(self, code: str, filename: str = "<code>") -> Dict[str, Any]:
        """分析代码质量。

        Args:
            code: Python 代码字符串
            filename: 文件名（用于报告）

        Returns:
            质量报告字典
        """
        report = {
            "filename": filename,
            "complexity": {},
            "issues": [],
            "metrics": {},
            "technical_debt": {},
        }

        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            report["issues"].append(CodeIssue(
                "syntax_error", f"语法错误: {e}", "high",
                getattr(e, "lineno", 1),
                fix_suggestion="修复语法错误后重新分析"
            ).to_dict())
            return report

        lines = code.splitlines()
        total_lines = len(lines)

        # 1. 复杂度分析
        complexity_report = self._analyze_complexity(tree)
        report["complexity"] = complexity_report

        # 2. 代码异味检测
        issues = self._detect_smells(tree, lines)
        report["issues"] = [i.to_dict() for i in issues]

        # 3. 度量指标
        report["metrics"] = {
            "total_lines": total_lines,
            "code_lines": sum(1 for l in lines if l.strip() and not l.strip().startswith("#")),
            "comment_lines": sum(1 for l in lines if l.strip().startswith("#")),
            "blank_lines": sum(1 for l in lines if not l.strip()),
            "functions": complexity_report["total_functions"],
            "classes": complexity_report["total_classes"],
            "avg_complexity": complexity_report["average"],
            "max_complexity": complexity_report["max"],
        }

        # 4. 技术债务评估
        report["technical_debt"] = self._estimate_technical_debt(report["issues"])

        return report

    def _analyze_complexity(self, tree: ast.AST) -> Dict[str, Any]:
        """分析圈复杂度。"""
        functions = []

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                complexity = self._calculate_complexity(node)
                functions.append({
                    "name": node.name,
                    "lineno": node.lineno,
                    "complexity": complexity,
                    "risk": self._classify_risk(complexity),
                })

        classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]

        total = sum(f["complexity"] for f in functions)
        avg = round(total / len(functions), 1) if functions else 0
        max_c = max((f["complexity"] for f in functions), default=0)

        return {
            "functions": functions,
            "total_functions": len(functions),
            "total_classes": len(classes),
            "total_complexity": total,
            "average": avg,
            "max": max_c,
            "high_risk_count": sum(1 for f in functions if f["risk"] in ("high", "very_high")),
        }

    def _calculate_complexity(self, func_node: ast.AST) -> int:
        """计算函数的圈复杂度。

        基础复杂度为 1，每遇到一个分支节点加 1：
        if/elif, for, while, except, with, and, or, 三元表达式
        """
        complexity = 1
        for node in ast.walk(func_node):
            if isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While,
                                 ast.ExceptHandler, ast.With, ast.AsyncWith)):
                complexity += 1
            elif isinstance(node, ast.BoolOp):
                complexity += len(node.values) - 1  # and/or 链
            elif isinstance(node, ast.IfExp):
                complexity += 1
            elif isinstance(node, ast.Assert):
                complexity += 1
        return complexity

    def _classify_risk(self, complexity: int) -> str:
        """根据复杂度分类风险等级。"""
        if complexity <= 10:
            return "low"
        elif complexity <= 20:
            return "medium"
        elif complexity <= 50:
            return "high"
        else:
            return "very_high"

    def _detect_smells(self, tree: ast.AST, lines: List[str]) -> List[CodeIssue]:
        """检测代码异味。"""
        issues = []

        for node in ast.walk(tree):
            # 长函数
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_lines = node.end_lineno - node.lineno + 1 if hasattr(node, "end_lineno") else 0
                if func_lines > self.LONG_FUNCTION_LINES:
                    issues.append(CodeIssue(
                        "long_function",
                        f"函数 '{node.name}' 有 {func_lines} 行（超过 {self.LONG_FUNCTION_LINES} 行阈值）",
                        "medium", node.lineno, node.end_lineno,
                        f"建议将函数拆分为多个小函数，每个不超过 {self.LONG_FUNCTION_LINES} 行"
                    ))

                # 长参数列表
                args = node.args
                total_args = len(args.args) + len(args.kwonlyargs)
                if total_args > self.LONG_PARAMS:
                    issues.append(CodeIssue(
                        "long_parameter_list",
                        f"函数 '{node.name}' 有 {total_args} 个参数（超过 {self.LONG_PARAMS} 个阈值）",
                        "low", node.lineno,
                        fix_suggestion="考虑使用数据类或字典封装相关参数"
                    ))

                # 嵌套过深
                max_depth = self._calculate_max_nesting(node)
                if max_depth > self.DEEP_NESTING:
                    issues.append(CodeIssue(
                        "deep_nesting",
                        f"函数 '{node.name}' 嵌套深度 {max_depth} 层（超过 {self.DEEP_NESTING} 层阈值）",
                        "medium", node.lineno,
                        fix_suggestion="考虑使用提前返回（guard clause）或提取函数减少嵌套"
                    ))

            # 大类
            elif isinstance(node, ast.ClassDef):
                class_lines = node.end_lineno - node.lineno + 1 if hasattr(node, "end_lineno") else 0
                methods = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                if class_lines > self.LARGE_CLASS_LINES or len(methods) > self.LARGE_CLASS_METHODS:
                    issues.append(CodeIssue(
                        "large_class",
                        f"类 '{node.name}' 有 {class_lines} 行、{len(methods)} 个方法",
                        "medium", node.lineno, node.end_lineno,
                        fix_suggestion="考虑使用组合模式或拆分为多个职责单一的类"
                    ))

            # 魔法数字
            elif isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                if abs(node.value) >= self.MAGIC_NUMBER_MIN and node.value not in (2, 10, 100, 1000):
                    # 检查是否在赋值或比较中
                    issues.append(CodeIssue(
                        "magic_number",
                        f"魔法数字 {node.value}",
                        "low", node.lineno,
                        fix_suggestion="考虑定义为命名常量以提高可读性"
                    ))

        # 重复条件分支（if/elif 中相同的代码块）
        issues.extend(self._detect_duplicate_branches(tree))

        return issues

    def _calculate_max_nesting(self, node: ast.AST, current_depth: int = 0) -> int:
        """计算函数内的最大嵌套深度。"""
        max_depth = current_depth
        nesting_nodes = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With,
                         ast.AsyncWith, ast.Try)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, nesting_nodes):
                child_depth = self._calculate_max_nesting(child, current_depth + 1)
                max_depth = max(max_depth, child_depth)
            else:
                child_depth = self._calculate_max_nesting(child, current_depth)
                max_depth = max(max_depth, child_depth)
        return max_depth

    def _detect_duplicate_branches(self, tree: ast.AST) -> List[CodeIssue]:
        """检测重复的条件分支。"""
        issues = []
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                # 比较 if 体和 elif/else 体是否相同
                bodies = [node.body]
                if node.orelse:
                    if isinstance(node.orelse[0], ast.If):
                        bodies.append(node.orelse[0].body)
                        if node.orelse[0].orelse:
                            bodies.append(node.orelse[0].orelse)
                    else:
                        bodies.append(node.orelse)

                # 简单比较：如果两个分支的语句数量和类型完全相同，可能是重复
                for i in range(len(bodies)):
                    for j in range(i + 1, len(bodies)):
                        if self._is_duplicate_block(bodies[i], bodies[j]):
                            issues.append(CodeIssue(
                                "duplicate_branch",
                                f"第 {i+1} 和第 {j+1} 分支代码重复",
                                "low", node.lineno,
                                fix_suggestion="考虑合并重复分支或提取公共代码"
                            ))
        return issues

    def _is_duplicate_block(self, block1: List[ast.AST], block2: List[ast.AST]) -> bool:
        """简单判断两个代码块是否重复（类型序列相同）。"""
        if len(block1) != len(block2) or len(block1) < 2:
            return False
        for n1, n2 in zip(block1, block2):
            if type(n1) != type(n2):
                return False
        return True

    def _estimate_technical_debt(self, issues: List[Dict[str, Any]]) -> Dict[str, Any]:
        """估算技术债务。

        基于 SonarQube 的技术债务估算方法：
        - high 问题：每个 4 小时
        - medium 问题：每个 1 小时
        - low 问题：每个 20 分钟
        """
        hours = 0
        counts = {"high": 0, "medium": 0, "low": 0}
        for issue in issues:
            severity = issue.get("severity", "low")
            counts[severity] = counts.get(severity, 0) + 1
            if severity == "high":
                hours += 4
            elif severity == "medium":
                hours += 1
            else:
                hours += 1/3  # 20分钟

        # 格式化
        if hours < 1:
            debt_str = f"{int(hours * 60)} 分钟"
        elif hours < 8:
            debt_str = f"{hours:.1f} 小时"
        else:
            days = hours / 8
            debt_str = f"{days:.1f} 天（{hours:.0f} 小时）"

        return {
            "estimated_hours": round(hours, 1),
            "formatted": debt_str,
            "issue_counts": counts,
            "total_issues": sum(counts.values()),
        }


def analyze_code_quality(code: str, filename: str = "<code>") -> Dict[str, Any]:
    """便捷函数：分析代码质量。"""
    analyzer = CodeQualityAnalyzer()
    return analyzer.analyze(code, filename)


def extract_dependencies(code: str) -> Dict[str, List[str]]:
    """提取代码依赖（import 语句），v3.9 第13轮新增。"""
    import ast
    std_libs = {"os", "sys", "re", "json", "math", "time", "datetime", "collections",
        "itertools", "functools", "typing", "dataclasses", "enum", "subprocess",
        "threading", "multiprocessing", "asyncio", "socket", "pathlib", "shutil",
        "tempfile", "io", "string", "random", "statistics", "copy", "operator",
        "abc", "contextlib", "logging", "warnings", "argparse", "csv", "hashlib",
        "base64", "zlib", "zipfile", "tarfile", "gzip", "struct", "binascii"}
    result = {"standard": [], "third_party": [], "local": []}
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mod = alias.name.split(".")[0]
                    if mod in std_libs:
                        result["standard"].append(alias.name)
                    else:
                        result["third_party"].append(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                mod = node.module.split(".")[0]
                if mod in std_libs:
                    result["standard"].append(node.module)
                else:
                    result["third_party"].append(node.module)
    except SyntaxError:
        pass
    for k in result:
        result[k] = sorted(set(result[k]))
    return result


def detect_duplicate_code(code: str, min_lines: int = 5) -> List[Dict[str, Any]]:
    """检测重复代码块（行级），v3.9 第13轮新增。"""
    lines = code.split("\n")
    effective = [(i, l.strip()) for i, l in enumerate(lines) if l.strip() and not l.strip().startswith("#")]
    if len(effective) < min_lines * 2:
        return []
    duplicates = []
    seen = {}
    for i in range(len(effective) - min_lines + 1):
        block = tuple(effective[j][1] for j in range(i, i + min_lines))
        if block in seen and i >= seen[block] + min_lines:
            s1, s2 = seen[block], i
            duplicates.append({
                "lines": list(block),
                "occurrences": [
                    (effective[s1][0] + 1, effective[s1 + min_lines - 1][0] + 1),
                    (effective[s2][0] + 1, effective[s2 + min_lines - 1][0] + 1),
                ],
            })
        else:
            seen[block] = i
    return duplicates[:10]
