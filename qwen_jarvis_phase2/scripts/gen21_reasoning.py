"""Phase 2.1 reasoning generator (entry point). Combines the gen21_reason_* family modules, saves dataset/generated/reasoning21.jsonl.
Every numeric / logical answer is computed or brute-force verified inside the family modules; failed verifications are dropped and counted."""
import re
import gen21_reason_util as U
import gen21_reason_logic as LG
import gen21_reason_puzzles as PZ
import gen21_reason_prob as PB
import gen21_reason_text as TX
from lib import save, seeded


def skeleton(text):
    t = re.sub(r"`[^`]*`|\"[^\"]*\"|'[^']*'", " Q ", text)
    t = re.sub(r"\b\d+(?:[.,]\d+)?\b", " N ", t)
    t = re.sub(r"\b[A-Z][a-z]+\b", " W ", t)
    return re.sub(r"[^a-zA-Z]+", " ", t).lower().strip()[:90]


if __name__ == "__main__":
    rng = seeded("reasoning21_main")
    counts = {}
    for name, mod in (("logic", LG), ("puzzles", PZ), ("prob", PB), ("text", TX)):
        counts[name] = mod.run(U.R(name))
    recs = U.OUT
    save("reasoning21", recs)
    shapes = {skeleton(next(m["content"] for m in r["messages"] if m["role"] == "user")) for r in recs}
    multi = sum(1 for r in recs if sum(1 for m in r["messages"] if m["role"] == "user") >= 2)
    subs = len({r["subcategory"] for r in recs})
    print("families:", counts)
    print(f"total {len(recs)}  multi-turn {multi}  subcategories {subs}  distinct prompt skeletons {len(shapes)}  dropped {U.STATS['dropped']}")
    for why in U.STATS["dropped_reasons"][:10]:
        print("  dropped:", why)
