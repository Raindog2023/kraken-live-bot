#!/bin/sh
set -eu
if [ "$#" -ne 1 ]; then echo "usage: backtest <csv>" >&2; exit 2; fi
exec python - "$1" <<'PY'
import csv, sys
from app.ml.backtest import backtest
with open(sys.argv[1], newline="") as f:
    rows=list(csv.DictReader(f))
print(backtest([float(r["price"]) for r in rows], [int(r["signal"]) for r in rows]))
PY
