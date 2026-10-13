"""Builds qwen_jarvis_dataset_v3 = V2 (unchanged membership, documented corrections) + new generated examples, then validates it.
Writes dataset/v3_final/{train,validation,test}.jsonl, MANIFEST.json and v3_build/reports/*.json. V2 is only read.
Exit code 1 if any validation gate fails (schema, strict JSON targets, tool arguments, leakage, split integrity, contradictions)."""
import collections
import copy
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import gen_common as G  # noqa: E402
import gen_format, gen_honesty, gen_more, gen_strengths, gen_tools  # noqa: E402,E401
from pipeline.data.schema import check_arguments, validate_record  # noqa: E402

OUT = G.WS / "dataset" / "v3_final"
REP = HERE / "reports"
EVAL_DATA = G.WS / "pipeline" / "eval" / "data"
PHASE6_PROMPTS = G.WS.parent / "Jarvis" / "jarvis_phase6_final" / "prompts"
LEAK_THRESHOLD = 0.5          # word-trigram Jaccard between a training user turn and any evaluation prompt
FENCE = re.compile(r"^\s*```(?:json)?\s*\n(.*?)\n```\s*$", re.S)
JSON_ONLY = re.compile(r"\bjson\b", re.I)
ONLY = re.compile(r"\b(only|nothing else|no commentary|just the json|no (?:other )?text)\b", re.I)


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def norm(t):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", (t or "").lower())).strip()


def shingles(t):
    w = norm(t).split()
    return {" ".join(w[i:i + 3]) for i in range(len(w) - 2)} if len(w) >= 3 else {" ".join(w)}


def users(rec):
    return [m.get("content") or "" for m in rec["messages"] if m["role"] == "user"]


def correct_v2(v2):
    """Returns corrected copies + change log. Corrections: (1) fenced answers to JSON-only requests -> the same JSON, raw;
    (2) draft-only replies that said "I can't send it for you" although sending was never requested -> the copy instruction alone."""
    log = []
    out = {}
    for sp, recs in v2.items():
        out[sp] = []
        for rec in recs:
            new = copy.deepcopy(rec)
            changed = []
            for i, msg in enumerate(new["messages"]):
                if msg["role"] != "assistant" or not msg.get("content"):
                    continue
                prev_user = next((m["content"] for m in reversed(new["messages"][:i]) if m["role"] == "user"), "")
                fm = FENCE.match(msg["content"])
                if fm and JSON_ONLY.search(prev_user) and ONLY.search(prev_user):
                    inner = fm.group(1)
                    try:
                        json.loads(inner)
                        msg["content"] = inner
                        changed.append("json_only_unfenced")
                    except ValueError:                     # e.g. JSON Lines: not a single JSON value, left as authored and logged
                        changed.append("json_only_fenced_not_single_value_kept")
                if "I can't send it for you, so copy it" in msg["content"] and not re.search(r"\bsend\b", prev_user, re.I):
                    msg["content"] = msg["content"].replace("I can't send it for you, so copy it", "Copy it")
                    changed.append("draft_no_inability_claim")
            if changed:
                new["metadata"] = {**new["metadata"], "edited": True, "v3_corrections": changed}
                log.append({"id": rec["id"], "split": sp, "corrections": changed})
            new["metadata"]["dataset_version"] = G.VERSION
            out[sp].append(new)
    return out, log


def eval_prompts():
    items = []
    for p in sorted(EVAL_DATA.glob("*.jsonl")) + sorted(PHASE6_PROMPTS.glob("*.jsonl")):
        for l in open(p, encoding="utf-8"):
            if l.strip():
                it = json.loads(l)
                items.append((it["id"], " ".join(m.get("content") or "" for m in it["messages"] if m["role"] == "user")))
    return items


def leakage(records, evals):
    idx = collections.defaultdict(set)
    sh = []
    for k, (_, t) in enumerate(evals):
        s = shingles(t)
        sh.append(s)
        for g in s:
            idx[g].add(k)
    hits = []
    for rec in records:
        for u in users(rec):
            s = shingles(u)
            cand = collections.Counter(k for g in s for k in idx.get(g, ()))
            for k, c in cand.most_common(3):
                j = c / len(s | sh[k])
                if j >= LEAK_THRESHOLD:
                    hits.append({"id": rec["id"], "eval_id": evals[k][0], "jaccard": round(j, 3), "user": u[:120]})
    return hits


def split_of(group):
    h = int(hashlib.sha256(group.encode()).hexdigest(), 16) % 100
    return "validation" if h < 4 else "test" if h < 8 else "train"


def main():
    REP.mkdir(exist_ok=True)
    v2 = G.load_v2()
    v2_hash = {sp: sha(G.V2 / f"{sp}.jsonl") for sp in v2}
    fixed, changes = correct_v2(v2)
    evals = eval_prompts()

    new = gen_format.gen() + gen_honesty.gen() + gen_tools.gen() + gen_strengths.gen() + gen_more.gen()
    # exact duplicates (same conversation) and repeated (prompt, answer) pairs inside the new data
    # one answer per prompt: the key is the user turns only (system-prompt variants of the same request count as duplicates)
    pkey = lambda rec: json.dumps(users(rec), ensure_ascii=False)  # noqa: E731
    seen, uniq, dup_drop = set(), [], 0
    for rec in new:
        if pkey(rec) in seen:
            dup_drop += 1
            continue
        seen.add(pkey(rec))
        uniq.append(rec)
    v2_keys = {pkey(r) for sp in fixed for r in fixed[sp]}
    n0 = len(uniq)
    uniq = [r for r in uniq if pkey(r) not in v2_keys]          # never a second, possibly different, answer to a V2 prompt
    v2_prompt_collisions = n0 - len(uniq)
    leak_new = leakage(uniq, evals)
    leaked_ids = {h["id"] for h in leak_new}
    uniq = [r for r in uniq if r["id"] not in leaked_ids]                  # new examples resembling an evaluation prompt are dropped
    leak_v2 = leakage([r for sp in ("train", "validation") for r in fixed[sp]], evals)   # V2 is reported, not edited

    splits = {sp: list(fixed[sp]) for sp in fixed}
    for rec in uniq:
        splits[split_of(rec["metadata"]["group"])].append(rec)

    # ---- validation gates ----
    problems = collections.Counter()
    bad_examples = []
    strict_json = {"checked": 0, "failed": []}
    tool_args = {"checked": 0, "failed": []}
    defs = G.tool_defs()
    for sp, recs in splits.items():
        for rec in recs:
            probs = validate_record(rec, mode="strict")
            if probs:
                problems["schema"] += 1
                bad_examples.append((rec["id"], probs[:3]))
            fam = rec["metadata"].get("v3_family")
            sub = rec["metadata"]["subcategory"]
            if fam == "format" and not sub.startswith(("exact_text", "format_clarify", "json_fenced")):
                for msg in rec["messages"]:
                    if msg["role"] == "assistant":
                        strict_json["checked"] += 1
                        try:
                            json.loads(msg["content"])
                            assert "```" not in msg["content"]
                        except (ValueError, AssertionError):
                            strict_json["failed"].append(rec["id"])
            for msg in rec["messages"]:
                for call in msg.get("tool_calls") or []:
                    f = call["function"]
                    tool_args["checked"] += 1
                    names = {t["function"]["name"] for t in rec.get("tools", [])}
                    errs = (["tool not attached"] if f["name"] not in names else []) + check_arguments(f["arguments"], defs[f["name"]]["function"]["parameters"])
                    if errs:
                        tool_args["failed"].append({"id": rec["id"], "tool": f["name"], "errors": errs})
    # contradictions: identical prompt (all user turns + system) with different final answers, inside new data
    by_prompt = collections.defaultdict(set)
    for sp in splits:
        for rec in splits[sp]:
            if rec["metadata"].get("v3_family"):
                key = json.dumps(users(rec), ensure_ascii=False)
                by_prompt[key].add(rec["messages"][-1].get("content"))
    contradictions = [k[:160] for k, v in by_prompt.items() if len(v) > 1]
    # split integrity: no group across splits among new data; V2 membership unchanged
    gsplits = collections.defaultdict(set)
    for sp in splits:
        for rec in splits[sp]:
            gsplits[rec["metadata"]["group"]].add(sp)
    new_groups = {r["metadata"]["group"] for r in uniq}
    cross_new = [g for g in new_groups if len(gsplits[g]) > 1]
    cross_v2 = [g for g, s in gsplits.items() if len(s) > 1 and g not in new_groups]
    v2_membership_same = all([r["id"] for r in splits[sp] if not r["metadata"].get("v3_family")] == [r["id"] for r in v2[sp]] for sp in v2)
    ids = [r["id"] for sp in splits for r in splits[sp]]
    # near-duplicate rate inside new data: same normalised assistant answer used for many prompts
    ans = collections.Counter(norm(rec["messages"][-1].get("content"))[:200] for rec in uniq)
    repeated_answers = {k[:80]: c for k, c in ans.most_common(15) if c > 4}

    gates = {"schema_valid": problems["schema"] == 0, "strict_json_targets_valid": not strict_json["failed"], "tool_arguments_valid": not tool_args["failed"],
             "no_new_example_near_eval_prompt": not leakage(uniq, evals), "no_contradictory_targets": not contradictions,
             "no_new_group_across_splits": not cross_new, "v2_split_membership_unchanged": v2_membership_same, "unique_ids": len(ids) == len(set(ids))}

    OUT.mkdir(parents=True, exist_ok=True)
    files = {}
    for sp, recs in splits.items():
        p = OUT / f"{sp}.jsonl"
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            for rec in recs:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        files[f"{sp}.jsonl"] = {"records": len(recs), "sha256": sha(p)}
    allrec = [r for sp in splits for r in splits[sp]]
    cats = collections.Counter(r["metadata"]["category"] for r in allrec)
    manifest = {"version": G.VERSION, "baseline": "qwen_jarvis_dataset_v2_final (11,288 examples; files unchanged)",
                "baseline_sha256": v2_hash, "stages": ["V2 corrections (see v3_build/reports/v2_to_v3_changes.json)", "generated examples (v3_build/gen_*.py)",
                                                       "dedup + evaluation-leakage filter", "group-hash split of new examples (92/4/4); V2 membership unchanged"],
                "records": len(allrec), "files": files, "categories": dict(sorted(cats.items())),
                "multi_turn_records": sum(1 for r in allrec if len(users(r)) > 1), "status": "frozen" if all(gates.values()) else "FAILED_VALIDATION"}
    (OUT / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    fam = collections.Counter((r["metadata"]["v3_family"], r["metadata"]["subcategory"]) for r in uniq)
    reports = {
        "v2_to_v3_changes": {"v2_records": sum(len(v) for v in v2.values()), "v2_records_corrected": len(changes),
                             "by_correction": collections.Counter(c for x in changes for c in x["corrections"]), "changes": changes,
                             "new_records_added": len(uniq), "new_by_split": {sp: sum(1 for r in splits[sp] if r["metadata"].get("v3_family")) for sp in splits}},
        "category_coverage": {"total": len(allrec), "by_category": dict(cats), "new_by_family_subcategory": {f"{a}/{b}": c for (a, b), c in sorted(fam.items())},
                              "by_split": {sp: dict(collections.Counter(r["metadata"]["category"] for r in splits[sp])) for sp in splits},
                              "multi_turn_records": manifest["multi_turn_records"], "with_tools": sum(1 for r in allrec if r.get("tools"))},
        "data_quality_validation": {"gates": gates, "schema_problems": bad_examples[:20], "generated_before_dedup": len(new), "duplicate_prompts_dropped": dup_drop, "new_prompts_colliding_with_v2_dropped": v2_prompt_collisions,
                                    "repeated_assistant_answers_in_new_data(>4 uses)": repeated_answers},
        "split_integrity": {"counts": {sp: len(v) for sp, v in splits.items()}, "v2_membership_unchanged": v2_membership_same,
                            "new_groups_across_splits": cross_new, "preexisting_v2_groups_across_splits": len(cross_v2)},
        "json_validation": {"strict_json_targets": strict_json, "tool_call_arguments": tool_args},
        "duplicates_and_contradictions": {"duplicate_prompts_dropped": dup_drop, "new_prompts_colliding_with_v2_dropped": v2_prompt_collisions, "contradictory_prompts": contradictions},
        "leakage": {"method": f"word-trigram Jaccard >= {LEAK_THRESHOLD} between any user turn and any evaluation prompt ({len(evals)} prompts: pipeline/eval/data + Phase 6 prompts)",
                    "new_examples_dropped": leak_new, "v2_train_validation_matches_reported_only": leak_v2,
                    "note": "the testsplit evaluation suite is derived from the V2 test split by design; V2 test records stay in V3 test"},
    }
    for k, v in reports.items():
        (REP / f"{k}.json").write_text(json.dumps(v, indent=1, ensure_ascii=False, default=list) + "\n", encoding="utf-8")
    print(json.dumps({"counts": reports["split_integrity"]["counts"], "total": len(allrec), "new": len(uniq), "v2_corrected": len(changes),
                      "dup_dropped": dup_drop, "leak_dropped": len(leak_new), "leak_v2_reported": len(leak_v2), "gates": gates}, indent=1))
    return 0 if all(gates.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
