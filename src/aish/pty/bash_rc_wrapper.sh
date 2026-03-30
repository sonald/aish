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

# IMPORTANT: Override any PS1/PROMPT_COMMAND from user's .bashrc
# This ensures aish prompt is always used
unset PS1

# Set up exit code tracking
__aish_last_exit_code=0

# Colors
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
    local exit_code=$?
    __aish_last_exit_code=$exit_code
    printf "[AISH_EXIT:%s]" "$exit_code"

    local p=""

    # Path (abbreviated, blue)
    p=":${__AISH_BL}$(__aish_abbrev_path "$PWD")${__AISH_R}"

    # Git info
    local git_info
    git_info=$(__aish_git_info)
    if [[ -n "$git_info" ]]; then
        IFS='|' read -r branch staged modified untracked ahead behind <<< "$git_info"

        # Branch color
        if [[ "$branch" == "HEAD" ]]; then
            p+="|${__AISH_D}$branch${__AISH_R}"
        else
            p+="|${__AISH_M}$branch${__AISH_R}"
        fi

        # Status colors
        if [[ "$staged" != "0" ]]; then
            p+="${__AISH_Y}●${__AISH_R} ${__AISH_Y}+$staged${__AISH_R}"
        elif [[ "$modified" != "0" ]]; then
            p+="${__AISH_RD}●${__AISH_R} ${__AISH_RD}~$modified${__AISH_R}"
        elif [[ "$untracked" != "0" ]]; then
            p+="${__AISH_C}●${__AISH_R} ${__AISH_D}?$untracked${__AISH_R}"
        else
            p+="${__AISH_G}●${__AISH_R}"
        fi

        # Ahead/behind
        [[ -n "$ahead" && "$ahead" != "0" ]] && p+=" ${__AISH_C}↑$ahead${__AISH_R}"
        [[ -n "$behind" && "$behind" != "0" ]] && p+=" ${__AISH_C}↓$behind${__AISH_R}"
    fi

    # Prompt symbol
    if [[ "$exit_code" != "0" ]]; then
        p+=" ${__AISH_RD}➜➜${__AISH_R} "
    else
        p+=" ${__AISH_G}➜${__AISH_R} "
    fi

    PS1="$p"
}

# Use a function to avoid issues with PROMPT_COMMAND chaining
__aish_prompt_command() {
    __aish_generate_prompt
    # Call original PROMPT_COMMAND if it exists
    if [[ -n "$__AISH_ORIGINAL_PROMPT_COMMAND" ]]; then
        eval "$__AISH_ORIGINAL_PROMPT_COMMAND"
    fi
}

# Save original PROMPT_COMMAND before we override it
__AISH_ORIGINAL_PROMPT_COMMAND="$PROMPT_COMMAND"

# Force aish prompt
PROMPT_COMMAND='__aish_prompt_command'
