"""Category 4c: debugging. Broken snippets are EXECUTED to capture the real error text; fixes are executed to confirm they work."""
import traceback
from lib import *

CAT = "programming"
R = seeded("prog_debug")
out = []

# (broken, fixed, why)   -- broken must raise, fixed must run cleanly
ERR = [
 ("nums = [1, 2, 3]\nprint(nums[3])", "nums = [1, 2, 3]\nprint(nums[2])", "Lists are zero-indexed, so a list of 3 items has indexes 0, 1 and 2. Index 3 is one past the end. Use `nums[-1]` for the last item or check `len(nums)` first."),
 ("age = '25'\nprint(age + 5)", "age = '25'\nprint(int(age) + 5)", "You're adding a string to an integer. Python won't guess which you meant, so convert explicitly with `int(age)` (or use an f-string if you wanted text)."),
 ("d = {'a': 1}\nprint(d['b'])", "d = {'a': 1}\nprint(d.get('b', 0))", "The key `'b'` isn't in the dictionary. Use `d.get('b', default)` when a key may be missing, or check `'b' in d` first."),
 ("def greet(name):\n    return 'Hi ' + name\n\nprint(greet())", "def greet(name='friend'):\n    return 'Hi ' + name\n\nprint(greet())", "The function requires an argument but was called with none. Pass one, or give the parameter a default value."),
 ("print(undefined_name)", "undefined_name = 'hello'\nprint(undefined_name)", "The name is used before anything defines it. Check for typos and make sure the assignment runs before the use."),
 ("x = int('12.5')", "x = int(float('12.5'))", "`int()` can't parse a string with a decimal point. Convert through `float` first (which truncates), or use `round`."),
 ("import math\nprint(math.sqrt(-4))", "import cmath\nprint(cmath.sqrt(-4))", "The real-valued `math.sqrt` rejects negative numbers. For complex results use `cmath.sqrt`, or validate the input is non-negative."),
 ("items = (1, 2, 3)\nitems[0] = 9", "items = [1, 2, 3]\nitems[0] = 9", "Tuples are immutable. If you need to change items, use a list (or build a new tuple)."),
 ("s = 'hello'\ns[0] = 'H'", "s = 'hello'\ns = 'H' + s[1:]", "Strings are immutable in Python. Build a new string instead of assigning to a character."),
 ("for i in range(3):\nprint(i)", "for i in range(3):\n    print(i)", "Python uses indentation to mark blocks. The body of the loop must be indented."),
 ("nums = [1, 2, 3]\nnums.add(4)", "nums = [1, 2, 3]\nnums.append(4)", "Lists have `append`, not `add`; `add` belongs to sets."),
 ("value = None\nprint(value.upper())", "value = None\nprint((value or '').upper())", "`value` is None, and None has no `.upper()`. Find out why it's None (a function that returned nothing is common) and handle that case explicitly."),
 ("def f():\n    return x\n    x = 1\nf()", "def f():\n    x = 1\n    return x\nf()", "The `return` ends the function before `x` is assigned, and in any case a local name has to be assigned before use."),
 ("count = 0\ndef inc():\n    count += 1\ninc()", "count = 0\ndef inc():\n    global count\n    count += 1\ninc()", "Assigning to `count` inside the function makes it a local variable, which doesn't exist yet when `+=` reads it. Declare `global count` (or better, return the new value instead of mutating a global)."),
 ("import json\njson.loads(\"{'a': 1}\")", "import json\njson.loads('{\"a\": 1}')", "JSON requires double quotes. Single-quoted keys make it invalid JSON (it's a Python-style dict literal)."),
 ("a, b = [1, 2, 3]", "a, b, c = [1, 2, 3]", "Unpacking needs the same number of names as items. Use `a, *rest = items` if the length varies."),
 ("print(int('abc'))", "text = 'abc'\nprint(int(text) if text.isdigit() else None)", "The string isn't a valid integer. Validate the input first or wrap the conversion in `try/except ValueError`."),
 ("import os\nprint(os.listdir('/definitely/not/here'))", "import os\npath = '/definitely/not/here'\nprint(os.listdir(path) if os.path.isdir(path) else [])", "The directory doesn't exist. Check with `os.path.isdir` first, or catch `FileNotFoundError`."),
 ("for x in 5:\n    print(x)", "for x in range(5):\n    print(x)", "An integer isn't iterable. Use `range(5)` to loop five times."),
 ("class A:\n    def hello():\n        return 'hi'\nA().hello()", "class A:\n    def hello(self):\n        return 'hi'\nA().hello()", "Instance methods receive the instance as the first argument automatically, so they need a `self` parameter."),
 ("nums = [3, 1, 2]\nprint(nums.sorted())", "nums = [3, 1, 2]\nprint(sorted(nums))", "`sorted()` is a built-in function that returns a new list. The list method is `.sort()`, which sorts in place and returns None."),
 ("data = {'a': 1}\nfor k, v in data:\n    print(k, v)", "data = {'a': 1}\nfor k, v in data.items():\n    print(k, v)", "Iterating a dict gives only its keys. Use `.items()` to get (key, value) pairs."),
 ("import json\nprint(json.dumps({1, 2, 3}))", "import json\nprint(json.dumps(sorted({1, 2, 3})))", "JSON has no set type. Convert the set to a list first."),
 ("def fact(n):\n    return n * fact(n - 1)\nfact(5)", "def fact(n):\n    return 1 if n <= 1 else n * fact(n - 1)\nfact(5)", "There's no base case, so the recursion never stops until Python raises `RecursionError`. Add a stopping condition."),
 ("from datetime import datetime\ndatetime.strptime('2024-13-45', '%Y-%m-%d')", "from datetime import datetime\ndatetime.strptime('2024-12-25', '%Y-%m-%d')", "The string isn't a real date: there is no month 13 or day 45. Validate user input or catch `ValueError`."),
 ("x = [1, 2, 3]\ny = x + 4", "x = [1, 2, 3]\ny = x + [4]", "You can only concatenate a list with another list. Wrap the value in a list, or use `append`."),
 ("print('Total: ' + 42)", "print('Total: ' + str(42))", "A string and an integer can't be joined with `+`. Convert the number with `str()` or use an f-string."),
 ("import statistics\nstatistics.mean([])", "import statistics\nvalues = []\nprint(statistics.mean(values) if values else 0)", "The mean of no values is undefined, so `statistics.mean` raises. Guard against an empty list."),
 ("with open('missing_file_123.txt') as f:\n    print(f.read())", "import os\nname = 'missing_file_123.txt'\nif os.path.exists(name):\n    with open(name) as f:\n        print(f.read())", "The file doesn't exist in the current working directory. Print `os.getcwd()` to confirm where Python is looking, and use an absolute path if needed."),
 ("def add(a, b):\n    return a + b\nadd(1, 2, 3)", "def add(a, b):\n    return a + b\nadd(1, 2)", "The function takes exactly two arguments but three were passed."),
 ("lst = [1, 2, 3]\nfor x in lst:\n    if x == 2:\n        lst.remove(x)\n    print(x)\nprint(lst[5])", "lst = [1, 2, 3]\nlst = [x for x in lst if x != 2]\nprint(lst[-1])", "Two things: removing items from a list while looping over it skips elements, and index 5 doesn't exist in a short list. Build a new filtered list and index within range."),
 ("import random\nprint(random.choice([]))", "import random\nitems = []\nprint(random.choice(items) if items else None)", "`random.choice` can't pick from an empty sequence. Check that the list isn't empty."),
 ("print(max([]))", "print(max([], default=None))", "`max` of an empty sequence is undefined. Pass `default=` or check the list first."),
 ("def f(x=[]):\n    x.append(1)\n    return x\nassert f() == [1] and f() == [1]", "def f(x=None):\n    x = [] if x is None else x\n    x.append(1)\n    return x\nassert f() == [1] and f() == [1]", "A mutable default argument is created once and shared between calls, so the second call sees the first call's data. Use `None` as the default and create the list inside."),
 ("a = 10\nb = 0\nprint(a // b)", "a = 10\nb = 0\nprint(a // b if b else None)", "Dividing by zero raises `ZeroDivisionError`. Decide what the result should be when the divisor is zero and handle that case."),
]

# ----- logic bugs: (buggy, fixed, call, expected, why) -----
LOGIC = [
 ("def average(nums):\n    return sum(nums) / len(nums) + 1", "def average(nums):\n    return sum(nums) / len(nums)", "average([2, 4, 6])", 4.0, "An extra `+ 1` was added to the result. The average is just the sum divided by the count."),
 ("def is_even(n):\n    return n % 2 == 1", "def is_even(n):\n    return n % 2 == 0", "is_even(4)", True, "The test is inverted: even numbers leave a remainder of 0, not 1."),
 ("def count_up_to(n):\n    return list(range(1, n))", "def count_up_to(n):\n    return list(range(1, n + 1))", "count_up_to(3)", [1, 2, 3], "`range` excludes its end, so it stops one short. Use `n + 1` to include `n`. This is a classic off-by-one error."),
 ("def last_item(items):\n    return items[len(items)]", "def last_item(items):\n    return items[len(items) - 1]", "last_item([4, 5, 6])", 6, "The last valid index is `len(items) - 1`, so `items[len(items)]` is out of range. `items[-1]` also works."),
 ("def max_of(nums):\n    best = 0\n    for x in nums:\n        if x > best:\n            best = x\n    return best", "def max_of(nums):\n    best = nums[0]\n    for x in nums:\n        if x > best:\n            best = x\n    return best", "max_of([-5, -2, -9])", -2, "Starting `best` at 0 is wrong when every number is negative. Start from the first element instead."),
 ("def reverse_words(s):\n    return ' '.join(s.split()).reverse()", "def reverse_words(s):\n    return ' '.join(reversed(s.split()))", "reverse_words('a b c')", "c b a", "`str` has no `.reverse()`, and even for lists it reverses in place and returns None. Use `reversed()` on the list of words and join them."),
 ("def remove_evens(nums):\n    for n in nums:\n        if n % 2 == 0:\n            nums.remove(n)\n    return nums", "def remove_evens(nums):\n    return [n for n in nums if n % 2 != 0]", "remove_evens([2, 4, 6, 7])", [7], "Removing from a list while iterating over it makes the iterator skip items. Build a new list with a comprehension."),
 ("def titlecase(s):\n    return s.title().strip", "def titlecase(s):\n    return s.title().strip()", "titlecase(' hello world ')", "Hello World", "`.strip` without parentheses returns the method itself rather than calling it."),
 ("def total_price(prices, tax):\n    return sum(prices) * tax", "def total_price(prices, tax):\n    return sum(prices) * (1 + tax)", "total_price([10, 20], 0.1)", 33.0, "Multiplying by the tax rate gives only the tax amount, not the price including tax. Multiply by `1 + tax`."),
 ("def contains_vowel(s):\n    for ch in s:\n        if ch in 'aeiou':\n            return True\n        else:\n            return False", "def contains_vowel(s):\n    return any(ch in 'aeiou' for ch in s.lower())", "contains_vowel('sky is')", True, "The `return False` inside the loop runs on the first non-vowel character, so only the first letter is ever checked. Return False only after the whole loop has finished."),
 ("def median(nums):\n    nums = sorted(nums)\n    return nums[len(nums) / 2]", "def median(nums):\n    nums = sorted(nums)\n    n = len(nums)\n    mid = n // 2\n    return nums[mid] if n % 2 else (nums[mid - 1] + nums[mid]) / 2", "median([3, 1, 2, 4])", 2.5, "`/` produces a float, which can't be a list index, and even `//` ignores the even-length case where the median is the mean of the two middle values."),
 ("def swap(a, b):\n    a = b\n    b = a\n    return a, b", "def swap(a, b):\n    return b, a", "swap(1, 2)", (2, 1), "After `a = b` the original `a` is lost, so both end up equal. Use tuple assignment or return `b, a`."),
 ("def sum_to(n):\n    total = 0\n    for i in range(n):\n        total += i\n    return total", "def sum_to(n):\n    total = 0\n    for i in range(1, n + 1):\n        total += i\n    return total", "sum_to(4)", 10, "The loop runs from 0 to n-1, so n itself is never added. Count from 1 up to and including n."),
 ("def merge_dicts(a, b):\n    return a.update(b)", "def merge_dicts(a, b):\n    merged = dict(a)\n    merged.update(b)\n    return merged", "merge_dicts({'x': 1}, {'y': 2})", {"x": 1, "y": 2}, "`dict.update` modifies in place and returns None, so the function always returned None (and mutated its input). Copy first, then update the copy."),
 ("def word_lengths(words):\n    return {w: len(w) for w in words}\n", "def word_lengths(words):\n    return {w: len(w) for w in words}", "word_lengths(['hi', 'sun'])", {"hi": 2, "sun": 3}, "That one actually works. If you're seeing a bug, it's likely in how it's called (for example duplicate words collapse into a single key)."),
 ("def percent(part, whole):\n    return part / whole * 100 if whole else 0", "def percent(part, whole):\n    return part / whole * 100 if whole else 0", "percent(1, 4)", 25.0, "This version is correct. The `if whole else 0` guard already prevents division by zero."),
]

KINDS_ASK = [
 "I'm getting an error when I run this. What's wrong?\n\n```python\n{c}\n```\n\n```\n{e}\n```",
 "My code crashes with this traceback. Can you help?\n\n```python\n{c}\n```\n\n```\n{e}\n```",
 "Why do I get `{short}`?\n\n```python\n{c}\n```",
 "python error help:\n\n{e}\n\nmy code:\n```python\n{c}\n```",
]

n_err = 0
for broken, fixed, why in ERR:
    try:
        exec(broken, {})
        print("NOT RAISING (skipped):", broken[:50].replace("\n", " | "))
        continue
    except BaseException as e:
        tb = traceback.format_exception_only(type(e), e)[-1].strip()
        short = tb
        fake_tb = f"Traceback (most recent call last):\n  File \"main.py\", line {max(1, broken.count(chr(10)) + 1)}, in <module>\n{tb}"
    try:
        exec(fixed, {})
    except BaseException as e:
        print("FIX FAILS:", fixed[:50], e)
        continue
    n_err += 1
    q1 = R.choice(KINDS_ASK).format(c=broken, e=tb if R.random() < 0.5 else fake_tb, short=short)
    out.append(mk(CAT, "debug_error", [("user", q1), ("assistant", f"The error is `{short}`.\n\n{why}\n\nHere's a corrected version:\n\n```python\n{fixed}\n```")], "err_" + broken[:24], "medium", "verified", pick(R, SYS_GENERAL)))
    out.append(mk(CAT, "debug_fix", [("user", f"Fix this Python code and tell me what you changed.\n\n```python\n{broken}\n```"), ("assistant", f"Running it raises `{short}`.\n\n```python\n{fixed}\n```\n\nWhat changed: {why}")], "err_" + broken[:24], "medium", "verified", pick(R, SYS_GENERAL)))
print("error snippets verified:", n_err)

n_log = 0
for buggy, fixed, call, exp, why in LOGIC:
    e1, e2 = {}, {}
    exec(buggy, e1)
    exec(fixed, e2)
    try:
        got_bad = eval(call, e1)
    except BaseException as e:
        got_bad = f"<raises {type(e).__name__}>"
    if "0x" in repr(got_bad):
        class _Fixed:          # canonical repr: memory addresses are noise in training text and break reproducibility
            def __init__(self, t):
                self.t = t

            def __repr__(self):
                return self.t

            def __eq__(self, o):
                return False
        got_bad = _Fixed(__import__("re").sub(r"0x[0-9a-fA-F]+", "0x7f2c41a8e3d0", repr(got_bad)))
    got_ok = eval(call, e2)
    if got_ok != exp:
        print("FIX WRONG", call, got_ok)
        continue
    if got_bad == exp:
        # the 'buggy' code is already right: use as a 'no bug here' honesty example
        out.append(mk(CAT, "debug_no_bug", [("user", f"This returns the wrong answer, I think. Can you find the bug?\n\n```python\n{buggy}\n```\n\nI called `{call}`."),
                    ("assistant", f"Working through `{call}`, this function gives `{got_bad!r}`, which is the expected result, so I don't see a bug in it as written. {why}\n\nIf you're seeing a different result, could you share the exact input and the output you got? That would show where the difference comes from.")],
                    "logic_" + buggy[:24], "hard", "verified", pick(R, SYS_GENERAL)))
        n_log += 1
        continue
    n_log += 1
    out.append(mk(CAT, "debug_logic", [("user", f"This function gives the wrong answer. `{call}` should return `{exp!r}`.\n\n```python\n{buggy}\n```"),
                ("assistant", f"Tracing `{call}` through your version gives `{got_bad!r}` instead of `{exp!r}`.\n\n{why}\n\n```python\n{fixed}\n```\n\nWith this change `{call}` returns `{got_ok!r}`.")], "logic_" + buggy[:24], "medium", "verified", pick(R, SYS_GENERAL)))
    out.append(mk(CAT, "debug_logic", [("user", f"Why is my function wrong?\n\n```python\n{buggy}\n```\n\n{call} -> {got_bad!r}"),
                ("assistant", f"{why}\n\nCorrected:\n\n```python\n{fixed}\n```")], "logic_" + buggy[:24], "medium", "verified", pick(R, SYS_GENERAL)))
print("logic bugs verified:", n_log)

save("prog_debug", out)
