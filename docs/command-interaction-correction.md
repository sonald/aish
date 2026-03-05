# Command Error Correction - 命令纠错功能文档

本文档介绍 AI Shell 的命令纠错功能。该功能监控命令执行结果，当检测到失败或潜在错误时，通过 LLM 智能分析错误原因并提供修复建议，帮助用户快速解决问题。

## 功能概述

命令纠错是一个**智能错误处理机制**，适用于所有 shell 命令。主要包含两个核心部分：

1. **显式错误纠错**：命令执行失败（返回码 != 0）时自动触发
2. **隐式错误检测**：通过 LLM 判断命令是否真正执行失败（避免误判正常输出为错误）

### 核心特性

- **通用纠错**：适用于所有 shell 命令（git, ls, cat, grep 等）
- **LLM 智能分析**：分析命令失败的根本原因
- **自动修正建议**：提供可执行的修正命令
- **目录纠错增强**：cd 命令失败时提供相似目录建议
- **智能错误检测**：通过 LLM 判断命令是否真正执行失败

## 架构设计

### 核心组件

```
                    ┌─────────────────────────────────────────┐
                    │        Shell Input Processing          │
                    └─────────────────────────────────────────┘
                                       │
                    ┌──────────────────┼──────────────────┐
                    │                  │                  │
           ┌────────▼────────┐ ┌─────▼──────┐ ┌─────▼──────┐
           │  ShellCommand   │ │  handle_   │ │ _suggest_  │
           │    Service      │ │  error_    │ │  similar_  │
           │                 │ │  detect()  │ │ directories │
           └────────┬───────┘ └─────┬──────┘ └─────┬──────┘
                    │                │               │
           ┌────────▼────────┐ ┌─────▼──────┐ ┌─────▼──────┐
           │  cmd_error.md  │ │error_detect│ │   Local    │
           │    Prompt      │ │   .md      │ │  Matching  │
           └────────┬───────┘ └─────┬──────┘ └────────────┘
                    │                │
           ┌────────▼────────────────▼────────┐
           │   LLM Analysis & JSON Response   │
           └────────┬─────────────────────────┘
                    │
           ┌────────▼────────┐
           │ process_ai_     │
           │ response()      │
           └────────┬───────┘
                    │
           ┌────────▼────────┐
           │ handle_json_    │
           │ command()       │
           └─────────────────┘
```

### 核心方法

| 方法 | 位置 | 功能 |
|------|------|------|
| `handle_command_error()` | `shell.py:1045` | 处理显式命令错误，调用 LLM 分析 |
| `handle_error_detect()` | `shell.py:982` | 智能检测失败命令是否真正需要纠错 |
| `_suggest_similar_directories()` | `shell.py:1169` | cd 命令的本地目录匹配建议 |
| `process_ai_response()` | `shell.py:970` | 处理 LLM 响应，解析 JSON 命令 |
| `try_parse_json_output()` | `shell.py:2005` | 从响应中提取 JSON 命令 |
| `handle_json_command()` | `shell.py:2038` | 执行 LLM 返回的修正命令 |

### Prompt 模板

| 模板 | 文件 | 用途 |
|------|------|------|
| `cmd_error.md` | `prompts/cmd_error.md` | 分析命令失败原因，提供修正方案 |
| `error_detect.md` | `prompts/error_detect.md` | 检测返回码非 0 时的潜在错误 |
| `guess_command.md` | `prompts/guess_command.md` | 判断用户输入是命令还是自然语言 |
| `role.md` | `prompts/role.md` | 系统角色定义（被其他 prompt 引用） |

## 显式错误纠错

### 功能说明

当命令执行失败（返回码 != 0）时，系统自动调用 `handle_command_error()` 方法，使用 LLM 分析错误并提供解决方案。

### 处理流程

```
命令执行失败（CommandStatus.ERROR）
   ↓
调用 handle_command_error(command, stdout, stderr)
   ↓
加载 cmd_error.md 模板，注入系统信息
   ↓
构建分析请求（包含命令、输出、系统信息）
   ↓
调用 ask_oracle_fast() 获取 LLM 响应
   ↓
try_parse_json_output() 解析 JSON
   ↓
process_ai_response() 处理响应
   ↓
handle_json_command() 显示修正建议
   ↓
用户确认后执行修正命令
```

### 状态管理

纠错过程中涉及多个状态转换：

```python
# 进入纠错前
self.interruption_manager.set_state(ShellState.AI_THINKING)
self.operation_in_progress = True

# 使用 CancelScope 支持用户中断
with self._safe_cancel_scope() as scope:
    self._current_op_scope = scope
    # LLM 调用...

# 完成后恢复正常状态
self.interruption_manager.set_state(ShellState.NORMAL)
self.operation_in_progress = False
```

### JSON 响应格式

LLM 返回以下 JSON 格式：

```json
{
  "type": "corrected_command",
  "command": "修正后的完整命令 或 空字符串",
  "description": "简短说明修正原因和命令作用"
}
```

**字段说明：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | string | 固定为 `"corrected_command"` |
| `command` | string | 修正后的命令，若无解决方案则为空字符串 |
| `description` | string | 修正原因和说明 |

### 用户确认流程

当 LLM 返回修正命令后，系统会：

1. 显示修正建议：`🚀 try: <command> (<description>)`
2. 请求用户确认：`Execute this command? (y/N)`
3. 用户确认后：
   - 将命令加入批准列表（避免重复安全确认）
   - 通过 `execute_command_with_security()` 执行
   - 记录执行历史
   - 如果修正命令仍然失败，递归调用 `handle_command_error()`

### 应用场景

| 错误类型 | 示例 | 可能的修正 |
|---------|------|-----------|
| 命令拼写错误 | `git comit` | `git commit` |
| 权限问题 | `systemctl restart nginx` | `sudo systemctl restart nginx` |
| 参数顺序错误 | `grep file pattern` | `grep pattern file` |
| 路径不存在 | `cd /usr/locat/bin` | `cd /usr/local/bin` |
| 命令不存在 | `docker-compose` | `docker compose` |
| 语法错误 | `echo "hello'` | `echo 'hello'` |

## 隐式错误检测

### 功能说明

`handle_error_detect()` 方法使用 LLM 智能分析命令输出，判断命令是否真正执行失败。

### 触发条件

在 `handle_error_detect()` 内部处理逻辑中：

1. LLM 分析命令的 stdout/stderr 输出
2. 判断是真正的错误还是正常的状态输出（如进度信息、用户中断）

### 为什么要检测？

| 命令 | 行为 |
|------|------|
| `dd` | 成功时向 stderr 输出进度信息，但返回码为 0 |
| 某些管道命令 | 中间命令出错但最终返回码为 0 |
| 用户中断 | Ctrl+C 导致返回码非 0 |

隐式错误检测通过 LLM 判断 stderr 是真正的错误还是正常的进度输出，避免误报。

### 处理流程

```
LLM 分析命令执行结果（stdout + stderr）
   ↓
加载 error_detect.md 模板
   ↓
LLM 判断是真正的错误还是正常行为
   ↓
解析 JSON 响应
   ↓
判断 is_success 值：
   ├─ true → 直接返回，不做任何事（正常行为，如用户中断）
   └─ false → 显示错误原因，调用 handle_command_error() 进行纠错
```

### JSON 响应格式

```json
{
  "type": "error_detect",
  "is_success": true or false,
  "reason": "判断原因的简明解释"
}
```

**字段说明：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | string | 固定为 `"error_detect"` |
| `is_success` | boolean | `true`=正常行为无需纠错，`false`=真正的错误需要纠错 |
| `reason` | string | 判断原因的说明 |

**注意**：
- `is_success=true`：命令返回非 0 退出码，但 LLM 判断这是正常行为（如用户中断、进度输出），**不触发纠错**
- `is_success=false`：命令真正执行失败，**会触发 `handle_command_error()` 进行纠错**

## 调用位置

命令纠错在以下场景被触发：

### 内置命令

| 命令 | 处理方法 | 纠错位置 |
|------|---------|---------|
| `cd` | `handle_cd_command()` | `shell.py:1159` |
| `pushd` | `handle_pushd_command()` | `shell.py:1228` |
| `popd` | `handle_popd_command()` | `shell.py:1265` |
| `export` | `handle_export_command()` | `shell.py:1834` |
| `unset` | `handle_unset_command()` | `shell.py:1956` |
| `dirs` | `handle_dirs_command()` | `shell.py:1293` |
| `pwd` | `handle_pwd_command()` | - |

### 外部命令

| 场景 | 处理方法 | 纠错位置 |
|------|---------|---------|
| 普通命令失败 | `handle_command_or_ai()` | `shell_command_service.py:225` |
| PTY 命令失败 | `execute_command_with_pty()` | `shell_pty_executor.py:662` |
| 管道命令失败 | 管道处理逻辑 | `shell_command_service.py` |
| 条件命令失败 | `;;`, `||`, `&&` 处理 | `shell_command_service.py` |

### 工具执行

| 场景 | 说明 |
|------|------|
| BashTool 执行失败 | LLM 调用的 bash 工具执行失败 |
| PythonTool 执行失败 | Python 代码执行失败 |

## Prompt 模板设计

### cmd_error 模板

**位置：** `src/aish/prompts/cmd_error.md`

**核心指令：**

```markdown
## 任务
根据给出的执行失败(return code != 0)的命令以及相应的执行结果，
分析命令失败的原因，并提供准确的解决方案。
如果没有合适的解决方案，请返回空字符串。
```

**输出要求：**

- 只能输出一个 JSON 代码块
- 不得输出任何额外文字
- 必须使用 ` ```json ` 代码块包裹
- JSON 必须完整且可解析

**上下文注入：**

模板会注入系统环境信息：
- `uname_info`：系统信息（uname -a）
- `user_nickname`：用户昵称
- `os_info`：发行版信息
- `basic_env_info`：基础环境信息（PATH、HOME 等）
- `output_language`：输出语言偏好（中文/英文）

### error_detect 模板

**位置：** `src/aish/prompts/error_detect.md`

**用途：** 判断命令失败是否真正需要纠错，还是只是正常的行为（如进度信息、用户中断）

**核心指令：**

```markdown
## 任务
根据命令的执行结果（包括标准输出、标准错误），判断命令是否真正执行失败，还是只是正常的状态输出（如进度信息、用户中断等）。

IMPORTANT:
- 任务给出的命令都是 return code 不为 0 的情况
- 某些命令（如 dd）会向 stderr 输出进度信息，即使命令最终成功
- 某些命令被用户中断（Ctrl+C）会导致 returncode != 0
- 需要根据 stderr 的具体内容来判断这是真正的错误还是正常行为
- 如果是正常行为（如用户中断、进度输出），设置 is_success = true
```

**响应格式：**

```json
{
  "type": "error_detect",
  "is_success": true or false,
  "reason": "判断原因的简明解释"
}
```

### guess_command 模板

**位置：** `src/aish/prompts/guess_command.md`

**功能：** 判断用户输入是 shell 命令还是自然语言问题

**决策标准：**

1. **命令**（返回 `true`）：
   - 第一个词匹配 POSIX 内置命令
   - 匹配 `$PATH` 中的可执行文件
   - 以 `./`, `bash -c`, `python -` 等开头
   - 包含命令分隔符（`;`, `&&`, `|`, `>` 等）

2. **问题**（返回 `false`）：
   - 包含问号或 WH-词
   - 以 "show", "explain", "tell me", "how to" 开头
   - 描述目标而非指令

## 状态管理

### ShellState 枚举

纠错过程中涉及以下状态：

| 状态 | 说明 |
|------|------|
| `NORMAL` | 正常交互状态 |
| `INPUTTING` | 用户正在输入 |
| `AI_THINKING` | LLM 处理中 |
| `SANDBOX_EVAL` | 沙箱安全评估中 |
| `COMMAND_EXEC` | 命令执行中 |
| `EXIT_PENDING` | 退出待确认 |
| `CLEAR_PENDING` | 清空输入待确认 |

### 取消机制

```python
# 创建可取消的操作作用域
with self._safe_cancel_scope() as scope:
    self._current_op_scope = scope
    self.operation_in_progress = True

    try:
        # LLM 调用...
    except anyio.get_cancelled_exc_class():
        # 用户按 Ctrl+C 取消
        completed_normally = False
        raise
    finally:
        # 清理状态
        self._current_op_scope = None
        self.interruption_manager.set_state(ShellState.NORMAL)
        if completed_normally:
            self.interruption_manager._input_buffer = None
            self.interruption_manager._restore_input = False
```

### 命令检测失败处理

当 LLM 在命令检测阶段（判断输入是命令还是自然语言）被用户取消时，系统会设置 `_command_detection_llm_failed` 标志，避免对同一输入重复触发 LLM 请求。

```python
async def process_input(self, user_input: str):
    # Reset per-input LLM failure flags
    self._command_detection_llm_failed = False
    # ...
```

这确保了用户取消 LLM 请求后，不会再次看到相同的 LLM 分析提示。

## 容错机制

| 机制 | 说明 |
|------|------|
| 静默失败 | 纠错功能本身的错误不影响正常命令执行 |
| 超时保护 | LLM 调用超时自动取消（AnyIO CancelScope） |
| 取消支持 | 用户可以按 Ctrl+C 中断 LLM 分析 |
| 空响应处理 | LLM 返回空 command 时只显示说明 |
| JSON 解析容错 | 支持多种 JSON 格式（代码块、单行） |
| 异常捕获 | 所有异常都被捕获，不影响主流程 |

## 性能考虑

### LLM 调用开销

| 项目 | 说明 |
|------|------|
| 调用时机 | 仅在命令失败或检测到潜在错误时 |
| 超时控制 | 使用 AnyIO CancelScope |
| 异步执行 | 不阻塞主循环 |

## 相关文件

| 文件 | 说明 |
|------|------|
| `src/aish/shell.py` | 主 shell 实现，包含所有纠错逻辑 |
| `src/aish/shell_enhanced/shell_command_service.py` | 命令执行服务 |
| `src/aish/shell_enhanced/shell_pty_executor.py` | PTY 命令执行实现 |
| `src/aish/shell_enhanced/shell_types.py` | 命令状态类型定义 |
| `src/aish/shell_enhanced/shell_input_router.py` | 输入意图路由 |
| `src/aish/builtin/handlers.py` | 内置命令处理器 |
| `src/aish/interruption.py` | 中断和状态管理 |
| `src/aish/prompts/cmd_error.md` | LLM 命令纠错 prompt |
| `src/aish/prompts/error_detect.md` | LLM 错误检测 prompt |
| `src/aish/prompts/guess_command.md` | 命令/问题判断 prompt |
| `src/aish/prompts/role.md` | 系统角色定义（被其他 prompt 引用） |
