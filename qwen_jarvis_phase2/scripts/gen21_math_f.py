"""Phase 2.1 maths batch F: 60+ further single-instance shapes across everyday life, puzzles, finance, data, geometry and reasoning-with-numbers."""
import math
import statistics as st
from fractions import Fraction as F
from gen21_math_common import *


def _pf(n):
    out, d = set(), 2
    while n > 1:
        while n % d == 0:
            out.add(d)
            n //= d
        d += 1
    return out


@shape(1, "puzzle", "medium")
def handshake_equation():
    n = ri(6, 20)
    h = n * (n - 1) // 2
    return (f"At a meeting everyone shook hands with everyone else exactly once, and there were {h} handshakes. How many people were there?",
            f"With n people there are n(n - 1)/2 handshakes, so n(n - 1) = {2 * h}, i.e. n^2 - n - {2 * h} = 0.\n\nn = (1 + sqrt(1 + {8 * h})) / 2 = (1 + {math.isqrt(1 + 8 * h)}) / 2 = **{n}** people (the negative root is rejected).")


@shape(1, "puzzle", "easy")
def clock_angle():
    h, m = ri(1, 11), pick([0, 10, 15, 20, 30, 40, 45, 50])
    ang = abs(30 * h - 5.5 * m)
    ang = min(ang, 360 - ang)
    return (f"What is the smaller angle between the hour and minute hands at {h}:{m:02d}?",
            f"The minute hand is at {6 * m} degrees (6 per minute). The hour hand is at {30 * h + m / 2:g} degrees (30 per hour plus 0.5 per minute).\n\nDifference = |{30 * h + m / 2:g} - {6 * m}| = {abs(30 * h + m / 2 - 6 * m):g}, so the smaller angle is **{ang:g} degrees**.")


@shape(1, "puzzle", "medium")
def frog_well():
    depth, up, down = ri(10, 30), ri(3, 6), ri(1, 2)
    days = math.ceil((depth - up) / (up - down)) + 1
    return (f"A snail climbs {up} m each day and slips back {down} m each night in a {depth} m well. On which day does it get out?",
            f"Each full day-night cycle nets {up - down} m, but once the snail reaches the top by day it doesn't slip back. So it only needs to be within {up} m of the top at the start of a day.\n\nAfter n cycles it's at {up - down}n. We need {up - down}n >= {depth - up}, so n = {math.ceil((depth - up) / (up - down))} cycles, and it escapes on day **{days}**.")


@shape(1, "puzzle", "medium")
def consecutive_sum():
    n = ri(3, 6)
    start = ri(5, 40)
    total = sum(range(start, start + n))
    return (f"The sum of {n} consecutive integers is {total}. What are they?",
            f"Let the first be a. The sum is {n}a + {n * (n - 1) // 2} = {total}, so a = ({total} - {n * (n - 1) // 2}) / {n} = {start}.\n\nThe numbers are **{', '.join(str(start + i) for i in range(n))}**. (The middle value is the mean: {total}/{n} = {num(F(total, n))}.)")


@shape(1, "puzzle", "hard")
def digit_puzzle():
    sols = [(a, b) for a in range(1, 10) for b in range(0, 10) if (10 * a + b) == 4 * (a + b) + 3 * 0 + 0 and a != b]
    return ("Find all two-digit numbers that are exactly 4 times the sum of their digits.",
            f"Write the number as 10a + b. We need 10a + b = 4(a + b), so 6a = 3b and b = 2a.\n\nWith digits 0-9 and a at least 1: a = 1, 2, 3, 4 give b = 2, 4, 6, 8. The numbers are **{', '.join(str(10 * a + b) for a, b in [(x, 2 * x) for x in range(1, 5)])}**. Check: 12 = 4 x 3, 24 = 4 x 6, 36 = 4 x 9, 48 = 4 x 12. (Brute-force confirms {len(sols)} solutions.)")


@shape(1, "puzzle", "medium")
def weighing_coins():
    n = pick([3, 9, 27])
    k = int(math.log(n, 3) + 0.5)
    return (f"One of {n} identical-looking coins is heavier. What is the minimum number of weighings on a balance scale that guarantees finding it?",
            f"Each weighing has three outcomes (left heavy, right heavy, balanced), so k weighings can distinguish up to 3^k coins.\n\nWe need 3^k >= {n}, so k = **{k}**: split into three equal groups, compare two, and the result tells you which group holds the heavy coin; repeat.")


@shape(1, "finance_math", "medium")
def inflation_purchasing():
    p, r, y = ri(50, 500), pick([2, 3, 4, 6]), ri(5, 25)
    return (f"At {r}% annual inflation, what will something that costs {money(p)} today cost in {y} years, and what would today's {money(p)} be worth then in today's terms?",
            f"Future price = {money(p)} x {1 + r / 100:g}^{y} = **{money(F(round(p * (1 + r / 100) ** y * 100), 100))}**.\n\nThe purchasing power of {money(p)} left in cash falls to {money(F(round(p / (1 + r / 100) ** y * 100), 100))} in today's terms, a loss of {100 - 100 / (1 + r / 100) ** y:.1f}%.")


@shape(1, "finance_math", "medium")
def break_even():
    fixed, price, var = ri(500, 5000), ri(10, 40), ri(3, 9)
    n = math.ceil(fixed / (price - var))
    return (f"A stall has fixed costs of {money(fixed)}. Each item sells for {money(price)} and costs {money(var)} to make. How many items must it sell to break even?",
            f"Contribution per item = {price} - {var} = {price - var}. Break-even units = fixed costs / contribution = {fixed} / {price - var} = {fixed / (price - var):.2f}, so round up to **{n} items**.\n\nAt {n} items, profit = {n} x {price - var} - {fixed} = {money(n * (price - var) - fixed)}.")


@shape(1, "finance_math", "hard")
def annuity_savings():
    pmt, r, n = pick([100, 200, 250, 500]), pick([3, 4, 5, 6]), ri(5, 25)
    i = r / 100 / 12
    fv = pmt * (((1 + i) ** (n * 12) - 1) / i)
    return (f"I save {money(pmt)} at the end of every month in an account paying {r}% a year, compounded monthly. How much will I have after {n} years, and how much of that did I pay in?",
            f"Future value of an annuity: FV = P x ((1 + i)^N - 1)/i, with i = {r}%/12 = {i:.6f} and N = {n * 12}.\n\nFV = {pmt} x ({(1 + i) ** (n * 12):.5f} - 1)/{i:.6f} = **{fv:,.2f}**.\n\nContributions: {pmt} x {n * 12} = {pmt * n * 12:,}. Interest earned: {fv - pmt * n * 12:,.2f}.")


@shape(1, "finance_math", "medium")
def depreciation():
    v, r, y = ri(8000, 40000), pick([10, 15, 20]), ri(2, 6)
    return (f"A car worth {money(v)} loses {r}% of its current value each year. What is it worth after {y} years, and what is the total loss?",
            f"Value = {money(v)} x {1 - r / 100:g}^{y} = **{money(F(round(v * (1 - r / 100) ** y * 100), 100))}**.\n\nTotal loss = {money(v)} - {money(F(round(v * (1 - r / 100) ** y * 100), 100))} = {money(F(round((v - v * (1 - r / 100) ** y) * 100), 100))}, which is {100 - 100 * (1 - r / 100) ** y:.1f}% of the original value (less than {r * y}% because each year's loss applies to a smaller base).")


@shape(1, "finance_math", "easy")
def currency_round_trip():
    amt, rate = ri(100, 900), round(R.uniform(0.7, 1.4), 3)
    back = round(amt * rate, 2)
    fee = 2
    return (f"I convert {amt} euros to dollars at {rate} dollars per euro, then back at the same rate minus a {fee}% fee on each conversion. How many euros do I end with?",
            f"Euros to dollars: {amt} x {rate} = {amt * rate:.2f} dollars; fee {fee}% leaves {amt * rate * 0.98:.2f}.\n\nBack to euros: {amt * rate * 0.98:.2f} / {rate} = {amt * 0.98:.2f}; fee {fee}% leaves **{amt * 0.98 * 0.98:.2f} euros**. Two {fee}% fees cost {amt - amt * 0.98 * 0.98:.2f} euros.")


@shape(1, "data", "easy")
def table_totals():
    items = [("apples", ri(2, 9), F(ri(20, 90), 100)), ("bread", ri(1, 4), F(ri(150, 400), 100)), ("milk", ri(1, 5), F(ri(80, 200), 100))]
    tot = sum(q * p for _, q, p in items)
    lines = "\n".join(f"- {n}: {q} x {money(p)}" for n, q, p in items)
    return (f"My basket has:\n{lines}\nWhat's the total, and which item costs the most overall?",
            "\n".join(f"{n}: {q} x {money(p)} = {money(q * p)}" for n, q, p in items) + f"\n\nTotal = **{money(tot)}**. The biggest line is **{max(items, key=lambda t: t[1] * t[2])[0]}** at {money(max(q * p for _, q, p in items))}.")


@shape(1, "data", "medium")
def frequency_table_mean():
    vals = [1, 2, 3, 4, 5]
    fr = [ri(1, 9) for _ in vals]
    n = sum(fr)
    mean = F(sum(v * f for v, f in zip(vals, fr)), n)
    return (f"A survey of ratings (1 to 5) gave these counts: " + ", ".join(f"{v} stars: {f}" for v, f in zip(vals, fr)) + ". Find the mean, the mode and the median rating.",
            f"Total responses = {n}. Sum of ratings = " + " + ".join(f"{v}x{f}" for v, f in zip(vals, fr)) + f" = {sum(v * f for v, f in zip(vals, fr))}.\n\nMean = {sum(v * f for v, f in zip(vals, fr))}/{n} = **{float(mean):.2f}**.\nMode = **{vals[fr.index(max(fr))]}** (count {max(fr)}).\nMedian: the middle response(s) are number {(n + 1) // 2}" + (f" and {(n + 1) // 2 + 1}" if n % 2 == 0 else "") + f", which fall in rating **{st.median([v for v, f in zip(vals, fr) for _ in range(f)]):g}**.")


@shape(1, "data", "medium")
def growth_rate_from_data():
    a, b, y = ri(100, 900), 0, ri(3, 9)
    b = a * ri(130, 400) // 100
    cagr = (b / a) ** (1 / y) - 1
    return (f"A website had {a:,} visitors in year 1 and {b:,} visitors {y} years later. What was the compound annual growth rate?",
            f"CAGR = (end/start)^(1/years) - 1 = ({b}/{a})^(1/{y}) - 1 = {(b / a):.4f}^{1 / y:.4f} - 1 = **{cagr * 100:.2f}% per year**.\n\nCheck: {a} x {1 + cagr:.5f}^{y} = {a * (1 + cagr) ** y:.1f}.")


@shape(1, "data", "easy")
def mode_tie_range():
    xs = [ri(1, 9) for _ in range(ri(8, 12))]
    from collections import Counter
    c = Counter(xs)
    top = max(c.values())
    modes = sorted(k for k, v in c.items() if v == top)
    return (f"Find the mode(s) and range of: {', '.join(map(str, xs))}.",
            f"Counts: " + ", ".join(f"{k}: {v}" for k, v in sorted(c.items())) + f".\n\nMode{'s' if len(modes) > 1 else ''}: **{', '.join(map(str, modes))}**" + (" (the data is multimodal)" if len(modes) > 1 else "") + f". Range = {max(xs)} - {min(xs)} = **{max(xs) - min(xs)}**.")


@shape(1, "geometry", "medium")
def ring_area():
    R_, r = ri(6, 15), ri(2, 5)
    return (f"A circular running track has an outer radius of {R_} m and an inner radius of {r} m. What is the area of the track surface?",
            f"Area = pi(R^2 - r^2) = pi({R_ * R_} - {r * r}) = pi x {R_ * R_ - r * r} = **{math.pi * (R_ * R_ - r * r):.1f} m2**. Factorising R^2 - r^2 = (R - r)(R + r) = {R_ - r} x {R_ + r} makes mental maths easier.")


@shape(1, "geometry", "medium")
def trapezoid_area():
    a, b, h = ri(4, 12), ri(13, 25), ri(3, 10)
    return (f"A trapezoid has parallel sides of {a} cm and {b} cm and a height of {h} cm. Find its area.",
            f"Area = (1/2)(a + b)h = 0.5 x ({a} + {b}) x {h} = 0.5 x {a + b} x {h} = **{num(F((a + b) * h, 2))} cm2**.")


@shape(1, "geometry", "hard")
def inscribed_circle_triangle():
    a, b, c = pick([(3, 4, 5), (5, 12, 13), (6, 8, 10), (8, 15, 17)])
    k = ri(1, 3)
    a, b, c = a * k, b * k, c * k
    r = (a + b - c) / 2
    return (f"A right triangle has legs {a} and {b} and hypotenuse {c}. What is the radius of its inscribed circle?",
            f"For a right triangle, r = (a + b - c)/2 = ({a} + {b} - {c})/2 = **{num(r)}**.\n\nCheck using area/semi-perimeter: area = {a * b / 2:g}, s = {(a + b + c) / 2:g}, r = {a * b / 2 / ((a + b + c) / 2):g}.")


@shape(1, "geometry", "medium")
def regular_hexagon():
    s = ri(2, 12)
    return (f"A regular hexagon has side {s} cm. Find its perimeter and area.",
            f"Perimeter = 6 x {s} = **{6 * s} cm**.\n\nIt splits into 6 equilateral triangles of side {s}, each with area (sqrt 3/4) x {s}^2 = {math.sqrt(3) / 4 * s * s:.3f} cm2, so area = 6 x that = **{6 * math.sqrt(3) / 4 * s * s:.2f} cm2**.")


@shape(1, "geometry", "medium")
def arc_to_angle():
    r, arc = ri(4, 15), ri(5, 30)
    ang = arc / r
    return (f"On a circle of radius {r} cm, an arc is {arc} cm long. What is the central angle in radians and degrees?",
            f"Angle (radians) = arc / radius = {arc}/{r} = **{ang:.3f} rad**.\n\nIn degrees: {ang:.3f} x 180/pi = **{math.degrees(ang):.1f} degrees**.")


@shape(1, "geometry", "medium")
def reflect_point():
    x, y = ri(-6, 6), ri(-6, 6)
    return (f"Reflect the point ({x}, {y}) in the x-axis, the y-axis and the line y = x. Give the three images.",
            f"- In the x-axis, y changes sign: **({x}, {-y})**.\n- In the y-axis, x changes sign: **({-x}, {y})**.\n- In y = x, the coordinates swap: **({y}, {x})**.")


@shape(1, "geometry", "medium")
def circle_equation():
    h, k, r = ri(-4, 5), ri(-4, 5), ri(2, 7)
    return (f"Write the equation of the circle with centre ({h}, {k}) and radius {r}, and check whether the point ({h + r}, {k}) lies on it.",
            f"Standard form: (x - {h})^2 + (y - {k})^2 = {r}^2 = **{r * r}**.\n\nTest ({h + r}, {k}): ({h + r} - {h})^2 + ({k} - {k})^2 = {r * r} + 0 = {r * r}. It satisfies the equation, so the point **is on the circle**.")


@shape(1, "algebra", "medium")
def direct_variation():
    k, x1 = ri(2, 9), ri(2, 8)
    x2 = ri(9, 20)
    return (f"y varies directly with x, and y = {k * x1} when x = {x1}. Find the constant and the value of y when x = {x2}.",
            f"Direct variation means y = kx. From the data, k = {k * x1}/{x1} = **{k}**.\n\nWhen x = {x2}: y = {k} x {x2} = **{k * x2}**.")


@shape(1, "algebra", "medium")
def joint_variation():
    k, x, z = ri(2, 5), ri(2, 6), ri(2, 6)
    x2, z2 = x * 2, z * 3
    return (f"y is proportional to x and inversely proportional to z. When x = {x} and z = {z}, y = {F(k * x, z)}. Find y when x = {x2} and z = {z2}.",
            f"Write y = c x / z. From the first case, c = y z / x = {F(k * x, z)} x {z} / {x} = {k}.\n\nThen y = {k} x {x2} / {z2} = **{frac(F(k * x2, z2))}**. Doubling x multiplies y by 2 and tripling z divides it by 3, so the net factor is 2/3.")


@shape(1, "algebra", "medium")
def slope_intercept_from_story():
    fixed, per = ri(10, 40), ri(2, 9)
    n = ri(8, 30)
    tgt = fixed + per * n
    return (f"A gym charges a {money(fixed)} joining fee plus {money(per)} per visit. Write the cost formula and find how many visits cost {money(tgt)} in total.",
            f"Cost C = {per}v + {fixed}.\n\nSet C = {tgt}: {per}v = {tgt - fixed}, so v = **{n}** visits. The slope {per} is the cost per visit and the intercept {fixed} is the cost with zero visits.")


@shape(1, "algebra", "hard")
def break_even_two_plans():
    f1, p1, f2, p2 = ri(5, 20), ri(8, 14), ri(30, 60), ri(2, 6)
    x = F(f2 - f1, p1 - p2)
    return (f"Plan A costs {money(f1)} a month plus {money(p1)} per GB. Plan B costs {money(f2)} a month plus {money(p2)} per GB. At what usage do they cost the same, and which is cheaper beyond that?",
            f"Set {f1} + {p1}g = {f2} + {p2}g, so {p1 - p2}g = {f2 - f1} and g = **{num(x)} GB**.\n\nAt that usage both cost {money(f1 + p1 * x)}. Plan B has the lower per-GB price, so beyond {num(x)} GB, **Plan B** is cheaper; below it, Plan A is.")


@shape(1, "algebra", "medium")
def sum_of_roots_product():
    r1, r2 = ri(-6, 8), ri(-6, 8)
    return (f"A quadratic x^2 + bx + c = 0 has roots {r1} and {r2}. Find b and c without expanding a bracket first.",
            f"For x^2 + bx + c, the sum of roots is -b and the product is c (Vieta's formulas).\n\n-b = {r1} + {r2} = {r1 + r2}, so b = **{-(r1 + r2)}**; c = {r1} x {r2} = **{r1 * r2}**.\n\nThe equation is {poly([1, -(r1 + r2), r1 * r2])} = 0.")


@shape(1, "algebra", "hard")
def discriminant_cases():
    k = ri(2, 12)
    disc = k * k - 16
    return (f"For which values of k does x^2 + kx + 4 = 0 have real roots? Describe what happens for k = {k}.",
            f"The discriminant is k^2 - 16. Real roots need k^2 - 16 >= 0, i.e. k <= -4 or k >= 4.\n\nFor k = {k}: discriminant = {k * k} - 16 = {disc}, which is " + ("positive, so there are two distinct real roots." if disc > 0 else "zero, so there is one repeated root." if disc == 0 else "negative, so there are no real roots (two complex ones)."))


@shape(1, "number_theory", "medium")
def repeating_decimal():
    d = pick([3, 6, 7, 9, 11, 12, 13])
    n = ri(1, d - 1)
    fr = F(n, d)
    return (f"Write {n}/{d} as a decimal and say whether it terminates.",
            f"{n}/{d} = {n / d:.10f}...\n\nIn lowest terms it is {frac(fr)}. A fraction terminates exactly when its denominator has only 2 and 5 as prime factors. The denominator {fr.denominator} " + ("does, so it **terminates**." if all(p in (2, 5) for p in _pf(fr.denominator)) else "does not, so it **repeats** forever."))


def _pf(n):
    out, d = set(), 2
    while n > 1:
        while n % d == 0:
            out.add(d)
            n //= d
        d += 1
    return out


@shape(1, "number_theory", "medium")
def lcm_schedule():
    a, b, c = pick([(4, 6, 10), (6, 8, 12), (5, 10, 15), (3, 4, 5), (12, 18, 30)])
    l = math.lcm(a, b, c)
    return (f"Three buses leave the station every {a}, {b} and {c} minutes. They all leave together at 8:00. When do they next leave together?",
            f"The next common departure is after lcm({a}, {b}, {c}) = **{l} minutes**, which is {8 + l // 60}:{l % 60:02d} " + ("(that is " + str(l // 60) + " h " + str(l % 60) + " min after 8:00)." if l >= 60 else "") + f"\n\nCheck: {l}/{a} = {l // a}, {l}/{b} = {l / b:g}, {l}/{c} = {l // c}.")


@shape(1, "number_theory", "hard")
def chinese_remainder():
    n = ri(10, 200)
    a, b = n % 3, n % 5
    sols = [x for x in range(1, 16) if x % 3 == a and x % 5 == b]
    return (f"A number leaves remainder {a} when divided by 3 and remainder {b} when divided by 5. What is its remainder when divided by 15, and what is the smallest positive such number?",
            f"Since 3 and 5 are coprime, the remainders mod 3 and mod 5 together fix the remainder mod 15 (Chinese Remainder Theorem). Searching 1 to 15 finds **{sols[0] if sols else 0}**.\n\nSo the remainder mod 15 is {sols[0]}, and the smallest positive such number is {sols[0]}. All solutions are {sols[0]} + 15t.")


@shape(1, "combinatorics", "medium")
def circular_arrangement():
    n = ri(5, 9)
    return (f"In how many ways can {n} people sit around a round table, if rotations count as the same arrangement?",
            f"Fix one person's seat to remove rotations, then arrange the other {n - 1}: ({n} - 1)! = **{math.factorial(n - 1):,}**.\n\nIf reflections were also treated as the same, you'd divide by 2 (for n > 2): {math.factorial(n - 1) // 2:,}.")


@shape(1, "combinatorics", "medium")
def grid_paths():
    r, c = ri(3, 7), ri(3, 7)
    return (f"How many shortest paths go from the bottom-left to the top-right of a grid that is {r} blocks up and {c} blocks across (moving only up or right)?",
            f"Every shortest path has {r} up-moves and {c} right-moves in some order. Choose which {r} of the {r + c} moves are 'up': C({r + c},{r}) = **{math.comb(r + c, r):,}**.")


@shape(1, "combinatorics", "hard")
def derangements():
    n = ri(4, 8)
    d = [1, 0]
    for k in range(2, n + 1):
        d.append((k - 1) * (d[-1] + d[-2]))
    return (f"{n} people put their hats in a pile and each takes one at random. How many ways can nobody get their own hat, and what's the probability?",
            f"These are derangements. D(n) satisfies D(n) = (n - 1)(D(n-1) + D(n-2)) with D(1) = 0, D(2) = 1.\n\nD({n}) = **{d[n]}**. Probability = {d[n]}/{n}! = {d[n]}/{math.factorial(n)} = **{d[n] / math.factorial(n):.4f}**, which is already very close to 1/e = {1 / math.e:.4f}.")


@shape(1, "combinatorics", "medium")
def license_plates():
    L, D = ri(2, 4), ri(2, 4)
    return (f"A plate has {L} letters followed by {D} digits. How many plates are possible if letters can't repeat but digits can? And if the first digit can't be 0?",
            f"Letters: 26 x 25 x ... for {L} places = {math.perm(26, L):,}. Digits: 10^{D} = {10 ** D:,}.\n\nTotal = {math.perm(26, L):,} x {10 ** D:,} = **{math.perm(26, L) * 10 ** D:,}**.\n\nWith a non-zero first digit: 9 x 10^{D - 1} = {9 * 10 ** (D - 1):,} digit patterns, so **{math.perm(26, L) * 9 * 10 ** (D - 1):,}** plates.")


@shape(1, "probability", "medium")
def monty_like():
    n = pick([3, 5, 10])
    return (f"There are {n} doors and one prize. You pick one, then the host opens {n - 2} of the others (all empty) and offers a switch to the one remaining closed door. What are your chances if you stay and if you switch?",
            f"Staying wins only if your first pick was right: **1/{n}** ({100 / n:.0f}%).\n\nSwitching wins whenever your first pick was wrong: **{n - 1}/{n}** ({100 * (n - 1) / n:.1f}%). The host's knowledge concentrates the original {n - 1}/{n} probability onto the one door he left closed.")


@shape(1, "probability", "medium")
def gambler_ruin_short():
    p = F(1, 2)
    start, goal = ri(1, 4), ri(5, 9)
    return (f"A gambler starts with ${start} and bets $1 on a fair coin each round until reaching ${goal} or going broke. What's the probability of reaching ${goal}?",
            f"For a fair game, the probability of reaching the target is start/goal (the gambler's ruin result).\n\nP = {start}/{goal} = **{frac(F(start, goal))}** (about {start / goal:.3f}). The expected number of rounds is start x (goal - start) = {start * (goal - start)}.")


@shape(1, "probability", "medium")
def conditional_cards():
    return ("Two cards are drawn from a standard deck. Given that at least one is an ace, what's the probability both are aces?",
            f"P(both aces) = (4/52)(3/51) = {frac(F(12, 2652))}.\n\nP(at least one ace) = 1 - (48/52)(47/51) = 1 - {frac(F(48 * 47, 52 * 51))} = {frac(1 - F(48 * 47, 52 * 51))}.\n\nConditional probability = {frac(F(12, 2652))} / {frac(1 - F(48 * 47, 52 * 51))} = **{frac(F(12, 2652) / (1 - F(48 * 47, 52 * 51)))}** (about {float(F(12, 2652) / (1 - F(48 * 47, 52 * 51))):.4f}).")


@shape(1, "probability", "medium")
def reliability_series_parallel():
    p1, p2, p3 = F(ri(80, 99), 100), F(ri(80, 99), 100), F(ri(80, 99), 100)
    series = p1 * p2 * p3
    parallel = 1 - (1 - p1) * (1 - p2) * (1 - p3)
    return (f"Three independent components work with probabilities {float(p1)}, {float(p2)} and {float(p3)}. What's the probability the system works if they're wired in series (all must work), and in parallel (any one suffices)?",
            f"Series: {float(p1)} x {float(p2)} x {float(p3)} = **{float(series):.4f}**.\n\nParallel: 1 - (1 - {float(p1)})(1 - {float(p2)})(1 - {float(p3)}) = 1 - {float((1 - p1) * (1 - p2) * (1 - p3)):.6f} = **{float(parallel):.6f}**.\n\nSeries wiring is less reliable than any single part; parallel is more reliable than any single part.")


@shape(1, "estimation", "medium")
def order_of_magnitude():
    a, b = ri(2, 9) * 10 ** ri(3, 6), ri(2, 9) * 10 ** ri(2, 5)
    return (f"Estimate {a:,} x {b:,} to the nearest power of ten, then give the exact product.",
            f"Round each to one significant figure: {a:.1e} x {b:.1e} is about 10^{math.floor(math.log10(a * b))} (order of magnitude).\n\nExact: **{a * b:,}**, which is {a * b:.2e} in scientific notation.")


@shape(1, "estimation", "easy")
def fermi_breaths():
    rate = ri(12, 20)
    return (f"If someone breathes {rate} times a minute, roughly how many breaths is that in a day and a year?",
            f"Per day: {rate} x 60 x 24 = **{rate * 1440:,}** breaths. Per year (365 days): {rate * 1440 * 365:,}, about **{rate * 1440 * 365 / 1e6:.1f} million**.")


@shape(1, "estimation", "medium")
def fermi_stack():
    th = pick([0.1, 0.12])
    n = ri(500, 5000)
    return (f"A sheet of paper is about {th} mm thick. How tall is a stack of {n:,} sheets?",
            f"{n:,} x {th} mm = {n * th:,.0f} mm = **{n * th / 10:,.0f} cm**, or {n * th / 1000:.2f} m. A ream of 500 sheets is {500 * th / 10:.1f} cm, which matches what you see on a shelf.")


@shape(1, "units", "easy")
def temperature_conversions():
    f = ri(-20, 110)
    c = (f - 32) * 5 / 9
    return (f"Convert {f} F to Celsius and Kelvin.",
            f"C = (F - 32) x 5/9 = ({f} - 32) x 5/9 = **{c:.1f} C**.\n\nKelvin = C + 273.15 = **{c + 273.15:.2f} K**.")


@shape(1, "units", "medium")
def recipe_volume_units():
    cups = pick([1.5, 2, 2.5, 3, 0.75])
    return (f"A recipe needs {cups} US cups of milk. How many millilitres and how many tablespoons is that? (1 US cup = 236.588 mL = 16 tbsp.)",
            f"Millilitres: {cups} x 236.588 = **{cups * 236.588:.0f} mL**.\n\nTablespoons: {cups} x 16 = **{cups * 16:g} tbsp**.")


@shape(1, "units", "medium")
def density_pop():
    pop, area = ri(2, 90) * 100000, ri(200, 9000)
    return (f"A city of {pop:,} people covers {area:,} km2. What is the population density, and how much space is that per person in square metres?",
            f"Density = {pop:,} / {area:,} = **{pop / area:.1f} people per km2**.\n\nSpace per person = {area:,} km2 x 1,000,000 m2/km2 / {pop:,} = **{area * 1e6 / pop:,.0f} m2** each.")


@shape(1, "logic_arith", "medium")
def age_digits_swap():
    a = ri(2, 8)
    b = a + ri(1, 9 - a)
    n = 10 * a + b
    return (f"A two-digit number's digits differ by {b - a}, and swapping them gives a number {9 * (b - a)} more than the original. If the tens digit is {a}, what is the number?",
            f"The tens digit is {a} and the digits differ by {b - a}, so the units digit is {a} + {b - a} = {b}. The number is **{n}**.\n\nCheck: swapping gives {10 * b + a}, and {10 * b + a} - {n} = {9 * (b - a)}.")


@shape(1, "logic_arith", "easy")
def change_making():
    price = F(ri(100, 1999), 100)
    paid = (int(price) + 1 + ri(0, 5))
    chg = paid - price
    coins = []
    c = round(chg * 100)
    for v, nm in [(100, "$1"), (25, "25c"), (10, "10c"), (5, "5c"), (1, "1c")]:
        if c // v:
            coins.append(f"{c // v} x {nm}")
            c %= v
    return (f"I pay with a ${paid} note for something costing {money(price)}. What change do I get, and what's the fewest coins to make it using $1, 25c, 10c, 5c and 1c?",
            f"Change = ${paid} - {money(price)} = **{money(chg)}**.\n\nGreedy coin choice (which is optimal for these denominations): {', '.join(coins) if coins else 'none'}.")


@shape(1, "logic_arith", "medium")
def two_trains_meeting():
    d, v1, v2 = ri(200, 900), ri(40, 90), ri(50, 110)
    t = F(d, v1 + v2)
    return (f"Two stations are {d} km apart. Trains leave each toward the other at the same moment at {v1} km/h and {v2} km/h. When do they meet, and how far from the first station?",
            f"They close the gap at {v1} + {v2} = {v1 + v2} km/h.\n\nTime = {d} / {v1 + v2} = **{float(t):.3f} h** ({float(t) * 60:.0f} minutes).\n\nDistance from the first station = {v1} x {float(t):.3f} = **{v1 * float(t):.1f} km**.")


@shape(1, "logic_arith", "medium")
def pipe_inflow_outflow():
    a, b, c = pick([(6, 4, 12), (10, 15, 30), (5, 8, 20)])
    net = F(1, a) + F(1, b) - F(1, c)
    return (f"Two taps fill a pool in {a} h and {b} h respectively, while a drain empties a full pool in {c} h. If all three are open, how long to fill the pool?",
            f"Net rate = 1/{a} + 1/{b} - 1/{c} = {frac(net)} pool per hour.\n\nTime = **{frac(1 / net)} h** (about {float(1 / net):.2f} h).")


@shape(1, "reasoning_math", "medium")
def check_triangle_inequality():
    a, b, c = ri(2, 12), ri(2, 12), ri(2, 25)
    ok = a + b > c and a + c > b and b + c > a
    return (f"Can a triangle have sides {a}, {b} and {c}?",
            f"The triangle inequality says each side must be less than the sum of the other two. Check the largest: the largest side is {max(a, b, c)} and the other two sum to {a + b + c - max(a, b, c)}.\n\n" + ("It works, so **yes**, such a triangle exists." if ok else f"{max(a, b, c)} is not less than {a + b + c - max(a, b, c)}, so **no**, the sides can't close into a triangle."))


@shape(1, "reasoning_math", "medium")
def odd_one_out_numbers():
    base = pick([3, 4, 5, 6, 7])
    seq = [base * k for k in range(2, 7)]
    odd_pos = ri(0, 4)
    bad = seq[odd_pos] + ri(1, base - 1)
    seq2 = seq[:]
    seq2[odd_pos] = bad
    return (f"Which number doesn't belong: {', '.join(map(str, seq2))}?",
            f"Look for the shared property. Every number except one is a multiple of {base}.\n\n**{bad}** is the odd one out, since {bad} / {base} = {bad / base:.2f} is not a whole number. (The multiples would be {', '.join(map(str, seq))}.)")


@shape(1, "reasoning_math", "hard")
def sum_of_digits_pattern():
    n = ri(3, 8)
    return (f"What is the digit sum of 10^{n} - 1, and why? Also what is it for 10^{n} - 2?",
            f"10^{n} - 1 is {n} nines: {'9' * n}, so the digit sum is 9 x {n} = **{9 * n}**.\n\n10^{n} - 2 = {10 ** n - 2}: the digits are {'9' * (n - 1)}8, so the sum is 9 x {n - 1} + 8 = **{9 * (n - 1) + 8}**.")


@shape(1, "reasoning_math", "medium")
def scale_model():
    k = pick([20, 24, 48, 72, 100])
    h = ri(2, 9) * 10
    return (f"A model is built at 1:{k} scale. The real building is {h} m tall. How tall is the model in centimetres, and how many times smaller is its floor area than the real thing?",
            f"Model height = {h} m / {k} = {h / k:.3f} m = **{h * 100 / k:.1f} cm**.\n\nAreas scale with the square: the model's floor area is 1/{k}^2 = 1/{k * k} of the real one, so it is **{k * k:,} times smaller**.")


@shape(1, "reasoning_math", "medium")
def rank_by_value():
    items = [("A", F(ri(1, 9), ri(2, 9))), ("B", F(ri(1, 9), ri(2, 9))), ("C", F(ri(1, 9), ri(2, 9))), ("D", F(ri(1, 9), ri(2, 9)))]
    order = sorted(items, key=lambda t: t[1])
    return (f"Order these fractions from smallest to largest: " + ", ".join(f"{n} = {frac(v)}" for n, v in items) + ".",
            "Convert each to a decimal: " + ", ".join(f"{n} = {float(v):.4f}" for n, v in items) + f".\n\nSmallest to largest: **{' < '.join(f'{n} ({frac(v)})' for n, v in order)}**.")


@shape(1, "reasoning_math", "medium")
def convert_recurring():
    d = ri(1, 8)
    return (f"Express 0.{d}{d}{d}... (repeating) as a fraction.",
            f"Let x = 0.{d}{d}{d}...; then 10x = {d}.{d}{d}{d}..., and subtracting gives 9x = {d}, so x = **{frac(F(d, 9))}**.")


@shape(1, "reasoning_math", "easy")
def round_vs_truncate():
    x = round(R.uniform(2, 99), 3)
    return (f"What is {x} rounded to the nearest whole number, to one decimal place, and truncated to one decimal place?",
            f"- Nearest whole number: **{round(x)}**\n- One decimal place (rounded): **{round(x, 1)}**\n- Truncated to one decimal place: **{math.trunc(x * 10) / 10}**\n\nRounding looks at the next digit, truncating just cuts the rest off; the two differ whenever the dropped digits are 5 or more.")


@shape(1, "reasoning_math", "medium")
def speed_unit_sanity():
    km, mins = ri(5, 60), ri(10, 90)
    return (f"A cyclist covers {km} km in {mins} minutes. Express the speed in km/h and in m/s, and say whether that is plausible for a commuter.",
            f"km/h = {km} / ({mins}/60) = **{km * 60 / mins:.1f} km/h**. In m/s divide by 3.6: **{km * 60 / mins / 3.6:.2f} m/s**.\n\n" + ("That's a normal commuting pace (roughly 15 to 25 km/h)." if 10 <= km * 60 / mins <= 28 else "That's outside a typical commuting pace (roughly 15 to 25 km/h), so double-check the numbers."))


@shape(1, "reasoning_math", "medium")
def pairwise_differences():
    xs = sorted(ri(1, 30) for _ in range(5))
    diffs = [b - a for a, b in zip(xs, xs[1:])]
    return (f"For the sorted list {', '.join(map(str, xs))}, find the gaps between neighbours and the largest gap.",
            f"Gaps: " + ", ".join(f"{b} - {a} = {b - a}" for a, b in zip(xs, xs[1:])) + f".\n\nThe largest gap is **{max(diffs)}**, between {xs[diffs.index(max(diffs))]} and {xs[diffs.index(max(diffs)) + 1]}. The gaps sum to {sum(diffs)}, which equals the range {xs[-1]} - {xs[0]}.")


@shape(1, "reasoning_math", "medium")
def reciprocal_sum():
    a, b = ri(2, 9), ri(10, 20)
    return (f"Find 1/{a} + 1/{b} as a single fraction in lowest terms, and compare it to 1/{a}.",
            f"Common denominator {a * b}: ({b} + {a})/{a * b} = {a + b}/{a * b} = **{frac(F(1, a) + F(1, b))}** (about {float(F(1, a) + F(1, b)):.4f}).\n\nIt's larger than 1/{a} = {1 / a:.4f}, by exactly 1/{b}.")
