#!/usr/bin/env bash
# ============================================================
# 树场 APK 分析模式：APK → jadx 反编译 → 树场引擎分析
# ============================================================
# 用法:
#   bash apk_analyze.sh 你的应用.apk                 # 反编译 + 树场体检
#   bash apk_analyze.sh 你的应用.apk --brief         # 反编译 + AI 工作简报
#   bash apk_analyze.sh 你的应用.apk --search 关键词  # 反编译 + 语义搜索
#   bash apk_analyze.sh 你的应用.apk --analyze 某文件 # 反编译 + LLM 找小鸟
#   bash apk_analyze.sh 你的应用.apk --llm status    # 查看 LLM 卡
# ============================================================

set -e
cd "$(dirname "$0")/.."          # 技能包根目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

APK="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
shift || true

if [ ! -f "$APK" ]; then
  echo "✖ 找不到 APK: $APK"
  echo "用法: bash apk_analyze.sh 你的应用.apk [树场参数...]"
  exit 1
fi

OUT="$(dirname "$APK")/$(basename "$APK" .apk)_反编译"
SRC="$OUT/sources"

echo "📦 APK: $(basename "$APK")"
echo "🔧 反编译输出: $OUT"

# ---------- 1. 反编译（只做一次，已有输出就跳过）----------
if [ ! -d "$SRC" ]; then
  echo "🔄 正在用 jadx 反编译（首次约 1-3 分钟，大 APK 更久）..."
  if command -v jadx >/dev/null 2>&1; then
    jadx -d "$OUT" --no-res --threads-count 4 "$APK" 2>&1 | tail -5
  else
    echo "✖ 未安装 jadx。Termux 安装: pkg install jadx"
    exit 1
  fi
else
  echo "✅ 已有反编译结果，跳过反编译（省时间）"
fi

if [ ! -d "$SRC" ]; then
  echo "✖ 反编译失败，没找到 $SRC"
  exit 1
fi

# ---------- 2. 树场分析 ----------
echo ""
echo "🌳 树场引擎启动..."
python3 "$SCRIPT_DIR/tree_farm.py" "$SRC" "$@"
