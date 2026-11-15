#!/bin/sh
# Collect + render in one step: wrapped.sh [year] [extra render.py args...]
set -e
here=$(cd "$(dirname "$0")" && pwd)
year=${1:-$(date +%Y)}
[ $# -gt 0 ] && shift
out="$HOME/t3-wrapped/$year"
python3 "$here/collect.py" --year "$year" --out "$out/wrapped.json" ${T3_WRAPPED_COLLECT_ARGS:-}
python3 "$here/render.py" "$out" "$@"
