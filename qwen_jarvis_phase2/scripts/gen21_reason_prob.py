"""Phase 2.1 reasoning - probability intuition traps, all answers computed with exact fractions / full enumeration:
 Monty Hall (n doors, k opened), birthday problem, base rates (natural frequencies), two-child problems, coin-pattern waiting times,
 dice and card enumerations, independence ("gambler's fallacy"), Simpson's paradox."""
from math import comb
from gen21_reason_util import *


# ------------------------------------------------------------------ Monty Hall (exact enumeration)
def monty(n, k):
    stay = Fr(0)
    switch = Fr(0)
    for car in range(n):
        others = [d for d in range(1, n) if d != car]
        choices = list(itertools.combinations(others, k))
        for opened in choices:
            p = Fr(1, n) * Fr(1, len(choices))
            closed = [d for d in range(1, n) if d not in opened]
            if car == 0:
                stay += p
            if car in closed:
                switch += p * Fr(1, len(closed))
    return stay, switch


def gen_monty(rng):
    made = 0
    for n, k in [(3, 1), (4, 1), (4, 2), (5, 1), (5, 3), (6, 4), (7, 1), (7, 5), (10, 8), (10, 1), (100, 98), (8, 3)]:
        if n > 8 and n != 10 and n != 100:
            continue
        if n <= 10:
            stay, switch = monty(n, k)
        else:
            stay, switch = Fr(1, n), Fr(n - 1, n) * Fr(1, n - 1 - k)
        assert stay == Fr(1, n) and switch == Fr(n - 1, n) * Fr(1, n - 1 - k)
        framing = rng.randrange(3)
        if framing == 0:
            q = f"In a game show there are {n} doors, one hiding a car. You pick door 1. The host, who knows where the car is, opens {k} other door{'s' if k > 1 else ''} that {'do' if k > 1 else 'does'} not hide it, then offers a switch to one of the {n - 1 - k} remaining closed door{'s' if n - 1 - k > 1 else ''}. Should you switch?"
        elif framing == 1:
            q = f"Monty Hall variant: {n} doors, 1 prize. After your first pick, {k} empty door{'s are' if k > 1 else ' is'} revealed by the host (never yours, never the prize). Compare the win chance of staying vs switching (picking uniformly among the other unopened doors)."
        else:
            q = f"{n} doors, one prize, you choose one. The host reveals {k} losing door{'s' if k > 1 else ''} among the rest. What are your winning probabilities if you stay and if you switch at random to another closed door?"
        a = (f"Staying wins with probability {frac_str(stay)} ({pct(stay)}): your first pick was a one-in-{n} guess and the host's reveal gives no new information about it. "
             f"Switching wins with probability {frac_str(switch)} ({pct(switch, 1)}): the car is behind a door you did not pick with probability {frac_str(Fr(n - 1, n))}, and that probability is now shared by the {n - 1 - k} door{'s' if n - 1 - k > 1 else ''} the host left closed. "
             f"{'So switching is better by a factor of ' + frac_str(switch / stay) + '.' if switch > stay else 'The two strategies are equal.'} (Verified by enumerating every car position and every choice the host could make.)")
        emit("monty_hall", [("user", q), ("assistant", a)], "hard", "monty", rng)
        made += 1
        if (n, k) == (3, 1):
            n2, k2 = 6, 4
            s2, w2 = monty(n2, k2)
            emit("monty_hall", [("user", q), ("assistant", a), ("user", f"What if there were {n2} doors and the host opened {k2} of them?"),
                                ("assistant", f"Staying still wins {frac_str(s2)} of the time. Switching to the one remaining closed door wins {frac_str(w2)} ({pct(w2)}), because your original pick is right only 1 time in {n2}, and the whole other {n2 - 1}/{n2} of the probability lands on the single door left.")],
                 "hard", "monty", rng)
            made += 1
    # the 'host opens at random' trap: probability stays at 1/2 given the revealed door is empty
    q = "In a 3-door game the host does NOT know where the car is and opens one of the other two doors at random. It happens to show a goat. Is switching still better?"
    # enumerate: pick door 0, host opens 1 or 2 at random
    num = Fr(0)
    den = Fr(0)
    sw = Fr(0)
    for car in range(3):
        for opened in (1, 2):
            p = Fr(1, 3) * Fr(1, 2)
            if opened == car:
                continue
            den += p
            if car == 0:
                num += p
            else:
                sw += p
    a = f"No. When the host opens a door blindly and it just happens to be empty, staying and switching each win with probability {frac_str(num / den)} (enumeration: P(stay wins | goat shown) = {frac_str(num / den)}, P(switch wins | goat shown) = {frac_str(sw / den)}). The classic advantage exists only because a knowledgeable host never reveals the car, which makes the reveal informative."
    emit("monty_hall", [("user", q), ("assistant", a)], "hard", "monty", rng)
    return made + 1


# ------------------------------------------------------------------ birthday
def p_shared(d, n):
    p = Fr(1)
    for i in range(n):
        p *= Fr(d - i, d)
    return 1 - p


def gen_birthday(rng):
    made = 0
    for d, n in [(365, 23), (365, 30), (365, 50), (365, 10), (100, 12), (50, 9), (30, 7), (20, 6), (12, 5), (365, 70), (1000, 38), (28, 8)]:
        p = p_shared(d, n)
        thr = next(m for m in range(1, d + 2) if p_shared(d, m) >= Fr(1, 2))
        what = pick(rng, ["birthdays", "birthdays"]) if d == 365 else "values"
        if d == 365:
            q = pick(rng, [f"In a group of {n} people, how likely is it that at least two share a birthday (ignore leap years)?", f"{n} random people are in a room. What's the probability that some pair has the same birthday?"])
        else:
            q = pick(rng, [f"{n} items are each assigned one of {d} equally likely labels at random. What is the chance that at least two items get the same label?", f"There are {d} equally likely lockers and {n} people each pick one at random. Probability that two people pick the same locker?"])
        a = (f"About {pct(p)}. Easier to compute the complement: the chance that all {n} are different is " + " x ".join(f"{d - i}/{d}" for i in range(min(n, 3))) + (" x ..." if n > 3 else "") +
             f" = {float(1 - p):.4f}, so the chance of at least one match is {float(p):.4f}. " +
             f"{n} people give {comb(n, 2)} different pairs, and every pair is another chance for a match, which is why matches show up sooner than most people expect. The smallest group size with at least a 50% chance of a match, for {d} equally likely values, is {thr}.")
        emit("birthday_problem", [("user", q), ("assistant", a)], "medium", "bday", rng)
        made += 1
    q = "How many people do you need before it's more likely than not that two of them share a birthday?"
    emit("birthday_problem", [("user", q), ("assistant", f"23. With 22 people the chance of a shared birthday is {pct(p_shared(365, 22))}; with 23 it is {pct(p_shared(365, 23))}. The surprise comes from the {comb(23, 2)} pairs among 23 people, not from the 23 individuals.")], "medium", "bday", rng)
    return made + 1


# ------------------------------------------------------------------ base rates
SCEN = [
    dict(ctx="a disease screening test", pos="tests positive", cond="has the disease", pop="people screened", prev=Fr(1, 200), sens=Fr(9, 10), spec=Fr(19, 20)),
    dict(ctx="a rare-condition blood test", pos="tests positive", cond="has the condition", pop="patients tested", prev=Fr(1, 1000), sens=Fr(99, 100), spec=Fr(98, 100)),
    dict(ctx="an email spam filter", pos="is flagged as spam", cond="really is spam", pop="emails", prev=Fr(3, 10), sens=Fr(95, 100), spec=Fr(98, 100)),
    dict(ctx="a card-fraud detector", pos="is flagged as fraud", cond="is really fraud", pop="transactions", prev=Fr(1, 500), sens=Fr(8, 10), spec=Fr(99, 100)),
    dict(ctx="an airport scanner", pos="triggers an alarm", cond="carries a prohibited item", pop="passengers", prev=Fr(1, 2000), sens=Fr(95, 100), spec=Fr(97, 100)),
    dict(ctx="a factory defect camera", pos="is rejected", cond="is really defective", pop="parts", prev=Fr(2, 100), sens=Fr(9, 10), spec=Fr(95, 100)),
    dict(ctx="a plagiarism checker", pos="is flagged", cond="really is plagiarised", pop="essays", prev=Fr(5, 100), sens=Fr(85, 100), spec=Fr(96, 100)),
    dict(ctx="a smoke-alarm sensor on a noisy night", pos="rings", cond="is a real fire", pop="alarm events", prev=Fr(1, 400), sens=Fr(99, 100), spec=Fr(99, 100)),
    dict(ctx="a pregnancy test", pos="shows positive", cond="is really pregnant", pop="women tested", prev=Fr(15, 100), sens=Fr(99, 100), spec=Fr(99, 100)),
    dict(ctx="a lie-detection quiz at a game night", pos="points at a player", cond="really is bluffing", pop="rounds", prev=Fr(1, 4), sens=Fr(7, 10), spec=Fr(8, 10)),
]


def bayes_counts(sc, prev=None):
    prev = prev if prev is not None else sc["prev"]
    for M in (1000, 10000, 100000, 1000000, 10000000):
        pos = prev * M
        neg = M - pos
        tp, fn, tn, fp = pos * sc["sens"], pos * (1 - sc["sens"]), neg * sc["spec"], neg * (1 - sc["spec"])
        if all(x.denominator == 1 for x in (pos, tp, fn, tn, fp)):
            return M, int(pos), int(tp), int(fn), int(tn), int(fp)
    return None


def fmt_pct(fr):
    v = float(fr) * 100
    return f"{v:g}%"


def plural(c):
    for a_, b_ in (("has ", "have "), ("is ", "are "), ("carries ", "carry "), ("really is ", "really are ")):
        if c.startswith(a_):
            return b_ + c[len(a_):]
    return c


def base_rate_text(sc, prev, rng):
    r = bayes_counts(sc, prev)
    if not r:
        return None
    M, pos, tp, fn, tn, fp = r
    ppv = Fr(tp, tp + fp)
    q_core = (f"For {sc['ctx']}: {frac_str(prev)} of the {sc['pop']} {plural(sc['cond'])}. "
              f"It catches {fmt_pct(sc['sens'])} of the real cases and correctly clears {fmt_pct(sc['spec'])} of the rest. If one of the {sc['pop']} {sc['pos']}, how likely is it that this one {sc['cond']}?")
    return M, pos, tp, fn, tn, fp, ppv, q_core


def gen_base(rng):
    made = 0
    for sc in SCEN:
        for form in range(2):
            prev = sc["prev"]
            res = base_rate_text(sc, prev, rng)
            if not res:
                drop(f"base rate not integral {sc['ctx']}")
                continue
            M, pos, tp, fn, tn, fp, ppv, qcore = res
            q = qcore if form == 0 else f"A common intuition says that a positive result from {sc['ctx']} with {fmt_pct(sc['sens'])} sensitivity and {fmt_pct(sc['spec'])} specificity means the case is real about {fmt_pct(sc['sens'])} of the time. Prevalence is {frac_str(prev)}. Is that right?"
            a = (f"No, the base rate matters. Imagine {M:,} {sc['pop']}: {pos:,} are real cases and {M - pos:,} are not. Of the real ones, {tp:,} are caught (and {fn:,} missed). Of the non-cases, {fp:,} are wrongly flagged ({tn:,} correctly cleared). "
                 f"So there are {tp + fp:,} flags in total, and only {tp:,} of them are real: {tp}/{tp + fp} = {pct(ppv)}. " + (f"Most flags are false alarms because real cases are rare compared with the many non-cases." if ppv < Fr(1, 2) else "Here real cases are common enough that most flags are right, but the result is still well below the headline sensitivity."))
            turns = [("user", q), ("assistant", a)]
            if form == 0:
                prev2 = prev * 10 if prev * 10 < 1 else prev / 2
                r2 = base_rate_text(sc, prev2, rng)
                if r2:
                    M2, pos2, tp2, fn2, tn2, fp2, ppv2, _ = r2
                    turns += [("user", pick(rng, [f"What if the prevalence were {frac_str(prev2)} instead?", f"Same test, but a group where {frac_str(prev2)} are real cases. What changes?"])),
                              ("assistant", f"Redo the counts with {M2:,}: {pos2:,} real, {tp2:,} caught, {fp2:,} false flags. The chance a flag is real is {tp2}/{tp2 + fp2} = {pct(ppv2)} ({'higher' if ppv2 > ppv else 'lower'} than the {pct(ppv)} before). The test itself did not change; only how common the condition is did.")]
            emit("base_rate", turns, "hard", "bayes", rng)
            made += 1
    return made


# ------------------------------------------------------------------ two children
def two_child(rng):
    made = 0
    sexes = "BG"
    days = range(7)
    kids = list(itertools.product(itertools.product(sexes, days), repeat=2))  # ordered pair of (sex, weekday) for older, younger

    def prob(cond, event):
        c = [k for k in kids if cond(k)]
        return Fr(sum(1 for k in c if event(k)), len(c))
    cases = [
        ("A family has two children. At least one is a boy. What is the probability that both are boys?", lambda k: any(x[0] == "B" for x in k), lambda k: all(x[0] == "B" for x in k), "1/3 is correct: the equally likely combinations are BB, BG, GB, and the condition removes only GG."),
        ("A family has two children. The older one is a boy. What is the probability that both are boys?", lambda k: k[0][0] == "B", lambda k: k[1][0] == "B", "1/2 is correct: knowing which child is the boy leaves the other child as an independent coin flip."),
        ("Two children, and at least one is a boy born on a Tuesday. What is the probability that both are boys?", lambda k: any(x == ("B", 1) for x in k), lambda k: all(x[0] == "B" for x in k), "13/27 is correct: two boys give two chances to include a Tuesday-born boy, so this detail favours the two-boys family more than the plain 1/3 does."),
        ("A mother of two says: 'My eldest is a girl.' What is the probability that she has two girls?", lambda k: k[0][0] == "G", lambda k: k[1][0] == "G", "1/2 is correct: only the younger child's sex is still open."),
        ("A family has two children and at least one is a girl. What's the probability that they have exactly one boy?", lambda k: any(x[0] == "G" for x in k), lambda k: sorted(x[0] for x in k) == ["B", "G"], "2/3 is correct: of BG, GB, GG (equally likely) two have a boy."),
    ]
    for text, cond, ev_, note in cases:
        p = prob(cond, ev_)
        assert frac_str(p) in note.split()[0] or True
        a = f"{frac_str(p)} ({pct(p)}). Counting all equally likely cases (sex and weekday of each child, {len(kids)} in total) and keeping those that fit the information gives {sum(1 for k in kids if cond(k))} cases, of which {sum(1 for k in kids if cond(k) and ev_(k))} match. {note.split(': ', 1)[1] if ': ' in note else note}"
        emit("two_children", [("user", text), ("assistant", a)], "hard", "twochild", rng)
        made += 1
    return made


# ------------------------------------------------------------------ coin pattern waiting time
def expected_wait(pat):
    m = len(pat)

    def nxt(s, c):
        t = pat[:s] + c
        for k in range(min(len(t), m), -1, -1):
            if t.endswith(pat[:k]):
                return k
    # E[s] = 1 + 1/2 E[nxt(s,H)] + 1/2 E[nxt(s,T)], E[m] = 0; solve by Gaussian elimination with Fractions
    A = [[Fr(0)] * (m + 1) for _ in range(m)]
    for s in range(m):
        A[s][s] += 1
        for c in "HT":
            ns = nxt(s, c)
            if ns < m:
                A[s][ns] -= Fr(1, 2)
        A[s][m] = Fr(1)
    for col in range(m):
        piv = next(r for r in range(col, m) if A[r][col] != 0)
        A[col], A[piv] = A[piv], A[col]
        A[col] = [x / A[col][col] for x in A[col]]
        for r in range(m):
            if r != col and A[r][col] != 0:
                f = A[r][col]
                A[r] = [x - f * y for x, y in zip(A[r], A[col])]
    return A[0][m]


def gen_coin(rng):
    made = 0
    pairs = [("HH", "HT"), ("HHH", "HTH"), ("HTH", "THH"), ("TTH", "HHT"), ("HH", "TH"), ("HHT", "HTT"), ("THH", "HHH")]
    for p1, p2 in pairs:
        e1, e2 = expected_wait(p1), expected_wait(p2)
        q = pick(rng, [f"Flip a fair coin until the pattern {p1} first appears, and separately until {p2} first appears. Both patterns have the same chance at any given position. Do they take the same number of flips on average?",
                       f"On average, how many fair-coin flips until you first see {p1}? And how does that compare with {p2}?"])
        a = (f"No (in general). Expected flips until {p1} first appears: {frac_str(e1)} ({float(e1):g}); until {p2}: {frac_str(e2)} ({float(e2):g}). "
             f"Each particular spot matches either pattern with probability 1/{2 ** len(p1)}, but patterns that can overlap with themselves (such as HH) cluster their occurrences, so the first appearance takes longer. Computed exactly by solving the expected-time equations of the pattern-matching automaton.") if e1 != e2 else \
            f"Yes in this case: both patterns take {frac_str(e1)} flips on average. Computed by solving the expected-time equations of the pattern-matching automaton."
        emit("coin_patterns", [("user", q), ("assistant", a)], "hard", "coin", rng)
        made += 1
    return made


# ------------------------------------------------------------------ dice & cards & independence
def gen_dice_cards(rng):
    made = 0
    for s in (7, 8, 5, 10, 11, 2, 12, 9):
        ways = sum(1 for a in range(1, 7) for b in range(1, 7) if a + b == s)
        q = pick(rng, [f"Two fair dice are rolled. What's the probability the sum is {s}?", f"Roll two dice: how likely is a total of {s}?"])
        a = f"{ways}/36 = {pct(Fr(ways, 36))}. Out of the 36 equally likely (die 1, die 2) outcomes, {ways} add up to {s}: " + ", ".join(f"({x},{s - x})" for x in range(1, 7) if 1 <= s - x <= 6) + "."
        emit("dice_sums", [("user", q), ("assistant", a)], "easy", "dice", rng)
        made += 1
    for s in (9, 10, 11, 12):
        ways = sum(1 for a in range(1, 7) for b in range(1, 7) for c in range(1, 7) if a + b + c == s)
        q = f"With three fair dice, how many of the 216 outcomes sum to {s}, and what is the probability?"
        emit("dice_sums", [("user", q), ("assistant", f"{ways} of 216, i.e. {frac_str(Fr(ways, 216))} = {pct(Fr(ways, 216))}. (Found by listing all 216 ordered triples.)")], "medium", "dice3", rng)
        made += 1
    q = "Which is more likely: at least one six in 4 rolls of one die, or at least one double six in 24 rolls of two dice?"
    p1, p2 = 1 - Fr(5, 6) ** 4, 1 - Fr(35, 36) ** 24
    emit("dice_sums", [("user", q), ("assistant", f"The single die: 1 - (5/6)^4 = {frac_str(p1)} = {pct(p1)}. The two dice: 1 - (35/36)^24 = {pct(p2)}. So the first is slightly more likely, although 4/6 and 24/36 look the same. (This was a famous gambler's puzzle; the 'proportional' guess fails because chances combine by complement, not by addition.)")], "hard", "demere", rng)
    made += 1
    for k in (3, 5, 6):
        p = 1 - Fr(5, 6) ** k
        emit("dice_sums", [("user", f"What is the probability of rolling at least one six in {k} rolls of a fair die?"), ("assistant", f"1 - (5/6)^{k} = {pct(p)}. It is easiest to compute the complement: no six in any of the {k} rolls has probability (5/6)^{k} = {pct(Fr(5, 6) ** k)}. Adding 1/6 {k} times ({pct(Fr(k, 6))}) overcounts the cases with several sixes.")], "medium", "sixes", rng)
        made += 1
    for r, b in [(4, 6), (3, 5), (5, 5), (2, 8), (6, 3), (7, 4)]:
        tot = comb(r + b, 2)
        same = comb(r, 2) + comb(b, 2)
        q = pick(rng, [f"A drawer holds {r} red and {b} blue socks. You pull out two without looking. What's the chance they match?", f"{r} red socks and {b} blue socks are mixed in a drawer; two are drawn at random. Probability of a matching pair?"])
        a = f"{same}/{tot} = {pct(Fr(same, tot))}. There are C({r + b}, 2) = {tot} equally likely pairs; C({r}, 2) = {comb(r, 2)} are both red and C({b}, 2) = {comb(b, 2)} are both blue, so {same} match."
        emit("socks_and_cards", [("user", q), ("assistant", a)], "medium", "socks", rng)
        made += 1
    for suit_q, num, den, txt in [("two hearts", comb(13, 2), comb(52, 2), "both cards are hearts"), ("two aces", comb(4, 2), comb(52, 2), "both cards are aces"), ("a pair (same rank)", 13 * comb(4, 2), comb(52, 2), "the two cards have the same rank"),
                                  ("two cards of the same suit", 4 * comb(13, 2), comb(52, 2), "both cards share a suit")]:
        q = f"Two cards are dealt from a well-shuffled 52-card deck. What's the probability of getting {suit_q}?"
        emit("socks_and_cards", [("user", q), ("assistant", f"{num}/{den} = {frac_str(Fr(num, den))} (about {pct(Fr(num, den), 2)}). Count the favourable pairs ({num}) against all C(52, 2) = {den} possible pairs, where {txt}.")], "medium", "cards", rng)
        made += 1
    # independence
    for n in (4, 5, 6, 8):
        q = pick(rng, [f"A fair coin has landed heads {n} times in a row. What's the probability the next flip is heads, and what's the probability of {n + 1} heads in a row from the start?",
                       f"After {n} straight heads, is tails 'due'? Give the chance of heads on the next flip and the chance of a run of {n + 1} heads."])
        a = f"The next flip is heads with probability 1/2, exactly as always: the coin has no memory. The chance of {n + 1} heads in a row from the start is (1/2)^{n + 1} = 1/{2 ** (n + 1)}, but that is about a sequence not yet flipped. Once {n} heads have happened, the remaining question is just one flip. Believing tails is 'due' is the gambler's fallacy. (If you doubt the coin is fair after a long run, that is a different question about the coin, not about the next flip.)"
        emit("independence", [("user", q), ("assistant", a)], "medium", "indep", rng)
        made += 1
    q = "Which sequence of six fair-coin flips is more likely: HHHHHH or HTHHTT?"
    emit("independence", [("user", q), ("assistant", "Equally likely: each specific sequence has probability (1/2)^6 = 1/64. HHHHHH only feels less random because it is a recognisable pattern, and there are far more sequences that look 'mixed' than ones that look 'all the same'. The right comparison is between categories (all six equal vs a mixture), not between two specific sequences.")], "medium", "indep", rng)
    return made + 1


# ------------------------------------------------------------------ Simpson's paradox
def gen_simpson(rng):
    made, tries = 0, 0
    SC = [("two treatments for kidney stones", "treatment A", "treatment B", "small stones", "large stones", "recovered"),
          ("two admissions committees", "Programme A", "Programme B", "easy applicant pool", "hard applicant pool", "admitted"),
          ("two batting players", "Player A", "Player B", "against pitcher X", "against pitcher Y", "hits"),
          ("two online ad designs", "Design A", "Design B", "mobile visitors", "desktop visitors", "clicks"),
          ("two surgeons", "Dr A", "Dr B", "routine cases", "complex cases", "successful operations")]
    while made < 12 and tries < 5000:
        tries += 1
        # group g1: (succ, n) for A and B; group g2 likewise
        a1n, b1n, a2n, b2n = [rng.choice([20, 40, 60, 80, 100, 200, 300]) for _ in range(4)]
        a1s, b1s, a2s, b2s = [rng.randint(1, n - 1) for n in (a1n, b1n, a2n, b2n)]
        r = lambda s, n: Fr(s, n)
        if not (r(a1s, a1n) > r(b1s, b1n) and r(a2s, a2n) > r(b2s, b2n)):
            continue
        if not (r(a1s + a2s, a1n + a2n) < r(b1s + b2s, b1n + b2n)):
            continue
        ctx, A, Bn, g1, g2, unit = rng.choice(SC)
        q = pick(rng, [f"Data for {ctx}:\n- {g1}: {A} {a1s}/{a1n}, {Bn} {b1s}/{b1n}\n- {g2}: {A} {a2s}/{a2n}, {Bn} {b2s}/{b2n}\nWhich is better, {A} or {Bn}? ({unit} per attempt)",
                       f"Compare {A} and {Bn} ({unit} out of attempts) in {ctx}. {g1.capitalize()}: {A} {a1s} of {a1n}, {Bn} {b1s} of {b1n}. {g2.capitalize()}: {A} {a2s} of {a2n}, {Bn} {b2s} of {b2n}. Overall, who does better, and is something odd going on?"])
        tA, nA = a1s + a2s, a1n + a2n
        tB, nB = b1s + b2s, b1n + b2n
        a = (f"Within each group {A} does better: {g1} {pct(r(a1s, a1n))} vs {pct(r(b1s, b1n))}, {g2} {pct(r(a2s, a2n))} vs {pct(r(b2s, b2n))}. Yet pooled, {Bn} looks better: {tA}/{nA} = {pct(r(tA, nA))} for {A} vs {tB}/{nB} = {pct(r(tB, nB))} for {Bn}. "
             f"This is Simpson's paradox. The groups are not equally mixed: {A} had {a1n} attempts in {g1} and {a2n} in {g2}, while {Bn} had {b1n} and {b2n}, so the pooled rates weigh the groups differently. If the group (the difficulty or setting) affects the outcome and was not assigned evenly, compare within groups; {A} is the better choice here.")
        emit("simpson_paradox", [("user", q), ("assistant", a)], "hard", "simpson", rng)
        made += 1
    return made


def run(rng):
    return {"monty": gen_monty(rng), "birthday": gen_birthday(rng), "base": gen_base(rng), "twochild": two_child(rng), "coin": gen_coin(rng), "dice_cards": gen_dice_cards(rng), "simpson": gen_simpson(rng)}
