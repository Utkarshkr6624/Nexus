"""Shared helpers for the Phase 2.1 reasoning generators. Every answer in these modules is computed or brute-force verified."""
import itertools
from fractions import Fraction as Fr
from lib import *

CAT = "reasoning"
OUT = []
STATS = {"dropped": 0, "dropped_reasons": []}
SYS = [None, None, None, None, "You are a helpful assistant.", "You are a careful assistant. Show the key steps of your reasoning.",
       "You are a helpful, honest and concise assistant.", "You are an AI assistant. Be accurate, direct and kind."]


def R(name):
    return seeded("reason21_" + name)


def emit(sub, turns, diff, group, rng, src="computed", system="auto"):
    """turns: [(role, text)...]"""
    if system == "auto":
        system = pick(rng, SYS)
    OUT.append(mk(CAT, sub, turns, group, diff, src, system))


def drop(why):
    STATS["dropped"] += 1
    STATS["dropped_reasons"].append(why)


def pct(fr, nd=1):
    return f"{float(fr) * 100:.{nd}f}%"


def frac_str(fr):
    fr = Fr(fr)
    return f"{fr.numerator}/{fr.denominator}" if fr.denominator != 1 else str(fr.numerator)


def oxford(items, conj="and"):
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return f"{items[0]} {conj} {items[1]}"
    return ", ".join(items[:-1]) + f", {conj} {items[-1]}"


def cap(s):
    return s[:1].upper() + s[1:]


def ordinal(n):
    return {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh"}.get(n, f"{n}th")
