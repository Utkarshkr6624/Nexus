"""Category 5: mathematics / technical problem solving. Every answer is computed, not guessed."""
import math
import statistics
from fractions import Fraction as Fr
from itertools import combinations
from lib import *

CAT = "mathematics"
rng = seeded("math")
R = rng
out = []


def add(sub, q, a, diff="medium", g=None):
    out.append(mk(CAT, sub, [("user", q), ("assistant", a)], g or sub, diff, "computed", pick(rng, SYS_GENERAL)))


def fr(x):
    x = Fr(x)
    return str(x.numerator) if x.denominator == 1 else f"{x.numerator}/{x.denominator}"


NAMES = ["Maya", "Arjun", "Lena", "Omar", "Sofia", "Ken", "Priya", "Tom", "Aisha", "Diego", "Nora", "Ravi", "Chloe", "Ivan", "Zara"]
ITEMS = ["notebooks", "apples", "tickets", "pencils", "stickers", "mugs", "books", "cookies", "batteries", "candles"]

# 1. order of operations
for _ in range(22*3):
    a, b, c, d = R.randint(2, 20), R.randint(2, 12), R.randint(2, 9), R.randint(2, 15)
    form = R.choice([
        (f"{a} + {b} * {c} - {d}", a + b * c - d, f"Multiply first: {b} * {c} = {b*c}. Then {a} + {b*c} = {a+b*c}, and {a+b*c} - {d} = {a+b*c-d}."),
        (f"({a} + {b}) * {c} - {d}", (a + b) * c - d, f"Brackets first: {a} + {b} = {a+b}. Then {a+b} * {c} = {(a+b)*c}, and {(a+b)*c} - {d} = {(a+b)*c-d}."),
        (f"{a} * {b} + {c} * {d}", a * b + c * d, f"Do both products: {a} * {b} = {a*b} and {c} * {d} = {c*d}. Then {a*b} + {c*d} = {a*b+c*d}."),
        (f"{a*c} / {c} + {b}^2", a + b * b, f"{a*c} / {c} = {a}, and {b}^2 = {b*b}. The sum is {a} + {b*b} = {a+b*b}."),
    ])
    q = R.choice([f"What is {form[0]}?", f"Evaluate {form[0]} and show the steps.", f"compute {form[0]}"])
    add("order_of_operations", q, f"{form[2]}\n\nAnswer: {form[1]}", "easy")

# 2. percentages / discounts / tax
for _ in range(24*3):
    price = R.choice([40, 60, 80, 120, 150, 200, 250, 360, 480, 520, 750])
    p = R.choice([5, 10, 12, 15, 20, 25, 30, 35, 40])
    t = R.choice([5, 8, 10, 18])
    typ = R.randrange(4)
    if typ == 0:
        v = Fr(price * p, 100)
        add("percent", f"What is {p}% of {price}?", f"{p}% means {p}/100, so {p}/100 * {price} = {fr(v)}.\n\nAnswer: {fr(v)}", "easy")
    elif typ == 1:
        d = Fr(price * (100 - p), 100)
        add("percent", f"A jacket costs ${price} and is {p}% off. What's the sale price?", f"The discount is {p}% of {price} = {fr(Fr(price*p,100))}. The sale price is {price} - {fr(Fr(price*p,100))} = {fr(d)}.\n\nAnswer: ${fr(d)}")
    elif typ == 2:
        d = Fr(price * (100 - p), 100) * Fr(100 + t, 100)
        add("percent", f"Something costs ${price}. It gets {p}% off, then {t}% tax is added on the discounted price. What do I pay?",
            f"After the discount: {price} * {100-p}/100 = {fr(Fr(price*(100-p),100))}.\nAdding {t}% tax multiplies by {100+t}/100: {fr(Fr(price*(100-p),100))} * {100+t}/100 = {fr(d)}.\n\nAnswer: ${float(d):.2f}", "hard")
    else:
        new = Fr(price * (100 + p), 100)
        add("percent", f"A value goes from {price} to {fr(new)}. What is the percentage increase?", f"Increase = {fr(new)} - {price} = {fr(new-price)}. Percentage = {fr(new-price)}/{price} * 100 = {p}%.\n\nAnswer: {p}%")

# 3. linear equations
for _ in range(26*3):
    x = R.randint(-9, 12)
    a = R.choice([2, 3, 4, 5, 6, 7])
    b = R.randint(-15, 15)
    c = a * x + b
    sign = "+" if b >= 0 else "-"
    eq = f"{a}x {sign} {abs(b)} = {c}"
    q = R.choice([f"Solve for x: {eq}", f"What value of x satisfies {eq}?", f"help me solve {eq}"])
    add("linear_equation", q, f"Isolate the x term. Move {sign}{abs(b)} to the other side: {a}x = {c} {'-' if b>=0 else '+'} {abs(b)} = {c-b}.\nDivide by {a}: x = {c-b}/{a} = {x}.\n\nCheck: {a}*{x} {sign} {abs(b)} = {a*x+b}, which equals {c}.\n\nAnswer: x = {x}", "easy")

# 4. quadratics with integer roots
for _ in range(24*3):
    r1, r2 = R.randint(-8, 8), R.randint(-8, 8)
    B, C = -(r1 + r2), r1 * r2
    def term(coef, v):
        if coef == 0:
            return ""
        s = "+" if coef > 0 else "-"
        return f" {s} {abs(coef) if abs(coef)!=1 or v=='' else ''}{v}"
    eq = "x^2" + term(B, "x") + term(C, "") + " = 0"
    disc = B * B - 4 * C
    add("quadratic", R.choice([f"Solve {eq}", f"Find the roots of {eq}", f"what are the solutions of {eq}?"]),
        f"Use the quadratic formula with a = 1, b = {B}, c = {C}.\nDiscriminant: b^2 - 4ac = {B*B} - {4*C} = {disc}, and sqrt({disc}) = {int(math.isqrt(disc))}.\nx = ({-B} ± {math.isqrt(disc)}) / 2, so x = {fr(Fr(-B+math.isqrt(disc),2))} or x = {fr(Fr(-B-math.isqrt(disc),2))}.\n\nCheck by factoring: (x {'-' if r1>=0 else '+'} {abs(r1)})(x {'-' if r2>=0 else '+'} {abs(r2)}) = 0.\n\nAnswer: x = {r1} or x = {r2}" if r1 != r2 else
        f"The discriminant is {B}^2 - 4*{C} = {disc}, which is 0, so there is one repeated root: x = {-B}/2 = {r1}.\n\nAnswer: x = {r1} (double root)", "medium")

# 5. 2x2 systems
for _ in range(22*3):
    x, y = R.randint(-5, 8), R.randint(-5, 8)
    a1, b1, a2, b2 = R.randint(1, 5), R.randint(1, 5), R.randint(1, 5), R.randint(-5, -1)
    if a1 * b2 - a2 * b1 == 0:
        continue
    c1, c2 = a1 * x + b1 * y, a2 * x + b2 * y
    det = a1 * b2 - a2 * b1
    add("system_of_equations", f"Solve the system:\n{a1}x + {b1}y = {c1}\n{a2}x - {abs(b2)}y = {c2}",
        f"Use elimination. Multiply the first equation by {abs(b2)} and the second by {b1} so the y terms cancel when added:\n{a1*abs(b2)}x + {b1*abs(b2)}y = {c1*abs(b2)}\n{a2*b1}x - {abs(b2)*b1}y = {c2*b1}\nAdding: {a1*abs(b2)+a2*b1}x = {c1*abs(b2)+c2*b1}, so x = {x}.\nSubstitute into the first equation: {a1*x} + {b1}y = {c1}, so y = {y}.\n\nCheck in the second: {a2}*{x} - {abs(b2)}*{y} = {a2*x+b2*y} = {c2}.\n\nAnswer: x = {x}, y = {y}", "medium")

# 6. probability
for _ in range(36*3):
    typ = R.randrange(6)
    if typ == 0:
        t = R.randint(2, 12)
        n = sum(1 for i in range(1, 7) for j in range(1, 7) if i + j == t)
        add("probability", f"Two fair six-sided dice are rolled. What is the probability that the sum is {t}?", f"There are 6*6 = 36 equally likely outcomes. The pairs that sum to {t}: {n}.\nProbability = {n}/36 = {fr(Fr(n,36))}.\n\nAnswer: {fr(Fr(n,36))} (about {n/36:.3f})")
    elif typ == 1:
        red, blue = R.randint(2, 9), R.randint(2, 9)
        tot = red + blue
        p = Fr(red, tot) * Fr(red - 1, tot - 1)
        add("probability", f"A bag has {red} red and {blue} blue marbles. You draw two without replacement. What is the probability both are red?",
            f"First draw red: {red}/{tot}. Second draw red given the first was red: {red-1}/{tot-1}.\nMultiply: {red}/{tot} * {red-1}/{tot-1} = {fr(p)}.\n\nAnswer: {fr(p)} (about {float(p):.3f})", "hard")
    elif typ == 2:
        n = R.randint(3, 8)
        k = R.randint(1, n - 1)
        p = Fr(math.comb(n, k), 2 ** n)
        add("probability", f"A fair coin is flipped {n} times. What is the probability of exactly {k} heads?", f"Count the ways: C({n},{k}) = {math.comb(n,k)}. Each sequence has probability 1/2^{n} = 1/{2**n}.\nProbability = {math.comb(n,k)}/{2**n} = {fr(p)}.\n\nAnswer: {fr(p)} (about {float(p):.3f})", "medium")
    elif typ == 3:
        p1 = R.choice([Fr(1, 2), Fr(1, 3), Fr(1, 4), Fr(2, 5), Fr(3, 4)])
        p2 = R.choice([Fr(1, 2), Fr(1, 5), Fr(1, 6), Fr(2, 3)])
        u = p1 + p2 - p1 * p2
        add("probability", f"Events A and B are independent with P(A) = {fr(p1)} and P(B) = {fr(p2)}. What is P(A or B)?", f"For independent events P(A and B) = {fr(p1)} * {fr(p2)} = {fr(p1*p2)}.\nP(A or B) = P(A) + P(B) - P(A and B) = {fr(p1)} + {fr(p2)} - {fr(p1*p2)} = {fr(u)}.\n\nAnswer: {fr(u)}", "medium")
    elif typ == 4:
        sens, prev, fp = R.choice([(0.9, 0.01, 0.05), (0.95, 0.02, 0.1), (0.8, 0.05, 0.1), (0.99, 0.001, 0.02)]), None, None
        s, pr, f = sens
        pos = s * pr + f * (1 - pr)
        post = s * pr / pos
        add("probability", f"A test detects a condition {int(s*100)}% of the time when present and gives a false positive {int(f*100)}% of the time when absent. The condition affects {pr*100:g}% of people. If someone tests positive, what is the probability they have it?",
            f"Use Bayes' theorem.\nP(positive) = {s}*{pr} + {f}*{round(1-pr,4)} = {pos:.5f}.\nP(condition | positive) = {s}*{pr} / {pos:.5f} = {post:.4f}.\n\nAnswer: about {post*100:.1f}%. When the condition is rare, most positives are false alarms, even with an accurate test.", "hard")
    else:
        n = R.randint(5, 12)
        k = R.randint(2, 4)
        add("probability", f"A class of {n} students picks {k} at random for a committee. How many different committees are possible, and what is the chance a specific student is on it?",
            f"The number of committees is C({n},{k}) = {math.comb(n,k)}. Committees containing the specific student: choose the other {k-1} from the remaining {n-1}: C({n-1},{k-1}) = {math.comb(n-1,k-1)}.\nProbability = {math.comb(n-1,k-1)}/{math.comb(n,k)} = {fr(Fr(math.comb(n-1,k-1),math.comb(n,k)))}, which is simply {k}/{n}.\n\nAnswer: {math.comb(n,k)} committees; probability {fr(Fr(k,n))}", "hard")

# 7. statistics
for _ in range(36*3):
    n = R.randint(5, 9)
    data = [R.randint(2, 40) for _ in range(n)]
    s = sorted(data)
    mean = Fr(sum(data), n)
    med = statistics.median(data)
    var_p = sum((Fr(x) - mean) ** 2 for x in data) / n
    typ = R.randrange(4)
    if typ == 0:
        add("statistics", f"Find the mean and median of: {', '.join(map(str, data))}", f"Sum = {sum(data)}, count = {n}, so mean = {sum(data)}/{n} = {float(mean):.2f}.\nSorted: {', '.join(map(str, s))}. {'The middle value' if n%2 else 'The average of the two middle values'} is {med}.\n\nAnswer: mean ≈ {float(mean):.2f}, median = {med}", "easy")
    elif typ == 1:
        add("statistics", f"What are the population variance and standard deviation of {', '.join(map(str, data))}?", f"Mean = {sum(data)}/{n} = {float(mean):.3f}.\nSquared deviations sum = {float(sum((Fr(x)-mean)**2 for x in data)):.3f}.\nPopulation variance = that / {n} = {float(var_p):.3f}.\nStandard deviation = sqrt({float(var_p):.3f}) = {math.sqrt(var_p):.3f}.\n\nAnswer: variance ≈ {float(var_p):.3f}, standard deviation ≈ {math.sqrt(var_p):.3f}", "medium")
    elif typ == 2:
        data2 = data + [R.choice(data)]
        modes = statistics.multimode(data2)
        add("statistics", f"What's the mode of {', '.join(map(str, data2))}?", f"Count how often each value appears. The most frequent value{'s are' if len(modes)>1 else ' is'} {', '.join(map(str, sorted(modes)))}, appearing {data2.count(modes[0])} time{'s' if data2.count(modes[0])>1 else ''}.\n\nAnswer: {', '.join(map(str, sorted(modes)))}", "easy")
    else:
        sv = statistics.variance(data)
        add("statistics", f"Compute the sample standard deviation (n-1) of {', '.join(map(str, data))}.", f"Mean = {float(mean):.3f}. Sum of squared deviations = {float(sum((Fr(x)-mean)**2 for x in data)):.3f}.\nSample variance = that / ({n}-1) = {sv:.3f}. Standard deviation = {math.sqrt(sv):.3f}.\n\nAnswer: ≈ {math.sqrt(sv):.3f}", "medium")

# 8. combinatorics
for _ in range(30*3):
    typ = R.randrange(4)
    if typ == 0:
        n, k = R.randint(5, 14), R.randint(2, 4)
        add("combinatorics", f"In how many ways can {k} people be chosen from {n}?", f"Order doesn't matter, so use combinations: C({n},{k}) = {n}! / ({k}! * {n-k}!) = {math.comb(n,k)}.\n\nAnswer: {math.comb(n,k)}", "easy")
    elif typ == 1:
        n, k = R.randint(6, 12), R.randint(2, 4)
        add("combinatorics", f"A race has {n} runners. How many ways can gold, silver and bronze be awarded?" if k == 3 else f"How many ordered ways can {k} of {n} candidates fill {k} distinct roles?",
            f"Order matters, so use permutations: P({n},{k}) = {n}!/({n-k})! = {math.perm(n,k)}.\n\nAnswer: {math.perm(n,k)}" if k != 3 else f"Order matters, so P({n},3) = {n}*{n-1}*{n-2} = {math.perm(n,3)}.\n\nAnswer: {math.perm(n,3)}", "easy")
    elif typ == 2:
        word = R.choice(["LEVEL", "BANANA", "ALLOCATE", "MISSISSIPPI", "STATISTICS", "BOOKKEEPER", "TOMORROW", "PEPPER"])
        from collections import Counter
        c = Counter(word)
        den = 1
        for v in c.values():
            den *= math.factorial(v)
        ans = math.factorial(len(word)) // den
        reps = ", ".join(f"{ch} x{v}" for ch, v in c.items() if v > 1)
        add("combinatorics", f"How many distinct arrangements are there of the letters in {word}?", f"{word} has {len(word)} letters, so {len(word)}! orderings, divided by the factorial of each repeated letter's count ({reps or 'no repeats'}).\n{len(word)}! = {math.factorial(len(word))}, divisor = {den}.\n\nAnswer: {ans}", "hard")
    else:
        n = R.randint(4, 9)
        add("combinatorics", f"A password has {n} characters, each a lowercase letter or digit, with repeats allowed. How many passwords are possible?", f"Each position has 26 + 10 = 36 options, and the choices are independent, so 36^{n} = {36**n}.\n\nAnswer: {36**n}", "medium")

# 9. discrete math / number theory
for _ in range(36*3):
    typ = R.randrange(7)
    if typ == 0:
        a, b = R.randint(12, 200), R.randint(12, 200)
        g = math.gcd(a, b)
        steps, x, y = [], max(a, b), min(a, b)
        while y:
            steps.append(f"{x} = {x//y}*{y} + {x%y}")
            x, y = y, x % y
        add("number_theory", f"Find gcd({a}, {b}) and lcm({a}, {b}).", f"Euclid's algorithm:\n" + "\n".join(steps) + f"\nThe last non-zero remainder is {g}, so gcd = {g}.\nlcm = a*b/gcd = {a}*{b}/{g} = {a*b//g}.\n\nAnswer: gcd = {g}, lcm = {a*b//g}", "medium")
    elif typ == 1:
        n = R.choice([60, 84, 90, 126, 180, 210, 252, 360, 462, 504, 693, 1001])
        f, m, p = {}, n, 2
        while m > 1:
            while m % p == 0:
                f[p] = f.get(p, 0) + 1
                m //= p
            p += 1
        txt = " * ".join(f"{p}^{e}" if e > 1 else str(p) for p, e in f.items())
        add("number_theory", f"What is the prime factorisation of {n}?", f"Divide by the smallest primes repeatedly until nothing is left: {n} = {txt}.\n\nAnswer: {txt}", "easy")
    elif typ == 2:
        n = R.randint(20, 255)
        add("number_theory", f"Convert {n} to binary and hexadecimal.", f"Binary: repeatedly divide by 2 and read remainders upward, giving {bin(n)[2:]}.\nHex: group the bits in fours from the right: {bin(n)[2:].zfill((len(bin(n))-2+3)//4*4)}, which is {hex(n)[2:].upper()}.\n\nAnswer: {n} = {bin(n)[2:]} (binary) = 0x{hex(n)[2:].upper()}", "easy")
    elif typ == 3:
        a, e, m = R.randint(2, 12), R.randint(5, 40), R.choice([7, 11, 13, 17, 19, 23])
        add("discrete_math", f"What is {a}^{e} mod {m}?", f"Reduce step by step using repeated squaring modulo {m}. The result is {pow(a,e,m)}.\n(Check: Fermat's little theorem gives a^({m}-1) ≡ 1 mod {m} when {m} doesn't divide {a}, so the exponent can be taken mod {m-1} = {e % (m-1)}.)\n\nAnswer: {pow(a,e,m)}", "hard")
    elif typ == 4:
        n = R.randint(15, 120)
        d = R.choice([3, 4, 6, 9, 11])
        add("discrete_math", f"What is the remainder when {n*7+3} is divided by {d}, and is {n*d} divisible by {d}?", f"{n*7+3} = {(n*7+3)//d}*{d} + {(n*7+3)%d}, so the remainder is {(n*7+3)%d}.\n{n*d} = {n}*{d}, so yes, it is divisible by {d}.\n\nAnswer: remainder {(n*7+3)%d}; yes", "easy")
    elif typ == 5:
        n = R.randint(3, 8)
        add("discrete_math", f"How many subsets does a set with {n} elements have, and how many have exactly 2 elements?", f"Each element is either in or out, so 2^{n} = {2**n} subsets. Subsets of size 2: C({n},2) = {math.comb(n,2)}.\n\nAnswer: {2**n} subsets; {math.comb(n,2)} of size 2", "easy")
    else:
        n = R.randint(4, 9)
        add("discrete_math", f"A graph has {n} vertices and every pair is connected by an edge (a complete graph). How many edges does it have?", f"Each edge is a pair of vertices, so the count is C({n},2) = {n}*{n-1}/2 = {math.comb(n,2)}.\n\nAnswer: {math.comb(n,2)}", "easy")

# 10. calculus concepts (polynomial derivatives / integrals / limits)
def poly_str(cs):
    parts = []
    for p, c in sorted(cs.items(), reverse=True):
        if c == 0:
            continue
        mag = abs(c)
        co = "" if mag == 1 and p != 0 else str(mag)
        v = "" if p == 0 else ("x" if p == 1 else f"x^{p}")
        parts.append((" - " if c < 0 else " + ") + co + v)
    s = "".join(parts).strip()
    return s[2:] if s.startswith("+ ") else ("-" + s[2:] if s.startswith("- ") else s)

for _ in range(30*3):
    cs = {p: R.randint(-6, 7) for p in range(0, R.randint(3, 5))}
    cs[max(cs)] = R.choice([1, 2, 3, 4, -2, 5])
    typ = R.randrange(3)
    if typ == 0:
        d = {p - 1: c * p for p, c in cs.items() if p > 0}
        x0 = R.randint(-2, 3)
        val = sum(c * x0 ** p for p, c in d.items())
        add("calculus", f"Differentiate f(x) = {poly_str(cs)} and evaluate f'({x0}).", f"Apply the power rule term by term: d/dx (c x^n) = c n x^(n-1), and the derivative of a constant is 0.\nf'(x) = {poly_str(d)}.\nf'({x0}) = {val}.\n\nAnswer: f'(x) = {poly_str(d)}; f'({x0}) = {val}", "medium")
    elif typ == 1:
        a, b = 0, R.randint(1, 4)
        integ = {p + 1: Fr(c, p + 1) for p, c in cs.items()}
        F = lambda x: sum(c * Fr(x) ** p for p, c in integ.items())
        val = F(b) - F(a)
        terms = " + ".join(f"({fr(c)})x^{p}" for p, c in sorted(integ.items(), reverse=True) if c != 0)
        add("calculus", f"Compute the definite integral of {poly_str(cs)} from 0 to {b}.", f"Integrate term by term using x^n -> x^(n+1)/(n+1): an antiderivative is {terms}.\nEvaluate at {b} and at 0 (which gives 0): F({b}) - F(0) = {fr(val)}.\n\nAnswer: {fr(val)} (≈ {float(val):.3f})", "hard")
    else:
        a = R.randint(1, 6)
        add("calculus", f"What is the limit of (x^2 - {a*a}) / (x - {a}) as x approaches {a}?", f"Direct substitution gives 0/0, so factor: x^2 - {a*a} = (x - {a})(x + {a}). Cancel (x - {a}) (valid for x ≠ {a}) leaving x + {a}, whose limit as x → {a} is {2*a}.\n\nAnswer: {2*a}", "medium")

# 11. matrices
def mstr(m):
    return "[" + "; ".join(" ".join(str(v) for v in row) for row in m) + "]"
for _ in range(30*3):
    A = [[R.randint(-4, 6) for _ in range(2)] for _ in range(2)]
    B = [[R.randint(-4, 6) for _ in range(2)] for _ in range(2)]
    typ = R.randrange(4)
    if typ == 0:
        C = [[sum(A[i][k] * B[k][j] for k in range(2)) for j in range(2)] for i in range(2)]
        add("matrices", f"Multiply A = {mstr(A)} by B = {mstr(B)} (rows separated by semicolons).", f"Entry (i,j) is row i of A dotted with column j of B.\nC11 = {A[0][0]}*{B[0][0]} + {A[0][1]}*{B[1][0]} = {C[0][0]}\nC12 = {A[0][0]}*{B[0][1]} + {A[0][1]}*{B[1][1]} = {C[0][1]}\nC21 = {A[1][0]}*{B[0][0]} + {A[1][1]}*{B[1][0]} = {C[1][0]}\nC22 = {A[1][0]}*{B[0][1]} + {A[1][1]}*{B[1][1]} = {C[1][1]}\n\nAnswer: AB = {mstr(C)}", "medium")
    elif typ == 1:
        det = A[0][0] * A[1][1] - A[0][1] * A[1][0]
        add("matrices", f"What is the determinant of {mstr(A)}? Is it invertible?", f"For a 2x2 matrix the determinant is ad - bc = {A[0][0]}*{A[1][1]} - {A[0][1]}*{A[1][0]} = {det}.\n{'It is non-zero, so the matrix is invertible.' if det else 'It is zero, so the matrix is singular and not invertible.'}\n\nAnswer: det = {det}", "easy")
    elif typ == 2:
        det = A[0][0] * A[1][1] - A[0][1] * A[1][0]
        if det == 0:
            continue
        inv = [[Fr(A[1][1], det), Fr(-A[0][1], det)], [Fr(-A[1][0], det), Fr(A[0][0], det)]]
        add("matrices", f"Find the inverse of {mstr(A)}.", f"det = {A[0][0]}*{A[1][1]} - {A[0][1]}*{A[1][0]} = {det}, which is non-zero.\nThe inverse is (1/det) * [d -b; -c a] = (1/{det}) * [{A[1][1]} {-A[0][1]}; {-A[1][0]} {A[0][0]}].\n\nAnswer: {mstr([[fr(v) for v in row] for row in inv])}", "hard")
    else:
        T = [[A[j][i] for j in range(2)] for i in range(2)]
        tr = A[0][0] + A[1][1]
        add("matrices", f"For A = {mstr(A)}, give the transpose and the trace.", f"The transpose swaps rows and columns: {mstr(T)}.\nThe trace is the sum of the diagonal: {A[0][0]} + {A[1][1]} = {tr}.\n\nAnswer: A^T = {mstr(T)}, trace = {tr}", "easy")

# 12. finance, rates, work, motion
for _ in range(36*3):
    typ = R.randrange(6)
    nm = R.choice(NAMES)
    if typ == 0:
        P, r, n = R.choice([1000, 2500, 5000, 8000]), R.choice([3, 4, 5, 6, 8]), R.randint(2, 10)
        A = P * (1 + r / 100) ** n
        add("finance_math", f"If I invest ${P} at {r}% compound interest per year, how much will I have after {n} years?", f"Use A = P(1 + r)^n with P = {P}, r = {r/100}, n = {n}.\nA = {P} * {1+r/100:.2f}^{n} = {A:.2f}.\n\nAnswer: about ${A:,.2f}. The interest earned is ${A-P:,.2f}.", "medium")
    elif typ == 1:
        d, t1 = R.choice([120, 150, 180, 240, 300]), R.choice([2, 3, 4, 5])
        add("rates", f"{nm} drives {d} km in {t1} hours. At that speed, how far in {t1+2} hours, and how long for {d*2} km?", f"Speed = {d}/{t1} = {fr(Fr(d,t1))} km/h.\nIn {t1+2} hours: {fr(Fr(d,t1))} * {t1+2} = {fr(Fr(d*(t1+2),t1))} km.\nTime for {d*2} km: {d*2} / {fr(Fr(d,t1))} = {2*t1} hours.\n\nAnswer: {fr(Fr(d*(t1+2),t1))} km; {2*t1} hours", "easy")
    elif typ == 2:
        a, b = R.choice([(3, 6), (4, 12), (6, 3), (5, 20), (10, 15), (2, 3)])
        both = Fr(1, a) + Fr(1, b)
        add("work_rates", f"One pipe fills a tank in {a} hours and another in {b} hours. How long do they take together?", f"Rates add. Pipe 1 fills 1/{a} per hour and pipe 2 fills 1/{b}. Together: 1/{a} + 1/{b} = {fr(both)} of the tank per hour.\nTime = 1 / {fr(both)} = {fr(1/both)} hours (≈ {float(1/both):.2f}).\n\nAnswer: {fr(1/both)} hours", "medium")
    elif typ == 3:
        age_c = R.randint(5, 14)
        mult = R.choice([2, 3, 4])
        yrs = R.randint(2, 8)
        # parent age = mult * child; in `yrs` years parent age = ?
        add("word_problems", f"{nm} is {mult} times as old as their child. The child is {age_c}. How old will {nm} be in {yrs} years?", f"{nm}'s age now: {mult} * {age_c} = {mult*age_c}. In {yrs} years: {mult*age_c} + {yrs} = {mult*age_c+yrs}.\n\nAnswer: {mult*age_c+yrs}", "easy")
    elif typ == 4:
        it = R.choice(ITEMS)
        a, pa, b, pb = R.randint(3, 9), R.choice([2, 3, 4, 5]), R.randint(2, 8), R.choice([6, 7, 8, 9])
        add("word_problems", f"{nm} buys {a} packs of {it} at ${pa} each and {b} more packs at ${pb} each. What is the total and the average price per pack?", f"Cost of the first group: {a}*{pa} = {a*pa}. Second group: {b}*{pb} = {b*pb}. Total = {a*pa+b*pb}.\nPacks = {a+b}. Average = {a*pa+b*pb}/{a+b} = {float(Fr(a*pa+b*pb,a+b)):.2f}.\n\nAnswer: total ${a*pa+b*pb}, average ${float(Fr(a*pa+b*pb,a+b)):.2f} per pack", "medium")
    else:
        s, e, k = R.randint(1, 5), R.randint(2, 6), R.randint(3, 8)
        tot = k * (2 * s + (k - 1) * e) // 2
        add("sequences", f"An arithmetic sequence starts at {s} with common difference {e}. What is the {k}th term and the sum of the first {k} terms?", f"The nth term is a + (n-1)d, so term {k} = {s} + {k-1}*{e} = {s+(k-1)*e}.\nThe sum of n terms is n(a + last)/2 = {k}*({s} + {s+(k-1)*e})/2 = {tot}.\n\nAnswer: term {k} = {s+(k-1)*e}; sum = {tot}", "medium")

# 13. computational reasoning / algorithm costs
for _ in range(32*3):
    typ = R.randrange(5)
    if typ == 0:
        n = R.choice([8, 16, 100, 1000, 1024, 5000, 1000000])
        add("algorithm_math", f"Roughly how many comparisons does binary search need on a sorted list of {n:,} items in the worst case?", f"Each comparison halves the search space, so the worst case is about ceil(log2({n:,}) + 1) steps. log2({n:,}) ≈ {math.log2(n):.2f}, so about {math.floor(math.log2(n))+1} comparisons.\n\nAnswer: about {math.floor(math.log2(n))+1}", "easy")
    elif typ == 1:
        n = R.randint(5, 40)
        add("algorithm_math", f"How many times does the inner statement run?\n\nfor i in range({n}):\n    for j in range(i):\n        work()", f"For each i the inner loop runs i times, so the total is 0 + 1 + ... + ({n}-1) = {n}*({n}-1)/2 = {n*(n-1)//2}. That is Θ(n²) growth.\n\nAnswer: {n*(n-1)//2}", "medium")
    elif typ == 2:
        n = R.randint(3, 10)
        add("algorithm_math", f"A recursive function f(n) = f(n-1) + f(n-2) with f(0)=0 and f(1)=1 is called. What is f({n}), and why is the naive recursion slow?", f"Computing the sequence: " + ", ".join(str(sum_) for sum_ in [__import__('functools').reduce(lambda ab, _: (ab[1], ab[0] + ab[1]), range(i), (0, 1))[0] for i in range(n + 1)]) + f".\nSo f({n}) = {__import__('functools').reduce(lambda ab, _: (ab[1], ab[0] + ab[1]), range(n), (0, 1))[0]}.\nThe naive recursion recomputes the same subproblems, so it takes roughly 1.6^n calls (exponential); memoisation or a loop makes it linear.\n\nAnswer: f({n}) = {__import__('functools').reduce(lambda ab, _: (ab[1], ab[0] + ab[1]), range(n), (0, 1))[0]}", "medium")
    elif typ == 3:
        n = R.choice([10, 20, 50, 100, 1000])
        add("algorithm_math", f"Compare the growth of n log2(n) and n^2 for n = {n}.", f"n log2(n) = {n} * {math.log2(n):.2f} ≈ {n*math.log2(n):,.0f}.\nn^2 = {n*n:,}.\nThe quadratic is about {n*n/(n*math.log2(n)):.1f} times larger, and the gap widens as n grows, which is why an O(n log n) sort beats an O(n²) one on large inputs.\n\nAnswer: n log n ≈ {n*math.log2(n):,.0f} versus n² = {n*n:,}", "easy")
    else:
        bits = R.choice([8, 10, 16, 24, 32])
        add("algorithm_math", f"How many distinct values can {bits} bits represent, and what is the largest unsigned value?", f"Each bit doubles the options, so 2^{bits} = {2**bits:,} values. Unsigned values run from 0 to 2^{bits} - 1 = {2**bits-1:,}.\n\nAnswer: {2**bits:,} values; max {2**bits-1:,}", "easy")

# 14. ratios, units, proportions, geometry
for _ in range(32*3):
    typ = R.randrange(5)
    if typ == 0:
        r, c = R.randint(2, 15), R.randint(2, 10)
        add("geometry", f"A circle has radius {r} cm. Give its circumference and area.", f"Circumference = 2πr = 2π*{r} = {2*math.pi*r:.2f} cm.\nArea = πr² = π*{r*r} = {math.pi*r*r:.2f} cm².\n\nAnswer: C ≈ {2*math.pi*r:.2f} cm, A ≈ {math.pi*r*r:.2f} cm²", "easy")
    elif typ == 1:
        a, b = R.choice([(3, 4), (5, 12), (8, 15), (6, 8), (7, 24), (9, 12)])
        add("geometry", f"A right triangle has legs {a} and {b}. What is the hypotenuse and the area?", f"Pythagoras: c = sqrt({a}² + {b}²) = sqrt({a*a+b*b}) = {math.sqrt(a*a+b*b):g}.\nArea = (1/2)*{a}*{b} = {a*b/2:g}.\n\nAnswer: hypotenuse {math.sqrt(a*a+b*b):g}, area {a*b/2:g}", "easy")
    elif typ == 2:
        x, y = R.choice([(3, 2), (5, 3), (7, 4), (2, 5)])
        tot = (x + y) * R.randint(4, 12)
        add("ratios", f"Two people share ${tot} in the ratio {x}:{y}. How much does each get?", f"The ratio has {x}+{y} = {x+y} parts, so one part is {tot}/{x+y} = {tot//(x+y)}.\nShares: {x}*{tot//(x+y)} = {x*(tot//(x+y))} and {y}*{tot//(x+y)} = {y*(tot//(x+y))}.\n\nAnswer: ${x*(tot//(x+y))} and ${y*(tot//(x+y))}", "easy")
    elif typ == 3:
        km = R.choice([5, 10, 21.1, 42.2, 8, 3.5])
        add("units", f"Convert {km} km to miles (1 mile = 1.609344 km) and to metres.", f"Miles = {km} / 1.609344 = {km/1.609344:.3f}.\nMetres = {km} * 1000 = {km*1000:g}.\n\nAnswer: ≈ {km/1.609344:.2f} miles; {km*1000:g} m", "easy")
    else:
        l, w, h = R.randint(2, 12), R.randint(2, 10), R.randint(2, 9)
        add("geometry", f"A box measures {l} by {w} by {h} cm. What is its volume and surface area?", f"Volume = {l}*{w}*{h} = {l*w*h} cm³.\nSurface area = 2(lw + lh + wh) = 2({l*w} + {l*h} + {w*h}) = {2*(l*w+l*h+w*h)} cm².\n\nAnswer: {l*w*h} cm³ and {2*(l*w+l*h+w*h)} cm²", "easy")

# 15. conceptual explanations (authored)
CONCEPTS = [
 ("Why is dividing by zero undefined?", "Division asks 'what number times the divisor gives the dividend?'. For 6/0 you would need a number n with n*0 = 6, but anything times 0 is 0, so no such n exists. For 0/0 every n works, so the answer is not unique. Either way there is no single well-defined value, which is why it is left undefined."),
 ("What is the difference between permutations and combinations?", "A permutation counts arrangements where order matters; a combination counts selections where order doesn't. Choosing 3 people for gold, silver and bronze is a permutation, P(n,3). Choosing 3 people for a committee is a combination, C(n,3), which is P(n,3)/3! because each group of 3 can be ordered 3! ways."),
 ("What does a derivative actually measure?", "The derivative measures instantaneous rate of change: how fast the output changes per unit of input at one point. Geometrically it's the slope of the tangent line there. For position over time, the derivative is velocity."),
 ("Explain standard deviation without formulas.", "Standard deviation is a typical distance of values from their average. Small means the data hugs the mean; large means it's spread out. Two classes can both average 70 on a test, but one with a small standard deviation has everyone near 70 while the other has a mix of very high and very low scores."),
 ("What is a matrix determinant good for?", "For a square matrix, the determinant tells you whether it is invertible (non-zero) and how it scales area or volume: a 2x2 matrix with determinant 3 stretches areas by a factor of 3, and a negative sign means orientation flips. A determinant of zero means the transformation squashes space into a lower dimension."),
 ("Why does 0.1 + 0.2 not equal 0.3 in most programming languages?", "Computers store most decimals in binary floating point, and 0.1 and 0.2 have no exact binary representation, just close approximations. Their sum comes out as 0.30000000000000004. Compare floats using a tolerance, or use decimal types for money."),
 ("What does Big-O notation tell me?", "Big-O describes how an algorithm's cost grows with input size n, ignoring constants and lower-order terms. O(n) doubles when n doubles; O(n²) quadruples; O(log n) barely moves. It is an upper bound on growth rate, useful for comparing algorithms at scale rather than predicting exact run time."),
 ("What's the intuition behind Bayes' theorem?", "It tells you how to update a belief after seeing evidence: the new probability depends on the old probability (the prior) and how likely the evidence is under each possibility. When a condition is rare, even an accurate test produces mostly false positives, which is the classic base-rate effect."),
 ("What is the difference between mean and median, and when does it matter?", "The mean is the arithmetic average; the median is the middle value when sorted. Outliers pull the mean but barely move the median, so for skewed data like incomes or house prices the median is usually a better 'typical' value."),
 ("What does it mean for events to be independent?", "Two events are independent if knowing one happened doesn't change the probability of the other: P(A and B) = P(A)P(B). Two coin flips are independent. Drawing two cards without replacement is not, because the first draw changes what is left."),
 ("Explain modular arithmetic in simple terms.", "Modular arithmetic is clock arithmetic: numbers wrap around after reaching a modulus. On a 12-hour clock, 9 + 5 = 14, which wraps to 2, written 14 ≡ 2 (mod 12). It is used in hashing, cryptography, checksums and cyclic schedules."),
 ("What is a proof by induction?", "Induction proves a statement for all natural numbers in two steps. First the base case: show it for the starting value. Then the inductive step: assume it holds for k and show it holds for k + 1. Like dominoes, the first falls and each knocks over the next."),
]
for q, a in CONCEPTS:
    add("math_concepts", q, a, "medium", "concept_" + q[:12])

save("math", out)
