"""Phase 2.1 mathematics: structurally varied, code-computed problems (many new shapes, not number swaps of the Phase 2 templates).
Batches: a (everyday), b (algebra/geometry/stats/calculus/linear algebra), c (probability/combinatorics/number theory/sets),
d (spot-the-error, claims, concepts, estimation, multi-turn follow-ups).  Every number in every answer is computed in code."""
import gen21_math_a  # noqa: F401
import gen21_math_b  # noqa: F401
import gen21_math_c  # noqa: F401
import gen21_math_d  # noqa: F401
import gen21_math_e  # noqa: F401
import gen21_math_f  # noqa: F401
from gen21_math_common import B, SHAPES
from lib import save

recs = B.records()
if __name__ == "__main__":
    save("math21", recs)
    print("distinct shapes:", len(SHAPES), " multi-turn:", sum(1 for r in recs if sum(m["role"] == "user" for m in r["messages"]) >= 2))
