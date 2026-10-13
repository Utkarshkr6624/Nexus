"""Phase 2.1 instruction following: many NEW prompt shapes whose compliance is checked in code.
Hand-written source material lives in gen21_instr_data.py; every expected output is computed from it, and each example is verified
against its own constraints before it is kept (failures are dropped and counted).  Stdlib only."""
import csv
import datetime
import io
import json
import re
from lib import *
from gen21_instr_data import SRC, TOPICS, QA, SIMPLE

CAT = "instruction_following"
R = seeded("instruction21")
recs = []
dropped = []
_n = [0]


def wc(s):
    return len(s.split())


def add(sub, turns, src="computed", diff="medium", system=None):
    _n[0] += 1
    recs.append(mk(CAT, sub, turns, f"i21-{sub}-{_n[0]}", diff, src, system))


def drop(why, what=""):
    dropped.append((why, what[:60]))


# ---------- validate hand-written sources ----------
S = []
for row in SRC:
    text, people, iso, written, aval, atext, place, label, summ = row
    ok = all(p in text for p in people) and written in text and atext in text and (not place or place in text)
    ok = ok and datetime.date.fromisoformat(iso).strftime("%B %-d, %Y" if False else "%B") in written and wc(summ) <= 18
    try:
        d = datetime.date.fromisoformat(iso)
        ok = ok and written == f"{d.strftime('%B')} {d.day}, {d.year}"
    except ValueError:
        ok = False
    if ok:
        S.append(dict(text=text, people=people, iso=iso, written=written, aval=aval, atext=atext, place=place, label=label, summ=summ, d=d))
    else:
        drop("bad_source", text)
LABELS = ["complaint", "praise", "question", "announcement", "request"]


def fmt_num(v):
    return f"{v:,}" if float(v).is_integer() else f"{v:,.2f}"


def sample(seq, k):
    return R.sample(list(seq), k)


# ============ T1: extraction to JSON with chosen keys ============
J_PROMPTS = [
    "Read the message below and give me a JSON object with the keys {keys}. {extra}\n\n\"{t}\"",
    "{t}\n\nFrom the text above, produce JSON containing {keys}. {extra}",
    "Extract the following into JSON ({keys}). {extra}\nText: {t}",
    "I need this logged. Pull {keys} out of the message and return them as a JSON object. {extra}\n\n{t}",
    "Turn this note into structured data with exactly these keys: {keys}. {extra}\n\n---\n{t}\n---",
]
for s in sample(S, 44):
    avail = [("person", s["people"][0]), ("date", s["iso"]), ("amount", s["aval"])]
    if s["place"]:
        avail.append(("place", s["place"]))
    k = R.randint(2, len(avail))
    chosen = sample(avail, k)
    chosen.sort(key=lambda kv: [a[0] for a in avail].index(kv[0]))
    obj = {a: b for a, b in chosen}
    keys = ", ".join(f"`{a}`" for a, _ in chosen)
    extra = R.choice(["Dates must be ISO (YYYY-MM-DD).", "Return only the JSON, no commentary.", "Amounts should be plain numbers without currency symbols.", "Use ISO dates and plain numbers; no other text."])
    if "date" in obj and "ISO" not in extra and "Dates" not in extra:
        extra += " Write the date as YYYY-MM-DD."
    if "amount" in obj and "number" not in extra:
        extra += " Give the amount as a plain number."
    ind = R.choice([None, 2])
    ans = "```json\n" + json.dumps(obj, indent=ind) + "\n```"
    back = json.loads(ans[8:-4])
    if back != obj or list(back) != [a for a, _ in chosen]:
        drop("json_roundtrip", s["text"])
        continue
    add("extract_json", [("user", R.choice(J_PROMPTS).format(keys=keys, extra=extra.strip(), t=s["text"])), ("assistant", ans)])

# ============ T2: multi-row tables/CSV/YAML from several messages ============
T_COLS = [("person", lambda s: s["people"][0]), ("place", lambda s: s["place"]), ("date", lambda s: s["iso"]), ("amount", lambda s: fmt_num(s["aval"])), ("label", lambda s: s["label"])]
pool_pl = [s for s in S if s["place"]]
for i in range(28):
    k = R.choice([3, 3, 4])
    rows = sample(pool_pl, k)
    cols = sample(T_COLS, R.randint(2, 4))
    names = [c[0] for c in cols]
    body = [[c[1](s) for c in cols] for s in rows]
    msgs = "\n".join(f"{j + 1}. {s['text']}" for j, s in enumerate(rows))
    kind = R.choice(["markdown", "csv", "yaml", "tsv", "json_lines"])
    if kind == "markdown":
        ans = "| " + " | ".join(names) + " |\n|" + "---|" * len(names) + "\n" + "\n".join("| " + " | ".join(r) + " |" for r in body)
        ask = f"a Markdown table with the columns {', '.join(names)} (in that order, one row per message)"
        ok = len(ans.splitlines()) == k + 2
    elif kind == "csv":
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(names)
        w.writerows(body)
        ans = "```csv\n" + buf.getvalue().strip() + "\n```"
        back = list(csv.reader(io.StringIO(buf.getvalue())))
        ask = f"CSV with a header row and the columns {', '.join(names)}, one row per message"
        ok = len(back) == k + 1 and all(len(r) == len(names) for r in back)
    elif kind == "tsv":
        ans = "```\n" + "\t".join(names) + "\n" + "\n".join("\t".join(r) for r in body) + "\n```"
        ask = f"tab-separated text (header row first) with the columns {', '.join(names)}"
        ok = all(len(l.split("\t")) == len(names) for l in ans.strip("`\n").splitlines())
    elif kind == "yaml":
        lines = []
        for r in body:
            for j, (n, v) in enumerate(zip(names, r)):
                lines.append(("- " if j == 0 else "  ") + f"{n}: {v}")
        ans = "```yaml\n" + "\n".join(lines) + "\n```"
        ask = f"a YAML list where each item has the keys {', '.join(names)}"
        ok = sum(1 for l in lines if l.startswith("- ")) == k
    else:
        ans = "```\n" + "\n".join(json.dumps(dict(zip(names, r))) for r in body) + "\n```"
        ask = f"JSON Lines (one JSON object per line) with the keys {', '.join(names)}"
        ok = all(json.loads(l) for l in ans.strip("`\n").splitlines())
    if not ok:
        drop("table_check", kind)
        continue
    lead = R.choice(["Summarise these messages as", "Convert the messages below into", "Give me", "Organise the following into"])
    add("extract_table_" + kind, [("user", f"{lead} {ask}. Output only the result.\n\n{msgs}"), ("assistant", ans)])

# ============ T3: classification with a fixed label set ============
C_PROMPTS = [
    "Classify the message into exactly one of these labels: {labs}. Reply with the label only.\n\n{t}",
    "{t}\n\nWhich category fits best: {labs}? One word, nothing else.",
    "Label this customer message using only {labs}. No explanation.\n\"{t}\"",
    "You are sorting incoming mail. Options: {labs}. Print the single best option for the following text and nothing more.\n\n{t}",
]
for s in sample(S, 38):
    others = [l for l in LABELS if l != s["label"]]
    labs = sample(others, R.randint(2, 3)) + [s["label"]]
    R.shuffle(labs)
    if R.random() < 0.3:
        idx = labs.index(s["label"]) + 1
        ask = f"Number the labels in the order given ({', '.join(labs)}) and answer with the number of the right one only.\n\n{s['text']}"
        add("classify_number", [("user", ask), ("assistant", str(idx))])
    else:
        add("classify_label", [("user", R.choice(C_PROMPTS).format(labs=", ".join(labs), t=s["text"])), ("assistant", s["label"])])

# ============ T4: capped-length summaries ============
for s in sample(S, 34):
    n = wc(s["summ"]) + R.randint(0, 5)
    nsent = len(re.findall(r"[.!?](?:\s|$)", s["summ"]))
    if nsent != 1 or wc(s["summ"]) > n:
        drop("summary", s["summ"])
        continue
    p = R.choice([
        f"Summarise this in one sentence of at most {n} words:\n\n{s['text']}",
        f"{s['text']}\n\nGive me a one-line summary. Hard limit: {n} words.",
        f"TL;DR please - a single sentence, {n} words or fewer.\n\n\"{s['text']}\"",
        f"Condense the message into one sentence. It must not exceed {n} words.\n\n{s['text']}",
    ])
    add("summary_wordcap", [("user", p), ("assistant", s["summ"])])

# ============ T5: date reformatting and weekdays ============
DATE_FMTS = [
    ("DD/MM/YYYY", lambda d: d.strftime("%d/%m/%Y")), ("YYYY.MM.DD", lambda d: d.strftime("%Y.%m.%d")), ("D Mon YYYY (for example 5 Jan 2025)", lambda d: f"{d.day} {d.strftime('%b')} {d.year}"),
    ("MM-DD-YY", lambda d: d.strftime("%m-%d-%y")), ("ISO 8601 (YYYY-MM-DD)", lambda d: d.isoformat()), ("Weekday, D Month YYYY", lambda d: f"{d.strftime('%A')}, {d.day} {d.strftime('%B')} {d.year}"),
    ("YYYYMMDD with no separators", lambda d: d.strftime("%Y%m%d")),
]
for s in sample(S, 30):
    if R.random() < 0.35:
        p = R.choice([f"Which day of the week is {s['written']}? Reply with the day name only.", f"What weekday falls on {s['written']}? One word."])
        add("date_weekday", [("user", p), ("assistant", s["d"].strftime("%A"))], diff="medium")
    else:
        name, fn = R.choice(DATE_FMTS)
        p = R.choice([f"Rewrite the date {s['written']} as {name}. Output only the date.", f"Convert \"{s['written']}\" to the format {name} and say nothing else.", f"Date: {s['written']}. Give it in {name} form, output only."])
        add("date_reformat", [("user", p), ("assistant", fn(s["d"]))])

# ============ T6: acrostics ============
ACRO = {
    "a quiet morning": dict(A="A soft light spills across the kitchen floor.", B="Birds begin their chorus outside the window.", C="Coffee steams gently in a favourite mug.", D="Dew still clings to the garden grass.", E="Every sound seems slower at this hour.", F="Fresh bread warms the whole house with its smell.", G="Gentle breezes lift the curtains.", H="Hurried thoughts fade as the day begins.", I="It is a good time to plan the hours ahead.", L="Long shadows stretch toward the doorway.", M="Morning traffic hums faintly in the distance.", N="Neighbours wave as they walk their dogs.", O="Only a few clouds drift across the sky.", P="Peace settles over the street.", R="Rain from the night has washed the air clean.", S="Sunlight climbs slowly over the rooftops.", T="Time feels generous before the first meeting.", W="Warm toast waits beside a bowl of fruit."),
    "learning to cook": dict(A="A sharp knife makes chopping safer and faster.", B="Boil the water before adding the pasta.", C="Clean as you go to keep the counter clear.", D="Dry the vegetables so they brown instead of steam.", E="Eggs cook best over gentle heat.", F="Follow the recipe once before changing it.", G="Garlic burns quickly, so add it late.", H="Heat the pan before the oil goes in.", I="Imagine the finished plate while you season.", L="Let meat rest before slicing it.", M="Measure the spices until you trust your taste.", N="Never leave a hot pan unattended.", O="Onions turn sweet when cooked slowly.", P="Prepare every ingredient before you start.", R="Rice needs steady heat and a tight lid.", S="Salt in small steps and taste each time.", T="Taste often, because seasoning builds gradually.", W="Wash your hands before touching raw food."),
}
WORDS = ["SPRING", "WINTER", "BREAD", "HOME", "TRAIN", "LIGHT", "GARDEN", "DREAM", "OCEAN", "MORNING", "PEACE", "BRIDGE", "NIGHT", "STONE", "FIRST", "PLANT", "CHEESE", "HEART", "GREEN", "SMILE", "TEA", "TIME", "RAIN", "SHORE"]
for w in sample(WORDS, 18):
    theme = R.choice(list(ACRO))
    bank = ACRO[theme]
    if not all(ch in bank for ch in w):
        drop("acrostic_letters", w)
        continue
    lines = [bank[ch] for ch in w]
    if "".join(l[0] for l in lines) != w or len(set(lines)) != len(lines):
        drop("acrostic_dupe", w)
        continue
    p = R.choice([f"Write an acrostic for the word {w}. Each line is one sentence about {theme}.", f"Make an acrostic poem spelling {w.lower()} (first letters of the lines), on the topic of {theme}. One sentence per line, no title.", f"Give me {len(w)} lines whose first letters spell {w}. Theme: {theme}. Output the lines only."])
    add("acrostic", [("user", p), ("assistant", "\n".join(lines))], src="authored")

# ============ T7: mechanical text transforms ============
def title_case(s):
    return " ".join(w[:1].upper() + w[1:] for w in s.split())


def words_only(s):
    return re.findall(r"[A-Za-z0-9']+", s)


TRANS = [
    ("convert to ALL CAPS", lambda s: s.upper()),
    ("make it all lowercase", lambda s: s.lower()),
    ("convert it to Title Case (capitalise the first letter of every word)", title_case),
    ("reverse the order of the words, keeping each word as it is", lambda s: " ".join(reversed(s.split()))),
    ("replace every digit with the symbol #", lambda s: re.sub(r"\d", "#", s)),
    ("remove all commas", lambda s: s.replace(",", "")),
    ("write it as a snake_case identifier made of its words in lowercase, without punctuation", lambda s: "_".join(w.lower() for w in words_only(s))),
    ("write it as a kebab-case slug of its words in lowercase, without punctuation", lambda s: "-".join(w.lower() for w in words_only(s))),
    ("put each word on its own line", lambda s: "\n".join(s.split())),
    ("replace each space with a vertical bar surrounded by spaces", lambda s: " | ".join(s.split())),
]
for s in sample(S, 30):
    desc, fn = R.choice(TRANS)
    src_t = s["summ"] if "snake" not in desc and "kebab" not in desc else " ".join(s["summ"].split()[:5])
    out = fn(src_t)
    p = R.choice([f"Take this text and {desc}. Return only the result.\n\n{src_t}", f"{src_t}\n\nInstruction: {desc}. Reply with the transformed text only.", f"Please {desc}:\n\"{src_t}\""])
    if desc.startswith("put each") or "line" in desc:
        out = "```\n" + out + "\n```"
    add("text_transform", [("user", p), ("assistant", out)])

# ============ T8: list operations ============
POOLS = {
    "fruits": ["apple", "banana", "cherry", "mango", "kiwi", "pear", "fig", "plum", "grape", "lemon", "peach", "melon", "apricot", "lime", "papaya", "guava"],
    "tools": ["hammer", "wrench", "saw", "drill", "pliers", "chisel", "level", "tape", "screwdriver", "mallet", "clamp", "file", "awl", "plane"],
    "cities": ["Oslo", "Lima", "Cairo", "Perth", "Quito", "Dublin", "Hanoi", "Kyoto", "Accra", "Bern", "Riga", "Doha", "Lagos", "Rome", "Seoul", "Tunis"],
    "animals": ["otter", "heron", "lynx", "gecko", "bison", "falcon", "mole", "newt", "puffin", "jackal", "koala", "marmot", "ibis", "stoat", "wombat", "yak"],
    "colours": ["crimson", "teal", "amber", "ivory", "olive", "indigo", "coral", "mauve", "navy", "lilac", "ochre", "azure", "sepia", "taupe"],
}
for i in range(40):
    name = R.choice(list(POOLS))
    items = sample(POOLS[name], R.randint(5, 8))
    if R.random() < 0.4:                      # add a case-insensitive duplicate
        dup = R.choice(items)
        items.insert(R.randrange(len(items) + 1), R.choice([dup.upper(), dup.capitalize(), dup]))
    lst = ", ".join(items)
    op = R.randrange(10)
    if op == 0:
        out = ", ".join(sorted(items, key=str.lower)); d = "sort the list alphabetically (ignore case)"
    elif op == 1:
        out = ", ".join(sorted(items, key=lambda x: (len(x), x.lower()))); d = "sort by word length, shortest first, breaking ties alphabetically"
    elif op == 2:
        seen, res = set(), []
        for x in items:
            if x.lower() not in seen:
                seen.add(x.lower()); res.append(x)
        out = ", ".join(res); d = "remove duplicates, ignoring case, keeping the first occurrence of each"
    elif op == 3:
        ch = R.choice("aeiou")
        res = [x for x in items if ch not in x.lower()]
        if not res or len(res) == len(items):
            drop("list_filter", lst); continue
        out = ", ".join(res); d = f"keep only the items that do not contain the letter '{ch}'"
    elif op == 4:
        n = R.randint(4, 6)
        res = [x for x in items if len(x) >= n]
        if not res or len(res) == len(items):
            drop("list_len", lst); continue
        out = "\n".join(f"{j + 1}. {x}" for j, x in enumerate(res)); d = f"keep only items with at least {n} letters and present them as a numbered list"
    elif op == 5:
        out = ", ".join(reversed(items)); d = "reverse the order"
    elif op == 6:
        g = {}
        for x in sorted(items, key=str.lower):
            g.setdefault(x[0].upper(), []).append(x)
        out = "\n".join(f"{k}: {', '.join(v)}" for k, v in sorted(g.items())); d = "group the items by first letter (uppercase letter, a colon, then the items alphabetically), one group per line, groups in alphabetical order"
    elif op == 7:
        out = " | ".join(x.capitalize() for x in items); d = "capitalise each item and join them with ' | '"
    elif op == 8:
        out = str(len(items)); d = "answer with only the number of items in it"
    else:
        out = str(len({x.lower() for x in items})); d = "count the distinct items, ignoring case, and answer with only that number"
    p = R.choice([f"Here is a list of {name}: {lst}\n\nPlease {d}.", f"List: {lst}\nTask: {d}. Return only the result.", f"I have these {name}: {lst}. Could you {d}? Output the result only.", f"{lst}\n\n^ Those are {name}. {d[0].upper() + d[1:]}."])
    add("list_operation", [("user", p), ("assistant", out)])

# ============ T9/T12: exact counts, required/forbidden words, combined constraints ============
def pick_facts(topic, n):
    return sample(TOPICS[topic], n)


NUMW = {3: "three", 4: "four", 5: "five", 6: "six"}
for i in range(40):
    topic = R.choice(list(TOPICS))
    n = R.randint(3, 5)
    facts = pick_facts(topic, n)
    mx = max(wc(f) for f in facts) + R.randint(0, 2)
    kind = R.randrange(5)
    if kind == 0:
        out = "\n".join("- " + f for f in facts)
        p = R.choice([f"Give me exactly {NUMW[n]} bullet points about {topic}, each at most {mx} words.", f"List {n} short facts on {topic} as bullets. Every bullet must be {mx} words or fewer, and there must be exactly {n}."])
        ok = len(out.splitlines()) == n and all(wc(l[2:]) <= mx for l in out.splitlines())
    elif kind == 1:
        out = "\n".join(f"{j + 1}. {f}" for j, f in enumerate(facts))
        p = R.choice([f"Number {NUMW[n]} facts about {topic}, one per line, none longer than {mx} words.", f"Write a numbered list of exactly {n} facts about {topic} (max {mx} words per item)."])
        ok = len(out.splitlines()) == n and all(wc(re.sub(r"^\d+\.\s", "", l)) <= mx for l in out.splitlines())
    elif kind == 2:
        out = " ".join(facts)
        p = R.choice([f"Write a single paragraph about {topic} that has exactly {NUMW[n]} sentences.", f"In one paragraph, no line breaks, tell me about {topic} using exactly {n} sentences."])
        ok = len(re.findall(r"[.!?](?:\s|$)", out)) == n and "\n" not in out
    elif kind == 3:
        out = "```json\n" + json.dumps(facts, indent=2) + "\n```"
        p = R.choice([f"Return a JSON array with exactly {n} strings, each a fact about {topic}.", f"Give {NUMW[n]} facts about {topic} as a JSON list of strings. Nothing else."])
        ok = len(json.loads(out[8:-4])) == n
    else:
        w = R.choice([x.strip(".,").lower() for f in facts for x in f.split() if len(x.strip(".,")) >= 6])
        has = [f for f in TOPICS[topic] if re.search(r"\b" + re.escape(w) + r"\b", f.lower())]
        rest = [f for f in TOPICS[topic] if f not in has]
        out_f = [has[0]] + sample(rest, n - 1)
        R.shuffle(out_f)
        out = "\n".join("* " + f for f in out_f)
        p = f"Give me {NUMW[n]} bullet points about {topic}. At least one bullet must contain the word \"{w}\"."
        ok = len(out_f) == n and any(re.search(r"\b" + re.escape(w) + r"\b", f.lower()) for f in out_f)
    if not ok:
        drop("count_check", p)
        continue
    add("exact_count", [("user", p), ("assistant", out)])

for i in range(26):
    topic = R.choice(list(TOPICS))
    pool = list(TOPICS[topic])
    cons_names = sample(["count", "lower", "nocomma", "prefix", "closing", "title", "numbered", "maxw"], R.randint(3, 4))
    n = R.randint(3, 4)
    phrases, check = [], []
    if "nocomma" in cons_names:
        pool = [f for f in pool if "," not in f]
    facts = sample(pool, min(n, len(pool)))
    n = len(facts)
    lines = list(facts)
    if "lower" in cons_names:
        lines = [l.lower() for l in lines]; phrases.append("everything in lowercase")
    if "maxw" in cons_names:
        mx = max(wc(l) for l in lines) + 1
        phrases.append(f"no line longer than {mx} words (not counting the bullet or number)")
    if "numbered" in cons_names:
        lines = [f"{j + 1}. {l}" for j, l in enumerate(lines)]; phrases.append("a numbered list")
    elif "prefix" in cons_names:
        lines = [f">> {l}" for l in lines]; phrases.append("each line starting with '>> '")
    else:
        lines = [f"- {l}" for l in lines]; phrases.append("hyphen bullets")
    head = []
    if "title" in cons_names:
        head = [topic.upper() + " FACTS"]; phrases.append(f"a first line that is the title {topic.upper()} FACTS")
    tail = []
    if "closing" in cons_names:
        tail = ["Hope this helps."]; phrases.append("a final line reading exactly 'Hope this helps.'")
    if "nocomma" in cons_names:
        phrases.append("no commas anywhere")
    out = "\n".join(head + lines + tail)
    ok = len([l for l in out.splitlines() if l not in head + tail]) == n
    if "nocomma" in cons_names and "," in out:
        ok = False
    if "lower" in cons_names and any(ch.isupper() for l in lines for ch in l) :
        ok = False
    if "title" in cons_names and out.splitlines()[0] != topic.upper() + " FACTS":
        ok = False
    if not ok:
        drop("combo", topic)
        continue
    random_order = phrases[:]
    R.shuffle(random_order)
    p = R.choice([f"Write exactly {n} facts about {topic}. Requirements: " + "; ".join(random_order) + ".", f"Topic: {topic}. Produce {n} lines of facts and obey every rule - " + ", ".join(random_order) + ".", f"I want {NUMW.get(n, n)} short facts about {topic}, formatted with " + " and ".join(random_order) + "."])
    add("multi_constraint", [("user", p), ("assistant", out)], diff="hard")

# exclusion of a word / letter
for i in range(14):
    topic = R.choice(list(TOPICS))
    bad = R.choice(["the", "and", "is", "are", "of"])
    pool = [f for f in TOPICS[topic] if not re.search(r"\b" + bad + r"\b", f.lower())]
    n = min(R.randint(2, 4), len(pool))
    if n < 2:
        drop("exclude_pool", topic); continue
    facts = sample(pool, n)
    out = "\n".join("- " + f for f in facts)
    if re.search(r"\b" + bad + r"\b", out.lower()) or len(out.splitlines()) != n:
        drop("exclude", topic); continue
    p = R.choice([f"List {n} facts about {topic} without using the word \"{bad}\" anywhere. Use bullets.", f"Give me {NUMW.get(n, n)} bullets about {topic}. The word '{bad}' is banned - it must not appear at all."])
    add("forbidden_word", [("user", p), ("assistant", out)], diff="hard")

# ============ T10: templated emails with required fields ============
SIGNOFFS = ["Kind regards,\nThe Accounts Team", "Best wishes,\nCustomer Care", "Thank you,\nOperations", "Warm regards,\nThe Events Desk", "Sincerely,\nAdministration"]
for s in sample([x for x in S if x["place"]], 24):
    sign = R.choice(SIGNOFFS)
    name = s["people"][0]
    first = name.split()[0]
    kind = R.randrange(4)
    if kind == 0:
        subj = f"Payment reminder: {s['atext']} due {s['written']}"
        body = f"Dear {first},\n\nThis is a friendly reminder that a payment of {s['atext']} is due on {s['written']}. Please arrange it at your earliest convenience.\n\n{sign}"
        req = [name.split()[0], s["atext"], s["written"], "Subject:"]
        p = f"Write a payment reminder email to {name}. It needs a subject line (starting 'Subject:'), a greeting using the first name, the amount {s['atext']}, the due date {s['written']}, and must end with the sign-off \"{sign.splitlines()[0]}\" followed by \"{sign.splitlines()[1]}\"."
    elif kind == 1:
        subj = f"Invitation: {s['place']} meeting on {s['written']}"
        body = f"Hello {first},\n\nWe would be glad to see you at our {s['place']} meeting on {s['written']}. Please reply to confirm that you can attend.\n\n{sign}"
        req = [first, s["place"], s["written"], "Subject:"]
        p = f"Draft a short meeting invitation to {name} for a meeting in {s['place']} on {s['written']}. Include a 'Subject:' line, address them by first name, ask for a reply, and close with exactly:\n{sign}"
    elif kind == 2:
        subj = f"Thank you, {first}"
        body = f"Dear {first},\n\nThank you for your message about {s['place']}. We have noted the details from {s['written']} and will follow up shortly.\n\n{sign}"
        req = [first, s["place"], s["written"], "Subject:"]
        p = f"Reply to {name} to thank them for their message of {s['written']} about {s['place']}. Rules: a 'Subject:' line, use their first name, mention both the place and the date, and finish with this sign-off exactly:\n{sign}"
    else:
        subj = f"Delay notice for {s['place']}"
        body = f"Dear {first},\n\nWe are sorry to tell you that the work planned for {s['place']} on {s['written']} will be delayed. We will send a new date soon.\n\n{sign}"
        req = [first, s["place"], s["written"], "Subject:"]
        p = f"Write a polite delay notice to {name}. Mention that the {s['place']} work planned for {s['written']} is postponed, say a new date will follow, add a 'Subject:' line, greet {name.split()[0]} by first name, and use this closing verbatim:\n{sign}"
    out = f"Subject: {subj}\n\n{body}"
    if not all(r in out for r in req) or not out.endswith(sign):
        drop("email", subj); continue
    add("email_fields", [("user", p), ("assistant", out)], src="composed", diff="hard")

# ============ T11: "answer only with ..." ============
KEYWORDS = [("refund", "refund"), ("a discount", "discount"), ("a deposit", "deposit"), ("Wi-Fi", "wi-fi"), ("lunch", "lunch"), ("a warranty", "warranty"), ("a bicycle", "bicycle"), ("a visa", "visa"), ("tickets", "ticket"), ("pets", "pets")]
for s in sample(S, 40):
    kind = R.randrange(4)
    if kind == 0 and s["place"]:
        p = R.choice([f"Which place is named in this message? Answer with the place name only.\n\n{s['text']}", f"{s['text']}\n\nWhere is this about? Reply with one place name, nothing else."])
        add("answer_only_place", [("user", p), ("assistant", s["place"])])
    elif kind == 1:
        label, kw = R.choice(KEYWORDS)
        ans = "Yes" if kw in s["text"].lower() else "No"
        p = R.choice([f"Does this message mention {label}? Answer Yes or No only.\n\n{s['text']}", f"{s['text']}\n\nTrue or false in one word (Yes/No): is {label} mentioned?"])
        add("answer_only_yesno", [("user", p), ("assistant", ans)], diff="easy")
    elif kind == 2:
        p = R.choice([f"How many people are named in this message? Reply with a single digit.\n\n{s['text']}", f"{s['text']}\n\nCount the named people. Digit only."])
        add("answer_only_count", [("user", p), ("assistant", str(len(s["people"])))], diff="easy")
    else:
        p = R.choice([f"What year is mentioned in this message? Reply with the four digits only.\n\n{s['text']}", f"{s['text']}\n\nReply with just the year that appears in the text."])
        add("answer_only_year", [("user", p), ("assistant", str(s["d"].year))], diff="easy")

# ============ T15: simplified rewrites with a hard word-length limit ============
for orig, simp in SIMPLE:
    mxl = max(len(w.strip(".,")) for w in simp.split())
    sentences = len(re.findall(r"[.!?](?:\s|$)", simp))
    if mxl > 6 or sentences != 1 or wc(simp) > 16:
        drop("simple", simp); continue
    p = R.choice([f"Rewrite this for a young reader using only words of {mxl} letters or fewer, in one sentence of at most {wc(simp)} words:\n\n{orig}", f"Simplify: \"{orig}\"\nConstraints: one sentence, {wc(simp)} words max, no word longer than {mxl} letters. Output only the new sentence."])
    add("simplify_constrained", [("user", p), ("assistant", simp)], src="authored", diff="hard")

# ============ T13: conflicting or impossible constraints (hand-written) ============
CONFLICTS = [
    ("Write a 300-word essay about oceans in exactly 40 words.", "Those two requirements conflict: 300 words and exactly 40 words cannot both be true. Tell me which one you want. If you meant a 40-word version, here it is:\n\nOceans cover most of the planet, hold nearly all its water and shape weather, climate and trade. They feed billions, absorb heat and carbon dioxide, and shelter countless species. Protecting them from pollution, overfishing and warming matters for everyone's future."),
    ("Answer only with 'yes' or 'no': what is the capital of France?", "That question can't be answered with yes or no, because it asks for a name rather than a choice. The answer is Paris. If you want a yes/no, you could ask \"Is Paris the capital of France?\" and I would say yes."),
    ("Give me five bullet points, but only three lines.", "Five bullets need five lines, so \"five bullets\" and \"three lines\" conflict. I can do one of these: three bullets on three lines, or five bullets with the first three combined into pairs. Which would you like? Meanwhile, here is the three-bullet version:\n\n- Plan first\n- Start small\n- Review often"),
    ("Reply in JSON only, and also add a friendly paragraph before the JSON.", "Those conflict: if the reply is JSON only, there can be no paragraph before it. I'll keep the output valid JSON and put the friendly note inside it:\n\n```json\n{\n  \"message\": \"Happy to help! Tell me what you need.\"\n}\n```"),
    ("Write a sentence without using the letter 'e' that includes the word 'the'.", "Those rules conflict, because the word \"the\" contains the letter 'e'. I can write an e-free sentence that omits \"the\", for example: \"Birds sing at dawn from a tall, dark oak.\" Or I can keep \"the\" and drop the no-'e' rule."),
    ("Summarise this article in one word and explain your reasoning in detail.", "A one-word summary and a detailed explanation pull in opposite directions. I can give the one word and then a short explanation set apart from it, or just the single word. Also, you haven't pasted the article yet, so please send it."),
    ("Make the list alphabetical and also sorted by length.", "Both orders can't apply at once because they usually disagree. I can sort primarily by length and alphabetically within the same length, or the other way round. Tell me which should come first and paste the list."),
    ("Write exactly 10 words that are all longer than 12 letters.", "That is a very tight request, since words of 13 letters or more are rare. It is possible with effort, but the result would be a list of odd, stilted terms rather than useful writing. Would you accept 10 words of at least 9 letters instead, or a shorter list of long words such as \"internationalisation\" and \"misunderstandings\"?"),
    ("Translate this into French and keep it in English.", "Those two instructions conflict: the text can't be both French and English. Do you want the French translation alone, or both versions side by side? Please paste the text and tell me which."),
    ("Give a one-sentence answer with three separate paragraphs.", "A single sentence can't fill three paragraphs. I can write one sentence, or three one-sentence paragraphs. Which would you like?"),
    ("Don't use any numbers, then tell me what 12 times 12 is.", "I can't both avoid numbers and state 12 times 12 without a number, unless I spell it out. The answer in words is: one hundred forty-four. If digits are the problem, tell me and I'll adjust."),
    ("Use only one-syllable words to explain photosynthesis, including the word 'photosynthesis'.", "That can't be done strictly, because \"photosynthesis\" has five syllables. I can explain it in one-syllable words and name it once as an exception: Plants take light, air and water and make food and the air we breathe. Is that acceptable?"),
    ("Write a poem that has no words.", "A poem with no words contradicts itself, since a poem is made of words. I could write a very short one, use only punctuation as an experiment, or describe a wordless poem as a concept. Which of these do you prefer?"),
    ("List all prime numbers, and keep it under 20 words.", "There are infinitely many primes, so I can't list all of them in any length. Under 20 words I can give the first ones: 2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47."),
    ("Respond in exactly two sentences, but explain the full history of Rome.", "These pull against each other, since the full history of Rome runs across centuries. I can give a two-sentence overview: Rome grew from a small city on the Tiber into a republic and then an empire that shaped law, language and architecture across Europe. After the western empire collapsed in 476, its legacy lived on in the Byzantine East, the Church and modern institutions. For more depth, I'd need more room."),
]
for q, a in CONFLICTS:
    add("conflicting_constraints", [("user", q), ("assistant", a)], src="authored", diff="hard")
    if q.startswith("Answer only with 'yes' or 'no'"):
        pass

# ============ T14: system-prompt rules that persist across turns ============
def sentence_count(s):
    return len(re.findall(r"[.!?](?:\s|$)", s))


RULES = [
    dict(name="two_sentences", systems=["You are a concise tutor. Every reply must be exactly two sentences.", "Always answer in exactly two sentences, never more or fewer.", "You are a quiz companion. Reply with exactly two sentences each time."],
         fn=lambda q: f"{q[1][0]} {q[1][1]}", chk=lambda a, q: sentence_count(a) == 2 and "\n" not in a),
    dict(name="short_phrase", systems=["Answer with the shortest correct phrase only: no full sentences, no trailing punctuation.", "You are a lookup tool. Return only the answer phrase, with no punctuation at the end."],
         fn=lambda q: q[2], chk=lambda a, q: a == q[2] and not a.endswith(".")),
    dict(name="lowercase", systems=["Write every reply entirely in lowercase letters.", "You are a laid-back assistant who only types in lowercase, even for names."],
         fn=lambda q: f"{q[1][0]} {q[1][1]}".lower(), chk=lambda a, q: a == a.lower()),
    dict(name="json", systems=["Respond only with valid JSON using exactly the keys \"answer\" and \"confidence\" (high, medium or low).", "You are an API. Output a single JSON object with keys answer and confidence. No prose."],
         fn=lambda q: json.dumps({"answer": q[2], "confidence": q[3]}), chk=lambda a, q: set(json.loads(a)) == {"answer", "confidence"}),
    dict(name="noted_prefix", systems=["Begin every reply with the word 'Noted:' and then give one sentence.", "Start each answer with 'Noted:' and keep it to a single sentence."],
         fn=lambda q: f"Noted: {q[1][0]}", chk=lambda a, q: a.startswith("Noted: ") and sentence_count(a) == 1),
    dict(name="numbered_two", systems=["Format every answer as a numbered list with exactly two items.", "Reply using a numbered list: '1.' and '2.' only, one sentence each."],
         fn=lambda q: f"1. {q[1][0]}\n2. {q[1][1]}", chk=lambda a, q: [l[:3] for l in a.splitlines()] == ["1. ", "2. "]),
    dict(name="all_caps", systems=["Reply in ALL CAPITAL LETTERS, in one sentence.", "You shout every answer: capital letters only, one sentence."],
         fn=lambda q: q[1][0].upper(), chk=lambda a, q: a == a.upper() and sentence_count(a) == 1),
    dict(name="closing_phrase", systems=["End every reply with the exact phrase 'Anything else?' on its own line.", "After you answer in one sentence, add a final line that says exactly: Anything else?"],
         fn=lambda q: f"{q[1][0]}\nAnything else?", chk=lambda a, q: a.splitlines()[-1] == "Anything else?" and len(a.splitlines()) == 2),
    dict(name="answer_source", systems=["Use this format for every reply: 'Answer: <short answer>. Source: general knowledge.'", "You must reply exactly as 'Answer: X. Source: general knowledge.' where X is the short answer."],
         fn=lambda q: f"Answer: {q[2]}. Source: general knowledge.", chk=lambda a, q: a == f"Answer: {q[2]}. Source: general knowledge."),
]
FOLLOW = ["Next question: {q}", "{q}", "Okay, and {ql}", "One more: {q}", "Thanks. {q}", "Alright - {q}"]
singles = multis = 0
for r in RULES:
    for rep in range(7):
        sysm = R.choice(r["systems"])
        nturn = 1 if rep < 3 else R.choice([2, 3])
        qs = sample(QA, nturn)
        turns, ok = [], True
        for j, q in enumerate(qs):
            ask = q[0] if j == 0 else R.choice(FOLLOW).format(q=q[0], ql=q[0][0].lower() + q[0][1:])
            ans = r["fn"](q)
            if not r["chk"](ans, q):
                ok = False
                break
            turns += [("user", ask), ("assistant", ans)]
        if not ok:
            drop("rule_check", r["name"])
            continue
        add("system_rule_" + r["name"], turns, system=sysm, diff="medium")
        if nturn == 1:
            singles += 1
        else:
            multis += 1

# multi-turn where the user mid-way asks the format to change, then the new rule persists
for i in range(10):
    a, b = R.sample(RULES, 2)
    if a["name"] in ("short_phrase", "json") or b["name"] in ("short_phrase", "json"):
        continue
    q1, q2, q3 = sample(QA, 3)
    ask_b = {"lowercase": "From now on please write only in lowercase.", "all_caps": "Switch to capital letters from now on, one sentence each.", "numbered_two": "Please format answers as a two-item numbered list from here on.",
             "two_sentences": "From now on, answer in exactly two sentences.", "noted_prefix": "Start every answer with 'Noted:' from now on, one sentence.", "closing_phrase": "From now on finish each answer with a line saying Anything else?",
             "answer_source": "Use this format from now on: 'Answer: X. Source: general knowledge.'"}.get(b["name"])
    if not ask_b:
        continue
    t = [("user", q1[0]), ("assistant", f"{q1[1][0]} {q1[1][1]}"), ("user", ask_b + " " + q2[0]), ("assistant", b["fn"](q2)), ("user", q3[0]), ("assistant", b["fn"](q3))]
    if not (b["chk"](b["fn"](q2), q2) and b["chk"](b["fn"](q3), q3)):
        drop("rule_switch", b["name"]); continue
    add("format_switch_persists", t, diff="hard")

if __name__ == "__main__":
    save("instruction21", recs)
    print("system single/multi:", singles, multis, "| dropped", len(dropped))
    for d in dropped[:12]:
        print("  ", d)
