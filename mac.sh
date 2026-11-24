#!/bin/bash
# Compatible with macOS /bin/bash 3.2; no installs or shell configuration changes.
set -u

project_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P) || exit 1
seen=('')

try_python() {
    local candidate="$1" previous details
    shift
    [ -f "$candidate" ] && [ -x "$candidate" ] || return 1
    for previous in "${seen[@]}"; do
        [ "$previous" != "$candidate" ] || return 1
    done
    seen+=("$candidate")
    # Avoid triggering a developer-tools installation prompt from Apple's stub.
    if [ "$candidate" = /usr/bin/python3 ] && ! /usr/bin/xcode-select -p >/dev/null 2>&1; then
        printf '跳过 %s：系统开发工具尚不可用。\n' "$candidate" >&2
        return 1
    fi
    if ! details=$("$candidate" -B "$project_dir/cli.py" --probe-python "${1-}" 2>&1); then
        last_failure="$candidate: $details"
        return 1
    fi
    printf '使用解释器：%s\n%s\n' "$candidate" "$details" >&2
    export CODEX_MIGRATION_PROBED=1
    exec "$candidate" -B "$project_dir/cli.py" "$@"
}

last_failure=''
if [ -n "${CODEX_MIGRATION_PYTHON-}" ]; then
    last_failure='该路径不是可执行的 Python 文件。'
    try_python "$CODEX_MIGRATION_PYTHON" "$@"
    printf '指定的 Python 无法运行或不符合要求：%s\n%s\n' "$CODEX_MIGRATION_PYTHON" "$last_failure" >&2
    exit 1
fi

# Prefer an existing app runtime, then common user installs, then PATH. No downloads.
for candidate in \
    "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3" \
    "${VIRTUAL_ENV:-/nonexistent}/bin/python3" "${CONDA_PREFIX:-/nonexistent}/bin/python3" \
    /opt/homebrew/bin/python3 /usr/local/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/*/bin/python3 \
    /opt/homebrew/opt/python@3.*/bin/python3.* /usr/local/opt/python@3.*/bin/python3.* \
    "${PYENV_ROOT:-$HOME/.pyenv}"/versions/*/bin/python3 \
    "${UV_PYTHON_INSTALL_DIR:-$HOME/.local/share/uv/python}"/*/bin/python3; do
    case "$candidate" in *-config) continue ;; esac
    try_python "$candidate" "$@"
done
IFS=: read -r -a search_dirs <<< "$PATH"
for dir in "${search_dirs[@]}"; do
    case "$dir" in /*) ;; *) continue ;; esac
    for candidate in "$dir/python3" "$dir"/python3.[0-9] "$dir"/python3.[0-9][0-9]; do
        try_python "$candidate" "$@"
    done
done
printf '没有找到满足当前命令要求的 Python。\n%s\n' "$last_failure" >&2
exit 1
