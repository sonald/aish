# AISH 产品路线图

> **愿景**：将 AISH 从入侵式 AI Shell 转变为智能旁路助手，让用户完全掌控命令行。

---

## 🎯 战略目标（2026 Q2）

### 核心原则
1. **用户控制优先**：AI 提供建议，绝不阻塞主流程
2. **默认异步**：所有 AI 分析在后台进行
3. **智能介入**：通过智能过滤减少 95% 的误报
4. **渐进增强**：在架构演进中保持向后兼容

### 成功指标
- ✅ 命令执行后 < 50ms 返回提示符
- ✅ 主执行路径零阻塞 AI 调用
- ✅ 误报率 < 5%
- ✅ 用户满意度 > 4.5/5

---

## 📅 发布时间线

```
第 1-2 周  │ v0.1.0 → v0.2.0  │ 异步 AI 旁路架构
第 3-4 周  │ v0.2.0 → v0.3.0  │ 智能分析与用户控制
第 5-6 周  │ v0.3.0 → v0.4.0  │ 计划模式与增强工具
第 7-8 周  │ v0.4.0 → v0.5.0  │ 多 Agent 系统
第 9-10 周 │ v0.5.0 → v0.6.0  │ Rich UI 与任务管理
第 11-12 周│ v0.6.0 → v0.7.0  │ Agent SDK 与 MCP 协议
```

---

## 🚀 第一阶段：架构重构（第 1-4 周）

### v0.1.1（第 1 周）- 紧急修复
**类型**：补丁版本

**修复内容**
- 🐛 实现 Skill 工具调用（`skill.py` TODO）
- 🐛 修复工具调用历史未加入内存（`shell.py:889`）
- 🐛 禁用 `handle_error_detect()` 自动触发（临时缓解方案）

**影响**：减少用户当前痛点

---

### v0.2.0（第 2 周）- 异步 AI 旁路 🔥
**类型**：次要版本（破坏性变更）

**新架构**
```
┌─────────────────────────────────────────────────────────┐
│                      主 Shell 进程                       │
│  ┌──────────────┐                                       │
│  │  用户输入    │ → 执行 → 显示 → 提示符               │
│  └──────────────┘      ↓                                │
│                   入队事件（非阻塞）                     │
└─────────────────────────┼───────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│                    后台 AI 旁路                          │
│  ┌──────────────┐   ┌──────────────┐   ┌────────────┐ │
│  │  事件队列    │ → │  智能过滤器  │ → │ LLM Worker │ │
│  └──────────────┘   └──────────────┘   └────────────┘ │
│                          ↓                               │
│                   ┌──────────────┐                      │
│                   │  通知中心    │ → [AI:2] 指示器     │
│                   └──────────────┘                      │
└─────────────────────────────────────────────────────────┘
```

**新增模块**
- `src/aish/sidecar/event_queue.py` - 非阻塞命令事件队列
- `src/aish/sidecar/analyzer.py` - 智能过滤逻辑
- `src/aish/sidecar/worker.py` - 后台 LLM 分析 Worker
- `src/aish/sidecar/notification.py` - 非入侵式通知系统
- `src/aish/sidecar/storage.py` - 分析结果持久化

**核心特性**
1. **事件队列**：命令完成事件入队，不阻塞主流程
2. **智能分析器**：过滤误报（grep、diff、ssh、Ctrl-C 等）
3. **后台 Worker**：独立任务中异步 LLM 分析
4. **通知中心**：提示符中的轻量级状态指示器（`[AI:2]`）

**用户命令**
- `:ai` 或 `:ai last` - 查看最新建议
- `:ai list` - 列出所有待查看建议
- `:ai show <n>` - 查看指定建议
- `:ai apply <n>` - 应用建议（进入安全审批流程）

**破坏性变更**
- ❌ 移除 `handle_error_detect()` 自动触发
- ❌ 移除 `handle_command_error()` 自动触发
- ❌ 废弃 `ShellState.CORRECT_PENDING` 状态

**迁移指南**
- 旧行为：命令失败时 AI 自动中断
- 新行为：AI 后台分析，用户主动查看建议
- 兼容性：保留 `handle_command_error()` API 供手动调用

**配置**
```yaml
# ~/.config/aish/config.yaml
sidecar:
  enabled: true
  max_queue_size: 100
  worker_threads: 1
```

---

### v0.2.1（第 3 周）- 稳定性
**类型**：补丁版本

**修复内容**
- 🐛 修复 Sidecar Worker 内存泄漏
- 🐛 优化事件队列性能
- 📊 添加 Sidecar 分析指标（成功率、延迟）

---

### v0.3.0（第 4 周）- 智能分析
**类型**：次要版本

**增强智能**
- 🧠 **上下文感知过滤**
  - 命令历史分析（连续失败 → 提高优先级）
  - 用户行为追踪（立即重试 → 降低优先级）
  - 基于时间调整（深夜 → 降低优先级）

**可配置策略**
```yaml
sidecar:
  analysis_mode: smart  # smart | aggressive | minimal
  notification_style: indicator  # indicator | toast | silent

  # 智能模式规则
  smart_rules:
    ignore_commands: [grep, diff, test, ssh]
    ignore_exit_codes: [130]  # Ctrl-C
    benign_stderr_patterns:
      - "^Warning:"
      - "^Note:"
```

**新增命令**
- `:ai clear` - 清空所有建议
- `:ai stats` - 显示分析统计

**改进**
- 误报减少 95%
- 命令执行开销 < 50ms
- 可配置通知样式

---

## 🛠️ 第二阶段：核心能力（第 5-8 周）

### v0.3.1（第 5 周）- 优化
**类型**：补丁版本

**修复内容**
- 🐛 修复智能过滤的边界情况
- 🐛 优化建议存储性能

---

### v0.4.0（第 6 周）- 计划模式与工具
**类型**：次要版本

**计划模式**（参考 Claude Code）
- 🎯 `PlanAgent`：任务分解专家
- 📋 执行前用户审批流程
- 💾 计划持久化到 `.aish/plans/`

**使用示例**
```bash
aish> ;部署应用到生产环境
[AI 创建计划]
┌─ 部署计划 ────────────────────────────────────────────┐
│ 1. 运行测试套件                                        │
│ 2. 构建 Docker 镜像                                    │
│ 3. 推送到镜像仓库                                      │
│ 4. 更新 Kubernetes 部署                                │
│                                                        │
│ 批准？[y/N]:                                           │
└───────────────────────────────────────────────────────┘
```

**增强工具集**
- 🔍 `WebSearchTool`：DuckDuckGo 集成
- 🔧 `GitTool`：Git 操作封装（status、diff、commit、push）
- 🧠 `CodeAnalysisTool`：基于 AST 的代码分析（tree-sitter）

**新增命令**
- `:plan` - 进入计划模式
- `:plan show` - 查看当前计划
- `:plan approve` - 批准并执行计划

---

### v0.4.1（第 7 周）- 打磨
**类型**：补丁版本

**修复内容**
- 🐛 修复计划模式边界情况
- 🐛 优化 WebSearch 结果解析
- 📝 添加计划模式文档

---

### v0.5.0（第 8 周）- 多 Agent 系统
**类型**：次要版本

**Agent 生态**
- 🤖 `CodeReviewAgent`：静态分析 + 最佳实践
- 🐛 `DebugAgent`：日志分析 + 根因定位
- 🔍 `ResearchAgent`：Web + 文档搜索
- 🎭 `AgentOrchestrator`：并行/串行 Agent 协调

**Agent 架构**
```python
# 示例：并行 Agent 执行
aish> ;审查这个 PR 并检查安全问题

[并行启动 Agents]
├─ CodeReviewAgent: 分析代码质量...
├─ SecurityAgent: 扫描漏洞...
└─ TestAgent: 检查测试覆盖率...

[结果聚合并展示]
```

**智能上下文管理**
- 📊 基于优先级的消息排序
- 🗜️ 长对话自动摘要（使用小模型）
- 🧠 跨会话知识库（可选向量检索）

**配置**
```yaml
agents:
  enabled: true
  max_parallel: 3
  context_window: 8000
  auto_summarize: true
```

---

## 🎨 第三阶段：用户体验与生态（第 9-12 周）

### v0.5.1（第 9 周）- 稳定性
**类型**：补丁版本

**修复内容**
- 🐛 修复 Agent 并行执行竞态条件
- 🐛 优化上下文压缩算法
- 📊 添加 Agent 性能指标

---

### v0.6.0（第 10 周）- Rich UI 与任务管理
**类型**：次要版本

**Rich UI 增强**
- 🎨 Agent 执行实时进度条
- 🌳 任务树可视化
- 🔍 交互式确认面板（支持 diff 预览）

**任务管理系统**（参考 Claude Code）
- 📋 内置任务追踪（`TaskCreate`、`TaskUpdate`、`TaskList`）
- 🔗 任务依赖与优先级
- 💾 任务持久化与恢复

**使用示例**
```bash
aish> :task list
┌─ 活跃任务 ────────────────────────────────────────────┐
│ [1] ⏳ 实现用户认证                                    │
│     ├─ [2] ✅ 设置数据库 schema                        │
│     ├─ [3] 🔄 创建登录端点                            │
│     └─ [4] ⏸️  添加 JWT token 验证                    │
└───────────────────────────────────────────────────────┘

aish> :task show 3
[详细任务视图，包含进度和阻塞项]
```

**新增命令**
- `:task create` - 创建新任务
- `:task list` - 列出所有任务
- `:task show <id>` - 查看任务详情
- `:task complete <id>` - 标记任务完成

---

### v0.6.1（第 11 周）- 优化
**类型**：补丁版本

**修复内容**
- 🐛 修复任务管理器内存占用
- 🐛 优化 Rich UI 渲染性能
- 📝 添加任务管理文档

---

### v0.7.0（第 12 周）- Agent SDK 与 MCP
**类型**：次要版本

**Agent SDK**
- 🔌 标准化 Agent 开发接口
- 🏗️ Agent 模板生成器（`aish create-agent`）
- 🌐 Agent 市场（社区分享）

**Agent 开发示例**
```bash
# 创建新 Agent
aish create-agent --name my-agent --type diagnostic

# 生成的结构
~/.config/aish/agents/my-agent/
├── agent.py          # Agent 实现
├── config.yaml       # Agent 配置
├── README.md         # 文档
└── tests/            # 单元测试
```

**MCP 协议支持**
- 🔗 兼容 Claude Desktop MCP 服务器
- 🔌 内置 MCP 客户端调用外部服务
- 📡 与 MCP 生态双向通信

**可观测性面板**（可选）
- 📊 Web UI（基于 FastAPI）
- 📈 实时会话监控
- 💰 Token 使用统计
- ⚡ Agent 性能分析

**配置**
```yaml
mcp:
  enabled: true
  servers:
    - name: filesystem
      command: npx
      args: [-y, @modelcontextprotocol/server-filesystem, /tmp]
    - name: github
      command: npx
      args: [-y, @modelcontextprotocol/server-github]
      env:
        GITHUB_TOKEN: ${GITHUB_TOKEN}

observability:
  enabled: false  # 可选 Web 面板
  port: 8080
```

---

## 📊 功能对比矩阵

| 功能 | v0.1.0（当前） | v0.2.0 | v0.4.0 | v0.6.0 | v0.7.0 |
|------|----------------|--------|--------|--------|--------|
| **异步 AI 分析** | ❌ | ✅ | ✅ | ✅ | ✅ |
| **智能过滤** | ❌ | ⚠️ 基础 | ✅ 高级 | ✅ | ✅ |
| **计划模式** | ❌ | ❌ | ✅ | ✅ | ✅ |
| **多 Agent** | ⚠️ 1 个 | ⚠️ 1 个 | ⚠️ 1 个 | ✅ 4+ 个 | ✅ |
| **任务管理** | ❌ | ❌ | ❌ | ✅ | ✅ |
| **Agent SDK** | ❌ | ❌ | ❌ | ❌ | ✅ |
| **MCP 协议** | ❌ | ❌ | ❌ | ❌ | ✅ |
| **Rich UI** | ⚠️ 基础 | ⚠️ 基础 | ⚠️ 基础 | ✅ | ✅ |

---

## 🎯 竞争定位

### vs Claude Code

| 方面 | AISH v0.7.0 | Claude Code |
|------|-------------|-------------|
| **开源** | ✅ Apache 2.0 | ❌ 专有软件 |
| **本地模型** | ✅ 完全支持 | ❌ 有限 |
| **多提供商** | ✅ LiteLLM | ⚠️ 主要 Claude |
| **PTY 支持** | ✅ 完整 | ⚠️ 有限 |
| **运维聚焦** | ✅ 系统诊断 | ⚠️ 开发聚焦 |
| **隐私** | ✅ 本地优先 | ⚠️ 云端 |
| **成本** | ✅ 免费 | ❌ 订阅制 |
| **异步 AI** | ✅ 非阻塞 | ⚠️ 阻塞 |

### 独特价值主张

1. **非入侵式 AI**：后台分析绝不阻塞用户工作流
2. **运维原生**：专为系统管理和故障排查而生
3. **隐私优先**：本地模型支持，数据不出本机
4. **社区驱动**：开源且可扩展的 Agent 生态
5. **企业就绪**：沙箱、审计日志、细粒度权限

---

## 🚨 风险管理

| 风险 | 影响 | 缓解措施 | 状态 |
|------|------|----------|--------|
| **异步复杂度** | 高 | 充分测试 + 降级模式 | 第 1-2 周 |
| **智能过滤准确性** | 中 | 可配置规则 + 用户反馈 | 第 3-4 周 |
| **Worker 资源占用** | 中 | 队列限制 + 自动节流 | 第 2-3 周 |
| **用户采用** | 低 | 渐进式迁移 + 文档 | 持续 |
| **Agent 协调** | 中 | 超时限制 + 错误恢复 | 第 7-8 周 |

---

## 📈 成功指标

### 技术 KPI
- ✅ 命令执行延迟 < 50ms（P95）
- ✅ AI 分析误报率 < 5%
- ✅ Sidecar Worker 内存 < 50MB
- ✅ Agent 响应时间 < 2s（P95）
- ✅ 测试覆盖率 > 80%

### 用户 KPI
- 📈 日活用户增长 > 20% MoM
- ⭐ 用户满意度 > 4.5/5
- 🤝 社区 Skills > 50 个
- 🏢 企业部署 > 10 家

### 生态 KPI
- 🔌 社区 Agents > 20 个
- 📦 MCP 集成 > 5 个
- 📝 文档完整度 > 90%

---

## 🤝 贡献

欢迎贡献！优先领域：

### 第 1-4 周（第一阶段）
- 🔧 Sidecar 架构实现
- 🧪 智能过滤规则开发
- 📝 迁移指南文档

### 第 5-8 周（第二阶段）
- 🤖 新 Agent 实现
- 🔍 工具集成（Web 搜索、代码分析）
- 🧠 上下文管理优化

### 第 9-12 周（第三阶段）
- 🎨 UI/UX 改进
- 🔌 Agent SDK 开发
- 📊 可观测性面板

详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

---

## 📚 资源

- **文档**：[docs.aishell.ai](https://docs.aishell.ai)
- **GitHub**：[github.com/AI-Shell-Team/aish](https://github.com/AI-Shell-Team/aish)
- **Discord**：[discord.gg/aish](https://discord.gg/aish)
- **博客**：[blog.aishell.ai](https://blog.aishell.ai)

---

## 📝 更新日志

### 即将发布
- 见上述各版本详情

### v0.1.0（当前）
- ✅ 完整 PTY 支持
- ✅ 多模型支持（LiteLLM）
- ✅ 基础安全风险评估
- ✅ Skills 热加载系统
- ✅ ReAct 诊断 Agent
- ✅ 会话持久化（SQLite）
- ✅ 输出 Offload 机制
- ✅ i18n 支持

---

**最后更新**：2026-03-06
**路线图版本**：2.0
**状态**：🟢 活跃开发中
