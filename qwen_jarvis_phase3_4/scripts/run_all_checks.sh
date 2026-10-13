#!/usr/bin/env bash
# Runs every Phase 3/4 verification and writes a summary to reports/final_checks.txt.  usage: bash scripts/run_all_checks.sh
cd "$(dirname "$0")/.." || exit 1
export PYTHONIOENCODING=utf-8
PY=.venv/Scripts/python.exe
OUT=reports/final_checks.txt
: > "$OUT"
step() { echo "=== $1" | tee -a "$OUT"; }
res()  { echo "$1" | tee -a "$OUT"; }

step "1. dataset validation (v2_final): content checks, duplicates, leakage, code blocks"
P2_SCRATCH=${TMPDIR:-/tmp}/p34s $PY preflight/validate_clean.py dataset/v2_final preflight/validation_final.json > /tmp/_v.txt 2>&1
res "exit=$?  $(grep -E 'HARD FAILURES' /tmp/_v.txt)"

step "2. tool protocol: validator over v2_final with chat-template rendering"
$PY tool_protocol/validate_protocol.py dataset/v2_final --render > /tmp/_p.txt 2>&1
res "exit=$?  $(grep -E '\"records\"|\"violations\"|rendered' /tmp/_p.txt | tr -d '\n ')"

step "3. test suites (pytest / runners)"
for t in tests/test_tokenizer.py tests/test_data_schema.py tests/test_data_render.py tests/test_data_loader.py; do
  res "$t: $($PY $t 2>&1 | grep -E 'passed|FAIL' | tail -1)"
done
res "protocol: $($PY tool_protocol/test_protocol.py 2>&1 | grep -E '^(Ran|OK|FAILED)' | tr '\n' ' ')"
res "pytest (train/checkpoint/hardening/failure-recovery/eval): $($PY -m pytest -q tests -p no:cacheprovider --ignore=tests/test_tokenizer.py --ignore=tests/test_data_schema.py --ignore=tests/test_data_render.py --ignore=tests/test_data_loader.py 2>&1 | tail -1)"

step "4. safe modes of the trainer"
$PY -m pipeline.train.train --config configs/qlora_sft.yaml --validate-config > /tmp/_a.txt 2>&1; res "validate-config exit=$? $(tail -1 /tmp/_a.txt)"
$PY -m pipeline.train.train --config configs/qlora_sft.yaml --dry-run > /tmp/_b.txt 2>&1; res "dry-run exit=$? $(tail -1 /tmp/_b.txt)"
$PY -m pipeline.train.train --config configs/smoke_test.yaml --smoke-test > /tmp/_c.txt 2>&1; res "smoke-test exit=$? $(tail -1 /tmp/_c.txt)"
env -u QWEN_PHASE5_UNLOCK $PY -m pipeline.train.train --config configs/qlora_sft.yaml > /tmp/_d.txt 2>&1; res "production without unlock exit=$? (expected 3) $(head -1 /tmp/_d.txt | cut -c1-90)"

step "5. evaluation self-test (stubs; not model results)"
$PY -m pipeline.eval.cli selftest > /tmp/_e.txt 2>&1; res "selftest exit=$?"; grep -E "oracle|refuse_all|generic_hedge|echo" /tmp/_e.txt | tee -a "$OUT"

step "6. Kaggle notebook validation + gate test"
res "notebook validation: $($PY kaggle/validate_notebook.py --strict 2>&1 | tail -1)"
res "gate test: $($PY kaggle/test_notebook_gate.py 2>&1 | tail -1)"

step "7. security audit"
$PY security/audit.py > /tmp/_s.txt 2>&1; res "exit=$?  $(grep -m1 '"status"' /tmp/_s.txt)"

step "8. reproducibility (each deterministic step run twice)"
PATH="$PWD/.venv/Scripts:$PATH" $PY scripts/check_reproducibility.py 2>&1 | grep -E "IDENTICAL|DIFFERENT|ALL" | tee -a "$OUT"

step "9. git safety (read-only git commands; nothing is written outside the workspace)"
res "tracked modifications: $(git -C .. ls-files -m | wc -l)"
res "git diff bytes: $(git -C .. diff | wc -c)"
res "untracked entries: $(git -C .. status --porcelain | grep -c '^??')  (3 workspaces + the 20 older files)"
git -C .. status --porcelain | grep -v "^?? scripts/\|qwen.txt\|generate_dataset\|trim_and" | tee -a "$OUT"
echo "DONE" | tee -a "$OUT"
