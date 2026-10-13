"""Phase 2.1 maths batch B: algebra, geometry, statistics, calculus, logs/exponents, matrices, trigonometry. All numbers computed."""
import math
import statistics as st
from fractions import Fraction as F
from gen21_math_common import *


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


# ----------------------------- algebra -----------------------------
@shape(2, "algebra", "easy")
def isolate_variable():
    a, b, c, d = ri(2, 9), ri(1, 15), ri(10, 40), ri(2, 9)
    while a == d:
        d = ri(2, 9)
    x = F(c - b, a - d)
    return (f"Solve for x: {a}x + {b} = {d}x + {c}.",
            f"Move the x terms to one side and the constants to the other.\n\n{a}x - {d}x = {c} - {b}\n{a - d}x = {c - b}\nx = **{frac(x)}**" + ("" if x.denominator == 1 else f" (about {float(x):.3f})") + f".\n\nCheck: left = {a}({frac(x)}) + {b} = {frac(a * x + b)}, right = {d}({frac(x)}) + {c} = {frac(d * x + c)}.")


@shape(2, "algebra", "medium")
def fractional_equation():
    a, b = ri(2, 6), ri(2, 6)
    while a == b:
        b = ri(2, 6)
    k = ri(3, 9)
    x = F(k * a * b, a + b)
    return (f"Solve: x/{a} + x/{b} = {k}.",
            f"Multiply through by {a * b} (the common denominator): {b}x + {a}x = {k * a * b}.\n\n{a + b}x = {k * a * b}, so x = **{frac(x)}**.\n\nCheck: {frac(x / a)} + {frac(x / b)} = {frac(x / a + x / b)}.")


@shape(2, "algebra", "medium")
def quadratic_roots_nonint():
    p, q = ri(1, 7), ri(-9, 9)
    c = ri(-12, -1)
    disc = q * q - 4 * p * c
    r = math.sqrt(disc)
    return (f"Find the roots of {poly([p, q, c])} = 0, to 3 decimal places.",
            f"Quadratic formula with a = {p}, b = {q}, c = {c}:\n\ndiscriminant = b^2 - 4ac = {q * q} - ({4 * p * c}) = {disc}\n\nx = (-b +/- sqrt({disc})) / 2a = ({-q} +/- {r:.4f}) / {2 * p}\n\nx1 = **{(-q + r) / (2 * p):.3f}**, x2 = **{(-q - r) / (2 * p):.3f}**" + (" (exact integers)" if r == int(r) else "."))


@shape(2, "algebra", "medium")
def complete_square():
    b = pick([-8, -6, -4, 2, 4, 6, 10])
    c = ri(-10, 10)
    h = F(b, 2)
    k = c - h * h
    sg = lambda v: ("+ " if v >= 0 else "- ") + frac(abs(v))
    return (f"Write x^2 {sg(b)}x {sg(c)} in the form (x - h)^2 + k, and state the vertex.",
            f"Half of the x coefficient is {frac(h)}, and its square is {frac(h * h)}. Add and subtract it:\n\nx^2 {sg(b)}x + {frac(h * h)} - {frac(h * h)} {sg(c)} = (x {sg(h)})^2 {sg(k)}\n\nSo h = {frac(-h)}, k = {frac(k)}, and the vertex is **({frac(-h)}, {frac(k)})**. Because the coefficient of x^2 is positive, that is the minimum point.")


@shape(2, "algebra", "medium")
def inequality_solve():
    a, b, c = ri(2, 8), ri(1, 20), ri(25, 70)
    x = F(c - b, a)
    flip = ri(0, 1)
    if flip:
        a = -a
        x = F(c - b, a)
    return (f"Solve the inequality {a}x + {b} {'<' if True else '>'} {c} and describe the solution set.",
            f"Subtract {b}: {a}x < {c - b}.\n\n" + (f"Divide by {a}. Dividing by a negative number flips the inequality, so x > {frac(x)}." if a < 0 else f"Divide by {a} (positive, so the inequality keeps its direction): x < {frac(x)}.") + f"\n\nSolution set: **x {'>' if a < 0 else '<'} {frac(x)}**.")


@shape(2, "algebra", "hard")
def three_unknowns_sum():
    x, y, z = ri(1, 9), ri(1, 9), ri(1, 9)
    p_, q_ = y - x, z - 2 * x          # y = x + p_ ,  z = 2x + q_
    sg = lambda v: ("+ " if v >= 0 else "- ") + str(abs(v))
    S = x + y + z
    return (f"Three numbers satisfy x + y + z = {S}, y = x {sg(p_)}, and z = 2x {sg(q_)}. Find x, y and z.",
            f"Substitute the expressions for y and z into the sum:\n\nx + (x {sg(p_)}) + (2x {sg(q_)}) = {S}\n4x {sg(p_ + q_)} = {S}\n4x = {S - p_ - q_}\n\nSo x = **{x}**, then y = {x} {sg(p_)} = **{y}** and z = 2({x}) {sg(q_)} = **{z}**. Check: {x} + {y} + {z} = {S}.")


@shape(2, "algebra", "medium")
def exponent_rules():
    a, b, c = ri(2, 6), ri(2, 7), ri(1, 4)
    base = pick([2, 3, 5])
    e = a + b - c
    return (f"Simplify {base}^{a} x {base}^{b} / {base}^{c} and give the value.",
            f"Multiplying powers with the same base adds exponents; dividing subtracts them: {base}^({a} + {b} - {c}) = {base}^{e}.\n\n{base}^{e} = **{base ** e:,}**.")


@shape(2, "algebra", "medium")
def log_solve():
    b = pick([2, 3, 5, 10])
    n = ri(3, 8)
    v = b ** n
    return (f"Solve log_{b}(x) = {n}, then evaluate log_{b}({v}) - log_{b}({b ** (n - 2)}).",
            f"log_{b}(x) = {n} means x = {b}^{n} = **{v:,}**.\n\nlog_{b}({v}) = {n} and log_{b}({b ** (n - 2)}) = {n - 2}, so the difference is **2**. (Equivalently log of the quotient: log_{b}({v} / {b ** (n - 2)}) = log_{b}({b ** 2}) = 2.)")


@shape(2, "algebra", "medium")
def exp_growth_time():
    r = pick([3, 5, 7, 10])
    mult = pick([2, 3, 5, 10])
    t = math.log(mult) / math.log(1 + r / 100)
    return (f"At {r}% annual growth, how many years until a quantity multiplies by {mult}?",
            f"Solve {1 + r / 100}^t = {mult}. Taking logs: t = ln {mult} / ln {1 + r / 100} = {math.log(mult):.4f} / {math.log(1 + r / 100):.4f} = **{t:.1f} years**.")


@shape(2, "algebra", "medium")
def function_composition():
    a, b, c, d = ri(2, 5), ri(-4, 6), ri(2, 4), ri(-5, 5)
    x0 = ri(-3, 4)
    fx = lambda x: a * x + b
    gx = lambda x: c * x * x + d
    return (f"Let f(x) = {poly([a, b])} and g(x) = {poly([c, 0, d])}. Find f(g({x0})) and g(f({x0})).",
            f"g({x0}) = {c}({x0})^2 + ({d}) = {gx(x0)}, so f(g({x0})) = f({gx(x0)}) = {a}({gx(x0)}) + ({b}) = **{fx(gx(x0))}**.\n\nf({x0}) = {fx(x0)}, so g(f({x0})) = g({fx(x0)}) = {c}({fx(x0)})^2 + ({d}) = **{gx(fx(x0))}**.\n\nThe two differ, which shows composition is not commutative.")


@shape(2, "algebra", "hard")
def absolute_value_eq():
    c, k = ri(1, 8), ri(2, 9)
    r = ri(3, 12)
    return (f"Solve |{k}x - {c}| = {r}.",
            f"An absolute value equation splits into two cases.\n\n{k}x - {c} = {r}  =>  x = {frac(F(r + c, k))}\n{k}x - {c} = -{r}  =>  x = {frac(F(c - r, k))}\n\nSolutions: **x = {frac(F(r + c, k))}** or **x = {frac(F(c - r, k))}**.")


# ----------------------------- geometry -----------------------------
@shape(2, "geometry", "easy")
def pythagoras_ladder():
    triples = [(3, 4, 5), (5, 12, 13), (8, 15, 17), (7, 24, 25), (20, 21, 29)]
    a, b, c = pick(triples)
    k = ri(1, 3)
    return (f"A {c * k} m ladder leans against a wall with its foot {a * k} m from the wall. How high up the wall does it reach?",
            f"The ladder, wall and ground form a right triangle with hypotenuse {c * k} m.\n\nheight = sqrt({c * k}^2 - {a * k}^2) = sqrt({c * c * k * k} - {a * a * k * k}) = sqrt({b * b * k * k}) = **{b * k} m**.")


@shape(2, "geometry", "medium")
def circle_sector():
    r, ang = ri(3, 12), pick([30, 45, 60, 72, 90, 120, 150])
    arc = 2 * math.pi * r * ang / 360
    area = math.pi * r * r * ang / 360
    return (f"A circular sector has radius {r} cm and central angle {ang} degrees. Find the arc length and the sector area.",
            f"The sector is {ang}/360 = {frac(F(ang, 360))} of a full circle.\n\nArc length = {frac(F(ang, 360))} x 2 x pi x {r} = **{arc:.2f} cm**.\nArea = {frac(F(ang, 360))} x pi x {r}^2 = **{area:.2f} cm2**.")


@shape(2, "geometry", "medium")
def cylinder_capacity():
    r, h = ri(2, 9), ri(5, 25)
    v = math.pi * r * r * h
    return (f"A cylindrical tank has an inside radius of {r} dm and is {h} dm tall. How many litres does it hold? (1 dm3 = 1 litre.)",
            f"Volume = pi r^2 h = pi x {r}^2 x {h} = pi x {r * r * h} = {v:.1f} dm3.\n\nThat's **{v:,.0f} litres** (rounded to the nearest litre).")


@shape(2, "geometry", "medium")
def triangle_area_sides():
    a, b, c = pick([(13, 14, 15), (7, 8, 9), (9, 10, 17), (5, 6, 7), (10, 13, 13), (6, 25, 29)])
    s = (a + b + c) / 2
    area = math.sqrt(s * (s - a) * (s - b) * (s - c))
    return (f"Find the area of a triangle with sides {a}, {b} and {c}.",
            f"Heron's formula: s = ({a} + {b} + {c})/2 = {num(s)}.\n\nArea = sqrt(s(s-a)(s-b)(s-c)) = sqrt({num(s)} x {num(s - a)} x {num(s - b)} x {num(s - c)}) = sqrt({num(s * (s - a) * (s - b) * (s - c))}) = **{area:.2f}**.")


@shape(2, "geometry", "medium")
def similar_triangles_shadow():
    h, sh, ts = ri(15, 20) / 10, ri(20, 40) / 10, ri(60, 200) / 10
    th = ts * h / sh
    return (f"A {h} m person casts a {sh} m shadow at the same moment a tree casts a {ts} m shadow. How tall is the tree?",
            f"Sunlight makes similar right triangles, so height / shadow is the same for both.\n\n{h} / {sh} = tree / {ts}, so tree = {ts} x {h} / {sh} = **{th:.2f} m**.")


@shape(2, "geometry", "easy")
def polygon_angles():
    n = ri(5, 12)
    return (f"What is the sum of the interior angles of a {n}-sided polygon, and each angle if it is regular?",
            f"Sum = (n - 2) x 180 = ({n} - 2) x 180 = **{(n - 2) * 180} degrees**.\n\nEach interior angle of a regular {n}-gon = {(n - 2) * 180} / {n} = **{num(F((n - 2) * 180, n))} degrees**, and each exterior angle is 360/{n} = {num(F(360, n))}.")


@shape(2, "geometry", "medium")
def distance_midpoint():
    x1, y1, x2, y2 = ri(-6, 6), ri(-6, 6), ri(-6, 8), ri(-6, 8)
    d = math.hypot(x2 - x1, y2 - y1)
    return (f"Find the distance between ({x1}, {y1}) and ({x2}, {y2}), and the midpoint of the segment.",
            f"Distance = sqrt(({x2} - {x1})^2 + ({y2} - {y1})^2) = sqrt({(x2 - x1) ** 2} + {(y2 - y1) ** 2}) = sqrt({(x2 - x1) ** 2 + (y2 - y1) ** 2}) = **{d:.3f}**.\n\nMidpoint = (({x1} + {x2})/2, ({y1} + {y2})/2) = **({frac(F(x1 + x2, 2))}, {frac(F(y1 + y2, 2))})**.")


@shape(2, "geometry", "medium")
def line_through_points():
    x1, y1, x2, y2 = ri(-4, 4), ri(-5, 5), ri(5, 9), ri(-5, 9)
    m = F(y2 - y1, x2 - x1)
    b = y1 - m * x1
    return (f"Find the equation of the line through ({x1}, {y1}) and ({x2}, {y2}) in y = mx + b form.",
            f"Slope m = ({y2} - {y1}) / ({x2} - {x1}) = {frac(m)}.\n\nUse b = y - mx at the first point: b = {y1} - ({frac(m)})({x1}) = {frac(b)}.\n\n**y = {frac(m)}x {'+' if b >= 0 else '-'} {frac(abs(b))}**. Check with the second point: {frac(m)} x {x2} {'+' if b >= 0 else '-'} {frac(abs(b))} = {frac(m * x2 + b)}.")


@shape(2, "trigonometry", "medium")
def trig_height():
    d, ang = ri(20, 80), pick([20, 25, 30, 35, 40, 50, 60])
    h = d * math.tan(math.radians(ang))
    return (f"From {d} m away from the base of a building, the angle of elevation to the top is {ang} degrees. How tall is the building (ignoring eye height)?",
            f"tan(angle) = opposite / adjacent, so height = {d} x tan({ang} deg) = {d} x {math.tan(math.radians(ang)):.4f} = **{h:.1f} m**.")


@shape(2, "trigonometry", "medium")
def law_of_cosines():
    a, b, C = ri(5, 15), ri(5, 15), pick([40, 55, 70, 100, 120])
    c = math.sqrt(a * a + b * b - 2 * a * b * math.cos(math.radians(C)))
    return (f"Two sides of a triangle are {a} and {b}, with the angle between them {C} degrees. Find the third side.",
            f"Law of cosines: c^2 = a^2 + b^2 - 2ab cos C = {a * a} + {b * b} - 2({a})({b})cos({C} deg).\n\ncos({C} deg) = {math.cos(math.radians(C)):.4f}, so c^2 = {c * c:.2f} and **c = {c:.2f}**.")


@shape(2, "trigonometry", "easy")
def degrees_radians():
    d = pick([30, 45, 60, 90, 120, 135, 150, 210, 270, 300])
    f = F(d, 180)
    return (f"Convert {d} degrees to radians, and give sin and cos of that angle to 3 decimals.",
            f"Radians = degrees x pi/180 = {d}/180 x pi = **{frac(f)}pi** (about {float(f) * math.pi:.4f}).\n\nsin = {math.sin(math.radians(d)):.3f}, cos = {math.cos(math.radians(d)):.3f}.")


# ----------------------------- statistics -----------------------------
@shape(2, "statistics", "easy")
def mean_median_outlier():
    xs = [ri(40, 90) for _ in range(ri(6, 9))]
    out = ri(200, 400)
    ys = xs + [out]
    return (f"Test scores are {', '.join(map(str, xs))}. A late paper arrives scoring {out}. How do the mean and median change when it's included?",
            f"Before: mean = {sum(xs)}/{len(xs)} = {sum(xs) / len(xs):.2f}, median = {num(st.median(xs))}.\nAfter: mean = {sum(ys)}/{len(ys)} = {sum(ys) / len(ys):.2f}, median = {num(st.median(ys))}.\n\nThe mean moved by {sum(ys) / len(ys) - sum(xs) / len(xs):+.2f} while the median moved by {st.median(ys) - st.median(xs):+.1f}: the median is more resistant to an extreme value.")


@shape(2, "statistics", "medium")
def std_dev_manual():
    xs = [ri(2, 20) for _ in range(ri(5, 7))]
    m = sum(xs) / len(xs)
    sp = st.pstdev(xs)
    ss = st.stdev(xs)
    return (f"Compute the population and sample standard deviation of {', '.join(map(str, xs))}.",
            f"Mean = {sum(xs)}/{len(xs)} = {m:.3f}.\nSum of squared deviations = {sum((x - m) ** 2 for x in xs):.3f}.\n\n- Population SD = sqrt({sum((x - m) ** 2 for x in xs):.3f} / {len(xs)}) = **{sp:.3f}**\n- Sample SD = sqrt({sum((x - m) ** 2 for x in xs):.3f} / {len(xs) - 1}) = **{ss:.3f}**\n\nUse the sample version when the numbers are a sample from a larger population.")


@shape(2, "statistics", "medium")
def weighted_average_grade():
    w = [pick([20, 30, 40]), pick([10, 20, 30])]
    w.append(100 - sum(w))
    g = [ri(55, 98) for _ in range(3)]
    tot = sum(a * b for a, b in zip(w, g)) / 100
    return (f"A course grade is {w[0]}% coursework, {w[1]}% midterm and {w[2]}% final. Scores are {g[0]}, {g[1]} and {g[2]}. What's the final grade?",
            f"Weighted average = ({w[0]} x {g[0]} + {w[1]} x {g[1]} + {w[2]} x {g[2]}) / 100 = {sum(a * b for a, b in zip(w, g))} / 100 = **{tot:.2f}**.")


@shape(2, "statistics", "medium")
def needed_score():
    n = ri(3, 6)
    sc = [ri(60, 90) for _ in range(n)]
    tgt = ri(max(sc) - 5, 92)
    need = tgt * (n + 1) - sum(sc)
    return (f"My {n} scores so far are {', '.join(map(str, sc))}. What score do I need on the next one to average {tgt}?",
            f"Total needed over {n + 1} scores = {tgt} x {n + 1} = {tgt * (n + 1)}. I have {sum(sc)}.\n\nNeeded = {tgt * (n + 1)} - {sum(sc)} = **{need}**" + ("" if need <= 100 else f", which is above 100, so with a maximum of 100 the target isn't reachable") + ".")


@shape(2, "statistics", "medium")
def z_score():
    mu, sd = ri(60, 100), ri(5, 15)
    x = mu + ri(-2, 3) * sd + ri(0, sd - 1)
    z = (x - mu) / sd
    return (f"Exam scores have mean {mu} and standard deviation {sd}. How many standard deviations from the mean is a score of {x}?",
            f"z = (x - mean) / SD = ({x} - {mu}) / {sd} = **{z:.2f}**. " + ("That's above average." if z > 0 else "That's below average." if z < 0 else "Exactly average."))


@shape(2, "statistics", "medium")
def quartiles_range():
    xs = sorted(ri(1, 60) for _ in range(ri(9, 13)))
    q = st.quantiles(xs, n=4, method="inclusive")
    return (f"Find the range, median and interquartile range of: {', '.join(map(str, xs))}. (Use the inclusive quartile method.)",
            f"Range = {xs[-1]} - {xs[0]} = **{xs[-1] - xs[0]}**.\nMedian = **{num(st.median(xs))}**.\nQ1 = {num(q[0])}, Q3 = {num(q[2])}, so IQR = **{num(q[2] - q[0])}**.\n\nA common outlier rule flags values beyond Q1 - 1.5 x IQR = {num(q[0] - 1.5 * (q[2] - q[0]))} or Q3 + 1.5 x IQR = {num(q[2] + 1.5 * (q[2] - q[0]))}.")


@shape(2, "statistics", "medium")
def correlation_simple():
    n = 6
    xs = list(range(1, n + 1))
    ys = [2 * x + ri(-2, 2) for x in xs]
    mx, my = sum(xs) / n, sum(ys) / n
    r = sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / math.sqrt(sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys))
    m = sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / sum((a - mx) ** 2 for a in xs)
    return (f"Hours studied: {', '.join(map(str, xs))}. Marks: {', '.join(map(str, ys))}. Compute the correlation coefficient and the least-squares slope.",
            f"Means: x = {mx:.2f}, y = {my:.2f}.\n\nCorrelation r = Sxy / sqrt(Sxx Syy) = **{r:.3f}**, a {'strong' if abs(r) > 0.8 else 'moderate'} positive relationship.\nSlope = Sxy / Sxx = **{m:.3f}** marks per extra hour.\n\nCorrelation shows association, not that studying causes the marks to rise.")


# ----------------------------- calculus -----------------------------
@shape(2, "calculus", "medium")
def product_rule():
    a, b = ri(1, 4), ri(1, 5)
    x0 = ri(1, 3)
    f = lambda x: x ** a * (x + b)
    d = a * x0 ** (a - 1) * (x0 + b) + x0 ** a
    return (f"Use the product rule to differentiate f(x) = x^{a}(x + {b}) and evaluate f'({x0}).",
            f"Let u = x^{a} and v = x + {b}, so u' = {a}x^{a - 1} and v' = 1.\n\nf'(x) = u'v + uv' = {a}x^{a - 1}(x + {b}) + x^{a}.\n\nf'({x0}) = {a} x {x0 ** (a - 1)} x {x0 + b} + {x0 ** a} = **{d}**.\n\n(Expanding first gives the same answer: f(x) = x^{a + 1} + {b}x^{a}.)")


@shape(2, "calculus", "medium")
def chain_rule():
    a, b, n = ri(2, 5), ri(1, 6), ri(2, 5)
    x0 = ri(0, 2)
    d = n * (a * x0 + b) ** (n - 1) * a
    return (f"Differentiate g(x) = ({a}x + {b})^{n} and evaluate g'({x0}).",
            f"Chain rule: outer function u^{n}, inner function u = {a}x + {b}, with du/dx = {a}.\n\ng'(x) = {n}({a}x + {b})^{n - 1} x {a} = {n * a}({a}x + {b})^{n - 1}.\n\ng'({x0}) = {n * a} x ({a * x0 + b})^{n - 1} = **{d}**.")


@shape(2, "calculus", "medium")
def critical_points():
    a = ri(1, 3)
    r1, r2 = ri(-3, 0), ri(1, 4)
    # f'(x)=3a(x-r1)(x-r2) -> f = a x^3 - 1.5 a (r1+r2) x^2 + 3 a r1 r2 x
    A = a * 2
    B = -3 * a * (r1 + r2)
    C = 6 * a * r1 * r2
    f = lambda x: A * x ** 3 + B * x ** 2 + C * x
    return (f"Find the stationary points of f(x) = {poly([A, B, C, 0])} and classify them.",
            f"f'(x) = {poly([3 * A, 2 * B, C])} = {3 * a * 2}(x - {r1})(x - {r2}). Setting it to zero gives x = **{r1}** and x = **{r2}**.\n\nf''(x) = {poly([6 * A, 2 * B])}.\n- At x = {r1}: f'' = {6 * A * r1 + 2 * B} so it's a **local {'minimum' if 6 * A * r1 + 2 * B > 0 else 'maximum'}**, f = {f(r1)}.\n- At x = {r2}: f'' = {6 * A * r2 + 2 * B} so it's a **local {'minimum' if 6 * A * r2 + 2 * B > 0 else 'maximum'}**, f = {f(r2)}.")


@shape(2, "calculus", "medium")
def definite_integral():
    a, b, c = ri(1, 6), ri(0, 5), ri(1, 4)
    lo, hi = 0, ri(2, 5)
    val = F(a, 3) * hi ** 3 + F(b, 2) * hi ** 2 + c * hi
    return (f"Evaluate the integral of ({a}x^2 + {b}x + {c}) dx from {lo} to {hi}.",
            f"Antiderivative: F(x) = {frac(F(a, 3))}x^3 + {frac(F(b, 2))}x^2 + {c}x.\n\nF({hi}) - F({lo}) = {frac(val)} - 0 = **{frac(val)}**" + ("" if val.denominator == 1 else f" (about {float(val):.3f})") + ".")


@shape(2, "calculus", "hard")
def optimisation_fence():
    L = pick([40, 60, 80, 100, 120])
    return (f"You have {L} m of fencing to enclose a rectangular field against a straight wall, so only three sides need fence. What dimensions maximise the area?",
            f"Let the two sides perpendicular to the wall be x, so the side parallel to the wall is {L} - 2x. Area A = x({L} - 2x) = {L}x - 2x^2.\n\nA'(x) = {L} - 4x = 0 gives x = **{num(F(L, 4))} m**, and the parallel side is {L} - 2 x {num(F(L, 4))} = **{num(F(L, 2))} m**.\n\nA'' = -4 < 0, so it's a maximum. Maximum area = {num(F(L, 4))} x {num(F(L, 2))} = **{num(F(L * L, 8))} m2**.")


@shape(2, "calculus", "medium")
def simple_limits():
    a = ri(2, 6)
    return (f"Evaluate the limit as x approaches {a} of (x^2 - {a * a}) / (x - {a}).",
            f"Substituting x = {a} gives 0/0, so factor: x^2 - {a * a} = (x - {a})(x + {a}).\n\nCancel (x - {a}) for x not equal to {a}: the expression becomes x + {a}, and as x approaches {a} it approaches **{2 * a}**.")


@shape(2, "calculus", "medium")
def rate_of_change_story():
    a, b = ri(2, 6), ri(3, 12)
    t = ri(2, 5)
    return (f"A ball's height in metres after t seconds is h(t) = {b}t - {a}t^2. Find its velocity at t = {t}, the time it peaks, and the peak height.",
            f"Velocity is the derivative: h'(t) = {b} - {2 * a}t, so h'({t}) = {b} - {2 * a * t} = **{b - 2 * a * t} m/s** ({'upward' if b - 2 * a * t > 0 else 'downward'}).\n\nThe peak is where h'(t) = 0: t = {b}/{2 * a} = **{num(F(b, 2 * a))} s**. Peak height = {b}({num(F(b, 2 * a))}) - {a}({num(F(b, 2 * a))})^2 = **{num(F(b * b, 4 * a))} m**.")


# ----------------------------- matrices / vectors -----------------------------
@shape(2, "linear_algebra", "medium")
def matrix_multiply():
    A = [[ri(-3, 4) for _ in range(2)] for _ in range(2)]
    Bm = [[ri(-3, 4) for _ in range(2)] for _ in range(2)]
    C = [[sum(A[i][k] * Bm[k][j] for k in range(2)) for j in range(2)] for i in range(2)]
    D = [[sum(Bm[i][k] * A[k][j] for k in range(2)) for j in range(2)] for i in range(2)]
    s = lambda M: f"[{M[0][0]} {M[0][1]}; {M[1][0]} {M[1][1]}]"
    return (f"Multiply A = {s(A)} by B = {s(Bm)} (rows separated by semicolons), and check whether BA gives the same result.",
            f"Row-by-column: AB[i][j] = sum over k of A[i][k] x B[k][j].\n\nAB = {s(C)}\nBA = {s(D)}\n\n" + ("They happen to be equal here." if C == D else "AB and BA differ, so matrix multiplication is not commutative in general."))


@shape(2, "linear_algebra", "medium")
def determinant_area():
    a, b, c, d = ri(-4, 6), ri(-4, 6), ri(-4, 6), ri(-4, 6)
    det = a * d - b * c
    return (f"The vectors ({a}, {c}) and ({b}, {d}) are the sides of a parallelogram. Find its area using a determinant, and say whether the vectors are independent.",
            f"det = ({a})({d}) - ({b})({c}) = {a * d} - ({b * c}) = {det}.\n\nArea = |det| = **{abs(det)}**. " + ("The determinant is non-zero, so the vectors are linearly independent." if det else "The determinant is zero, so the vectors lie on one line and are dependent (the 'parallelogram' is flat)."))


@shape(2, "linear_algebra", "medium")
def dot_product_angle():
    u = [ri(-4, 6) for _ in range(3)]
    v = [ri(-4, 6) for _ in range(3)]
    dot = sum(a * b for a, b in zip(u, v))
    nu, nv = math.sqrt(sum(a * a for a in u)), math.sqrt(sum(a * a for a in v))
    if nu == 0 or nv == 0:
        return None
    ang = math.degrees(math.acos(max(-1, min(1, dot / (nu * nv)))))
    return (f"Find the dot product of u = {tuple(u)} and v = {tuple(v)} and the angle between them.",
            f"u . v = {' + '.join(f'({a})({b})' for a, b in zip(u, v))} = **{dot}**.\n\n|u| = {nu:.3f}, |v| = {nv:.3f}, so cos(theta) = {dot} / {nu * nv:.3f} = {dot / (nu * nv):.4f} and theta = **{ang:.1f} degrees**.")


# ----------------------------- sequences / series -----------------------------
@shape(2, "sequences", "medium")
def geometric_sum():
    a, r, n = ri(2, 6), pick([2, 3, F(1, 2)]), ri(4, 8)
    tot = a * (1 - F(r) ** n) / (1 - F(r))
    return (f"A geometric sequence starts at {a} with common ratio {frac(F(r))}. Find the {n}th term and the sum of the first {n} terms.",
            f"{n}th term = a r^(n-1) = {a} x {frac(F(r))}^{n - 1} = **{frac(a * F(r) ** (n - 1))}**.\n\nSum = a(1 - r^n)/(1 - r) = {a}(1 - {frac(F(r) ** n)})/(1 - {frac(F(r))}) = **{frac(tot)}**" + ("" if F(tot).denominator == 1 else f" (about {float(tot):.4f})") + ".")


@shape(2, "sequences", "medium")
def find_nth_pattern():
    a, d = ri(-5, 10), ri(2, 9)
    seq = [a + i * d for i in range(5)]
    n = ri(20, 60)
    return (f"Find a rule for the k-th term of the sequence {', '.join(map(str, seq))}, and what is term number {n}?",
            f"The differences are all {d}, so it's arithmetic with first term {a}: term k = {a} + {d}(k - 1) = {d}k + {a - d}.\n\nTerm {n} = {d} x {n} + {a - d} = **{d * n + a - d}**.")


@shape(2, "sequences", "medium")
def infinite_series():
    a, r = ri(2, 12), pick([F(1, 2), F(1, 3), F(2, 3), F(1, 4), F(3, 4)])
    return (f"Find the sum of the infinite series {a} + {frac(a * r)} + {frac(a * r * r)} + ... and explain when such a sum exists.",
            f"This is geometric with first term {a} and ratio {frac(r)}. An infinite geometric series converges only when |r| < 1; here |{frac(r)}| < 1.\n\nSum = a / (1 - r) = {a} / {frac(1 - r)} = **{frac(a / (1 - r))}**.")
