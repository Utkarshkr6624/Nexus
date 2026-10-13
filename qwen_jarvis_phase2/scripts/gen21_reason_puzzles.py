"""Phase 2.1 reasoning - search-verified puzzle families:
 D. arrangement puzzles with adjacency / gap / before / end clues (unique solution guaranteed, redundant clues pruned, narrowing counts computed)
 F. jug pouring (BFS), bridge-and-torch (Dijkstra), river crossing with conflict graphs (BFS, including impossible cases)
 G. task networks: number of valid orders, finish time, slack (exhaustive / DP)"""
import heapq
from collections import deque
from math import gcd
from gen21_reason_util import *

# ------------------------------------------------------------------ D. arrangements
PACKS = [
    dict(name="swim", items=["Ana", "Bo", "Cy", "Dee", "Eli", "Fay"], at=lambda k: f"in lane {k + 1}", unit="lane", before="has a lower-numbered lane than", end="in an outside lane",
         intro="Swimmers are assigned to lanes", ask="Which lane does each swimmer get?"),
    dict(name="shelf", items=["the atlas", "the cookbook", "the dictionary", "the biography", "the manual", "the almanac"], at=lambda k: f"in slot {k + 1} from the left", unit="slot",
         before="sits somewhere to the left of", end="at one end of the shelf", intro="Books stand in a row on a shelf", ask="What is the left-to-right order?"),
    dict(name="talks", items=["Priya", "Tom", "Lena", "Omar", "Ken", "Zara"], at=lambda k: f"at {9 + k}:00", unit="hour", before="speaks earlier than", end="as the first or last speaker",
         intro="Talks run back to back, one per hour starting at 9:00", ask="Who speaks at which time?"),
    dict(name="parking", items=["the red van", "the blue hatchback", "the grey sedan", "the white truck", "the green estate", "the black coupe"], at=lambda k: f"in bay {k + 1}", unit="bay",
         before="is parked in a lower-numbered bay than", end="in an end bay", intro="Cars are parked in a single row of bays", ask="Which bay does each vehicle occupy?"),
    dict(name="stage", items=["the drummer", "the bassist", "the singer", "the pianist", "the guitarist", "the violinist"], at=lambda k: f"in position {k + 1} from stage left", unit="position",
         before="stands to the left of", end="at one edge of the stage", intro="A band lines up in a row on stage", ask="Who stands where?"),
    dict(name="desks", items=["Mira", "Sam", "Noor", "Ivan", "Hana", "Luis"], at=lambda k: f"at desk {k + 1}", unit="desk", before="sits at a lower-numbered desk than", end="at an end desk",
         intro="A team sits along one wall", ask="Who sits at which desk?"),
]


def clue_true(c, perm):
    # perm[pos] = item index
    pos = {it: p for p, it in enumerate(perm)}
    t = c[0]
    n = len(perm)
    if t == "not":
        return pos[c[1]] != c[2]
    if t == "fixed":
        return pos[c[1]] == c[2]
    if t == "adj":
        return abs(pos[c[1]] - pos[c[2]]) == 1
    if t == "notadj":
        return abs(pos[c[1]] - pos[c[2]]) != 1
    if t == "before":
        return pos[c[1]] < pos[c[2]]
    if t == "gap":
        return abs(pos[c[1]] - pos[c[2]]) - 1 == c[3]
    if t == "end":
        return pos[c[1]] in (0, n - 1)
    if t == "notend":
        return pos[c[1]] not in (0, n - 1)
    raise ValueError(t)


def clue_text(c, pk, names):
    t = c[0]
    cap_ = lambda s: s[:1].upper() + s[1:]
    A = names[c[1]]
    if t == "not":
        return f"{cap_(A)} is not {pk['at'](c[2])}."
    if t == "fixed":
        return f"{cap_(A)} is {pk['at'](c[2])}."
    B = names[c[2]] if t in ("adj", "notadj", "before", "gap") else None
    if t == "adj":
        return f"{cap_(A)} and {B} are right next to each other."
    if t == "notadj":
        return f"{cap_(A)} and {B} are not next to each other."
    if t == "before":
        return f"{cap_(A)} {pk['before']} {B}."
    if t == "gap":
        m = c[3]
        return f"Exactly {m} {pk['unit']}{'' if m == 1 else 's'} separate{'s' if m == 1 else ''} {A} and {B}." if m else f"{cap_(A)} and {B} are right next to each other."
    if t == "end":
        return f"{cap_(A)} is {pk['end']}."
    return f"{cap_(A)} is not {pk['end']}."


def all_true_clues(perm, rng):
    n = len(perm)
    pos = {it: p for p, it in enumerate(perm)}
    cl = []
    for a in range(n):
        for k in range(n):
            if pos[a] != k:
                cl.append(("not", a, k))
        cl.append(("end", a) if pos[a] in (0, n - 1) else ("notend", a))
        for b in range(n):
            if a == b:
                continue
            if abs(pos[a] - pos[b]) == 1 and a < b:
                cl.append(("adj", a, b))
            elif abs(pos[a] - pos[b]) != 1 and a < b:
                cl.append(("notadj", a, b))
            if pos[a] < pos[b]:
                cl.append(("before", a, b))
            if a < b and abs(pos[a] - pos[b]) >= 2:
                cl.append(("gap", a, b, abs(pos[a] - pos[b]) - 1))
    return cl


def solve_count(clues, n, perms):
    return sum(1 for pm in perms if all(clue_true(c, pm) for c in clues))


def make_arrangement(rng, n):
    pk = rng.choice(PACKS)
    names = rng.sample(pk["items"], n)
    perms = list(itertools.permutations(range(n)))
    truth = rng.choice(perms)
    pool = all_true_clues(truth, rng)
    weight = {"not": 1, "notend": 1, "notadj": 1, "end": 2, "adj": 3, "before": 3, "gap": 3}
    rng.shuffle(pool)
    pool.sort(key=lambda c: -weight.get(c[0], 1) * rng.random())
    chosen = []
    remaining = perms
    for c in pool:
        new_rem = [pm for pm in remaining if clue_true(c, pm)]
        if len(new_rem) < len(remaining):
            chosen.append(c)
            remaining = new_rem
        if len(remaining) == 1:
            break
    if len(remaining) != 1 or remaining[0] != truth:
        return None
    # prune redundant clues
    i = 0
    while i < len(chosen):
        trial = chosen[:i] + chosen[i + 1:]
        if solve_count(trial, n, perms) == 1:
            chosen = trial
        else:
            i += 1
    if len(chosen) < 3 or len(chosen) > 8:
        return None
    rng.shuffle(chosen)
    counts = [len(perms)]
    rem = perms
    for c in chosen:
        rem = [pm for pm in rem if clue_true(c, pm)]
        counts.append(len(rem))
    return pk, names, truth, chosen, counts, perms


def arrangement_answer(pk, names, truth, chosen, counts, rng, style):
    n = len(truth)
    arr = cap("; ".join(f"{pk['at'](p)}: {names[it]}" for p, it in enumerate(truth)))
    if style == 0:
        steps = " ".join(f"After clue {i + 1} the arrangements left drop from {counts[i]} to {counts[i + 1]}." for i in range(len(chosen)))
        return f"Counting how many of the {counts[0]} possible arrangements survive each clue in turn: {steps} Exactly one remains.\n\n{arr}."
    if style == 1:
        lines = "\n".join(f"- Clue {i + 1}: {counts[i + 1]} arrangement{'s' if counts[i + 1] != 1 else ''} still possible" for i in range(len(chosen)))
        return f"Start from all {counts[0]} orders and apply the clues one at a time:\n{lines}\n\nThe unique arrangement is: {arr}."
    if style == 2:
        return f"The only arrangement that satisfies every clue is: {arr}. (Each clue narrows the {counts[0]} possible orders; after the last one a single order is left, and the counts along the way were {', '.join(map(str, counts))}.)"
    checks = " ".join(f"Clue {i + 1} holds." for i in range(len(chosen)))
    return f"{arr}.\n\nQuick check against the clues: {checks} No other order passes all of them (the number of survivors shrinks {' -> '.join(map(str, counts))})."


def gen_arrangements(rng, count=110):
    made, tries = 0, 0
    while made < count and tries < 800:
        tries += 1
        n = rng.choice([4, 5, 5, 6, 6])
        res = make_arrangement(rng, n)
        if not res:
            continue
        pk, names, truth, chosen, counts, perms = res
        clue_lines = [f"{i + 1}. {clue_text(c, pk, names)}" for i, c in enumerate(chosen)]
        name_list = oxford([x for x in names])
        form = rng.randrange(3)
        if form == 0:
            q = f"{pk['intro']}: {name_list}. Clues:\n" + "\n".join(clue_lines) + f"\n\n{pk['ask']}"
        elif form == 1:
            q = f"Puzzle. {pk['intro']} ({n} of them: {name_list}).\n" + " ".join(c.split('. ', 1)[1] for c in clue_lines) + f"\nIs the arrangement forced? If so, {pk['ask'][0].lower() + pk['ask'][1:]}"
        else:
            q = "Work out the unique arrangement.\n\n" + "\n".join(clue_lines) + f"\n\n(Context: {pk['intro'].lower()}; the {n} are {name_list}.)"
        style = rng.randrange(4)
        a = arrangement_answer(pk, names, truth, chosen, counts, rng, style)
        turns = [("user", q), ("assistant", a)]
        if made % 3 == 0:
            k = rng.randrange(len(chosen))
            rest = chosen[:k] + chosen[k + 1:]
            sols = [pm for pm in perms if all(clue_true(c, pm) for c in rest)]
            ex = sols[:2]
            fq = pick(rng, [f"What if clue {k + 1} were missing? Would the answer still be forced?", f"Suppose we lose clue {k + 1}. Is the arrangement still determined?",
                            f"Is clue {k + 1} actually needed?"])
            exs = " and ".join("(" + ", ".join(f"{pk['at'](p)}: {names[it]}" for p, it in enumerate(pm)) + ")" for pm in ex)
            fa = f"No. Without clue {k + 1} ({clue_text(chosen[k], pk, names)[:-1]}), {len(sols)} arrangements satisfy the remaining clues, for example {exs}. So that clue is needed to single out one order."
            turns += [("user", fq), ("assistant", fa)]
        emit("arrangement_clues", turns, "hard" if n >= 6 else "medium", "arr", rng)
        made += 1
    return made


# ------------------------------------------------------------------ F. state-space puzzles
def pour_bfs(a, b, target):
    start = (0, 0)
    prev = {start: None}
    dq = deque([start])
    while dq:
        s = dq.popleft()
        x, y = s
        if x == target or y == target:
            path = []
            while s is not None:
                path.append(s)
                s = prev[s][0] if prev[s] else None
            return list(reversed(path))
        moves = [((a, y), f"fill the {a}-litre jug"), ((x, b), f"fill the {b}-litre jug"), ((0, y), f"empty the {a}-litre jug"), ((x, 0), f"empty the {b}-litre jug")]
        t = min(x, b - y)
        moves.append(((x - t, y + t), f"pour the {a}-litre jug into the {b}-litre jug"))
        t = min(y, a - x)
        moves.append(((x + t, y - t), f"pour the {b}-litre jug into the {a}-litre jug"))
        for ns, desc in moves:
            if ns not in prev:
                prev[ns] = (s, desc)
                dq.append(ns)
    return None


def pour_text(a, b, path, prev_desc=None):
    # recompute the step descriptions
    steps = []
    for i in range(1, len(path)):
        x, y = path[i - 1]
        nx, ny = path[i]
        if (nx, ny) == (a, y) and nx != x:
            d = f"fill the {a}-litre jug"
        elif (nx, ny) == (x, b) and ny != y:
            d = f"fill the {b}-litre jug"
        elif (nx, ny) == (0, y) and x != 0:
            d = f"empty the {a}-litre jug"
        elif (nx, ny) == (x, 0) and y != 0:
            d = f"empty the {b}-litre jug"
        elif nx < x:
            d = f"pour the {a}-litre jug into the {b}-litre jug"
        else:
            d = f"pour the {b}-litre jug into the {a}-litre jug"
        steps.append(f"{i}. {cap(d)} -> ({nx}, {ny})")
    return "\n".join(steps)


def pour_answer(a, b, t, rng):
    path = pour_bfs(a, b, t)
    if path is None:
        g = gcd(a, b)
        why = f"every amount you can measure is a multiple of gcd({a}, {b}) = {g}" if t <= max(a, b) and t % g else f"neither jug can hold {t} litres"
        return f"It cannot be done. A breadth-first search over all reachable (jug A, jug B) states never produces {t}; the reason is that {why}.", None
    k = len(path) - 1
    return (pick(rng, [f"The shortest solution takes {k} steps (state shown as ({a}-litre jug, {b}-litre jug)):\n", f"Minimum {k} steps. Tracking (A, B) = ({a}-litre, {b}-litre):\n",
                       f"{k} moves is optimal, found by exploring every reachable state level by level:\n"]) + pour_text(a, b, path) + f"\n\nNo shorter sequence exists, because the search finds {t} litres first at depth {k}."), k


def gen_pouring(rng, count=36):
    made = 0
    combos = []
    for a in range(3, 10):
        for b in range(a + 1, 14):
            for t in range(1, b + 1):
                if t not in (a, b):
                    combos.append((a, b, t))
    rng.shuffle(combos)
    for a, b, t in combos:
        if made >= count:
            break
        path = pour_bfs(a, b, t)
        reachable = path is not None
        if reachable and len(path) - 1 < 4:
            continue
        if not reachable and rng.random() > 0.18:
            continue
        who = pick(rng, ["You have", "A chemist has", "A camper has", "Imagine"])
        q = pick(rng, [f"{who} a {a}-litre jug and a {b}-litre jug, no markings, and unlimited water. How can you measure exactly {t} litres?",
                       f"Two unmarked containers hold {a} L and {b} L. Get exactly {t} L into one of them, using as few moves as possible. A move is a fill, an empty, or a pour.",
                       f"Is it possible to measure {t} litres with only a {a} L and a {b} L jug? If yes, give the shortest procedure."])
        ans, k = pour_answer(a, b, t, rng)
        turns = [("user", q), ("assistant", ans)]
        if made % 3 == 1:
            t2 = rng.choice([x for x in range(1, b + 1) if x not in (a, b, t)])
            ans2, k2 = pour_answer(a, b, t2, rng)
            turns += [("user", pick(rng, [f"What if I needed {t2} litres instead?", f"Same jugs, but the target is {t2} L now.", f"And for {t2} litres?"])), ("assistant", ans2)]
        emit("jug_pouring", turns, "hard", "pour", rng)
        made += 1
    return made


def torch_min(times):
    n = len(times)
    full = (1 << n) - 1
    best = {(full, 0): 0}  # state: set on the start side, torch side 0=start
    pq = [(0, full, 0, ())]
    seen = {}
    while pq:
        d, left, side, hist = heapq.heappop(pq)
        if (left, side) in seen:
            continue
        seen[(left, side)] = d
        if left == 0:
            return d, hist
        if side == 0:
            ppl = [i for i in range(n) if left >> i & 1]
            for i, j in itertools.combinations(ppl, 2):
                nl = left & ~(1 << i) & ~(1 << j)
                heapq.heappush(pq, (d + max(times[i], times[j]), nl, 1, hist + (("over", (i, j)),)))
        else:
            ppl = [i for i in range(n) if not (left >> i & 1)]
            for i in ppl:
                heapq.heappush(pq, (d + times[i], left | (1 << i), 0, hist + (("back", (i,)),)))
    return None


def gen_torch(rng, count=18):
    made = 0
    names = ["Ada", "Bo", "Cy", "Di", "Ed"]
    tries = 0
    while made < count and tries < 300:
        tries += 1
        n = rng.choice([4, 4, 5])
        times = sorted(rng.sample(range(1, 16), n))
        d, hist = torch_min(times)
        # skip if the naive "fastest escorts everyone" is already optimal (boring)
        naive = times[0] * (n - 2) + sum(times[1:])
        if naive == d and rng.random() < 0.7:
            continue
        nm = names[:n]
        by = {nm[i]: times[i] for i in range(n)}
        tl = ", ".join(f"{nm[i]} {times[i]} min" for i in range(n))
        q = pick(rng, [f"{n} hikers must cross a bridge at night with one torch. At most two cross together, at the slower person's pace, and the torch must travel with them. Times: {tl}. What is the minimum total time?",
                       f"Bridge and torch: {tl}. Two may cross at once; someone must bring the torch back each time. Find the fastest schedule and its total time.",
                       f"A bridge holds two people at a time and there is a single torch. Crossing times are {tl}. How quickly can all {n} get across?"])
        steps = []
        t_acc = 0
        for i, (kind, who) in enumerate(hist, 1):
            tt = max(times[x] for x in who)
            t_acc += tt
            steps.append(f"{i}. {'Cross: ' + ' and '.join(nm[x] for x in who) if kind == 'over' else 'Return: ' + nm[who[0]]} ({tt} min, running total {t_acc})")
        a = pick(rng, [f"The minimum is {d} minutes. A schedule that achieves it:\n", f"{d} minutes is optimal (found by searching every possible sequence of crossings). One schedule:\n"]) + "\n".join(steps)
        if naive != d:
            a += f"\n\nNote the 'obvious' plan where the fastest person escorts everyone takes {naive} minutes, which is worse; sending the two slowest together saves time."
        else:
            a += f"\n\nHere the simple strategy of the fastest person escorting everyone is already optimal."
        turns = [("user", q), ("assistant", a)]
        if made % 2 == 0:
            i = rng.randrange(1, n)
            newt = times[:]
            newt[i] = newt[i] + rng.randint(2, 6)
            newt_sorted = sorted(newt)
            d2, _ = torch_min(newt_sorted)
            turns += [("user", f"What if {nm[i]} took {newt[i]} minutes instead of {times[i]}?"), ("assistant", f"Re-solving with times {', '.join(map(str, newt_sorted))}, the minimum becomes {d2} minutes ({'+' if d2 >= d else ''}{d2 - d} compared with before).")]
        emit("bridge_torch", turns, "hard", "torch", rng)
        made += 1
    return made


CROSS = [("fox", "hen", "sack of grain"), ("wolf", "goat", "cabbage"), ("lion", "zebra", "bale of hay"), ("cat", "mouse", "cheese"), ("dog", "rabbit", "carrot"),
         ("tiger", "deer", "bundle of grass"), ("fisherman's cat", "trout", "bucket of bait")]
CROSS4 = [("wolf", "goat", "cabbage", "farmer's dog"), ("fox", "hen", "grain", "chick"), ("lion", "zebra", "hay", "monkey")]


def cross_bfs(items, conflicts, cap):
    n = len(items)
    # state: (frozenset of items on the start bank, farmer side 0=start)
    start = (frozenset(range(n)), 0)
    goal = (frozenset(), 1)

    def safe(group):
        return not any(a in group and b in group for a, b in conflicts)
    prev = {start: None}
    dq = deque([start])
    while dq:
        s = dq.popleft()
        if s == goal:
            path = []
            while s:
                path.append(s)
                s = prev[s][0] if prev[s] else None
            return list(reversed(path))
        left, side = s
        here = left if side == 0 else frozenset(range(n)) - left
        for k in range(0, cap + 1):
            for cargo in itertools.combinations(sorted(here), k):
                nl = left - set(cargo) if side == 0 else left | set(cargo)
                other = nl if side == 0 else frozenset(range(n)) - nl
                same_bank_unattended = (nl if side == 0 else frozenset(range(n)) - nl)
                # after moving, the bank the farmer leaves must be safe
                leaving_bank = nl if side == 0 else frozenset(range(n)) - nl
                if not safe(leaving_bank):
                    continue
                ns = (nl, 1 - side)
                if ns not in prev:
                    prev[ns] = (s, cargo)
                    dq.append(ns)
    return None


def gen_cross(rng, count=26):
    made, tries = 0, 0
    while made < count and tries < 500:
        tries += 1
        items = list(rng.choice(CROSS4 if rng.random() < 0.4 else CROSS))
        n = len(items)
        pairs = list(itertools.combinations(range(n), 2))
        conflicts = [p for p in pairs if rng.random() < 0.5]
        if not conflicts:
            continue
        bcap = rng.choice([1, 1, 2])
        path = cross_bfs(items, conflicts, bcap)
        possible = path is not None
        if not possible and rng.random() > 0.35:
            continue
        who = pick(rng, ["a farmer", "a ferryman", "a ranger", "a boatman"])
        cons = "; ".join(f"the {items[a]} and the {items[b]}" for a, b in conflicts)
        boat = "carries the farmer plus at most one item" if bcap == 1 else "carries the farmer plus at most two items"
        q = pick(rng, [f"{cap(who)} must take the {', the '.join(items)} across a river. The boat {boat}. If left alone together without the farmer, these pairs cannot stay: {cons}. Can it be done, and what is the minimum number of crossings?".replace("A farmer", "A farmer"),
                       f"River puzzle: {who} ferries a {', a '.join(items)}. The boat {boat}. Unattended pairs that cannot be left together: {cons}. Find the fewest crossings, or show that it is impossible."])
        if possible:
            moves = []
            s0 = path[0]
            for i in range(1, len(path)):
                (pl, ps), (nl, ns_) = path[i - 1], path[i]
                cargo = [items[x] for x in (pl ^ nl)]
                direction = "over" if ps == 0 else "back"
                moves.append(f"{i}. Cross {direction} " + (f"with the {cargo[0]}" if len(cargo) == 1 else f"with the {' and the '.join(cargo)}" if cargo else "alone"))
            a = pick(rng, [f"Yes, in {len(path) - 1} crossings, and a breadth-first search of every legal state shows nothing shorter exists:\n", f"It can be done; the minimum is {len(path) - 1} crossings:\n"]) + "\n".join(moves)
        else:
            a = pick(rng, ["It is impossible. ", "No solution exists. "]) + "Exploring every reachable arrangement of the banks (with every allowed boat load) never reaches the state where everything is across; every sequence eventually forces a forbidden pair to be left together."
        emit("river_crossing", [("user", q), ("assistant", a)], "hard", "cross", rng)
        made += 1
    return made


# ------------------------------------------------------------------ G. task networks
PROJECTS = [
    ("baking a layered cake", ["bake sponges", "make filling", "make frosting", "level and layer", "frost and decorate", "chill"]),
    ("moving into a flat", ["book movers", "pack boxes", "transfer utilities", "clean old flat", "unload", "unpack"]),
    ("launching a small web page", ["write copy", "design layout", "build page", "buy domain", "set up hosting", "go live"]),
    ("preparing a conference talk", ["outline", "draft slides", "gather data", "rehearse", "get feedback", "final edit"]),
    ("organising a school fair", ["book hall", "recruit helpers", "print posters", "order supplies", "set up stalls", "run event"]),
    ("repainting a room", ["buy paint", "move furniture", "mask edges", "prime walls", "paint walls", "reassemble"]),
]


def gen_networks(rng, count=30):
    made, tries = 0, 0
    while made < count and tries < 500:
        tries += 1
        title, tasks = rng.choice(PROJECTS)
        tasks = tasks[:]
        n = len(tasks)
        dur = [rng.randint(1, 6) for _ in range(n)]
        deps = {i: [] for i in range(n)}
        for j in range(1, n):
            for i in range(j):
                if rng.random() < 0.28:
                    deps[j].append(i)
        if sum(len(v) for v in deps.values()) < 4:
            continue
        # enumerate topological orders
        cnt = 0
        for pm in itertools.permutations(range(n)):
            pos = {t: p for p, t in enumerate(pm)}
            if all(pos[d] < pos[t] for t in deps for d in deps[t]):
                cnt += 1
        es = [0] * n
        for j in range(n):
            es[j] = max([es[i] + dur[i] for i in deps[j]], default=0)
        finish = max(es[j] + dur[j] for j in range(n))
        succ = {i: [j for j in range(n) if i in deps[j]] for i in range(n)}
        lf = [finish] * n
        for i in reversed(range(n)):
            lf[i] = min([lf[j] - dur[j] for j in succ[i]], default=finish)
        slack = [lf[i] - dur[i] - es[i] for i in range(n)]
        crit = [i for i in range(n) if slack[i] == 0]
        if cnt < 4 or cnt > 400 or len(crit) == n:
            continue
        dl = "\n".join(f"- {tasks[i]} ({dur[i]} h)" + (f", after: {', '.join(tasks[d] for d in deps[i])}" if deps[i] else ", no prerequisite") for i in range(n))
        form = rng.randrange(3)
        qs = [f"Plan for {title}. Each task, its duration and what it must wait for:\n{dl}\n\nAssuming enough helpers to run independent tasks at the same time, how soon can everything finish, and which tasks cannot slip without delaying the end?",
              f"Tasks for {title}:\n{dl}\n\nHow many different valid orders could one person follow, and what is the shortest possible total time with unlimited helpers?",
              f"{cap(title)}:\n{dl}\n\nWhich task has the most slack, and how much?"]
        slack_max = max(range(n), key=lambda i: (slack[i], -i))
        ties = [i for i in range(n) if slack[i] == slack[slack_max]]
        if form == 0:
            a = f"With unlimited helpers the earliest finish is {finish} hours. Tasks on the critical path (zero slack) are: {oxford([tasks[i] for i in crit])}; a delay to any of them delays the finish. The others have slack: " + ", ".join(f"{tasks[i]} {slack[i]} h" for i in range(n) if slack[i]) + "."
        elif form == 1:
            a = f"There are {cnt} valid orders for a single person (counted by checking every ordering of the {n} tasks against the prerequisites). With unlimited helpers the minimum total time is the longest chain of dependent tasks: {finish} hours. One person working alone needs {sum(dur)} hours."
        else:
            a = f"Most slack: {oxford([tasks[i] for i in ties])} with {slack[slack_max]} hour{'s' if slack[slack_max] != 1 else ''}. (Slack = latest start that still meets the {finish}-hour finish minus earliest start.) Zero-slack tasks: {oxford([tasks[i] for i in crit])}."
        turns = [("user", qs[form]), ("assistant", a)]
        if made % 3 == 2:
            j = rng.choice(crit)
            extra = rng.randint(1, 3)
            dur2 = dur[:]
            dur2[j] += extra
            es2 = [0] * n
            for k in range(n):
                es2[k] = max([es2[i] + dur2[i] for i in deps[k]], default=0)
            fin2 = max(es2[k] + dur2[k] for k in range(n))
            turns += [("user", f"What if '{tasks[j]}' takes {extra} hour{'s' if extra > 1 else ''} longer?"), ("assistant", f"Then the earliest finish moves from {finish} to {fin2} hours: {'it is on the critical path, so the whole project slips by the same amount' if fin2 - finish == extra else 'the project slips by ' + str(fin2 - finish) + ' because another chain now becomes the longest'}.")]
        emit("task_network", turns, "medium", "net", rng)
        made += 1
    return made


def run(rng):
    return {"arr": gen_arrangements(rng), "pour": gen_pouring(rng), "torch": gen_torch(rng), "cross": gen_cross(rng), "net": gen_networks(rng)}
