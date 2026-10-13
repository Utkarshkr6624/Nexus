#!/usr/bin/env bash
# Rebuilds dataset/v2_clean -> v2_final_candidate -> v2_final from qwen_jarvis_phase2 (read-only) and prints the sha256 of the frozen files.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-python}
export PYTHONIOENCODING=utf-8
(cd preflight && $PY patch_js_timing.py >/dev/null && $PY apply_patches.py >/dev/null)
(cd tool_protocol && $PY build_arg_overrides.py >/dev/null && $PY make_protocol_examples.py >/dev/null && $PY convert.py --strict >/dev/null)
$PY scripts/freeze_dataset.py >/dev/null
sha256sum dataset/v2_final/*.jsonl
