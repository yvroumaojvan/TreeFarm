# 🌳 TreeFarm 最佳实践

> 怎么用树场最省 token、最出效果。实战经验总结。

---

## 1. 与 AI 工具配合（省 token 核心用法）

### 1.1 正确姿势：AI 先读简报，别全读项目

```bash
python3 tree_farm.py <项目> --brief > brief.txt
```

然后把 `brief.txt` 给 AI。简报包含：大树清单（每个文件的耦合基因）、杂草索引
（不读全文，命中才展开）、垃圾箱状态、下一步指引。

**为什么省**：AI 读代码的 token 大头是「无关文件的全文」。简报把项目压成
「文件 + 耦合关系」摘要，AI 只在需要时用 `--genes <文件>` / `--search` 定向展开。

### 1.2 排查流程（多轮循环）

```
简报（入口）→ 挑重点文件 → 报小鸟（发现耦合）
  → 验证 → 下一轮（--next-round）→ 收敛（--converge）→ 清垃圾箱（--fix）
```

### 1.3 大项目分级策略

| 项目规模 | 建议 |
|---------|------|
| <10 个文件 | 别开树场，直接读 |
| 10~100 文件 | 简报 + 定向展开足够 |
| 100~1000 文件 | 全流程：简报 → 小树视角 → 影响分析 |
| 1000+ 文件 | 配合 `.treefarm.json` 的 `scan.ignore` 裁剪范围 |

### 1.4 配置忽略规则缩小扫描范围

```json
{"scan": {"ignore": ["vendor/", "*.min.js", "*.generated.*"],
          "ignore_dirs": ["node_modules", "dist", ".next", "build"]}}
```

大项目务必配 ignore——把「杂草」里的无关目录直接挡在扫描外，又快又省。

---

## 2. 代码质量工作流

### 2.1 定期健康检查（推荐每周或每轮迭代末）

```bash
python3 tree_farm.py <项目> --debt        # 总分 + 等级（A/B/C/D）
python3 tree_farm.py <项目> --smells      # 代码异味清单（v3.7）
```

债务等级参考：A（0-10）健康 / B（11-25）轻度 / C（26-45）中度 / D（46+）重债。
**按建议逐条修**：先死代码（--dead-code 确认后删），再拆高复杂度函数，再合并重复。

### 2.2 改代码前必做：影响分析

```bash
python3 tree_farm.py <项目> --impact <要改的文件>
```

改核心文件前跑一次，知道谁会受影响——跨语言也生效（v3.6 确认）。

### 2.3 循环依赖与架构

```bash
python3 tree_farm.py <项目> --circular-deps   # 破环
python3 tree_farm.py <项目> --architecture    # 分层合理性
python3 tree_farm.py <项目> --graph | 粘贴到 mermaid.live 渲染
```

### 2.4 代码异味阈值调优

`detect_code_smells()` 支持自定义阈值；项目规范不同可调：

```python
from treefarm.analysis import detect_code_smells
r = detect_code_smells(files, long_func_lines=80,   # 你们团队函数写多长算长
                       max_params=8, max_nesting=5,
                       big_class_lines=500)
```

---

## 3. CI 集成

GitHub Actions 里加一步健康检查 job：

```yaml
- name: TreeFarm health check
  run: |
    cd tree-farm/scripts
    python3 tree_farm.py ${{ github.workspace }} --debt
    python3 tree_farm.py ${{ github.workspace }} --smells
```

配合 `.treefarm.json` 的 `scan.ignore` 排除测试目录，CI 输出即报告。

---

## 4. 与其他工具对比（诚实边界）

| 场景 | 用 TreeFarm | 用专业工具（CodeQL/SonarQube） |
|------|------------|-------------------------------|
| 快速了解项目结构 | ✅ 秒级 | ❌ 太重 |
| 给 AI 喂上下文 | ✅ 省 token 设计 | ❌ 为人类设计 |
| 精确定位安全漏洞 | ❌ 轻量启发式 | ✅ 数据流/污点分析 |
| 大规模规则扫描 | ❌ | ✅ |
| 离线/零依赖环境 | ✅ 纯标准库 | ❌ 需要运行时 |

**一句话**：TreeFarm 是「轻量 + 快 + 零依赖 + 面向 AI」的分析器；
重型工具留给需要精确规则引擎的场景。

---

## 5. 性能优化经验

- **增量扫描**：树场默认增量（FileCache 指纹），改一个文件重扫 <0.5s
- **大项目**：`--quiet` 关进度条可再省一点；1000 文件首次约 2s、峰值内存 ~40MB
- **查重**：函数级查重按函数体大小分桶（size//64），1000 文件约 1.2s
- **REPL 模式**：启动扫一次，连续多条命令复用结果，最省

---

## 6. LLM 配合建议

- 省 token 技巧：先 `--brief`，再 `--genes` 定向看，别 `--analyze` 全量
- 报小鸟用 `--analyze <文件>` 让 LLM 自动找（需配 key）；无 key 时 dry-run 预览提示词
- 幻觉控制：小虫子（verify_candidate）静态验证真伪，假耦合自动吃掉 + 进垃圾箱
