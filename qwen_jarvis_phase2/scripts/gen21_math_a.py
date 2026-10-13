"""Phase 2.1 maths batch A: everyday word problems (money, time, travel, recipes, mixtures, rates, ages). All numbers computed."""
import math
from fractions import Fraction as F
from gen21_math_common import *


@shape(2, "shopping")
def tax_and_discount():
    p = ri(40, 400)
    d = pick([10, 15, 20, 25, 30])
    t = pick([5, 6, 7, 8, 10])
    after = F(p) * (100 - d) / 100
    total = after * (100 + t) / 100
    return (f"A coat is listed at {money(p)}. It's {d}% off, and then {t}% sales tax is added to the discounted price. What do I pay at the till?",
            f"Take the discount first: {money(p)} x {F(100 - d, 100)} = {money(after)}. Then add tax: {money(after)} x {F(100 + t, 100)} = {money(total)}.\n\nYou pay **{money(total)}**.")


@shape(2, "shopping")
def unit_price_compare():
    a_g, b_g = pick([250, 400, 500]), pick([750, 900, 1000, 1200])
    a_p, b_p = F(ri(180, 420), 100), F(ri(420, 900), 100)
    ua, ub = a_p / a_g * 100, b_p / b_g * 100
    better = "the small pack" if ua < ub else "the large pack"
    return (f"Cereal comes in a {a_g} g box for {money(a_p)} and a {b_g} g box for {money(b_p)}. Which one is cheaper per 100 g?",
            f"Divide price by weight, scaled to 100 g.\n\n- {a_g} g box: {money(a_p)} / {a_g} x 100 = {money(ua)} per 100 g (about {float(ua):.3f})\n- {b_g} g box: {money(b_p)} / {b_g} x 100 = {money(ub)} per 100 g (about {float(ub):.3f})\n\nSo {better} is cheaper per 100 g.")


@shape(2, "shopping")
def bogo():
    p = ri(8, 30)
    n = ri(3, 9)
    paid = (n + 1) // 2
    return (f"A shop has a buy-one-get-one-free deal on socks at {money(p)} a pair. I want {n} pairs. What's the cheapest I can pay?",
            f"With BOGO you pay for half the pairs, rounded up. For {n} pairs that's {paid} paid pairs.\n\n{paid} x {money(p)} = **{money(paid * p)}**" + ("" if n % 2 == 0 else f"\n\nBecause {n} is odd, one extra pair is paid at full price."))


@shape(2, "finance_math")
def loan_simple():
    pr = ri(5, 40) * 100
    r = pick([3, 4, 5, 6, 8])
    y = ri(2, 6)
    i = F(pr * r * y, 100)
    return (f"I borrow {money(pr)} at {r}% simple interest per year for {y} years. How much do I owe at the end, and how much of that is interest?",
            f"Simple interest = principal x rate x time = {money(pr)} x {r}% x {y} = {money(i)}.\n\nTotal owed: {money(pr)} + {money(i)} = **{money(pr + i)}**, of which **{money(i)}** is interest.")


@shape(2, "finance_math")
def compound_years_to_double():
    r = pick([4, 6, 8, 9, 12])
    yrs = 72 / r
    exact = math.log(2) / math.log(1 + r / 100)
    return (f"Roughly how long does money take to double at {r}% annual compound interest? Use the rule of 72 and compare with the exact figure.",
            f"Rule of 72: 72 / {r} = {num(yrs)} years.\n\nExact: solve (1 + {r}/100)^t = 2, so t = ln 2 / ln {1 + r / 100} = {exact:.2f} years.\n\nThe shortcut is within {abs(yrs - exact):.2f} years of the exact value, which is why it is popular for quick mental estimates.")


@shape(2, "finance_math")
def monthly_payment():
    pr = pick([6000, 9000, 12000, 15000, 24000])
    apr = pick([6, 9, 12])
    n = pick([24, 36, 48])
    i = apr / 1200
    pay = pr * i / (1 - (1 + i) ** -n)
    return (f"A {n}-month loan of {money(pr)} has a {apr}% APR, compounded monthly. What is the monthly payment, and the total interest paid?",
            f"Use the amortisation formula P = L x i / (1 - (1 + i)^-n), where i = {apr}%/12 = {i:.5f} per month and n = {n}.\n\nP = {pay:.2f} per month (rounded to the cent).\n\nTotal paid = {pay:.2f} x {n} = {pay * n:,.2f}, so interest is about **{pay * n - pr:,.2f}**. Lenders round the payment to cents, so the real last payment may differ by a few cents.")


@shape(2, "time")
def timezone_arrival():
    h = ri(1, 9)
    m = pick([0, 15, 30, 45])
    dur = ri(3, 11)
    off = pick([-8, -5, -3, 3, 5, 9])
    dep = h * 60 + m
    arr = (dep + dur * 60 + off * 60) % 1440
    day = (dep + dur * 60 + off * 60) // 1440
    return (f"A flight leaves at {h}:{m:02d} a.m. local time and lasts {dur} hours. The destination is {abs(off)} hours {'ahead of' if off > 0 else 'behind'} the departure city. What is the local arrival time there?",
            f"Add the flight time, then the time-zone difference.\n\n{h}:{m:02d} + {dur} h = {(dep + dur * 60) // 60 % 24}:{m:02d} departure-city time, then {'+' if off > 0 else '-'}{abs(off)} h gives **{arr // 60:02d}:{arr % 60:02d}** local time" + ("" if day == 0 else (" on the next day" if day == 1 else " on the previous day")) + ".")


@shape(2, "time")
def meeting_length():
    s = ri(8, 11) * 60 + pick([0, 20, 40])
    e = s + ri(5, 14) * 15
    br = pick([10, 15, 20, 30])
    return (f"A workshop runs from {s // 60}:{s % 60:02d} to {e // 60}:{e % 60:02d} with one {br}-minute break. How many minutes of actual teaching is that?",
            f"Total span = {e - s} minutes ({(e - s) // 60} h {(e - s) % 60} min). Subtract the break: {e - s} - {br} = **{e - s - br} minutes**.")


@shape(2, "time")
def days_between():
    import datetime
    a = datetime.date(2025, ri(1, 6), ri(1, 28))
    b = a + datetime.timedelta(days=ri(20, 200))
    d = (b - a).days
    return (f"How many days are there from {a:%d %B %Y} to {b:%d %B %Y}, and how many full weeks plus leftover days is that?",
            f"The difference is **{d} days**. Since {d} = 7 x {d // 7} + {d % 7}, that's {d // 7} full weeks and {d % 7} extra day{'s' if d % 7 != 1 else ''}.")


@shape(2, "travel")
def average_speed_two_legs():
    d = pick([60, 90, 120, 180])
    v1, v2 = pick([30, 40, 45, 60]), pick([20, 36, 50, 90])
    t = F(d, v1) + F(d, v2)
    avg = F(2 * d) / t
    return (f"I drive {d} km at {v1} km/h and then drive back the same {d} km at {v2} km/h. What is my average speed for the whole round trip?",
            f"Average speed is total distance over total time, not the average of the two speeds.\n\nTime out = {d}/{v1} = {num(F(d, v1))} h, time back = {d}/{v2} = {num(F(d, v2))} h, total {num(t)} h.\nDistance = {2 * d} km.\n\nAverage = {2 * d} / {num(t)} = **{float(avg):.2f} km/h**, which is the harmonic mean of {v1} and {v2}.")


@shape(2, "travel")
def catch_up():
    v1, v2 = ri(4, 7) * 5, ri(8, 12) * 5
    head = ri(1, 3)
    gap = v1 * head
    t = F(gap, v2 - v1)
    return (f"A cyclist leaves at {v1} km/h. {head} hour{'s' if head > 1 else ''} later, a car leaves the same place along the same road at {v2} km/h. How long after the car leaves does it catch the cyclist, and how far have they gone?",
            f"When the car starts, the cyclist is {v1} x {head} = {gap} km ahead. The car closes the gap at {v2} - {v1} = {v2 - v1} km/h.\n\nTime = {gap} / {v2 - v1} = **{num(t)} hours** ({float(t) * 60:.0f} minutes). Distance from the start = {v2} x {num(t)} = **{num(v2 * t)} km**.")


@shape(2, "travel")
def fuel_cost():
    km = ri(150, 900)
    l100 = F(ri(45, 95), 10)
    price = F(ri(150, 210), 100)
    litres = km * l100 / 100
    return (f"My car uses {num(l100)} litres per 100 km and fuel costs {money(price)} per litre. How much fuel and money does a {km} km trip take?",
            f"Fuel: {km} / 100 x {num(l100)} = {num(litres)} litres.\nCost: {num(litres)} x {money(price)} = **{money(litres * price)}**.")


@shape(2, "travel")
def relative_motion_train():
    L = ri(10, 30) * 10
    v = ri(5, 12) * 5
    t = F(L * 18, v * 5)
    return (f"A {L} m long train passes a signal pole in {num(t)} seconds. How fast is it going, in km/h?",
            f"Passing the pole means travelling its own length: speed = {L} m / {num(t)} s = {num(F(L) / t)} m/s.\n\nConvert to km/h by multiplying by 3.6: **{num(F(L) / t * F(18, 5))} km/h**.")


@shape(2, "recipes")
def scale_recipe():
    n0, n1 = pick([4, 6]), pick([9, 10, 14, 15])
    fl = pick([240, 300, 360])
    eggs = pick([2, 3])
    k = F(n1, n0)
    return (f"A recipe for {n0} people uses {fl} g flour, {eggs} eggs and 1.5 tsp salt. I'm cooking for {n1}. What amounts do I need?",
            f"Scale factor = {n1}/{n0} = {frac(k)}.\n\n- Flour: {fl} x {frac(k)} = {num(fl * k)} g\n- Eggs: {eggs} x {frac(k)} = {num(eggs * k)} (round up to {math.ceil(eggs * k)})\n- Salt: 1.5 x {frac(k)} = {num(F(3, 2) * k)} tsp")


@shape(2, "recipes")
def oven_conversion():
    c = pick([160, 180, 190, 200, 220])
    return (f"A recipe says bake at {c} C. My oven is in Fahrenheit. What temperature should I set?",
            f"F = C x 9/5 + 32 = {c} x 1.8 + 32 = **{num(c * 9 / 5 + 32)} F**.\n\nMost ovens are set in steps of 5 or 25 degrees, so set {round((c * 9 / 5 + 32) / 5) * 5} F.")


@shape(2, "mixtures")
def mixture_percent():
    v1, c1 = ri(2, 8), pick([10, 20, 30])
    v2, c2 = ri(2, 8), pick([50, 60, 80])
    tot = F(v1 * c1 + v2 * c2, v1 + v2)
    return (f"I mix {v1} litres of a {c1}% juice concentrate with {v2} litres of a {c2}% concentrate. What is the concentration of the mixture?",
            f"Add the amounts of pure juice and divide by the total volume.\n\nPure juice: {v1} x {c1}% + {v2} x {c2}% = {num(F(v1 * c1, 100))} + {num(F(v2 * c2, 100))} = {num(F(v1 * c1 + v2 * c2, 100))} L.\nTotal volume: {v1 + v2} L.\n\nConcentration = {num(F(v1 * c1 + v2 * c2, 100))} / {v1 + v2} = **{float(tot):.2f}%**.")


@shape(2, "mixtures")
def dilute_to_target():
    v = ri(2, 6) * 5
    c = pick([40, 50, 60])
    t = pick([10, 15, 20, 25])
    w = F(v * c, t) - v
    return (f"I have {v} litres of {c}% antifreeze solution. How much water must I add to dilute it to {t}%?",
            f"The amount of antifreeze stays the same: {v} x {c}% = {num(F(v * c, 100))} L.\n\nLet w be the water added: {num(F(v * c, 100))} / ({v} + w) = {t}%, so {v} + w = {num(F(v * c, t))}.\n\nw = **{num(w)} litres**.")


@shape(2, "work_rate")
def three_workers():
    a, b, c = pick([4, 6, 12]), pick([3, 4, 6]), pick([6, 8, 12, 24])
    r = F(1, a) + F(1, b) + F(1, c)
    t = 1 / r
    return (f"Three painters can each finish a fence alone in {a}, {b} and {c} hours. How long do they take working together, assuming steady speeds?",
            f"Add their rates (jobs per hour): 1/{a} + 1/{b} + 1/{c} = {frac(r)} per hour.\n\nTime = 1 / ({frac(r)}) = **{frac(t)} hours** (about {float(t):.2f} h, or {float(t) * 60:.0f} minutes).")


@shape(2, "work_rate")
def leaky_tank():
    fill, leak = pick([3, 4, 5]), pick([10, 12, 15, 20])
    r = F(1, fill) - F(1, leak)
    return (f"A tap fills a tank in {fill} hours, but there's a leak that would empty a full tank in {leak} hours. How long to fill the tank with both going?",
            f"Net rate = 1/{fill} - 1/{leak} = {frac(r)} tank per hour.\n\nTime = **{frac(1 / r)} hours** ({float(1 / r):.2f} h). The leak adds {float(1 / r - fill):.2f} hours compared with filling without it.")


@shape(2, "ages")
def ages_sum_ratio():
    c = ri(5, 14)
    k = pick([2, 3, 4])
    p = k * c
    yrs = ri(3, 12)
    return (f"A parent is {k} times as old as their child. The sum of their ages is {p + c}. In {yrs} years, what will the age difference be, and how old will each be?",
            f"Let the child be c, parent {k}c. Then {k}c + c = {p + c}, so c = {c} and the parent is {p}.\n\nThe age difference never changes: {p} - {c} = **{p - c} years**. In {yrs} years they'll be **{c + yrs}** and **{p + yrs}**.")


@shape(2, "ages")
def ages_past_future():
    a, b = ri(20, 40), ri(5, 18)
    n = ri(2, 12)
    nm = name()
    return (f"{nm} is {a} and their cousin is {b}. How many years from now will {nm}'s age be exactly twice the cousin's age?",
            f"Let n be the years from now: {a} + n = 2({b} + n), so {a} + n = {2 * b} + 2n and n = {a - 2 * b}.\n\n" + (f"That gives **{a - 2 * b} years**." if a - 2 * b > 0 else f"n = {a - 2 * b} is not positive, so the older person is already less than twice the cousin's age and the moment was in the past (or never)."))


@shape(2, "proportion")
def inverse_proportion():
    w, d = ri(3, 8), ri(10, 30)
    w2 = w + ri(1, 5)
    return (f"{w} workers build a wall in {d} days. Assuming everyone works at the same pace, how long would {w2} workers take?",
            f"Total work stays the same: {w} x {d} = {w * d} worker-days.\n\nWith {w2} workers: {w * d} / {w2} = **{num(F(w * d, w2))} days**. More workers means fewer days: this is inverse proportion, not direct.")


@shape(2, "proportion")
def map_scale():
    s = pick([25000, 50000, 100000, 200000])
    cm = F(ri(15, 90), 10)
    km = cm * s / 100000
    return (f"On a map with a scale of 1:{s:,}, two towns are {num(cm)} cm apart. How far apart are they in real life?",
            f"1 cm on the map is {s:,} cm in reality. {num(cm)} cm x {s:,} = {num(cm * s)} cm = {num(cm * s / 100)} m.\n\nThat's **{num(km)} km**.")


@shape(2, "units")
def unit_chain():
    mph = ri(20, 80)
    return (f"Convert {mph} miles per hour to metres per second. (1 mile = 1609.344 m.)",
            f"{mph} mph = {mph} x 1609.344 m per 3600 s = {mph * 1609.344:,.1f} / 3600 = **{mph * 1609.344 / 3600:.2f} m/s**.")


@shape(2, "units")
def data_units():
    gb = ri(2, 40)
    mbps = pick([8, 20, 50, 100, 200])
    sec = gb * 8 * 1000 / mbps
    return (f"How long does it take to download a {gb} GB file on a {mbps} Mbps connection if the line is fully used? (1 GB = 1000 MB, 1 byte = 8 bits.)",
            f"{gb} GB = {gb * 1000} MB = {gb * 8000} megabits.\n\nTime = {gb * 8000} / {mbps} = {num(sec)} seconds = **{int(sec) // 60} min {int(sec) % 60} s** (about {sec / 60:.1f} minutes). Real downloads are slower because of overhead and shared bandwidth.")


@shape(2, "estimation", "easy")
def estimate_cost():
    n = ri(180, 420)
    p = ri(11, 29)
    return (f"Estimate {n} x {p} in your head, then give the exact value.",
            f"Round to friendly numbers: {round(n, -1)} x {round(p, -1)} = {round(n, -1) * round(p, -1):,}. A closer estimate: {n} x {p} is near {n} x 10 + {n} x {p - 10} = {n * 10:,} + {n * (p - 10):,}.\n\nExact: **{n * p:,}**.")


@shape(2, "estimation", "medium")
def fermi_piano():
    return ("Roughly how many seconds are in a year? Give a quick estimate, then the exact figure for a non-leap year.",
            "Quick estimate: about 3.15 x 10^7 seconds (a famous shortcut is that a year is roughly pi x 10^7 s).\n\nExact: 365 x 24 x 60 x 60 = **31,536,000 seconds**. A leap year has 31,622,400.")


@shape(2, "percent")
def reverse_percent():
    p = ri(5, 40)
    price = F(ri(2000, 20000), 100)
    orig = price * 100 / (100 - p)
    return (f"After a {p}% discount, a pair of shoes costs {money(price)}. What was the original price?",
            f"The sale price is {100 - p}% of the original, so original = {money(price)} / {F(100 - p, 100)} = **{money(orig)}**.\n\nCheck: {money(orig)} x {F(100 - p, 100)} = {money(orig * (100 - p) / 100)}.")


@shape(2, "percent")
def successive_changes():
    up, down = pick([10, 20, 25, 30, 50]), pick([10, 20, 25, 30, 50])
    f = F(100 + up, 100) * F(100 - down, 100)
    ch = (f - 1) * 100
    return (f"A share price rises {up}% on Monday and falls {down}% on Tuesday. What is the net change over the two days?",
            f"Multiply the factors: {F(100 + up, 100)} x {F(100 - down, 100)} = {num(f, 4)}.\n\nNet change = ({num(f, 4)} - 1) x 100 = **{float(ch):+.2f}%**. Percentage changes don't simply add or cancel because the second one applies to a different base.")


@shape(2, "percent")
def percent_points():
    a, b = ri(20, 45), ri(46, 70)
    return (f"An approval rating rises from {a}% to {b}%. Express the change in percentage points and as a relative percentage.",
            f"Percentage points: {b} - {a} = **{b - a} points**.\n\nRelative change: ({b} - {a}) / {a} = **{(b - a) / a * 100:.1f}%**. Mixing up the two is a common source of misleading headlines.")


@shape(2, "percent")
def tip_split():
    bill = F(ri(4000, 18000), 100)
    tip = pick([15, 18, 20])
    n = ri(3, 7)
    tot = bill * (100 + tip) / 100
    return (f"A dinner bill is {money(bill)} before tip. We add {tip}% tip and split it equally among {n} people. What does each person pay?",
            f"Tip = {money(bill)} x {tip}% = {money(bill * tip / 100)}. Total = {money(tot)}.\n\nEach person pays {money(tot)} / {n} = **{money(tot / n)}** (rounded to the cent).")


@shape(2, "ratios")
def three_way_split():
    a, b, c = ri(1, 5), ri(2, 6), ri(1, 4)
    total = (a + b + c) * ri(4, 20) * 5
    s = a + b + c
    return (f"Three friends invest in the ratio {a}:{b}:{c} and make a profit of {money(total)}. How much does each get?",
            f"Total parts = {a} + {b} + {c} = {s}. One part = {money(total)} / {s} = {money(F(total, s))}.\n\n- First: {a} x {money(F(total, s))} = **{money(a * F(total, s))}**\n- Second: {b} x {money(F(total, s))} = **{money(b * F(total, s))}**\n- Third: {c} x {money(F(total, s))} = **{money(c * F(total, s))}**")


@shape(2, "rates")
def hourly_vs_salary():
    sal = ri(30, 90) * 1000
    hrs = pick([35, 37.5, 40])
    wk = pick([48, 50, 52])
    hr = F(sal) / (F(hrs) * wk)
    return (f"A job pays {money(sal)} a year for a {num(hrs)}-hour week, {wk} paid weeks a year. What is that per hour?",
            f"Hours per year = {num(hrs)} x {wk} = {num(F(hrs) * wk)}.\n\nHourly rate = {money(sal)} / {num(F(hrs) * wk)} = **{money(hr)}** before tax.")


@shape(2, "rates")
def overtime_pay():
    rate = ri(12, 35)
    reg = 40
    ot = ri(3, 14)
    pay = rate * reg + F(3, 2) * rate * ot
    return (f"An employee earns {money(rate)}/hour with time-and-a-half after 40 hours. They worked {reg + ot} hours this week. What's their gross pay?",
            f"Regular: 40 x {money(rate)} = {money(rate * 40)}.\nOvertime: {ot} h x {money(F(3, 2) * rate)} = {money(F(3, 2) * rate * ot)}.\n\nTotal = **{money(pay)}**.")


@shape(2, "rates")
def water_bill():
    base = ri(8, 20)
    r1, r2 = F(ri(20, 40), 100), F(ri(45, 80), 100)
    used = ri(20, 60)
    tier = 15
    cost = base + r1 * min(used, tier) + r2 * max(0, used - tier)
    return (f"A water company charges a {money(base)} standing fee, {money(r1)} per cubic metre for the first {tier} m3 and {money(r2)} per m3 above that. What's the bill for {used} m3?",
            f"Tier 1: {tier} x {money(r1)} = {money(tier * r1)}.\nTier 2: {used - tier} x {money(r2)} = {money((used - tier) * r2)}.\nStanding fee: {money(base)}.\n\nTotal = **{money(cost)}**.")


@shape(2, "geometry_everyday")
def paint_room():
    L, W, H = ri(30, 60) / 10, ri(25, 50) / 10, F(ri(24, 30), 10)
    door = F(2)
    cover = pick([8, 10, 12])
    area = 2 * (F(L).limit_denominator() + F(W).limit_denominator()) * H - door
    tins = math.ceil(area / cover)
    return (f"A room is {L} m by {W} m with walls {num(H)} m high. The doors and windows take up about 2 m2. One litre of paint covers {cover} m2 per coat. How many litres for two coats, rounded up to whole litres?",
            f"Wall area = perimeter x height - openings = 2({L} + {W}) x {num(H)} - 2 = {num(area)} m2.\n\nTwo coats = {num(2 * area)} m2. At {cover} m2 per litre that's {num(2 * area / cover)} L, so buy **{math.ceil(2 * area / cover)} litres**.")


@shape(2, "geometry_everyday")
def tiles_floor():
    L, W = ri(30, 70) / 10, ri(25, 55) / 10
    t = pick([30, 40, 50, 60])
    n = math.ceil(L * 100 / t) * math.ceil(W * 100 / t)
    return (f"I'm tiling a {L} m x {W} m floor with square {t} cm tiles, whole tiles only (cut tiles still count as one). How many tiles?",
            f"Tiles along the length: {L * 100:.0f} / {t} = {L * 100 / t:.2f}, rounded up to {math.ceil(L * 100 / t)}.\nAlong the width: {W * 100:.0f} / {t} = {W * 100 / t:.2f}, rounded up to {math.ceil(W * 100 / t)}.\n\n{math.ceil(L * 100 / t)} x {math.ceil(W * 100 / t)} = **{n} tiles** (buy 5-10% extra for breakage).")


@shape(2, "geometry_everyday")
def pizza_value():
    d1, d2 = pick([8, 10, 12]), pick([14, 16, 18])
    p1, p2 = F(ri(700, 1200), 100), F(ri(1300, 2200), 100)
    a1, a2 = math.pi * (d1 / 2) ** 2, math.pi * (d2 / 2) ** 2
    return (f"A {d1}-inch pizza costs {money(p1)} and a {d2}-inch pizza costs {money(p2)}. Which gives more pizza per dollar?",
            f"Area = pi x (d/2)^2.\n\n- {d1}-inch: {a1:.1f} sq in, so {a1 / float(p1):.2f} sq in per dollar\n- {d2}-inch: {a2:.1f} sq in, so {a2 / float(p2):.2f} sq in per dollar\n\n**The {d1 if a1 / float(p1) > a2 / float(p2) else d2}-inch** is the better value. Area grows with the square of the diameter, so a size that is slightly bigger can have much more pizza.")


@shape(2, "sequences_everyday")
def saving_plan():
    a, d, n = ri(10, 40), ri(5, 15), ri(8, 20)
    last = a + (n - 1) * d
    tot = n * (a + last) // 2
    return (f"I save {money(a)} in week 1 and increase my deposit by {money(d)} every week. How much will I deposit in week {n}, and what's my total after {n} weeks?",
            f"This is an arithmetic sequence: week k deposit = {a} + (k - 1) x {d}.\n\nWeek {n}: {a} + {n - 1} x {d} = **{money(last)}**.\nTotal = n/2 x (first + last) = {n}/2 x ({a} + {last}) = **{money(tot)}**.")


@shape(2, "sequences_everyday")
def population_growth():
    p = ri(10, 90) * 1000
    g = pick([2, 3, 5])
    y = ri(5, 12)
    return (f"A town of {p:,} people grows {g}% a year. What is the population after {y} years, and how many people were added?",
            f"Multiply by {1 + g / 100} each year: {p:,} x {1 + g / 100}^{y} = {p * (1 + g / 100) ** y:,.0f}.\n\nAdded people: about **{p * (1 + g / 100) ** y - p:,.0f}** (population rounded to a whole number).")


@shape(2, "sequences_everyday")
def half_life():
    h = pick([5, 8, 12, 30])
    t = h * ri(2, 5) + pick([0, h // 2])
    n0 = pick([80, 200, 640, 1000])
    return (f"A sample has a half-life of {h} days and starts at {n0} mg. How much is left after {t} days?",
            f"Number of half-lives = {t}/{h} = {t / h:.2f}.\n\nRemaining = {n0} x (1/2)^{t / h:.2f} = **{n0 * 0.5 ** (t / h):.2f} mg**.")


@shape(2, "estimation", "medium")
def sig_figs_check():
    a, b = round(R.uniform(2, 9), 1), round(R.uniform(11, 99), 1)
    return (f"A rectangle is measured as {a} cm by {b} cm (each to one decimal place). What area should I report, and with how many significant figures?",
            f"Product: {a} x {b} = {a * b:.3f} cm2.\n\nWhen multiplying measurements, keep the fewest significant figures of the inputs. {a} has 2 and {b} has 3, so report **{float(f'{a * b:.2g}'):g} cm2** (2 significant figures). Extra digits would suggest precision the measurements don't have.")


@shape(2, "logic_arith", "easy")
def coins_puzzle():
    q, d = ri(2, 9), ri(2, 9)
    tot = 25 * q + 10 * d
    return (f"A jar has quarters (25c) and dimes (10c). There are {q + d} coins worth {money(F(tot, 100))} in total. How many of each?",
            f"Let q be quarters, d dimes. Then q + d = {q + d} and 25q + 10d = {tot}. Replacing d with {q + d} - q: 25q + 10({q + d} - q) = {tot}, so 15q = {tot - 10 * (q + d)} and q = {q}.\n\nSo **{q} quarters and {d} dimes**. Check: 25 x {q} + 10 x {d} = {tot}.")


@shape(2, "logic_arith", "medium")
def tickets_system():
    a, c = ri(30, 120), ri(20, 100)
    pa, pc = pick([12, 15, 18, 20]), pick([5, 6, 8, 9])
    return (f"A theatre sold {a + c} tickets for {money(a * pa + c * pc)}. Adult tickets cost {money(pa)} and child tickets {money(pc)}. How many adult tickets were sold?",
            f"Let a be adult tickets. Then children = {a + c} - a, and {pa}a + {pc}({a + c} - a) = {a * pa + c * pc}.\n\n{pa - pc}a = {a * pa + c * pc - pc * (a + c)}, so a = **{a}** adult tickets and {c} child tickets.")


@shape(2, "estimation", "easy")
def mental_percent():
    p = pick([15, 35, 45, 12])
    n = ri(40, 400)
    return (f"What's a quick way to find {p}% of {n} in your head? Give the exact answer too.",
            f"Split the percentage: 10% of {n} is {num(F(n, 10))}" + (f", and 5% is half of that ({num(F(n, 20))}), so 15% = {num(F(n, 10) + F(n, 20))}." if p == 15 else f"; here that's built from 10% = {num(F(n, 10))} and 1% = {num(F(n, 100))}.") + f"\n\nExact: {p}% of {n} = **{num(F(n * p, 100))}**.")
