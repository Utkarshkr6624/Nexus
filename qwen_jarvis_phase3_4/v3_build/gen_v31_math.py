"""V3.1 objective-correctness examples: arithmetic, time/scheduling (incl. breaks), dates, unit conversions, remainders,
percentages. Every answer is computed, shows short working, re-checks itself where useful, and ends with an explicit
'Answer: X' line (the existing V2/V3 convention: 853 of 2,276 maths/reasoning answers)."""
from datetime import date, timedelta

from gen_common import GENERAL_SYS, Maker

ITEMS = ["notebooks", "tickets", "mugs", "plants", "pens", "batteries", "towels", "candles"]
CUR = [("₹", "rupees"), ("€", "euros"), ("$", "dollars"), ("£", "pounds")]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def hm(total_min):
    total_min %= 24 * 60
    return f"{total_min // 60:02d}:{total_min % 60:02d}"


def gen(seed=310):
    m = Maker("v31_math", seed)
    r = m.rng
    add = lambda q, a, sub: m.add([{"role": "user", "content": q}, {"role": "assistant", "content": a}], "mathematics", sub, system=m.sys(GENERAL_SYS))  # noqa: E731
    for _ in range(70):   # work + breaks -> finish time (the verified 12:15 vs 12:30 error)
        work, brk, nb = r.choice([30, 45, 50, 60, 75, 90, 105, 120]), r.choice([5, 10, 15, 20]), r.randint(1, 2)
        sh, sm = r.randint(7, 16), r.choice([0, 10, 15, 20, 30, 40, 45])
        start = sh * 60 + sm
        end = start + work + brk * nb
        q = r.choice([f"I need {work} minutes of work and I'll take {nb} break{'s' if nb > 1 else ''} of {brk} minutes. If I start at {hm(start)}, when do I finish?",
                      f"A job takes {work} minutes of actual work. Starting at {hm(start)} with {nb} {brk}-minute break{'s' if nb > 1 else ''} in between, what time will it be done?"])
        a = (f"Breaks don't replace work time; they add to it.\nWork: {work} min. Breaks: {nb} x {brk} = {brk * nb} min. Total elapsed: {work + brk * nb} min.\n"
             f"{hm(start)} + {work + brk * nb} min = {hm(end)}.\n\nAnswer: {hm(end)}")
        add(q, a, "schedule_with_breaks")
    for _ in range(50):   # duration between two clock times (incl. crossing noon/midnight)
        s, d = r.randint(0, 23 * 60), r.randint(20, 600)
        e = s + d
        q = f"How long is it from {hm(s)} to {hm(e)}" + (" the next day" if e >= 1440 else "") + "? Give hours and minutes."
        a = f"From {hm(s)} to {hm(e)} is {d} minutes, which is {d // 60} h {d % 60} min.\n\nAnswer: {d // 60} h {d % 60} min"
        add(q, a, "time_duration")
    for _ in range(40):   # date arithmetic
        d0 = date(2026, r.randint(1, 12), r.randint(1, 28))
        n = r.randint(3, 120)
        d1 = d0 + timedelta(days=n)
        q = f"What date is {n} days after {d0.day} {MONTHS[d0.month - 1]} {d0.year}?"
        a = f"Counting {n} days forward from {d0.day} {MONTHS[d0.month - 1]} {d0.year} gives {d1.strftime('%A')}, {d1.day} {MONTHS[d1.month - 1]} {d1.year}.\n\nAnswer: {d1.day} {MONTHS[d1.month - 1]} {d1.year}"
        add(q, a, "date_offset")
    for _ in range(50):   # change from a payment (currency kept as given)
        sym, _name = r.choice(CUR)
        n, price = r.randint(2, 6), r.choice([12, 15, 18, 25, 35, 40, 45, 60])
        pay = next(p for p in (50, 100, 200, 500, 1000, 2000) if p >= n * price)
        it = r.choice(ITEMS)
        q = f"I buy {n} {it} at {sym}{price} each and pay with a {sym}{pay} note. How much change should I get?"
        a = f"Cost: {n} x {sym}{price} = {sym}{n * price}. Change: {sym}{pay} - {sym}{n * price} = {sym}{pay - n * price}.\n\nAnswer: {sym}{pay - n * price}"
        add(q, a, "change_due")
    for _ in range(50):   # mean of a list (the sum is not the answer)
        xs = [r.randint(1, 99) for _ in range(r.randint(3, 7))]
        mean = sum(xs) / len(xs)
        q = f"What is the average of {', '.join(map(str, xs))}?"
        a = f"Sum {sum(xs)}, count {len(xs)}; mean {sum(xs)} / {len(xs)} = {mean:g}.\n\nAnswer: {mean:g}"
        add(q, a, "mean")
    for _ in range(50):   # unit conversions
        v, (u1, u2, f) = r.choice([0.5, 1.2, 2.5, 3.75, 4, 7.5, 12, 0.25]), r.choice([("kilometres", "metres", 1000), ("metres", "centimetres", 100),
                                                                                    ("kilograms", "grams", 1000), ("litres", "millilitres", 1000), ("hours", "minutes", 60)])
        q = f"Convert {v:g} {u1} to {u2}."
        a = f"1 {u1[:-1]} = {f} {u2}, so {v:g} x {f} = {v * f:g} {u2}.\n\nAnswer: {v * f:g} {u2}"
        add(q, a, "unit_convert")
    for _ in range(40):   # remainders
        b = r.randint(3, 12)
        x = r.randint(20, 400)
        q = f"What is the remainder when {x} is divided by {b}?"
        a = f"{x} = {x // b} x {b} + {x % b}, so the quotient is {x // b} and the remainder is {x % b}.\n\nAnswer: {x % b}"
        add(q, a, "remainder")
    for _ in range(50):   # percentages, with a check step
        p, base = r.choice([5, 8, 12, 15, 20, 25, 30, 40]), r.choice([40, 60, 80, 120, 150, 240, 360, 500])
        v = base * p / 100
        q = r.choice([f"What is {p}% of {base}?", f"A {base}-euro item is {p}% off. How much is the discount?"])
        a = f"{p}% of {base} = {base} x {p} / 100 = {v:g}. Check: {v:g} / {base} = {v / base:g} = {p}%.\n\nAnswer: {v:g}"
        add(q, a, "percent")
    return m.out
