#!/usr/bin/env bash
# ============================================================
# TreeOfThought + TreeFarm 融合版 · 一键安装脚本
# 一次装两个技能：
#   tree-of-thought  → 思维树（多分支深度推理）
#   tree-farm        → 树场（大项目省 token 代码分析，思维树的子技能）
# 用法：
#   bash install.sh               # 装到用户级（~/.claude/skills 等）
#   bash install.sh /项目目录     # 顺便装到项目级（.cursor/skills 等）
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ---------- 两个技能目录 ----------
SKILLS=(
  "tree-of-thought:TreeOfThought 思维树"
  "tree-farm:TreeFarm 树场"
)

echo "📦 融合版技能来源：$SCRIPT_DIR"
echo ""

# ---------- 1. 用户级安装（所有项目通用） ----------
echo "── 安装到用户级 skills 目录 ──"
USER_TARGETS=(
  "$HOME/.claude/skills"   # Claude Code
  "$HOME/.qwen/skills"     # Qwen Code
  "$HOME/.gemini/skills"   # Gemini CLI
)
for dir in "${USER_TARGETS[@]}"; do
  if [ -d "$(dirname "$dir")" ]; then   # 只装给已存在的 agent
    mkdir -p "$dir"
    for entry in "${SKILLS[@]}"; do
      skill_dir="${entry%%:*}"
      skill_name="${entry##*:}"
      if [ -f "$SCRIPT_DIR/$skill_dir/SKILL.md" ]; then
        cp -r "$SCRIPT_DIR/$skill_dir" "$dir/"
        echo "  ✅ $dir/$skill_dir ($skill_name)"
      else
        echo "  ⚠ 找不到技能文件，跳过：$SCRIPT_DIR/$skill_dir/SKILL.md"
      fi
    done
  else
    echo "  ⏭ 跳过（未安装该 agent）：$dir"
  fi
done

# ---------- 2. 项目级安装（可选参数） ----------
PROJECT_DIR="${1:-}"
if [ -n "$PROJECT_DIR" ]; then
  echo ""
  echo "── 安装到项目级：$PROJECT_DIR ──"
  for sub in .claude/skills .cursor/skills .windsurf/skills .qwen/skills; do
    mkdir -p "$PROJECT_DIR/$sub"
    for entry in "${SKILLS[@]}"; do
      skill_dir="${entry%%:*}"
      cp -r "$SCRIPT_DIR/$skill_dir" "$PROJECT_DIR/$sub/"
      echo "  ✅ $PROJECT_DIR/$sub/$skill_dir"
    done
  done
fi

# ---------- 3. 说明 ----------
echo ""
echo "🎉 安装完成！重启对应 agent（新开会话）后生效。"
echo ""
echo "验证方法（两级触发链，全部默认关闭，绝不自动触发）："
echo "  1. 对 agent 说「开启思维树」→ 只开思维树（复杂问题用多分支推理）"
echo "  2. 再说「开启深度思考模式」→ 🌳 树场也启动（大项目代码分析省 token）"
echo "  3. 只说「开启思维树」→ 就只用思维树，树场保持关闭"
echo "  4. 说「关闭思维树」/「关闭深度思考模式」→ 逐级关闭"
echo ""
echo "注意：豆包/ChatGPT/Kimi 这类聊天 App 没有技能目录，"
echo "请用同目录下的 纯提示词版.md 粘贴到人设/对话里。"
