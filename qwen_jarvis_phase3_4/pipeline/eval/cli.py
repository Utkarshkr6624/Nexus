"""Command line: python -m pipeline.eval.cli {build-suites,run,compare,loss,selftest} ...  (see README.md)

Safety lock: a real model is loaded only with --allow-model-load. Stubs never load anything. Nothing here is run in Phase 3/4 against Qwen."""
import argparse
import json
import sys
from pathlib import Path

from . import generators, report, runner
from .build_suites import DATA
from .runner import SUITES


def _parse_suites(s):
    if s in ("all", "", None):
        return list(SUITES)
    names = [x.strip() for x in s.split(",") if x.strip()]
    bad = [n for n in names if n not in SUITES]
    if bad:
        raise SystemExit(f"unknown suite(s) {bad}; choose from {SUITES}")
    return names


def cmd_run(a):
    suites = _parse_suites(a.suite)
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    defs = runner.tool_defs(a.tool_schemas)
    items = []
    for s in suites:
        items += runner.subsample(runner.load_suite(s), a.max_items, a.seed)
    if a.stub:
        if a.stub == "oracle":
            gen = generators.OracleGenerator(items)
        elif a.stub in generators.STUBS:
            gen = generators.STUBS[a.stub]()
        else:
            raise SystemExit(f"unknown stub {a.stub!r}; choose oracle or one of {sorted(generators.STUBS)}")
    else:
        if not a.model:
            raise SystemExit("give --model (HF id or local path) or --stub")
        if not a.allow_model_load:
            raise SystemExit("refusing to load a model: pass --allow-model-load (this is a Phase 5 action; Phase 3/4 only uses stubs)")
        gen = generators.HFGenerator(a.model, a.adapter, load_in_4bit=not a.no_4bit, max_new_tokens=a.max_new_tokens)
    results = runner.run_items(gen, items, defs)
    meta = runner.build_meta(gen, suites=suites, seed=a.seed, max_items=a.max_items, dataset_version=a.dataset_version or runner.default_dataset_version(), manifest=manifest,
                             extra={"tag": a.tag} if a.tag else None)
    rep = report.make_report(meta, results)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "eval_results.json").write_text(json.dumps({"report": rep, "items": results}, indent=1, ensure_ascii=False), encoding="utf-8")
    (out / "eval_report.md").write_text(report.to_markdown(rep), encoding="utf-8")
    mt = rep["metrics"]
    print(("[STUB — NOT A MODEL RESULT] " if rep.get("disclaimer") else "") + f"overall {mt['overall']['pass']}/{mt['overall']['n']} = {mt['overall']['rate']}")
    for s, r in mt["suites"].items():
        print(f"  {s:24s} {r['pass']:4d}/{r['n']:<4d} {r['rate']}")
    print("wrote", out / "eval_results.json", "and eval_report.md")
    return rep


def cmd_compare(a):
    A = json.loads(Path(a.a).read_text(encoding="utf-8"))
    B = json.loads(Path(a.b).read_text(encoding="utf-8"))
    cmp = report.compare(A["report"], B["report"], A["items"], B["items"], a.label_a, a.label_b)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "comparison.json").write_text(json.dumps(cmp, indent=1), encoding="utf-8")
    (out / "comparison.md").write_text(report.compare_markdown(cmp, A["report"], B["report"]), encoding="utf-8")
    print(f"compared {cmp['n_common']} common scored items; overall delta {cmp['rows']['overall']['delta']}; wrote {out / 'comparison.md'}")
    return cmp


def cmd_loss(a):
    if not a.allow_model_load:
        raise SystemExit("refusing to load a model: pass --allow-model-load (Phase 5 action)")
    from . import losseval
    gen = generators.HFGenerator(a.model, a.adapter, load_in_4bit=not a.no_4bit)
    summ = losseval.evaluate_split(gen.model, gen.tok, a.split, max_len=a.max_len, batch_size=a.batch_size, device=str(gen.model.device), limit=a.limit, bootstrap=a.bootstrap)
    Path(a.out).mkdir(parents=True, exist_ok=True)
    (Path(a.out) / "loss_results.json").write_text(json.dumps(summ, indent=1), encoding="utf-8")
    print("overall mean loss", summ["overall"]["mean_loss"], "perplexity", summ["overall"]["perplexity"])


MAX_STUB_OVERALL = 0.05       # every adversarial stub must score at most 5% overall ...
MAX_STUB_SUITE = 0.10         # ... and at most 10% on every single suite


def selftest_rows(items=None):
    """Runs the oracle and every adversarial stub through all suites. Returns (rows, violations). rows: (name, overall_rate, pass, n, {suite: rate})."""
    defs = runner.tool_defs()
    if items is None:
        items = []
        for s in SUITES:
            items += runner.load_suite(s)
    rows, bad = [], []
    for name in ["oracle"] + sorted(generators.STUBS):
        gen = generators.OracleGenerator(items) if name == "oracle" else generators.STUBS[name]()
        agg = report.aggregate(runner.run_items(gen, items, defs))
        per = {s: r["rate"] for s, r in agg["suites"].items()}
        rows.append((name, agg["overall"]["rate"], agg["overall"]["pass"], agg["overall"]["n"], per))
        if name == "oracle":
            if agg["overall"]["rate"] != 1.0 or any(v != 1.0 for v in per.values()):
                bad.append(f"oracle must score 100% everywhere, got {agg['overall']['rate']}")
        else:
            if agg["overall"]["rate"] > MAX_STUB_OVERALL:
                bad.append(f"{name}: overall {agg['overall']['rate']:.3f} > {MAX_STUB_OVERALL}")
            for s, v in per.items():
                if v > MAX_STUB_SUITE:
                    bad.append(f"{name}: suite {s} {v:.3f} > {MAX_STUB_SUITE}")
    return rows, bad


def cmd_selftest(a):
    rows, bad = selftest_rows()
    print("STUB — NOT A MODEL RESULT (scorer self-test; bounds: adversarial stubs <= %d%% overall, <= %d%% per suite; oracle = 100%%)" % (100 * MAX_STUB_OVERALL, 100 * MAX_STUB_SUITE))
    for n, r, k, t, per in rows:
        worst = max(per.values()) if per else 0.0
        print(f"  {n:20s} {k:4d}/{t:<4d} {r:.4f}   worst suite {worst:.3f}")
    if bad:
        for b in bad:
            print("  VIOLATION:", b)
        raise SystemExit(1)
    print("  self-test OK")
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m pipeline.eval.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build-suites", help="rebuild pipeline/eval/data (see build_suites.py)")
    b.add_argument("rest", nargs=argparse.REMAINDER)
    r = sub.add_parser("run", help="run suites through a model or a stub")
    r.add_argument("--model")
    r.add_argument("--adapter")
    r.add_argument("--stub", help="oracle | " + " | ".join(sorted(generators.STUBS)))
    r.add_argument("--suite", default="all")
    r.add_argument("--out", required=True)
    r.add_argument("--max-items", type=int, default=None, help="deterministic per-suite subsample")
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--max-new-tokens", type=int, default=None)
    r.add_argument("--dataset-version", default=None)
    r.add_argument("--tag", default=None)
    r.add_argument("--tool-schemas", default=None)
    r.add_argument("--no-4bit", action="store_true")
    r.add_argument("--allow-model-load", action="store_true")
    c = sub.add_parser("compare", help="side-by-side comparison of two eval_results.json files (base vs fine-tuned)")
    c.add_argument("--a", required=True)
    c.add_argument("--b", required=True)
    c.add_argument("--label-a", default="base")
    c.add_argument("--label-b", default="fine-tuned")
    c.add_argument("--out", required=True)
    l = sub.add_parser("loss", help="assistant-token loss / perplexity of a model on a split")
    l.add_argument("--model", required=True)
    l.add_argument("--adapter")
    l.add_argument("--split", required=True)
    l.add_argument("--out", required=True)
    l.add_argument("--max-len", type=int, default=2048)
    l.add_argument("--batch-size", type=int, default=4)
    l.add_argument("--limit", type=int, default=None)
    l.add_argument("--bootstrap", type=int, default=0)
    l.add_argument("--no-4bit", action="store_true")
    l.add_argument("--allow-model-load", action="store_true")
    sub.add_parser("selftest", help="run the oracle and adversarial stubs through every suite (scorer sanity check)")
    a = ap.parse_args(argv)
    if a.cmd == "build-suites":
        from . import build_suites
        return build_suites.main(a.rest)
    return {"run": cmd_run, "compare": cmd_compare, "loss": cmd_loss, "selftest": cmd_selftest}[a.cmd](a)


if __name__ == "__main__":
    main()
