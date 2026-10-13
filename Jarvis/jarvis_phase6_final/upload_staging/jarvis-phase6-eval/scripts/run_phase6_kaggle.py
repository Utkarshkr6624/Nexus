"""Phase 6 generation run: base Qwen3-8B (no adapter) + the 7 recovered LoRA adapters, same prompts, same decoding.

Loads the 4-bit base ONCE, attaches all adapters by name, and proves adapter control before generating:
  * a logit fingerprint of the bare base model is taken BEFORE any adapter is attached;
  * `with model.disable_adapter()` must reproduce that fingerprint (baseline really has no LoRA active);
  * after `set_adapter(name)`, `active_adapters == [name]` and the fingerprint must differ from the base and from every other adapter.
Generation reuses the ORIGINAL pipeline.eval code (runner.run_items -> scorers.score_item) with greedy decoding and enable_thinking=False,
exactly like pipeline.eval.generators.HFGenerator. Results: <out>/raw/<config>.json (one file per configuration; existing complete files are
skipped, so a killed session resumes). Never writes to the checkpoint directories. Use --stub NAME to exercise the pipeline without a model.

Kaggle usage (GPU T4, internet on for the base model download):
  python run_phase6_kaggle.py --code /kaggle/working/pkg --ckpt-root /kaggle/input/<dataset> --out /kaggle/working/phase6 --max-items 25
"""
import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ORIGINAL_SUITES = ["instruction_following", "multi_turn", "nexus_facts", "tool_awareness", "capability_boundaries", "safety", "general", "testsplit"]
PHASE6_SUITES = ["phase6_format", "phase6_reasoning", "phase6_coding", "phase6_identity", "phase6_agentic"]
STEPS = [800, 1100, 1200, 1300, 1400, 1500, 1596]
CONFIGS = ["base_model"] + [f"checkpoint_{s}" for s in STEPS]
FINGERPRINT_PROMPT = [{"role": "user", "content": "Who are you? Answer in one sentence."}]


FULL_SUITES = set()   # set from --full-suites: original suites that are not subsampled


def load_items(suite, max_items, seed):
    from pipeline.eval.runner import load_suite, subsample
    if suite.startswith("phase6_"):
        with open(HERE.parent / "prompts" / f"{suite}.jsonl", encoding="utf-8") as fh:
            return [json.loads(l) for l in fh if l.strip()]               # supplemental suites always run in full
    return load_suite(suite) if suite in FULL_SUITES else subsample(load_suite(suite), max_items, seed)


def run_config(name, gen, suites, max_items, seed, repeat_format, describe):
    """Runs every suite through `gen(messages, tools) -> str`; keeps the full output and the wall time per item."""
    from pipeline.eval.runner import run_items, tool_defs
    defs = tool_defs()
    rows = []
    for suite in suites:
        items = load_items(suite, max_items, seed)
        for rep in range(repeat_format if suite == "phase6_format" else 1):
            full, times = [], []

            def timed(messages, tools=None):
                t0, out = time.perf_counter(), ""
                try:
                    out = gen(messages, tools)
                    return out
                finally:                                                    # keep full/times aligned with items even when gen raises
                    times.append(round(time.perf_counter() - t0, 3))
                    full.append(out if isinstance(out, str) else "")
            for r, out, t, it in zip(run_items(timed, items, defs), full, times, items):
                r.update({"config": name, "repeat": rep, "seconds": t, "output_full": out, "prompt_messages": it["messages"]})
                rows.append(r)
            print(f"  {name} {suite} rep{rep}: {sum(r['pass'] for r in rows[-len(items):])}/{len(items)} pass", flush=True)
    secs = [r["seconds"] for r in rows]
    return {"config": name, "describe": describe, "n_rows": len(rows), "complete": True,
            "runtime": {"total_s": round(sum(secs), 1), "mean_s": round(statistics.mean(secs), 3), "median_s": round(statistics.median(secs), 3)} if secs else {},
            "rows": rows}


def model_backend(args):
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from pipeline.eval.generators import DECODING
    import trusted_hashes as TH

    # integrity gate FIRST: every one of the 7 adapters must match its trusted sha256 before the base model is even downloaded
    paths, adapter_report = TH.find_adapters(args.ckpt_root.split(","))
    print("adapter hash gate: PASS", {s: str(p) for s, p in paths.items()}, flush=True)
    tok = AutoTokenizer.from_pretrained(args.model_id)
    q = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.float16)
    base = AutoModelForCausalLM.from_pretrained(args.model_id, quantization_config=q, device_map="auto")
    base.eval()
    eos = tok.convert_tokens_to_ids("<|im_end|>")
    decoding = dict(DECODING, max_new_tokens=args.max_new_tokens)

    def fingerprint(m):
        p = tok.apply_chat_template(FINGERPRINT_PROMPT, add_generation_prompt=True, tokenize=False, enable_thinking=False)
        enc = tok(p, return_tensors="pt", add_special_tokens=False).to(m.device)
        with torch.no_grad():
            return m(**enc).logits[0, -1].float().cpu()

    ref = fingerprint(base)                                                  # bare base, before any adapter exists
    model = PeftModel.from_pretrained(base, str(paths[STEPS[0]]), adapter_name=f"checkpoint_{STEPS[0]}")
    for s in STEPS[1:]:
        model.load_adapter(str(paths[s]), adapter_name=f"checkpoint_{s}")
    model.eval()

    ver = {"reference": "bare base logits before any adapter was attached", "adapter_hash_gate": adapter_report}
    with model.disable_adapter():
        d = float((fingerprint(model) - ref).abs().max())
    ver["base_model"] = {"max_abs_logit_diff_vs_reference": d, "pass": d < 1e-3}
    fps = {}
    for s in STEPS:
        n = f"checkpoint_{s}"
        model.set_adapter(n)
        fps[n] = fingerprint(model)
        act = list(model.active_adapters) if not callable(model.active_adapters) else list(model.active_adapters())
        dd = float((fps[n] - ref).abs().max())
        ver[n] = {"active_adapters": act, "max_abs_logit_diff_vs_base": dd, "pass": act == [n] and dd > 1e-3}
    names = list(fps)
    ver["adapters_pairwise_distinct"] = all(float((fps[a] - fps[b]).abs().max()) > 1e-4 for i, a in enumerate(names) for b in names[i + 1:])
    print("adapter verification:", json.dumps(ver, default=str)[:600], flush=True)
    if not (ver["base_model"]["pass"] and all(ver[n]["pass"] for n in names) and ver["adapters_pairwise_distinct"]):
        raise SystemExit("adapter verification FAILED - not generating (see output above)")

    def make_gen(config):
        def gen(messages, tools=None):
            prompt = tok.apply_chat_template(messages, tools=tools or None, add_generation_prompt=True, tokenize=False, enable_thinking=False)
            enc = tok(prompt, return_tensors="pt", add_special_tokens=False).to(model.device)
            kw = dict(max_new_tokens=decoding["max_new_tokens"], do_sample=False, num_beams=1, pad_token_id=tok.pad_token_id, eos_token_id=eos)
            with torch.no_grad():
                if config == "base_model":
                    with model.disable_adapter():
                        out = model.generate(**enc, **kw)
                else:
                    model.set_adapter(config)
                    out = model.generate(**enc, **kw)
            text = tok.decode(out[0][enc["input_ids"].shape[1]:], skip_special_tokens=True)
            import re
            return re.sub(r"^\s*<think>\s*</think>\s*", "", text).strip()
        return gen

    gpu = {"name": torch.cuda.get_device_name(0), "count": torch.cuda.device_count()} if torch.cuda.is_available() else None
    describe = {"model_id": args.model_id, "quantization": "4-bit NF4 double-quant, fp16 compute", "decoding": decoding, "gpu": gpu, "adapter_verification": ver}
    return make_gen, describe


def stub_backend(args):
    from pipeline.eval import generators as G
    from pipeline.eval.runner import load_suite  # noqa: F401
    items = [it for s in args.suites for it in load_items(s, args.max_items, args.seed)]
    table = {"oracle": lambda: G.OracleGenerator(items), "refuse_all": G.RefuseAll, "always_call_tool": G.AlwaysCallTool, "never_call_tool": G.NeverCallTool,
             "empty": G.EmptyOutput, "echo": G.EchoUser, "generic_hedge": G.GenericHedge, "comply_generic": G.ComplyGeneric}
    g = table[args.stub]()
    return (lambda config: g), {"model_id": f"STUB:{args.stub} - NOT A MODEL RESULT", "decoding": None, "adapter_verification": "not applicable (stub)"}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=True, help="extracted training package (contains pipeline/eval)")
    ap.add_argument("--ckpt-root", help="directory holding checkpoint-<step>/adapter/")
    ap.add_argument("--out", required=True)
    ap.add_argument("--model-id", default="Qwen/Qwen3-8B")
    ap.add_argument("--configs", default=",".join(CONFIGS))
    ap.add_argument("--suites", default=",".join(ORIGINAL_SUITES + PHASE6_SUITES))
    ap.add_argument("--max-items", type=int, default=25, help="deterministic per-suite subsample of the ORIGINAL suites (phase6_* always full)")
    ap.add_argument("--full-suites", default="tool_awareness,safety,capability_boundaries", help="original suites run in full (no subsample)")
    ap.add_argument("--seed", type=int, default=20261011)
    ap.add_argument("--repeat-format", type=int, default=2, help="phase6_format is run this many times per config (determinism check)")
    ap.add_argument("--max-new-tokens", type=int, default=384)
    ap.add_argument("--stub", help="run a stub generator instead of a model (pipeline test; results stamped NOT A MODEL RESULT)")
    ap.add_argument("--no-exec", action="store_true", help="python_exec checks become static-only")
    ap.add_argument("--time-budget-hours", type=float, default=10.5,
                    help="do not START a config if elapsed + the slowest finished config would exceed this (Kaggle sessions end at 12 h)")
    a = ap.parse_args(argv)
    t_start = time.time()
    a.suites = a.suites.split(",")
    configs = a.configs.split(",")
    unknown = [c for c in configs if c not in CONFIGS]
    if unknown:
        raise SystemExit(f"unknown configs {unknown}; valid: {CONFIGS}")
    FULL_SUITES.update(s for s in a.full_suites.split(",") if s)
    if a.no_exec:
        os.environ["PHASE6_NO_EXEC"] = "1"
    sys.path.insert(0, str(HERE))
    import trusted_hashes as TH
    pkg_report = TH.verify_package(a.code)       # before ANY package code is imported; trusted constant, not a hash read from the input
    print("package gate: PASS", pkg_report, flush=True)
    sys.path.insert(0, str(Path(a.code)))
    import phase6_checks  # noqa: F401  (registers strict checks)
    out = Path(a.out) / "raw"
    out.mkdir(parents=True, exist_ok=True)
    make_gen, describe = stub_backend(a) if a.stub else model_backend(a)
    describe["package_gate"] = pkg_report
    status = {"configs_requested": configs, "completed": [], "skipped_existing": [], "not_started_time_budget": [], "package_gate": pkg_report,
              "adapter_verification": describe.get("adapter_verification")}
    durations = []
    for cfg in configs:
        f = out / f"{cfg}.json"
        if f.exists() and json.loads(f.read_text(encoding="utf-8")).get("complete"):
            print("skip (complete):", cfg)
            status["skipped_existing"].append(cfg)
            continue
        elapsed = time.time() - t_start
        if durations and elapsed + max(durations) > a.time_budget_hours * 3600:
            status["not_started_time_budget"] = configs[configs.index(cfg):]
            print(f"time budget: not starting {status['not_started_time_budget']} (elapsed {elapsed / 3600:.2f} h)", flush=True)
            break
        print("config:", cfg, flush=True)
        t0 = time.time()
        res = run_config(cfg, make_gen(cfg), a.suites, a.max_items, a.seed, a.repeat_format, describe)
        durations.append(time.time() - t0)
        res["settings"] = {"suites": a.suites, "max_items_original": a.max_items, "full_suites": sorted(FULL_SUITES), "seed": a.seed, "repeat_format": a.repeat_format, "exec_enabled": not a.no_exec,
                           "stub": a.stub}
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(res, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(tmp, f)
        status["completed"].append(cfg)
    status["elapsed_h"] = round((time.time() - t_start) / 3600, 3)
    (Path(a.out) / "run_status.json").write_text(json.dumps(status, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
