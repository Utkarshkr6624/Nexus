"""Shared helpers for the Phase 2.1 mathematics batches. Every numeric result in an answer is computed here in code (exact arithmetic)."""
from fractions import Fraction
from fractions import Fraction as F2
from gen21_common import Bank
from lib import seeded

B = Bank("mathematics", "m21")
R = seeded("math21")
SHAPES = []          # (name, count) for the shape tally


def shape(n=2, sub="word_problem", diff="medium"):
    """Decorator: run the shape function n times with different numbers; it returns (prompt, answer) or a list of turns."""
    def deco(fn):
        for _ in range(n):
            out = fn()
            if out is None:
                continue
            turns = list(out) if isinstance(out, (list, tuple)) and len(out) > 2 else list(out)
            B.d(sub, *turns, diff=diff, src="computed")
        SHAPES.append(fn.__name__)
        return fn
    return deco


def money(x):
    x = Fraction(x)
    import math as _m
    sign = "-" if x < 0 else ""
    cents = _m.floor(abs(x) * 100 + F2(1, 2))   # round half up, as money is usually rounded
    return f"{sign}${cents // 100:,}.{cents % 100:02d}"


def num(x, nd=2):
    """Trim a number: ints stay ints, Fractions/floats are rounded to nd decimals without trailing zeros."""
    if isinstance(x, int):
        return f"{x:,}" if abs(x) >= 10000 else str(x)
    f = float(x)
    s = f"{f:.{nd}f}".rstrip("0").rstrip(".")
    return s


def frac(x):
    x = Fraction(x)
    return str(x.numerator) if x.denominator == 1 else f"{x.numerator}/{x.denominator}"


def ri(a, b):
    return R.randint(a, b)


def pick(seq):
    return seq[R.randrange(len(seq))]


NAMES = ["Ana", "Ben", "Chen", "Dara", "Eli", "Fatima", "Gus", "Hana", "Ivan", "Jada", "Kofi", "Lena", "Mateo", "Nia", "Omar", "Priya", "Quinn", "Rosa", "Sam", "Tariq", "Uma", "Viktor", "Wen", "Yara", "Zane"]


def name(exclude=()):
    while True:
        n = pick(NAMES)
        if n not in exclude:
            return n


def poly(coefs, var="x"):
    """coefs high->low degree, e.g. [3, -2, 5] -> 3x^2 - 2x + 5"""
    deg = len(coefs) - 1
    parts = []
    for i, c in enumerate(coefs):
        p = deg - i
        if c == 0:
            continue
        a = abs(c)
        body = ("" if a == 1 and p > 0 else str(a)) + (var if p >= 1 else "") + (f"^{p}" if p > 1 else "")
        parts.append(("- " if c < 0 else "+ ") + body)
    s = " ".join(parts)
    return (s[2:] if s.startswith("+ ") else "-" + s[2:]) if s else "0"
