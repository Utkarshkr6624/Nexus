"""Multi-turn programming dialogs; every code block is executed."""
from gen21_algo_lib import *

D("dialog_debug", "medium", [
    ("My function to find the most common word returns the wrong thing:\n\n{code0}\n\nIt prints `{out0}` but I expected `the`.",
     "Two things are going on. `max(counts)` compares the dictionary **keys** alphabetically, so it returns the alphabetically last word, not the one with the highest count. To pick by count, tell `max` what to compare: `max(counts, key=counts.get)`. Want me to show the fixed function?"),
    ("yes please, and what if two words tie?",
     "```python\ndef most_common(text):\n    counts = {}\n    for w in text.lower().split():\n        counts[w] = counts.get(w, 0) + 1\n    return max(counts, key=lambda w: (counts[w], -ord(w[0])))\n\nprint(most_common(\"the cat and the hat and the bat\"))\n```\n\nFor ties, `max` returns the first maximal item it meets in iteration order, which for a dict is insertion order. If you need a different rule (for example alphabetical), put it in the key as a tuple. `collections.Counter(words).most_common(1)` does the counting for you."),
], [("python", 'def most_common(text):\n    counts = {}\n    for w in text.lower().split():\n        counts[w] = counts.get(w, 0) + 1\n    return max(counts)\n\nprint(most_common("the cat and the hat and the bat"))')])

D("dialog_optimise", "hard", [
    ("This works but is slow on 100k items:\n\n{code0}\n\nHow can I speed it up?",
     "The slow part is `x not in result` on a list: each check scans the list, so the whole function is O(n^2). Keep a set of what you've seen for O(1) lookups while still building the list to preserve order."),
    ("Show me, and does the order stay the same?",
     "{code1}\n\nYes: the list keeps first-seen order, and the set is used only for membership. Both versions print `{out1}`, so the behaviour is unchanged while the cost drops to O(n). In Python 3.7+, `list(dict.fromkeys(items))` is a one-line equivalent because dicts preserve insertion order."),
], [("python", 'def dedupe(items):\n    result = []\n    for x in items:\n        if x not in result:\n            result.append(x)\n    return result\n\nprint(dedupe([3, 1, 3, 2, 1]))'),
    ("python", 'def dedupe(items):\n    seen, result = set(), []\n    for x in items:\n        if x not in seen:\n            seen.add(x)\n            result.append(x)\n    return result\n\nprint(dedupe([3, 1, 3, 2, 1]))')])

D("dialog_design", "hard", [
    ("I need to keep the last 3 search queries for each user. What data structure?",
     "A small bounded queue per user: a dictionary from user id to a `deque(maxlen=3)`. Appending beyond three automatically drops the oldest item, and every operation is O(1). Do you need duplicates removed, or the most recent first?"),
    ("most recent first, and no duplicates",
     "Remove an existing copy before appending, then read the deque in reverse:\n\n{code0}\n\nOutput: `{out0}`. The `remove` is O(k) with k at most 3, which is negligible. If k were large you would use an `OrderedDict` with `move_to_end` instead."),
], [("python", 'from collections import defaultdict, deque\n\nrecent = defaultdict(lambda: deque(maxlen=3))\n\ndef record(user, q):\n    d = recent[user]\n    if q in d:\n        d.remove(q)\n    d.append(q)\n\nfor q in ["a", "b", "c", "b", "d"]:\n    record("ana", q)\nprint(list(reversed(recent["ana"])))')])

D("dialog_c_crash", "medium", [
    ("My C program computes the wrong average:\n\n{code0}\n\nIt prints `{out0}` but the true average of 1, 2 and 2 is 1.67.",
     "Integer division. In C, when both operands are `int`, `/` discards the fractional part: 5 / 3 is 1. Assigning the result to a `double` afterwards doesn't bring the fraction back, because the division was already done in integers. Convert one operand **before** dividing."),
    ("how exactly do I fix it",
     "{code1}\n\nOutput: `{out1}`. Casting `sum` to `double` makes the division a floating-point one (the other operand is converted automatically). Writing `sum / (double)n` or `1.0 * sum / n` works too. Do this conversion before the division, not on the result."),
], [("c", '#include <stdio.h>\n\nint main(void) {\n    int sum = 1 + 2 + 2, n = 3;\n    double avg = sum / n;\n    printf("%.2f\\n", avg);\n    return 0;\n}'),
    ("c", '#include <stdio.h>\n\nint main(void) {\n    int sum = 1 + 2 + 2, n = 3;\n    double avg = (double)sum / n;\n    printf("%.2f\\n", avg);\n    return 0;\n}')])

D("dialog_refactor", "medium", [
    ("Is this Java method too long? It validates, calculates and prints:\n\n{code0}",
     "It mixes three responsibilities, which makes each hard to test or reuse. A clean split is one method that validates, one that calculates, and the caller decides how to present the result. Want me to rewrite it?"),
    ("yes",
     "{code1}\n\nOutput: `{out1}`. Now `total` is a pure function you can unit-test with plain inputs, validation is reusable, and printing lives at the edge of the program. The behaviour is the same as before for valid input."),
], [("java", 'public class Main {\n    static void process(int qty, double price) {\n        if (qty <= 0 || price < 0) {\n            System.out.println("invalid");\n            return;\n        }\n        double total = qty * price;\n        if (qty >= 10) total *= 0.9;\n        System.out.println("Total: " + total);\n    }\n\n    public static void main(String[] args) {\n        process(10, 2.5);\n    }\n}'),
    ("java", 'public class Main {\n    static boolean valid(int qty, double price) {\n        return qty > 0 && price >= 0;\n    }\n\n    static double total(int qty, double price) {\n        double t = qty * price;\n        return qty >= 10 ? t * 0.9 : t;\n    }\n\n    public static void main(String[] args) {\n        if (valid(10, 2.5)) System.out.println("Total: " + total(10, 2.5));\n        else System.out.println("invalid");\n    }\n}')])

D("dialog_complexity", "medium", [
    ("What's the complexity of this function?\n\n{code0}",
     "The loop runs n times, and inside it `s[:i]` slices a string of up to n characters, and `in` scans another one, so each iteration costs O(n) and the total is O(n^2), not O(n). Hidden costs like slicing, `in` on lists and string concatenation are the usual surprises."),
    ("How would I make it linear?",
     "Track the characters you have already seen in a set, which makes each membership test O(1):\n\n{code1}\n\nBoth return `{out1}` for this input, and the second does a constant amount of work per character, so it is O(n) time and O(k) space for k distinct characters."),
], [("python", 'def first_repeat(s):\n    for i in range(len(s)):\n        if s[i] in s[:i]:\n            return s[i]\n    return None\n\nprint(first_repeat("abcdbe"))'),
    ("python", 'def first_repeat(s):\n    seen = set()\n    for ch in s:\n        if ch in seen:\n            return ch\n        seen.add(ch)\n    return None\n\nprint(first_repeat("abcdbe"))')])

D("dialog_cpp_ownership", "hard", [
    ("Why does copying this C++ struct leave both objects pointing at the same text?\n\n{code0}",
     "The default copy constructor copies each member, and the member here is just a pointer, so `a.p` and `b.p` hold the same address (the program prints `{out0}`, meaning equal). If the struct owned that memory, both copies would try to free it: a double free. This example uses a string literal, so nothing is freed, but the design flaw is the same. The rule of three/five says that if you manage a resource you must define (or delete) the copy constructor, copy assignment and destructor."),
    ("so what's the modern fix?",
     "Don't manage raw memory at all. Use `std::string` or `std::vector`, which copy deeply and free themselves:\n\n{code1}\n\nOutput: `{out1}`. Each object owns its own data, so the copy is independent and there is nothing to free by hand (the rule of zero)."),
], [("cpp", '#include <iostream>\n\nstruct Name {\n    const char *p;\n    Name(const char *s) : p(s) {}\n};\n\nint main() {\n    Name a("Ana");\n    Name b = a;                 // copies the pointer, not the text\n    std::cout << (a.p == b.p) << "\\n";\n}'),
    ("cpp", '#include <iostream>\n#include <string>\n\nstruct Name {\n    std::string s;\n};\n\nint main() {\n    Name a{"Ana"};\n    Name b = a;\n    b.s += "!";\n    std::cout << a.s << " " << b.s << "\\n";\n}')])

D("dialog_algorithm_pick", "medium", [
    ("I have to find all pairs of cities whose distance is under 100km, from 50 cities. Which algorithm?",
     "With only 50 cities, simply check every pair: 50 x 49 / 2 = 1,225 comparisons, which is instant. Choosing a more elaborate structure would add complexity for no benefit. Does the number of cities grow?"),
    ("yes, up to a million",
     "Then all-pairs (about 5 x 10^11 checks) is too slow, and you need spatial indexing: a grid (hash points into cells and compare only neighbouring cells) or a k-d tree. A grid keeps it simple:\n\n{code0}\n\nOutput: `{out0}`. Each point is compared only with points in its own and adjacent cells, so work scales with the number of nearby points rather than with n^2."),
], [("python", 'from collections import defaultdict\nfrom math import floor, hypot\n\npts = [(0, 0), (5, 5), (50, 50), (52, 51), (200, 200)]\nR = 10\ncells = defaultdict(list)\nfor i, (x, y) in enumerate(pts):\n    cells[(floor(x / R), floor(y / R))].append(i)\n\npairs = set()\nfor (cx, cy), ids in cells.items():\n    for dx in (-1, 0, 1):\n        for dy in (-1, 0, 1):\n            for j in cells.get((cx + dx, cy + dy), []):\n                for i in ids:\n                    if i < j and hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1]) <= R:\n                        pairs.add((i, j))\nprint(sorted(pairs))')])

D("dialog_js_bug", "medium", [
    ("My JS sorts numbers wrong:\n\n{code0}\n\nI expected 1, 2, 10, 25.",
     "`Array.prototype.sort` with no comparator converts items to **strings** and sorts them alphabetically, so \"10\" comes before \"2\". Pass a numeric comparator: `(a, b) => a - b`."),
    ("thanks. is a - b always safe?",
     "For ordinary finite numbers, yes. It can misbehave with `NaN` or `Infinity` (for example `Infinity - Infinity` is `NaN`), and with huge values mixed with BigInt it throws. For very large or special values, compare explicitly: `(a, b) => (a < b ? -1 : a > b ? 1 : 0)`. Also note `sort` mutates the array in place; use `toSorted` or copy first if you need the original. Result with the comparator: `{out1}`."),
], [("javascript", 'const nums = [25, 1, 10, 2];\nnums.sort();\nconsole.log(nums);'),
    ("javascript", 'const nums = [25, 1, 10, 2];\nnums.sort((a, b) => a - b);\nconsole.log(nums);')])
