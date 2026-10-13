"""Run the full NEXUS backend suite in sequential batches, one fresh pytest process per batch.

Why: a single 49-minute pytest process was killed by Windows at ~66% with the system low on memory
(memory accumulated in one long-lived process: the ML suites load torch/DeBERTa and nothing is released
until the process exits). Separate processes return memory to the OS after every batch. Batches never
run concurrently: every DB test truncates the same test database.

usage: python run_backend_suite_batched.py [--batches 12] [--out evidence/full_suite_run3_batched.txt]
"""
import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
PY = BACKEND / ".venv" / "Scripts" / "python.exe"
COUNT = re.compile(r"(\d+) (passed|failed|skipped|xfailed|xpassed|errors?|deselected)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batches", type=int, default=12)
    ap.add_argument("--from-batch", type=int, default=1, help="resume: skip batches already recorded as passing")
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "evidence" / "full_suite_run3_batched.txt")
    a = ap.parse_args()
    files = sorted(str(p.relative_to(BACKEND)).replace("\\", "/") for p in (BACKEND / "tests").rglob("test_*.py"))
    size = -(-len(files) // a.batches)
    groups = [files[i : i + size] for i in range(0, len(files), size)]
    totals: dict[str, int] = {}
    worst = 0
    with a.out.open("a" if a.from_batch > 1 else "w", encoding="utf-8") as log:  # resuming never erases earlier batches
        log.write(f"{len(files)} test files in {len(groups)} sequential batches (from batch {a.from_batch})\n")
        for n, group in enumerate(groups, 1):
            if n < a.from_batch:
                continue
            t0 = time.monotonic()
            proc = subprocess.run([str(PY), "-m", "pytest", "-q", "-p", "no:cacheprovider", *group],
                                  cwd=BACKEND, capture_output=True, text=True, encoding="utf-8", errors="replace")
            out = proc.stdout + proc.stderr
            summary = next((ln for ln in reversed(out.splitlines()) if COUNT.search(ln) and " in " in ln), "NO SUMMARY")
            for num, kind in COUNT.findall(summary):
                kind = "errors" if kind.startswith("error") else kind
                totals[kind] = totals.get(kind, 0) + int(num)
            worst = max(worst, proc.returncode)
            log.write(f"\n=== batch {n}/{len(groups)} exit={proc.returncode} {time.monotonic() - t0:.0f}s\n")
            log.write("files: " + " ".join(group) + "\n" + summary + "\n")
            # -ra short summary: why each test failed, errored or was skipped
            log.write("\n".join(ln for ln in out.splitlines() if ln.startswith(("FAILED", "ERROR", "SKIPPED"))) + "\n")
            log.flush()
            print(f"batch {n}/{len(groups)} exit={proc.returncode}: {summary}", flush=True)
        log.write(f"\nTOTAL {totals} worst_exit={worst}\n")
    print(f"TOTAL {totals} worst_exit={worst}")
    return worst


if __name__ == "__main__":
    sys.exit(main())
