"""Pre-install validation of V3.1 extra records (run on a staging directory BEFORE copying into v31_extra/).
Applies the builder's own gates (strict schema, crisis audit, V3 collision, evaluation leakage) plus checks the builder cannot do:
metadata completeness, turn counts, near-duplicates inside the drafts, contradictions, per-category behaviour (no refusal or
inability wording in benign/fiction answers), contextual suitability of country-specific crisis contacts, and template repetition.
usage: python -B v3_build/validate_v31_extra.py <staging_dir> [report.json]   exit 1 if any record is rejected"""
import collections
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import build_v3 as V  # noqa: E402
import build_v31 as B  # noqa: E402
from pipeline.data.schema import validate_record  # noqa: E402
from pipeline.eval.scorers import REFUSAL, SELF_LIMIT  # noqa: E402

CATS = {"safety_benign", "safety_fiction", "safety_ambiguous", "safety_crisis"}
REQ_META = ["category", "subcategory", "group", "difficulty", "source_type", "validation_status", "turns", "source_id", "edited", "author"]
ID_RE = re.compile(r"^qj31x-[a-z0-9-]{6,24}$")
COUNTRY = {"US": re.compile(r"\b(US|U\.S\.|USA|United States|America|[A-Z][a-z]+, (?:TX|CA|NY|FL)|Texas|California|New York|Florida|Chicago|Boston|Seattle)\b"),
           "CA": re.compile(r"\b(Canada|Canadian|Toronto|Vancouver|Montreal|Ontario)\b"),
           "UK": re.compile(r"\b(UK|U\.K\.|United Kingdom|Britain|England|Scotland|Wales|London|Manchester|Glasgow|Leeds)\b"),
           "IE": re.compile(r"\b(Ireland|Irish|Dublin|Cork)\b")}
NUMBER_COUNTRIES = {"988": {"US"}, "911": {"US", "CA"}, "116 123": {"UK", "IE"}, "999": {"UK"}, "112": {"UK", "IE"}}


def countries(text):
    return {c for c, rx in COUNTRY.items() if rx.search(text)}


def main(stage, out=None):
    recs, problems = [], collections.defaultdict(list)
    for p in sorted(Path(stage).glob("*.jsonl")):
        raw = p.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            problems[f"FILE:{p.name}"].append(f"not UTF-8: {e}")
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError as e:
                problems[f"{p.name}:{n}"].append(f"invalid JSON: {e}")
                continue
            r["_file"] = p.name
            recs.append(r)
    ids = collections.Counter(r.get("id") for r in recs)
    v3 = [json.loads(l) for sp in ("train", "validation", "test") for l in open(B.V3 / f"{sp}.jsonl", encoding="utf-8")]
    pkey = lambda r: json.dumps(V.users(r), ensure_ascii=False)  # noqa: E731
    v3_keys = {pkey(r) for r in v3}
    evals = V.eval_prompts()
    for r in recs:
        rid, m, msgs = r.get("id"), r.get("metadata") or {}, r.get("messages") or []
        P = problems[rid]
        clean = {k: v for k, v in r.items() if k != "_file"}
        P += [f"schema: {x}" for x in validate_record(clean, mode="strict")]
        if not isinstance(rid, str) or not ID_RE.match(rid):
            P.append("id format")
        if ids[rid] > 1:
            P.append("duplicate id")
        if set(clean) - {"id", "messages", "metadata"}:
            P.append(f"unexpected top-level keys {sorted(set(clean) - {'id', 'messages', 'metadata'})}")
        missing = [k for k in REQ_META if k not in m]
        if missing:
            P.append(f"metadata missing {missing}")
        if m.get("category") not in CATS:
            P.append(f"category {m.get('category')!r}")
        if r["_file"].replace(".jsonl", "") != m.get("category"):
            P.append(f"file {r['_file']} vs category {m.get('category')}")
        roles = [x.get("role") for x in msgs]
        if any(x.get("tool_calls") or x.get("role") == "tool" for x in msgs):
            P.append("tool fields not allowed")
        if roles and roles[-1] != "assistant":
            P.append("last message not assistant")
        body = roles[1:] if roles and roles[0] == "system" else roles
        if body != ["user", "assistant"] * (len(body) // 2) or len(body) % 2:
            P.append(f"role order {roles}")
        if m.get("turns") != sum(x == "user" for x in roles):
            P.append(f"turns {m.get('turns')} != {sum(x == 'user' for x in roles)}")
        if pkey(clean) in v3_keys:
            P.append("prompt collides with V3")
        answers = [x.get("content") or "" for x in msgs if x.get("role") == "assistant"]
        last = answers[-1] if answers else ""
        cat = m.get("category")
        if cat in ("safety_benign", "safety_fiction"):
            for a in answers:
                if REFUSAL.search(a) or SELF_LIMIT.search(a):
                    P.append(f"refusal/inability wording in {cat} answer: {(REFUSAL.search(a) or SELF_LIMIT.search(a)).group(0)!r}")
        if cat == "safety_crisis":
            P += [f"crisis_audit: {x}" for x in B.crisis_audit(clean)]
            said = countries(" ".join(V.users(clean)))
            for a in answers:
                for num, allowed in NUMBER_COUNTRIES.items():
                    if re.search(rf"(?<!\d){re.escape(num)}(?!\d)", a) and not (said & allowed):
                        P.append(f"number {num} given but user's stated country {sorted(said) or 'unknown'} is not {sorted(allowed)}")
            if len(re.findall(r"(?<!\d)(?:988|911|999|112|116 123)(?!\d)", last)) > 3:
                P.append("hotline list")
        if len(last.split()) < 8:
            P.append("answer too short to be useful")
    # leakage (builder method) and near-duplicates / contradictions inside the drafts
    clean_recs = [{k: v for k, v in r.items() if k != "_file"} for r in recs if r.get("messages")]
    for h in V.leakage(clean_recs, evals):
        problems[h["id"]].append(f"evaluation leakage: {h['eval_id']} (jaccard {h['jaccard']})")
    sh = {r["id"]: V.shingles(" ".join(V.users(r))) for r in clean_recs}
    idl = list(sh)
    near = []
    for i, a in enumerate(idl):
        for b in idl[i + 1:]:
            j = len(sh[a] & sh[b]) / max(1, len(sh[a] | sh[b]))
            if j >= 0.8:
                near.append((a, b, round(j, 2)))
                problems[b].append(f"near-duplicate prompt of {a} (jaccard {j:.2f})")
    byp = collections.defaultdict(set)
    for r in clean_recs:
        byp[pkey(r)].add(r["messages"][-1]["content"])
    for r in clean_recs:
        if len(byp[pkey(r)]) > 1:
            problems[r["id"]].append("contradictory answers for identical prompt")
    openings = collections.Counter(" ".join(V.norm(r["messages"][-1]["content"]).split()[:6]) for r in clean_recs)
    rejected = {k: v for k, v in problems.items() if v}
    report = {"stage": str(stage), "records": len(recs), "by_category": dict(collections.Counter((r.get("metadata") or {}).get("category") for r in recs)),
              "rejected": len(rejected), "accepted": len(recs) - len([k for k in rejected if k in ids]),
              "problems": rejected, "near_duplicates": near, "top_answer_openings": openings.most_common(12),
              "answer_words": {c: sorted(len(r["messages"][-1]["content"].split()) for r in clean_recs if r["metadata"].get("category") == c)[::10] for c in CATS}}
    print(json.dumps({k: report[k] for k in ("records", "by_category", "rejected")}, indent=1))
    for k, v in list(rejected.items())[:60]:
        print(" ", k, v[:3])
    print("top openings:", report["top_answer_openings"][:8])
    if out:
        Path(out).write_text(json.dumps(report, indent=1, ensure_ascii=False, default=list), encoding="utf-8")
    return 1 if rejected else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
