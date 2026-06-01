#!/usr/bin/env sh
set -eu

BOARD_HOME="${CODEX_BOARD_HOME:-$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)}"
COMMAND_DIR="${CODEX_DASH_BIN:-$HOME/.local/bin}"
SCRIPT="$BOARD_HOME/codex_board.py"

if [ ! -f "$SCRIPT" ]; then
  echo "codex_board.py not found in $BOARD_HOME" >&2
  exit 1
fi

if command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON="python"
else
  echo "python3/python was not found on PATH" >&2
  exit 1
fi

mkdir -p "$COMMAND_DIR"
cat > "$COMMAND_DIR/codex-dash" <<EOF
#!/usr/bin/env sh
exec $PYTHON "$SCRIPT" "\$@"
EOF
chmod +x "$COMMAND_DIR/codex-dash"

echo "Installed codex-dash:"
echo "  $COMMAND_DIR/codex-dash"
echo
echo "Make sure this is on PATH:"
echo "  $COMMAND_DIR"
echo
echo "Try:"
echo "  codex-dash"
echo "  codex-dash keys"
