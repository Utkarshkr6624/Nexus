"""Category 4g: output-prediction (JS/Python/Java/C++/C — executed), regex (executed), code reviews, and multi-turn programming follow-ups."""
import os
import re
import subprocess
import tempfile
import shutil
from pathlib import Path
from lib import *

CAT = "programming"
R = seeded("prog_extra")
out = []
SCRATCH = Path(os.environ.get("P2_SCRATCH", tempfile.gettempdir())) / "p2_extra"
SCRATCH.mkdir(parents=True, exist_ok=True)


def run_code(lang, code):
    d = Path(tempfile.mkdtemp(dir=SCRATCH))
    try:
        if lang == "python":
            f = d / "t.py"; f.write_text(code, encoding="utf-8")
            r = subprocess.run(["python", str(f)], capture_output=True, text=True, timeout=30)
        elif lang == "javascript":
            f = d / "t.js"; f.write_text(code, encoding="utf-8")
            r = subprocess.run(["node", str(f)], capture_output=True, text=True, timeout=30)
        elif lang == "java":
            f = d / "Main.java"; f.write_text(code, encoding="utf-8")
            c = subprocess.run(["javac", str(f)], capture_output=True, text=True, timeout=60)
            if c.returncode:
                return None
            r = subprocess.run(["java", "-cp", str(d), "Main"], capture_output=True, text=True, timeout=30)
        else:
            ext = "cpp" if lang == "cpp" else "c"
            f = d / f"t.{ext}"; f.write_text(code, encoding="utf-8")
            exe = d / "t.exe"
            c = subprocess.run(["g++" if lang == "cpp" else "gcc", str(f), "-o", str(exe)], capture_output=True, text=True, timeout=90)
            if c.returncode:
                return None
            r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else None
    finally:
        shutil.rmtree(d, ignore_errors=True)


# (lang, code, expected stdout (stripped), explanation)
OUT = [
 ("javascript", "console.log(typeof null);\nconsole.log(typeof undefined);\nconsole.log(typeof []);", "object\nundefined\nobject", "`typeof null` is `'object'` (a historical bug that was never fixed), `typeof undefined` is `'undefined'`, and arrays are objects, so `typeof []` is `'object'`. Use `Array.isArray` to test for arrays."),
 ("javascript", "console.log(0.1 + 0.2 === 0.3);\nconsole.log(Math.abs(0.1 + 0.2 - 0.3) < Number.EPSILON);", "false\ntrue", "Floating-point addition gives 0.30000000000000004, so strict equality fails, while comparing within a tiny tolerance succeeds."),
 ("javascript", "console.log([1, 2, 3] + [4]);\nconsole.log([] + {});\nconsole.log(1 + '2' - 1);", "1,2,34\n[object Object]\n11", "`+` with arrays converts both to strings ('1,2,3' + '4' gives '1,2,34'), `[] + {}` becomes '' + '[object Object]', and `1 + '2'` is the string '12', which `- 1` coerces to the number 11. Implicit coercion is why `===` and explicit conversions are safer."),
 ("javascript", "for (var i = 0; i < 3; i++) {\n  setTimeout(() => console.log(i), 0);\n}", "3\n3\n3", "`var` is function-scoped, so all three callbacks share one `i`, which is 3 by the time the timers fire. Using `let` creates a new binding per iteration and prints 0, 1, 2."),
 ("javascript", "for (let i = 0; i < 3; i++) {\n  setTimeout(() => console.log(i), 0);\n}", "0\n1\n2", "With `let`, each loop iteration gets its own `i`, so each callback remembers a different value."),
 ("javascript", "console.log('start');\nsetTimeout(() => console.log('timeout'), 0);\nPromise.resolve().then(() => console.log('promise'));\nconsole.log('end');", "start\nend\npromise\ntimeout", "Synchronous code runs first (start, end). Then the microtask queue (promise callbacks) runs before the next macrotask (the timer), so `promise` prints before `timeout`."),
 ("javascript", "const a = [1, 2, 3];\nconst b = a;\nb.push(4);\nconsole.log(a.length);", "4", "Arrays are objects, and `b = a` copies the reference, not the array. Both names point to the same array, so pushing through `b` changes `a`. Use `[...a]` for a shallow copy."),
 ("javascript", "console.log([10, 9, 1, 100].sort());\nconsole.log([10, 9, 1, 100].sort((x, y) => x - y));", "[ 1, 10, 100, 9 ]\n[ 1, 9, 10, 100 ]", "`sort()` without a comparator sorts items as strings ('10' < '100' < '9'). Pass a numeric comparator for numbers."),
 ("javascript", "const obj = { a: 1, b: { c: 2 } };\nconst copy = { ...obj };\ncopy.b.c = 99;\nconsole.log(obj.b.c);", "99", "Spread makes a shallow copy: the top-level properties are copied, but `b` still points to the same nested object. Use `structuredClone` for a deep copy."),
 ("javascript", "console.log(NaN === NaN);\nconsole.log(Number.isNaN(NaN));\nconsole.log([NaN].includes(NaN));", "false\ntrue\ntrue", "NaN is the only value not equal to itself, so use `Number.isNaN` to test for it. `includes` uses a comparison that treats NaN as equal to NaN."),
 ("javascript", "function f() { return this; }\nconsole.log(f() === undefined);\n'use strict';", "false", "In sloppy mode a plain function call sets `this` to the global object, so it is not undefined. In a strict-mode function (or module) `this` would be undefined. Note `'use strict'` only works as a directive at the top of a script or function."),
 ("javascript", "const nums = [1, 2, 3, 4];\nconsole.log(nums.map(n => n * 2).filter(n => n > 4).reduce((a, n) => a + n, 0));", "14", "map doubles to [2, 4, 6, 8], filter keeps [6, 8], and reduce sums them to 14."),
 ("javascript", "console.log(parseInt('08'), parseInt('12px'), Number('12px'), +'', +' 7 ');", "8 12 NaN 0 7", "`parseInt` reads leading digits and stops at the first invalid character, whereas `Number` needs the whole string to be numeric (an empty or whitespace-only string becomes 0, surrounding whitespace is ignored)."),
 ("javascript", "const s = 'hello';\nconsole.log(s.at(-1), s.slice(1, 3), s.split('').reverse().join(''));", "o el olleh", "`at(-1)` indexes from the end, `slice(1, 3)` takes characters at positions 1 and 2 (the end is exclusive), and the last expression reverses the string."),
 ("javascript", "const user = { profile: null };\nconsole.log(user.profile?.name ?? 'anonymous');", "anonymous", "Optional chaining stops at the null `profile` and evaluates to `undefined`, and `??` supplies the default."),
 ("javascript", "async function a() { return 1; }\nconsole.log(a() instanceof Promise);", "true", "An `async` function always returns a Promise, even when it returns a plain value."),
 ("python", "print([1, 2, 3] * 2)\nprint('ab' * 3)\nprint([[0] * 2] * 2)", "[1, 2, 3, 1, 2, 3]\nababab\n[[0, 0], [0, 0]]", "Multiplying a sequence repeats it. Careful with `[[0] * 2] * 2`: it repeats a reference to the same inner list, so changing one row would change both. Use a comprehension to create independent rows."),
 ("python", "a = [[0] * 2] * 2\na[0][0] = 1\nprint(a)", "[[1, 0], [1, 0]]", "Both rows are the same list object, so assigning through one row shows up in the other. Build rows separately: `[[0] * 2 for _ in range(2)]`."),
 ("python", "print(0.1 + 0.2)\nprint(round(0.1 + 0.2, 2) == 0.3)", "0.30000000000000004\nTrue", "Binary floating point can't represent 0.1 and 0.2 exactly. Round (or use `math.isclose` / `decimal`) when comparing."),
 ("python", "x = [1, 2, 3]\ny = x\ny.append(4)\nprint(x, x is y)", "[1, 2, 3, 4] True", "Assignment never copies; `y` and `x` are two names for one list."),
 ("python", "print(bool([]), bool(''), bool(0), bool([0]), bool(None))", "False False False True False", "Empty containers, zero, empty strings and None are falsy. `[0]` is a non-empty list, so it is truthy."),
 ("python", "print(10 // 3, -10 // 3, 10 % 3, -10 % 3, divmod(-10, 3))", "3 -4 1 2 (-4, 2)", "Python's floor division rounds toward negative infinity and the remainder takes the sign of the divisor, so -10 // 3 is -4 and -10 % 3 is 2, keeping a = (a // b) * b + a % b."),
 ("python", "print('hello world'.title(), 'a,b,,c'.split(','), ' x '.strip())", "Hello World ['a', 'b', '', 'c'] x", "`split(',')` keeps empty strings between consecutive separators, unlike `split()` with no argument which collapses whitespace."),
 ("python", "d = {'a': 1}\nd.setdefault('b', []).append(2)\nprint(d)", "{'a': 1, 'b': [2]}", "`setdefault` returns the existing value or inserts the default and returns it, so you can append in one line."),
 ("python", "def f(x, items=[]):\n    items.append(x)\n    return items\nprint(f(1), f(2))", "[1, 2] [1, 2]", "The default list is created once when the function is defined and shared by all calls. Both printed lists are the same object, holding both values."),
 ("python", "print(sorted(['banana', 'Apple', 'cherry']))\nprint(sorted(['banana', 'Apple', 'cherry'], key=str.lower))", "['Apple', 'banana', 'cherry']\n['Apple', 'banana', 'cherry']", "Uppercase letters sort before lowercase in plain code-point order. Both outputs happen to look the same here because 'Apple' starts with an uppercase A; add 'apple' to see the difference."),
 ("python", "print(list(zip([1, 2, 3], 'ab')))\nprint(list(enumerate('xy', start=1)))", "[(1, 'a'), (2, 'b')]\n[(1, 'x'), (2, 'y')]", "`zip` stops at the shortest input; `enumerate` pairs each item with a counter, starting where you tell it."),
 ("python", "x = 5\ndef f():\n    print(x)\nf()\nx = 6\nf()", "5\n6", "The function looks up the global `x` when it runs, not when it is defined, so it sees the updated value on the second call."),
 ("python", "print(any([]), all([]), sum([]), max([1, 5, 3]))", "False True 0 5", "`all` of an empty list is True (nothing violates it), `any` of an empty list is False, and `sum` of nothing is 0."),
 ("python", "t = (1,)\nprint(type(t), type((1)), len(t))", "<class 'tuple'> <class 'int'> 1", "A one-element tuple needs a trailing comma; `(1)` is just the integer 1 in parentheses."),
 ("python", "s = 'abc'\nprint(s[::-1], s[1:], s[:-1], s[5:])", "cba bc ab", "Slicing is forgiving: `s[5:]` is out of range, so it gives an empty string (the output has nothing after `ab`) instead of raising an error, unlike indexing."),
 ("java", "public class Main {\n    public static void main(String[] args) {\n        String a = \"hi\";\n        String b = new String(\"hi\");\n        System.out.println(a == b);\n        System.out.println(a.equals(b));\n    }\n}\n", "false\ntrue", "`==` compares references and `new String(\"hi\")` creates a separate object, so it is false; `equals` compares the characters."),
 ("java", "public class Main {\n    public static void main(String[] args) {\n        int x = 7 / 2;\n        double y = 7 / 2;\n        double z = 7 / 2.0;\n        System.out.println(x + \" \" + y + \" \" + z);\n    }\n}\n", "3 3.0 3.5", "Dividing two ints performs integer division (3), even if the result is stored in a double. Make one operand a double to get 3.5."),
 ("java", "public class Main {\n    public static void main(String[] args) {\n        int big = Integer.MAX_VALUE;\n        System.out.println(big + 1);\n    }\n}\n", "-2147483648", "Java ints overflow silently by wrapping around. Use `long` or `Math.addExact`, which throws on overflow."),
 ("cpp", "#include <iostream>\n#include <vector>\nint main() {\n    std::vector<int> v{1, 2, 3};\n    for (auto x : v) x *= 2;\n    for (int x : v) std::cout << x << ' ';\n    std::cout << '\\n';\n    for (auto& x : v) x *= 2;\n    for (int x : v) std::cout << x << ' ';\n}\n", "1 2 3 \n2 4 6", "`auto x` copies each element, so the first loop doubles temporary copies and the vector is unchanged. `auto& x` binds a reference, so the second loop modifies the vector."),
 ("cpp", "#include <iostream>\nint main() {\n    int a = 5;\n    int* p = &a;\n    *p = 9;\n    std::cout << a << ' ' << (p == &a);\n}\n", "9 1", "`p` holds the address of `a`, so writing through `*p` changes `a`, and the pointer compares equal to `&a`."),
 ("c", "#include <stdio.h>\nint main() {\n    char s[] = \"abc\";\n    printf(\"%zu %zu\\n\", sizeof(s), sizeof(\"abc\") - 1);\n}\n", "4 3", "The array includes the terminating NUL byte, so `sizeof` is 4 for a 3-letter string. `strlen` would give 3."),
 ("c", "#include <stdio.h>\nint main() {\n    int a[] = {1, 2, 3, 4};\n    printf(\"%d %d\\n\", *(a + 2), a[3]);\n}\n", "3 4", "`*(a + 2)` is pointer arithmetic and means exactly the same as `a[2]`."),
]
n = 0
for lang, code, expected, expl in OUT:
    got = run_code(lang, code)
    if got is None or got.replace("\r", "") != expected:
        print("MISMATCH", lang, repr(code[:40]), "->", repr(got), "expected", repr(expected))
        continue
    n += 1
    fence = lang
    lab = {"javascript": "JavaScript", "python": "Python", "java": "Java", "cpp": "C++", "c": "C"}[lang]
    qs = [f"What does this {lab} code print?\n\n```{fence}\n{code.strip()}\n```", f"Predict the output of this {lab} snippet and explain why.\n\n```{fence}\n{code.strip()}\n```", f"Why does this {lab} code behave this way?\n\n```{fence}\n{code.strip()}\n```"]
    out.append(mk(CAT, "output_prediction", [("user", R.choice(qs)), ("assistant", f"It prints:\n```\n{expected}\n```\n\n{expl}")], "outp_" + code[:20], "medium", "verified", pick(R, SYS_GENERAL)))
print("output-prediction verified:", n, "of", len(OUT))

# ---------------- regex (executed) ----------------
RX = [
 ("match a US-style phone number like 555-123-4567", r"\b\d{3}-\d{3}-\d{4}\b", "call 555-123-4567 or 800-555-0199 today", ["555-123-4567", "800-555-0199"], "`\\d{3}` is exactly three digits and `\\b` anchors the match at word boundaries so it doesn't match inside longer digit strings."),
 ("find all hashtags in a tweet", r"#\w+", "loving #python and #regex, not #123!", ["#python", "#regex", "#123"], "`#` is literal and `\\w+` matches one or more letters, digits or underscores."),
 ("extract all email addresses from text", r"[\w.+-]+@[\w-]+\.[\w.-]+", "mail ada@example.com or bob.smith+x@mail.co.uk now", ["ada@example.com", "bob.smith+x@mail.co.uk"], "A simple pattern for typical addresses. A perfect email regex is impractical; this is fine for extraction, not strict validation."),
 ("find dates in YYYY-MM-DD format", r"\b\d{4}-\d{2}-\d{2}\b", "from 2024-01-15 until 2024-03-02.", ["2024-01-15", "2024-03-02"], "Four digits, two digits and two digits separated by hyphens. It matches the shape only; it doesn't check that the date exists."),
 ("find hex colour codes like #1a2B3c", r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b", "colours: #fff, #1a2B3c and #12 and #abcd", ["#fff", "#1a2B3c"], "A `#` followed by either 6 or 3 hex digits and a word boundary, so `#abcd` and `#12` are rejected."),
 ("find words that are repeated twice in a row", r"\b(\w+)\s+\1\b", "this is is a test of the the system", ["is", "the"], "`\\1` is a back-reference to what group 1 matched, so the same word must follow immediately. `findall` returns the captured group."),
 ("match numbers with optional decimals and a sign", r"[-+]?\d+(?:\.\d+)?", "temps -3.5, 12 and +0.25 degrees", ["-3.5", "12", "+0.25"], "An optional sign, digits, and an optional fractional part in a non-capturing group."),
 ("extract the text inside square brackets", r"\[([^\]]*)\]", "tags: [alpha] [beta gamma] []", ["alpha", "beta gamma", ""], "`\\[` and `\\]` match literal brackets and `[^\\]]*` matches any characters except a closing bracket, captured in a group."),
 ("find all words starting with a capital letter", r"\b[A-Z][a-z]*\b", "Alice met Bob in Paris on monday", ["Alice", "Bob", "Paris"], "A word boundary, one uppercase letter, then lowercase letters."),
 ("find all URLs starting with http or https", r"https?://[^\s,]+", "see https://example.com/a?b=1, http://x.org and ftp://nope", ["https://example.com/a?b=1", "http://x.org"], "`s?` makes the s optional, and `[^\\s,]+` takes characters until whitespace or a comma."),
]
nr = 0
for task, pat, text, expected, expl in RX:
    got = re.findall(pat, text)
    if got != expected:
        print("REGEX MISMATCH", task, got, expected)
        continue
    nr += 1
    out.append(mk(CAT, "regex", [("user", f"Write a regex to {task}."), ("assistant", f"```python\nimport re\n\npattern = r\"{pat}\"\nre.findall(pattern, text)\n```\n\n{expl}\n\nTested on `{text!r}`, it returns `{expected!r}`.")], "rx_" + task[:20], "medium", "verified", pick(R, SYS_GENERAL)))
    out.append(mk(CAT, "regex", [("user", f"Explain this regex: `{pat}`"), ("assistant", f"This pattern is meant to {task}.\n\n{expl}\n\nFor example, on `{text!r}` it finds `{expected!r}`.")], "rx_" + task[:20], "medium", "verified", pick(R, SYS_GENERAL)))
print("regex verified:", nr)

# ---------------- code reviews (authored) ----------------
REV = [
 ("python", "def get_user(id):\n    query = \"SELECT * FROM users WHERE id = \" + id\n    return db.execute(query).fetchone()", ["**SQL injection.** The id is concatenated into the query, so input such as `1 OR 1=1` changes the query. Use a parameterised query: `db.execute(\"SELECT * FROM users WHERE id = ?\", (id,))`.", "`id` shadows the built-in `id()`; use `user_id`.", "`SELECT *` returns every column, including ones you may not want to expose; select what you need.", "Decide what happens when no user exists (`fetchone()` returns None) and document or handle it."]),
 ("python", "def read_config(path):\n    try:\n        return json.load(open(path))\n    except:\n        return {}", ["**Bare `except`** hides every error, including a typo in the path, invalid JSON and even KeyboardInterrupt. Catch `FileNotFoundError` and `json.JSONDecodeError` separately and decide what each means.", "The file is never closed explicitly; use `with open(path) as f:`.", "Returning `{}` silently on a corrupt config means the app runs with defaults and no one knows; log the problem or raise."]),
 ("javascript", "app.post('/login', (req, res) => {\n  const user = users.find(u => u.name == req.body.name && u.password == req.body.password);\n  if (user) res.send('ok');\n  else res.send('fail');\n});", ["**Passwords are compared in plain text**, which means they're stored in plain text. Store a salted hash (bcrypt/argon2) and compare with the library's verify function.", "`==` should be `===` to avoid type coercion surprises.", "Use proper status codes (`401` on failure) instead of `200` with 'fail', and don't reveal whether the username or password was wrong.", "No rate limiting on a login route invites brute-force attacks."]),
 ("javascript", "function total(items) {\n  var sum = 0;\n  for (var i = 0; i <= items.length; i++) {\n    sum += items[i].price;\n  }\n  return sum;\n}", ["**Off-by-one:** `i <= items.length` reads one element past the end, so `items[i]` is `undefined` and `.price` throws. Use `<`, or better `items.reduce((s, it) => s + it.price, 0)`.", "Prefer `let`/`const` over `var`.", "Consider what should happen with an empty list or an item without a price."]),
 ("python", "def add_item(item, cart=[]):\n    cart.append(item)\n    return cart", ["**Mutable default argument.** The same list is reused across calls, so items from earlier calls leak into later carts. Use `cart=None` and create the list inside.", "Mutating and returning the argument is surprising; either return a new list or just mutate and return nothing."]),
 ("python", "import requests\n\ndef fetch(url):\n    r = requests.get(url)\n    return r.json()", ["There's **no timeout**, so a stalled server blocks forever. Pass `timeout=10`.", "It doesn't check the status; call `r.raise_for_status()` before `.json()` so a 500 error page doesn't crash with a confusing JSON error.", "Network errors (`requests.RequestException`) aren't handled; decide whether to retry or surface them."]),
 ("javascript", "const el = document.getElementById('out');\nel.innerHTML = '<p>' + userInput + '</p>';", ["**XSS risk:** inserting untrusted text with `innerHTML` lets an attacker inject scripts. Use `el.textContent = userInput` or build elements with `createElement`, or sanitise the HTML if you must allow markup."]),
 ("python", "class User:\n    def __init__(self, name, email):\n        self.name = name\n        self.email = email\n\n    def save(self):\n        conn = sqlite3.connect('app.db')\n        conn.execute('INSERT INTO users VALUES (?, ?)', (self.name, self.email))\n        conn.commit()", ["The model **opens a new database connection** on each save and never closes it. Pass in a connection or use a context manager.", "Persistence is mixed into the data class, which makes it harder to test. Consider a separate repository.", "`INSERT INTO users VALUES (?, ?)` depends on column order; name the columns."]),
 ("java", "public int divide(int a, int b) {\n    try {\n        return a / b;\n    } catch (Exception e) {\n        return 0;\n    }\n}", ["Catching `Exception` and returning 0 **hides a real error**: dividing by zero would look like a legitimate result of 0. Let `ArithmeticException` propagate or validate `b != 0` and throw a meaningful exception.", "If you catch, log it; silent failures are very hard to debug."]),
 ("python", "def process(data):\n    result = []\n    for i in range(len(data)):\n        for j in range(len(data)):\n            if data[i] == data[j] and i != j:\n                result.append(data[i])\n    return result", ["This is **O(n²)** and returns each duplicate multiple times. A `Counter` or a set finds duplicates in O(n).", "Iterate over values with `for x in data` where you don't need indexes.", "The function name `process` says nothing; name it for what it does (`find_duplicates`)."]),
 ("javascript", "useEffect(() => {\n  fetch('/api/items').then(r => r.json()).then(setItems);\n});", ["**Missing dependency array:** the effect runs after every render, and since it updates state it triggers another render, creating an infinite request loop. Add `[]` (or the real dependencies).", "No error handling or loading state, and no cleanup for a response that arrives after unmount.", "Consider a data-fetching library (TanStack Query) rather than raw effects."]),
 ("python", "password = 'hunter2'\nAPI_KEY = 'sk-live-abc123'\n\ndef connect():\n    return client.login(password, API_KEY)", ["**Hard-coded secrets.** Anyone with repository access has them, and git history keeps them forever. Read them from environment variables or a secrets manager, and rotate this key now since it was committed.", "Add the real config file to `.gitignore` and commit only an example with placeholders."]),
]
for lang, code, points in REV:
    fence = lang
    qs = ["Can you review this code?", "Please do a code review of this snippet.", "What would you flag in a code review of this?"]
    ans = "Here's what I'd flag, most important first:\n\n" + "\n".join(f"{i+1}. {p}" for i, p in enumerate(points)) + "\n\nI haven't run this in your environment, so check the behaviour of the fixes against your own setup and tests."
    out.append(mk(CAT, "code_review", [("user", f"{R.choice(qs)}\n\n```{fence}\n{code}\n```"), ("assistant", ans)], "rev_" + code[:25], "hard", "authored", pick(R, SYS_GENERAL)))

# ---------------- multi-turn programming follow-ups (computed from the Python bank) ----------------
import textwrap
from py_bank2 import F2
import importlib
bank_mod = importlib.import_module("gen_prog_py") if False else None  # avoid re-running; rebuild bank directly
src = open(Path(__file__).parent / "gen_prog_py.py", encoding="utf-8").read()
ns = {}
head = src.split("# ----- run every function against its tests -----")[0]
head = head.replace("from lib import *", "from lib import *")
exec(compile(head, "gen_prog_py_head", "exec"), ns)
bank = ns["F"]
FOLLOW = ["What would it return for `{call}`?", "Can you tell me what `{call}` gives, and why?", "And if I call `{call}`?"]
nm = 0
for name, desc, code, tests, expl in bank:
    if len(tests) < 2 or "lambda" in tests[1][0] or code.lstrip().startswith("class"):
        continue
    env = {}
    exec(code, env)
    call, exp = tests[1]
    got = eval(call, env)
    if got != exp:
        continue
    nm += 1
    first = [("user", f"Write a Python function to {desc}."), ("assistant", f"```python\n{code}\n```\n\n{expl}")]
    second = [("user", R.choice(FOLLOW).format(call=call)), ("assistant", f"`{call}` returns `{got!r}`.\n\nThat follows from the logic above: {expl[0].lower() + expl[1:]}")]
    out.append(mk(CAT, "multi_turn_code", first + second, "mtc_" + name, "medium", "verified", pick(R, SYS_GENERAL), {"turns": 2}))
    # third turn: ask for tests
    if nm % 2 == 0:
        tc = "\n".join(f"    assert {c} == {e!r}" for c, e in tests[:3])
        out[-1]["messages"] += [{"role": "user", "content": "Thanks. Can you give me a quick test for it?"},
                                {"role": "assistant", "content": f"Sure, a minimal pytest file (assuming the function is importable):\n\n```python\ndef test_{name}():\n{tc}\n```\n\nThese are the same cases I just walked through, so they should pass."}]
        out[-1]["meta"]["turns"] = 3
print("multi-turn code dialogs:", nm)

save("prog_extra", out)
