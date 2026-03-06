# AISH Roadmap

> **Vision**: Transform AISH from an intrusive AI shell into an intelligent sidecar assistant that gives users absolute control over their command line.

---

## 🎯 Strategic Goals (Q2 2026)

### Core Principles
1. **User Control First**: AI provides suggestions, never blocks the main workflow
2. **Async by Default**: All AI analysis happens in the background
3. **Smart Intervention**: Reduce false positives by 95% through intelligent filtering
4. **Progressive Enhancement**: Maintain backward compatibility while evolving architecture

### Success Metrics
- ✅ Command execution returns to prompt in < 50ms
- ✅ Zero blocking AI calls in main execution path
- ✅ False positive rate < 5%
- ✅ User satisfaction > 4.5/5

---

## 📅 Release Timeline

```
Week 1-2   │ v0.1.0 → v0.2.0  │ Async AI Sidecar Architecture
Week 3-4   │ v0.2.0 → v0.3.0  │ Smart Analysis & User Controls
Week 5-6   │ v0.3.0 → v0.4.0  │ Plan Mode & Enhanced Tools
Week 7-8   │ v0.4.0 → v0.5.0  │ Multi-Agent System
Week 9-10  │ v0.5.0 → v0.6.0  │ Rich UI & Task Management
Week 11-12 │ v0.6.0 → v0.7.0  │ Agent SDK & MCP Protocol
```

---

## 🚀 Phase 1: Architecture Overhaul (Week 1-4)

### v0.1.1 (Week 1) - Critical Fixes
**Type**: Patch Release

**Fixes**
- 🐛 Implement skill tool invocation (`skill.py` TODO)
- 🐛 Fix tool call history not added to memory (`shell.py:889`)
- 🐛 Disable auto-trigger of `handle_error_detect()` (temporary mitigation)

**Impact**: Reduces immediate user pain points

---

### v0.2.0 (Week 2) - Async AI Sidecar 🔥
**Type**: Minor Release (Breaking Changes)

**New Architecture**
```
┌─────────────────────────────────────────────────────────┐
│                    Main Shell Process                    │
│  ┌──────────────┐                                       │
│  │ User Input   │ → Execute → Display → Prompt         │
│  └──────────────┘      ↓                                │
│                   Enqueue Event (non-blocking)          │
└─────────────────────────┼───────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│                  Background AI Sidecar                   │
│  ┌──────────────┐   ┌──────────────┐   ┌────────────┐ │
│  │ Event Queue  │ → │ Smart Filter │ → │ LLM Worker │ │
│  └──────────────┘   └──────────────┘   └────────────┘ │
│                          ↓                               │
│                   ┌──────────────┐                      │
│                   │ Notification │ → [AI:2] indicator  │
│                   └──────────────┘                      │
└─────────────────────────────────────────────────────────┘
```

**New Modules**
- `src/aish/sidecar/event_queue.py` - Non-blocking command event queue
- `src/aish/sidecar/analyzer.py` - Smart filtering logic
- `src/aish/sidecar/worker.py` - Background LLM analysis worker
- `src/aish/sidecar/notification.py` - Non-intrusive notification system
- `src/aish/sidecar/storage.py` - Analysis result persistence

**Key Features**
1. **Event Queue**: Commands enqueue completion events without blocking
2. **Smart Analyzer**: Filters out false positives (grep, diff, ssh, Ctrl-C, etc.)
3. **Background Worker**: Async LLM analysis in separate task
4. **Notification Center**: Lightweight status indicator in prompt (`[AI:2]`)

**User Commands**
- `:ai` or `:ai last` - View latest suggestion
- `:ai list` - List all pending suggestions
- `:ai show <n>` - View specific suggestion
- `:ai apply <n>` - Apply suggestion (enters security approval flow)

**Breaking Changes**
- ❌ Removed auto-trigger of `handle_error_detect()`
- ❌ Removed auto-trigger of `handle_command_error()`
- ❌ Deprecated `ShellState.CORRECT_PENDING`

**Migration Guide**
- Old behavior: AI automatically interrupts on command failure
- New behavior: AI analyzes in background, user explicitly views suggestions
- Compatibility: `handle_command_error()` API preserved for manual invocation

**Configuration**
```yaml
# ~/.config/aish/config.yaml
sidecar:
  enabled: true
  max_queue_size: 100
  worker_threads: 1
```

---

### v0.2.1 (Week 3) - Stability
**Type**: Patch Release

**Fixes**
- 🐛 Fix sidecar worker memory leak
- 🐛 Optimize event queue performance
- 📊 Add sidecar analysis metrics (success rate, latency)

---

### v0.3.0 (Week 4) - Smart Analysis
**Type**: Minor Release

**Enhanced Intelligence**
- 🧠 **Context-Aware Filtering**
  - Command history analysis (consecutive failures → higher priority)
  - User behavior tracking (immediate retry → lower priority)
  - Time-based adjustment (late night → lower priority)

**Configurable Strategies**
```yaml
sidecar:
  analysis_mode: smart  # smart | aggressive | minimal
  notification_style: indicator  # indicator | toast | silent

  # Smart mode rules
  smart_rules:
    ignore_commands: [grep, diff, test, ssh]
    ignore_exit_codes: [130]  # Ctrl-C
    benign_stderr_patterns:
      - "^Warning:"
      - "^Note:"
```

**New Commands**
- `:ai clear` - Clear all suggestions
- `:ai stats` - Show analysis statistics

**Improvements**
- 95% reduction in false positives
- < 50ms command execution overhead
- Configurable notification styles

---

## 🛠️ Phase 2: Core Capabilities (Week 5-8)

### v0.3.1 (Week 5) - Refinement
**Type**: Patch Release

**Fixes**
- 🐛 Fix edge cases in smart filtering
- 🐛 Optimize suggestion storage performance

---

### v0.4.0 (Week 6) - Plan Mode & Tools
**Type**: Minor Release

**Plan Mode** (Inspired by Claude Code)
- 🎯 `PlanAgent`: Task decomposition expert
- 📋 User approval workflow before execution
- 💾 Plan persistence to `.aish/plans/`

**Usage**
```bash
aish> ;deploy the application to production
[AI creates plan]
┌─ Deployment Plan ─────────────────────────────────────┐
│ 1. Run test suite                                     │
│ 2. Build Docker image                                 │
│ 3. Push to registry                                   │
│ 4. Update Kubernetes deployment                       │
│                                                        │
│ Approve? [y/N]:                                       │
└───────────────────────────────────────────────────────┘
```

**Enhanced Tool Suite**
- 🔍 `WebSearchTool`: DuckDuckGo integration
- 🔧 `GitTool`: Git operations wrapper (status, diff, commit, push)
- 🧠 `CodeAnalysisTool`: AST-based code analysis (tree-sitter)

**New Commands**
- `:plan` - Enter plan mode
- `:plan show` - View current plan
- `:plan approve` - Approve and execute plan

---

### v0.4.1 (Week 7) - Polish
**Type**: Patch Release

**Fixes**
- 🐛 Fix plan mode edge cases
- 🐛 Optimize WebSearch result parsing
- 📝 Add plan mode documentation

---

### v0.5.0 (Week 8) - Multi-Agent System
**Type**: Minor Release

**Agent Ecosystem**
- 🤖 `CodeReviewAgent`: Static analysis + best practices
- 🐛 `DebugAgent`: Log analysis + root cause identification
- 🔍 `ResearchAgent`: Web + documentation search
- 🎭 `AgentOrchestrator`: Parallel/sequential agent coordination

**Agent Architecture**
```python
# Example: Parallel agent execution
aish> ;review this PR and check for security issues

[Spawning agents in parallel]
├─ CodeReviewAgent: Analyzing code quality...
├─ SecurityAgent: Scanning for vulnerabilities...
└─ TestAgent: Checking test coverage...

[Results aggregated and presented]
```

**Smart Context Management**
- 📊 Priority-based message ranking
- 🗜️ Auto-summarization of long conversations (using small model)
- 🧠 Cross-session knowledge base (optional vector search)

**Configuration**
```yaml
agents:
  enabled: true
  max_parallel: 3
  context_window: 8000
  auto_summarize: true
```

---

## 🎨 Phase 3: User Experience & Ecosystem (Week 9-12)

### v0.5.1 (Week 9) - Stability
**Type**: Patch Release

**Fixes**
- 🐛 Fix agent parallel execution race conditions
- 🐛 Optimize context compression algorithm
- 📊 Add agent performance metrics

---

### v0.6.0 (Week 10) - Rich UI & Tasks
**Type**: Minor Release

**Rich UI Enhancements**
- 🎨 Real-time progress bars for agent execution
- 🌳 Task tree visualization
- 🔍 Interactive confirmation panels with diff preview

**Task Management System** (Inspired by Claude Code)
- 📋 Built-in task tracking (`TaskCreate`, `TaskUpdate`, `TaskList`)
- 🔗 Task dependencies and priorities
- 💾 Task persistence and recovery

**Usage**
```bash
aish> :task list
┌─ Active Tasks ────────────────────────────────────────┐
│ [1] ⏳ Implement user authentication                  │
│     ├─ [2] ✅ Set up database schema                  │
│     ├─ [3] 🔄 Create login endpoint                   │
│     └─ [4] ⏸️  Add JWT token validation               │
└───────────────────────────────────────────────────────┘

aish> :task show 3
[Detailed task view with progress and blockers]
```

**New Commands**
- `:task create` - Create new task
- `:task list` - List all tasks
- `:task show <id>` - View task details
- `:task complete <id>` - Mark task as complete

---

### v0.6.1 (Week 11) - Optimization
**Type**: Patch Release

**Fixes**
- 🐛 Fix task manager memory usage
- 🐛 Optimize Rich UI rendering performance
- 📝 Add task management documentation

---

### v0.7.0 (Week 12) - Agent SDK & MCP
**Type**: Minor Release

**Agent SDK**
- 🔌 Standardized agent development interface
- 🏗️ Agent template generator (`aish create-agent`)
- 🌐 Agent marketplace (community sharing)

**Agent Development Example**
```bash
# Create new agent
aish create-agent --name my-agent --type diagnostic

# Generated structure
~/.config/aish/agents/my-agent/
├── agent.py          # Agent implementation
├── config.yaml       # Agent configuration
├── README.md         # Documentation
└── tests/            # Unit tests
```

**MCP Protocol Support**
- 🔗 Compatible with Claude Desktop MCP servers
- 🔌 Built-in MCP client for external services
- 📡 Bidirectional communication with MCP ecosystem

**Observability Dashboard** (Optional)
- 📊 Web UI (FastAPI-based)
- 📈 Real-time session monitoring
- 💰 Token usage statistics
- ⚡ Agent performance analytics

**Configuration**
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
  enabled: false  # Optional web dashboard
  port: 8080
```

---

## 📊 Feature Comparison Matrix

| Feature | v0.1.0 (Current) | v0.2.0 | v0.4.0 | v0.6.0 | v0.7.0 |
|---------|------------------|--------|--------|--------|--------|
| **Async AI Analysis** | ❌ | ✅ | ✅ | ✅ | ✅ |
| **Smart Filtering** | ❌ | ⚠️ Basic | ✅ Advanced | ✅ | ✅ |
| **Plan Mode** | ❌ | ❌ | ✅ | ✅ | ✅ |
| **Multi-Agent** | ⚠️ 1 Agent | ⚠️ 1 Agent | ⚠️ 1 Agent | ✅ 4+ Agents | ✅ |
| **Task Management** | ❌ | ❌ | ❌ | ✅ | ✅ |
| **Agent SDK** | ❌ | ❌ | ❌ | ❌ | ✅ |
| **MCP Protocol** | ❌ | ❌ | ❌ | ❌ | ✅ |
| **Rich UI** | ⚠️ Basic | ⚠️ Basic | ⚠️ Basic | ✅ | ✅ |

---

## 🎯 Competitive Positioning

### vs Claude Code

| Aspect | AISH v0.7.0 | Claude Code |
|--------|-------------|-------------|
| **Open Source** | ✅ Apache 2.0 | ❌ Proprietary |
| **Local Models** | ✅ Full support | ❌ Limited |
| **Multi-Provider** | ✅ LiteLLM | ⚠️ Mainly Claude |
| **PTY Support** | ✅ Full | ⚠️ Limited |
| **Ops Focus** | ✅ System diagnostics | ⚠️ Dev-focused |
| **Privacy** | ✅ Local-first | ⚠️ Cloud-based |
| **Cost** | ✅ Free | ❌ Subscription |
| **Async AI** | ✅ Non-blocking | ⚠️ Blocking |

### Unique Value Propositions

1. **Non-Intrusive AI**: Background analysis never blocks user workflow
2. **Ops-Native**: Built for system administration and troubleshooting
3. **Privacy-First**: Local model support with no data leaving your machine
4. **Community-Driven**: Open source with extensible agent ecosystem
5. **Enterprise-Ready**: Sandbox, audit logs, and fine-grained permissions

---

## 🚨 Risk Management

| Risk | Impact | Mitigation | Status |
|------|--------|------------|--------|
| **Async complexity** | High | Extensive testing + fallback mode | Week 1-2 |
| **Smart filter accuracy** | Medium | Configurable rules + user feedback | Week 3-4 |
| **Worker resource usage** | Medium | Queue limits + auto-throttling | Week 2-3 |
| **User adoption** | Low | Progressive migration + docs | Ongoing |
| **Agent coordination** | Medium | Timeout limits + error recovery | Week 7-8 |

---

## 📈 Success Metrics

### Technical KPIs
- ✅ Command execution latency < 50ms (P95)
- ✅ AI analysis false positive rate < 5%
- ✅ Sidecar worker memory < 50MB
- ✅ Agent response time < 2s (P95)
- ✅ Test coverage > 80%

### User KPIs
- 📈 Daily active users growth > 20% MoM
- ⭐ User satisfaction > 4.5/5
- 🤝 Community skills > 50
- 🏢 Enterprise deployments > 10

### Ecosystem KPIs
- 🔌 Community agents > 20
- 📦 MCP integrations > 5
- 📝 Documentation completeness > 90%

---

## 🤝 Contributing

We welcome contributions! Priority areas:

### Week 1-4 (Phase 1)
- 🔧 Sidecar architecture implementation
- 🧪 Smart filtering rule development
- 📝 Migration guide documentation

### Week 5-8 (Phase 2)
- 🤖 New agent implementations
- 🔍 Tool integrations (web search, code analysis)
- 🧠 Context management optimization

### Week 9-12 (Phase 3)
- 🎨 UI/UX improvements
- 🔌 Agent SDK development
- 📊 Observability dashboard

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines.

---

## 📚 Resources

- **Documentation**: [docs.aishell.ai](https://docs.aishell.ai)
- **GitHub**: [github.com/AI-Shell-Team/aish](https://github.com/AI-Shell-Team/aish)
- **Discord**: [discord.gg/aish](https://discord.gg/aish)
- **Blog**: [blog.aishell.ai](https://blog.aishell.ai)

---

## 📝 Changelog

### Upcoming
- See individual release sections above

### v0.1.0 (Current)
- ✅ Full PTY support
- ✅ Multi-model support (LiteLLM)
- ✅ Basic security risk assessment
- ✅ Skills hot-reload system
- ✅ ReAct diagnostic agent
- ✅ Session persistence (SQLite)
- ✅ Output offload mechanism
- ✅ i18n support

---

**Last Updated**: 2026-03-06
**Roadmap Version**: 2.0
**Status**: 🟢 Active Development
