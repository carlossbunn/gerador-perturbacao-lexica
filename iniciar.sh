#!/usr/bin/env bash
set -eu
BASE="$(dirname "$(readlink -f "$0")")"
exec python3 "$BASE/gerador.py" "$@"
