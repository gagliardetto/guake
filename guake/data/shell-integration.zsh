#!/bin/zsh
# Guake shell integration for Zsh
# Source this in your .zshrc:
#   [ -f /usr/share/guake/shell-integration.zsh ] && source /usr/share/guake/shell-integration.zsh
#
# Compatible with: Powerlevel10k, oh-my-zsh, prezto, vanilla zsh
# Provides: command blocks, inline editor support, exit code tracking

if [[ "$GUAKE_SHELL_INTEGRATION" == "1" ]]; then
    return 0
fi
export GUAKE_SHELL_INTEGRATION=1

# Bail if not running inside Guake (no FIFO path set)
if [[ -z "$GUAKE_BLOCK_FIFO" || ! -p "$GUAKE_BLOCK_FIFO" ]]; then
    return 0
fi

_guake_emit() {
    # Write a JSON event line to the FIFO (non-blocking, ignore errors)
    printf '%s\n' "$1" > "$GUAKE_BLOCK_FIFO" 2>/dev/null
}

_guake_cmd_start_time=""

_guake_precmd() {
    local exit_code=$?
    local now=$EPOCHSECONDS
    [[ -z "$now" ]] && now=$(date +%s)

    if [[ -n "$_guake_cmd_start_time" ]]; then
        local duration=$((now - _guake_cmd_start_time))
        _guake_emit "{\"event\":\"command_end\",\"exit_code\":$exit_code,\"duration\":$duration}"
        _guake_cmd_start_time=""
    fi

    _guake_emit "{\"event\":\"prompt_start\"}"
}

_guake_preexec() {
    _guake_cmd_start_time=$EPOCHSECONDS
    [[ -z "$_guake_cmd_start_time" ]] && _guake_cmd_start_time=$(date +%s)

    # $1 in preexec is the command string
    local cmd="${1:0:500}"
    # Escape double quotes and backslashes for JSON
    cmd="${cmd//\\/\\\\}"
    cmd="${cmd//\"/\\\"}"
    # Replace newlines with \n
    cmd="${cmd//$'\n'/\\n}"
    _guake_emit "{\"event\":\"command_start\",\"command\":\"$cmd\"}"
}

# Install hooks using add-zsh-hook (non-destructive — preserves existing hooks)
autoload -Uz add-zsh-hook
add-zsh-hook precmd  _guake_precmd
add-zsh-hook preexec _guake_preexec

# Emit initial prompt marker
_guake_emit '{"event":"prompt_start"}'
