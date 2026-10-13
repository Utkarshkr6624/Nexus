"""Phase 2.1 maths batch C: probability, combinatorics, number theory, modular arithmetic, sets/Venn, logic counting. All numbers computed."""
import itertools
import math
from fractions import Fraction as F
from gen21_math_common import *


# ----------------------------- probability -----------------------------
@shape(2, "probability", "medium")
def cards_two_draws():
    k = pick([("hearts", 13), ("aces", 4), ("face cards", 12), ("black cards", 26)])
    p = F(k[1], 52) * F(k[1] - 1, 51)
    return (f"Two cards are dealt from a standard 52-card deck without replacement. What's the probability that both are {k[0]}?",
            f"There are {k[1]} {k[0]} in the deck.\n\nP(first) = {k[1]}/52. Given that, P(second) = {k[1] - 1}/51.\n\nP(both) = {k[1]}/52 x {k[1] - 1}/51 = **{frac(p)}** (about {float(p):.4f}).")


@shape(2, "probability", "medium")
def dice_sum_at_least():
    n = pick([2, 3])
    t = ri(n * 3 + 1, n * 6 - 1)
    tot = 6 ** n
    fav = sum(1 for r in itertools.product(range(1, 7), repeat=n) if sum(r) >= t)
    return (f"Roll {n} fair six-sided dice. What is the probability that the total is at least {t}?",
            f"There are 6^{n} = {tot} equally likely outcomes. Counting the outcomes with sum >= {t} gives {fav}.\n\nP = {fav}/{tot} = **{frac(F(fav, tot))}** (about {fav / tot:.4f}).")


@shape(2, "probability", "medium")
def urn_marbles():
    r, b, g = ri(3, 8), ri(2, 7), ri(1, 5)
    n = r + b + g
    p_same = F(r, n) * F(r - 1, n - 1) + F(b, n) * F(b - 1, n - 1) + F(g, n) * F(g - 1, n - 1)
    return (f"A bag holds {r} red, {b} blue and {g} green marbles. Two are drawn without replacement. What is the probability they are the same colour?",
            f"Add the chance for each colour.\n\n- Red: {r}/{n} x {r - 1}/{n - 1} = {frac(F(r, n) * F(r - 1, n - 1))}\n- Blue: {b}/{n} x {b - 1}/{n - 1} = {frac(F(b, n) * F(b - 1, n - 1))}\n- Green: {g}/{n} x {g - 1}/{n - 1} = {frac(F(g, n) * F(g - 1, n - 1))}\n\nTotal = **{frac(p_same)}** (about {float(p_same):.4f}).")


@shape(2, "probability", "hard")
def conditional_test():
    prev = pick([F(1, 100), F(1, 50), F(1, 200)])
    sens, spec = pick([F(95, 100), F(99, 100), F(90, 100)]), pick([F(95, 100), F(90, 100), F(98, 100)])
    pp = prev * sens + (1 - prev) * (1 - spec)
    post = prev * sens / pp
    return (f"A condition affects {float(prev) * 100:g}% of people. A test detects it {float(sens) * 100:g}% of the time when present and gives a false positive {float((1 - spec)) * 100:g}% of the time when absent. If someone tests positive, what is the chance they actually have it?",
            f"Use Bayes' theorem.\n\nP(positive) = {float(prev):g} x {float(sens):g} + {float(1 - prev):g} x {float(1 - spec):g} = {float(pp):.5f}.\n\nP(condition | positive) = {float(prev * sens):.5f} / {float(pp):.5f} = **{float(post) * 100:.1f}%**.\n\nEven with an accurate test, a rare condition means most positives are false alarms.")


@shape(2, "probability", "medium")
def expected_value_game():
    cost = ri(2, 6)
    prizes = [(ri(10, 30), F(1, ri(8, 15))), (ri(2, 8), F(1, ri(3, 6)))]
    ev = sum(p * pr for p, pr in prizes)
    return (f"A game costs ${cost} to play. You win ${prizes[0][0]} with probability {frac(prizes[0][1])}, ${prizes[1][0]} with probability {frac(prizes[1][1])}, and nothing otherwise. What is the expected profit per play?",
            f"Expected winnings = {prizes[0][0]} x {frac(prizes[0][1])} + {prizes[1][0]} x {frac(prizes[1][1])} = {frac(ev)} = {float(ev):.3f}.\n\nExpected profit = {float(ev):.3f} - {cost} = **{float(ev - cost):+.3f} per play**. " + ("Over many plays you'd expect to lose money." if ev < cost else "Over many plays you'd expect to come out ahead."))


@shape(2, "probability", "medium")
def at_least_one():
    p = pick([F(1, 6), F(1, 10), F(1, 4), F(1, 20)])
    n = ri(3, 12)
    ans = 1 - (1 - p) ** n
    return (f"Each independent attempt succeeds with probability {frac(p)}. What is the chance of at least one success in {n} attempts?",
            f"It's easier to find the opposite: no successes in {n} tries = ({frac(1 - p)})^{n} = {float((1 - p) ** n):.4f}.\n\nAt least one = 1 - {float((1 - p) ** n):.4f} = **{float(ans):.4f}** ({float(ans) * 100:.1f}%).")


@shape(2, "probability", "medium")
def binomial_exact():
    n, k = ri(5, 10), ri(2, 4)
    p = pick([F(1, 2), F(1, 3), F(1, 4), F(2, 3)])
    pr = math.comb(n, k) * p ** k * (1 - p) ** (n - k)
    return (f"A coin lands heads with probability {frac(p)}. What is the probability of exactly {k} heads in {n} flips?",
            f"Binomial: C({n},{k}) p^{k} (1-p)^{n - k} = {math.comb(n, k)} x ({frac(p)})^{k} x ({frac(1 - p)})^{n - k} = **{float(pr):.4f}**.")


@shape(2, "probability", "easy")
def birthday_like():
    n = ri(8, 30)
    pn = 1.0
    for i in range(n):
        pn *= (365 - i) / 365
    return (f"In a room of {n} people, what's the probability that at least two share a birthday? (Ignore leap days and assume uniform birthdays.)",
            f"Find the probability everyone's birthday is different: (365/365)(364/365)...({365 - n + 1}/365) = {pn:.4f}.\n\nSo P(at least one match) = 1 - {pn:.4f} = **{1 - pn:.3f}**. The number is surprisingly high because the number of pairs ({n * (n - 1) // 2}) grows quickly.")


# ----------------------------- combinatorics -----------------------------
@shape(2, "combinatorics", "medium")
def committee_selection():
    m, w = ri(4, 8), ri(4, 8)
    k = ri(3, 4)
    tot = math.comb(m + w, k)
    two = math.comb(m, 2) * math.comb(w, k - 2)
    return (f"A club has {m} men and {w} women. How many ways can a committee of {k} be chosen, and how many of those have exactly 2 men?",
            f"Any committee: C({m + w},{k}) = **{tot}**.\n\nExactly 2 men: choose 2 of {m} men and {k - 2} of {w} women: C({m},2) x C({w},{k - 2}) = {math.comb(m, 2)} x {math.comb(w, k - 2)} = **{two}**.")


@shape(2, "combinatorics", "medium")
def seating_rows():
    n = ri(5, 8)
    return (f"{n} friends sit in a row. How many arrangements are there, and how many if two particular friends must sit together?",
            f"All arrangements: {n}! = **{math.factorial(n):,}**.\n\nTreat the pair as one block: ({n} - 1)! arrangements of blocks x 2 orders inside the block = {math.factorial(n - 1)} x 2 = **{2 * math.factorial(n - 1):,}**.")


@shape(2, "combinatorics", "medium")
def password_count():
    L = ri(6, 10)
    return (f"A password is {L} characters from 26 lowercase letters and 10 digits (repetition allowed). How many passwords exist, and how many have no repeated character?",
            f"Any: 36^{L} = **{36 ** L:,}**.\n\nNo repeats: 36 x 35 x ... x {36 - L + 1} = **{math.perm(36, L):,}**.\n\nThe ratio is {math.perm(36, L) / 36 ** L:.3f}, so banning repeats removes only a modest share of the space.")


@shape(2, "combinatorics", "hard")
def stars_and_bars():
    n, k = ri(8, 14), ri(3, 5)
    return (f"In how many ways can {n} identical sweets be given to {k} children if a child may get none?",
            f"Stars and bars: choose positions for {k - 1} dividers among {n} + {k - 1} slots.\n\nC({n + k - 1}, {k - 1}) = **{math.comb(n + k - 1, k - 1)}**.\n\nIf every child must get at least one, give each one first and distribute the remaining {n - k}: C({n - 1}, {k - 1}) = {math.comb(n - 1, k - 1)}.")


@shape(2, "combinatorics", "medium")
def word_arrangements():
    w = pick(["BANANA", "LETTER", "COMMITTEE", "STATISTICS", "BOOKKEEPER", "PEPPER", "BALLOON"])
    from collections import Counter
    c = Counter(w)
    den = 1
    for v in c.values():
        den *= math.factorial(v)
    return (f"How many distinct arrangements are there of the letters in {w}?",
            f"{len(w)} letters, so {len(w)}! = {math.factorial(len(w)):,} if all were different. Repeated letters ({', '.join(f'{k} x{v}' for k, v in c.items() if v > 1)}) make some arrangements identical, so divide by {' x '.join(f'{v}!' for v in c.values() if v > 1)} = {den}.\n\n**{math.factorial(len(w)) // den:,}** arrangements.")


@shape(2, "combinatorics", "medium")
def handshakes_games():
    n = ri(6, 25)
    return (f"{n} teams play a league where every team plays every other team once. How many matches are played, and how many if each pair plays twice (home and away)?",
            f"Pairs of teams = C({n},2) = {n}({n} - 1)/2 = **{n * (n - 1) // 2}** matches.\n\nHome and away doubles it: **{n * (n - 1)}**.")


@shape(2, "combinatorics", "hard")
def inclusion_exclusion():
    N = ri(60, 200)
    a, b = pick([2, 3, 4]), pick([5, 7])
    cnt = N // a + N // b - N // (a * b)
    return (f"How many integers from 1 to {N} are divisible by {a} or by {b}?",
            f"Inclusion-exclusion: |A| + |B| - |A and B|.\n\n- Divisible by {a}: {N} // {a} = {N // a}\n- Divisible by {b}: {N} // {b} = {N // b}\n- Divisible by both (by {a * b}): {N // (a * b)}\n\nTotal = {N // a} + {N // b} - {N // (a * b)} = **{cnt}**.")


# ----------------------------- number theory / modular -----------------------------
@shape(2, "number_theory", "easy")
def gcd_lcm_pair():
    a, b = ri(12, 90), ri(12, 90)
    g = math.gcd(a, b)
    return (f"Find the greatest common divisor and least common multiple of {a} and {b}.",
            f"Euclid's algorithm gives gcd({a}, {b}) = **{g}**.\n\nlcm = a x b / gcd = {a} x {b} / {g} = **{a * b // g}**.")


@shape(2, "number_theory", "medium")
def modular_clock():
    a, m = ri(100, 900), pick([7, 12, 24, 5])
    return (f"What day of the week is it {a} days after a Monday? And what is {a} mod {m}?",
            f"Days repeat every 7. {a} = 7 x {a // 7} + {a % 7}, so the day is {['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'][a % 7]}.\n\n{a} mod {m} = **{a % m}** because {a} = {m} x {a // m} + {a % m}.")


@shape(2, "number_theory", "hard")
def modular_power():
    b, e, m = ri(2, 12), ri(20, 200), pick([7, 11, 13, 17, 100])
    return (f"What is the last two digits of {b}^{e}? Also compute {b}^{e} mod {m}.",
            f"Last two digits means mod 100. Using repeated squaring and reducing at each step: {b}^{e} mod 100 = **{pow(b, e, 100):02d}**.\n\n{b}^{e} mod {m} = **{pow(b, e, m)}**.")


@shape(2, "number_theory", "medium")
def linear_congruence():
    m = pick([7, 11, 13, 15, 17])
    while True:
        a = ri(2, m - 1)
        if math.gcd(a, m) == 1:
            break
    x = ri(1, m - 1)
    b = a * x % m
    return (f"Solve {a}x = {b} (mod {m}).",
            f"Since gcd({a}, {m}) = 1, {a} has an inverse mod {m}: {a} x {pow(a, -1, m)} = {a * pow(a, -1, m)} = 1 (mod {m}).\n\nMultiply both sides by {pow(a, -1, m)}: x = {b} x {pow(a, -1, m)} = {b * pow(a, -1, m)} = **{x}** (mod {m}).\n\nCheck: {a} x {x} = {a * x} = {b} (mod {m}).")


@shape(2, "number_theory", "medium")
def divisibility_digit_sum():
    n = ri(10000, 999999)
    return (f"Without a calculator, how can you tell whether {n} is divisible by 3, 9 and 11?",
            f"- **By 3 or 9:** add the digits: {' + '.join(str(n))} = {sum(map(int, str(n)))}. {sum(map(int, str(n)))} is {'divisible' if sum(map(int, str(n))) % 3 == 0 else 'not divisible'} by 3 and {'divisible' if sum(map(int, str(n))) % 9 == 0 else 'not divisible'} by 9.\n- **By 11:** alternate the signs of the digits from the right: the alternating sum is {sum(int(d) * (-1) ** i for i, d in enumerate(reversed(str(n))))}, which is {'divisible' if sum(int(d) * (-1) ** i for i, d in enumerate(reversed(str(n)))) % 11 == 0 else 'not divisible'} by 11.\n\nDirect check: {n} mod 3 = {n % 3}, mod 9 = {n % 9}, mod 11 = {n % 11}.")


@shape(2, "number_theory", "medium")
def count_primes_range():
    lo = ri(10, 60)
    hi = lo + ri(20, 60)
    ps = [n for n in range(lo, hi + 1) if n > 1 and all(n % d for d in range(2, int(n ** 0.5) + 1))]
    return (f"List the primes between {lo} and {hi} inclusive and say how many there are.",
            f"Test each number for divisors up to its square root. The primes are {', '.join(map(str, ps))}.\n\nThat's **{len(ps)}** primes.")


@shape(2, "number_theory", "medium")
def perfect_squares_between():
    a = ri(50, 400)
    b = a + ri(100, 800)
    cnt = math.isqrt(b) - math.isqrt(a - 1)
    return (f"How many perfect squares are there from {a} to {b}?",
            f"The smallest square >= {a} is {(math.isqrt(a - 1) + 1) ** 2} ({math.isqrt(a - 1) + 1}^2) and the largest <= {b} is {math.isqrt(b) ** 2} ({math.isqrt(b)}^2).\n\nCount = {math.isqrt(b)} - {math.isqrt(a - 1) + 1} + 1 = **{cnt}**.")


@shape(2, "number_theory", "easy")
def base_conversion():
    n = ri(20, 500)
    return (f"Convert {n} to binary and to hexadecimal, and back-check the binary.",
            f"Binary: repeatedly divide by 2. {n} = **{bin(n)[2:]}** in binary.\nHexadecimal: **{hex(n)[2:].upper()}**.\n\nBack-check: " + " + ".join(f"{2 ** i}" for i, d in enumerate(reversed(bin(n)[2:])) if d == "1") + f" = {n}.")


# ----------------------------- sets / logic / counting -----------------------------
@shape(2, "sets", "medium")
def venn_two_sets():
    t = ri(40, 100)
    a, b = ri(20, t - 10), ri(20, t - 10)
    both = ri(max(0, a + b - t), min(a, b))
    neither = t - (a + b - both)
    return (f"In a class of {t}, {a} students play football, {b} play chess, and {both} play both. How many play neither?",
            f"At least one sport: {a} + {b} - {both} = {a + b - both}.\n\nNeither = {t} - {a + b - both} = **{neither}**. (Football only: {a - both}; chess only: {b - both}.)")


@shape(2, "sets", "hard")
def venn_three_sets():
    A, B_, C = ri(20, 40), ri(20, 40), ri(20, 40)
    ab, bc, ac = ri(5, 12), ri(5, 12), ri(5, 12)
    abc = ri(2, 4)
    u = A + B_ + C - ab - bc - ac + abc
    return (f"In a survey, {A} like tea, {B_} like coffee and {C} like juice. {ab} like tea and coffee, {bc} like coffee and juice, {ac} like tea and juice, and {abc} like all three. How many people like at least one drink?",
            f"Inclusion-exclusion: |A| + |B| + |C| - |A and B| - |B and C| - |A and C| + |A and B and C|.\n\n= {A} + {B_} + {C} - {ab} - {bc} - {ac} + {abc} = **{u}**.")


@shape(2, "sets", "easy")
def subsets_count():
    n = ri(4, 10)
    return (f"A pizza place offers {n} toppings. How many different topping combinations can you order, including plain, and how many use exactly 3 toppings?",
            f"Each topping is either on or off: 2^{n} = **{2 ** n}** combinations including none.\n\nExactly 3: C({n},3) = **{math.comb(n, 3)}**.")


@shape(2, "logic", "medium")
def liars_all_say_same():
    n = ri(3, 6)
    k = ri(1, n)
    sols = [liars for liars in range(n + 1) if all(((liars == k) == (role == "truth")) for role in (["truth"] * (n - liars) + ["lie"] * liars))]
    return (f"On an island each of {n} people either always tells the truth or always lies. Every one of them says: 'Exactly {k} of us are liars.' How many liars are there?",
            f"Suppose L people are liars. The statement is true exactly when L = {k}.\n\n- If L = {k}, the statement is true, so the liars would be telling the truth, which is impossible unless there are no liars (L = 0). That would need {k} = 0.\n- If L is not {k}, the statement is false, so the truth-tellers would be lying, which is impossible unless there are no truth-tellers (L = {n}).\n\n" + (f"So the only consistent case is **L = {n}**: everyone is a liar, and 'exactly {k}' is false because {k} != {n}." if k != n else f"With k = {n}, L = {n} would make the statement true, so nobody can be consistent: **there is no consistent assignment**.") + f" (Brute-force check over all L from 0 to {n}: {'solutions ' + str(sols) if sols else 'no solutions'}.)")


@shape(2, "logic", "medium")
def pigeonhole_months():
    N = ri(14, 60)
    m = ri(2, 6)
    need = 12 * (m - 1) + 1
    must = N >= need
    return (f"Among {N} people, must at least {m} of them share a birth month? Explain.",
            f"There are 12 months. The most people you can have with at most {m - 1} per month is 12 x {m - 1} = {12 * (m - 1)}.\n\n" + (f"Since {N} > {12 * (m - 1)}, **yes**: some month must have at least {m} people." if must else f"Since {N} <= {12 * (m - 1)}, **no**: the people could be spread out with at most {m - 1} per month.") + f" In general some month holds at least ceil({N}/12) = {math.ceil(N / 12)} people.")


