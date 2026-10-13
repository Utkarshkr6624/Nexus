"""Phase 2.1 maths batch E: science-flavoured applications, 3D geometry, more algebra/probability/statistics shapes, complex numbers, extra follow-ups."""
import cmath
import math
import statistics as st
from fractions import Fraction as F
from gen21_math_common import *


def _seq(a0, r, d, n):
    out = [a0]
    for _ in range(n):
        out.append(r * out[-1] + d)
    return out


# ----------------------------- applied science -----------------------------
@shape(2, "physics_math", "easy")
def ohms_law():
    v, r = ri(3, 24), pick([2, 4, 5, 8, 10, 12, 20])
    i = F(v, r)
    return (f"A {v} V supply drives a current through a {r} ohm resistor. Find the current and the power dissipated.",
            f"Ohm's law: I = V/R = {v}/{r} = **{num(i)} A**.\n\nPower P = V x I = {v} x {num(i)} = **{num(v * i)} W** (equivalently V^2/R = {v * v}/{r}).")


@shape(2, "physics_math", "medium")
def free_fall_time():
    h = ri(5, 80)
    t = math.sqrt(2 * h / 9.81)
    return (f"A stone is dropped from a {h} m high bridge. Ignoring air resistance (g = 9.81 m/s2), how long does it take to hit the water and how fast is it going?",
            f"h = (1/2) g t^2, so t = sqrt(2h/g) = sqrt(2 x {h} / 9.81) = sqrt({2 * h / 9.81:.3f}) = **{t:.2f} s**.\n\nSpeed v = g t = 9.81 x {t:.2f} = **{9.81 * t:.1f} m/s** (about {9.81 * t * 3.6:.0f} km/h).")


@shape(2, "physics_math", "medium")
def density_object():
    m, v = ri(50, 900), ri(20, 300)
    d = F(m, v)
    return (f"An object has mass {m} g and volume {v} cm3. What is its density, and will it float in water (1 g/cm3)?",
            f"Density = mass / volume = {m}/{v} = **{float(d):.2f} g/cm3**.\n\nIt is {'denser' if d > 1 else 'less dense'} than water, so it will **{'sink' if d > 1 else 'float'}** (assuming it is solid and not hollow).")


@shape(2, "physics_math", "medium")
def kinetic_energy():
    m, v = ri(2, 1500), ri(2, 40)
    return (f"How much kinetic energy does a {m} kg object moving at {v} m/s have? What happens to it if the speed doubles?",
            f"KE = (1/2) m v^2 = 0.5 x {m} x {v}^2 = **{0.5 * m * v * v:,.0f} J**.\n\nDoubling the speed multiplies KE by 4 (because of the square): {0.5 * m * (2 * v) ** 2:,.0f} J. This is why stopping distances grow so fast with speed.")


@shape(2, "physics_math", "medium")
def decibel_ratio():
    ratio = pick([2, 10, 100, 1000, 50])
    return (f"A sound has {ratio} times the power of another. How many decibels louder is it?",
            f"dB difference = 10 log10(power ratio) = 10 x log10({ratio}) = **{10 * math.log10(ratio):.1f} dB**.\n\nEvery doubling of power adds about 3 dB, and every factor of 10 adds exactly 10 dB.")


@shape(2, "physics_math", "medium")
def ph_concentration():
    ph = round(R.uniform(1.5, 12.5), 1)
    h = 10 ** (-ph)
    return (f"A solution has pH {ph}. What is the hydrogen-ion concentration, and is the solution acidic or basic?",
            f"pH = -log10[H+], so [H+] = 10^(-{ph}) = **{h:.2e} mol/L**.\n\n" + ("pH below 7 means acidic." if ph < 7 else "pH above 7 means basic (alkaline).") + f" A one-unit pH change is a tenfold change in concentration.")


@shape(2, "physics_math", "medium")
def molarity_dilution():
    c1, v1, v2 = ri(2, 12), ri(10, 100), 0
    v2 = v1 * ri(2, 6)
    return (f"I dilute {v1} mL of a {c1} mol/L solution with water to a final volume of {v2} mL. What is the new concentration?",
            f"The amount of solute is unchanged: C1 V1 = C2 V2.\n\nC2 = {c1} x {v1} / {v2} = **{num(F(c1 * v1, v2))} mol/L**. Moles of solute = {c1} x {v1}/1000 = {num(F(c1 * v1, 1000))} mol.")


@shape(2, "physics_math", "medium")
def speed_of_sound_distance():
    t = ri(2, 12)
    return (f"You see lightning and hear thunder {t} seconds later. Taking the speed of sound as 343 m/s, how far away was the strike?",
            f"Light arrives essentially instantly, so distance = 343 x {t} = **{343 * t:,} m**, about {343 * t / 1000:.2f} km. The classic rule of thumb is 3 seconds per kilometre: here {t}/3 = {t / 3:.1f} km.")


@shape(2, "physics_math", "hard")
def lever_balance():
    w1, d1, d2 = ri(10, 80), ri(1, 4), ri(2, 6)
    w2 = F(w1 * d1, d2)
    return (f"A seesaw balances when a {w1} kg child sits {d1} m from the pivot. How heavy must a child be to balance it from {d2} m on the other side?",
            f"Torques balance: {w1} x {d1} = w x {d2}.\n\nw = {w1 * d1} / {d2} = **{num(w2)} kg**. The heavier child sits closer to the pivot; moving further out increases the leverage.")


# ----------------------------- 3D geometry -----------------------------
@shape(2, "geometry_3d", "medium")
def cone_volume():
    r, h = ri(2, 9), ri(4, 15)
    v = math.pi * r * r * h / 3
    sl = math.hypot(r, h)
    return (f"A cone has base radius {r} cm and height {h} cm. Find its volume and slant height.",
            f"Volume = (1/3) pi r^2 h = (1/3) x pi x {r * r} x {h} = **{v:.1f} cm3**.\n\nSlant height = sqrt(r^2 + h^2) = sqrt({r * r} + {h * h}) = **{sl:.2f} cm**. Curved surface area would be pi r l = {math.pi * r * sl:.1f} cm2.")


@shape(2, "geometry_3d", "medium")
def sphere_scaling():
    r = ri(2, 10)
    k = pick([2, 3])
    v1 = 4 / 3 * math.pi * r ** 3
    return (f"A sphere has radius {r} cm. How does its volume and surface area change if the radius is multiplied by {k}?",
            f"Original: V = (4/3) pi r^3 = {v1:.1f} cm3, S = 4 pi r^2 = {4 * math.pi * r * r:.1f} cm2.\n\nWith radius {r * k}: V = {4 / 3 * math.pi * (r * k) ** 3:.1f} cm3 (x{k ** 3}), S = {4 * math.pi * (r * k) ** 2:.1f} cm2 (x{k * k}).\n\nVolume scales with the cube of the factor, area with the square.")


@shape(2, "geometry_3d", "medium")
def box_diagonal():
    a, b, c = ri(2, 12), ri(2, 12), ri(2, 12)
    d = math.sqrt(a * a + b * b + c * c)
    return (f"What is the length of the longest straight rod that fits inside a {a} x {b} x {c} cm box?",
            f"It's the space diagonal: sqrt(a^2 + b^2 + c^2) = sqrt({a * a} + {b * b} + {c * c}) = sqrt({a * a + b * b + c * c}) = **{d:.2f} cm**.")


@shape(2, "geometry_3d", "hard")
def melt_and_recast():
    R_, h = ri(3, 8), ri(6, 20)
    r = pick([1, 2])
    vol_cyl = math.pi * R_ ** 2 * h
    vol_sph = 4 / 3 * math.pi * r ** 3
    return (f"A solid metal cylinder of radius {R_} cm and height {h} cm is melted and recast into spheres of radius {r} cm. How many spheres can be made?",
            f"Volume is conserved. Cylinder: pi x {R_ * R_} x {h} = {vol_cyl:.1f} cm3. One sphere: (4/3) pi x {r ** 3} = {vol_sph:.2f} cm3.\n\nNumber = {vol_cyl / vol_sph:.2f}, so **{int(vol_cyl / vol_sph)} whole spheres** (with a bit of metal left over).")


# ----------------------------- algebra extras -----------------------------
@shape(2, "algebra", "medium")
def factorise_quadratic():
    r1, r2 = ri(-8, 8), ri(-8, 8)
    a = pick([1, 1, 2, 3])
    b = -a * (r1 + r2)
    c = a * r1 * r2
    return (f"Factorise {poly([a, b, c])} and find where it crosses the x-axis.",
            f"{'Take out the common factor ' + str(a) + ' first. ' if a > 1 else ''}Then look for two numbers that multiply to {r1 * r2} and add to {r1 + r2}: they are {r1} and {r2}, so the factors are (x - ({r1})) and (x - ({r2})).\n\n"
            f"{poly([a, b, c])} = {a if a > 1 else ''}(x {'-' if r1 > 0 else '+'} {abs(r1)})(x {'-' if r2 > 0 else '+'} {abs(r2)}).\n\nIt crosses the x-axis at **x = {r1}** and **x = {r2}**" + (" (a double root, so it just touches)." if r1 == r2 else "."))


@shape(2, "algebra", "medium")
def remainder_theorem():
    a, b, c = ri(1, 4), ri(-5, 5), ri(-6, 6)
    k = ri(-3, 3)
    rem = a * k ** 3 + b * k * k + c * k + 4
    return (f"Without long division, find the remainder when {poly([a, b, c, 4])} is divided by (x {'-' if k >= 0 else '+'} {abs(k)}).",
            f"Remainder theorem: the remainder equals the polynomial evaluated at x = {k}.\n\n{a}({k})^3 + ({b})({k})^2 + ({c})({k}) + 4 = {a * k ** 3} + {b * k * k} + {c * k} + 4 = **{rem}**.\n\nIf the result were 0, (x - {k}) would be a factor.")


@shape(2, "algebra", "medium")
def rationalise():
    a, b = ri(1, 5), pick([2, 3, 5, 7])
    return (f"Rationalise the denominator of {a}/sqrt({b}) and give a decimal value.",
            f"Multiply top and bottom by sqrt({b}): {a}/sqrt({b}) x sqrt({b})/sqrt({b}) = **{a}sqrt({b})/{b}**.\n\nDecimal: {a * math.sqrt(b) / b:.4f}.")


@shape(2, "algebra", "hard")
def two_variable_word():
    p, q = ri(2, 5), ri(3, 8)
    x, y = ri(2, 9), ri(2, 9)
    t1 = p * x + q * y
    t2 = (p + 1) * x + (q - 1) * y if q > 1 else p * x + y
    return (f"{p} notebooks and {q} pens cost ${t1}. {p + 1} notebooks and {q - 1} pens cost ${t2}. Find the price of each item.",
            f"Let n be a notebook and e a pen. Then {p}n + {q}e = {t1} and {p + 1}n + {q - 1}e = {t2}.\n\nSubtract the first from the second: n - e = {t2 - t1}. Substituting n = e + {t2 - t1} into the first: {p}(e + {t2 - t1}) + {q}e = {t1}, so {p + q}e = {t1 - p * (t2 - t1)} and e = **${(t1 - p * (t2 - t1)) // (p + q)}**.\n\nThen n = **${(t1 - p * (t2 - t1)) // (p + q) + t2 - t1}**.")


@shape(2, "algebra", "medium")
def scientific_notation():
    a, b = round(R.uniform(1.1, 9.9), 1), round(R.uniform(1.1, 9.9), 1)
    m, n = ri(3, 9), ri(2, 7)
    prod = a * b * 10 ** (m + n)
    return (f"Compute ({a} x 10^{m}) x ({b} x 10^{n}) and write the answer in scientific notation.",
            f"Multiply the coefficients and add the exponents: {a} x {b} = {a * b:.2f}, and 10^{m} x 10^{n} = 10^{m + n}.\n\n{a * b:.2f} x 10^{m + n}" + (f" = **{a * b / 10:.3f} x 10^{m + n + 1}** after normalising to one digit before the decimal point." if a * b >= 10 else " is already normalised, so the answer is **" + f"{a * b:.2f} x 10^{m + n}**."))


@shape(2, "algebra", "easy")
def complex_arithmetic():
    a, b, c, d = ri(-5, 6), ri(-5, 6), ri(-5, 6), ri(-5, 6)
    z, w = complex(a, b), complex(c, d)
    p = z * w
    return (f"Compute ({a} + {b}i)({c} + {d}i), where i^2 = -1.",
            f"Expand: {a}{c} + {a}({d})i + {b}({c})i + {b}({d})i^2 = {a * c} + {a * d + b * c}i + {b * d}(-1).\n\nReal part: {a * c} - {b * d} = {int(p.real)}. Imaginary part: {a * d + b * c}.\n\nResult: **{int(p.real)} {'+' if p.imag >= 0 else '-'} {abs(int(p.imag))}i**. Its modulus is {abs(p):.3f}, which equals |{a}+{b}i| x |{c}+{d}i| = {abs(z):.3f} x {abs(w):.3f}.")


@shape(2, "algebra", "hard")
def partial_fractions_sum():
    n = ri(3, 12)
    return (f"Find the sum 1/(1x2) + 1/(2x3) + ... + 1/({n}x{n + 1}).",
            f"Each term splits: 1/(k(k+1)) = 1/k - 1/(k+1), so the sum telescopes.\n\n(1 - 1/2) + (1/2 - 1/3) + ... + (1/{n} - 1/{n + 1}) = 1 - 1/{n + 1} = **{n}/{n + 1}**.")


@shape(2, "algebra", "medium")
def inverse_function():
    a, b = ri(2, 6), ri(-7, 8)
    x0 = ri(-3, 5)
    y0 = a * x0 + b
    return (f"Find the inverse of f(x) = {poly([a, b])}, and check it on f({x0}).",
            f"Write y = {a}x {'+' if b >= 0 else '-'} {abs(b)}, swap x and y, and solve: x = {a}y {'+' if b >= 0 else '-'} {abs(b)}, so y = (x {'-' if b >= 0 else '+'} {abs(b)})/{a}.\n\nf^(-1)(x) = **(x {'-' if b >= 0 else '+'} {abs(b)})/{a}**.\n\nCheck: f({x0}) = {y0}, and f^(-1)({y0}) = ({y0} {'-' if b >= 0 else '+'} {abs(b)})/{a} = {x0}.")


# ----------------------------- probability / statistics extras -----------------------------
@shape(2, "probability", "medium")
def expected_rolls():
    k = pick([1, 2, 3, 4])
    p = F(k, 6)
    return (f"How many rolls of a fair die do you expect to wait until you first get a value of {k} or lower? And what's the chance it happens on the first roll?",
            f"Each roll succeeds with probability p = {k}/6 = {frac(p)}. The number of rolls until the first success is geometric with mean 1/p = **{frac(1 / p)}** rolls.\n\nChance on the first roll = **{frac(p)}**, and the chance it takes more than 3 rolls is (1 - p)^3 = {float((1 - p) ** 3):.4f}.")


@shape(2, "probability", "medium")
def normal_percentile():
    mu, sd = ri(50, 100), ri(5, 15)
    x = mu + pick([-2, -1, 1, 2]) * sd
    z = (x - mu) / sd
    cdf = 0.5 * (1 + math.erf(z / math.sqrt(2)))
    return (f"Heights are roughly normal with mean {mu} and SD {sd}. What fraction of people are below {x}?",
            f"z = ({x} - {mu})/{sd} = {z:g}. Using the normal CDF, P(Z < {z:g}) = **{cdf:.4f}**, about {cdf * 100:.1f}%.\n\n(Reference points: about 68% lie within one SD of the mean, 95% within two.)")


@shape(2, "probability", "hard")
def expected_max_two_dice():
    n = pick([4, 6, 8, 10, 12, 20])
    ev = sum(F(m * (2 * m - 1), n * n) for m in range(1, n + 1))
    return (f"Roll two fair {n}-sided dice. What is the expected value of the larger roll?",
            f"P(max = m) = (2m - 1)/{n * n}, because there are 2m - 1 ordered outcomes whose larger value is m.\n\nE[max] = sum from m = 1 to {n} of m(2m - 1)/{n * n} = **{frac(ev)}** (about {float(ev):.3f}).")


@shape(2, "statistics", "medium")
def percentile_rank():
    xs = sorted(ri(30, 100) for _ in range(ri(12, 20)))
    v = xs[ri(2, len(xs) - 3)]
    below = sum(1 for x in xs if x < v)
    return (f"In the scores {', '.join(map(str, xs))}, what percentile is the score {v}?",
            f"Count the scores below {v}: {below} of {len(xs)}. Percentile rank = {below}/{len(xs)} x 100 = **{below / len(xs) * 100:.1f}**. (Some definitions add half of the ties; here there {'are' if xs.count(v) > 1 else 'is no'} tie{'s' if xs.count(v) > 1 else ''}.)")


@shape(2, "statistics", "medium")
def combined_mean_change():
    n = ri(5, 12)
    m = ri(60, 85)
    newv = ri(20, 100)
    nm = F(n * m + newv, n + 1)
    return (f"The mean of {n} numbers is {m}. A new number {newv} is added. What is the new mean?",
            f"Original total = {n} x {m} = {n * m}. New total = {n * m} + {newv} = {n * m + newv}.\n\nNew mean = {n * m + newv} / {n + 1} = **{float(nm):.2f}** ({'up' if nm > m else 'down'} by {abs(float(nm) - m):.2f}).")


@shape(2, "statistics", "medium")
def sample_size_margin():
    p, z = pick([0.5, 0.3, 0.2]), 1.96
    e = pick([0.03, 0.05, 0.02])
    n = z * z * p * (1 - p) / (e * e)
    return (f"A poll expects a proportion near {int(p * 100)}%. How many people must be surveyed for a 95% margin of error of +/-{int(e * 100)} points?",
            f"n = z^2 p(1 - p)/e^2 = {z}^2 x {p} x {1 - p:.1f} / {e}^2 = {n:.1f}.\n\nRound up: **{math.ceil(n)} respondents**. Halving the margin of error needs four times the sample.")


# ----------------------------- numbers / series extras -----------------------------
@shape(2, "number_theory", "medium")
def trailing_zeros():
    n = ri(25, 500)
    z, k = 0, 5
    while k <= n:
        z += n // k
        k *= 5
    return (f"How many trailing zeros does {n}! have?",
            f"Each trailing zero comes from a factor 10 = 2 x 5, and there are always more 2s than 5s, so count the 5s: {n}//5 + {n}//25 + {n}//125 + ... = " + " + ".join(str(n // 5 ** i) for i in range(1, 6) if 5 ** i <= n) + f" = **{z}**.")


@shape(2, "number_theory", "medium")
def prime_factor_divisors():
    n = pick([72, 84, 90, 120, 180, 210, 360, 504, 900])
    divs = [d for d in range(1, n + 1) if n % d == 0]
    fac, m, d = {}, n, 2
    while m > 1:
        while m % d == 0:
            fac[d] = fac.get(d, 0) + 1
            m //= d
        d += 1
    return (f"How many positive divisors does {n} have, and what is their sum?",
            f"Prime factorisation: {n} = {' x '.join(f'{p}^{e}' for p, e in fac.items())}.\n\nNumber of divisors = " + " x ".join(f"({e} + 1)" for e in fac.values()) + f" = **{len(divs)}**.\nSum = " + " x ".join("(" + " + ".join(str(p ** i) for i in range(e + 1)) + ")" for p, e in fac.items()) + f" = **{sum(divs)}**.")


@shape(2, "sequences", "hard")
def fibonacci_ratio():
    n = ri(10, 25)
    a, b = 0, 1
    seq = []
    for _ in range(n + 2):
        seq.append(a)
        a, b = b, a + b
    return (f"With F(1) = F(2) = 1, what is F({n}), and how close is F({n})/F({n - 1}) to the golden ratio?",
            f"Iterating the recurrence F(k) = F(k-1) + F(k-2) gives F({n}) = **{seq[n]:,}** and F({n - 1}) = {seq[n - 1]:,}.\n\nRatio = {seq[n] / seq[n - 1]:.6f}, versus (1 + sqrt 5)/2 = {(1 + 5 ** 0.5) / 2:.6f}. The difference is {abs(seq[n] / seq[n - 1] - (1 + 5 ** 0.5) / 2):.2e}.")


@shape(2, "sequences", "medium")
def sum_squares_formula():
    n = ri(5, 30)
    return (f"Use a formula to find 1^2 + 2^2 + ... + {n}^2, then confirm by adding directly.",
            f"Formula: n(n + 1)(2n + 1)/6 = {n} x {n + 1} x {2 * n + 1} / 6 = **{n * (n + 1) * (2 * n + 1) // 6:,}**.\n\nDirect sum: {sum(k * k for k in range(1, n + 1)):,}, which matches.")


@shape(2, "sequences", "medium")
def recurrence_closed():
    a0, r, d = ri(1, 5), pick([2, 3]), ri(1, 4)
    n = ri(5, 9)
    x = a0
    for _ in range(n):
        x = r * x + d
    return (f"A sequence has a_0 = {a0} and a_(k+1) = {r}a_k + {d}. Find a_{n}.",
            f"Compute step by step: " + ", ".join(str(v) for v in _seq(a0, r, d, n)) + f".\n\nSo a_{n} = **{x:,}**. A closed form is a_k = ({a0} + {d}/({r}-1)) x {r}^k - {d}/({r}-1) = {a0 + F(d, r - 1)} x {r}^k - {F(d, r - 1)}.")


def _seq(a0, r, d, n):
    out = [a0]
    for _ in range(n):
        out.append(r * out[-1] + d)
    return out


# ----------------------------- calculus extras -----------------------------
@shape(2, "calculus", "medium")
def area_between_curves():
    a = ri(1, 4)
    k = ri(2, 5)
    return (f"Find the area enclosed between the parabola y = {k * k} - x^2 and the x-axis.",
            f"The parabola meets the x-axis where x^2 = {k * k}, so x = -{k} and x = {k}.\n\nArea = integral from -{k} to {k} of ({k * k} - x^2) dx = [{k * k}x - x^3/3] from -{k} to {k}.\n\nAt x = {k}: {k * k * k} - {k ** 3}/3 = {frac(F(2 * k ** 3, 3))}. The curve is symmetric about the y-axis, so doubling gives **{frac(F(4 * k ** 3, 3))}** (about {4 * k ** 3 / 3:.2f}).")


@shape(2, "calculus", "hard")
def related_rates_balloon():
    r = ri(2, 10)
    dr = F(ri(1, 6), 2)
    dv = 4 * math.pi * r * r * float(dr)
    return (f"A spherical balloon's radius grows at {num(dr)} cm/s. How fast is its volume growing when the radius is {r} cm?",
            f"V = (4/3) pi r^3, so dV/dt = 4 pi r^2 dr/dt.\n\nAt r = {r}: dV/dt = 4 x pi x {r * r} x {num(dr)} = **{dv:.1f} cm3/s**. The same radius growth adds volume faster as the balloon gets bigger, because the surface it grows on gets larger.")


@shape(2, "calculus", "medium")
def exp_derivative_eval():
    a, b = ri(2, 5), ri(1, 3)
    x0 = ri(0, 2)
    return (f"Differentiate f(x) = {a}e^({b}x) and evaluate f'({x0}).",
            f"The derivative of e^(kx) is k e^(kx), so f'(x) = {a * b}e^({b}x).\n\nf'({x0}) = {a * b} x e^({b * x0}) = **{a * b * math.exp(b * x0):.3f}**.")


@shape(2, "calculus", "medium")
def average_value():
    a, b = ri(1, 4), ri(1, 5)
    hi = ri(2, 6)
    avg = F(a * hi ** 2, 3) + F(b)
    return (f"What is the average value of f(x) = {a}x^2 + {b} on the interval [0, {hi}]?",
            f"Average value = (1/({hi} - 0)) x integral of f from 0 to {hi} = (1/{hi}) x [{frac(F(a, 3))}x^3 + {b}x] from 0 to {hi}.\n\n= (1/{hi}) x ({frac(F(a, 3) * hi ** 3 + b * hi)}) = **{frac(avg)}** (about {float(avg):.3f}).")


# ----------------------------- trig / matrices extras -----------------------------
@shape(2, "trigonometry", "medium")
def trig_equation():
    s = pick([F(1, 2), F(3, 5), F(1, 4), F(4, 5)])
    a = math.degrees(math.asin(float(s)))
    return (f"Solve sin(x) = {frac(s)} for x between 0 and 360 degrees.",
            f"The calculator gives x = arcsin({frac(s)}) = {a:.1f} degrees. Sine is positive in the first and second quadrants, so the second solution is 180 - {a:.1f}.\n\nSolutions: **x = {a:.1f} degrees** and **x = {180 - a:.1f} degrees**.")


@shape(2, "trigonometry", "medium")
def bearing_distance():
    d1, d2 = ri(5, 30), ri(5, 30)
    ang = pick([60, 90, 120])
    c = math.sqrt(d1 * d1 + d2 * d2 - 2 * d1 * d2 * math.cos(math.radians(180 - ang)))
    return (f"A hiker walks {d1} km north, turns {ang} degrees to the right and walks another {d2} km. How far is she from the start?",
            f"The interior angle between the two legs is 180 - {ang} = {180 - ang} degrees, so by the law of cosines:\n\nd^2 = {d1}^2 + {d2}^2 - 2({d1})({d2})cos({180 - ang} deg) = {d1 * d1} + {d2 * d2} - {2 * d1 * d2 * math.cos(math.radians(180 - ang)):.1f} = {c * c:.1f}.\n\nd = **{c:.2f} km**.")


@shape(2, "linear_algebra", "medium")
def matrix_inverse_2x2():
    a, b, c, d = ri(1, 6), ri(-4, 5), ri(-4, 5), ri(1, 6)
    det = a * d - b * c
    if det == 0:
        return None
    s = lambda m: f"[{m[0]} {m[1]}; {m[2]} {m[3]}]"
    inv = [F(d, det), F(-b, det), F(-c, det), F(a, det)]
    return (f"Find the inverse of {s([a, b, c, d])}.",
            f"det = {a}({d}) - ({b})({c}) = {det}. For a 2x2 matrix [a b; c d] the inverse is (1/det)[d -b; -c a].\n\nInverse = (1/{det}) {s([d, -b, -c, a])} = **{s([frac(x) for x in inv])}**.\n\nCheck: the first row times the first column gives {frac(a * inv[0] + b * inv[2])}.")


@shape(2, "linear_algebra", "hard")
def eigenvalues_2x2():
    l1, l2 = ri(1, 6), ri(-3, 5)
    if l1 == l2:
        return None
    # matrix [[l1+l2, -l1*l2],[1,0]] has eigenvalues l1,l2
    tr, det = l1 + l2, l1 * l2
    return (f"Find the eigenvalues of [{tr} {-det}; 1 0].",
            f"Characteristic equation: lambda^2 - (trace) lambda + det = 0, with trace = {tr} and det = (0 - {-det}) = {det}.\n\nlambda^2 - {tr}lambda + {det} = 0 factors as (lambda - {l1})(lambda - {l2}) = 0.\n\nEigenvalues: **{l1}** and **{l2}**. Check: they add to the trace ({l1 + l2}) and multiply to the determinant ({l1 * l2}).")


# ----------------------------- more follow-ups -----------------------------
@shape(3, "followup_percent", "medium")
def follow_tax_included():
    p = F(ri(2000, 20000), 100)
    t = pick([5, 8, 10, 20])
    return (f"A price is {money(p)} before {t}% tax. What's the total?",
            f"{money(p)} x {F(100 + t, 100)} = **{money(p * (100 + t) / 100)}**.",
            "The receipt says that total already included tax. What was the pre-tax price then?",
            f"Divide instead of subtracting {t}%: pre-tax = total / {F(100 + t, 100)}. If the {money(p * (100 + t) / 100)} total includes {t}% tax, the pre-tax price is {money(p * (100 + t) / 100)} / {F(100 + t, 100)} = **{money(p)}**.\n\nSubtracting {t}% of the total would give {money(p * (100 + t) / 100 * (100 - t) / 100)}, which is wrong because the tax is {t}% of the pre-tax amount.")


@shape(3, "followup_stats", "medium")
def follow_add_outlier():
    xs = [ri(10, 30) for _ in range(5)]
    big = ri(80, 150)
    return (f"What's the average of {', '.join(map(str, xs))}?",
            f"Sum = {sum(xs)}, count = {len(xs)}, so the mean is **{sum(xs) / len(xs):.2f}**.",
            f"What if I add {big} to the list? Does the median change too?",
            f"New mean = ({sum(xs)} + {big}) / {len(xs) + 1} = **{(sum(xs) + big) / (len(xs) + 1):.2f}**. The median moves from {st.median(xs)} to **{st.median(xs + [big])}**.\n\nThe mean jumps by {(sum(xs) + big) / (len(xs) + 1) - sum(xs) / len(xs):.2f} but the median by only {st.median(xs + [big]) - st.median(xs):.1f}, because the median depends on position, not on how extreme the new value is.")


@shape(3, "followup_algebra", "medium")
def follow_why_flip():
    a, b = ri(2, 6), ri(10, 40)
    return (f"Solve -{a}x > {b}.",
            f"Divide both sides by -{a}: x < {frac(F(-b, a))}. The inequality sign flips.",
            "Why did the sign flip?",
            f"Multiplying or dividing by a negative number reverses order on the number line: for example 2 < 5, but -2 > -5. Check with x = {math.floor(-b / a) - 1}: -{a} x ({math.floor(-b / a) - 1}) = {-a * (math.floor(-b / a) - 1)}, which is greater than {b}. A value like x = {math.ceil(-b / a) + 1} gives {-a * (math.ceil(-b / a) + 1)}, which fails.")


@shape(3, "followup_combinatorics", "medium")
def follow_with_repetition():
    n = ri(4, 7)
    r = ri(2, 3)
    return (f"How many ways can I choose {r} different books from {n} to read in order?",
            f"Order matters and books can't repeat, so it's a permutation: P({n},{r}) = {n}! / ({n - r})! = **{math.perm(n, r)}**.",
            "What if the order didn't matter?",
            f"Divide by the {r}! ways to order the chosen books: C({n},{r}) = {math.perm(n, r)} / {math.factorial(r)} = **{math.comb(n, r)}**.\n\nIf you could pick the same book again (order mattering), it would be {n}^{r} = {n ** r}.")


@shape(3, "followup_geometry", "medium")
def follow_circle_units():
    r = ri(3, 12)
    return (f"What's the circumference of a circle with radius {r} cm?",
            f"C = 2 pi r = 2 x pi x {r} = **{2 * math.pi * r:.2f} cm**.",
            "And in metres? Also what's the area?",
            f"In metres: {2 * math.pi * r / 100:.4f} m. Area = pi r^2 = pi x {r * r} = **{math.pi * r * r:.2f} cm2**, which is {math.pi * r * r / 10000:.5f} m2 (not {math.pi * r * r / 100:.4f}: area units convert with the square, 1 m2 = 10,000 cm2).")


@shape(2, "followup_three_turn", "hard")
def follow_three_turns_equation():
    x, y = ri(2, 9), ri(2, 9)
    a, b = ri(2, 5), ri(2, 5)
    c, d = ri(1, 4), ri(1, 4)
    while a * d == b * c:
        d += 1
    return (f"Solve {a}x + {b}y = {a * x + b * y} and {c}x + {d}y = {c * x + d * y}.",
            f"Eliminate x. Multiply the first equation by {c} and the second by {a}:\n{a * c}x + {b * c}y = {c * (a * x + b * y)}\n{a * c}x + {a * d}y = {a * (c * x + d * y)}\n\nSubtract: {a * d - b * c}y = {a * (c * x + d * y) - c * (a * x + b * y)}, so y = **{y}**, then x = **{x}**.",
            "Verify with the second equation.",
            f"{c} x {x} + {d} x {y} = {c * x} + {d * y} = {c * x + d * y}, which matches the right-hand side. The first also checks: {a} x {x} + {b} x {y} = {a * x + b * y}.",
            "What if the two equations were multiples of each other?",
            f"Then {a}d - {b}c would be 0, the elimination step would leave 0 = something, and the lines would be parallel (no solution) or identical (infinitely many solutions). Here {a * d - b * c} is not zero, so the lines cross at exactly one point, ({x}, {y}).")
