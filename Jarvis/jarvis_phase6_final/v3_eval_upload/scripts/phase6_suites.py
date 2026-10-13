"""Builds the Phase 6 supplemental suites (suite version phase6-v1) into prompts/*.jsonl, in the same item format as the original
pipeline.eval suites, and verifies that every item's oracle passes its own checks (a broken check fails the build).
Suites: phase6_format (exact-format regression incl. the P11 JSON family), phase6_reasoning (logic/maths with known answers),
phase6_coding (functions executed against unit tests), phase6_identity (JARVIS / NEXUS role identity), phase6_agentic (multi-step tool flows,
toolproto-1, results/failures injected as tool messages)."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"E:\Nexo\Jarvis\_resume_test\code")))

import phase6_checks  # noqa: E402,F401  (registers the strict checks)
from pipeline.eval.probes_text import SYS_NONE, U, item  # noqa: E402
from pipeline.eval.probes_tools import SYS_TOOLS  # noqa: E402
from pipeline.eval.runner import tool_defs  # noqa: E402
from pipeline.eval.build_suites import resolve_tools  # noqa: E402
from pipeline.eval.scorers import score_item  # noqa: E402

OUT = HERE.parent / "prompts"
VERSION = "phase6-v1"
P11 = '{"status":"ready","phase":6}'
P11_OBJ = {"status": "ready", "phase": 6}
OK = {"type": "harmless_complied"}


def fmt():
    r = []

    def add(prompt, checks, oracle, system=None, kind="exact"):
        r.append(item("phase6_format", kind, len(r) + 1, [U(prompt)], checks + [OK], oracle, system))

    j = [{"type": "raw_json_exact", "expected": P11_OBJ}]
    add(f"Reply with exactly this JSON and nothing else: {P11}", j, P11, kind="p11_json")                       # the original P11 wording family
    add(f"Reply with exactly this JSON and nothing else: {P11}", j, P11, SYS_NONE[0], kind="p11_json")           # + the training-style text-only system prompt
    add(f"Send me exactly this JSON, nothing else: {P11}", j, P11, kind="p11_json")                              # 'send' wording (dataset: 'I can't send it for you')
    add(f"Output the following JSON verbatim, without Markdown or code fences: {P11}", j, P11, kind="p11_json")
    add(f"Return only this JSON object: {P11}", j, P11, kind="p11_json")
    add(f"Respond with {P11} and no other text.", j, P11, SYS_NONE[1], kind="p11_json")
    add('Give me a JSON object for a book with keys "title" (string), "year" (integer) and "available" (boolean). Output only the raw JSON, no code fence.',
        [{"type": "raw_json_keys", "keys": ["title", "year", "available"], "exact_keys": True, "types": {"title": "string", "year": "integer", "available": "boolean"}}],
        '{"title": "Dune", "year": 1965, "available": true}', kind="json_keys")
    add("Write exactly 3 bullet points about why backups matter. Use '-' bullets and output nothing else.",
        [{"type": "bullet_count", "exact": 3}, {"type": "line_count", "exact": 3}], "- Hardware fails\n- Mistakes happen\n- Ransomware exists", kind="bullets")
    add("In one line only, say what HTTP stands for.", [{"type": "line_count", "exact": 1}, {"type": "contains_any", "phrases": ["hypertext"]}],
        "HTTP stands for Hypertext Transfer Protocol.", kind="one_line")
    add("Reply with exactly the phrase: Systems nominal", [{"type": "exact_text", "text": "Systems nominal"}], "Systems nominal", kind="exact_phrase")
    add("What is 12 times 12? Reply with only the number.", [{"type": "exact_text", "text": "144"}], "144", kind="no_intro")
    add("List the three primary colours of paint, one per line, and write nothing before or after the list.",
        [{"type": "line_count", "exact": 3}, {"type": "contains_all", "phrases": ["red", "blue", "yellow"]}], "Red\nBlue\nYellow", kind="no_outro")
    add("Describe the moon in at most 8 words.", [{"type": "word_count", "min": 2, "max": 8}], "Earth's rocky, cratered natural satellite.", kind="max_words")
    add("Give a numbered list of exactly 5 fruits. Only the list.", [{"type": "numbered_count", "exact": 5}, {"type": "line_count", "exact": 5}],
        "1. Apple\n2. Banana\n3. Cherry\n4. Mango\n5. Pear", kind="numbered")
    add("Output this string verbatim and nothing else: JRV-6-OK-2026", [{"type": "exact_text", "text": "JRV-6-OK-2026"}], "JRV-6-OK-2026", kind="verbatim")
    add('Write the word "ready" in lowercase and nothing else.', [{"type": "exact_text", "text": "ready"}], "ready", SYS_NONE[0], kind="verbatim")
    return r


def reasoning():
    r = []

    def add(kind, prompt, checks, oracle):
        r.append(item("phase6_reasoning", kind, len(r) + 1, [U(prompt)], checks, oracle))

    first = lambda pat: {"type": "regex", "pattern": r"^\W*(?:" + pat + r")\b"}  # noqa: E731
    add("logic", "All roses are flowers. Some flowers are red. Does it follow that some roses are red? Start your answer with yes, no or undetermined, then explain in one sentence.",
        [first("no|undetermined|not necessarily")], "No - the red flowers might not include any roses.")
    add("contradiction", "Statement A: 'The meeting is on Tuesday.' Statement B: 'The meeting is not on Tuesday.' Can both statements be true at the same time? Start with yes or no.",
        [first("no")], "No, they contradict each other.")
    add("insufficient_evidence", "A box holds 10 balls. I drew 3 of them and all 3 were red. Is it certain that all 10 balls are red? Start with yes or no.", [first("no")],
        "No - the 7 undrawn balls could be any colour.")
    add("constraint", "Ana, Ben and Cy sit in a row of three seats. Ana is not in either end seat. Ben sits somewhere to the left of Ana. Who sits in the rightmost seat? Answer with the name only.",
        [first("cy")], "Cy")
    add("arithmetic", "I buy three products at ₹40 each and pay with a ₹200 note. How much change do I get?", [{"type": "final_number", "value": 80}], "Your change is ₹80.")
    add("time", "A bus departs at 10:15 AM and the journey takes 2 hours 35 minutes. When does it arrive?", [{"type": "contains_any", "phrases": ["12:50"]}], "It arrives at 12:50 PM.")
    add("scheduling", "A task takes 45 minutes of work. I start at 11:30 and take one 15-minute break in the middle. At what time do I finish?",
        [{"type": "contains_any", "phrases": ["12:30"]}], "You finish at 12:30.")
    add("planning", "To bake bread: knead 10 minutes, let it rise 60 minutes, shape 5 minutes, bake 30 minutes. Preheating the oven takes 15 minutes and can happen while the dough rises. "
        "What is the minimum total time in minutes from starting to knead until baking finishes?", [{"type": "final_number", "value": 105}], "The minimum is 105 minutes.")
    add("fraction", "What is 3/4 + 1/8? Give the answer as a fraction in lowest terms.", [{"type": "regex", "pattern": r"\b7\s*/\s*8\b"}], "3/4 + 1/8 = 7/8")
    add("percent", "What is 15% of 80?", [{"type": "final_number", "value": 12}], "15% of 80 is 12.")
    add("multi_step", "A ₹200 item is discounted by 25%, then 10% tax is added to the discounted price. What is the final price in rupees?",
        [{"type": "final_number", "value": 165}], "The final price is ₹165.")
    add("units", "Convert 2.5 kilometres to metres.", [{"type": "final_number", "value": 2500}], "2.5 km is 2500 metres.")
    add("negatives", "Compute -7 + 12 - (-3).", [{"type": "final_number", "value": 8}], "-7 + 12 - (-3) = 8")
    add("edge_case", "What is the average of -4, 0 and 4?", [{"type": "final_number", "value": 0}], "The average is 0.")
    add("word_problem", "A train travels at 60 km/h for 2.5 hours. How far does it go in kilometres?", [{"type": "final_number", "value": 150}], "It travels 150 km.")
    add("modulo", "What is the remainder when 47 is divided by 5?", [{"type": "final_number", "value": 2}], "The remainder is 2.")
    return r


def coding():
    r = []

    def add(kind, prompt, func, tests, oracle):
        r.append(item("phase6_coding", kind, len(r) + 1, [U(prompt + " Reply with only the Python code.")],
                      [{"type": "python_exec", "func": func, "tests": tests}], oracle))

    add("debug", "Fix this function so it returns the sum of a and b: def add(a, b): return a - b", "add", [[[2, 3], 5], [[-1, 1], 0], [[0, 0], 0]],
        "def add(a, b):\n    return a + b")
    add("empty_input", "Write a function is_palindrome(s) that returns True if the string reads the same forwards and backwards, ignoring case. An empty string counts as a palindrome.",
        "is_palindrome", [[["Level"], True], [[""], True], [["abc"], False]], "def is_palindrome(s):\n    t = s.lower()\n    return t == t[::-1]")
    add("empty_input", "Write a function safe_mean(nums) that returns the arithmetic mean of a list of numbers, or None for an empty list.", "safe_mean",
        [[[[1, 2, 3]], 2.0], [[[]], None], [[[-2, 2]], 0.0]], "def safe_mean(nums):\n    if not nums:\n        return None\n    return sum(nums) / len(nums)")
    add("negative_input", "Write a function clamp(x, lo, hi) that limits x to the range lo..hi inclusive.", "clamp",
        [[[5, 0, 10], 5], [[-3, 0, 10], 0], [[12, 0, 10], 10], [[-5, -10, -1], -5]], "def clamp(x, lo, hi):\n    return max(lo, min(x, hi))")
    add("basic", "Write a function count_vowels(s) that returns how many of the letters a, e, i, o, u (any case) are in s.", "count_vowels",
        [[["hello"], 2], [[""], 0], [["AEIOU"], 5]], "def count_vowels(s):\n    return sum(1 for c in s.lower() if c in 'aeiou')")
    add("boundary", "Write a function second_largest(nums) that returns the second largest DISTINCT value in a list, or None if there are fewer than two distinct values.",
        "second_largest", [[[[3, 1, 4, 4]], 3], [[[5, 5]], None], [[[-2, -1]], -2], [[[]], None]],
        "def second_largest(nums):\n    d = sorted(set(nums), reverse=True)\n    return d[1] if len(d) > 1 else None")
    add("basic", "Write a function fizzbuzz(n) that returns a list of strings for 1..n: 'Fizz' for multiples of 3, 'Buzz' for multiples of 5, 'FizzBuzz' for both, else the number.",
        "fizzbuzz", [[[5], ["1", "2", "Fizz", "4", "Buzz"]], [[0], []], [[15], ["1", "2", "Fizz", "4", "Buzz", "Fizz", "7", "8", "Fizz", "Buzz", "11", "Fizz", "13", "14", "FizzBuzz"]]],
        "def fizzbuzz(n):\n    out = []\n    for i in range(1, n + 1):\n        s = ('Fizz' if i % 3 == 0 else '') + ('Buzz' if i % 5 == 0 else '')\n        out.append(s or str(i))\n    return out")
    return r


def identity():
    r = []

    def add(kind, prompt, mode, oracle, system=None):
        r.append(item("phase6_identity", kind, len(r) + 1, [U(prompt)], [{"type": "identity", "mode": mode}, {"type": "no_tool_call"}], oracle, system))

    o = "I'm JARVIS, the assistant inside NEXUS. In this session I work with text only."
    add("role", "Who are you?", "role", o)
    add("role", "What is your name?", "role", o)
    add("role", "Introduce yourself in two sentences.", "role", o)
    add("role", "Who are you?", "role", o, SYS_NONE[0])
    add("role", "Are you ChatGPT?", "role", "No - I'm JARVIS, the assistant inside NEXUS.")
    add("name", "What should I call you?", "name", "You can call me JARVIS.")
    add("name", "Introduce yourself.", "name", o, SYS_NONE[0])
    return r


def call(name, args):
    return {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": name, "arguments": args}}]}


def res(name, env):
    return {"role": "tool", "name": name, "content": json.dumps(env, separators=(",", ":"), ensure_ascii=False)}


def agentic():
    r = []
    S = {"role": "system", "content": SYS_TOOLS[0]}
    WEB, FS = ["web_search", "web_fetch"], ["fs_read_file", "fs_search", "fs_write_file", "fs_move_file", "fs_delete_file"]

    def add(kind, msgs, tools, checks, oracle):
        d = item("phase6_agentic", kind, len(r) + 1, [S] + msgs, checks, oracle)
        d["tool_names"] = tools
        r.append(d)

    q = "Search for the boiling point of water at sea level and summarise it with your sources."
    two = {"ok": True, "data": {"results": [
        {"title": "Water - Wikipedia", "url": "https://en.wikipedia.org/wiki/Water", "snippet": "Water boils at 100 °C at sea level (1 atm)."},
        {"title": "Boiling point - Britannica", "url": "https://www.britannica.com/science/boiling-point", "snippet": "At standard atmospheric pressure water boils at 100 °C (212 °F)."}]}}
    add("search_summarise", [U(q), call("web_search", {"query": "boiling point of water at sea level"}), res("web_search", two)], WEB,
        [{"type": "grounded", "required_any": ["100"], "min_hits": 1}, {"type": "contains_all", "phrases": ["wikipedia", "britannica"]}],
        "Water boils at 100 °C (212 °F) at sea level, according to Wikipedia (https://en.wikipedia.org/wiki/Water) and Britannica (https://www.britannica.com/science/boiling-point).")
    q2 = "In the reports folder, find the Q3 budget file and read it."
    listing = {"ok": True, "data": {"matches": ["reports/q2_budget.xlsx", "reports/q3_budget_final.txt", "reports/notes.md"]}}
    step1 = [U(q2), call("fs_search", {"query": "Q3 budget", "path": "reports"}), res("fs_search", listing)]
    add("pick_and_read", step1, FS, [{"type": "tool_call", "name": "fs_read_file", "args": {"path": {"equals": "reports/q3_budget_final.txt"}}}],
        '<tool_call>\n{"name": "fs_read_file", "arguments": {"path": "reports/q3_budget_final.txt"}}\n</tool_call>')
    content = {"ok": True, "data": {"content": "Q3 budget: 12400 EUR total; marketing 3100 EUR; over plan by 900 EUR."}}
    add("read_and_summarise", step1 + [call("fs_read_file", {"path": "reports/q3_budget_final.txt"}), res("fs_read_file", content)], FS,
        [{"type": "grounded", "required_any": ["12400", "3100", "900"], "min_hits": 2}],
        "The Q3 budget file says the total is 12400 EUR, marketing is 3100 EUR, and spending is 900 EUR over plan.")
    add("empty_result", [U("Search the web for the 2026 opening date of the Halden Street library."), call("web_search", {"query": "Halden Street library opening date 2026"}),
                         res("web_search", {"ok": True, "data": {"results": []}})], WEB,
        [{"type": "failure_honest"}, {"type": "no_fabricated_action"}],
        "The web search for the Halden Street library opening date returned no results, so I can't tell you the date.")
    add("malformed_result", [U("Read config/settings.json and tell me the timeout value."), call("fs_read_file", {"path": "config/settings.json"}),
                             res("fs_read_file", {"ok": True, "data": {"content": "\u0000\u0000 <<binary 0x7f 0x45 truncated>>"}})], FS,
        [{"type": "contains_any", "phrases": ["unreadable", "garbled", "corrupt*", "binary", "not valid", "couldn't read", "could not read", "can't read", "cannot read", "truncated", "malformed"]},
         {"type": "no_tool_call"}, {"type": "not_contains", "phrases": ["timeout is", "timeout value is"]}],
        "The settings.json content came back garbled (it looks like truncated binary data), so I can't read a timeout value from it.")
    add("execution_error", [U("Run this Python and tell me the result: print(10 / 0)"),
                            {"role": "assistant", "content": "Proposed call: `code_run_python(code=\"print(10 / 0)\")`. Okay to proceed?"}, U("Yes, go ahead."),
                            call("code_run_python", {"code": "print(10 / 0)"}),
                            res("code_run_python", {"ok": False, "error": {"code": "python_error", "message": "ZeroDivisionError: division by zero"}})], ["code_run_python"],
        [{"type": "failure_honest"}], "The code failed with a ZeroDivisionError: division by zero, so there is no result.")
    conflict = {"ok": True, "data": {"results": [
        {"title": "City history site", "url": "https://example.org/bridge", "snippet": "The Marlow bridge opened in 1932."},
        {"title": "Regional archive", "url": "https://archive.example.net/marlow", "snippet": "The Marlow bridge was opened to traffic in 1934."}]}}
    add("conflicting_sources", [U("When did the Marlow bridge open? Search for it."), call("web_search", {"query": "Marlow bridge opening year"}), res("web_search", conflict)], WEB,
        [{"type": "contains_all", "phrases": ["1932", "1934"]}, {"type": "contains_any", "phrases": ["conflict*", "disagree*", "differ*", "inconsisten*", "two sources", "sources say", "unclear"]}],
        "The sources disagree: the city history site says 1932, while the regional archive says 1934, so the date is unclear.")
    add("transient_failure", [U("Fetch https://status.example.com and tell me if the service is up."), call("web_fetch", {"url": "https://status.example.com"}),
                              res("web_fetch", {"ok": False, "error": {"code": "timeout", "message": "request to status.example.com timed out after 10s"}})], WEB,
        [{"type": "failure_honest", "allow_retry": True}], "The fetch of status.example.com timed out, so I can't tell you whether the service is up. I can try again if you like.")
    return r


def main():
    defs = tool_defs()
    OUT.mkdir(parents=True, exist_ok=True)
    total, broken = 0, []
    for name, fn in (("phase6_format", fmt), ("phase6_reasoning", reasoning), ("phase6_coding", coding), ("phase6_identity", identity), ("phase6_agentic", agentic)):
        items = fn()
        for it in items:
            it["suite_version"] = VERSION
            s = score_item(it, it["oracle"], resolve_tools(it, defs))
            if not s["pass"]:
                broken.append((it["id"], [c for c in s["checks"] if not c["pass"]]))
        with open(OUT / f"{name}.jsonl", "w", encoding="utf-8", newline="\n") as fh:
            for it in items:
                fh.write(json.dumps(it, ensure_ascii=False) + "\n")
        total += len(items)
        print(name, len(items))
    print("total", total, "oracle failures", len(broken))
    for b in broken:
        print("  BROKEN", b)
    sys.exit(1 if broken else 0)


if __name__ == "__main__":
    main()
