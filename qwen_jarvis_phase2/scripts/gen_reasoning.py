"""Category 3: reasoning & problem solving (target ~1500). Puzzles are solved by brute force, so answers are verified."""
import itertools
from fractions import Fraction as Fr
from lib import *

CAT = "reasoning"
rng = seeded("reasoning")
R = rng
out = []


def add(sub, q, a, diff="medium", g=None, src="computed"):
    out.append(mk(CAT, sub, [("user", q), ("assistant", a)], g or sub, diff, src, pick(rng, SYS_GENERAL)))


NAMES = ["Maya", "Arjun", "Lena", "Omar", "Sofia", "Ken", "Priya", "Tom", "Aisha", "Diego", "Nora", "Ravi", "Chloe", "Ivan", "Zara", "Ben", "Hana", "Luis", "Mira", "Sam"]

# ---------- 1. ordering puzzles ----------
ATTR = [("tall", "taller", "shortest", "tallest"), ("old", "older", "youngest", "oldest"), ("fast", "faster", "slowest", "fastest"),
        ("heavy", "heavier", "lightest", "heaviest"), ("early", "earlier", "last", "first")]
for _ in range(110*3):
    n = R.choice([3, 4, 4, 5])
    people = R.sample(NAMES, n)
    adj, comp, low, high = R.choice(ATTR)
    truth = people[:]  # truth[0] is highest
    # generate clues until unique
    pairs = [(truth[i], truth[j]) for i in range(n) for j in range(i + 1, n)]
    R.shuffle(pairs)
    clues = []
    def sols(cl):
        c = 0
        for perm in itertools.permutations(people):
            pos = {p: i for i, p in enumerate(perm)}
            if all(pos[a] < pos[b] for a, b in cl):
                c += 1
        return c
    for p in pairs:
        clues.append(p)
        if sols(clues) == 1:
            break
        if len(clues) > n + 1 and sols(clues) == 1:
            break
    else:
        continue
    # drop redundant clues
    for c in clues[:]:
        rest = [x for x in clues if x != c]
        if rest and sols(rest) == 1:
            clues = rest
    R.shuffle(clues)
    cl_txt = ". ".join(f"{a} is {comp} than {b}" for a, b in clues) + "."
    order = " > ".join(truth)
    steps = []
    for a, b in clues:
        steps.append(f"{a} > {b}")
    add("ordering", f"{cl_txt} Who is the {high} and who is the {low}? Give the full order.",
        f"Treat each clue as an ordering: {', '.join(steps)}.\nChain them together: {order}.\nThat is the only order consistent with every clue.\n\nAnswer: {truth[0]} is the {high}, {truth[-1]} is the {low}. Full order from {high} to {low}: {', '.join(truth)}.", "medium" if n < 5 else "hard")

# ---------- 2. syllogisms ----------
CLASSES = [("cats", "mammals", "animals"), ("roses", "flowers", "plants"), ("squares", "rectangles", "polygons"), ("sparrows", "birds", "animals"),
           ("laptops", "computers", "machines"), ("oaks", "trees", "plants"), ("sedans", "cars", "vehicles"), ("novels", "books", "publications"),
           ("pianists", "musicians", "artists"), ("python scripts", "programs", "files"), ("doctors", "professionals", "people"), ("rivers", "waterways", "geographic features")]
for _ in range(60*3):
    a, b, c = R.choice(CLASSES)
    typ = R.randrange(5)
    if typ == 0:
        add("syllogism", f"All {a} are {b}. All {b} are {c}. Does it follow that all {a} are {c}?", f"Yes. Every {a[:-1] if a.endswith('s') else a} is in the group of {b}, and the whole group of {b} sits inside {c}, so {a} sit inside {c} too. This is a valid chain (Barbara form).\n\nAnswer: Yes, all {a} are {c}.", "easy")
    elif typ == 1:
        add("syllogism", f"All {a} are {b}. Does it follow that all {b} are {a}?", f"No. 'All {a} are {b}' puts {a} inside {b}, but {b} can contain things that are not {a}. Reversing a universal statement is a classic mistake (affirming the converse).\n\nAnswer: No, it does not follow.", "easy")
    elif typ == 2:
        x = R.choice(NAMES)
        add("syllogism", f"{x}'s pet is one of the {a}. All {a} are {b}. What can we conclude about {x}'s pet?", f"The pet belongs to {a}, and {a} are all {b}, so the pet is one of the {b}. We cannot conclude anything beyond that from these two statements.\n\nAnswer: {x}'s pet is one of the {b}.", "easy")
    elif typ == 3:
        add("syllogism", f"No {a} are {R.choice(['reptiles','machines','minerals','languages'])}. {b.capitalize()} include all {a}. Is it valid to say that no {b} are in that excluded group?", f"No. Knowing that none of the {a} are in the excluded group says nothing about the other members of {b}. {b.capitalize()} are a bigger set, and the rest of them could belong to the excluded group.\n\nAnswer: Not valid; the premises only cover {a}.", "medium")
    else:
        x = R.choice(NAMES)
        add("syllogism", f"If something is among the {b}, it is among the {c}. {x}'s item is not among the {c}. Is it among the {b}?", f"Use the contrapositive: 'if {b} then {c}' is equivalent to 'if not {c} then not {b}'. The item is not among the {c}, so it cannot be among the {b}.\n\nAnswer: No.", "medium")

# ---------- 3. knights and knaves ----------
for _ in range(90*3):
    a, b = R.sample(NAMES, 2)
    forms = [
        (f"{a} says: 'We are both knights.'", lambda A, B: (A == (A and B))),
        (f"{a} says: 'At least one of us is a knave.'", lambda A, B: (A == ((not A) or (not B)))),
        (f"{a} says: '{b} is a knave.'", lambda A, B: (A == (not B))),
        (f"{a} says: 'We are both knaves.'", lambda A, B: (A == ((not A) and (not B)))),
        (f"{a} says: 'We are the same kind.'", lambda A, B: (A == (A == B))),
    ]
    txt, f = R.choice(forms)
    sols = [(A, B) for A in (True, False) for B in (True, False) if f(A, B)]
    kind = lambda v: "knight" if v else "knave"
    if len(sols) != 1:
        continue
    A, B = sols[0]
    rows = []
    for AA in (True, False):
        for BB in (True, False):
            rows.append(f"- {a} {kind(AA)}, {b} {kind(BB)}: " + ("consistent" if f(AA, BB) else "contradiction (the statement's truth doesn't match what a " + kind(AA) + " would say)"))
    add("knights_knaves", f"On an island, knights always tell the truth and knaves always lie. {txt} What are {a} and {b}?",
        f"Knights tell the truth and knaves lie, so {a}'s statement has to be true exactly when {a} is a knight. Check all four combinations:\n" + "\n".join(rows) + f"\n\nOnly one combination survives.\n\nAnswer: {a} is a {kind(A)} and {b} is a {kind(B)}.", "hard")

# ---------- 4. scheduling feasibility (earliest deadline first) ----------
TASKS = ["write the report", "review the pull request", "prepare slides", "fix the login bug", "update the budget", "email the client", "book travel", "study for the quiz",
         "refactor the parser", "draft the proposal", "test the release", "clean the dataset", "plan the sprint", "record the demo"]
for _ in range(80*3):
    k = R.choice([3, 4, 4, 5])
    ts = R.sample(TASKS, k)
    dur = [R.choice([1, 1, 2, 2, 3, 4]) for _ in ts]
    total = sum(dur)
    feasible = R.random() < 0.55
    # deadlines in hours from now
    order = list(range(k))
    R.shuffle(order)
    cum, dl = 0, {}
    for i in order:
        cum += dur[i]
        dl[i] = cum + (R.randint(0, 2) if feasible else 0)
    if not feasible:
        j = R.choice(order)
        dl[j] = max(1, dl[j] - R.randint(2, 4))
    seq = sorted(range(k), key=lambda i: dl[i])
    t, ok, trace = 0, True, []
    for i in seq:
        t += dur[i]
        late = t > dl[i]
        ok = ok and not late
        trace.append(f"{ts[i]} finishes at hour {t} (deadline {dl[i]}){' ✗ late' if late else ' ✓'}")
    listing = "; ".join(f"{ts[i]} takes {dur[i]}h and is due in {dl[i]}h" for i in range(k))
    add("scheduling", f"I have one person working sequentially, starting now. Tasks: {listing}. Can everything be done on time?",
        f"With a single worker, the best order for meeting deadlines is earliest deadline first. Sorted by deadline:\n" + "\n".join(f"{n+1}. {l}" for n, l in enumerate(trace)) +
        f"\n\nTotal work is {total} hours.\n\nAnswer: {'Yes, every task can finish on time in that order.' if ok else 'No. Even with the best ordering at least one deadline is missed, so something has to be cut, delegated or extended.'}", "medium")

# ---------- 5. dependency planning / critical path ----------
PROJ = [("launch a landing page", ["design mockup", "write copy", "build page", "review", "deploy"]),
        ("ship a feature", ["spec", "implement", "write tests", "code review", "release"]),
        ("organise a workshop", ["choose date", "book venue", "invite attendees", "prepare materials", "run session"]),
        ("publish a paper", ["collect data", "analyse", "draft", "revise", "submit"]),
        ("move offices", ["pick new space", "sign lease", "order furniture", "pack", "move in"])]
for _ in range(60*3):
    pname, steps = R.choice(PROJ)
    n = len(steps)
    dur = {s: R.randint(1, 5) for s in steps}
    deps = {steps[0]: []}
    for i in range(1, n):
        cand = steps[:i]
        deps[steps[i]] = R.sample(cand, R.randint(1, min(2, len(cand))))
    finish = {}
    for s in steps:
        start = max([finish[d] for d in deps[s]], default=0)
        finish[s] = start + dur[s]
    total = max(finish.values())
    # critical path
    path = [max(finish, key=finish.get)]
    while deps[path[-1]]:
        path.append(max(deps[path[-1]], key=lambda d: finish[d]))
    path.reverse()
    desc = "; ".join(f"{s} ({dur[s]}d{', needs ' + ' and '.join(deps[s]) if deps[s] else ''})" for s in steps)
    add("critical_path", f"To {pname}: {desc}. Independent steps can run in parallel. What is the minimum total time and which steps decide it?",
        "Work forward, computing the earliest finish of each step as its longest prerequisite finish plus its own duration:\n" + "\n".join(f"- {s}: finishes day {finish[s]}" for s in steps) +
        f"\n\nThe longest chain is {' -> '.join(path)}, so delays to those steps delay everything.\n\nAnswer: {total} days; the critical path is {' -> '.join(path)}.", "hard")

# ---------- 6. weighted decisions ----------
OPT = [("laptops", ["Atlas", "Borealis", "Cedar"]), ("job offers", ["Offer A", "Offer B", "Offer C"]), ("apartments", ["The Loft", "The Annex", "The Cottage"]),
       ("web frameworks for a side project", ["Framework X", "Framework Y", "Framework Z"]), ("study plans", ["Plan 1", "Plan 2", "Plan 3"]), ("cloud hosting options", ["Host A", "Host B", "Host C"])]
CRIT = [["price", "quality", "convenience"], ["salary", "growth", "work-life balance"], ["cost", "speed", "reliability"], ["learning curve", "community", "performance"]]
for _ in range(60*3):
    what, opts = R.choice(OPT)
    crit = R.choice(CRIT)
    w = R.choice([(5, 3, 2), (4, 4, 2), (6, 3, 1), (2, 5, 3), (3, 3, 4)])
    sc = {o: [R.randint(3, 9) for _ in crit] for o in opts}
    tot = {o: sum(a * b for a, b in zip(sc[o], w)) for o in opts}
    if len(set(tot.values())) < len(opts):
        continue
    best = max(tot, key=tot.get)
    table = "\n".join(f"- {o}: " + ", ".join(f"{c} {s}" for c, s in zip(crit, sc[o])) for o in opts)
    calc = "\n".join(f"- {o}: " + " + ".join(f"{s}×{ww}" for s, ww in zip(sc[o], w)) + f" = {tot[o]}" for o in opts)
    add("weighted_decision", f"I'm choosing between {what}. I score each 1-9 on {', '.join(crit)}, and my priorities are weighted {w[0]}, {w[1]}, {w[2]} respectively.\n{table}\nWhich should I pick?",
        f"Multiply each score by its weight and add them up:\n{calc}\n\nThe highest total is {best} with {tot[best]}. The margin over the runner-up is {tot[best]-sorted(tot.values())[-2]}; if that is small, the decision is sensitive to your weights, so it's worth checking whether your priorities are really what you think they are.\n\nAnswer: {best}.", "medium")

# ---------- 7. sequences / pattern recognition ----------
for _ in range(70*3):
    typ = R.randrange(6)
    if typ == 0:
        a, d = R.randint(1, 20), R.randint(2, 9)
        seq = [a + d * i for i in range(5)]
        add("pattern", f"What comes next: {', '.join(map(str, seq))}, ...?", f"The differences are all {d} (e.g. {seq[1]}-{seq[0]} = {d}), so it's an arithmetic sequence. Next: {seq[-1]} + {d} = {seq[-1]+d}.\n\nAnswer: {seq[-1]+d}", "easy")
    elif typ == 1:
        a, r = R.randint(1, 5), R.choice([2, 3, 4])
        seq = [a * r ** i for i in range(5)]
        add("pattern", f"Find the next number: {', '.join(map(str, seq))}, ...", f"Each term is the previous times {r} ({seq[1]}/{seq[0]} = {r}). Next: {seq[-1]} × {r} = {seq[-1]*r}.\n\nAnswer: {seq[-1]*r}", "easy")
    elif typ == 2:
        s = R.randint(1, 5)
        seq = [(s + i) ** 2 for i in range(5)]
        add("pattern", f"What's the next term of {', '.join(map(str, seq))}, ...?", f"These are squares: {', '.join(f'{s+i}²' for i in range(5))}. The next is {s+5}² = {(s+5)**2}. (Differences grow by 2: {', '.join(str(seq[i+1]-seq[i]) for i in range(4))}.)\n\nAnswer: {(s+5)**2}", "medium")
    elif typ == 3:
        a, b = R.randint(1, 4), R.randint(1, 5)
        seq = [a, b]
        for _ in range(4):
            seq.append(seq[-1] + seq[-2])
        add("pattern", f"Continue the sequence: {', '.join(map(str, seq))}, ...", f"Each term is the sum of the two before it ({seq[-2]} + {seq[-1]} would be next, as {seq[2]} = {seq[0]} + {seq[1]}). Next: {seq[-2]} + {seq[-1]} = {seq[-2]+seq[-1]}.\n\nAnswer: {seq[-1]+seq[-2]}", "medium")
    elif typ == 4:
        a, b, c = R.randint(2, 6), R.randint(2, 5), R.randint(1, 4)
        seq = [a]
        for i in range(5):
            seq.append(seq[-1] + b if i % 2 == 0 else seq[-1] - c)
        add("pattern", f"What number comes next? {', '.join(map(str, seq))}, ...", f"The steps alternate: +{b}, -{c}, +{b}, -{c}, +{b}. The next step is -{c}: {seq[-1]} - {c} = {seq[-1]-c}.\n\nAnswer: {seq[-1]-c}", "medium")
    else:
        s = R.randint(1, 4)
        seq = [i * (i + 1) // 2 + s for i in range(1, 6)]
        add("pattern", f"Find the pattern: {', '.join(map(str, seq))}, ...", f"Look at the gaps: {', '.join(str(seq[i+1]-seq[i]) for i in range(4))}. They go up by 1 each time, so the next gap is {seq[-1]-seq[-2]+1}. Next term: {seq[-1]} + {seq[-1]-seq[-2]+1} = {seq[-1]+seq[-1]-seq[-2]+1}.\n\nAnswer: {seq[-1]+seq[-1]-seq[-2]+1}", "medium")

# ---------- 8. assignment / constraint puzzles ----------
THINGS = [("drink", ["tea", "coffee", "juice"]), ("pet", ["cat", "dog", "fish"]), ("language", ["Python", "Rust", "Go"]), ("fruit", ["mango", "pear", "plum"]), ("sport", ["tennis", "swimming", "cycling"])]
for _ in range(80*3):
    ppl = R.sample(NAMES, 3)
    label, vals = R.choice(THINGS)
    truth = dict(zip(ppl, R.sample(vals, 3)))
    clue_pool = []
    for p in ppl:
        clue_pool.append((f"{p} has the {truth[p]}" if label == "pet" else f"{p}'s {label} is {truth[p]}", ("is", p, truth[p])))
        for v in vals:
            if v != truth[p]:
                clue_pool.append((f"{p}'s {label} is not {v}" if label != "pet" else f"{p} does not have the {v}", ("not", p, v)))
    def count(cl):
        n = 0
        for perm in itertools.permutations(vals):
            asg = dict(zip(ppl, perm))
            if all((asg[p] == v) if t == "is" else (asg[p] != v) for t, p, v in cl):
                n += 1
        return n
    R.shuffle(clue_pool)
    chosen = []
    for txt, c in clue_pool:
        if c[0] == "is" and R.random() < 0.7:
            continue
        chosen.append((txt, c))
        if count([c for _, c in chosen]) == 1:
            break
    if count([c for _, c in chosen]) != 1:
        continue
    for item in chosen[:]:
        rest = [x for x in chosen if x is not item]
        if rest and count([c for _, c in rest]) == 1:
            chosen = rest
    cl = ". ".join(t[0].upper() + t[1:] for t, _ in chosen) + "."
    sol = ", ".join(f"{p}: {truth[p]}" for p in ppl)
    cand = {p: set(vals) for p in ppl}
    log = []
    for txt_, (t_, p_, v_) in chosen:
        if t_ == "is":
            cand[p_] = {v_}
            log.append(f"{p_} has {v_} ({txt_}).")
        else:
            cand[p_].discard(v_)
            log.append(f"{p_} cannot have {v_}, leaving {' or '.join(sorted(cand[p_]))}.")
    changed = True
    while changed:
        changed = False
        for p in ppl:
            if len(cand[p]) == 1:
                v = next(iter(cand[p]))
                for q_ in ppl:
                    if q_ != p and v in cand[q_]:
                        cand[q_].discard(v)
                        log.append(f"{p} has {v}, so nobody else can: {q_} is down to {' or '.join(sorted(cand[q_]))}.")
                        changed = True
    trace = chr(10).join(f"{n+1}. {l}" for n, l in enumerate(log))
    add("assignment", f"{', '.join(ppl[:-1])} and {ppl[-1]} each have a different {label} from this list: {', '.join(vals)}. Clues: {cl} Who has what?",
        f"Each {label} belongs to exactly one person, so work through the clues and eliminate options:\n{trace}\n\nEvery person is now down to one option.\n\nAnswer: {sol}.", "medium")

# ---------- 9. expected value / decisions under uncertainty ----------
for _ in range(60*3):
    typ = R.randrange(3)
    if typ == 0:
        p = R.choice([Fr(1, 5), Fr(1, 4), Fr(1, 3), Fr(1, 10), Fr(2, 5)])
        win, cost = R.choice([30, 40, 50, 100, 200]), R.choice([5, 10, 15, 20])
        ev = p * win - cost
        add("expected_value", f"A game costs ${cost} to play and pays ${win} with probability {p.numerator}/{p.denominator}, otherwise nothing. Is it worth playing repeatedly?", f"Expected payout = {p.numerator}/{p.denominator} × {win} = {float(p*win):.2f}. Subtract the cost: {float(p*win):.2f} - {cost} = {float(ev):+.2f} per play.\n\nAnswer: {'Positive expected value, so over many plays you would expect to come out ahead, although individual results vary.' if ev > 0 else 'Negative expected value: on average you lose $' + format(float(-ev), '.2f') + ' per play, so repeated play loses money.'}", "medium")
    elif typ == 1:
        pa, va, vb = R.choice([Fr(1, 2), Fr(3, 4), Fr(1, 4)]), R.choice([100, 200, 400]), R.choice([50, 80, 120, 150])
        eva = pa * va
        add("expected_value", f"Option A pays ${va} with probability {pa.numerator}/{pa.denominator} (else $0). Option B pays ${vb} for sure. Which has the higher expected value, and what else should I consider?", f"Option A: {pa.numerator}/{pa.denominator} × {va} = {float(eva):.2f}. Option B: {vb}.\n{'A has the higher expected value' if eva > vb else ('B has the higher expected value' if vb > eva else 'They are equal')}. But expected value ignores risk: if you can't afford the {float(1-pa)*100:.0f}% chance of getting nothing, or you need the money for sure, the guaranteed option can still be the better choice.\n\nAnswer: {'Option A' if eva > vb else ('Option B' if vb > eva else 'A tie')} on expected value ({float(eva):.2f} vs {vb}).", "medium")
    else:
        fixed, per, units = R.choice([200, 500, 1000]), R.choice([4, 6, 10]), R.choice([50, 80, 120, 200])
        alt_fixed, alt_per = fixed // 2, per + R.choice([2, 3, 4])
        be = Fr(fixed - alt_fixed, alt_per - per)
        c1, c2 = fixed + per * units, alt_fixed + alt_per * units
        add("break_even", f"Plan A costs ${fixed} fixed plus ${per} per unit. Plan B costs ${alt_fixed} fixed plus ${alt_per} per unit. At how many units do they cost the same, and which is cheaper for {units} units?",
            f"Set the costs equal: {fixed} + {per}n = {alt_fixed} + {alt_per}n, so {fixed-alt_fixed} = {alt_per-per}n and n = {float(be):.1f}.\nAt {units} units: Plan A = {c1}, Plan B = {c2}. {'Plan A' if c1 < c2 else 'Plan B'} is cheaper there. Below the break-even point the plan with the lower fixed cost wins, above it the plan with the lower per-unit cost wins.\n\nAnswer: break-even at about {float(be):.1f} units; {'A' if c1 < c2 else 'B'} is cheaper at {units} units.", "medium")

# ---------- 10. cause/effect, scenario analysis, estimation (authored) ----------
SCEN = [
 ("Ice cream sales and drowning incidents both rise in summer. Does ice cream cause drowning?", "Almost certainly not. Both rise because of a third factor, hot weather: more people buy ice cream and more people swim. This is a confounder, and it's the standard example of why correlation does not imply causation. To test a causal claim you'd need to control for temperature or run an experiment."),
 ("Our app's crash rate doubled the week we shipped a redesign. Can we conclude the redesign caused it?", "Not yet, but it's the leading suspect. Timing is only suggestive. Check what else changed that week (OS update, new traffic source, backend release), compare crash stacks to see whether they point at new code, and if possible roll back or flag-gate the redesign for a subset of users. If crashes fall only in the rolled-back group, causation is much more convincing."),
 ("A team adds more developers to a late project and it gets later. Why might that happen?", "Because new people need onboarding time from existing developers, communication paths grow quickly with team size, and work that is tightly coupled can't be split cleanly. This is often called Brooks's law. Adding people helps most when the work is separable and early enough for onboarding to pay off."),
 ("Students who use flashcards score higher on exams. Should the school mandate flashcards?", "Not on that evidence alone. Students who choose flashcards may already be more motivated or study more, so selection bias could explain the gap. A mandate is only justified if there is evidence from randomised or well-controlled comparisons, and you'd also want to know whether forced use works as well as chosen use."),
 ("If a startup's revenue doubled but profit fell, what could explain it?", "Costs grew faster than revenue. Plausible causes include heavy spending on marketing or hiring to win growth, discounts that lowered margins, rising cost of goods, or one-time expenses. To diagnose it, compare gross margin, customer acquisition cost and operating expenses between the two periods."),
 ("A website's load time improved by 30% but conversion didn't change. What might be going on?", "Load time may not have been the bottleneck. Perhaps the users who convert were already patient, the checkout flow or pricing is the real barrier, the measurement is dominated by another factor, or the improvement only affected a part of the page users don't care about. Segment by device and step in the funnel to find where users drop."),
 ("Why might a model with 99% accuracy still be useless?", "If the classes are imbalanced, a model that always predicts the majority class can score 99% while never finding the rare positive cases you care about. Look at precision, recall and the confusion matrix, and consider the cost of each type of error."),
 ("If every deploy on Friday has caused an incident this quarter, should we ban Friday deploys?", "It's a reasonable heuristic but check the reason. Friday deploys leave less time to notice problems and staff are leaving, so the risk is real. But if the incidents come from a specific risky change type, fixing testing or adding feature flags may solve the problem without a calendar rule. A common compromise is no risky deploys on Friday afternoons."),
 ("A survey of customers who stayed shows high satisfaction. What is wrong with concluding customers are satisfied overall?", "Survivorship bias. Customers who left aren't in the sample, and they are the ones most likely to be dissatisfied. To get a fair picture you need responses from churned customers too."),
 ("I studied 2 hours a day for a month and my grade didn't improve. What should I examine before giving up?", "Look at how you studied, not just how long. Passive rereading is much less effective than practice questions and spaced recall. Also check whether you studied the material the exam tests, whether you were sleeping enough, and whether you can identify specific topics you're losing marks on. Then change one variable at a time."),
 ("Two search ads: A has a 4% click rate on 50 impressions, B has 3% on 5,000. Which is better?", "Don't trust A yet. Four percent of 50 is 2 clicks, so one extra or missing click moves it a lot; B's 3% comes from about 150 clicks and is much more reliable. Collect more impressions for A before declaring it the winner, or compute confidence intervals to see how much they overlap."),
 ("Our meetings run long. Is the problem too many attendees or no agenda?", "It could be either, so look at the evidence rather than guessing: compare meetings with and without agendas, and large versus small. Often the biggest improvement is a written agenda with time-boxes plus inviting only people who need to decide or contribute. Try one change for two weeks and measure the average length."),
]
for q, a in SCEN:
    add("cause_effect", q, a, "medium", "scen_" + q[:10], "authored")
    add("cause_effect", q + " Please reason step by step.", "Let's go step by step.\n\n" + a, "medium", "scen_" + q[:10], "composed")

FERMI = [
 ("Roughly how many piano tuners might there be in a city of 2 million people?", "Estimate it in steps. Say 1 in 20 households has a piano: 2 million people / 2.5 per household = 800,000 households, so about 40,000 pianos. If each is tuned once a year, that's 40,000 tunings. A tuner can do maybe 4 a day for 250 days, about 1,000 a year. So roughly 40 tuners. The exact figure doesn't matter; the method of breaking it into estimable pieces does, and the answer is likely within a factor of a few."),
 ("Estimate how many words a person reads in a lifetime.", "Suppose someone reads 30 minutes a day on average at about 250 words per minute, so roughly 7,500 words a day. Over 60 reading years of 365 days, that's 7,500 x 21,900 ≈ 164 million words. Allowing for much lower or higher habits, a plausible range is tens of millions to a few hundred million."),
 ("About how much data does a one-hour 1080p video take?", "At a typical streaming bitrate of around 5 megabits per second: 5 Mb/s x 3,600 s = 18,000 megabits = 2,250 megabytes ≈ 2.2 GB. Higher quality encodes can be several times bigger and heavily compressed ones smaller, so 1-4 GB is a sensible range."),
 ("How many text messages could fit on a 1 TB drive?", "A typical text message is about 100 bytes. 1 TB is about 10^12 bytes, so 10^12 / 100 = 10^10, around 10 billion messages. Even a very heavy user sending 100 a day would need over 270,000 years to fill it."),
 ("Estimate the number of seconds in a year and why it's useful.", "365 days x 24 hours x 3,600 seconds = 31,536,000, so about 3.15 x 10^7 seconds. A handy rule: a year is roughly π x 10^7 seconds. It's useful for converting rates, such as 1 request per second becoming about 31.5 million requests per year."),
]
for q, a in FERMI:
    add("estimation", q, a, "medium", "fermi_" + q[:10], "authored")

# ---------- 11. comparison / trade-off / planning reasoning (authored, composed from parts) ----------
COMPARE = [
 ("SQL or NoSQL for a small inventory app?", "SQL (a relational database) is usually the better default here. Inventory data is structured, with clear relationships between products, suppliers and orders, and you want transactions and constraints so counts stay correct. NoSQL fits better when the schema changes constantly, the data is naturally document-shaped, or you need massive horizontal scale. A small inventory app has none of those pressures."),
 ("Should I rent or buy a laptop for a 6-month contract?", "Compare the total costs for six months. Buying means paying the full price now and keeping the machine (and its resale value), which only makes sense if you'll use it afterwards. Renting costs more per month but has no upfront cost and no resale hassle. If you already need a laptop long term, buy; if it's purely for the contract, renting or using the employer's hardware is likely cheaper."),
 ("Is it better to refactor first or ship the feature first?", "It depends on how much the existing code blocks the feature. If the feature is hard to add safely without restructuring, do the smallest refactor that makes it easy, then ship. If it fits cleanly, ship first and schedule the refactor, because refactoring without a concrete use tends to guess wrong. Avoid both extremes: a rewrite before any delivery, or feature piling that makes the next one slower."),
 ("Microservices or a monolith for a team of three?", "A monolith. With three people, the overhead of microservices (deployment pipelines, network failures, distributed debugging, data consistency) usually outweighs the benefits. A well-structured modular monolith keeps boundaries clear and lets you extract a service later if a real scaling or ownership need appears."),
 ("Should I learn one language deeply or several shallowly?", "Go deep on one first. Concepts like data structures, debugging and design transfer between languages, and depth gets you to building real things sooner. After that, picking up a second language is much faster and you'll know what to look for. Having one strong language plus working familiarity with one or two others is a good target."),
 ("Is it better to study in the morning or at night?", "There's no universal answer. What matters most is when you are alert and uninterrupted, and that you're consistent and sleeping enough. Try a week in each slot, rate your focus afterward, and keep whichever works. If you have an exam at a particular time, practising at that time of day can help too."),
]
for q, a in COMPARE:
    add("tradeoff", q, a, "medium", "cmp_" + q[:10], "authored")
    add("tradeoff", f"Give me a recommendation, not a survey: {q[0].lower() + q[1:]}", a.split(". ")[0] + ". " + a.split(". ")[1] + ".", "medium", "cmp_" + q[:10], "composed")

# ---------- 12. what-if with schedule slip ----------
for _ in range(40*3):
    pname, steps = R.choice(PROJ)
    dur = [R.randint(1, 4) for _ in steps]
    k = R.randrange(len(steps))
    slip = R.randint(1, 3)
    tot = sum(dur)
    add("what_if", f"A sequential project ({pname}) has steps: " + ", ".join(f"{s} ({d}d)" for s, d in zip(steps, dur)) + f". If '{steps[k]}' takes {slip} extra day{'s' if slip>1 else ''}, what happens to the finish date?",
        f"Every step waits for the previous one, so the schedule is just the sum of durations: {' + '.join(map(str, dur))} = {tot} days.\nAdding {slip} to '{steps[k]}' gives {tot+slip} days, so the finish moves later by exactly {slip} day{'s' if slip>1 else ''} unless you can shorten a later step or run something in parallel.\n\nAnswer: {tot+slip} days instead of {tot}.", "easy")

save("reasoning", out)
