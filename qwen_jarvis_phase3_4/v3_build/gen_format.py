"""Priority A: exact JSON / exact-format control. Every target is built with json.dumps and re-parsed; the requested format decides
whether the answer is raw JSON, fenced JSON (only when the user asks for a code block) or a clarifying question."""
import json

from gen_common import GENERAL_SYS, TEXT_ONLY_SYS, Maker

FIRST = ["Amara", "Bruno", "Chen", "Dana", "Elif", "Farid", "Greta", "Hiro", "Ines", "Jonas", "Kofi", "Lucía", "Mateo", "Nadia", "Oskar", "Priya",
         "Quinn", "Rosa", "Sven", "Tariq", "Uma", "Viktor", "Wen", "Ximena", "Yusuf", "Zoë", "Åsa", "Noël", "Søren", "Renée"]
LAST = ["Okafor", "Silva", "Nakamura", "Müller", "Kowalski", "Haddad", "Novak", "O'Brien", "Rossi", "Lindqvist", "Patel", "García", "Dubois",
        "Kim", "Mensah", "Ivanova", "Costa", "Fischer", "Yamada", "Nguyen"]
CITY = ["Lisbon", "Nairobi", "São Paulo", "Kraków", "Osaka", "Montréal", "Reykjavík", "Cape Town", "Zürich", "Hanoi", "Bogotá", "Dublin", "Tallinn",
        "Porto", "Kyoto", "Auckland", "Marrakesh", "Gdańsk", "Valparaíso", "Tromsø"]
ITEM = ["notebook", "desk lamp", "USB-C cable", "coffee grinder", "yoga mat", "kettle", "backpack", "monitor stand", "water bottle", "headphones",
        "stapler", "plant pot", "keyboard", "umbrella", "frying pan", "phone case", "bike light", "travel adapter"]
STATUS = ["pending", "active", "done", "blocked", "archived", "draft", "shipped", "cancelled"]
TAGS = ["urgent", "home", "work", "finance", "health", "travel", "study", "garden", "family", "admin"]

RAW_ASK = ["Return only the JSON, with no explanation and no code fences.", "Output the JSON object and nothing else.",
           "Respond with raw JSON only. No Markdown, no backticks, no preface.", "I'll parse your reply with json.loads, so send nothing but valid JSON.",
           "Only the JSON, please. Don't wrap it in a code block.", "Give me the JSON alone: no intro, no closing sentence.",
           "Your entire reply must be the JSON value itself.", "Plain JSON only; the output goes straight into a program."]
FENCE_ASK = ["Put the JSON in a ```json code block so I can copy it.", "Show it as a fenced JSON code block with a one-line note on what it contains.",
             "Format it as a Markdown code block, please."]


def person(r):
    return f"{r.choice(FIRST)} {r.choice(LAST)}"


def date(r):
    return f"2026-{r.randint(1, 12):02d}-{r.randint(1, 28):02d}"


def random_obj(r, depth=0):
    """A varied JSON object: strings (incl. quotes, backslashes, unicode), ints, floats, booleans, null, nested objects, arrays, empties."""
    kinds = ["order", "contact", "task", "event", "config", "reading", "inventory"]
    k = r.choice(kinds)
    if k == "order":
        o = {"order_id": f"ORD-{r.randint(1000, 9999)}", "customer": person(r), "items": [{"name": r.choice(ITEM), "qty": r.randint(1, 5),
             "unit_price": round(r.uniform(2, 120), 2)} for _ in range(r.randint(0, 3))], "paid": r.random() < 0.5, "coupon": None if r.random() < 0.6 else "SPRING10"}
    elif k == "contact":
        o = {"name": person(r), "city": r.choice(CITY), "email": None if r.random() < 0.3 else f"user{r.randint(10, 99)}@example.com",
             "tags": r.sample(TAGS, r.randint(0, 3)), "address": {"street": f"{r.randint(1, 300)} Harbour Road", "postcode": f"{r.randint(10000, 99999)}"}}
    elif k == "task":
        o = {"title": r.choice(["Renew passport", "Book dentist", "Fix the \"login\" bug", "Email the landlord", "Back up C:\\Projects", "Plan picnic"]),
             "due": date(r), "priority": r.randint(1, 5), "status": r.choice(STATUS), "subtasks": [] if r.random() < 0.4 else [{"step": i + 1, "done": r.random() < 0.5} for i in range(r.randint(1, 3))]}
    elif k == "event":
        o = {"event": r.choice(["Team sync", "Garden club", "Café meetup", "Quarterly review", "Piano lesson"]), "date": date(r), "start": f"{r.randint(7, 19):02d}:{r.choice(['00', '15', '30', '45'])}",
             "attendees": [person(r) for _ in range(r.randint(1, 3))], "online": r.random() < 0.5, "notes": "" if r.random() < 0.5 else "Bring the 2025 figures."}
    elif k == "config":
        o = {"service": r.choice(["indexer", "mailer", "scheduler", "sync"]), "enabled": r.random() < 0.7, "retries": r.randint(0, 5), "timeout_s": r.choice([2.5, 10, 30, 0.75]),
             "features": {}, "allowed_hosts": [] if r.random() < 0.5 else ["localhost", "127.0.0.1"]}
    elif k == "reading":
        o = {"sensor": f"T-{r.randint(1, 40)}", "celsius": round(r.uniform(-12, 38), 1), "humidity": r.randint(10, 95), "ok": r.random() < 0.85, "error": None}
    else:
        o = {"sku": f"SKU-{r.randint(100, 999)}", "item": r.choice(ITEM), "in_stock": r.randint(0, 250), "locations": {r.choice(CITY): r.randint(0, 40) for _ in range(r.randint(1, 3))}}
    return o


def dumps(o, compact):
    return json.dumps(o, ensure_ascii=False, separators=(",", ":")) if compact else json.dumps(o, ensure_ascii=False, indent=2)


def check(answer, expected):
    assert json.loads(answer) == expected, answer


def gen(seed=301):
    m = Maker("format", seed)
    r = m.rng

    # 1) verbatim reproduction of a given JSON value (the P11 capability, with many payloads and wordings, incl. text-only system prompts)
    lead = ["Reply with this exact JSON value and nothing more: {j}", "Echo back the following JSON exactly as given: {j}",
            "Send me back this JSON, unchanged, with no other text: {j}", "Output precisely this and nothing else: {j}",
            "Copy this JSON into your reply verbatim, no commentary: {j}", "Return the following object exactly. No code fences. {j}",
            "Please respond with just this JSON: {j}", "Write out this JSON exactly, with nothing before or after it: {j}"]
    for i in range(170):
        o = random_obj(r) if r.random() < 0.7 else {"state": r.choice(STATUS), "step": r.randint(1, 20), "ok": r.random() < 0.5}
        if r.random() < 0.2:
            o = r.choice([[], {}, [1, 2, 3], ["a", None, True], {"empty": {}, "list": []}, r.randint(-50, 50), "ready", True, None])
        j = json.dumps(o, ensure_ascii=False, separators=(",", ":"))
        system = m.sys(TEXT_ONLY_SYS + GENERAL_SYS, p_none=0.3)
        check(j, o)
        m.add([{"role": "user", "content": r.choice(lead).format(j=j)}, {"role": "assistant", "content": j}], "instruction_following", "exact_json_verbatim", system=system)

    # 2) build JSON from a description / extraction from text, raw output requested
    for i in range(190):
        nm, city, d, amt = person(r), r.choice(CITY), date(r), r.choice([12, 40, 75.5, 120, 9.99, 300, 0])
        item, qty = r.choice(ITEM), r.randint(1, 9)
        text = r.choice([f"{nm} from {city} ordered {qty} x {item} on {d} and paid {amt} euros.",
                         f"On {d}, {nm} ({city}) bought {qty} {item}(s) for a total of {amt} EUR.",
                         f"Receipt: customer {nm}; branch {city}; date {d}; {qty} units of {item}; total {amt}."])
        keys = r.choice([("customer", "city", "date", "quantity", "total"), ("name", "date", "item", "total"), ("customer", "item", "quantity")])
        vals = {"customer": nm, "name": nm, "city": city, "date": d, "quantity": qty, "total": amt, "item": item}
        o = {k: vals[k] for k in keys}
        compact = r.random() < 0.5
        ask = f"{text}\n\nExtract {', '.join(f'`{k}`' for k in keys)} as a JSON object" + (" on a single line" if compact else "") + ". Numbers must be numbers, not strings. " + r.choice(RAW_ASK)
        a = dumps(o, compact)
        check(a, o)
        m.add([{"role": "user", "content": ask}, {"role": "assistant", "content": a}], "instruction_following", "extract_json_raw", system=m.sys(GENERAL_SYS + TEXT_ONLY_SYS))

    # 3) arrays, nesting, null/booleans, empty containers, schema-constrained objects
    for i in range(130):
        n = r.randint(0, 4)
        people = [{"name": person(r), "age": r.randint(18, 80), "member": r.random() < 0.5, "nickname": None} for _ in range(n)]
        variant = r.choice(["array", "wrapped", "schema"])
        if variant == "array":
            ask = f"Make a JSON array of {n} people with fields name (string), age (integer), member (boolean) and nickname (always null). Use these names: {', '.join(p['name'] for p in people) or 'none - the list is empty'}; ages {', '.join(str(p['age']) for p in people) or '-'}; members: {', '.join('yes' if p['member'] else 'no' for p in people) or '-'}. {r.choice(RAW_ASK)}"
            o = people
        elif variant == "wrapped":
            o = {"count": n, "people": people, "meta": {"source": "manual", "filters": {}}}
            ask = f"Build an object with `count`, `people` (array of name/age/member/nickname=null) and `meta` = {{\"source\": \"manual\", \"filters\": {{}}}}. People: " + ("; ".join(f"{p['name']}, {p['age']}, {'member' if p['member'] else 'not a member'}" for p in people) or "none") + f". {r.choice(RAW_ASK)}"
        else:
            o = {"id": r.randint(1, 9999), "label": r.choice(["alpha", "beta", "gamma"]), "active": r.random() < 0.5, "score": round(r.uniform(0, 1), 3), "parent": None, "children": []}
            ask = ("Schema: {\"id\": integer, \"label\": one of alpha|beta|gamma, \"active\": boolean, \"score\": number 0-1, \"parent\": null, \"children\": empty array}. "
                   f"Produce one valid instance with id {o['id']}, label {o['label']}, active {str(o['active']).lower()}, score {o['score']}. {r.choice(RAW_ASK)}")
        a = dumps(o, r.random() < 0.5)
        check(a, o)
        m.add([{"role": "user", "content": ask}, {"role": "assistant", "content": a}], "instruction_following", f"json_{variant}", system=m.sys(GENERAL_SYS))

    # 4) invalid JSON repair -> raw corrected JSON
    breakers = [("trailing comma", lambda s: s[:-1] + ",}" if s.endswith("}") else s),
                ("single quotes", lambda s: s.replace('"', "'")),
                ("missing closing brace", lambda s: s[:-1]),
                ("Python literals", lambda s: s.replace("true", "True").replace("false", "False").replace("null", "None"))]
    for i in range(80):
        o = {"name": person(r), "active": r.random() < 0.5, "limit": r.randint(1, 100), "note": None}
        good = json.dumps(o, ensure_ascii=False, separators=(",", ":"))
        why, f = r.choice(breakers)
        bad = f(good)
        if bad == good:
            continue
        try:
            json.loads(bad)
            continue
        except ValueError:
            pass
        ask = r.choice([f"This isn't valid JSON: {bad}\nFix it and return only the corrected JSON.",
                        f"My parser rejects {bad} - repair it. Output just the fixed JSON, no explanation.",
                        f"Correct the JSON below so it parses. Reply with the JSON only.\n{bad}"])
        check(good, o)
        m.add([{"role": "user", "content": ask}, {"role": "assistant", "content": good}], "instruction_following", "json_repair", system=m.sys(GENERAL_SYS))

    # 5) fences only when asked: brief note + fenced block
    for i in range(45):
        o = random_obj(r)
        body = json.dumps(o, ensure_ascii=False, indent=2)
        ask = f"Here are the details: {json.dumps(o, ensure_ascii=False)}. Pretty-print this as JSON. {r.choice(FENCE_ASK)}"
        m.add([{"role": "user", "content": ask}, {"role": "assistant", "content": f"Here it is, pretty-printed with 2-space indentation:\n\n```json\n{body}\n```"}],
              "instruction_following", "json_fenced_on_request", system=m.sys(GENERAL_SYS))

    # 6) ambiguous format requests -> one clarifying question
    amb = [("Turn my contacts into JSON.", "Sure. Please paste the contacts you want converted. Should each contact be an object with fields like name, email and phone, and do you want raw JSON or a code block?"),
           ("Give me the data as JSON.", "Which data do you mean? Paste it (or describe the fields), and tell me if you want a single object or an array."),
           ("Output the config in the usual format.", "Which format do you want: JSON, YAML or TOML? And please paste the settings you'd like included."),
           ("Make a JSON of the meeting.", "Happy to. Which meeting, and which fields do you need (title, date, time, attendees, notes)? Paste the details and I'll return the JSON."),
           ("Convert this to JSON.", "There's nothing attached to convert yet. Paste the text or table and tell me the key names you want.")]
    for q, a in amb:
        for s in (None, r.choice(GENERAL_SYS)):
            m.add([{"role": "user", "content": q}, {"role": "assistant", "content": a}], "instruction_following", "format_clarify", system=s, source_type="authored")

    # 7) exact-text answers (only the value, no trailing words)
    for i in range(150):
        a, b = r.randint(2, 99), r.randint(2, 99)
        kind = r.choice(["mul", "add", "sub", "yesno", "word", "upper", "count"])
        if kind == "mul":
            q, ans = f"What is {a} times {b}? Reply with only the number.", str(a * b)
        elif kind == "add":
            q, ans = f"{a} + {b} = ? Answer with just the number, nothing else.", str(a + b)
        elif kind == "sub":
            q, ans = f"Compute {a} minus {b}. Output only the result.", str(a - b)
        elif kind == "yesno":
            q, ans = f"Is {a * b} divisible by {a}? Answer only yes or no.", "yes"
            if r.random() < 0.5:
                c = a * b + 1
                q, ans = f"Is {c} divisible by {a}? Answer only yes or no.", "yes" if c % a == 0 else "no"
        elif kind == "word":
            w = r.choice(["ready", "confirmed", "OK", "done", "received"])
            q, ans = f"Reply with exactly the word {w} and nothing else.", w
        elif kind == "upper":
            w = r.choice(CITY)
            q, ans = f"Write {w} in uppercase. Only the word, no punctuation.", w.upper()
        else:
            w = r.choice(["banana", "mississippi", "parallel", "bookkeeper", "committee", "reference"])
            ch = r.choice(sorted(set(w)))
            q, ans = f"How many times does the letter '{ch}' appear in '{w}'? Reply with just the number.", str(w.count(ch))
        m.add([{"role": "user", "content": q}, {"role": "assistant", "content": ans}], "instruction_following", f"exact_text_{kind}", system=m.sys(GENERAL_SYS + TEXT_ONLY_SYS))

    # 8) multi-turn: JSON then a follow-up edit, still raw
    for i in range(70):
        o = {"title": r.choice(["Grocery run", "Gym", "Read chapter 4", "Call the bank", "Water plants"]), "due": date(r), "done": False}
        a1 = json.dumps(o, ensure_ascii=False)
        o2 = dict(o)
        edit = r.choice(["done", "tags", "due"])
        if edit == "done":
            o2["done"] = True
            q2 = "Mark it as done. Same format, JSON only."
        elif edit == "tags":
            o2["tags"] = r.sample(TAGS, 2)
            q2 = f"Add a `tags` array with {o2['tags'][0]} and {o2['tags'][1]}. Still just the JSON."
        else:
            o2["due"] = date(r)
            q2 = f"Move the due date to {o2['due']}. Return the updated JSON only."
        a2 = json.dumps(o2, ensure_ascii=False)
        check(a1, o), check(a2, o2)
        m.add([{"role": "user", "content": f"Create a task object with title \"{o['title']}\", due {o['due']}, done false. JSON only, no fences."},
               {"role": "assistant", "content": a1}, {"role": "user", "content": q2}, {"role": "assistant", "content": a2}],
              "multi_turn_context", "json_followup_edit", system=m.sys(GENERAL_SYS + TEXT_ONLY_SYS))
    return m.out
