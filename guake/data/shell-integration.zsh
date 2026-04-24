#!/bin/zsh
# Guake shell integration for Zsh
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

    # Self-healing: re-register preexec if it was wiped (e.g. by p10k)
    if [[ ${preexec_functions[(Ie)_guake_preexec]} -eq 0 ]]; then
        preexec_functions+=(_guake_preexec)
    fi
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

# Install hooks — append directly to arrays (more robust than add-zsh-hook)
precmd_functions+=(_guake_precmd)
preexec_functions+=(_guake_preexec)

# Watchdog: use DEBUG trap to re-install hooks after p10k deferred init
# The trap fires once, re-installs if needed, then removes itself.
_guake_watchdog() {
    if [[ ${precmd_functions[(Ie)_guake_precmd]} -eq 0 ]]; then
        precmd_functions+=(_guake_precmd)
    fi
    if [[ ${preexec_functions[(Ie)_guake_preexec]} -eq 0 ]]; then
        preexec_functions+=(_guake_preexec)
    fi
    # Keep the trap for a few invocations then remove it
    (( _guake_watchdog_count++ ))
    if (( _guake_watchdog_count > 5 )); then
        trap - DEBUG
    fi
}
_guake_watchdog_count=0
trap '_guake_watchdog' DEBUG

# Emit initial prompt marker
_guake_emit '{"event":"prompt_start"}'
