#!/bin/bash
# Guake shell integration for Bash
# Source this in your .bashrc:
#   [ -f /usr/share/guake/shell-integration.bash ] && source /usr/share/guake/shell-integration.bash
#
# Provides: command blocks, inline editor support, exit code tracking

if [[ "$GUAKE_SHELL_INTEGRATION" == "1" ]]; then
    return 0 2>/dev/null || exit 0
fi
export GUAKE_SHELL_INTEGRATION=1

# Bail if not running inside Guake (no FIFO path set)
if [[ -z "$GUAKE_BLOCK_FIFO" || ! -p "$GUAKE_BLOCK_FIFO" ]]; then
    return 0 2>/dev/null || exit 0
fi

_guake_emit() {
    # Write a JSON event line to the FIFO (non-blocking, ignore errors)
    printf '%s\n' "$1" > "$GUAKE_BLOCK_FIFO" 2>/dev/null
}

_guake_cmd_start_time=""

_guake_prompt_command() {
    local exit_code=$?
    local now
    now=$(date +%s)

    # If we have a start time, this is a command completion
    if [[ -n "$_guake_cmd_start_time" ]]; then
        local duration=$((now - _guake_cmd_start_time))
        _guake_emit "{\"event\":\"command_end\",\"exit_code\":$exit_code,\"duration\":$duration}"
        _guake_cmd_start_time=""
    fi

    _guake_emit "{\"event\":\"prompt_start\"}"
}

_guake_preexec() {
    # Avoid firing for PROMPT_COMMAND itself
    if [[ "$BASH_COMMAND" == "_guake_prompt_command" ]]; then
        return
    fi
    _guake_cmd_start_time=$(date +%s)
    _guake_emit "{\"event\":\"command_start\",\"command\":\"$(printf '%s' "$BASH_COMMAND" | head -c 500 | sed 's/"/\\"/g')\"}"
}

# Install hooks (non-destructive — preserves existing PROMPT_COMMAND)
PROMPT_COMMAND="_guake_prompt_command${PROMPT_COMMAND:+;$PROMPT_COMMAND}"
trap '_guake_preexec' DEBUG

# Emit initial prompt marker
_guake_emit '{"event":"prompt_start"}'
