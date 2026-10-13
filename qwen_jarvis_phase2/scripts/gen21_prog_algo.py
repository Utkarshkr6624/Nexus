"""Phase 2.1 programming: algorithm selection, data structures, complexity, systems languages (C, C++, Java) and JS/TS.
Every snippet is executed; printed output in the answers is the real output.  Entries that fail are dropped and listed."""
import gen21_algo_lib as L
import gen21_algo_py      # noqa: F401  (registers examples on import)
import gen21_algo_sys     # noqa: F401
import gen21_algo_dialogs  # noqa: F401
import gen21_algo_more     # noqa: F401
import gen21_algo_extra    # noqa: F401
from lib import save

if __name__ == "__main__":
    save("prog21_algo", L.recs)
    print("dropped", len(L.dropped))
    for x in L.dropped:
        print("  ", x)
