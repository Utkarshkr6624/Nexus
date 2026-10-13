"""Phase 2.1 maths batch D: spot-the-error, true/false claims, estimation, concept explanations, and 2-3 turn follow-ups. All numbers computed."""
import math
from fractions import Fraction as F
from gen21_math_common import *


# ----------------------------- spot the error -----------------------------
@shape(2, "spot_error", "medium")
def error_add_fractions():
    a, b, c, d = ri(1, 4), ri(2, 7), ri(1, 4), ri(2, 7)
    wrong = F(a + c, b + d)
    right = F(a, b) + F(c, d)
    if wrong == right:
        return None
    return (f"A student wrote {a}/{b} + {c}/{d} = {a + c}/{b + d}. Is that right?",
            f"No. You can't add fractions by adding numerators and denominators separately. Use a common denominator.\n\n{a}/{b} + {c}/{d} = {a * d}/{b * d} + {c * b}/{b * d} = {a * d + c * b}/{b * d} = **{frac(right)}**.\n\nThe student's {frac(wrong)} = {float(wrong):.3f} differs from the correct {float(right):.3f}.")


@shape(2, "spot_error", "medium")
def error_sign_distribution():
    a, b, c = ri(2, 6), ri(2, 9), ri(1, 8)
    return (f"Someone simplified -{a}({b}x - {c}) as -{a * b}x - {a * c}. What went wrong?",
            f"The minus sign has to multiply both terms inside the bracket.\n\n-{a} x {b}x = -{a * b}x, and -{a} x (-{c}) = +{a * c}.\n\nCorrect: **-{a * b}x + {a * c}**. The student kept the sign of the second term unchanged.")


@shape(2, "spot_error", "medium")
def error_square_sum():
    a, b = ri(2, 9), ri(2, 9)
    return (f"A classmate says ({a} + {b})^2 = {a}^2 + {b}^2 = {a * a + b * b}. Check that.",
            f"That drops the cross term. (a + b)^2 = a^2 + 2ab + b^2.\n\n({a} + {b})^2 = {(a + b) ** 2}, whereas {a}^2 + {b}^2 = {a * a + b * b}. The missing piece is 2ab = 2 x {a} x {b} = **{2 * a * b}**.")


@shape(2, "spot_error", "hard")
def error_cancel_terms():
    a, b = ri(2, 6), ri(2, 6)
    x = ri(2, 7)
    return (f"A student 'simplifies' ({x} + {a}) / {x} to {a} by cancelling the {x}s. Is that valid?",
            f"No. You can cancel only common *factors*, not terms that are added.\n\n({x} + {a}) / {x} = {x}/{x} + {a}/{x} = **1 + {frac(F(a, x))}** = {frac(1 + F(a, x))}.\n\nThe student's answer {a} is {float(a):g}, while the true value is {float(1 + F(a, x)):.3f}. Cancelling is valid in ({x}({a})) / {x}, where {x} multiplies everything.")


@shape(2, "spot_error", "medium")
def error_percent_base():
    p = pick([10, 20, 25, 50])
    pr = ri(40, 400)
    after = F(pr * (100 + p), 100)
    wrong = after * (100 - p) / 100
    return (f"A price goes up {p}% and then down {p}%. {name()} says it's back to {money(pr)}. Is that correct?",
            f"No. The decrease is {p}% of the *new* price, which is larger.\n\nAfter the increase: {money(pr)} x {F(100 + p, 100)} = {money(after)}.\nAfter the decrease: {money(after)} x {F(100 - p, 100)} = **{money(wrong)}**.\n\nThat's {money(pr - wrong)} lower than the start, a net change of {float((wrong / pr - 1) * 100):.2f}%.")


@shape(2, "spot_error", "medium")
def error_solution_steps():
    a, b, c = ri(2, 6), ri(3, 12), ri(20, 60)
    x = F(c - b, a)
    wrong_x = F(c + b, a)
    return (f"Check this solution: {a}x + {b} = {c}, so {a}x = {c} + {b} = {c + b}, so x = {frac(wrong_x)}.",
            f"The error is in the second step. To remove +{b} from the left side you subtract {b} from both sides, not add.\n\n{a}x = {c} - {b} = {c - b}, so x = **{frac(x)}**.\n\nCheck: {a}({frac(x)}) + {b} = {frac(a * x + b)} = {c}. The student's value gives {frac(a * wrong_x + b)} instead.")


@shape(2, "spot_error", "hard")
def error_probability_add():
    n = ri(2, 4)
    return (f"I flip a fair coin {n} times, and I think P(at least one head) = {n} x 1/2 = {n}/2. Why does that look wrong?",
            f"Probabilities can't exceed 1, and {n}/2 = {num(F(n, 2))} does. Adding probabilities only works for mutually exclusive events, and 'head on flip 1' and 'head on flip 2' can both happen.\n\nUse the complement: P(no heads) = (1/2)^{n} = {frac(F(1, 2 ** n))}, so P(at least one head) = 1 - {frac(F(1, 2 ** n))} = **{frac(1 - F(1, 2 ** n))}**.")


@shape(2, "spot_error", "medium")
def error_unit_conversion():
    side = ri(2, 9)
    return (f"A square has side {side} m. Someone says its area is {side * side} m2 = {side * side * 100} cm2 because 1 m = 100 cm. Spot the mistake.",
            f"Areas scale with the square of the length factor. 1 m2 = 100 x 100 = 10,000 cm2, not 100 cm2.\n\nArea = {side * side} m2 = {side * side} x 10,000 = **{side * side * 10000:,} cm2**.\n\nCheck by converting the side first: {side * 100} cm x {side * 100} cm = {side * 100 * side * 100:,} cm2.")


@shape(2, "spot_error", "hard")
def error_average_of_averages():
    n1, n2 = ri(5, 20), ri(25, 60)
    m1, m2 = ri(50, 70), ri(75, 95)
    true = F(n1 * m1 + n2 * m2, n1 + n2)
    return (f"Class A ({n1} students) averaged {m1} and class B ({n2} students) averaged {m2}. A teacher says the combined average is ({m1} + {m2})/2 = {num(F(m1 + m2, 2))}. Why is that off?",
            f"That treats the classes as equal in size. The combined average must weight by the number of students.\n\nTotal points = {n1} x {m1} + {n2} x {m2} = {n1 * m1 + n2 * m2}. Students = {n1 + n2}.\n\nCombined = {n1 * m1 + n2 * m2} / {n1 + n2} = **{float(true):.2f}**, much closer to class B's average because B is larger.")


# ----------------------------- claims -----------------------------
@shape(2, "claims", "easy")
def claim_even_odd():
    a, b = ri(1, 9) * 2 + 1, ri(1, 9) * 2 + 1
    return ("True or false: the sum of two odd numbers is always odd. Give a reason and an example.",
            f"False. The sum of two odd numbers is always **even**. Write them as 2m + 1 and 2n + 1; the sum is 2(m + n + 1), a multiple of 2.\n\nExample: {a} + {b} = {a + b}, which is even.")


@shape(2, "claims", "medium")
def claim_square_root_sum():
    a, b = pick([(9, 16), (16, 9), (4, 25), (36, 64)])
    return (f"True or false: sqrt({a} + {b}) = sqrt({a}) + sqrt({b}).",
            f"False in general. sqrt({a} + {b}) = sqrt({a + b}) = {math.sqrt(a + b):.4f}, but sqrt({a}) + sqrt({b}) = {int(math.sqrt(a))} + {int(math.sqrt(b))} = {int(math.sqrt(a)) + int(math.sqrt(b))}.\n\nSquare roots don't distribute over addition. (They do over multiplication: sqrt(ab) = sqrt(a) sqrt(b) for non-negative a, b.)")


@shape(2, "claims", "medium")
def claim_prime_formula():
    n = pick([41, 40, 39, 35, 11])
    val = n * n + n + 41
    isp = lambda k: k > 1 and all(k % d for d in range(2, int(k ** 0.5) + 1))
    return (f"Euler noticed that n^2 + n + 41 gave primes for n = 0, 1, 2, ... Is it prime for every n? Test n = {n}.",
            f"For n = {n}: {n}^2 + {n} + 41 = {val}, which is {'prime' if isp(val) else 'not prime (' + str(next(d for d in range(2, val) if val % d == 0)) + ' divides it)'}.\n\nThe expression is prime for n = 0 through 39, but it fails at n = 40 (1681 = 41 x 41). **No polynomial gives primes for every n**, and a pattern holding for many cases is not a proof.")


@shape(2, "claims", "medium")
def claim_divisible_digits():
    n = ri(1000, 9999)
    return (f"True or false: a number is divisible by 6 if it is divisible by 2 and by 3. Test with {n}.",
            f"True, because 2 and 3 are coprime: if both divide a number, so does their product, 6.\n\nTest {n}: even? {'yes' if n % 2 == 0 else 'no'}. Digit sum {sum(map(int, str(n)))}, divisible by 3? {'yes' if sum(map(int, str(n))) % 3 == 0 else 'no'}. So {n} is {'' if n % 6 == 0 else 'not '}divisible by 6 ({n} mod 6 = {n % 6}).\n\nBe careful: the rule would fail for 2 and 4, which are not coprime (12 is divisible by 4 but 4 does not follow from 2 and 3 alone).")


@shape(2, "claims", "hard")
def claim_infinite_primes():
    return ("Is it true that there are infinitely many primes? Give the idea of the proof.",
            "Yes. Euclid's argument: suppose there were finitely many primes p1, ..., pk. Form N = p1 x p2 x ... x pk + 1.\n\nN leaves remainder 1 when divided by each pi, so none of them divides N. But N > 1 has some prime factor, which must be a prime not on the list. That contradicts the assumption, so the list can't be complete.\n\n(Note that N itself need not be prime: 2 x 3 x 5 x 7 x 11 x 13 + 1 = 30031 = 59 x 509.)")


# ----------------------------- concepts -----------------------------
@shape(1, "concepts", "medium")
def concept_zero_factorial():
    return ("Why is 0! equal to 1?",
            f"Three ways to see it:\n\n1. **Counting:** n! counts the ways to arrange n objects. There is exactly one way to arrange nothing (do nothing).\n2. **Pattern:** n! = n x (n-1)!, so 1! = 1 x 0!, which forces 0! = 1.\n3. **Formulas:** C(n,0) = n!/(0! n!) must equal 1 (one way to choose nothing), which again needs 0! = 1.\n\nIt is a definition chosen so that these formulas keep working.")


@shape(1, "concepts", "medium")
def concept_negative_times_negative():
    return ("Why is a negative times a negative positive?",
            "A pattern argument: 3 x (-2) = -6, 2 x (-2) = -4, 1 x (-2) = -2, 0 x (-2) = 0. The results rise by 2 each time the first factor drops by 1, so the next entries are (-1) x (-2) = 2 and (-2) x (-2) = 4.\n\nAnother view: multiplying by -1 flips a number across zero on the number line. Flipping twice puts you back on the positive side. It also keeps the distributive law true: 0 = (-1)(1 + (-1)) = -1 + (-1)(-1), so (-1)(-1) must be 1.")


@shape(1, "concepts", "medium")
def concept_mean_vs_median():
    xs = [ri(20, 40) for _ in range(6)] + [ri(300, 500)]
    return ("When should I report the median instead of the mean?",
            f"Use the median when the data are skewed or have outliers, because a few extreme values drag the mean.\n\nExample: incomes of {', '.join(map(str, xs))} (thousands). Mean = {sum(xs) / len(xs):.1f}, median = {sorted(xs)[len(xs) // 2]}. The mean is pulled toward the one huge value, so the median describes a typical person better. For roughly symmetric data they agree and the mean is more efficient.")


@shape(1, "concepts", "medium")
def concept_independent_vs_exclusive():
    return ("What is the difference between independent and mutually exclusive events?",
            "**Mutually exclusive** events cannot happen together: P(A and B) = 0 (rolling a 2 and a 5 on one die). **Independent** events don't influence each other: P(A and B) = P(A) x P(B) (two separate coin flips).\n\nMutually exclusive events with non-zero probabilities are actually *dependent*: if A happens, B is certainly not happening. So the two ideas are different, and they only overlap trivially when one event has probability zero.")


@shape(1, "concepts", "medium")
def concept_derivative_meaning():
    a = ri(2, 5)
    return (f"What does the derivative of f(x) = x^2 at x = {a} actually mean? Not just the formula.",
            f"The derivative f'(x) = 2x gives f'({a}) = {2 * a}. It is the slope of the tangent line at x = {a}: if you zoom in far enough on the curve, it looks like a straight line rising {2 * a} units for each 1 unit to the right.\n\nAs a rate of change: near x = {a}, f(x + h) is about f({a}) + {2 * a}h. Check with h = 0.1: f({a + 0.1}) = {(a + 0.1) ** 2:.2f} against the estimate {a * a + 2 * a * 0.1:.2f}.")


@shape(1, "concepts", "easy")
def concept_percent_vs_ratio():
    a, b = ri(2, 9), ri(10, 19)
    return (f"What's the difference between '{a} is what percent of {b}' and '{a} is what percent more than {b}'?",
            f"'What percent of {b}' compares to the whole: {a}/{b} x 100 = **{a / b * 100:.1f}%**.\n\n'Percent more than {b}' measures the extra relative to {b}: ({a} - {b})/{b} x 100 = **{(a - b) / b * 100:+.1f}%**" + (" (so it is actually less)" if a < b else "") + ".")


@shape(1, "concepts", "medium")
def concept_exponential_vs_linear():
    return ("Why does exponential growth eventually beat linear growth, even if the linear one starts much bigger?",
            f"Linear growth adds a fixed amount per step; exponential growth multiplies by a fixed factor, so its increments themselves grow.\n\nExample: 1,000 + 100n versus 2^n. At n = 5: {1000 + 500} vs {2 ** 5}. At n = 10: {1000 + 1000} vs {2 ** 10}. At n = 15: {1000 + 1500} vs {2 ** 15}. At n = 20: {1000 + 2000} vs {2 ** 20:,}. The exponential overtakes between n = 10 and n = 15, and then the gap explodes.")


# ----------------------------- estimation -----------------------------
@shape(2, "estimation", "medium")
def fermi_digits():
    n = ri(30, 90)
    return (f"Roughly how many digits does 2^{n} have? Estimate with logarithms and then verify.",
            f"Digits = floor(n log10 2) + 1 = floor({n} x 0.30103) + 1 = floor({n * math.log10(2):.2f}) + 1 = **{int(n * math.log10(2)) + 1}**.\n\nExact: 2^{n} = {2 ** n:,}, which has {len(str(2 ** n))} digits.")


@shape(2, "estimation", "medium")
def sanity_check_division():
    a, b = ri(1200, 9800), ri(23, 79)
    return (f"Quickly estimate {a} / {b}, then compute it exactly to two decimals.",
            f"Round to friendly numbers: {round(a, -2)} / {round(b, -1)} is about {round(a, -2) / round(b, -1):.0f}.\n\nExact: {a} / {b} = **{a / b:.2f}**. The estimate is within {abs(round(a, -2) / round(b, -1) - a / b) / (a / b) * 100:.0f}%, good enough to catch a misplaced decimal point.")


# ----------------------------- multi-turn follow-ups -----------------------------
@shape(3, "followup_percent", "medium")
def follow_new_percent():
    p = ri(120, 900)
    r1, r2 = pick([10, 15, 20]), pick([25, 30, 35])
    return (f"A {money(p)} laptop is {r1}% off. What's the sale price?",
            f"{money(p)} x {F(100 - r1, 100)} = **{money(F(p * (100 - r1), 100))}**.",
            f"What if the discount were {r2}% instead?",
            f"Same method: {money(p)} x {F(100 - r2, 100)} = **{money(F(p * (100 - r2), 100))}**, which is {money(F(p * (r2 - r1), 100))} cheaper than at {r1}% off.")


@shape(3, "followup_explain", "medium")
def follow_why_divide():
    d, t = ri(60, 300), ri(2, 6)
    v2 = ri(2, 4)
    return (f"A van travels {d} km in {t} hours. How fast is it going?",
            f"Speed = distance / time = {d} / {t} = **{num(F(d, t))} km/h**.",
            "Why do we divide, not multiply?",
            f"Speed means distance covered *per hour*, so we ask how many kilometres fit into each hour: share {d} km equally over {t} hours. A quick unit check: km / h gives km/h, whereas km x h would give km-hours, which isn't a speed.\n\nThe reverse also works: at {num(F(d, t))} km/h for {t} hours you cover {num(F(d, t))} x {t} = {d} km.")


@shape(3, "followup_units", "easy")
def follow_unit_chain():
    km = ri(5, 42)
    return (f"How many miles is a {km} km run? (1 mile = 1.609344 km.)",
            f"{km} / 1.609344 = **{km / 1.609344:.2f} miles**.",
            "And how long is that at 6 minutes per kilometre?",
            f"Time = {km} km x 6 min/km = **{km * 6} minutes**, which is {km * 6 // 60} h {km * 6 % 60} min. Pace per mile is 6 x 1.609344 = {6 * 1.609344:.2f} minutes, or about {int(6 * 1.609344)}:{round((6 * 1.609344 % 1) * 60):02d} per mile.")


@shape(3, "followup_algebra", "medium")
def follow_check_answer():
    a, b, c = ri(2, 7), ri(1, 15), ri(20, 80)
    x = F(c - b, a)
    return (f"Solve {a}x + {b} = {c}.",
            f"{a}x = {c} - {b} = {c - b}, so x = **{frac(x)}**.",
            "Can you check that it's right?",
            f"Substitute back: {a} x {frac(x)} + {b} = {frac(a * x)} + {b} = {frac(a * x + b)}. That equals {c}, so x = {frac(x)} satisfies the equation.")


@shape(3, "followup_probability", "medium")
def follow_dice_extension():
    t = ri(5, 9)
    fav = sum(1 for a in range(1, 7) for b in range(1, 7) if a + b == t)
    fav3 = sum(1 for a in range(1, 7) for b in range(1, 7) for c in range(1, 7) if a + b + c == t + 3)
    return (f"What's the probability of rolling a total of {t} with two fair dice?",
            f"{fav} of the 36 equally likely outcomes sum to {t}, so P = {fav}/36 = **{frac(F(fav, 36))}** (about {fav / 36:.3f}).",
            f"And a total of {t + 3} with three dice?",
            f"Now there are 6^3 = 216 outcomes, and {fav3} of them sum to {t + 3}. P = {fav3}/216 = **{frac(F(fav3, 216))}** (about {fav3 / 216:.3f}).")


@shape(3, "followup_correction", "medium")
def follow_user_correction():
    a, b = ri(12, 60), ri(12, 60)
    wrong = a * b + ri(1, 9)
    return (f"What is {a} x {b}?",
            f"{a} x {b} = **{a * b:,}**.",
            f"I got {wrong:,}. Who's right?",
            f"Let's check by splitting: {a} x {b} = {a} x {b // 10 * 10} + {a} x {b % 10} = {a * (b // 10 * 10):,} + {a * (b % 10):,} = {a * b:,}.\n\nSo {a * b:,} is correct, and your {wrong:,} is off by {abs(wrong - a * b)}. A common slip is a small carry error in the partial products, so recheck those.")


@shape(3, "followup_three_turn", "hard")
def follow_three_turns_loan():
    pr = pick([2000, 5000, 8000])
    r = pick([4, 5, 6])
    y1, y2 = 2, 5
    return (f"I put {money(pr)} in an account paying {r}% compound interest annually. What do I have after {y1} years?",
            f"{money(pr)} x (1 + {r}/100)^{y1} = {pr} x {(1 + r / 100) ** y1:.6f} = **{pr * (1 + r / 100) ** y1:,.2f}**.",
            f"What about after {y2} years?",
            f"{money(pr)} x {(1 + r / 100):g}^{y2} = **{pr * (1 + r / 100) ** y2:,.2f}**.",
            "How much of that is interest?",
            f"Interest after {y2} years = {pr * (1 + r / 100) ** y2:,.2f} - {pr:,.2f} = **{pr * (1 + r / 100) ** y2 - pr:,.2f}**. With simple interest it would have been {pr * r * y2 / 100:,.2f}, so compounding adds {pr * (1 + r / 100) ** y2 - pr - pr * r * y2 / 100:,.2f}.")


@shape(3, "followup_geometry", "medium")
def follow_scale_shape():
    w, h = ri(3, 12), ri(3, 12)
    k = pick([2, 3, 4])
    return (f"A rectangle is {w} cm by {h} cm. What are its area and perimeter?",
            f"Area = {w} x {h} = **{w * h} cm2**; perimeter = 2({w} + {h}) = **{2 * (w + h)} cm**.",
            f"If I scale both sides by {k}, what happens to each?",
            f"The new sides are {w * k} cm and {h * k} cm. Perimeter scales by {k}: {2 * (w + h) * k} cm. Area scales by {k}^2 = {k * k}: {w * h * k * k} cm2.\n\nLengths scale by the factor, areas by its square, and volumes by its cube.")


@shape(2, "followup_graph", "medium")
def follow_quadratic_vertex():
    a, b, c = ri(1, 3), ri(-6, 6), ri(-8, 8)
    vx = F(-b, 2 * a)
    vy = a * vx * vx + b * vx + c
    return (f"Where does y = {poly([a, b, c])} reach its minimum?",
            f"The vertex is at x = -b/(2a) = {-b}/{2 * a} = **{frac(vx)}**, where y = **{frac(vy)}**.",
            "How do you know it's a minimum and not a maximum?",
            f"The coefficient of x^2 is {a}, which is positive, so the parabola opens upward and the vertex is its lowest point. If it were negative the vertex would be a maximum. You can also see it from y'' = {2 * a} > 0.")
