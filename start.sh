#!/usr/bin/env bash
# start.sh — placed in the root of your project

# 1) Figure out where this script actually lives (resolves symlinks)
SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(dirname "$SCRIPT_PATH")"

# 2) Jump into the project root
cd "$SCRIPT_DIR" || exit 1

# 3) Activate the venv next to it
source "${SCRIPT_DIR}/.venv/bin/activate"

# 4) Delegate to your CLI, forwarding all args
exec python -m main "$@"
