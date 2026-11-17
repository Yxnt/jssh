#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/jssh"
EXAMPLE="$ROOT/config.example.yaml"

if ! command -v python3 >/dev/null; then
  echo "python3 is required" >&2
  exit 1
fi

if ! command -v fzf >/dev/null; then
  if command -v brew >/dev/null; then
    brew install fzf
  else
    echo "install fzf first: brew install fzf" >&2
    exit 1
  fi
fi

if command -v pipx >/dev/null; then
  pipx install --force "$ROOT"
else
  python3 -m pip install --user --upgrade "$ROOT"
fi

mkdir -p "$CONFIG_DIR"
if [[ ! -f "$CONFIG_DIR/config.yaml" ]]; then
  cp "$EXAMPLE" "$CONFIG_DIR/config.yaml"
  chmod 600 "$CONFIG_DIR/config.yaml"
  echo "wrote $CONFIG_DIR/config.yaml"
else
  echo "kept existing $CONFIG_DIR/config.yaml"
fi

echo "next: set JMS_AK and JMS_SK, edit $CONFIG_DIR/config.yaml, then run jssh doctor"
