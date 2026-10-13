"""Phase 2.1 helpers: turn compact hand-written dialogue tuples into records.
A dialogue is d(sub, user1, asst1, user2, asst2, ..., sys=None, diff='medium'); turns alternate user/assistant."""
from lib import *


class Bank:
    def __init__(self, cat, prefix):
        self.cat, self.prefix, self.items = cat, prefix, []

    def d(self, sub, *turns, sys=None, diff="medium", src="authored"):
        assert len(turns) % 2 == 0 and len(turns) >= 2, (sub, turns[:1])
        self.items.append((sub, sys, list(turns), diff, src))

    def records(self):
        out = []
        for i, (sub, sysm, turns, diff, src) in enumerate(self.items):
            pairs = [("user" if j % 2 == 0 else "assistant", t) for j, t in enumerate(turns)]
            out.append(mk(self.cat, sub, pairs, f"{self.prefix}-{i}", diff=diff, src=src, system=sysm))
        return out
