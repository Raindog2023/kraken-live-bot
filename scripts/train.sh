#!/bin/sh
set -eu
if [ "$#" -ne 2 ]; then echo "usage: train <csv> <artifact.joblib>" >&2; exit 2; fi
exec python - "$1" "$2" <<'PY'
import csv, sys
from pathlib import Path
from app.ml.features import FEATURE_COLUMNS
from app.ml.model import build_pipeline, save_artifact, train_walk_forward
csv_path, out_path = sys.argv[1:]
rows=[]; labels=[]
with open(csv_path, newline="") as f:
    for row in csv.DictReader(f):
        rows.append({c: float(row[c]) for c in FEATURE_COLUMNS}); labels.append(int(row["label"]))
if len(rows) < 51: raise SystemExit("need at least 51 chronological rows")
result=train_walk_forward(rows, labels)
model=build_pipeline().fit([[r[c] for c in FEATURE_COLUMNS] for r in rows], labels)
Path(out_path).parent.mkdir(parents=True, exist_ok=True); save_artifact(model, out_path)
print(result)
PY
