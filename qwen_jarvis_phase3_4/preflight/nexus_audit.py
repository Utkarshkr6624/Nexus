"""A7: NEXUS factual audit. For every nexus_knowledge example, extract concrete tokens (ML_* settings, API paths, file paths, snake_case identifiers,
camelCase / CONSTANT names, numbers with units) and check each against three sources of truth:
  1. qwen_jarvis_phase2/scripts/nexus_facts.py (the fact base derived from Phase 1),
  2. qwen_jarvis_phase1/*.md (the Phase 1 documents),
  3. the NEXUS source tree itself (read-only grep over backend/app, backend/ml/artifacts/*/metrics.md, frontend/src, .env.example, README, docs).
Tokens found in none of them are written to preflight/nexus_unverified_tokens.json for manual reading."""
import collections
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NEXUS = HERE.parent.parent
sys.path.insert(0, str(HERE))
from triage_risk import load  # noqa: E402

corpus_parts = []
corpus_parts.append((NEXUS / "qwen_jarvis_phase2" / "scripts" / "nexus_facts.py").read_text(encoding="utf-8"))
for p in (NEXUS / "qwen_jarvis_phase1").glob("*.md"):
    corpus_parts.append(p.read_text(encoding="utf-8", errors="replace"))
doc_corpus = "\n".join(corpus_parts).lower()

SRC_DIRS = [NEXUS / "backend" / "app", NEXUS / "backend" / "ml", NEXUS / "frontend" / "src", NEXUS / "docs"]
SRC_FILES = [NEXUS / ".env.example", NEXUS / "README.md", NEXUS / "backend" / "requirements.txt", NEXUS / "frontend" / "package.json"]
EXT = {".py", ".ts", ".tsx", ".md", ".json", ".txt", ".yaml", ".yml", ".example", ".toml"}
src_chunks = []
for d in SRC_DIRS:
    for p in d.rglob("*"):
        if p.is_file() and p.suffix in EXT and "node_modules" not in p.parts and ".venv" not in p.parts and p.stat().st_size < 400_000:
            try:
                src_chunks.append(p.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                pass
for p in SRC_FILES:
    if p.exists():
        src_chunks.append(p.read_text(encoding="utf-8", errors="ignore"))
src_corpus = "\n".join(src_chunks).lower()
print("corpus sizes: docs", len(doc_corpus), "source", len(src_corpus))

TOKEN = re.compile(r"\b(ML_[A-Z_]+|[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+|/(?:api/v1/)?[a-z_]+(?:/[a-z_{}:]+)*|[A-Za-z]+\.(?:ts|tsx|py)|[a-z]+(?:_[a-z]+){1,4}|[a-z]+[A-Z][A-Za-z]+|\d+(?:\.\d+)?%?)\b")
ENGLISH_OK = re.compile(r"^(?:\d{1,2}|\d+\.\d+)$")   # small ordinary numbers are checked separately below
COMMON = {"e_g", "i_e", "built_vs_planned", "open_source", "non_negative", "so_far", "up_to", "per_tool", "end_to_end", "read_only", "write_file", "real_world",
          "in_browser", "high_risk", "long_term", "multi_turn", "built_in", "step_by_step", "task_manage", "project_manage", "schedule_plan"}

recs = [r for r in load() if r["metadata"]["category"] == "nexus_knowledge"]
print("nexus examples", len(recs))
unverified = collections.defaultdict(list)
checked = collections.Counter()
for r in recs:
    text = " ".join(m["content"] for m in r["messages"] if m["role"] == "assistant")
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    for m in TOKEN.finditer(text):
        tok = m.group(0)
        low = tok.lower()
        if low in COMMON or len(low) < 4 and not low.startswith("/"):
            continue
        if re.fullmatch(r"\d+(?:\.\d+)?%?", tok):
            # numbers: check the same number string occurs in the docs/source near the NEXUS facts (loose), only for >=3 digits or decimals/percent
            if len(tok.rstrip("%")) < 3 and "." not in tok:
                continue
        checked[tok] += 1
        if low in doc_corpus or low in src_corpus:
            continue
        unverified[tok].append(r["id"])
json.dump({k: v for k, v in sorted(unverified.items(), key=lambda kv: -len(kv[1]))}, open(HERE / "nexus_unverified_tokens.json", "w"), indent=0)
print("distinct tokens checked", len(checked), "unverified", len(unverified))
for k, v in sorted(unverified.items(), key=lambda kv: -len(kv[1]))[:60]:
    print(f"{k!r:40} {len(v):3}  {v[:2]}")
