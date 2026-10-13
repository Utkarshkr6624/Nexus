"""Additional high-variety families: CSV/table -> raw JSON, tool-argument objects validated against the real schemas, more computed
maths (percent change, unit and time arithmetic), and honest reporting of partial / unverified tool results."""
import json

from gen_common import GENERAL_SYS, TEXT_ONLY_SYS, TOOL_SYS, Maker, env, tool_defs
from gen_format import CITY, ITEM, RAW_ASK, date, person
from pipeline.data.schema import check_arguments


def gen(seed=305):
    m = Maker("more", seed)
    r = m.rng
    D = tool_defs()
    # CSV -> JSON array of objects, typed values
    for i in range(220):
        n = r.randint(1, 5)
        rows = [{"name": person(r), "city": r.choice(CITY), "qty": r.randint(0, 40), "price": round(r.uniform(1, 99), 2), "active": r.random() < 0.5} for _ in range(n)]
        cols = r.sample(["name", "city", "qty", "price", "active"], r.randint(3, 5))
        csv = ",".join(cols) + "\n" + "\n".join(",".join(str(x[c]).lower() if isinstance(x[c], bool) else str(x[c]) for c in cols) for x in rows)
        o = [{c: x[c] for c in cols} for x in rows]
        compact = r.random() < 0.5
        a = json.dumps(o, ensure_ascii=False, separators=(",", ":")) if compact else json.dumps(o, ensure_ascii=False, indent=2)
        assert json.loads(a) == o
        q = f"Convert this CSV to a JSON array of objects. Numbers as numbers, true/false as booleans.{' One line.' if compact else ''} {r.choice(RAW_ASK)}\n\n{csv}"
        m.add([{"role": "user", "content": q}, {"role": "assistant", "content": a}], "instruction_following", "csv_to_json_raw", system=m.sys(GENERAL_SYS + TEXT_ONLY_SYS))
    # tool-argument objects requested as plain JSON (no call is made): validated against the real parameter schema
    for i in range(160):
        t = r.choice(["app_create_event", "app_create_task", "web_search", "fs_read_file", "app_update_goal", "db_query"])
        if t == "app_create_event":
            args = {"title": r.choice(["Dentist", "Team retro", "Piano lesson", "Call with Kofi"]), "start": f"{date(r)}T{r.randint(8, 18):02d}:00", "minutes": r.choice([15, 30, 45, 60, 90])}
            what = f"an event '{args['title']}' starting {args['start']} for {args['minutes']} minutes"
        elif t == "app_create_task":
            args = {"title": r.choice(["Renew insurance", "Buy a gift for Uma", "Submit expenses"]), "due": date(r)}
            what = f"a task '{args['title']}' due {args['due']}"
        elif t == "web_search":
            args = {"query": r.choice(["train times Porto to Lisbon", "how to descale a kettle", "ISO 8601 week numbers"])}
            what = f"a search for '{args['query']}'"
        elif t == "fs_read_file":
            args = {"path": r.choice(["notes/ideas.md", "data/2026/report.csv", "src/main.py"])}
            what = f"reading {args['path']}"
        elif t == "app_update_goal":
            args = {"goal": r.choice(["Run 5k", "Read 12 books", "Save 2000 euros"]), "progress": f"{r.randint(0, 100)}%"}
            what = f"setting goal '{args['goal']}' to {args["progress"]} progress"
        else:
            args = {"sql": r.choice(["SELECT name FROM users WHERE active = 1", "SELECT COUNT(*) FROM orders"])}
            what = f"running `{args['sql']}`"
        assert not check_arguments(args, D[t]["function"]["parameters"]), t
        a = json.dumps(args, ensure_ascii=False)
        q = f"Write the arguments object for `{t}` for {what}. Output only the JSON object - I'll pass it to the tool myself."
        m.add([{"role": "user", "content": q}, {"role": "assistant", "content": a}], "tool_aware", "tool_arguments_json", system=m.sys(GENERAL_SYS))
    # computed maths
    for i in range(260):
        k = r.choice(["pct_change", "convert", "time", "ratio", "compound"])
        if k == "pct_change":
            a0, a1 = r.randint(20, 400), r.randint(20, 400)
            ch = (a1 - a0) / a0 * 100
            q, a = f"A price went from {a0} to {a1}. What's the percentage change?", f"({a1} - {a0}) / {a0} x 100 = {ch:.1f}%, so it {'rose' if ch > 0 else 'fell' if ch < 0 else 'did not change'}" + (f" by {abs(ch):.1f}%." if ch else ".")
        elif k == "convert":
            v, (u1, u2, f) = r.randint(1, 250), r.choice([("km", "miles", 0.621371), ("kg", "pounds", 2.20462), ("litres", "US gallons", 0.264172), ("metres", "feet", 3.28084)])
            q, a = f"Convert {v} {u1} to {u2}.", f"{v} {u1} x {f} = {v * f:.2f} {u2}."
        elif k == "time":
            h, mi, add = r.randint(0, 23), r.choice([0, 10, 15, 20, 30, 45, 50]), r.randint(25, 400)
            tot = h * 60 + mi + add
            q = f"It's {h:02d}:{mi:02d}. What time will it be in {add} minutes?"
            a = f"{add} minutes is {add // 60} h {add % 60} min, so it will be {(tot // 60) % 24:02d}:{tot % 60:02d}" + (" the next day." if tot >= 1440 else ".")
        elif k == "ratio":
            a0, b0, tot = r.randint(1, 5), r.randint(1, 5), r.choice([60, 120, 240, 360])
            if tot % (a0 + b0):
                continue
            part = tot // (a0 + b0)
            q, a = f"Split {tot} in the ratio {a0}:{b0}.", f"There are {a0 + b0} parts, each worth {tot} / {a0 + b0} = {part}, so the shares are {a0 * part} and {b0 * part}."
        else:
            p0, rt, yrs = r.choice([1000, 2500, 5000]), r.choice([2, 3, 4, 5]), r.randint(2, 5)
            v = p0 * (1 + rt / 100) ** yrs
            q, a = f"{p0} euros earns {rt}% interest compounded yearly. How much after {yrs} years?", f"{p0} x 1.0{rt}^{yrs} = {v:.2f} euros."
        m.add([{"role": "user", "content": q}, {"role": "assistant", "content": a}], "mathematics", f"computed_{k}", system=m.sys(GENERAL_SYS))
    # partial / unverified tool results reported honestly
    for i in range(150):
        want, got = r.randint(3, 6), r.randint(1, 2)
        topic = r.choice(["used e-bikes under 1000 euros", "coworking spaces in " + r.choice(CITY), "reviews of the " + r.choice(ITEM), "laptop stands"])
        sites = r.sample(["CityGuide", "MarketHub", "ReviewNest", "LocalList", "ShopCompare", "ForumPost"], got)
        hits = [f"{j + 1}) {s} - {topic}: {r.choice(['listing updated', 'page dated', 'post from'])} {date(r)}" for j, s in enumerate(sites)]
        res = env(True, " ".join(hits))
        a = (f"The search only returned {got} result{'s' if got > 1 else ''}, not the {want} you asked for: " + "; ".join(h[3:] for h in hits)
             + ". I haven't checked these pages beyond the snippets, so treat them as leads rather than verified facts. Want me to try a broader query?")
        m.add([{"role": "user", "content": f"Find {want} options for {topic}."}, {"role": "assistant", "content": "I'll search for that.", "tool_calls": [{"type": "function", "function": {"name": "web_search", "arguments": {"query": topic}}}]},
               {"role": "tool", "name": "web_search", "content": res}, {"role": "assistant", "content": a}],
              "tool_aware", "partial_result_honest", system=r.choice(TOOL_SYS), tools=[D["web_search"]] + [D[x] for x in r.sample([k for k in D if k != "web_search"], 2)])
    return m.out
