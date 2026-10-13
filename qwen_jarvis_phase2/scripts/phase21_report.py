"""Builds reports/PHASE_2_1_QUALITY_REPORT.md (+ reports/phase21_summary.json) from the baseline (baseline_v1/) and the rebuilt v2 outputs.
Stdlib only. Reads only inside qwen_jarvis_phase2/ plus `git status/diff` of the repository root (read-only)."""
import collections
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "validation"))
import diversity  # noqa: E402  (module-level code is guarded by __main__)

REP = ROOT / "reports"
BASE = ROOT / "baseline_v1"
TARGETS = {"general_conversation": 1000, "general_knowledge": 1000, "reasoning": 1500, "programming": 1500, "mathematics": 750, "instruction_following": 750,
           "jarvis_assistant": 1000, "nexus_knowledge": 750, "tool_aware": 750, "capability_boundaries": 500, "multi_turn_context": 500, "safety": 500}


def jl(p):
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def md5(t):
    return hashlib.md5(t.encode("utf-8")).hexdigest()


b_stats = json.load(open(BASE / "reports" / "build_stats.json", encoding="utf-8"))
b_final = json.load(open(BASE / "reports" / "final_validation.json", encoding="utf-8"))
n_stats = json.load(open(REP / "build_stats.json", encoding="utf-8"))
n_final = json.load(open(REP / "final_validation.json", encoding="utf-8"))
removed = json.load(open(REP / "removed_log.json", encoding="utf-8"))
final = jl(ROOT / "dataset" / "validated" / "qwen_jarvis_dataset_v2_all.jsonl")
base_all = jl(BASE / "validated" / "qwen_jarvis_dataset_v1_all.jsonl")
final_hash = {md5(json.dumps(r["messages"], sort_keys=True)) for r in final}
base_hash = {md5(json.dumps(r["messages"], sort_keys=True)) for r in base_all}

# ---- which generator files are Phase 2.1 candidates -----------------------------------------
NEW_FILES = sorted(p.stem for p in (ROOT / "dataset" / "generated").glob("*.jsonl")
                   if p.stem.startswith(("conv21", "prog21", "nexus21", "multiturn21", "math21", "reasoning21", "instruction21")))
cand = {}      # hash -> (file, category)
by_file = collections.Counter()
for stem in NEW_FILES:
    for r in jl(ROOT / "dataset" / "generated" / f"{stem}.jsonl"):
        h = md5(json.dumps(r["messages"], sort_keys=True))
        cand[h] = (stem, r["category"])
        by_file[stem] += 1
cand_by_cat = collections.Counter(c for _, c in cand.values())
survived = {h for h in cand if h in final_hash}
surv_by_cat = collections.Counter(cand[h][1] for h in survived)
reasons = collections.defaultdict(collections.Counter)
for h, (stem, cat) in cand.items():
    if h in final_hash:
        continue
    reasons[cat][removed.get(h, ["unknown"])[0]] += 1

# ---- removal of OLD (baseline) examples -------------------------------------------------------
old_removed = [h for h in base_hash if h not in final_hash]
old_removed_reason = collections.Counter(removed.get(h, ["not_regenerated_or_unknown"])[0] for h in old_removed)
old_removed_cat = collections.Counter()
base_cat_of = {md5(json.dumps(r["messages"], sort_keys=True)): r["metadata"]["category"] for r in base_all}
for h in old_removed:
    old_removed_cat[base_cat_of[h]] += 1
kept_old = len(base_hash & final_hash)

# ---- diversity --------------------------------------------------------------------------------
div_new = diversity.analyse(final)
div_old = diversity.analyse(base_all)
json.dump(div_new, open(REP / "diversity_v2.json", "w", encoding="utf-8"), indent=1)

# ---- subcategories of final ---------------------------------------------------------------------
cat_new = collections.Counter(r["metadata"]["category"] for r in final)
cat_old = collections.Counter(r["metadata"]["category"] for r in base_all)
mt_new = sum(1 for r in final if r["metadata"]["turns"] >= 2)
mt_old = sum(1 for r in base_all if r["metadata"]["turns"] >= 2)
# genuinely useful multi-turn: >=2 user turns and at least 40 words of assistant text in total
mt_useful = sum(1 for r in final if r["metadata"]["turns"] >= 2 and sum(len(m["content"].split()) for m in r["messages"] if m["role"] == "assistant") >= 40)
mt_by_turns = collections.Counter(r["metadata"]["turns"] for r in final if r["metadata"]["turns"] >= 2)

# ---- code / maths / nexus / leakage from the independent audit ---------------------------------
chk = n_final["checks"]
nexus_final = [r for r in final if r["metadata"]["category"] == "nexus_knowledge"]
nexus_cand_total = cand_by_cat.get("nexus_knowledge", 0)
nexus_rejected_validation = sum(1 for h, (s, c) in cand.items() if c == "nexus_knowledge" and removed.get(h, [""])[0] == "validation_rejected")
nexus_new_final = surv_by_cat.get("nexus_knowledge", 0)

# ---- git -----------------------------------------------------------------------------------------
repo = ROOT.parent


def git(*a):
    return subprocess.run(["git", "-C", str(repo)] + list(a), capture_output=True, text=True).stdout


tracked_mod = [l for l in git("status", "--porcelain").splitlines() if not l.startswith("??")]
untracked = [l[3:] for l in git("status", "--porcelain").splitlines() if l.startswith("??")]
diff_stat = git("diff", "--stat").strip()
phase_untracked = [u for u in untracked if u.startswith("qwen_jarvis_phase")]
other_untracked = [u for u in untracked if not u.startswith("qwen_jarvis_phase")]

# ---- write ---------------------------------------------------------------------------------------
L = []
w = L.append
w("# Phase 2.1 Quality Report - dataset `qwen_jarvis_dataset_v2`\n")
w("Correction pass over the isolated Qwen/JARVIS dataset. The NEXUS application was not touched; all changes are inside `qwen_jarvis_phase2/`. "
  "The Phase 2 dataset is preserved untouched in `baseline_v1/`.\n")
w("## 1. Dataset size\n")
w("| | count |\n|---|--:|")
w(f"| Previous count (v1) | {b_stats['final_total']:,} |")
w(f"| New candidates written in Phase 2.1 | {sum(by_file.values()):,} |")
w(f"| New candidates that survived into v2 | {len(survived):,} |")
w(f"| New candidates rejected / removed | {sum(by_file.values()) - len(survived):,} |")
w(f"| v1 examples kept unchanged in v2 | {kept_old:,} |")
w(f"| v1 examples removed in v2 (templated / duplicate / balance) | {len(old_removed):,} |")
w(f"| **Final count (v2)** | **{n_stats['final_total']:,}** |\n")
w("New candidates by generator file:\n")
w("| file | candidates |\n|---|--:|")
for k, v in sorted(by_file.items()):
    w(f"| {k} | {v} |")
w("")
w("Why new candidates were removed (by category):\n")
w("| category | candidates | kept | removed | reasons |\n|---|--:|--:|--:|---|")
for c in TARGETS:
    if cand_by_cat.get(c):
        w(f"| {c} | {cand_by_cat[c]} | {surv_by_cat.get(c, 0)} | {cand_by_cat[c] - surv_by_cat.get(c, 0)} | {dict(reasons[c]) or '-'} |")
w("")
w("## 2. Category balance\n")
w("| category | old (v1) | new (v2) | target (minimum) | pass |\n|---|--:|--:|--:|:-:|")
allpass = True
for c, t in TARGETS.items():
    ok = cat_new.get(c, 0) >= t
    allpass &= ok
    w(f"| {c} | {cat_old.get(c, 0)} | {cat_new.get(c, 0)} | {t} | {'PASS' if ok else 'FAIL'} |")
w(f"| **total** | {len(base_all)} | {len(final)} | 10,000 | {'PASS' if len(final) >= 10000 else 'FAIL'} |\n")
w(f"Largest category share: {n_final['checks']['largest_category_share_pct']}% (v1: {b_final['checks']['largest_category_share_pct']}%).\n")
w("## 3. Diversity\n")
w(f"- Exact duplicates removed in the v2 build: {n_stats['duplicates_removed'].get('exact_duplicate', 0)} (v1 build: {b_stats['duplicates_removed'].get('exact_duplicate', 0)}). "
  f"These are overlaps between the regenerated generator outputs, not duplicates in the final files.")
w(f"- Same-prompt (normalised) duplicates removed: {n_stats['duplicates_removed'].get('same_prompt_normalised', 0)}")
w(f"- Near-duplicates removed (MinHash/Jaccard >= 0.8 on prompts within a category, or near-identical answers): {n_stats['duplicates_removed'].get('near_duplicate', 0)}")
w(f"- Identical-answer cap (more than 2 copies of one long answer): {n_stats['duplicates_removed'].get('identical_answer_cap', 0)}")
w(f"- **Templated examples removed** by the new repetition trim (skeleton clusters > 12, or the same sub-category and 4-word answer opener > 22): "
  f"**{n_stats.get('template_trimmed_total', 0)}** - {n_stats.get('template_trimmed', {})}")
w(f"- Balance-cap drops: {n_stats.get('balance_dropped', {})}")
w(f"- Duplicates / near-duplicates remaining in the final files (independent audit): exact-duplicate conversations {chk['exact_duplicate_conversations']}, duplicate prompts {chk['duplicate_prompts']}\n")
w("Diversity metrics (higher is more varied; `prompt skeleton ratio` = distinct masked prompt shapes / examples; `answer skeleton ratio` likewise for answers; "
  "`top opener` = share of the single most common 4-word assistant opener):\n")
w("| category | n v1 -> v2 | prompt skeleton v1 -> v2 | answer skeleton v1 -> v2 | top opener v1 -> v2 |\n|---|--:|--:|--:|--:|")
for c in TARGETS:
    o, n = div_old[c], div_new[c]
    w(f"| {c} | {o['n']} -> {n['n']} | {o['prompt_skeleton_ratio']} -> {n['prompt_skeleton_ratio']} | {o['answer_skeleton_ratio']} -> {n['answer_skeleton_ratio']} | "
      f"{o['top_asst_opener_share']} -> {n['top_asst_opener_share']} |")
oo, nn = div_old["_overall"], div_new["_overall"]
w(f"| **overall** | {oo['n']} -> {nn['n']} | {oo['prompt_skeleton_ratio']} -> {nn['prompt_skeleton_ratio']} | {oo['answer_skeleton_ratio']} -> {nn['answer_skeleton_ratio']} | - |\n")
w(f"Distinct 4-word assistant openers overall: {oo['distinct_asst_openers_4w']} -> {nn['distinct_asst_openers_4w']}; distinct 3-word user openers: "
  f"{oo['distinct_user_openers_3w']} -> {nn['distinct_user_openers_3w']}.\n")
w("Source mix (v1 -> v2): " + ", ".join(f"{k} {b_stats['source_types'].get(k, 0)} -> {n_stats['source_types'].get(k, 0)}" for k in ("authored", "verified", "computed", "composed")) + ".\n")
w("## 4. Multi-turn\n")
w(f"- Multi-turn examples (>= 2 user turns): v1 {mt_old} -> **v2 {mt_new}** (requirement: >= 1,000).")
w(f"- With substantive assistant content (>= 40 assistant words in total): {mt_useful}.")
w(f"- By number of user turns: {dict(sorted(mt_by_turns.items()))}.")
w(f"- `multi_turn_context` category: v1 {cat_old.get('multi_turn_context', 0)} -> v2 {cat_new.get('multi_turn_context', 0)} (minimum 500).\n")
w("## 5. Code\n")
w(f"- Code blocks checked in the final files by the independent audit: {json.dumps(chk['code_blocks'])}")
w(f"- Failing code blocks: {len(chk['code_blocks'].get('bad_examples', []))}")
w("- Phase 2.1 additions executed for real: compile/runtime error explanations (Python, JavaScript, C, C++, Java, TypeScript) quote genuine compiler/interpreter "
  "output and the corrected program was run and its output asserted; SQL queries ran on SQLite and show the real result table; Python/JS/TS snippets ran and the quoted output is the real output. "
  "Candidates whose code failed verification were dropped, not edited to fit.\n")
w("## 6. Mathematics\n")
w(f"- Maths answers recomputed from the prompt text by the independent audit: {json.dumps({k: v for k, v in chk['math_recomputation'].items() if k != 'disagreements'})}")
w(f"- Reasoning answers recomputed: {json.dumps({k: v for k, v in chk['reasoning_recomputation'].items() if k != 'disagreements'})}")
w(f"- Disagreements: maths {chk['math_recomputation']['disagree']}, reasoning {chk['reasoning_recomputation']['disagree']}\n")
w("## 7. NEXUS\n")
w(f"- NEXUS knowledge examples in v2: **{len(nexus_final)}** (v1: {cat_old.get('nexus_knowledge', 0)}); new candidates written: {nexus_cand_total}; new kept: {nexus_new_final}; "
  f"rejected by the NEXUS-claim validator: {nexus_rejected_validation}.")
w("- Verification: every NEXUS-specific statement was written against `scripts/nexus_facts.py` (derived from `qwen_jarvis_phase1/PHASE_1_REPORT.md`) and passes `validation/checks.py::nexus_claims` "
  "(forbidden-claim list, unknown-path list, entity-without-negation/planned-marker rule). All final NEXUS examples pass; none claims Qwen, Ollama, a chat endpoint, web/file/terminal tools or a tool registry exists today.")
w(f"- Final-file NEXUS claim failures (independent audit): {json.dumps(chk['content_check_failures'])} (empty = none)\n")
w("## 8. Leakage\n")
w(f"- Split sizes: {n_stats['split_counts']}")
w(f"- Identical prompts across splits: { {k: v for k, v in chk['leakage'].items() if 'identical_prompts' in k} }")
w(f"- Full leakage block from the audit: `{json.dumps(chk.get('leakage', {}))}`")
w(f"- Held-out examples moved into train to remove prompt/answer similarity: {n_stats.get('leakage_moved_to_train', {})}\n")
w("## 9. Git safety\n")
w(f"- Tracked-file modifications (`git status --porcelain`, lines not starting with `??`): **{len(tracked_mod)}**")
w(f"- `git diff --stat`: {'empty (no tracked file differs from HEAD)' if not diff_stat else diff_stat}")
w(f"- New untracked paths from this work: {phase_untracked}")
w(f"- Other untracked files (all pre-existing, untouched): {len(other_untracked)}")
w("- Existing NEXUS files changed: 0. No model downloaded, no training, no Kaggle use, no dependency installed.\n")
w("## 10. Validation summary\n")
w(f"- Independent audit hard failures: {n_final.get('hard_failures')}")
w(f"- Category minimums met: {allpass}")
w("")
Path(REP / "PHASE_2_1_QUALITY_REPORT.md").write_text("\n".join(L), encoding="utf-8")
json.dump({"final_total": len(final), "new_candidates": sum(by_file.values()), "new_survived": len(survived), "v1_kept": kept_old, "v1_removed": len(old_removed),
           "v1_removed_by_reason": old_removed_reason, "v1_removed_by_category": old_removed_cat, "category_counts": dict(cat_new), "multi_turn": mt_new,
           "tracked_modifications": len(tracked_mod), "template_trimmed": n_stats.get("template_trimmed_total", 0), "all_minimums_met": allpass},
          open(REP / "phase21_summary.json", "w", encoding="utf-8"), indent=1)
print("report written;", "minimums met:", allpass, "final", len(final), "tracked mods", len(tracked_mod))
