# -*- coding: utf-8 -*-
"""树场小沙箱 —— 轻量级 AST 模式匹配器（v3.9 新增，对标 Semgrep）。

零依赖实现，不依赖 Semgrep 等第三方库。
功能：
  1. AST 模式匹配：用类似代码的模式字符串搜索匹配的代码片段
  2. 通配符支持：$X 匹配任意表达式，$FUNC 匹配任意函数名
  3. 多模式组合：AND / OR / NOT
  4. 自动修复建议：基于匹配结果给出修复方案

设计原则：极致轻量化，手机可运行，不做完整的 Semgrep 兼容，
只实现最常用的模式匹配能力。
"""

import ast
import re
from typing import Any, Dict, List, Optional, Set, Tuple


class PatternMatch:
    """单次模式匹配结果。"""

    def __init__(self, lineno: int, col_offset: int, end_lineno: int,
                 end_col_offset: int, matched_text: str,
                 bindings: Dict[str, str]):
        self.lineno = lineno
        self.col_offset = col_offset
        self.end_lineno = end_lineno
        self.end_col_offset = end_col_offset
        self.matched_text = matched_text
        self.bindings = bindings  # 通配符绑定：{$X: "value"}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lineno": self.lineno,
            "col_offset": self.col_offset,
            "end_lineno": self.end_lineno,
            "end_col_offset": self.end_col_offset,
            "matched_text": self.matched_text,
            "bindings": self.bindings,
        }


class CodePatternMatcher:
    """轻量级 AST 模式匹配器。

    用法：
        matcher = CodePatternMatcher()
        matches = matcher.search(code, "open($FILE, 'r')")
        # 找到所有 open(..., 'r') 调用

    模式语法：
        $X     - 匹配任意表达式
        $FUNC  - 匹配任意函数名
        $STR   - 匹配任意字符串
        $NUM   - 匹配任意数字
        ...    - 匹配任意参数序列（函数调用中）
    """

    # 通配符模式
    _WILDCARD_RE = re.compile(r'\$([A-Z_][A-Z0-9_]*)')

    def __init__(self):
        self._patterns_cache: Dict[str, ast.AST] = {}

    def __init__(self):
        self._patterns_cache = {}
        self.last_syntax_error = None  # 最近一次语法错误（v3.9.1 新增）

    def search(self, code: str, pattern: str) -> List[PatternMatch]:
        """在代码中搜索匹配模式的片段。

        Args:
            code: 要搜索的 Python 代码
            pattern: 模式字符串（支持 $X 通配符）

        Returns:
            匹配结果列表
        """
        try:
            tree = ast.parse(code)
            self.last_syntax_error = None
        except SyntaxError as e:
            self.last_syntax_error = {
                "message": str(e),
                "lineno": e.lineno,
                "offset": e.offset,
                "text": e.text,
            }
            return []

        # 解析模式为 AST
        pattern_ast = self._parse_pattern(pattern)
        if pattern_ast is None:
            return []

        matches = []
        lines = code.splitlines()

        # 遍历代码 AST，寻找匹配
        for node in ast.walk(tree):
            if self._match_node(node, pattern_ast):
                match = self._extract_match(node, lines, pattern_ast)
                if match:
                    matches.append(match)

        return matches

    def search_all(self, code: str, patterns: List[str],
                   combine: str = "or") -> List[PatternMatch]:
        """搜索多个模式。

        Args:
            code: 要搜索的代码
            patterns: 模式列表
            combine: "or" 任一匹配 / "and" 全部匹配（同一节点）

        Returns:
            匹配结果列表
        """
        if combine == "or":
            all_matches = []
            for pattern in patterns:
                all_matches.extend(self.search(code, pattern))
            return all_matches
        else:  # and
            # 对每个节点检查是否匹配所有模式
            try:
                tree = ast.parse(code)
            except SyntaxError:
                return []
            pattern_asts = [self._parse_pattern(p) for p in patterns]
            pattern_asts = [p for p in pattern_asts if p is not None]
            if not pattern_asts:
                return []

            matches = []
            lines = code.splitlines()
            for node in ast.walk(tree):
                if all(self._match_node(node, p) for p in pattern_asts):
                    match = self._extract_match(node, lines, pattern_asts[0])
                    if match:
                        matches.append(match)
            return matches

    def _parse_pattern(self, pattern: str) -> Optional[ast.AST]:
        """将模式字符串解析为 AST。"""
        if pattern in self._patterns_cache:
            return self._patterns_cache[pattern]

        # 替换通配符为合法的 Python 表达式
        # $X -> __WILDCARD_X__
        replaced = self._WILDCARD_RE.sub(r'__WILDCARD_\1__', pattern)

        try:
            # 尝试作为表达式解析
            tree = ast.parse(replaced, mode="eval")
            result = tree.body
        except SyntaxError:
            try:
                # 尝试作为语句解析
                tree = ast.parse(replaced)
                if tree.body:
                    result = tree.body[0]
                else:
                    result = None
            except SyntaxError:
                result = None

        self._patterns_cache[pattern] = result
        return result

    def _match_node(self, node: ast.AST, pattern: ast.AST) -> bool:
        """检查节点是否匹配模式。"""
        if pattern is None:
            return False

        # 通配符节点：__WILDCARD_X__ 匹配任意表达式
        if isinstance(pattern, ast.Name) and pattern.id.startswith("__WILDCARD_"):
            return True

        # 类型必须匹配
        if type(node) != type(pattern):
            return False

        # 逐字段比较
        for field in node._fields:
            node_val = getattr(node, field, None)
            pattern_val = getattr(pattern, field, None)

            if not self._match_field(node_val, pattern_val):
                return False

        return True

    def _match_field(self, node_val: Any, pattern_val: Any) -> bool:
        """比较字段值是否匹配。"""
        # 通配符匹配
        if isinstance(pattern_val, ast.Name) and pattern_val.id.startswith("__WILDCARD_"):
            return True

        # 都是 AST 节点
        if isinstance(node_val, ast.AST) and isinstance(pattern_val, ast.AST):
            return self._match_node(node_val, pattern_val)

        # 都是列表
        if isinstance(node_val, list) and isinstance(pattern_val, list):
            if len(pattern_val) == 1 and isinstance(pattern_val[0], ast.Expr):
                # 检查是否是 ... 通配符（Ellipsis）
                if isinstance(pattern_val[0].value, ast.Constant) and pattern_val[0].value.value is Ellipsis:
                    return True  # ... 匹配任意参数列表
            if len(node_val) != len(pattern_val):
                return False
            return all(self._match_field(n, p) for n, p in zip(node_val, pattern_val))

        # 常量比较
        return node_val == pattern_val

    def _extract_match(self, node: ast.AST, lines: List[str],
                       pattern: ast.AST) -> Optional[PatternMatch]:
        """从匹配节点提取匹配信息。"""
        if not hasattr(node, "lineno"):
            return None

        lineno = node.lineno
        col_offset = getattr(node, "col_offset", 0)
        end_lineno = getattr(node, "end_lineno", lineno)
        end_col_offset = getattr(node, "end_col_offset", col_offset + 10)

        # 提取匹配的文本
        if lineno == end_lineno and lineno <= len(lines):
            matched_text = lines[lineno - 1][col_offset:end_col_offset]
        else:
            matched_text = ast.unparse(node) if hasattr(ast, "unparse") else ""

        # 提取通配符绑定
        bindings = self._extract_bindings(node, pattern)

        return PatternMatch(
            lineno=lineno,
            col_offset=col_offset,
            end_lineno=end_lineno,
            end_col_offset=end_col_offset,
            matched_text=matched_text,
            bindings=bindings,
        )

    def _extract_bindings(self, node: ast.AST, pattern: ast.AST) -> Dict[str, str]:
        """提取通配符绑定。"""
        bindings = {}
        if isinstance(pattern, ast.Name) and pattern.id.startswith("__WILDCARD_"):
            name = pattern.id.replace("__WILDCARD_", "$")
            try:
                bindings[name] = ast.unparse(node) if hasattr(ast, "unparse") else str(node)
            except Exception:
                bindings[name] = str(node)
            return bindings

        if type(node) == type(pattern):
            for field in node._fields:
                node_val = getattr(node, field, None)
                pattern_val = getattr(pattern, field, None)
                bindings.update(self._extract_bindings_from_field(node_val, pattern_val))

        return bindings

    def _extract_bindings_from_field(self, node_val: Any,
                                      pattern_val: Any) -> Dict[str, str]:
        """从字段值中提取通配符绑定。"""
        bindings = {}
        if isinstance(pattern_val, ast.Name) and pattern_val.id.startswith("__WILDCARD_"):
            name = pattern_val.id.replace("__WILDCARD_", "$")
            try:
                bindings[name] = ast.unparse(node_val) if hasattr(ast, "unparse") else str(node_val)
            except Exception:
                bindings[name] = str(node_val)
        elif isinstance(node_val, ast.AST) and isinstance(pattern_val, ast.AST):
            bindings.update(self._extract_bindings(node_val, pattern_val))
        elif isinstance(node_val, list) and isinstance(pattern_val, list):
            for n, p in zip(node_val, pattern_val):
                bindings.update(self._extract_bindings_from_field(n, p))
        return bindings


# 预置安全检测模式（对标 Semgrep 规则库）
SECURITY_PATTERNS = {
    "hardcoded_password": {
        "pattern": "$VAR = '$PASSWORD'",
        "description": "可能的硬编码密码",
        "severity": "medium",
        "filter": lambda m: any(kw in m.bindings.get("$VAR", "").lower()
                                 for kw in ("password", "passwd", "pwd", "secret", "token", "api_key", "apikey")),
    },
    "dangerous_eval": {
        "pattern": "eval($EXPR)",
        "description": "危险的 eval 调用（代码注入风险）",
        "severity": "high",
    },
    "dangerous_exec": {
        "pattern": "exec($CODE)",
        "description": "危险的 exec 调用（代码注入风险）",
        "severity": "high",
    },
    "dangerous_compile": {
        "pattern": "compile($CODE)",
        "description": "危险的 compile 调用",
        "severity": "medium",
    },
    "shell_injection_system": {
        "pattern": "os.system($CMD)",
        "description": "可能的命令注入（os.system）",
        "severity": "high",
    },
    "shell_injection_popen": {
        "pattern": "subprocess.Popen($CMD, shell=True)",
        "description": "命令注入风险（shell=True）",
        "severity": "high",
    },
    "shell_injection_call": {
        "pattern": "subprocess.call($CMD, shell=True)",
        "description": "命令注入风险（shell=True）",
        "severity": "high",
    },
    "sql_injection": {
        "pattern": "$CURSOR.execute($QUERY)",
        "description": "可能的 SQL 注入",
        "severity": "medium",
    },
    "insecure_pickle": {
        "pattern": "pickle.loads($DATA)",
        "description": "不安全的反序列化（pickle.loads 可执行任意代码）",
        "severity": "high",
    },
    "insecure_yaml": {
        "pattern": "yaml.load($DATA)",
        "description": "不安全的 YAML 加载（应使用 yaml.safe_load）",
        "severity": "high",
    },
    "weak_hash_md5": {
        "pattern": "hashlib.md5()",
        "description": "弱哈希算法 MD5（不适合安全场景）",
        "severity": "low",
    },
    "weak_hash_sha1": {
        "pattern": "hashlib.sha1()",
        "description": "弱哈希算法 SHA1（不适合安全场景）",
        "severity": "low",
    },
    "insecure_random": {
        "pattern": "random.$FUNC()",
        "description": "不安全的随机数（应用 secrets 模块）",
        "severity": "low",
    },
    "assert_in_production": {
        "pattern": "assert $COND",
        "description": "生产代码中的 assert（优化模式下会被移除）",
        "severity": "low",
    },
    "broad_exception": {
        "pattern": "except:",
        "description": "裸 except（捕获所有异常）",
        "severity": "medium",
    },
    "try_except_pass": {
        "pattern": "except $EXC:\n    pass",
        "description": "静默异常捕获（隐藏错误）",
        "severity": "medium",
    },
    # v3.9.1 第4轮新增规则
    "insecure_marshal": {
        "pattern": "marshal.loads($DATA)",
        "description": "不安全的反序列化（marshal.loads 可执行任意代码）",
        "severity": "high",
    },
    "insecure_shelve": {
        "pattern": "shelve.open($PATH)",
        "description": "不安全的持久化（shelve 使用 pickle，可执行任意代码）",
        "severity": "medium",
    },
    "insecure_tempfile": {
        "pattern": "tempfile.mktemp()",
        "description": "不安全的临时文件创建（mktemp 存在竞态条件，应用 mkstemp）",
        "severity": "medium",
    },
    "insecure_ssl_verify": {
        "pattern": "ssl._create_unverified_context()",
        "description": "不安全的 SSL 上下文（禁用证书验证）",
        "severity": "high",
    },
    "insecure_requests_verify": {
        "pattern": "requests.$METHOD($URL, verify=False)",
        "description": "禁用 SSL 证书验证（中间人攻击风险）",
        "severity": "high",
    },
}


def scan_security(code: str) -> List[Dict[str, Any]]:
    """扫描代码中的安全问题（对标 Semgrep 安全规则）。

    Args:
        code: 要扫描的 Python 代码

    Returns:
        安全问题列表，每项包含 rule/description/severity/matches
    """
    matcher = CodePatternMatcher()
    results = []

    for rule_name, rule_info in SECURITY_PATTERNS.items():
        matches = matcher.search(code, rule_info["pattern"])
        # 检查语法错误
        if matcher.last_syntax_error is not None:
            results.append({
                "rule": "syntax_error",
                "description": f"代码语法错误: {matcher.last_syntax_error['message']}",
                "severity": "critical",
                "matches": [{
                    "lineno": matcher.last_syntax_error["lineno"],
                    "col_offset": matcher.last_syntax_error["offset"],
                    "matched_text": (matcher.last_syntax_error["text"] or "").strip(),
                    "bindings": {},
                }],
                "count": 1,
            })
            break  # 语法错误，无法继续扫描
        # 应用过滤器
        if "filter" in rule_info:
            matches = [m for m in matches if rule_info["filter"](m)]
        if matches:
            results.append({
                "rule": rule_name,
                "description": rule_info["description"],
                "severity": rule_info["severity"],
                "matches": [m.to_dict() for m in matches],
                "count": len(matches),
            })

    return results
