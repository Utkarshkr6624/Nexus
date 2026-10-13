"""Phase 2.1 reasoning - formal-logic families, each verified by truth table / model enumeration / exhaustive search:
 A. propositional argument validity (14 argument forms x many domains)   B. quantifier syllogisms with counter-models
 C. counterexample hunting                                              E. liar/truth-teller statements about counts"""
from gen21_reason_util import *

# ------------------------------------------------------------------ A. propositional validity
ATOMS = [  # (positive clause, negative clause)
    ("the server is down", "the server is up"), ("the alert fires", "the alert stays silent"), ("the on-call engineer is paged", "nobody is paged"),
    ("it rains on Saturday", "it stays dry on Saturday"), ("the match is postponed", "the match goes ahead"), ("the stadium sells out", "tickets are left over"),
    ("the build passes", "the build fails"), ("the code is merged", "the code is not merged"), ("the release ships", "the release is held back"),
    ("the battery is below 5%", "the battery is above 5%"), ("the phone shows a warning", "the phone shows no warning"), ("the screen dims", "the screen stays bright"),
    ("Maya studies tonight", "Maya skips studying tonight"), ("Maya passes the exam", "Maya fails the exam"), ("Maya celebrates", "Maya does not celebrate"),
    ("the bridge is closed", "the bridge is open"), ("traffic moves to the ring road", "traffic stays on the bridge road"), ("the commute takes over an hour", "the commute takes under an hour"),
    ("the recipe uses fresh basil", "the recipe uses no fresh basil"), ("the sauce tastes bright", "the sauce tastes flat"), ("guests ask for seconds", "guests do not ask for seconds"),
    ("the plant gets direct sun", "the plant stays in the shade"), ("the leaves scorch", "the leaves stay green"), ("the owner moves the pot", "the owner leaves the pot"),
    ("the contract is signed", "the contract is unsigned"), ("the deposit is paid", "the deposit is unpaid"), ("the keys are handed over", "the keys stay with the agent"),
    ("the library is open", "the library is closed"), ("the study room is booked", "the study room is free"), ("Ravi finishes the draft", "Ravi does not finish the draft"),
]
TRIPLES = [(ATOMS[i], ATOMS[i + 1], ATOMS[i + 2]) for i in range(0, 30, 3)]
# also shifted triples to widen the pool
TRIPLES += [(ATOMS[i], ATOMS[i + 3], ATOMS[i + 6]) for i in (0, 3, 6, 9, 12, 15, 18, 21)]


def lit_txt(atoms, lit):
    i, s = lit
    return atoms[i][0] if s else atoms[i][1]


def ev(spec, v):
    k = spec[0]
    if k == "lit":
        return v[spec[1][0]] == spec[1][1]
    if k == "imp":
        return (not (v[spec[1][0]] == spec[1][1])) or (v[spec[2][0]] == spec[2][1])
    if k == "or":
        return v[spec[1][0]] == spec[1][1] or v[spec[2][0]] == spec[2][1]
    if k == "iff":
        return (v[spec[1][0]] == spec[1][1]) == (v[spec[2][0]] == spec[2][1])
    raise ValueError(k)


def render(spec, atoms, rng):
    k = spec[0]
    t = lambda l: lit_txt(atoms, l)
    if k == "lit":
        return cap(t(spec[1])) + "."
    if k == "imp":
        a, b = t(spec[1]), t(spec[2])
        return pick(rng, [f"If {a}, then {b}.", f"Whenever {a}, {b}.", f"{cap(b)} if {a}.", f"Every time {a}, {b}."])
    if k == "or":
        a, b = t(spec[1]), t(spec[2])
        return pick(rng, [f"Either {a}, or {b} (or both).", f"At least one of these is true: {a}; {b}."])
    if k == "iff":
        a, b = t(spec[1]), t(spec[2])
        return pick(rng, [f"{cap(a)} exactly when {b}.", f"{cap(a)} if and only if {b}."])


def render_concl(spec, atoms):
    t = lambda l: lit_txt(atoms, l)
    if spec[0] == "lit":
        return t(spec[1])
    if spec[0] == "imp":
        return f"if {t(spec[1])}, then {t(spec[2])}"
    raise ValueError


P0, N0, P1, N1, P2, N2 = (0, True), (0, False), (1, True), (1, False), (2, True), (2, False)
FORMS = [  # name, premises, conclusion, explanation key
    ("modus_ponens", [("imp", P0, P1), ("lit", P0)], ("lit", P1)),
    ("modus_tollens", [("imp", P0, P1), ("lit", N1)], ("lit", N0)),
    ("hyp_syllogism", [("imp", P0, P1), ("imp", P1, P2), ("lit", P0)], ("lit", P2)),
    ("chain_tollens", [("imp", P0, P1), ("imp", P1, P2), ("lit", N2)], ("lit", N0)),
    ("denying_antecedent", [("imp", P0, P1), ("lit", N0)], ("lit", N1)),
    ("affirming_consequent", [("imp", P0, P1), ("lit", P1)], ("lit", P0)),
    ("disj_syllogism", [("or", P0, P1), ("lit", N0)], ("lit", P1)),
    ("or_trap", [("or", P0, P1), ("lit", P0)], ("lit", N1)),
    ("iff_denial", [("iff", P0, P1), ("lit", N1)], ("lit", N0)),
    ("iff_affirm", [("iff", P0, P1), ("lit", P0)], ("lit", P1)),
    ("contrapositive", [("imp", P0, P1)], ("imp", N1, N0)),
    ("converse", [("imp", P0, P1)], ("imp", P1, P0)),
    ("inverse", [("imp", P0, P1)], ("imp", N0, N1)),
    ("shared_consequent", [("imp", P0, P1), ("imp", P2, P1), ("lit", P2)], ("lit", P0)),
]
WHY_VALID = {
    "modus_ponens": "The rule links the first condition to the second, and the first condition holds, so the second must hold as well (modus ponens).",
    "modus_tollens": "If the first condition were true, the rule would force the second one, but the second one is false. So the first condition cannot be true (modus tollens).",
    "hyp_syllogism": "The rules chain: the first condition forces the second, and the second forces the third. Since the first holds, the third is forced.",
    "chain_tollens": "Run the chain backwards: the last result is false, so the middle condition cannot hold (it would force the last), and then the first cannot hold either (it would force the middle).",
    "disj_syllogism": "At least one of the two is true and one of them is ruled out, so the other has to be the true one.",
    "iff_denial": "With an 'exactly when' link the two sides always match, so if one side is false the other is false too.",
    "iff_affirm": "With an 'exactly when' link the two sides always match, so if one side is true the other is true too.",
    "contrapositive": "A rule 'A leads to B' is logically identical to 'not B leads to not A': if B were missing, A could not have been present.",
}
WHY_INVALID = {
    "denying_antecedent": "The rule only says what follows when the first condition holds. It is silent about the case where it does not, so the second condition could still be true for another reason.",
    "affirming_consequent": "The rule goes one way only. The second condition can be caused by something else, so seeing it does not show that the first one happened.",
    "or_trap": "'Either ... or' here is inclusive, so both can be true together. Knowing that one is true tells us nothing about the other.",
    "converse": "Reversing a rule does not preserve its truth: 'A leads to B' does not say that B leads to A.",
    "inverse": "Negating both sides of a rule does not preserve its truth: 'A leads to B' says nothing about what happens without A.",
    "shared_consequent": "Two different conditions can lead to the same result. Knowing the result's second cause is true does not show that the first cause is also true.",
}
Q_STYLE = ["Does it follow that {c}?", "Can we conclude that {c}?", "Is it valid to infer that {c}?", "Must it be true that {c}?"]
INTRO = ["Consider these statements.", "Here are the facts.", "Premises:", "Suppose the following hold.", "Take these as given.", ""]


def gen_prop(rng):
    n = 0
    for name, prem, concl in FORMS:
        doms = rng.sample(TRIPLES, 7)
        for dom in doms:
            atoms = dom
            rows = list(itertools.product([True, False], repeat=3))
            valid = all(ev(concl, v) for v in rows if all(ev(p, v) for p in prem))
            expected_valid = name in WHY_VALID
            if valid != expected_valid:
                drop(f"prop {name} validity mismatch")
                continue
            lines = [render(p, atoms, rng) for p in prem]
            style = rng.randrange(3)
            if style == 0:
                q = f"{pick(rng, INTRO)} " + " ".join(lines) + " " + pick(rng, Q_STYLE).format(c=render_concl(concl, atoms))
            elif style == 1:
                q = "\n".join(f"{i + 1}. {l}" for i, l in enumerate(lines)) + "\n\n" + cap(pick(rng, Q_STYLE).format(c=render_concl(concl, atoms)))
            else:
                q = "Given: " + " ".join(lines) + f"\nQuestion: is '{cap(render_concl(concl, atoms))}' guaranteed?"
            q = q.strip()
            if valid:
                a = pick(rng, ["Yes. ", "Yes, it follows. ", "It does. ", "Valid. "]) + WHY_VALID[name]
                if rng.random() < 0.5:
                    a += f" So: {render_concl(concl, atoms)}."
            else:
                cx = next(v for v in rows if all(ev(p, v) for p in prem) and not ev(concl, v))
                used = sorted({lit[0] for spec in prem + [concl] for lit in spec[1:]})
                described = "; ".join(lit_txt(atoms, (k, cx[k])) for k in used)
                a = pick(rng, ["No, it does not follow. ", "Not necessarily. ", "No: the argument is invalid. "]) + WHY_INVALID[name] + f" A case that fits every premise but breaks the conclusion: {described}."
            emit(f"prop_{name}", [("user", q), ("assistant", a)], "medium" if name not in ("modus_ponens",) else "easy", f"prop-{name}", rng)
            n += 1
    return n


# ------------------------------------------------------------------ B. quantifier syllogisms
NOUNS = [("violinists", "musicians", "people who practise daily"), ("tulips", "flowering plants", "things that need sunlight"),
         ("hybrid cars", "vehicles with batteries", "cars that need rare metals"), ("nurses", "healthcare workers", "people who work night shifts"),
         ("sparrows", "birds", "animals that migrate"), ("python files", "text files", "files a linter can read"), ("poets", "writers", "people who enjoy silence"),
         ("sprinters", "athletes", "people who sleep nine hours"), ("bakers", "food workers", "people who start work before dawn"),
         ("tablets", "devices with touch screens", "devices that need charging"), ("oak trees", "trees", "things that lose their leaves"),
         ("lawyers", "professionals", "people who read contracts"), ("whales", "mammals", "animals that breathe air"), ("rooks", "chess pieces", "pieces that can move sideways"),
         ("novels", "books", "things with a plot"), ("prime numbers above 2", "odd numbers", "numbers not divisible by 3 unless 3 itself")]
STY = ["all", "no", "some", "somenot"]


def holds(stmt, model):
    t, x, y = stmt
    xs = [e for e in model if e[x]]
    if t == "all":
        return all(e[y] for e in xs)
    if t == "no":
        return not any(e[y] for e in xs)
    if t == "some":
        return any(e[y] for e in xs)
    return any(not e[y] for e in xs)


TYPES = list(itertools.product([False, True], repeat=3))  # membership in (A,B,C)


def find_models(prem, concl, maxn=4):
    """returns (any premise-satisfying model exists, first counter-model or None)"""
    sat = False
    for n in range(1, maxn + 1):
        for model in itertools.combinations_with_replacement(TYPES, n):
            if all(holds(p, model) for p in prem):
                sat = True
                if not holds(concl, model):
                    return sat, model
    # also the empty model (relevant for 'all'/'no' premises with a 'some'-free argument)
    if all(holds(p, ()) for p in prem):
        sat = True
        if not holds(concl, ()):
            return sat, ()
    return sat, None


def stmt_txt(s, nouns):
    t, x, y = s
    X, Y = nouns[x], nouns[y]
    return {"all": f"All {X} are {Y}.", "no": f"No {X} are {Y}.", "some": f"Some {X} are {Y}.", "somenot": f"Some {X} are not {Y}."}[t]


def gen_quant(rng, count=100):
    made = 0
    seen = set()
    NM = ["Ana", "Bo", "Cy", "Dee"]
    tries = 0
    while made < count and tries < 4000:
        tries += 1
        t1, t2, tc = rng.choice(STY), rng.choice(STY), rng.choice(STY)
        o1, o2, oc = rng.random() < 0.5, rng.random() < 0.5, rng.random() < 0.5
        p1 = (t1, 0, 1) if o1 else (t1, 1, 0)
        p2 = (t2, 1, 2) if o2 else (t2, 2, 1)
        c = (tc, 0, 2) if oc else (tc, 2, 0)
        key = (p1, p2, c)
        if key in seen:
            continue
        sat, cm = find_models([p1, p2], c)
        if not sat:
            continue
        valid = cm is None
        # balance valid / invalid ~ 45/55
        want_valid = made % 20 < 9
        if valid != want_valid:
            continue
        seen.add(key)
        nouns = rng.choice(NOUNS)
        q_head = pick(rng, ["Two premises:", "Given these two statements:", "Consider:", "Premises:"])
        qs = f"{q_head}\n1. {stmt_txt(p1, nouns)}\n2. {stmt_txt(p2, nouns)}\n\n" + pick(rng, [f"Does it follow that: {stmt_txt(c, nouns)}", f"Can we conclude: {stmt_txt(c, nouns)}", f"Is this conclusion guaranteed? {stmt_txt(c, nouns)}"])
        if valid:
            neg = {"all": "some of the first group are outside the second", "no": "some individual is in both groups", "some": "the two groups do not overlap at all",
                   "somenot": "every member of the first group is in the second"}[tc]
            body = pick(rng, ["Yes, the conclusion is guaranteed. ", "It follows. ", "Valid. "]) + (
                f"To see why, suppose it failed, i.e. {neg}. Testing that supposition against both premises, in every possible arrangement of up to four individuals, always contradicts at least one premise. "
                "(This reading treats groups as possibly empty, so a conclusion starting with 'some' needs a premise that guarantees an individual exists.)")
        else:
            desc = []
            for i, e in enumerate(cm):
                inn = [nouns[k] for k in range(3) if e[k]]
                out = [nouns[k] for k in range(3) if not e[k]]
                part = f"{NM[i]} is in " + oxford(inn) if inn else f"{NM[i]} is in none of the groups"
                if inn and out:
                    part += " but not in " + oxford(out, "or")
                desc.append(part)
            body = pick(rng, ["No, it does not follow. ", "Not guaranteed. ", "Invalid. "]) + (
                "A counter-example shows it: " + "; ".join(desc) + f". Both premises are true here, yet '{stmt_txt(c, nouns)[:-1]}' is false." if cm else
                "With no individuals at all, both premises hold vacuously while the conclusion fails.")
        emit("quantifier_syllogism", [("user", qs), ("assistant", body)], "medium" if valid else "hard", "quant", rng)
        made += 1
    return made


# ------------------------------------------------------------------ C. counterexamples
def is_prime(n):
    if n < 2:
        return False
    i = 2
    while i * i <= n:
        if n % i == 0:
            return False
        i += 1
    return True


def first_cx(pred, rng_=range(1, 2000)):
    for n in rng_:
        if not pred(n):
            return n
    return None


CLAIMS = [  # text, predicate over n, how to show the failure, short proof when true
    ("For every positive integer n, n^2 + n + 41 is prime.", lambda n: is_prime(n * n + n + 41), lambda n: f"n = {n} gives {n}^2 + {n} + 41 = {n * n + n + 41} = {n + 1} x {(n * n + n + 41) // (n + 1)}" if (n * n + n + 41) % (n + 1) == 0 else f"n = {n} gives {n * n + n + 41}, which is composite", None),
    ("If n is prime, then 2^n - 1 is prime.", lambda n: (not is_prime(n)) or is_prime(2 ** n - 1), lambda n: f"n = {n} is prime but 2^{n} - 1 = {2 ** n - 1} is not prime", None),
    ("For every positive integer n, n^2 > n.", lambda n: n * n > n, lambda n: f"n = {n} gives {n}^2 = {n * n}, which is not greater than {n}", None),
    ("If n^2 is divisible by 4, then n is divisible by 4.", lambda n: (n * n % 4 != 0) or (n % 4 == 0), lambda n: f"n = {n}: n^2 = {n * n} is divisible by 4 but {n} is not", None),
    ("For every positive integer n, 2^n > n^2.", lambda n: 2 ** n > n * n, lambda n: f"n = {n}: 2^{n} = {2 ** n} and {n}^2 = {n * n}, so 2^n is not larger", None),
    ("If n is odd, then n^2 + 2 is prime.", lambda n: n % 2 == 0 or is_prime(n * n + 2), lambda n: f"n = {n} is odd but {n}^2 + 2 = {n * n + 2} is not prime", None),
    ("If n is divisible by 6, then n is divisible by 12.", lambda n: n % 6 != 0 or n % 12 == 0, lambda n: f"n = {n} is divisible by 6 but not by 12", None),
    ("For every integer n >= 2, n^2 - 1 is composite.", lambda n: n < 2 or not is_prime(n * n - 1), lambda n: f"n = {n} gives {n * n - 1}, which is prime", None),
    ("If n is a multiple of 3 and n is even, then n is a multiple of 9.", lambda n: n % 6 != 0 or n % 9 == 0, lambda n: f"n = {n} is a multiple of 3 and even but not a multiple of 9", None),
    ("For every positive integer n, n^3 - n is divisible by 6.", lambda n: (n ** 3 - n) % 6 == 0, None, "n^3 - n = (n - 1) n (n + 1), a product of three consecutive integers. Among any three consecutive integers one is divisible by 3 and at least one is even, so the product is divisible by 6."),
    ("For every positive integer n, the sum 1 + 2 + ... + n equals n(n+1)/2.", lambda n: sum(range(1, n + 1)) * 2 == n * (n + 1), None, "Write the sum forwards and backwards and add: each of the n columns adds to n + 1, giving n(n+1) for twice the sum."),
    ("For every integer n, n^2 leaves remainder 0 or 1 when divided by 4.", lambda n: n * n % 4 in (0, 1), None, "If n = 2k then n^2 = 4k^2 (remainder 0). If n = 2k + 1 then n^2 = 4k^2 + 4k + 1 (remainder 1)."),
    ("For every positive integer n, n^2 + n is even.", lambda n: (n * n + n) % 2 == 0, None, "n^2 + n = n(n + 1), and one of two consecutive integers is always even."),
]
ASKS = ["Is this claim true? If not, find a counterexample.\n\n{c}", "Someone says: \"{c}\" Can you break it?", "Test the statement and tell me whether it always holds:\n{c}",
        "Claim: {c}\nIs there a counterexample?", "Check by searching for a counterexample (try small cases first). {c}"]


def gen_cx(rng):
    n = 0
    for text, pred, show, proof in CLAIMS:
        for _ in range(2 if show else 3):
            cx = first_cx(pred)
            q = pick(rng, ASKS).format(c=text)
            if cx is not None:
                a = pick(rng, ["The claim is false. ", "It does not always hold. ", "No, it fails. "]) + f"Counterexample: {show(cx)}. One counterexample is enough to refute a universal claim."
                if cx > 3:
                    a += f" (Trying n = 1 up to {cx - 1} first, nothing goes wrong, which is why checking only a few cases would have been misleading.)"
            else:
                a = pick(rng, ["Yes, it holds, and checking cases is not the proof. ", "This one is true. "]) + f"A search of n = 1 to 2000 finds no counterexample, but the reason it works is general: {proof}"
            emit("counterexample", [("user", q), ("assistant", a)], "medium", "cx", rng)
            n += 1
    # two-variable and gcd families
    for a_, b_ in [(4, 6), (6, 10), (4, 10), (6, 9), (10, 15), (8, 12), (6, 15), (9, 12), (14, 21), (4, 14)]:
        # claim: if a and b both divide n then a*b divides n
        m = next(m for m in range(1, 1000) if m % a_ == 0 and m % b_ == 0 and m % (a_ * b_) != 0)
        q = pick(rng, [f"True or false: if {a_} and {b_} both divide n, then {a_ * b_} divides n.", f"Does 'divisible by {a_} and by {b_}' always imply 'divisible by {a_ * b_}'?"])
        a = f"False. Take n = {m}: it is divisible by {a_} ({m} = {a_} x {m // a_}) and by {b_} ({m} = {b_} x {m // b_}), yet {m} is not a whole multiple of {a_ * b_} ({m} / {a_ * b_} = {Fr(m, a_ * b_)}). The rule only works when {a_} and {b_} share no common factor; here gcd({a_}, {b_}) = {__import__('math').gcd(a_, b_)}, and the correct statement is that lcm({a_}, {b_}) = {a_ * b_ // __import__('math').gcd(a_, b_)} divides n."
        emit("counterexample_gcd", [("user", q), ("assistant", a)], "medium", "cx2", rng)
        n += 1
    return n


# ------------------------------------------------------------------ E. statements about the number of liars
PEOPLE = ["Ada", "Ben", "Cleo", "Dev", "Eli", "Fay", "Gus", "Hana", "Ira", "Jo"]
FRAMES = [("islanders on an island where every person is either a truth-teller or a liar", "truth-teller", "liar"),
          ("contestants on a quiz show, each of whom always tells the truth or always lies", "truth-teller", "liar"),
          ("committee members, each of whom is strictly honest or strictly dishonest", "honest member", "dishonest member"),
          ("witnesses, each of whom either tells the truth every time or lies every time", "truth-teller", "liar")]


def statements_ok(assign, stmts):
    # assign[i] True = truth-teller. stmt: ("count", k) = 'exactly k of us are liars'; ("liar", j) = 'j is a liar'; ("honest", j); ("atleast", k)
    liars = sum(1 for a in assign if not a)
    for i, s in enumerate(stmts):
        t = s[0]
        if t == "count":
            val = liars == s[1]
        elif t == "atleast":
            val = liars >= s[1]
        elif t == "liar":
            val = not assign[s[1]]
        else:
            val = assign[s[1]]
        if val != assign[i]:
            return False
    return True


def stmt_words(s, people):
    t = s[0]
    if t == "count":
        return f"\"Exactly {s[1]} of us {'is a liar' if s[1] == 1 else 'are liars'}.\"" if s[1] else "\"None of us is a liar.\""
    if t == "atleast":
        return f"\"At least {s[1]} of us {'is a liar' if s[1] == 1 else 'are liars'}.\""
    if t == "liar":
        return f"\"{people[s[1]]} is a liar.\""
    return f"\"{people[s[1]]} is telling the truth.\""


def gen_liars(rng, count=45):
    made, tries = 0, 0
    while made < count and tries < 3000:
        tries += 1
        n = rng.choice([3, 3, 4, 4, 5])
        people = rng.sample(PEOPLE, n)
        stmts = []
        for i in range(n):
            t = rng.choice(["count", "count", "atleast", "liar", "honest"])
            if t in ("count", "atleast"):
                stmts.append((t, rng.randint(0 if t == "count" else 1, n)))
            else:
                j = rng.choice([x for x in range(n) if x != i])
                stmts.append((t, j))
        sols = [a for a in itertools.product([True, False], repeat=n) if statements_ok(a, stmts)]
        if len(sols) != 1:
            continue
        sol = sols[0]
        frame, tt, lt = rng.choice(FRAMES)
        lines = "\n".join(f"- {people[i]}: {stmt_words(stmts[i], people)}" for i in range(n))
        q = pick(rng, [f"There are {n} {frame}. They say:\n{lines}\n\nWho is who?", f"{n} {frame}. Statements:\n{lines}\nDetermine each person's type.",
                       f"{cap(frame)}: {n} of them speak.\n{lines}\n\nWhich of them are {tt}s and which are {lt}s?"])
        tl = [people[i] for i in range(n) if sol[i]]
        ll = [people[i] for i in range(n) if not sol[i]]
        a = pick(rng, ["Checking all " + str(2 ** n) + " possible assignments of types, exactly one is consistent. ", "Only one of the " + str(2 ** n) + " possible assignments survives. ", "Trying every assignment (" + str(2 ** n) + " in total) leaves a single consistent one. "])
        a += f"Truth-tellers: {oxford(tl) if tl else 'nobody'}. Liars: {oxford(ll) if ll else 'nobody'}.\n\nVerification: " + "; ".join(
            f"{people[i]} ({'tells the truth' if sol[i] else 'lies'}) says {stmt_words(stmts[i], people)[1:-1]} which is {'true' if sol[i] else 'false'}" for i in range(n)) + "."
        emit("liar_counts", [("user", q), ("assistant", a)], "hard", "liars", rng)
        made += 1
    return made


def run(rng):
    return {"prop": gen_prop(rng), "quant": gen_quant(rng), "cx": gen_cx(rng), "liars": gen_liars(rng)}
