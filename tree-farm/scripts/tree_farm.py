# -*- coding: utf-8 -*-
# flake8: noqa —— 入口文件故意用星号导入聚合导出全部符号（F401/F403/F405 均为设计行为）
"""树场机制 —— v3.6（多模块版）入口

本文件是兼容入口：聚合导出 treefarm 包的全部公共符号 + 转发 main。
老用法完全不变：
  python3 tree_farm.py <项目> --brief [--compact]   # AI 工作简报
  python3 tree_farm.py <项目> --dead-code           # 死代码检测
  ...（全部命令见 treefarm/cli.py 的模块 docstring，或直接运行本文件无参数）

实现（v3.6 拆分）：
  单文件 3227 行 → treefarm/ 包（common/parser/storage/config/analysis/core/cli）。
  `import tree_farm as tf` 仍可拿到全部符号（tf.extract_genes、tf.TreeFarm 等）。
"""

# ========== 聚合导出（保持旧 API 面：import tree_farm as tf 全量可用） ==========
from treefarm.analysis import *                       # noqa: F401,F403
from treefarm.analysis import _mermaid_node           # noqa: F401
from treefarm.common import *                         # noqa: F401,F403
from treefarm.common import (_JAVA_DEF_PREFIX, _cache, _cosine, _gram_hashes,  # noqa: F401
                             _matches_ignore, _ngrams, _seg_match,
                             _seg_seq_match)
from treefarm.config import *                         # noqa: F401,F403
from treefarm.config import _parse_toml               # noqa: F401
from treefarm.core import TreeFarm                    # noqa: F401
from treefarm.parser import *                         # noqa: F401,F403
from treefarm.parser import _rust_defs, _strip_rust_noise  # noqa: F401
from treefarm.storage import *                        # noqa: F401,F403
from treefarm.cli import main, setup_logging, _dispatch  # noqa: F401

__all__ = [
    # 常量
    "VERSION", "SCHEMA_VERSION", "DB_FILE", "WEED_EXTS", "CODE_EXTS", "SKIP_DIRS",
    "SMALL_TREE_RATIO", "TRASH_WINDOW", "TRASH_FAIL_LIMIT", "CONVERGE_LIMIT",
    "WEAK_CONFIRM_LIMIT", "READ_HEAD_BYTES", "FINGERPRINT_BYTES", "GRAM_MAX",
    "SEMANTIC_SYMBOL_THRESHOLD", "SEMANTIC_CONTENT_THRESHOLD", "LLM_TIMEOUT",
    "LLM_CODE_CHARS", "BRIEF_MAX_SYMBOLS", "WEED_SUMMARY_LINES",
    "WEED_SUMMARY_LEN", "WEED_SHOW", "GENE_SCHEMA", "DB_SCHEMA", "IMPORT_PATTERNS",
    "PROVIDERS", "JS_KEYWORDS", "JAVA_KEYWORDS", "GO_KEYWORDS", "RUST_KEYWORDS",
    # 工具函数
    "stable_id", "normalize_gene", "classify", "scan", "rel_module",
    "module_to_path", "read_text", "extract_genes", "extract_call_graph",
    "extract_js_call_graph", "extract_java_call_graph", "extract_go_call_graph",
    "extract_rust_call_graph",
    "extract_symbols", "verify_candidate", "normalize_identifier",
    "identifier_tokens", "semantic_search", "file_fingerprint", "scan_changes",
    "load_config", "detect_dead_code", "detect_circular_dependencies",
    "impact_analysis", "calculate_complexity", "calculate_complexity_any",
    "detect_architecture_layers", "detect_code_smells",
    "generate_call_graph", "generate_call_graph_data",
    "collect_function_grams", "detect_duplicate_func_pairs",
    "detect_duplicates", "calculate_debt", "setup_logging", "main", "_dispatch",
    # 类
    "FileCache", "SymbolIndex", "GeneBank", "TrashBin", "Session", "WeedIndex",
    "SmallTree", "LLMClient", "TreeFarm", "_BatchContext",
]


if __name__ == "__main__":
    main()
