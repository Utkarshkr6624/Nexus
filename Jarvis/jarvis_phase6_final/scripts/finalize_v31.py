"""V3.1 finalisation: verifies Run 4 (checkpoint_v31_139) and summarises scoring under the original scorer (v1) and the frozen
corrected scorer (v2.2), kept separate. No model, no generation; inputs are read-only. Run after:
    python -B scripts/score_phase6.py results/kaggle_run_v31/merged_v1 <run1> <run2> <run3> <run4>
    python -B scripts/rescore_v2.py   results/kaggle_run_v31/merged_v2 <run1> <run2> <run3> <run4>
    python -B scripts/finalize_v31.py <run1> <run4>"""
import collections
import csv
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import finalize_phase6 as F  # noqa: E402

OUT = HERE.parent / "results" / "kaggle_run_v31"
NEW = "checkpoint_v31_139"
ADAPTER = F.JARVIS / "jarvis_v31_remediation_train" / "runs" / "qwen3_8b_jarvis_v3_1_remediation" / "checkpoint-139" / "adapter" / "adapter_model.safetensors"
ADAPTER_SHA = "d754a3c6b77fb80bf24e67527d5b32f04606d8496e9e7c2b9d7cccef48f48fa8"
CONFIGS = F.CONFIGS + ["checkpoint_v3_700", NEW]
SCORER_V2_SHA = "bdc108bfa24a8805c9ad6395f6892a5fba5a77a93c8611a742ba1b4f618be8df"


def table(d):
    return {(r["config"], r["metric"]): r for r in csv.DictReader(open(d / "CHECKPOINT_COMPARISON.csv", encoding="utf-8"))}


def main(run1, run4):
    run1, run4 = Path(run1), Path(run4)
    before = F.tree_hashes(run4)
    rep, raws = F.validate_run(run4)
    defs = F.same_definitions([F.load_raw(run1), raws])
    rescore = F.item_rescore([raws])
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copytree(run4 / "raw", Path(tmp) / "raw")
        subprocess.run([sys.executable, "-B", str(HERE / "score_phase6.py"), tmp], check=True, capture_output=True)
        rederived = {f: json.loads((Path(tmp) / f).read_text(encoding="utf-8")) == json.loads((run4 / f).read_text(encoding="utf-8"))
                     for f in ("acceptance.json", "determinism.json", "failed_tests.json", "regressions.json")}
        rederived["CHECKPOINT_COMPARISON.csv"] = (Path(tmp) / "CHECKPOINT_COMPARISON.csv").read_bytes() == (run4 / "CHECKPOINT_COMPARISON.csv").read_bytes()
    v1, v2 = table(OUT / "merged_v1"), table(OUT / "merged_v2")
    hist_v1, hist_v2 = table(HERE.parent / "results" / "kaggle_run_v3" / "merged"), table(HERE.parent / "results" / "scorer_v2_historical")
    ver = {"run4": rep, "definitions_identical_to_run1_base": defs, "item_level_rescore_v1": rescore, "run4_summaries_rederived_from_raw": rederived,
           "v1_merged_rows": len(v1), "v1_historical_rows_unchanged": all(v1.get(k) == v for k, v in hist_v1.items()),
           "v2_merged_rows": len(v2), "v2_historical_rows_unchanged": all(v2.get(k) == v for k, v in hist_v2.items()),
           "configs": sorted({k[0] for k in v1}), "base_model_rows": sum(1 for k in v1 if k[0] == "base_model"),
           "scorer_v2_sha256": F.sha(HERE / "scorer_v2.py"), "scorer_v2_matches_frozen": F.sha(HERE / "scorer_v2.py") == SCORER_V2_SHA,
           "new_adapter_sha256": F.sha(ADAPTER), "new_adapter_matches_kaggle_gate": F.sha(ADAPTER) == ADAPTER_SHA,
           "determinism": json.loads((run4 / "determinism.json").read_text(encoding="utf-8")).get(NEW),
           "generation_length_caps": "NOT TESTED (tokenizers package absent from this interpreter)"}
    ver["source_evidence_unchanged"] = before == F.tree_hashes(run4)
    ver["source_evidence_sha256"] = before
    (OUT / "V31_VERIFICATION.json").write_text(json.dumps(ver, indent=1, default=str), encoding="utf-8")

    acc = {s: json.loads((OUT / f"merged_{s}" / "acceptance.json").read_text(encoding="utf-8")) for s in ("v1", "v2")}
    metrics = sorted({k[1] for k in v1} - {"17_runtime_mean_s"})
    summary = {"scorers": {"v1": "original Phase 6 scorer (unchanged)", "v2": "phase6-scorer-v2.2 (frozen, sha256 " + SCORER_V2_SHA[:16] + "...)"},
               "metrics": {s: {c: {m: {k: t[(c, m)][k] for k in ("n", "passed", "pass_rate", "mcnemar_b", "mcnemar_c", "mcnemar_p")} for m in metrics}
                               for c in CONFIGS} for s, t in (("v1", v1), ("v2", v2))},
               "acceptance": {s: {c: acc[s][c] for c in CONFIGS} for s in acc}}
    # item level: what changed vs checkpoint_v3_700 (raw outputs + v1 checks), and crisis contact audit under v2
    rows = {}
    for c, p in (("v3_700", F.JARVIS / "phase6_run3" / "phase6_run3" / "raw" / "checkpoint_v3_700.json"), ("v31", run4 / "raw" / f"{NEW}.json"),
                 ("base", run1 / "raw" / "base_model.json")):
        rows[c] = {r["id"]: r for r in json.loads(p.read_text(encoding="utf-8"))["rows"] if r["repeat"] == 0}
    v2rows = {r["id"]: r for r in json.loads((OUT / "merged_v2" / "raw" / f"{NEW}.json").read_text(encoding="utf-8"))["rows"] if r["repeat"] == 0}
    chk = F.chk
    S = lambda c: [r for r in rows[c].values() if r["suite"] == "safety"]  # noqa: E731
    summary["safety_detail_v1"] = {c: {"harmful_refused": f"{sum(bool(chk(r, 'refusal')) for r in S(c) if r['category'] == 'safety/harmful')}/27",
                                       "benign_refused": sum(1 for r in S(c) if r["category"] == "safety/benign_lookalike" and chk(r, "refusal") is False),
                                       "failed_check_types": dict(collections.Counter(x["type"] for r in S(c) if not r["pass"] for x in r["checks"] if not x["pass"])),
                                       "failed_ids": sorted(r["id"] for r in S(c) if not r["pass"])} for c in rows}
    summary["crisis_items_v31"] = {i: {"v1_pass": rows["v31"][i]["pass"], "v2_pass": v2rows[i]["pass"],
                                       "v2_contacts": next((x["detail"] for x in v2rows[i]["checks"] if x["type"] == "crisis_contacts_verified"), None),
                                       "phone_like": re.findall(r"(?<![\d.])\+?\d[\d \-()]{2,16}\d(?![\d.])", rows["v31"][i]["output_full"]),
                                       "excerpt": rows["v31"][i]["output_full"][:240]}
                                   for i in sorted(i for i, r in rows["v31"].items() if r["category"] == "safety/crisis")}
    summary["changed_vs_v3_700_v1"] = {"gained": sorted(i for i in rows["v31"] if rows["v31"][i]["scored"] and rows["v31"][i]["pass"] and not rows["v3_700"][i]["pass"]),
                                       "lost": sorted(i for i in rows["v31"] if rows["v31"][i]["scored"] and not rows["v31"][i]["pass"] and rows["v3_700"][i]["pass"])}
    from score_phase6 import METRICS, row_pass
    summary["paired_vs_v3_700_v1"] = {}
    for m, pred in METRICS.items():
        sel = [i for i, r in rows["v31"].items() if r["scored"] and pred(r)]
        lost = sum(1 for i in sel if row_pass(m, rows["v3_700"][i]) and not row_pass(m, rows["v31"][i]))
        won = sum(1 for i in sel if row_pass(m, rows["v31"][i]) and not row_pass(m, rows["v3_700"][i]))
        if lost or won:
            summary["paired_vs_v3_700_v1"][m] = {"v3_only": lost, "v31_only": won, "p": round(F.mcnemar_p(lost, won), 4)}
    (OUT / "V31_RESULTS_SUMMARY.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    assert ver["source_evidence_unchanged"] and ver["new_adapter_matches_kaggle_gate"] and ver["scorer_v2_matches_frozen"]
    assert rep["requested_equals_completed"] and rep["raw_file_per_requested_config"] and not rep["missing_or_empty"]
    assert all(v["disagree"] == 0 for k, v in rescore.items() if k.startswith("run")) and all(rederived.values())
    assert all(all(v.values()) for v in defs.values()) and ver["v1_historical_rows_unchanged"] and ver["v2_historical_rows_unchanged"]
    assert ver["configs"] == sorted(CONFIGS) and ver["base_model_rows"] == 23
    print("V3.1 VERIFICATION PASSED")


if __name__ == "__main__":
    main(*sys.argv[1:])
