"""Phase 6 finalisation: verifies the two downloaded Kaggle runs and summarises the merged scoring. No model, no generation, read-only on inputs.
Usage (from jarvis_phase6_final, with the package venv python):
    python -B scripts/score_phase6.py results/kaggle_run/merged <run1> <run2>      # existing merged scoring (run first)
    python -B scripts/finalize_phase6.py <run1> <run2>
Writes results/kaggle_run/PHASE6_VERIFICATION.json and results/kaggle_run/PHASE6_RESULTS_SUMMARY.json."""
import csv
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from math import comb
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JARVIS = ROOT.parent
CODE = JARVIS / "_resume_test" / "code"
MERGED = ROOT / "results" / "kaggle_run" / "merged"
TOKENIZER = Path(os.path.expanduser(r"~\.cache\huggingface\hub\models--Qwen--Qwen3-8B\snapshots\b968826d9c46dd6066d109eabc6255188de91218\tokenizer.json"))
CONFIGS = ["base_model"] + [f"checkpoint_{s}" for s in (800, 1100, 1200, 1300, 1400, 1500, 1596)]
REQUIRED = ["CHECKPOINT_COMPARISON.csv", "acceptance.json", "determinism.json", "env.json", "failed_tests.json", "launcher.log", "regressions.json",
            "run_status.json", "runner.log"]
MAX_NEW_TOKENS = 384


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def tree_hashes(d):
    return {str(Path(p).relative_to(JARVIS)): sha(p) for p in sorted(glob.glob(str(Path(d) / "**" / "*"), recursive=True)) if Path(p).is_file()}


def load_raw(run):
    return {Path(p).stem: json.loads(Path(p).read_text(encoding="utf-8")) for p in sorted(glob.glob(str(Path(run) / "raw" / "*.json")))}


def mcnemar_p(b, c):
    n = b + c
    return 1.0 if n == 0 else min(1.0, 2 * sum(comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n)


def validate_run(run):
    run = Path(run)
    rep = {"dir": str(run), "missing_or_empty": [f for f in REQUIRED if not (run / f).is_file() or (run / f).stat().st_size == 0]}
    for f in REQUIRED:
        if f.endswith(".json"):
            json.loads((run / f).read_text(encoding="utf-8"))           # raises on malformed JSON
    rep["csv_rows"] = len(list(csv.DictReader(open(run / "CHECKPOINT_COMPARISON.csv", encoding="utf-8"))))
    st = json.loads((run / "run_status.json").read_text(encoding="utf-8"))
    raws = load_raw(run)
    rep["requested"], rep["completed"], rep["raw_files"] = st["configs_requested"], st["completed"], sorted(raws)
    rep["requested_equals_completed"] = st["configs_requested"] == st["completed"] and not st["not_started_time_budget"]
    rep["raw_file_per_requested_config"] = sorted(st["configs_requested"]) == sorted(raws)
    rep["elapsed_h"], rep["package_gate"] = st["elapsed_h"], st["package_gate"]["status"]
    av = st["adapter_verification"]
    rep["adapter_hash_gate_all_pass"] = all(v["status"] == "PASS" for v in av["adapter_hash_gate"].values())
    rep["base_logit_reference_pass"] = av["base_model"]["pass"]
    rep["adapters_pairwise_distinct"] = av["adapters_pairwise_distinct"]
    rep["configs"] = {}
    for c, d in raws.items():
        rows = d["rows"]
        keys = [(r["id"], r["repeat"]) for r in rows]
        rep["configs"][c] = {"n_rows": len(rows), "n_rows_field": d["n_rows"], "complete": d["complete"], "unique_items": sum(r["repeat"] == 0 for r in rows),
                             "repeats": sum(r["repeat"] == 1 for r in rows), "duplicates": len(keys) - len(set(keys)),
                             "gen_errors": sum(1 for r in rows if r["gen_error"]), "empty_outputs": sum(1 for r in rows if not r["output_full"].strip()),
                             "scored_items": sum(1 for r in rows if r["repeat"] == 0 and r["scored"]),
                             "suites": {s: sum(1 for r in rows if r["repeat"] == 0 and r["suite"] == s) for s in d["settings"]["suites"]},
                             "settings": d["settings"], "decoding": d["describe"]["decoding"]}
    return rep, raws


def same_definitions(raws_by_run):
    ref = raws_by_run[0]["base_model"]
    refk = {(r["id"], r["repeat"]): r for r in ref["rows"]}
    out = {}
    for i, raws in enumerate(raws_by_run, 1):
        for c, d in raws.items():
            k = {(r["id"], r["repeat"]): r for r in d["rows"]}
            out[f"run{i}/{c}"] = {
                "same_item_keys": set(k) == set(refk),
                "same_prompts": all(json.dumps(k[x]["prompt_messages"], sort_keys=True) == json.dumps(refk[x]["prompt_messages"], sort_keys=True) for x in refk if x in k),
                "same_category_and_scored": all(k[x]["category"] == refk[x]["category"] and k[x]["scored"] == refk[x]["scored"] for x in refk if x in k),
                "same_settings": d["settings"] == ref["settings"], "same_decoding": d["describe"]["decoding"] == ref["describe"]["decoding"]}
    return out


def base_cross_run(a, b):
    ka = {(r["id"], r["repeat"]): r for r in a["rows"]}
    kb = {(r["id"], r["repeat"]): r for r in b["rows"]}
    common = set(ka) & set(kb)
    return {"items_compared": len(common), "identical_outputs": sum(ka[x]["output_full"] == kb[x]["output_full"] for x in common),
            "identical_checks": sum(ka[x]["checks"] == kb[x]["checks"] for x in common), "pass_disagreements": sorted(x[0] for x in common if ka[x]["pass"] != kb[x]["pass"]),
            "runtime_run1": a["runtime"], "runtime_run2": b["runtime"],
            "used_in_merged_scoring": "run 1 copy (score_phase6.py takes the first run); run 2 copy kept as replication evidence, not double-counted"}


def item_rescore(raws_by_run):
    """Re-scores every recorded output with the ORIGINAL scorer + phase6_checks; python_exec is static-only locally (not re-executed)."""
    os.environ["PHASE6_NO_EXEC"] = "1"
    sys.path.insert(0, str(HERE))
    import trusted_hashes as TH
    gate = TH.verify_package(str(CODE))
    sys.path.insert(0, str(CODE))
    import phase6_checks  # noqa: F401
    from pipeline.eval.runner import load_suite, resolve_tools, tool_defs
    from pipeline.eval.scorers import score_item
    defs, items = tool_defs(), {}
    for s in ("instruction_following", "multi_turn", "nexus_facts", "tool_awareness", "capability_boundaries", "safety", "general", "testsplit"):
        items.update({it["id"]: it for it in load_suite(s)})
    for p in glob.glob(str(ROOT / "prompts" / "*.jsonl")):
        items.update({json.loads(l)["id"]: json.loads(l) for l in open(p, encoding="utf-8") if l.strip()})
    out = {"local_package_gate": gate, "note": "python_exec checks were executed on Kaggle only; locally they are compared by every other check type"}
    for i, raws in enumerate(raws_by_run, 1):
        for c, d in raws.items():
            agree, dis, prompt_mismatch = 0, [], 0
            for r in d["rows"]:
                it = items[r["id"]]
                prompt_mismatch += json.dumps(it["messages"], sort_keys=True) != json.dumps(r["prompt_messages"], sort_keys=True)
                s = score_item(it, r["output_full"], resolve_tools(it, defs))
                a = [(x["type"], x["pass"]) for x in r["checks"] if x["type"] != "python_exec"]
                b = [(x["type"], x["pass"]) for x in s["checks"] if x["type"] != "python_exec"]
                has_exec = any(x["type"] == "python_exec" for x in r["checks"])
                if a == b and s["scored"] == r["scored"] and (has_exec or s["pass"] == r["pass"]):
                    agree += 1
                else:
                    dis.append(r["id"])
            out[f"run{i}/{c}"] = {"rows": len(d["rows"]), "agree": agree, "disagree": len(dis), "disagreeing_ids": dis[:20],
                                  "prompt_mismatch_vs_item_definition": prompt_mismatch}
    return out


def rescore_runs(runs):
    """Re-runs score_phase6.py on a copy of each run's raw/ and compares with the downloaded summaries (parsed JSON, byte-equal CSV)."""
    out = {}
    for i, run in enumerate(runs, 1):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree(Path(run) / "raw", Path(tmp) / "raw")
            subprocess.run([sys.executable, "-B", str(HERE / "score_phase6.py"), tmp], check=True, capture_output=True)
            res = {"CHECKPOINT_COMPARISON.csv": (Path(tmp) / "CHECKPOINT_COMPARISON.csv").read_bytes() == (Path(run) / "CHECKPOINT_COMPARISON.csv").read_bytes()}
            for f in ("acceptance.json", "determinism.json", "failed_tests.json", "regressions.json"):
                res[f] = json.loads((Path(tmp) / f).read_text(encoding="utf-8")) == json.loads((Path(run) / f).read_text(encoding="utf-8"))
            out[f"run{i}"] = res
    return out


def merged_vs_runs(runs):
    m = {(r["config"], r["metric"]): r for r in csv.DictReader(open(MERGED / "CHECKPOINT_COMPARISON.csv", encoding="utf-8"))}
    out = {"merged_rows": len(m), "configs": sorted({k[0] for k in m}), "base_model_rows": sum(1 for k in m if k[0] == "base_model")}
    for i, run in enumerate(runs, 1):
        rows = list(csv.DictReader(open(Path(run) / "CHECKPOINT_COMPARISON.csv", encoding="utf-8")))
        out[f"run{i}_rows_differing_from_merged"] = [f"{r['config']}/{r['metric']}" for r in rows if m.get((r["config"], r["metric"])) != r]
    return out


def token_caps(raws_by_run):
    if not TOKENIZER.exists():
        return {"status": "NOT TESTED: local Qwen3-8B tokenizer snapshot missing"}
    from tokenizers import Tokenizer
    tok = Tokenizer.from_file(str(TOKENIZER))
    out = {"max_new_tokens": MAX_NEW_TOKENS, "tokenizer": str(TOKENIZER.parent.name)}
    for i, raws in enumerate(raws_by_run, 1):
        for c, d in raws.items():
            n = [len(tok.encode(r["output_full"], add_special_tokens=False).ids) for r in d["rows"]]
            capped = [r for r, k in zip(d["rows"], n) if k >= MAX_NEW_TOKENS - 1]
            out[f"run{i}/{c}"] = {"max_output_tokens": max(n), "rows_at_cap": len(capped),
                                  "scored_items_at_cap_failed": sorted(r["id"] for r in capped if r["repeat"] == 0 and r["scored"] and not r["pass"])}
    return out


def chk(r, t):
    v = [x["pass"] for x in r["checks"] if x["type"] == t]
    return all(v) if v else None


def summary(raws):
    rows = {c: {r["id"]: r for r in raws[c]["rows"] if r["repeat"] == 0} for c in CONFIGS}
    m = {}
    for r in csv.DictReader(open(MERGED / "CHECKPOINT_COMPARISON.csv", encoding="utf-8")):
        m.setdefault(r["config"], {})[r["metric"]] = {k: r[k] for k in ("n", "passed", "failed", "pass_rate", "wilson_lo", "wilson_hi", "delta_vs_base",
                                                                         "mcnemar_b", "mcnemar_c", "mcnemar_p")}
    acc = json.loads((MERGED / "acceptance.json").read_text(encoding="utf-8"))
    reg = json.loads((MERGED / "regressions.json").read_text(encoding="utf-8"))
    sens = {}
    for c in CONFIGS:
        h = [r for r in rows[c].values() if r["category"] == "safety/harmful"]
        dbg = rows[c]["phase6_coding-debug-001"]
        p11 = [r for r in rows[c].values() if r["category"] == "phase6_format/p11_json"]
        sens[c] = {"harmful_requests_refused (refusal check only)": f"{sum(bool(chk(r, 'refusal')) for r in h)}/{len(h)}",
                   "harmful_failed_only_on_wording_checks (topical/offers_alternative)": sum(1 for r in h if not r["pass"] and chk(r, "refusal")),
                   "benign_lookalike_refused (refusal check failed)": sum(1 for r in rows[c].values() if r["category"] == "safety/benign_lookalike" and chk(r, "refusal") is False),
                   "can_do_needless_self_limitation": sum(1 for r in rows[c].values() if r["category"] == "capability_boundaries/can_do" and chk(r, "no_self_limitation") is False),
                   "p11_outputs_with_code_fence_or_backticks": sum(1 for r in p11 if "`" in r["output_full"]),
                   "p11_outputs_claiming_inability": sum(1 for r in p11 if chk(r, "harmless_complied") is False),
                   "coding_debug_001": {"python_exec": chk(dbg, "python_exec"), "not_an_echo": chk(dbg, "not_an_echo"), "pass": dbg["pass"]}}
    pair = {}
    for a, b in (("checkpoint_1100", "checkpoint_1300"), ("checkpoint_1300", "checkpoint_1596"), ("checkpoint_1100", "checkpoint_1596")):
        pair[f"{a}_vs_{b}"] = {}
        for metric, pred in (("12_tool_selection", lambda r: r["category"].startswith("tool_awareness/") and r["category"].split("/")[1] in (
                "read_call", "write_preview", "not_needed", "no_tools_attached", "tool_missing", "ambiguous", "write_after_approval")),
                             ("01_objective_correctness", lambda r: r["category"] in ("general/math", "general/qa") or r["suite"] in ("phase6_reasoning", "phase6_coding")),
                             ("09_capability_honesty", lambda r: r["suite"] == "capability_boundaries"), ("10_safety", lambda r: r["suite"] == "safety")):
            ids = [i for i, r in rows[a].items() if r["scored"] and pred(r)]
            x = sum(1 for i in ids if rows[a][i]["pass"] and not rows[b][i]["pass"])
            y = sum(1 for i in ids if rows[b][i]["pass"] and not rows[a][i]["pass"])
            pair[f"{a}_vs_{b}"][metric] = {"n": len(ids), "a_only_pass": x, "b_only_pass": y, "mcnemar_p": round(mcnemar_p(x, y), 4)}
    ck = CONFIGS[1:]
    n_reg = {c: len(reg.get(c, [])) for c in ck}
    n_sig = {c: len(acc[c]["significant_regressions"]) for c in ck}
    step2 = [c for c in ck if n_reg[c] == min(n_reg.values()) and n_sig[c] == min(n_sig[x] for x in ck if n_reg[x] == min(n_reg.values()))]
    ts = {c: int(m[c]["12_tool_selection"]["passed"]) for c in step2}
    fr = {c: int(m[c]["15_failure_recovery"]["passed"]) for c in step2}
    step3 = [c for c in step2 if all(ts[c] >= ts[o] and fr[c] >= fr[o] for o in step2)]
    sel = {"step1_configs_passing_all_critical_criteria": [c for c in CONFIGS if acc[c]["all_critical_pass"]],
           "rubric_outcome": "no selected checkpoint (rubric: if no checkpoint passes every critical criterion there is only a best available candidate for further development)",
           "best_available_candidate_procedure": "rubric steps 2-5 applied to all checkpoints, since none passes step 1",
           "step2_regression_counts": {c: {"metrics_with_more_items_lost_than_gained": n_reg[c], "significant": n_sig[c]} for c in ck}, "step2_kept": step2,
           "step3_tool_selection_passed": ts, "step3_failure_recovery_passed": fr, "step3_dominant": step3,
           "step4_objective_correctness_passed": {c: int(m[c]["01_objective_correctness"]["passed"]) for c in step2},
           "pairwise_between_finalists": pair}
    return {"metrics": m, "acceptance": acc, "regressions_metric_counts": n_reg, "sensitivity_item_level": sens, "selection": sel}


def main(run1, run2):
    runs = [Path(run1), Path(run2)]
    before = {**tree_hashes(run1), **tree_hashes(run2)}
    ckpt_before = {str(Path(p).relative_to(JARVIS)): sha(p) for p in sorted(glob.glob(str(JARVIS / "checkpoint-*" / "adapter" / "*")))}
    reps, raws_by_run = zip(*(validate_run(r) for r in runs))
    raws = dict(raws_by_run[0])
    for c, d in raws_by_run[1].items():
        raws.setdefault(c, d)
    ver = {"runs": list(reps), "evaluation_definitions_identical": same_definitions(raws_by_run),
           "base_model_cross_run": base_cross_run(raws_by_run[0]["base_model"], raws_by_run[1]["base_model"]),
           "item_level_rescore_vs_recorded_checks": item_rescore(raws_by_run), "per_run_summaries_rederived_from_raw": rescore_runs(runs),
           "merged_csv_vs_run_csvs": merged_vs_runs(runs), "generation_length_caps": token_caps(raws_by_run),
           "all_eight_configs_present": sorted(raws) == sorted(CONFIGS)}
    after = {**tree_hashes(run1), **tree_hashes(run2)}
    ckpt_after = {str(Path(p).relative_to(JARVIS)): sha(p) for p in sorted(glob.glob(str(JARVIS / "checkpoint-*" / "adapter" / "*")))}
    ver["source_evidence_sha256"] = before
    ver["source_evidence_unchanged_during_finalisation"] = before == after
    ver["local_adapter_files_sha256"] = ckpt_before
    ver["local_adapters_unchanged_during_finalisation"] = ckpt_before == ckpt_after
    gate = {c: v["sha256"]["adapter_model.safetensors"] for c, v in
            {k: v["candidates"][0] for k, v in json.loads((runs[0] / "run_status.json").read_text())["adapter_verification"]["adapter_hash_gate"].items()}.items()}
    ver["local_adapters_match_kaggle_gate_hashes"] = {c: ckpt_before.get(f"checkpoint-{c.split('_')[1]}\\adapter\\adapter_model.safetensors") == h for c, h in gate.items()}
    out = ROOT / "results" / "kaggle_run"
    (out / "PHASE6_VERIFICATION.json").write_text(json.dumps(ver, indent=1, default=str), encoding="utf-8")
    (out / "PHASE6_RESULTS_SUMMARY.json").write_text(json.dumps(summary(raws), indent=1, default=str), encoding="utf-8")
    # self-check: every config, every run, every gate must hold for the evidence to be usable
    assert ver["all_eight_configs_present"] and ver["source_evidence_unchanged_during_finalisation"] and ver["local_adapters_unchanged_during_finalisation"]
    assert all(r["requested_equals_completed"] and r["raw_file_per_requested_config"] and not r["missing_or_empty"] for r in reps)
    assert all(v["disagree"] == 0 for k, v in ver["item_level_rescore_vs_recorded_checks"].items() if k.startswith("run"))
    assert all(all(v.values()) for v in ver["per_run_summaries_rederived_from_raw"].values())
    assert all(all(v.values()) for v in ver["evaluation_definitions_identical"].values()) and all(ver["local_adapters_match_kaggle_gate_hashes"].values())
    print("VERIFICATION PASSED")


if __name__ == "__main__":
    main(*sys.argv[1:])
