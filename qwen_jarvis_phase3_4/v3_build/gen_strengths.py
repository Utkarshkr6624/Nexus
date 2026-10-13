"""Priority E: keep strengths (maths with computed answers, multi-turn context, counted-format instruction following)."""
from fractions import Fraction

from gen_common import GENERAL_SYS, Maker

ITEMS = ["apples", "notebooks", "tickets", "bolts", "mugs", "seedlings", "batteries", "postcards"]
PEOPLE = ["Ana", "Ben", "Chloe", "Dev", "Eli", "Fatima", "Gus", "Hana"]
FRUIT = ["mango", "kiwi", "pear", "plum", "fig", "lime", "cherry", "papaya", "guava", "apricot"]


def gen(seed=304):
    m = Maker("strengths", seed)
    r = m.rng
    for i in range(110):   # word problems, answer computed
        k = r.choice(["discount", "rate", "share", "fraction", "avg"])
        if k == "discount":
            p, d = r.choice([40, 60, 80, 120, 250]), r.choice([10, 15, 20, 25, 30])
            ans = p * (100 - d) / 100
            q = f"A jacket costs {p} euros and is {d}% off. What is the sale price?"
            a = f"{d}% of {p} is {p * d / 100:g}, so the sale price is {p} - {p * d / 100:g} = {ans:g} euros."
        elif k == "rate":
            sp, t = r.choice([45, 60, 72, 80, 90]), r.choice([1.5, 2, 2.5, 3])
            q = f"A train travels at {sp} km/h for {t:g} hours. How far does it go?"
            a = f"Distance = speed x time = {sp} x {t:g} = {sp * t:g} km."
        elif k == "share":
            tot, n = r.choice([24, 36, 60, 84, 96]), r.choice([3, 4, 6])
            it, who = r.choice(ITEMS), r.sample(PEOPLE, n)
            q = f"{tot} {it} are shared equally among {', '.join(who)}. How many does each get?"
            a = f"{tot} / {n} = {tot // n}, so each person gets {tot // n} {it}."
        elif k == "fraction":
            a1, b1, a2, b2 = r.randint(1, 5), r.randint(6, 9), r.randint(1, 5), r.randint(6, 9)
            s = Fraction(a1, b1) + Fraction(a2, b2)
            q = f"What is {a1}/{b1} + {a2}/{b2}? Give the answer as a simplified fraction."
            a = f"Using a common denominator of {b1 * b2}: {a1 * b2}/{b1 * b2} + {a2 * b1}/{b1 * b2} = {a1 * b2 + a2 * b1}/{b1 * b2}, which simplifies to {s.numerator}/{s.denominator}."
        else:
            xs = [r.randint(50, 100) for _ in range(r.randint(3, 6))]
            q = f"My test scores are {', '.join(map(str, xs))}. What's my average?"
            a = f"The total is {sum(xs)} over {len(xs)} tests, so the average is {sum(xs)} / {len(xs)} = {sum(xs) / len(xs):.2f}."
        m.add([{"role": "user", "content": q}, {"role": "assistant", "content": a}], "mathematics", f"word_{k}", system=m.sys(GENERAL_SYS))
    for i in range(80):   # multi-turn running total / reference resolution
        n0, add1, sub1 = r.randint(10, 50), r.randint(2, 20), r.randint(1, 9)
        it = r.choice(ITEMS)
        m.add([{"role": "user", "content": f"I have {n0} {it} in stock."}, {"role": "assistant", "content": f"Noted: {n0} {it}."},
               {"role": "user", "content": f"A delivery adds {add1} more."}, {"role": "assistant", "content": f"That brings it to {n0} + {add1} = {n0 + add1} {it}."},
               {"role": "user", "content": f"I sold {sub1}. How many are left?"}, {"role": "assistant", "content": f"{n0 + add1} - {sub1} = {n0 + add1 - sub1} {it} left."}],
              "multi_turn_context", "running_total", system=m.sys(GENERAL_SYS))
    for i in range(70):   # exact-count lists, then an edit in a second turn
        n = r.randint(3, 6)
        fr = r.sample(FRUIT, n + 1)
        a1 = "\n".join(f"- {f}" for f in fr[:n])
        drop = r.choice(fr[:n])
        rest = [f for f in fr[:n] if f != drop] + [fr[n]]
        m.add([{"role": "user", "content": f"List exactly {n} fruits as bullet points, nothing else."}, {"role": "assistant", "content": a1},
               {"role": "user", "content": f"Replace {drop} with {fr[n]}. Keep the same format."}, {"role": "assistant", "content": "\n".join(f"- {f}" for f in rest)}],
              "multi_turn_context", "list_edit_exact", system=m.sys(GENERAL_SYS))
    return m.out
