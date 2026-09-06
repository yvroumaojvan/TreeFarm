# Changelog

所有重要的变更都会记录在这个文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
并且本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 规范。

## [4.9.8] - 2026-09-07

### 🏗️ C/C++ 函数级分析（用户全权委托十轮攻坚，目标 S 级）

**背景**：v4.9.7 评级 A+ 时明确两大短板：C/C++ 只有 import 级正则（`#include`），
无法函数级分析。用户委托「跑 10 轮以上，目标 S 级 + 模型提升 200%+」。

**新增（parser.py + common.py + core.py + analysis.py）**：
- `_strip_c_noise()`：C/C++ 去噪（字符串/字符/注释/预处理；`#if 0` 禁用块整块剥除，
  残缺 `#if 0`（无 `#endif`）剥到文件尾防截断文件提取禁用代码）
- `_c_defs()`：函数/方法/类定义提取——基本形态/指针返回/类内联/构造(初始化列表)/
  析构 `~Foo`/命名空间嵌套/`override`/模板函数/模板类方法 `Stack<T>::push`/模板特化/
  类继承列表/`final`；排除：关键字、全大写宏名、函数指针变量、lambda、运算符重载、
  声明（`.h` 原型 / 纯虚 `= 0`）
- `extract_c_call_graph()`：函数级调用图——`obj.method` / `p->method` / `ns::func` /
  模板实例化 `st.push` / 嵌套点链；`::`→`.` 归一化；排除声明行/析构/初始化列表/
  宏调用/控制流关键字
- `_count_complexity()` 扩展 c/cpp（if/for/while/switch/do/catch/&&/||/三元/case）
- core.py `_build_genes` 接入 `.c/.h/.cpp` 跨文件 call/inherit 基因
- common.py 符号提取（定义 + 声明形态，跨文件验证用）+ `C_KEYWORDS`/`CPP_KEYWORDS`
- analysis.py 死代码检测 C/C++ 分支：`Worker w;` 栈实例化算类被使用、构造/析构豁免、
  static 函数高置信度死代码（文件私有）
- 影响分析/架构分层自动受益（基因层打通）
- **C/C++ 安全规则 `_scan_c_security`**：system()/popen() 命令注入（动态参必报、纯静态
  不报）、gets() 无边界（critical）、strcpy/strcat 动态第二参溢出、sprintf 格式符/动态、
  scanf/fscanf `%s`、硬编码密钥

**修复的 bug（10 轮实测驱动）**：
1. 调用检测把 `declaration_only(...);` 声明当调用 → 声明行偏移排除
2. 声明正则把 `add(1, helper(2));` 当声明 → 严格要求「返回类型 + NAME(...) ;」形态
3. `return used_func();` 被声明正则吞（return 当伪类型）→ 返回类型排除语句关键字
4. `count_(0)` 构造初始化列表被当调用 → `) :` 前缀上下文排除
5. `~Base()` 析构 `Base(` 被当调用 → `~` 前缀排除
6. 类继承列表 `class Worker : public Base {` 未提取 → 支持继承/final
7. `#if 0` 内函数被提取 → 禁用块整块剥除
8. `Stack<T>::push` 模板方法未提取 → 类名支持 `<模板参数>`
9. **声明正则灾难性回溯（ReDoS，第6轮实锤）**：`(?:\s*[*&]+\s*|\s+)+` 嵌套量词在
   长连续空白上指数爆炸（7KB 系统头文件卡死 20s+）→ 有界量词修复，275 个头文件全扫
   无卡死（仅 sqlite3.h 0.31s）

**量化验证（第 9 轮，模型提升口径）**：
- 裸读 vs 插件加持：cproj 0.4→2 信号、psutil 7.6→38 信号，**相对提升 400%**（远超
  200% 目标）——C/C++ 第一次有函数级调用图/死代码/复杂度信号
- 误报治理：150 个系统头文件 **0 误报**；psutil 真实 C 库 6 文件仅 2 条保守
  strcpy 提示（`strcpy(dst, CONST_MACRO)` 惯用法）
- tornado 6.1 金标准回归：security 0/100 A 保持（Python 检测未受影响）

**测试**：487 → **499 全绿**（+12：C 定义/调用/死代码/static 高置信/模板/命名空间/
跨文件基因入库/边界轰炸/性能 ReDoS/残缺 if0/C 安全规则 12 项）

## [4.9.7] - 2026-09-06

### 🎯 OWASP 对抗靶场三修（TraeAI 金标准复测驱动）

**触发**：TraeAI 以官方 Release（sha256 实算一致）+ BugsInPy 8 bug + tornado 6.1 官方源码 +
自建 OWASP 11 样例复测 v4.9.6，报告已本地逐项实锤复核（无编造），暴露 3 个真问题。

#### 1. 参数化 SQL 误报（最重要，修复误报）

**现象**：`db.execute("SELECT * FROM products WHERE name LIKE ?", ("%" + kw + "%",))` 被报
「SQL注入：字符串拼接查询」风险 15/B。此写法是标准参数化查询（SQL 模板静态、`+` 拼接发生在
绑定参数值内），完全安全——grader 报得准的硬伤。

**根因**：基础层正则 `execute("...".*?["']\s*\+` 跨到第二参数 `"%` 的闭引号命中；增强层正则
`(execute|query)\s*\([^)]*\+[^)]*\)` 见 `+` 就报，两条路径都只认形态不认语义。

**修复**：新增 `_param_bind_exempt()`——逐字符扫描 `execute(` 后参数串，找第一个顶层逗号，
若第一参数是完整纯字符串字面量（无 +/%/.format/{...}）→ 参数化绑定豁免。基础层 + 增强层
同步生效；真危险形态（第一参拼接/f-string/跨行污点流入）保留必报。

#### 2. os.system 拼接漏报（修复漏报）

**现象**：`os.system("ping " + cmd)`（真实命令注入）报 0/A 漏出。

**根因**：os.system/os.popen 规则要求 `\(` 后首字符非引号（`[^"']`），只覆盖变量直传形态
`os.system(cmd)`，字符串开头的拼接形态被挡掉。

**修复**：cmd_patterns 补 `os.system/os.popen/subprocess 引号开头拼接` 形态
（`["'][^"']*["']\s*\+`），单文件层 + 跨文件层 `_CROSS_SINK_PATTERNS` 同步；纯静态字符串
`os.system("ping 127.0.0.1")` 仍不报。

#### 3. meta refresh 开放重定向漏报（修复漏报）

**现象**：`"<meta http-equiv=\"refresh\" content=\"0;url=" + target + "…>"` 报 0/A 漏出。

**根因**：open_redirect_patterns 只覆盖 `redirect(...)`/Location 响应头，无 meta refresh 形态；
且 HTML 属性转义引号 `\"` 让 `["']` 匹配不上。

**修复**：补 meta refresh 两条（`url=` 拼接 / `url=` 模板变量），正则用 `\\*["']` 容忍转义引号；
跨文件层同步。静态 `<meta http-equiv="refresh" content="0;url=/home">`（无拼接）不报。

**测试**：421 → **437 全绿**（+16 项：sample9 四个变体豁免、真拼接仍报三类、os.system/os.popen/
subprocess 拼接捕获 + 静态不报、meta refresh 拼接捕获 + 静态不报 + f-string、跨文件注入回归）
。tornado 6.1 干净源码回归：security 0/100 A 保持（11 条全在测试代码），grade 41.7 不变。

### 🌳 金标准说明（为什么 8 个 bug 只命中 3 个）

3 个命中全是**结构型**缺陷（API 契约委托不一致 / dict del KeyError / 协程未 await——静态分析
强项）；5 个漏报全是**语义型**缺陷（307 重定向 chunked body 丢失 / Range RFC7233 / 时钟回拨 /
事件循环泄漏 / WS key 缺失崩溃——需协议语义与运行时环境推理，行业静态工具普遍天花板）。
结构型命中率 3/3 = 100%；语义型靠 LLM 思维树模式（扣子全开 8/8）补齐。

## [4.9.6] - 2026-09-06

### 🐛 修复：--sandbox run/test/scan 缺参数误报「未知沙箱命令」

**触发**：扣子AI 基准测试报告（BugsInPy tornado 8 真实缺陷，全开 8/8 命中根因）指出
`--sandbox run/test/scan` 帮助声明支持但实际报「❌ 未知沙箱命令」，且错误提示同时
列出 支持: on/off/status/lang/run/trace/cov/test/scan/quality/profile——帮助与实现打架。

**根因**：cli.py 沙箱分发各分支要求 `len(rest) >= 2`（必须带后续参数），缺参数时直接
掉进「未知沙箱命令」兜底分支。功能本体完好（带参数可正常执行），但报错信息误导用户
以为功能不存在。

**修复**：已知命令（run/trace/cov/test/scan/quality/profile）缺参数时明确提示
「❌ 缺少参数」+ 具体用法；只有命令完全不在列表里才报「未知沙箱命令」。

**测试**：414 → **421 全绿**（+7 项：run/test/scan/trace 缺参友好提示、未知命令仍报
未知、run 带参执行、scan 带文件执行）

### 📚 文档同步 v4.8.2 → v4.9.6

README.md 与 SKILL.md 版本信息同步：版本号（v4.8.2 → v4.9.6）、测试数（313 → 421）、
补 v4.9 功能说明（污点分级 param/concat/user、跨文件污点检测 a.py→b.py 两层透传+来源追溯、
tornado 金标准 0/100 A 误报治理、BugsInPy 基准 8/8 战绩）。

## [4.9.5] - 2026-09-06

### ✨ 新能力：跨文件污点检测（a.py 用户输入 → b.py 危险函数漏报修复）

**触发**：豆包复测报告（正文不可信，但样例 2 线索真实）声称「a.py 收 request 输入 → b.py
execute SQL」漏报。本地构造最小样例**实锤 v4.9.4 确实 0 检出**——跨文件污点是静态分析普遍
难点（Semgrep/CodeQL 跨文件都需专门配置），TreeFarm 以轻量方案补上。

**实现（analysis.py，两阶段 + 零依赖）**：

- **第一阶段**：各文件收集 import/from 模块映射 + 函数参数表 + 「强污点实参传入模块函数」调用记录
- **第二阶段**：按模块名定位被调文件，把强污点实参映射到被调函数参数，参数**直接流入危险 sink**
  （SQL/命令/路径/SSRF/redirect）才报，带「跨文件数据流 + 需人工确认」标签 + **来源追溯**
  （`来源: a.py:6 的 user_input`）
- **两层透传链**（a→b→c）：b 把注入参数原样传给 c 模块函数则继续注入，来源一路拼接追溯
- **目标文件内轻量传播**：`q = "SELECT…" + sql` 拼接传播 → `execute(q)` 命中（单字母变量
  用严格词边界 `(?<![\w.])q(?!\w)` 防 query 子串误报）
- **三种导入形态**：`import b` / `import b as bb` / `import pkg.mod`（包路径）/ `from b import f`
- **豁免组合**：参数化 `execute(sql, params)`（污点在绑定参位）、列表 `Popen([cmd])`、ORM 查询
  （session.query/.objects. 白名单）、标准库模块（os/sqlite3 等映射不到文件天然跳过）
- **联动去重**：单文件层「param 拼接升级 concat」与跨文件层命中同一 sink 行时，只留证据链
  更完整的跨文件条目；多注入源同一 sink 行只报一次
- **性能**：正则预编译 + 无点/无括号/长别名子串三重短路，tornado 89 文件全扫
  **7.6s vs 基线 7.1s（+0.5s ≈ 8%）**，风险分 0/100 A 保持
- **健壮性**：循环 import 防死循环（handled 去重）、空文件/非 py/超多 import/中文变量名不崩不误报

**测试**：386 → **414 全绿**（+28 跨文件用例：单层/两层/拼接/去重/豁免/形态/健壮性全覆盖）

**金标准回归（tornado 6.1，89 文件）**：security 0/100 A（11 条全测试代码）| logic 92/100
（竞态 0、除零 1 真除法、API 契约 websocket:587 真 bug 保留）| grade 41.7（安全 100）不变。

## [4.9.4] - 2026-09-05

### 🐛 修复：tornado 金标准误报治理（10 大类误报全消，评分 9.7 → 41.7）

**触发**：别的 AI 拿 v4.9.3 跑 BugsInPy tornado（116 文件，验证 SQL 治理效果）——暴露大规模
误报把顶级开源项目打成 100/100 F 分、六维健康度安全/逻辑/性能/质量全 0.0，grader 决策支撑失效。

**根因**：污点分析「函数参数全标污点」+ 子串/Popen 的 "open(" 子串/参数名雷同（url/next/name）
等触发 + `%s` 格式化被当取模 + RLock 被当并发源 + 展示层测试代码混入核心段，多重叠加。

**修复（analysis.py + core.py）**：

- **污点来源分级**（总根源，set → dict）：`param`（函数参数，弱）< `concat`（拼接传播）< `user`
  （request/input/argv 直接源）。危险 sink 只对强污点必报，纯 param 豁免——
  `template.execute(add=add)`（文档示例）、`execute(schema)`（读静态 schema.sql）不再误报；
  **param 参与跨行拼接（`sql = "SELECT…" + table`）保留必报**（v4.5 污点分析核心能力不丢）
- **命令注入**：列表形态 `Popen([sys.executable] + argv)` 豁免（非 shell 拼接）
- **路径遍历**：`\bopen` 词边界 + 排除 `def open()` 定义与 `.open(` 方法调用——
  `Popen(...)` 的 "open(" 子串、`def open(self, *args)` 不再误伤
- **CRLF 注入**：只认真用户输入源拼接头值；`set_cookie` 参数名 name、
  `self.request.headers[...]` 自写（Basic 认证 b64 编码不含 \r\n）不再误报
- **临时文件竞争**：删除 `/tmp/xxx` 字符串宽泛规则
  （`define("root_directory", default="/tmp/s3")` 配置默认值不再误报），只认 `open(.../tmp/...)` 形态
- **开放重定向**：删 `url\b`/`next\b` 参数名触发（`self.redirect(url)` 误报），改污点层强污点接管
  （`target = request.args.get("next"); redirect(target)` 仍报）
- **除零风险**：`"%s" % var` 字符串格式化 ≠ 取模除法（web.py `"%r" % value` 误报根源），真除法保留
- **竞态条件**：threading.RLock/Lock 同步原语 ≠ 并发源（template.py 解析器不再误报）；
  异步文件 + ThreadPoolExecutor = offload 阻塞任务标准模式（tornado ioloop.py 不再误报）；
  `threading.Thread(` 真并发仍报（v4.7 降误报测试全含）
- **展示层**：测试代码的问题打 `scope=test`，从「严重/高危」段移出，独立
  「🧪 测试代码里的问题（不参与评分）」分组（security/logic/performance 三处展示全部生效）
- **硬编码凭据**：`__TODO:_GENERATE_YOUR_OWN_...` 占位符豁免（框架 demo 提示用户替换，非真实密钥）

### 📊 tornado 6.1 靶场实测（前后对比）

| 指标 | v4.9.3 | v4.9.4 |
|---|---|---|
| --security 风险分 | 100/100 F | **0/100 A** |
| 严重问题（核心代码） | 3（SQL×2 + 命令注入） | **0** |
| 高危路径遍历误报 | 2（Popen / def open） | **0** |
| --logic 竞态误报 | 5（ioloop×2 + template×3） | **0** |
| API 契约真 bug（tornado Bug#1 同类） | ✅ 检出 | ✅ 保留检出 |
| --grade 综合评分 | 9.7/100 F | **41.7/100** |
| 安全健康度 | 0.0 | **100.0** |
| 扫描耗时（89 文件 --security） | 16.2s | **9.3s** |

### 🧪 测试

- 新增 `tests/test_v494_fp_cleanup.py`（20 用例：SQL param 豁免/拼接保留、Popen 列表豁免、
  def open 豁免、/tmp 配置豁免、url 参数名豁免、% 格式化豁免、RLock/异步线程池不报竞态、
  scope=test 标识等），全部 tornado 靶场实测驱动
- **386 个 unittest 全绿**（366 → 386，零依赖）

## [4.9.3] - 2026-09-05

### 🐛 修复：SQL 注入误报（参数化查询 / 纯静态 SQL 被污点分析误判）

**触发**：别的 AI 测试报告提出「tornado demo 的参数化查询可能是误报」→ 本地建 sql对照靶场实测实锤：
`execute("...%s", uid)` / `execute(stmt, keyword)` 等**安全参数化写法**被污点分析报成 SQL 注入。

**根因**（污点层三重叠加）：函数参数全标污点 → 子串匹配（表名/列名撞上参数名）→ 「参数里无 `?` 即报」。

**修复**（analysis.py 污点 sink 判定，三种安全形态豁免）：
- **A. 单行拼接形态**（`f"…"` / `"…"+` / `"…" %` / `.format`）：基础正则规则已覆盖 → 污点层跳过，**不再重复刷屏**（f-string 原本 3 层各报 1 次 → 只报 1 次）
- **B. 参数化绑定** `execute(sql, params)`：逗号分隔的第二参数是绑定参数 → 豁免
- **C. 纯静态 SQL** `execute("SELECT …")`：字符串字面量内无变量 → 豁免
- **D. 跨行拼接** `query = "…" + 污点; execute(query)`：**保留必报**（v4.5 污点分析核心能力不丢）

### 🧪 测试

- 新增 `tests/test_v493_sql_fp.py`（7 用例：4 安全写法零误报 + 3 危险写法必报/去重/跨行保留）
- **366 个 unittest 全绿**（359 → 366）

## [4.8.2] - 2026-09-05

### ✨ 关键设计修正：--deep-full 默认输出「AI 思维链任务卡」（零配置，符合插件定位）

按用户设计理念修正：**插件是给 AI agent 用的 skill——思维链应由宿主 agent 用自己的思考能力完成，插件不该另开 LLM 连接、不该要求配 key**。

- **无 LLM key（默认）**：`--deep-full` 输出「AI 思维链任务卡」——每棵大树的**基因记忆 + 静态线索 + 4+1 分支任务清单**（数据/逻辑、安全、性能/资源、契约/接口 + 独立弱信号分支），宿主 agent 逐分支深挖即可。**零配置、零额外 token、任何 agent 拿来即用**
- **配了 OpenAI 兼容 key（可选增强）**：插件自动跑 AI 思维链（多轮深挖 + 正反馈闭环，v4.8.1 能力）
- **LLMClient 兼容修复**：不支持 `response_format` 参数的托管端点（如商汤 token.sensenova.cn）会卡到超时 → 失败自动去掉该参数重试；`chat()` 支持自定义超时（思维链 120s）
- **实测**：商汤托管 `deepseek-v4-flash` 下 AI 思维链 4+1 分支命中 tornado#1 双 bug（API 契约 + 抽象方法缺失）+ 2 个深层关联发现 + 自动触发弱耦合验证闭环

### 🧪 测试

- 任务卡模式断言更新，**316 个 unittest 全绿**

## [4.8.1] - 2026-09-05

### ✨ 新功能：全面深度体检（--deep-full / 全面深度体检 / 恐怖体检）——真·思维链版

与省 token 版（--deep，静态规则引擎）**根底检测模式不同**：本版是 AI 推理引擎。

- **每棵大树派 1 条完整思维链**：至少 4 条普通分支（数据/逻辑、安全、性能/资源、契约/接口）各自独立推理一遍 + **1 条独立弱信号分支**（绝不并入普通分支），只多不少
- **分支携带基因记忆**：库中已确认的耦合关系 + 历史教训，以库的视角判断，**防止重复犯错**
- **多轮深挖**：默认 2 轮（`--deep-full <轮数>` 可调，1~5），每轮携带上一轮发现继续挖，无新发现即收敛
- **正反馈闭环**：思维链发现涉及外部模块/符号的耦合 → 走小虫子验证（真则入库、假则吃掉、弱耦合双分支确认）→ 下一轮分支带新基因继续挖（检查点同步）
- 无 LLM key 时降级静态分桶展示（4 普通分支 + 1 弱信号分支）

### 🔧 机制全部接通（对照树场设计文档盘查，5 处偏差修复）

- **小虫子质检 `_alive_genes`**：分支带基因前先啃死基因——项目内目标文件已消失 → 吃掉（腐肉）；源文件 mtime 变更 → 基因降级弱信号待重验；垃圾箱内目标 → 跳过。**模块名（abc/flask 等）不误杀**
- **垃圾箱熔断接入**：垃圾箱里的树跳过普通思维链分支（AI 审核能力差），汇总提示「不清空 = 任务未完成，收尾 --fix 专项修复」
- **多轮检查点同步**：每轮重新拉活基因，上一轮验证入库的新基因立即生效
- **发现耦合入库正反馈**：LLM 发现带 related 的可疑耦合 → bird() 验证闭环
- 修复：基因 target 为模块名时被误当死基因吃掉的 bug

### 🧪 测试

- 新增 3 项（deep_scan_full 无 key 降级可跑 / _alive_genes 模块名不误杀 / 死基因吃掉），**316 个 unittest 全绿**

## [4.8.0] - 2026-09-05

### ✨ 新功能：全自动深度体检（--deep / 深度体检 / 大树体检 / 全自动流水线）

按「树场机制」原生设计实现的**一键全自动流水线**——参天大树 → 思维链分支 → 跨树串联：

- **参天大树识别**：基因库统计每棵树的「被引用度 + 基因数」，自动识别核心大树（被引用多 = 枢纽/根节点）；资源/非核心文件自动归为杂草（需要时才读，省 token）
- **每棵大树长出一条思维链分支**：该树的基因上下文（引用谁/被谁引用）+ 静态检测聚焦该树的问题（安全/性能/逻辑，按严重度排序）——边检测边思考，符合大树与思维链强关联设计
- **跨树串联**：核心树之间的引用搭桥（🔗 改前者需排查后者）+ 跨树共性问题（🐦 同一问题类型在多棵树出现 → 系统级缺陷嫌疑）自动报小鸟
- **决策支撑**：汇总问题统计 + 「优先修哪棵提分最快」建议；对无静态命中的树提示 --analyze 让 LLM 深挖

### 🔧 逻辑检测新增 2 条 AST 规则（对 tornado#3/#7 型真实 bug 验证命中）

- **get 后 del 同一字典 key**（tornado#3 复现）：`cache.get(k)` 后 `del cache[k]`，key 缺失时 get 返回 None 但 del 抛 KeyError → 建议 `pop(key, None)`；支持 `self.cache` 属性写法，同 key 精确匹配防误报
- **executor.submit() 直返**（tornado#7 复现）：`return executor.submit(...)` 返回 concurrent.futures.Future，异步 `await` 报 TypeError → 建议 `asyncio.wrap_future` / tornado Future 包装；已包装的不误报

### 🔧 其他

- **bug 症状词表补「异步/协程问题」**：await/async/异步/协程/Future/不兼容/asyncio/事件循环 → 症状画像正确识别异步类 bug 症状
- **评测基准**：BugsInPy tornado Bug#1~7 七个真实难 bug 功能全开实测——静态规则直接命中从 **1/7（14%）提升到 4/7（57%）**，配合思维树 7/7（100%）

### 🧪 测试

- 新增 v4.8 测试套件（test_v48_deepscan.py 8 项：deep_scan 大树识别/思维链/串联/汇总 + 新规则命中/防误报），**313 个 unittest 全绿**

## [4.7.1] - 2026-09-05

### ✨ 新功能：报 bug 症状画像（--bug）

- **`--bug "症状描述"`**：让被检者直接描述遇到的 bug（如"登录没反应、金额算错、很卡"），树场按症状关键词判定症状类别（崩溃/闪退、数据/逻辑错误、登录/认证、性能卡顿、网络问题、安全漏洞、显示/UI、文件、功能无响应、并发多线程），联动推断「重点排查」的检测类型
- 与 --spec/--spec-read 一样注入上下文：之后跑 --security/--performance/--logic 时，命中重点排查类型的问题标 🎯，先修用户报的症状
- 中文命令别名：`报bug` / `bug描述` / `症状` / `问题描述`；REPL 里 `bug <症状>` 同样可用

### 🔧 修复与优化

- **grade()/grade_diff() 质量维度算法统一**：grade() 原来按「异味总数×4」、grade_diff() 按「千行密度加权」算 quality，同一项目两次评分质量分会不一致 → 统一为千行密度加权（大项目异味多但密度低不再被误伤）
- **单维报告风险分命名修正**：安全/性能/逻辑报告从「XX评分」改为「风险分（越高越严重）」——原「性能评分: 100/100 等级 D」容易误读成满分，实为差评，现明确标注方向
- **README 配置文件说明补全**：`.treefarm.toml` / `.treefarm.json` 除 `[scan]` 段（v3.2）外，补顶层 `api_key` / `base_url` / `model` LLM 配置说明（v4.7 已支持但文档漏了）

### 🧪 测试

- 新增 5 个 bug 症状画像测试（症状判定/重点排查联动/空输入/格式化/无症状识别），**305 个 unittest 全绿**

## [4.7.0] - 2026-09-04

### ✨ 重大升级：Grader 化（把 TreeFarm 从「扫 bug 工具」培养成「能评估程序好坏、支撑后续决策」的评审系统）

#### 新功能：项目功能画像（上下文感知检测）
- **`--spec "功能描述"`**：让被检者输入被检测项目的所有功能（如"这是一个 Flask 博客系统，用户可注册登录、发文章、评论"），树场自动解析出技术栈（web/数据库/认证/文件/异步…）与重点检测类型
- **`--spec-read`**：AI 自己读项目——扫描 README/docs/入口 docstring 自动推断功能画像，零依赖启发式，无需 LLM key 也能用
- **上下文感知报告**：带画像跑 `--security`/`--performance`/`--logic`/`--all-checks` 时，报告开头显示「项目功能画像」，与核心功能相关的问题标记 🎯，抓 bug / 修 bug 更精准

#### 新功能：Grader 综合评分与趋势
- **`--grade`**：六维健康度（安全/逻辑/性能/结构/质量/技术债务）加权出综合分（0~100）+ 等级（A+~F）+ 短板优先的改进方向建议
- **`--grade-diff`**：与上次评分对比，输出各维度 📈/📉 变化，让"进步多少"可量化可追踪（评分存基因库 meta）
- 支持 `--grade "功能描述"` 一步到位：画像 + 评分

#### 逻辑检测增强（对应金标准报告 10.3 高优 1/2/4/5 项）
- **新增 API 契约检测**：同一家族公开方法委托对象不一致（如 tornado#1：4 个方法用 `self.ws_connection`，唯独 1 个用 `self.stream`）→ 报「API契约」并给修复建议（🔧 修复字段，grader 决策支撑）
- **新增抽象方法完整性检测**：继承 ABC 的具体子类未实现父类抽象方法 → 报「抽象方法未实现」，附修复建议
- **异步竞态降误报**：用 AST 判断真实线程创建（`threading.Thread`/`ThreadPoolExecutor`），纯 asyncio 文件不再误报竞态；字符串/规则库里的 "threading.Thread" 字样不再误当线程（自检误报根源修复）
- **测试目录降级**：`test/tests` 目录里检出到的问题标记 `scope=test`、单独计数（🧪），不参与核心风险评分——测试用例本身常故意构造脏数据/越权场景

#### 安全检测误报优化
- **SQL 注入 sink 排除执行器**：`executor.execute()`/`runner`/`run_code` 等代码执行器不再误报 SQL 注入
- **测试目录降级同安全一致**：不拉低核心 risk_score

#### 性能优化
- **AST 解析缓存**：同一文件多次检测（`--all-checks`/`--grade`）只 parse 一次（按 mtime+size 失效），大项目扫描提速

#### 工程/文档
- 新增 `docs/treefarm-ci.yml.example`：官方 GitHub Actions 集成模板（push/PR 自动跑 --grade）
- 全部模块 docstring 版本标注统一到 v4.7；README/CHANGELOG 同步
- **测试 279 → 300 全绿**：新增 `test_v47_grader.py`（功能画像/AI自读/综合评分/趋势/契约检测/异步竞态/测试目录降级 21 项）

## [4.6.0] - 2026-09-03

### 安全检测规则补齐（对应插件优化清单 P2）
- **扫描范围扩展**：从 `.py` 扩展到 `.js/.jsx/.ts/.tsx/.html/.htm/.vue`（前端代码也能检出漏洞）
- **新增 XXE 检测**：`etree/ET/ElementTree/xml.etree.ElementTree/lxml/minidom/sax/expat` 无防护解析 XML
- **新增开放重定向检测**：`redirect(...)` 与 `Location` 响应头目标来自 `request`/`args`/`form`/`params`/`next` 等用户输入
- **新增认证绕过检测**：含敏感操作/后台/支付路径的路由，全文件无认证装饰器（`login_required`/`auth`/`jwt_required` 等）→ 未授权访问风险（文件级判定，只报一次）
- **XSS 规则扩充**：`innerHTML`/`outerHTML`/`document.write`/`insertAdjacentHTML`/Vue `v-html`/React `dangerouslySetInnerHTML`/Jinja2 `|safe`

### 性能检测补齐（资源泄漏）
- **新增资源泄漏检测**：函数内 `open()`/`socket.socket()` 绑定变量，既不在 `with` 中、函数内也无 `.close()` → 文件句柄/连接泄漏（AST 精确，`with`/`try-finally` 模式零误报）

### 体验优化（P1）
- **路径不存在友好提示**：CLI 输入错误路径输出 ❌ + 💡 排查建议 + 帮助指引，不再静默退出

### 测试
- **257 → 279 测试全绿**：新增 `test_v46_security.py` 22 项（XXE 3 种/开放重定向 3 种/认证绕过正反例/前端 XSS 3 语言/资源泄漏 6 种正反例/干净文件零误报/CLI 路径提示/restricted 多语言回归）

## [4.5.0] - 2026-09-02

### 性能/逻辑检测：正则堆砌 → AST 精确重写（本次核心）
- `--performance` / `--logic` 从「同一文件遍历 8 遍 + 正则硬猜」重写为 **AST 单遍精确检测**
  （`_PerfVisitor` + `_LogicVisitor`），规则基于真实语法树节点，不再靠正则猜
- **性能检测规则**：循环内字符串拼接（`+=` / `s=s+'x'` / `str()` 调用）、循环内线性查找
  （`in list` / `.index` / `.count`）、循环内 `re.compile`、N+1 查询
  （`.execute` / `.query` / `.filter` 等）、递归无终止、递归无缓存
- **逻辑检测规则**：可变默认参数、除零风险、边界条件（`for i in range(len(x))` 内 `x[i±1]`）、
  竞态（仅真实多线程 + `self.x+=1` 或 dict 计数器无锁）、TOCTOU（含 `if os.path.exists` 分支）、
  `is` 比较字面量、字符串大小比较
- **误报大幅下降**：删除静态证明不了只会刷误报的规则（内存泄漏 / json 类型混淆 / `if x=y`）；
  `x += 1` 整数累加不再误报字符串拼接、`range(len(x)-1)` 正确代码不再误报 off-by-one、
  `json.loads().get()` 正常用法不再误报类型混淆、`count=5` 不再误报竞态
- **实测**：14 个深 bug 靶子 + 8 个正常对照组 → 13 类深 bug 全抓齐 + 对照组 0 误报

### 安全检测：污点变量收集 + 弱哈希收紧
- 新增轻量级跨行数据流跟踪：识别「用户输入源 → 变量赋值」传播链
  （`request.args/form/values/json/data.get`、`input()`、`sys.argv`、`params.get` 等 → 变量 → 拼接/赋值传播），
  解决单行正则检测不到跨行数据流的问题
- 弱哈希检测收紧：只在密码/口令/凭据语境下报，指纹/校验和场景（如 `sha1(raw).hexdigest()[:12]`）不再误报

### 沙箱修复（v4.5）
- **subprocess 路径补代码级防护**：此前只做资源限制，`os.system` / `open('/etc/shadow')` 可直接执行；
  现在补调用级黑名单检查（危险 os 调用 / 危险 import / 网络模块）
- **覆盖率精确统计**：改用 AST 解析可执行行号，不再把空行/注释/包装代码算进「总行」
  （实测覆盖率从失真 75%→6.25% 的问题修正）
- `mod` 别名修正：原为 `divmod`（返回元组），应为取模 `a % b`
- 进程退出瞬间 `/proc/<pid>` 消失抛 `ProcessLookupError/OSError`：补捕获，不再刷异常堆栈
- subprocess 覆盖率统一为与 restricted 一致的 `{files, overall_coverage}` 格式

### 工程
- 新增测试 `scripts/tests/test_perf_logic.py`（16 用例：14 深 bug 检出 + 2 对照组零误报）
- **单文件检测修复**：`scan()` 对单文件路径返回空（`os.walk` 只遍历目录），导致直接传
  `tree_farm.py xxx.py --performance` 时「扫描 0 个文件」；现补单文件分支，单文件模式恢复（新增 2 个回归测试）
- 全量 257 测试通过

## [4.4.1] - 2026-08-31

### 思维树 v3.1：自动检测（默认开启，复杂任务主动询问）
- 新增 0.6 节自动检测：AI 当门卫，任务复杂就提醒一句，用户点头才开启
- 默认开启；「关闭自动检测」可关；红线：绝不擅自开启、简单任务不提醒、同一任务只问一次、情绪倾诉不打断
- 评估标准表：复杂决策/深度分析/方案设计/多角度对比/严谨推理/修bug写代码 → 问；闲聊/查询/单步 → 不问
- 树场 SKILL.md 加衔接：深度代码分析任务可建议开树场（同样只建议不擅自开）

### 树场 v4.4.x：沙箱动态测试 + static-ast + 中文命令
- 修复 --sandbox 无项目路径入口（与 SKILL.md 文档一致，agent 可直接调用）
- 修复 static-ast 孤儿模块：core.static_ast() + --static-ast 命令分支
- 新增中文菜单：`python3 tree_farm.py 菜单`（小白速查）；全部主命令补中文别名（简报/体检/搜索/全量检测等）
- 沙箱全套：受限执行器 / subprocess 沙箱（资源限制+超时+内存监控）/ 调用追踪 / 覆盖率 / 自动输入生成 / 安全模式匹配
- 全量检测一键命令：安全 + 性能 + 逻辑 + 死代码 + 循环依赖 + 复杂度 + 架构 + 技术债务
- 清理旧备份文件（tree_farm_v35_backup.py 等）；测试版本断言同步 4.4.1
- 测试：239 个 Python + 14 个 Node 全绿

## [4.0.0] - 2026-08-中旬

### 沙箱动态测试体系（v3.8 起迭代）
- `treefarm/sandbox/` 新增：restricted 受限执行器、subprocess 多语言沙箱、调用追踪、覆盖率采集、
  代码质量分析、模式匹配安全扫描、资源限制、污点追踪（v3.9）
- `--sandbox` 命令：on/off/status/run/trace/cov/test/scan/quality/profile
- 安全等级：auto/restricted/subprocess/container；filesystem ro/rw/tmpfs；网络默认禁止
- 静态 AST 辅助分析（--static-ast）：动态执行、沙箱绕过、lambda 逃逸、类型混淆检测

## [3.7.0] - 2026-08-11

### 分析深度：Rust 函数级调用分析（第 5 大语言）
- 零依赖手写解析器 `extract_rust_call_graph`：函数/方法定义（`fn`，支持泛型 `<T>`、
  生命周期 `<'a>`、`unsafe fn`、`async fn`、`where` 子句）、调用识别（含 `self.method(` /
  `self.storage.write(` 叶子名记录，防方法误判死代码）、宏调用（`name!`）排除
- **trait 继承 → inherit 基因**：`trait Foo: Bar + Baz`；标准库 trait
  （Send/Sync/Clone/Debug 等）自动过滤（项目内无定义，纯噪音）
- 噪音剔除：注释 / 字符串 / raw string（`r#"..."#`）/ 字符 / **生命周期**（`'a` 不成对，
  专杀正则防 char 模式吞代码）/ 属性（`#[...]`）；顺序保证 `"http://x"` 里的 `//` 不被当注释
- 符号表接入：Rust 函数/方法/struct/enum/trait 纳入项目符号表（跨文件验证生效）
- 死代码/复杂度/查重扩展：`_rust_defs` 供四类分析共用；复杂度支持 `loop/match/?` 计数；
  类型使用识别（结构体字面量 / 类型注解 / `impl Trait for X` / 泛型容器）降低死类误报
- import 级基因：`use` 语句提取（清洗 `as` 重命名、`{a, b}` 花括号导入）
- 测试：新增 9 个 Rust 用例（解析器 5 + 端到端基因 1 + 死代码 1 + 复杂度 1 + self 调用 1）

### 新功能：代码异味检测（--smells）
- 6 类异味：长函数（>50 行）/ 长参数列表（>5）/ 嵌套过深（>4 层）/ 重复条件分支 /
  魔法数字 / 大类（>300 行或 >20 方法），按严重程度（高/中/低）排序 + 修复建议
- Python 走 AST 精确分析（含 `range(x)` 参数与测试文件自动豁免魔法数字）；
  JS/TS/Java/Go/Rust 走轻量启发式（复用定义提取 + 函数体括号配对 + 缩进估算）
- 中文命令：`分析 异味` / `异味` / `坏味道`

### 新功能：交互式 REPL 模式（--repl）
- `python3 tree_farm.py <项目> --repl`：交互式执行全部分析命令
- Tab 补全（命令名 + 项目文件路径）、命令历史（`~/.treefarm_history`）、
  彩色输出（自动检测终端）、启动扫一次建库后续复用（项目缓存）
- 支持命令：brief/search/dead-code/circular-deps/impact/complexity/architecture/
  graph/duplicates/duplicates-func/debt/smells/bird/trash/help/exit

### 工程化：core.py 二次拆分（931 行 → 734 行）
- 调用图（`generate_call_graph` / `generate_call_graph_data`）、文件级查重
  （`detect_duplicates`）、函数级查重（`detect_duplicate_func_pairs` / `collect_function_grams` /
  `_bucket_by_size`）、技术债务（`calculate_debt`）全部迁入 analysis.py
- core.py 保留薄包装方法，**零行为改变**（`--debt`/`--graph`/`--duplicates*` 输出格式逐字一致）
- `detect_duplicates` 返回 `(pairs, files_count)` 保持输出统计口径不变

### 文档体系（深度分析报告 P0 第 1 项，投入产出比最高）
- 新增 `docs/`：`USER_GUIDE.md` 用户手册 / `API.md` API 文档 /
  `BEST_PRACTICES.md` 最佳实践 / `CONTRIBUTING.md` 贡献指南 /
  `ARCHITECTURE.md` 架构设计 / `FAQ.md` 常见问题
- 新增 `docs/examples/`：python / js / java / go / rust / mixed 六类示例项目
  （每个都实测跑通，含期望输出说明）

### 性能与 CI
- 全量测试 154 → **155 个** 全绿；flake8 通过；零依赖不变
- 1000 文件性能保持：首次 ~2s、峰值内存 ~40MB（v3.5 实测，本版无回归）

## [3.6.0] - 2026-08-11

### 工程化：单文件 3227 行 → 多模块（深度分析报告 P0 第 1 项）
- **拆分 tree_farm.py 为 treefarm/ 包**，纯重构、零行为改变（新旧版本 9 条命令输出逐字节一致）：
  - `treefarm/common.py` 常量 / 工具 / 文件缓存 / 语义搜索 / 指纹与增量扫描
  - `treefarm/parser.py` 多语言基因提取（Python AST + JS/TS/Java/Go 零依赖轻量解析器）
  - `treefarm/storage.py` GeneBank / TrashBin / Session / WeedIndex / SmallTree（SQLite 存储层）
  - `treefarm/config.py` 配置解析（.treefarm.toml/.json）+ LLMClient
  - `treefarm/analysis.py` 死代码 / 循环依赖 / 影响分析 / 复杂度 / 架构分层
  - `treefarm/core.py` TreeFarm 主类
  - `treefarm/cli.py` 命令分发 / main
- **入口兼容**：`tree_farm.py` 保留为聚合导出入口（`import tree_farm as tf` 拿到全部符号，
  `python3 tree_farm.py` 全部命令不变），旧代码 / install.sh / CI 无需改动即可继续使用
- **模块边界**：common 不依赖任何内部模块 → parser/storage/config → analysis → core → cli
  单向依赖链，无循环导入；多语言关键词常量（JS/JAVA/GO）收编到 common 供解析器共用

### 影响分析 / 架构分层确认跨语言（深度分析报告 P0 第 2、3 项）
- 实测确认：v3.3~v3.5 的函数级 call 基因已让影响分析对 Python/JS/Java/Go 全部生效
  （`--impact utils/helpers.js` 正确列出 Main.java / main.py / web/app.js 三个跨语言调用方）
- 架构分层基于路径命名关键词，天然语言无关（web/app.js → 表现层、repository/*.go → 数据层）
- 补充 7 个回归测试锁定跨语言能力：JS 目标被三语言调用影响 / Java 方法被 Python 调用 /
  混合语言架构分层 / 模块结构（聚合导出完整性、子模块可独立导入、入口类即 core 类）

### 性能与 CI
- 拆分后性能不降反升：100 文件 4 语言项目首次扫描 **0.41s → 0.24s（快 43%）**，
  无变化增量 0.16s（模块化减少了解释器作用域查找开销）
- CI 更新：flake8 检查扩展到 `treefarm/` 全包；零依赖检查扫描全部 9 个文件
  （并修正相对导入 `from .common import` 与项目包 `treefarm` 的误报）
- 测试用例 131 → **138 个** 全绿；flake8 通过；零依赖不变

## [3.5.0] - 2026-08-11

### Go 函数级调用分析（分析深度 4 大语言，坚持零依赖红线）
- **Go 函数级分析**：新增零依赖轻量解析器（`extract_go_call_graph`），
  识别函数/方法（receiver）/泛型函数（`func Map[T any]`）定义与调用，
  支持泛型调用（`Map[int, string](...)`）；自动剔除注释、双引号字符串、
  反引号 raw string、单引号 rune 噪音；方法定义行、接口方法声明不视为调用
- **struct 嵌入 → inherit 基因**：Go 无显式继承，匿名嵌入（`type A struct { Base }`）
  是最接近继承的形态，提取为 inherit 基因（支持指针嵌入 `*Logger`、多行嵌入）；
  普通字段（`Name string`）不误判
- **符号表接入**：Go 函数/方法/类型符号纳入项目符号表，跨文件验证生效
  （修复通用正则把 `import (` 误判为符号的噪音）

### 死代码检测扩展到 JS/TS/Java/Go（v3.5）
- 此前只有 Python 能查死代码；现在 JS 类实例化（`new X()`）、Java 类实例化 +
  继承、Go 结构体字面量（`&T{}`）/`new(T)`/嵌入均纳入「被使用」判定
- 新增多语言定义提取（`_js_defs`/`_java_defs`/`_go_defs`）供死代码/复杂度共用
- **修复统计口径 bug**：`total_functions/total_classes` 按实例数统计
  （此前按名字去重，多文件同名函数时死代码比例会超过 100%）

### 复杂度分析扩展到 JS/TS/Java/Go（v3.5）
- `calculate_complexity_any`：JS/Java 计 `if/for/while/switch/case/catch/&&/||/三元`；
  Go 计 `if/for/switch/case/select/&&/||`；函数体括号配对精确统计
- Python 仍走 AST 精确分析，行为不变

### 其他
- **调用图 JSON 输出**：`--graph --json` 输出结构化 `{nodes, edges}`，
  方便其他工具集成（Mermaid 输出保留）
- **函数级重复代码检测**：`--duplicates-func`（中文：函数级查重），
  提取函数体做 n-gram Jaccard，比文件级更精确
- **技术债务评估**：`--debt`（中文：技术债务/健康度），
  死代码比例 + 高复杂度函数 + 高度重复函数 + 循环依赖四维打分（0~100）
  + 等级（A/B/C/D）+ 优化建议
- **函数级查重性能**：按函数体大小分桶（size//64）+ 只比同桶/相邻桶，
  把 O(n²) 降到 ~O(n·k)；1000 文件项目函数级查重 1.2s
- **Java 构造器判定修复**：`_java_def_context` 改为只看 `'('` 前同一行
  （跨行语句不再干扰），行首无 token = 构造器定义（`Color(int x)`），
  但裸调用（`helper();`）不误判为定义
- 实测：120 文件 4 语言项目首次扫描 **0.85s**、无变化增量 0.30s、改 1 文件 0.45s；
  1000 文件大项目首次扫描 **1.97s**（v3.4 报告按比例预估 37s，实际快 18 倍）
- 测试用例 106 → **131 个**（新增：Go 解析 5 + Go 端到端 1 + Java 边界 3 +
  多语言死代码 4 + 多语言复杂度 4 + JSON 调用图 2 + 函数级查重 3 + 技术债务 3）
- 全部测试全绿；flake8 通过；零依赖不变（Go 解析器依旧是手写正则，不引第三方库）

## [3.4.0] - 2026-08-11

### Java 函数级调用分析（分析深度 +1 语言，坚持零依赖红线）
- **Java 函数级分析**：新增零依赖轻量解析器（`extract_java_call_graph`），
  识别方法/构造器/接口抽象方法定义与调用，支持泛型类名与基类（`class A<T> extends Base<T>`）、
  `extends`/`implements` 多接口、interface 多继承、enum/record；
  自动剔除注释、字符串、文本块（`"""`）、注解（`@Override`）、lambda（`x -> foo()`）、
  `new` 实例化噪音；Java 文件现在也产生跨文件 call/inherit 基因
  （此前 Java 只有 import 级，APK 场景 9,187 个 Java 文件从此能吃上函数级）
- **方法定义判定**：`_java_def_context` 用「'(' 前紧邻 token」启发式
  （修饰符/返回类型 → 定义；`.`/`@`/`(`/`;`/`{`/lambda 箭头 → 调用），
  正确处理「定义前一行有语句」（`String s = "x";` 后接方法定义）等真实代码形态
- **JS 解析器加固**：支持单参数无括号箭头函数（`const f = x => ...`）、
  `static`/`async`/`get`/`set` 类方法修饰符；定义行判定从「行号」改为「字符偏移」，
  更精确（同行定义+调用不再互相误伤）且 O(1) 判定（大文件不再 O(匹配数×行数)）

### 其他
- 符号提取：Java 方法定义符号（含泛型返回类型）纳入项目符号表，跨文件验证更准
- `_add_deep_gene`：`os.path.abspath` 提到循环外（大项目微优化）
- 实测：103 文件多语言项目（34 Python + 34 JS + 34 Java + 1 README）首次扫描 **0.78s**、
  无变化增量 0.43s、改 1 文件增量 0.67s——v3.4 加了 Java 分析后速度不降反升
- 测试用例 96 → **106 个**（新增：Java 解析 5 + JS 边界 4 + Java 端到端基因 1）
- 全部测试全绿；flake8 通过；零依赖不变（Java 解析器依旧是手写正则，不引第三方库）

## [3.3.0] - 2026-08-11

### 分析深度提升（目标：专业工具的 60%+，坚持零依赖红线）
- **JS/TS 函数级调用分析**：新增零依赖轻量解析器（`extract_js_call_graph`），
  识别 function / class / 箭头函数 / 对象方法定义与调用，自动剔除字符串/注释噪音；
  JS/TS 文件现在也产生跨文件 call 基因（此前只有 Python 有函数级分析）
- **多符号基因修复**：`GeneBank._seen` 去重键从 (source, target) 升级为
  (source, target, relation, symbol)——同一个文件对调用多个函数时
  （如 `import {greet, formatPrice}` 调 3 个函数），此前只入库第一个符号，现在全部入库
- **调用图可视化**：`--graph [文件]` / `分析 调用图`，输出 Mermaid flowchart
  （无参数 = 全项目文件级调用图；带文件 = 该文件符号级调用图），复制到 mermaid.live 即可渲染
- **重复代码检测**：`--duplicates [阈值]` / `分析 重复代码`，内容 n-gram Jaccard 相似度，
  大小差 >30% 自动跳过，输出疑似重复 Top N
- **小树索引**：inn_index 去重也包含 symbol，多符号引用在简报中完整展示

### 其他
- `FileCache.ast()` 改名 `get_ast()`（避免与模块级 ast 混淆，深度分析报告建议）
- 测试用例 88 → **96 个**（新增：JS 解析 2 + 多符号基因 1 + 调用图 3 + 查重 2）
- 全部测试全绿；flake8 通过；零依赖不变

## [3.2.0] - 2026-08-11

### 性能（首次扫描提速 27 倍：2 文件 19.4s → 0.7s；100 文件 1.1s；无变化增量 0.24s）
- **FileCache**：文件内容 / AST / 符号三重复用缓存（键 = 路径+mtime_ns+size，文件变更自动失效）。
  首次扫描不再重复读盘 + 重复 `ast.walk` 全树遍历（实测原实现单个文件最多被读 6 次、parse 4 次）
- **批量事务**：`GeneBank.batch()` 上下文管理器，整个扫描过程包进一个事务，
  N 条基因 N 次 fsync → 1 次（Termux 闪存上每次 fsync 约 50-100ms）；异常自动回滚
- **指纹复用**：增量扫描时 mtime/size 未变的文件直接复用旧指纹，不再重读文件算 sha1
- **惰性符号表**：项目符号表从「每次 plant 无条件全量构建」改为首次 `_build_genes` 才构建，
  无变化增量扫描完全跳过（增量 3.2s → 0.24s）
- **COUNT 代替全表**：`is_new` 判定用 `gene_count()` 不再 `all_genes()` 拉全表

### 新增
- **进度条**：首次扫描在 stderr 显示 `🌳 扫描 i/N: 文件`（不污染 stdout 输出/管道/JSON），
  `--quiet` / `--no-progress` 可关闭
- **配置文件**：支持 `.treefarm.toml`（极简 TOML 解析，零依赖）与 `.treefarm.json`，
  TOML 优先；新增 `[scan]` 段：`ignore`（gitignore 风格 glob，支持 `**`/`*`/`?`）
  + `ignore_dirs`（目录名精确匹配，整棵子树跳过）
- **JSON 输出**：`--json` 全局选项，体检 / `--brief` 输出机器可读结构化数据
  （`brief_data()`，方便 AI / 其他工具直接消费）
- **VERSION 常量**：`--json` 输出携带工具版本号

### 测试
- 测试用例 71 → **88 个**（新增：FileCache 5 + 批量事务 2 + 忽略规则 5 + TOML 配置 3 + 进度/简报 2）
- 测试套件总耗时 3.15s → 2.08s（缓存收益）

## [3.1.1] - 2026-08-09

### 修复（审查 1971 行主代码发现，含 Python 3.14 实测警告）
- **SQLite 连接泄漏**：GeneBank 无 close()，Python 3.14 下大量 `ResourceWarning: unclosed database`
  → 新增 `close()` + `__del__` 兜底 + main() 统一 try/finally 释放
- **死代码逻辑**：`add()` 里 `self.conn.total_changes or True` 恒为 True（无意义判断）
  → 改为真实 rowcount 判定
- **死代码检测漏继承**：`class Foo(utils.Base)` 的 Attribute 基类不被收集，
  `utils.Base` 会被误判为死类 → 同时识别 Name/Attribute 两种基类
- **BFS 性能**：影响分析 `queue.pop(0)` O(n²) → `collections.deque` O(n)（大项目防卡）
- **Session 浅拷贝**：`weak_pending` 字典被多个 Session 实例共享 → `copy.deepcopy`
- **无用代码**：`fix_protocol` 里的占位行 `r = self.trash.record`
- **参数容错**：`--impact` / `分析 影响` 深度参数传非数字直接崩溃 → 友好提示
- **文件检查**：`--genes <文件>` 不存在的文件静默输出空结果 → 明确报错

### 测试
- 测试用例 66 → **71 个**（新增：Attribute 基类死代码 2 + close/__del__ 2 + Session 深拷贝 1）
- 全部测试全绿，ResourceWarning 消失

## [3.1.0] - 2026-08-09

### 新增（移植自融合版 v2.2 的代码分析模块）
- **死代码检测** `--dead-code` / `分析 死代码`：基于函数调用图，找出从未被调用的函数和类
- **循环依赖检测** `--circular-deps` / `分析 循环依赖`：基于基因库 import 关系，DFS 三色标记找环
- **影响分析** `--impact <文件> [符号] [深度]` / `分析 影响 <文件>`：BFS 反向遍历调用图，
  算出"改这个文件/函数会影响哪些地方"
- **代码复杂度** `--complexity [文件]` / `分析 复杂度`：圈复杂度 + 行数 + 嵌套深度，
  支持单文件与整个项目两种模式
- **架构分层识别** `--architecture` / `分析 架构`：按命名关键词自动归类表现层/业务层/数据层/工具层
- **中文命令**：`分析 死代码/循环依赖/复杂度/架构/影响 <文件>`，与英文命令等价

### 修复（移植时实测发现）
- 架构分层关键词匹配改用**相对路径**：绝对路径里的 `storage`/`tmp` 等字样
  会把整个项目误判进数据层（如 `/storage/emulated/0/...` 下的项目全部命中 data）

### 测试
- 测试用例 40 → **66 个**（新增：死代码 7 + 复杂度 6 + 架构 7 + 循环依赖 3 + 影响分析 3）
- 新增 storage 路径污染回归测试

### 工程化
- 新增 GitHub Actions CI：Python 3.8~3.12 矩阵全量测试 + 冒烟测试（brief + 全部分析命令）
  + flake8 + 零依赖自动验证
- 新增 `API.md`（Python API + CLI 文档）

## [3.0.0] - 2026-08-09

### 新增
- **SQLite 存储层**：JSON 全量读写 → SQLite（原子事务 / 索引查询 / WAL 并发安全），
  旧版 gene_bank.json / mtime_index.json / session.json / trash.json 自动迁移（留 .bak）
- **真增量更新**：文件级变更检测（新增/修改/删除/重命名 + 内容指纹），
  改一个文件只重扫一个；删文件级联回收引用基因；重命名自动迁移基因并重扫引用方
- **Python 函数级调用分析**：AST 跨文件 call 基因 + 类继承 inherit 基因（import 级 → 函数级）
- **搜索索引预构建**：符号索引 + 内容 n-gram 索引持久化到 SQLite，查询不再实时读文件
- **语义搜索增强**：标识符归一化（camelCase/PascalCase → snake）+ 分块匹配 + 相对导入修复
- **垃圾箱滑动窗口熔断**：最近 3 次验证 ≥2 次失败才熔断（对齐设计文档「3 次里 2 次」）

### 修复
- 基因 id 用 `hash()`（每次运行随机，跨会话不稳定）→ sha1 稳定哈希
- 非 Python 文件模块名截断错误（a.js → a.）→ 多语言"被引用索引"失效
- fresh_scan 不回收已删除文件的基因（僵尸基因）
- `.treefarm.json` 按当前目录找而非项目根
- README 宣传的 `--genes` 命令缺失 → 补上

### 工程化
- 类型注解 / 魔法数字常量收编 / 分级日志（--debug）/ 长函数拆分
- `--brief --compact` 精简输出（省输出 token）

## [2.0.0] - 2026-08-08

### 新增
- 基因格式 schema 拍板（v2.0，十一字段"快递单"）
- LLM API 接入：支持 OpenAI / 通义 / DeepSeek / 月之暗面 / 豆包（自动识别）
- 语义搜索：代码专用搜索算法（符号精确 + n-gram 模糊）
- 多语言支持：Python / JS / TS / Java / C / C++ / Go / Rust
- 小树机制：双向索引预构建，O(1) 查询
- 杂草索引：非代码文件只存路径 + 大小 + 首行摘要
- 小鸟机制：强耦合直接验证，弱耦合双分支确认
- 小虫子验证：自动验证耦合关系的真实性
- 垃圾箱机制：失败文件管理，避免重复扫描

## [1.0.0] - 2026-08-07

### 新增
- 树场机制 v1.0 首版（对应设计文档《树场机制的核心.txt》）
- 基因库（JSON 存储）+ 思维树集成
- 基础 import 级依赖分析
- 真实 APK 项目实测：21MB / 9,187 个 Java 文件，第一轮省 97.9% token
