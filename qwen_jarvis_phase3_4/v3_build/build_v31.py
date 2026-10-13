"""Builds qwen_jarvis_dataset_v3_1_remediation (dataset/v3_1_remediation/). Read-only on V3.
Components:
  * new objective-correctness examples (gen_v31_math.py);
  * user-supplied remediation examples from v3_build/v31_extra/*.jsonl (schema in v3_build/V31_EXTRA_SCHEMA.md), audited here;
  * a replay mixture sampled from V3 train (train split) and V3 validation (validation split) by fixed per-family quotas.
Status is PREPARATION_ONLY until the extra (safety-adjacent, fiction, crisis) examples are present and pass every gate.
Exit code 1 if any validation gate fails."""
import collections
import json
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import build_v3 as B  # noqa: E402  (reuses the V3 leakage, split and hashing helpers unchanged)
import gen_common as G  # noqa: E402
import gen_v31_math  # noqa: E402
from pipeline.data.schema import check_arguments, validate_record  # noqa: E402

VERSION = "qwen_jarvis_dataset_v3_1_remediation"
V3 = G.WS / "dataset" / "v3_final"
OUT = G.WS / "dataset" / "v3_1_remediation"
REP = HERE / "reports_v31"
EXTRA = HERE / "v31_extra"
SEED = 31
# replay quotas (train / validation) per family; predicates over V3 records
TARGETED_SAFETY_SUBCATS = {"practical_safety_help", "safe_dual_use", "benign_answer_directly", "crisis_support", "calibrated_answers", "uncertainty",
                           "health_limits", "scam_awareness", "decline_with_alternative", "clarify_ambiguous"}
FAMILIES = {   # order matters: targeted_safety_internal is drawn first, so the later random safety family cannot take its records
    "targeted_safety_internal": (lambda r: r["metadata"]["subcategory"] in TARGETED_SAFETY_SUBCATS
                                 and r["metadata"]["category"] in ("safety", "general_conversation"), 260, 12),
    "json_and_exact_format": (lambda r: r["metadata"].get("v3_family") in ("format", "more") and r["metadata"]["category"] == "instruction_following"
                              or r["metadata"]["subcategory"] in ("json_followup_edit",), 200, 10),
    "capability_honesty": (lambda r: r["metadata"]["category"] == "capability_boundaries", 170, 10),
    "tools": (lambda r: r["metadata"]["category"] == "tool_aware", 170, 10),
    "vetted_safety": (lambda r: r["metadata"]["category"] == "safety", 60, 4),
    "nexus_knowledge": (lambda r: r["metadata"]["category"] == "nexus_knowledge", 110, 6),
    "instruction_following_v2": (lambda r: r["metadata"]["category"] == "instruction_following" and not r["metadata"].get("v3_family"), 90, 6),
    "multi_turn": (lambda r: r["metadata"]["category"] == "multi_turn_context", 80, 5),
    "math_reasoning_code": (lambda r: r["metadata"]["category"] in ("mathematics", "reasoning", "programming"), 80, 5),
    "general": (lambda r: r["metadata"]["category"] in ("general_conversation", "general_knowledge", "jarvis_assistant"), 60, 4),
}
CRISIS_ALLOWED_NUMBERS = {"988", "911", "116 123", "999", "112"}       # audited facts; anything else in a crisis target fails the gate
CRISIS_ALLOWED_URLS = {"findahelpline.com"}
PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d\s\-()]{1,14}\d|\d{3})(?!\d)")
URL = re.compile(r"\b(?:https?://)?(?:www\.)?([a-z0-9-]+\.(?:com|org|net|gov|uk|ie|au|in|cn|io|info))\b", re.I)
CONTACT_CLAIM = re.compile(r"\b(I(?:'ve| have)? (?:called|contacted|connected you|reached|alerted|notified)|I can (?:call|connect you|reach|contact) )", re.I)


def crisis_audit(rec):
    probs = []
    for msg in rec["messages"]:
        if msg["role"] != "assistant":
            continue
        t = msg.get("content") or ""
        for num in PHONE.findall(t):
            n = re.sub(r"[\s\-()]", " ", num).strip()
            if n not in CRISIS_ALLOWED_NUMBERS and n.replace(" ", "") not in {x.replace(" ", "") for x in CRISIS_ALLOWED_NUMBERS}:
                probs.append(f"unaudited number {num!r}")
        for u in URL.findall(t):
            if u.lower() not in CRISIS_ALLOWED_URLS:
                probs.append(f"unaudited url {u!r}")
        if CONTACT_CLAIM.search(t):
            probs.append(f"contact claim {CONTACT_CLAIM.search(t).group(0)!r}")
    return probs


def load_extra():
    recs, problems = [], []
    for p in sorted(EXTRA.glob("*.jsonl")) if EXTRA.exists() else []:
        for n, line in enumerate(open(p, encoding="utf-8"), 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError as e:
                problems.append(f"{p.name}:{n} invalid JSON ({e})")
                continue
            meta = rec.setdefault("metadata", {})
            meta.setdefault("v3_family", "v31_extra")
            meta.setdefault("dataset_version", VERSION)
            meta["source_file"] = p.name
            recs.append(rec)
    return recs, problems


def main():
    REP.mkdir(exist_ok=True)
    v3 = {sp: [json.loads(l) for l in open(V3 / f"{sp}.jsonl", encoding="utf-8")] for sp in ("train", "validation", "test")}
    v3_hash = {sp: B.sha(V3 / f"{sp}.jsonl") for sp in v3}
    rng = random.Random(SEED)
    evals = B.eval_prompts()

    # ---- replay (each V3 record used at most once; V3 test never used) ----
    replay = {"train": [], "validation": []}
    replay_leak = B.leakage([r for sp in ("train", "validation") for r in v3[sp]], evals)
    leaky_v3 = {h["id"] for h in replay_leak}                 # V3 records resembling an evaluation prompt are never replayed
    used_prompts = set()
    used = set(leaky_v3)
    quotas = {}
    for fam, (pred, n_tr, n_va) in FAMILIES.items():
        for sp, n in (("train", n_tr), ("validation", n_va)):
            pool = [r for r in v3[sp] if pred(r) and r["id"] not in used]
            pick = []
            for r in rng.sample(pool, len(pool)):          # one record per user prompt across the replay (no conflicting duplicates)
                if len(pick) == n:
                    break
                if json.dumps(B.users(r), ensure_ascii=False) not in used_prompts:
                    pick.append(r)
                    used_prompts.add(json.dumps(B.users(r), ensure_ascii=False))
            for r in pick:
                used.add(r["id"])
                rr = json.loads(json.dumps(r))
                rr["metadata"] = {**rr["metadata"], "v31_role": "replay", "v31_replay_family": fam, "v31_source": f"{B.G.VERSION}/{sp}", "dataset_version": VERSION}
                replay[sp].append(rr)
            quotas[f"{fam}/{sp}"] = {"requested": n, "pool": len(pool), "taken": len(pick)}

    # ---- new targeted examples ----
    math_new = gen_v31_math.gen()
    extra, extra_parse_problems = load_extra()
    for r in math_new:
        r["metadata"].update({"v31_role": "targeted", "dataset_version": VERSION})
    for r in extra:
        r["metadata"].update({"v31_role": "targeted_extra"})
    pkey = lambda rec: json.dumps(B.users(rec), ensure_ascii=False)  # noqa: E731
    v3_keys = {pkey(r) for sp in v3 for r in v3[sp]}
    seen, new, dup, v3_coll = set(), [], 0, 0
    for r in math_new + extra:
        k = pkey(r)
        if k in seen:
            dup += 1
            continue
        if k in v3_keys:
            v3_coll += 1
            continue
        seen.add(k)
        new.append(r)
    leak = B.leakage(new, evals)
    leaked = {h["id"] for h in leak}
    new = [r for r in new if r["id"] not in leaked]
    splits = {"train": list(replay["train"]), "validation": list(replay["validation"]), "test": []}
    for r in new:
        splits[B.split_of(r["metadata"].get("group", r["id"]))].append(r)

    # ---- gates ----
    schema_bad, crisis_bad, tool_bad = [], [], []
    defs = G.tool_defs()
    for sp, recs in splits.items():
        for r in recs:
            p = validate_record(r, mode="strict")
            if p:
                schema_bad.append({"id": r["id"], "problems": p[:3]})
            is_crisis = r["metadata"].get("category") == "safety_crisis" or r["metadata"].get("subcategory") == "crisis_support"
            if is_crisis:                                   # every crisis record, extra or replayed, must pass the audit
                cp = crisis_audit(r)
                if cp:
                    crisis_bad.append({"id": r["id"], "problems": cp})
            for msg in r["messages"]:
                for call in msg.get("tool_calls") or []:
                    f = call["function"]
                    errs = check_arguments(f["arguments"], defs[f["name"]]["function"]["parameters"]) if f["name"] in defs else ["unknown tool"]
                    if errs:
                        tool_bad.append({"id": r["id"], "errors": errs})
    answers_ok = []
    for r in math_new:
        a = r["messages"][-1]["content"]
        answers_ok.append(bool(re.search(r"\nAnswer: \S", a)))
    by_prompt = collections.defaultdict(set)
    for sp in splits:
        for r in splits[sp]:
            by_prompt[pkey(r)].add(r["messages"][-1].get("content"))
    contradictions = [k[:160] for k, v in by_prompt.items() if len(v) > 1]
    gs = collections.defaultdict(set)
    for sp in splits:
        for r in splits[sp]:
            gs[r["metadata"].get("group", r["id"])].add(sp)
    new_groups = {r["metadata"].get("group", r["id"]) for r in new}
    ids = [r["id"] for sp in splits for r in splits[sp]]
    replay_ids_in_v3_split = all(r["id"] in {x["id"] for x in v3[sp]} for sp in ("train", "validation") for r in replay[sp])
    v3_unchanged = {sp: B.sha(V3 / f"{sp}.jsonl") for sp in v3} == v3_hash
    extras_present = bool(extra)
    replay_ids = {r["id"] for sp in ("train", "validation") for r in replay[sp]}
    gates = {"replay_excludes_eval_similar_v3_records": not (replay_ids & leaky_v3), "schema_valid": not schema_bad, "extra_files_parse": not extra_parse_problems, "tool_arguments_valid": not tool_bad,
             "math_answers_end_with_answer_line": all(answers_ok), "no_new_example_near_eval_prompt": not B.leakage(new, evals),
             "no_contradictory_targets": not contradictions, "no_new_group_across_splits": not [g for g in new_groups if len(gs[g]) > 1],
             "unique_ids": len(ids) == len(set(ids)), "replay_from_matching_v3_split_only": replay_ids_in_v3_split, "v3_unchanged": v3_unchanged,
             "crisis_facts_audited": not crisis_bad}
    status = ("READY_FOR_TRAINING_REVIEW" if extras_present else "PREPARATION_ONLY_AWAITING_SAFETY_EXTRAS") if all(gates.values()) else "FAILED_VALIDATION"

    OUT.mkdir(parents=True, exist_ok=True)
    files = {}
    for sp, recs in splits.items():
        p = OUT / f"{sp}.jsonl"
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            for r in recs:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        files[f"{sp}.jsonl"] = {"records": len(recs), "sha256": B.sha(p)}
    allr = [r for sp in splits for r in splits[sp]]
    role = collections.Counter(r["metadata"]["v31_role"] for r in allr)
    manifest = {"version": VERSION, "status": status, "foundation": "qwen_jarvis_dataset_v3 (dataset/v3_final, unchanged)", "foundation_sha256": v3_hash,
                "records": len(allr), "files": files, "by_role": dict(role),
                "assistant_turn_samples_train": sum(sum(m["role"] == "assistant" for m in r["messages"]) for r in splits["train"]),
                "note": "test.jsonl holds only new targeted examples; the full V3 test split remains the held-out reference"}
    (OUT / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    reports = {
        "v31_counts_and_provenance": {"status": status, "by_split": {sp: len(v) for sp, v in splits.items()}, "by_role": dict(role),
                                      "replay_quotas": quotas, "new_by_subcategory": dict(collections.Counter(r["metadata"]["subcategory"] for r in new)),
                                      "extra_files": sorted({r["metadata"].get("source_file") for r in extra}), "train_mix": {
                                          "targeted_share": round(sum(1 for r in splits["train"] if r["metadata"]["v31_role"] != "replay") / max(1, len(splits["train"])), 3)}},
        "v31_categories": {sp: dict(collections.Counter(r["metadata"].get("category") for r in splits[sp])) for sp in splits},
        "v31_validation": {"gates": gates, "schema_problems": schema_bad[:20], "extra_parse_problems": extra_parse_problems, "tool_argument_problems": tool_bad[:20],
                           "crisis_audit_problems": crisis_bad, "contradictions": contradictions[:20]},
        "v31_dedup_and_leakage": {"new_duplicate_prompts_dropped": dup, "new_prompts_colliding_with_v3_dropped": v3_coll,
                                  "new_dropped_for_eval_similarity": leak, "v3_records_excluded_from_replay_for_eval_similarity": len(leaky_v3), "leakage_method": f"word-trigram Jaccard >= {B.LEAK_THRESHOLD} vs {len(evals)} evaluation prompts"},
    }
    for k, v in reports.items():
        (REP / f"{k}.json").write_text(json.dumps(v, indent=1, ensure_ascii=False, default=list) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "counts": reports["v31_counts_and_provenance"]["by_split"], "by_role": dict(role),
                      "train_samples": manifest["assistant_turn_samples_train"], "dropped": {"dup": dup, "v3_collision": v3_coll, "eval_similar": len(leak)},
                      "gates": gates}, indent=1))
    return 0 if all(gates.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
