# aish bash rc wrapper
# This file is used as rcfile for interactive bash

# Enable readline for interactive use
set -o emacs

# Source user's bashrc if exists
if [ -f ~/.bashrc ]; then
    source ~/.bashrc
fi

# Source system bashrc if exists
if [ -f /etc/bash.bashrc ]; then
    source /etc/bash.bashrc
fi

# Set up exit code tracking
__aish_last_exit_code=0
__AISH_PROTOCOL_VERSION=1
__AISH_CONTROL_FD="${AISH_CONTROL_FD:-}"
__AISH_CUSTOM_PROMPT_ENABLED="${AISH_ENABLE_CUSTOM_PROMPT:-0}"
__AISH_AT_PROMPT=0

__aish_json_escape() {
    local value="$1"
    value=${value//\\/\\\\}
    value=${value//\"/\\\"}
    value=${value//$'\n'/\\n}
    value=${value//$'\r'/\\r}
    value=${value//$'\t'/\\t}
    printf '%s' "$value"
}

__aish_emit_control_line() {
    local payload="$1"
    if [[ ! "$__AISH_CONTROL_FD" =~ ^[0-9]+$ ]]; then
        return 0
    fi

    printf '%s\n' "$payload" >&${__AISH_CONTROL_FD} 2>/dev/null || true
}

__aish_emit_session_ready() {
    local ts cwd_json payload
    ts=$(date +%s)
    cwd_json=$(__aish_json_escape "$PWD")
    printf -v payload \
        '{"version":%s,"type":"session_ready","ts":%s,"shell_pid":%s,"cwd":"%s","shlvl":%s}' \
        "$__AISH_PROTOCOL_VERSION" "$ts" "$$" "$cwd_json" "${SHLVL:-0}"
    __aish_emit_control_line "$payload"
}

__aish_emit_prompt_ready() {
    local exit_code="$1"
    local ts cwd_json interrupted command_seq payload
    ts=$(date +%s)
    cwd_json=$(__aish_json_escape "$PWD")
    interrupted=false
    if [[ "$exit_code" == "130" ]]; then
        interrupted=true
    fi

    command_seq=null
    if [[ -n "${__AISH_ACTIVE_COMMAND_SEQ:-}" ]]; then
        command_seq="${__AISH_ACTIVE_COMMAND_SEQ}"
    fi

    printf -v payload \
        '{"version":%s,"type":"prompt_ready","ts":%s,"command_seq":%s,"exit_code":%s,"cwd":"%s","shlvl":%s,"interrupted":%s}' \
        "$__AISH_PROTOCOL_VERSION" "$ts" "$command_seq" "$exit_code" "$cwd_json" "${SHLVL:-0}" "$interrupted"
    __aish_emit_control_line "$payload"
    unset __AISH_ACTIVE_COMMAND_SEQ
}

__aish_emit_command_started() {
    local command="$1"
    local ts command_json command_seq payload
    ts=$(date +%s)
    command_json=$(__aish_json_escape "$command")

    command_seq=null
    if [[ -n "${__AISH_ACTIVE_COMMAND_SEQ:-}" ]]; then
        command_seq="${__AISH_ACTIVE_COMMAND_SEQ}"
    fi

    printf -v payload \
        '{"version":%s,"type":"command_started","ts":%s,"command_seq":%s,"command":"%s","cwd":"%s","shlvl":%s}' \
        "$__AISH_PROTOCOL_VERSION" "$ts" "$command_seq" "$command_json" "$(__aish_json_escape "$PWD")" "${SHLVL:-0}"
    __aish_emit_control_line "$payload"
}

__aish_emit_shell_exiting() {
    local exit_code="$1"
    local ts payload
    ts=$(date +%s)
    printf -v payload \
        '{"version":%s,"type":"shell_exiting","ts":%s,"exit_code":%s}' \
        "$__AISH_PROTOCOL_VERSION" "$ts" "$exit_code"
    __aish_emit_control_line "$payload"
}

__aish_on_exit() {
    local exit_code=$?
    __aish_emit_shell_exiting "$exit_code"
}

__aish_on_debug() {
    if [[ "${__AISH_AT_PROMPT:-0}" != "1" ]]; then
        return 0
    fi

    case "$BASH_COMMAND" in
        __aish_prompt_command*|__aish_on_debug*|__aish_emit_*|__aish_json_escape*|trap* )
            return 0
            ;;
        __AISH_ACTIVE_COMMAND_SEQ=* )
            return 0
            ;;
    esac

    __AISH_AT_PROMPT=0
    __aish_emit_command_started "$BASH_COMMAND"
    return 0
}

# Colors used by the optional aish prompt.
__AISH_R=$'\033[0m'      # Reset
__AISH_D=$'\033[2m'      # Dim
__AISH_G=$'\033[32m'     # Green
__AISH_Y=$'\033[33m'     # Yellow
__AISH_RD=$'\033[31m'    # Red
__AISH_BL=$'\033[34m'    # Blue
__AISH_M=$'\033[35m'     # Magenta
__AISH_C=$'\033[36m'     # Cyan

# Abbreviate path: ~/nfs/xzx/github/aish -> ~/n/x/g/aish
__aish_abbrev_path() {
    local p="$1"
    [[ "$p" == "$HOME"* ]] && p="~${p#$HOME}"

    local IFS='/' result="" part
    read -ra parts <<< "$p"
    local n=${#parts[@]}

    for ((i=0; i<n; i++)); do
        part="${parts[$i]}"
        [[ -z "$part" ]] && continue
        # Keep ~ and last part, abbreviate middle
        if [[ "$part" == "~" || $i -eq $((n-1)) ]]; then
            result+="$part/"
        else
            result+="${part:0:1}/"
        fi
    done
    echo "${result%/}"
}

# Get git status info
__aish_git_info() {
    if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        return
    fi

    local branch staged=0 modified=0 untracked=0 ahead=0 behind=0
    branch=$(git branch --show-current 2>/dev/null)
    [[ -z "$branch" ]] && branch="HEAD"

    # Get porcelain status
    local line
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        # First char: staged changes
        case "${line:0:1}" in
            M|A|D|R|C) ((staged++)) ;;
        esac
        # Second char: modified/deleted in work tree
        case "${line:1:1}" in
            M|D) ((modified++)) ;;
        esac
        # Untracked
        [[ "$line" == "?? "* ]] && ((untracked++))
    done < <(git status --porcelain 2>/dev/null)

    # Get ahead/behind
    local upstream
    upstream=$(git rev-parse --abbrev-ref '@{upstream}' 2>/dev/null)
    if [[ -n "$upstream" && "$upstream" != "@{upstream}" ]]; then
        ahead=$(git rev-list --count "$upstream"..HEAD 2>/dev/null || echo 0)
        behind=$(git rev-list --count HEAD.."$upstream" 2>/dev/null || echo 0)
    fi

    # Output: branch|staged|modified|untracked|ahead|behind
    echo "$branch|$staged|$modified|$untracked|$ahead|$behind"
}

__aish_generate_prompt() {
    local exit_code="${1:-$?}"
    local p=""

    # Path (abbreviated, blue)
    # Wrap ANSI sequences with \[...\] so readline correctly calculates visible prompt width.
    # Without this, Ctrl+R reverse-i-search leaves ghost text after accepting a result.
    p=":\[${__AISH_BL}\]$(__aish_abbrev_path "$PWD")\[${__AISH_R}\]"

    # Git info
    local git_info
    git_info=$(__aish_git_info)
    if [[ -n "$git_info" ]]; then
        IFS='|' read -r branch staged modified untracked ahead behind <<< "$git_info"

        # Branch color
        if [[ "$branch" == "HEAD" ]]; then
            p+="|\[${__AISH_D}\]$branch\[${__AISH_R}\]"
        else
            p+="|\[${__AISH_M}\]$branch\[${__AISH_R}\]"
        fi

        # Status colors
        if [[ "$staged" != "0" ]]; then
            p+="\[${__AISH_Y}\]●\[${__AISH_R}\] \[${__AISH_Y}\]+$staged\[${__AISH_R}\]"
        elif [[ "$modified" != "0" ]]; then
            p+="\[${__AISH_RD}\]●\[${__AISH_R}\] \[${__AISH_RD}\]~$modified\[${__AISH_R}\]"
        elif [[ "$untracked" != "0" ]]; then
            p+="\[${__AISH_C}\]●\[${__AISH_R}\] \[${__AISH_D}\]?$untracked\[${__AISH_R}\]"
        else
            p+="\[${__AISH_G}\]●\[${__AISH_R}\]"
        fi

        # Ahead/behind
        [[ -n "$ahead" && "$ahead" != "0" ]] && p+=" \[${__AISH_C}\]↑$ahead\[${__AISH_R}\]"
        [[ -n "$behind" && "$behind" != "0" ]] && p+=" \[${__AISH_C}\]↓$behind\[${__AISH_R}\]"
    fi

    # Prompt symbol
    if [[ "$exit_code" != "0" ]]; then
        p+=" \[${__AISH_RD}\]➜➜\[${__AISH_R}\] "
    else
        p+=" \[${__AISH_G}\]➜\[${__AISH_R}\] "
    fi

    PS1="$p"
}

__aish_prompt_command() {
    local exit_code=$?
    __aish_last_exit_code=$exit_code
    # Call original PROMPT_COMMAND if it exists
    if [[ -n "$__AISH_ORIGINAL_PROMPT_COMMAND" ]]; then
        eval "$__AISH_ORIGINAL_PROMPT_COMMAND"
    fi
    if [[ "$__AISH_CUSTOM_PROMPT_ENABLED" == "1" ]]; then
        unset PS1
        __aish_generate_prompt "$exit_code"
    else
        PS1=''
    fi
    __AISH_AT_PROMPT=1
    __aish_emit_prompt_ready "$exit_code"
}

# Save original PROMPT_COMMAND before we override it
__AISH_ORIGINAL_PROMPT_COMMAND="$PROMPT_COMMAND"

# Keep the backend prompt silent by default; only enable the custom aish
# prompt when AISH_ENABLE_CUSTOM_PROMPT=1 is set.
PROMPT_COMMAND='__aish_prompt_command'

trap '__aish_on_exit' EXIT
trap '__aish_on_debug' DEBUG
__aish_emit_session_ready
