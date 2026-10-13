#!/usr/bin/env bash
# Rebuilds everything derived from dataset/v2_final: token statistics, evaluation suites, memory plan, notebook and Kaggle package.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
PY=.venv/Scripts/python.exe
for s in train validation test; do
  $PY -m pipeline.data.cli stats --split $s --data-dir dataset/v2_final --mode strict --max-len 1024 --out-dir reports --tag final > /dev/null 2>&1
  cp reports/data_stats_${s}_final.json reports/data_stats_$s.json
  cp reports/data_stats_${s}_final.md reports/data_stats_$s.md
done
$PY -m pipeline.eval.build_suites > /dev/null
(cd kaggle && ../$PY memory_calc.py > /dev/null && ../$PY notebook/build_notebook.py > /dev/null && ../$PY build_package.py)
