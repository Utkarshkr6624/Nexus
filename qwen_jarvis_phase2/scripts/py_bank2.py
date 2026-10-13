"""Second batch of verified Python functions (consumed by gen_prog_py.py). Same tuple shape: (name, desc, code, tests, explanation)."""
import textwrap

F2 = []


def g(name, desc, code, tests, expl):
    F2.append((name, desc, textwrap.dedent(code).strip("\n"), tests, expl))


g("is_valid_password", "check a password has at least 8 characters, one digit, one uppercase and one lowercase letter", """
def is_valid_password(pw):
    return (len(pw) >= 8 and any(c.isdigit() for c in pw)
            and any(c.isupper() for c in pw) and any(c.islower() for c in pw))
""", [("is_valid_password('Abcdef12')", True), ("is_valid_password('abcdef12')", False), ("is_valid_password('Ab1')", False)], "Each rule is a separate check combined with `and`, so it is easy to add another rule. For real systems prefer length and breached-password checks over composition rules.")
g("sum_nested", "sum all numbers inside a nested list structure", """
def sum_nested(data):
    total = 0
    for item in data:
        total += sum_nested(item) if isinstance(item, list) else item
    return total
""", [("sum_nested([1,[2,3],[[4]],5])", 15), ("sum_nested([])", 0)], "It recurses into sub-lists and adds plain numbers, so the nesting depth doesn't matter.")
g("find_peak", "return the index of any peak element (greater than its neighbours) in a list", """
def find_peak(nums):
    lo, hi = 0, len(nums) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if nums[mid] < nums[mid + 1]:
            lo = mid + 1
        else:
            hi = mid
    return lo
""", [("find_peak([1,3,5,4,2])", 2)], "Binary search works because if the next element is higher, a peak must exist to the right; otherwise one exists at or to the left of mid. This runs in O(log n).")
g("hamming_distance", "count the positions at which two equal-length strings differ", """
def hamming_distance(a, b):
    if len(a) != len(b):
        raise ValueError("strings must have equal length")
    return sum(x != y for x, y in zip(a, b))
""", [("hamming_distance('karolin', 'kathrin')", 3), ("hamming_distance('abc', 'abc')", 0)], "`zip` pairs the characters; each mismatch counts as `True`, which sums as 1.")
g("count_words_in_file_text", "return the 3 most common words in a block of text", """
from collections import Counter
import re

def top_words(text, n=3):
    words = re.findall(r"[a-z']+", text.lower())
    return Counter(words).most_common(n)
""", [("top_words('a b a c b a')", [("a", 3), ("b", 2), ("c", 1)])], "`Counter.most_common` returns (word, count) pairs sorted by count.")
g("pairwise_diff", "return the differences between consecutive items of a list", """
def pairwise_diff(nums):
    return [b - a for a, b in zip(nums, nums[1:])]
""", [("pairwise_diff([1, 4, 9, 16])", [3, 5, 7]), ("pairwise_diff([5])", [])], "Zipping a list with itself shifted by one gives each neighbouring pair.")
g("chunk_string", "split a string into chunks of n characters", """
def chunk_string(s, n):
    return [s[i:i + n] for i in range(0, len(s), n)]
""", [("chunk_string('abcdefg', 3)", ["abc", "def", "g"])], "Slicing in steps of `n` gives the pieces, with a shorter final one if the length isn't a multiple of n.")
g("is_rotation", "check whether one string is a rotation of another", """
def is_rotation(a, b):
    return len(a) == len(b) and b in a + a
""", [("is_rotation('waterbottle', 'erbottlewat')", True), ("is_rotation('abc', 'acb')", False)], "If b is a rotation of a, it must appear inside a concatenated with itself. The length check avoids false positives.")
g("sort_by_frequency", "sort items by how often they appear, most frequent first, ties by value", """
from collections import Counter

def sort_by_frequency(items):
    counts = Counter(items)
    return sorted(items, key=lambda x: (-counts[x], x))
""", [("sort_by_frequency([4, 5, 6, 5, 4, 4])", [4, 4, 4, 5, 5, 6])], "The sort key is a tuple: negative count (so bigger counts come first), then the value itself to break ties deterministically.")
g("normalize_whitespace", "collapse runs of whitespace to single spaces and trim the ends", """
import re

def normalize_whitespace(s):
    return re.sub(r"\\s+", " ", s).strip()
""", [("normalize_whitespace('  a   b\\n\\tc  ')", "a b c")], "The regex `\\s+` matches any run of whitespace, including tabs and newlines, and replaces it with one space.")
g("zip_dict", "build a dictionary from a list of keys and a list of values, ignoring extra items", """
def zip_dict(keys, values):
    return dict(zip(keys, values))
""", [("zip_dict(['a','b','c'], [1,2])", {"a": 1, "b": 2})], "`zip` stops at the shorter list, and `dict` turns the pairs into entries.")
g("digits_of", "return the digits of a non-negative integer as a list", """
def digits_of(n):
    return [int(ch) for ch in str(n)]
""", [("digits_of(9047)", [9, 0, 4, 7]), ("digits_of(0)", [0])], "Converting to a string and back to ints is the simplest approach when you don't need to avoid string conversion.")
g("is_armstrong", "check whether a number equals the sum of its digits each raised to the number of digits", """
def is_armstrong(n):
    digits = [int(c) for c in str(n)]
    return n == sum(d ** len(digits) for d in digits)
""", [("is_armstrong(153)", True), ("is_armstrong(154)", False), ("is_armstrong(9474)", True)], "For 153 it computes 1³ + 5³ + 3³ = 153.")
g("collatz_steps", "count the steps for a number to reach 1 in the Collatz sequence", """
def collatz_steps(n):
    if n < 1:
        raise ValueError("n must be positive")
    steps = 0
    while n != 1:
        n = n // 2 if n % 2 == 0 else 3 * n + 1
        steps += 1
    return steps
""", [("collatz_steps(6)", 8), ("collatz_steps(1)", 0), ("collatz_steps(27)", 111)], "Halve even numbers and map odd n to 3n + 1 until reaching 1. It's unproven that this always terminates, but it has for every number checked.")
g("min_coins", "find the minimum number of coins needed to make an amount (dynamic programming)", """
def min_coins(coins, amount):
    INF = amount + 1
    best = [0] + [INF] * amount
    for a in range(1, amount + 1):
        for c in coins:
            if c <= a:
                best[a] = min(best[a], best[a - c] + 1)
    return best[amount] if best[amount] != INF else -1
""", [("min_coins([1, 5, 10, 25], 63)", 6), ("min_coins([2], 3)", -1), ("min_coins([1, 3, 4], 6)", 2)], "`best[a]` is the fewest coins for amount `a`, built from smaller amounts. A greedy choice would fail for coins like [1, 3, 4] (it would pick 4+1+1), which is why DP is needed.")
g("longest_increasing_run", "find the length of the longest run of strictly increasing consecutive numbers", """
def longest_increasing_run(nums):
    if not nums:
        return 0
    best = cur = 1
    for i in range(1, len(nums)):
        cur = cur + 1 if nums[i] > nums[i - 1] else 1
        best = max(best, cur)
    return best
""", [("longest_increasing_run([1, 2, 3, 1, 2, 3, 4, 0])", 4), ("longest_increasing_run([])", 0)], "One pass tracks the current run and the best so far, resetting when the sequence stops increasing.")
g("is_subsequence", "check whether one string is a subsequence of another", """
def is_subsequence(small, big):
    it = iter(big)
    return all(ch in it for ch in small)
""", [("is_subsequence('ace', 'abcde')", True), ("is_subsequence('aec', 'abcde')", False)], "`ch in it` consumes the iterator up to the match, so characters must be found in order.")
g("matrix_multiply", "multiply two matrices stored as lists of lists", """
def matrix_multiply(a, b):
    if len(a[0]) != len(b):
        raise ValueError("incompatible dimensions")
    return [[sum(x * y for x, y in zip(row, col)) for col in zip(*b)] for row in a]
""", [("matrix_multiply([[1,2],[3,4]], [[5,6],[7,8]])", [[19, 22], [43, 50]])], "Each output cell is the dot product of a row of `a` and a column of `b` (`zip(*b)` yields the columns).")
g("to_roman", "convert an integer from 1 to 3999 to a Roman numeral", """
def to_roman(n):
    table = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
             (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    out = []
    for value, symbol in table:
        count, n = divmod(n, value)
        out.append(symbol * count)
    return "".join(out)
""", [("to_roman(1994)", "MCMXCIV"), ("to_roman(58)", "LVIII"), ("to_roman(4)", "IV")], "Greedily take the largest symbol that fits, including the subtractive forms like CM and IV in the table.")
g("flatten_dict", "flatten a nested dictionary into dotted keys", """
def flatten_dict(d, prefix=""):
    flat = {}
    for key, value in d.items():
        full = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            flat.update(flatten_dict(value, full))
        else:
            flat[full] = value
    return flat
""", [("flatten_dict({'a': {'b': 1, 'c': {'d': 2}}, 'e': 3})", {"a.b": 1, "a.c.d": 2, "e": 3})], "It recurses into nested dictionaries and joins the path of keys with dots, which is handy for config files and logging.")
g("dedupe_by_key", "remove dictionaries from a list that repeat a given key, keeping the first", """
def dedupe_by_key(rows, key):
    seen = set()
    out = []
    for row in rows:
        if row[key] not in seen:
            seen.add(row[key])
            out.append(row)
    return out
""", [("dedupe_by_key([{'id':1,'v':'a'},{'id':2,'v':'b'},{'id':1,'v':'c'}], 'id')", [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}])], "A set of seen key values makes the check O(1) per row.")
g("bmi", "calculate BMI from weight in kg and height in metres, rounded to one decimal, with the category", """
def bmi(weight_kg, height_m):
    if weight_kg <= 0 or height_m <= 0:
        raise ValueError("weight and height must be positive")
    value = round(weight_kg / height_m ** 2, 1)
    if value < 18.5:
        label = "underweight"
    elif value < 25:
        label = "normal"
    elif value < 30:
        label = "overweight"
    else:
        label = "obese"
    return value, label
""", [("bmi(70, 1.75)", (22.9, "normal")), ("bmi(50, 1.8)", (15.4, "underweight"))], "BMI = weight / height². The categories are the common adult cut-offs; BMI is a rough screening number, not a diagnosis.")
g("group_consecutive", "group consecutive equal items of a list into sublists", """
from itertools import groupby

def group_consecutive(items):
    return [list(g) for _, g in groupby(items)]
""", [("group_consecutive([1,1,2,2,2,1])", [[1, 1], [2, 2, 2], [1]])], "`groupby` only merges adjacent equal items, which is exactly what is wanted here (unlike a dictionary grouping).")
g("simple_tokenizer", "split an arithmetic expression string into number and operator tokens", """
import re

def simple_tokenizer(expr):
    return re.findall(r"\\d+\\.?\\d*|[-+*/()]", expr)
""", [("simple_tokenizer('12+3.5*(4-1)')", ["12", "+", "3.5", "*", "(", "4", "-", "1", ")"])], "The regex matches either a number (with optional decimals) or a single operator or bracket character. A parser would build on these tokens.")
g("eval_rpn", "evaluate an expression written in Reverse Polish Notation", """
def eval_rpn(tokens):
    stack = []
    ops = {"+": lambda a, b: a + b, "-": lambda a, b: a - b, "*": lambda a, b: a * b, "/": lambda a, b: a / b}
    for t in tokens:
        if t in ops:
            b, a = stack.pop(), stack.pop()
            stack.append(ops[t](a, b))
        else:
            stack.append(float(t))
    return stack[0]
""", [("eval_rpn(['2', '1', '+', '3', '*'])", 9.0), ("eval_rpn(['4', '13', '5', '/', '+'])", 6.6)], "Numbers are pushed on a stack; an operator pops its two operands (right one first), applies itself and pushes the result.")
g("bfs_shortest_path", "find the shortest path length between two nodes in an unweighted graph given as an adjacency dict", """
from collections import deque

def bfs_shortest_path(graph, start, goal):
    queue = deque([(start, 0)])
    seen = {start}
    while queue:
        node, dist = queue.popleft()
        if node == goal:
            return dist
        for nxt in graph.get(node, []):
            if nxt not in seen:
                seen.add(nxt)
                queue.append((nxt, dist + 1))
    return -1
""", [("bfs_shortest_path({'A':['B','C'],'B':['D'],'C':['D'],'D':[]}, 'A', 'D')", 2), ("bfs_shortest_path({'A':[],'B':[]}, 'A', 'B')", -1)], "Breadth-first search explores nodes level by level, so the first time it reaches the goal is via a shortest path. `seen` prevents revisiting nodes.")
g("topological_order", "return a valid topological ordering of tasks given dependencies, or None if there is a cycle", """
from collections import deque

def topological_order(deps):
    nodes = set(deps) | {d for ds in deps.values() for d in ds}
    indegree = {n: 0 for n in nodes}
    children = {n: [] for n in nodes}
    for task, requires in deps.items():
        for r in requires:
            children[r].append(task)
            indegree[task] += 1
    queue = deque(sorted(n for n in nodes if indegree[n] == 0))
    order = []
    while queue:
        n = queue.popleft()
        order.append(n)
        for c in sorted(children[n]):
            indegree[c] -= 1
            if indegree[c] == 0:
                queue.append(c)
    return order if len(order) == len(nodes) else None
""", [("topological_order({'build': ['compile'], 'compile': [], 'test': ['build']})", ["compile", "build", "test"]), ("topological_order({'a': ['b'], 'b': ['a']})", None)], "Kahn's algorithm repeatedly takes tasks with no unmet prerequisites. If some tasks are never freed, they form a cycle. This is how build tools order work.")
g("dijkstra", "compute shortest distances from a start node in a weighted graph with non-negative weights", """
import heapq

def dijkstra(graph, start):
    dist = {start: 0}
    heap = [(0, start)]
    while heap:
        d, node = heapq.heappop(heap)
        if d > dist.get(node, float("inf")):
            continue
        for nxt, w in graph.get(node, []):
            nd = d + w
            if nd < dist.get(nxt, float("inf")):
                dist[nxt] = nd
                heapq.heappush(heap, (nd, nxt))
    return dist
""", [("dijkstra({'A':[('B',1),('C',4)],'B':[('C',2),('D',5)],'C':[('D',1)],'D':[]}, 'A')", {"A": 0, "B": 1, "C": 3, "D": 4})], "A min-heap always expands the closest unfinished node. Stale heap entries are skipped. Negative weights break the algorithm.")
g("quick_sort", "implement quicksort", """
def quick_sort(items):
    if len(items) <= 1:
        return list(items)
    pivot = items[len(items) // 2]
    left = [x for x in items if x < pivot]
    mid = [x for x in items if x == pivot]
    right = [x for x in items if x > pivot]
    return quick_sort(left) + mid + quick_sort(right)
""", [("quick_sort([3, 6, 1, 8, 2, 9, 2])", [1, 2, 2, 3, 6, 8, 9]), ("quick_sort([])", [])], "It picks a pivot and recursively sorts the smaller and larger parts. This readable version uses extra memory; production sorts work in place. Average O(n log n), worst case O(n²).")
g("merge_sort", "implement merge sort", """
def merge_sort(items):
    if len(items) <= 1:
        return list(items)
    mid = len(items) // 2
    left, right = merge_sort(items[:mid]), merge_sort(items[mid:])
    merged, i, j = [], 0, 0
    while i < len(left) and j < len(right):
        if left[i] <= right[j]:
            merged.append(left[i]); i += 1
        else:
            merged.append(right[j]); j += 1
    return merged + left[i:] + right[j:]
""", [("merge_sort([5, 2, 4, 7, 1, 3, 2, 6])", [1, 2, 2, 3, 4, 5, 6, 7])], "Split in half, sort each half, then merge. It is O(n log n) in every case and stable, at the cost of O(n) extra space.")
g("insertion_sort", "implement insertion sort", """
def insertion_sort(items):
    arr = list(items)
    for i in range(1, len(arr)):
        key = arr[i]
        j = i - 1
        while j >= 0 and arr[j] > key:
            arr[j + 1] = arr[j]
            j -= 1
        arr[j + 1] = key
    return arr
""", [("insertion_sort([4, 3, 2, 10, 12, 1, 5, 6])", [1, 2, 3, 4, 5, 6, 10, 12])], "Each element is slid left into its place among the already-sorted prefix. It's O(n²) in general but fast on nearly-sorted data.")
g("power_set", "generate all subsets of a list", """
def power_set(items):
    subsets = [[]]
    for x in items:
        subsets += [s + [x] for s in subsets]
    return subsets
""", [("power_set([1, 2, 3])", [[], [1], [2], [1, 2], [3], [1, 3], [2, 3], [1, 2, 3]]), ("len(power_set(list(range(5))))", 32)], "Each new item doubles the collection: every existing subset appears with and without it. A set of n items has 2ⁿ subsets.")
g("permutations_list", "return all permutations of a list without using itertools", """
def permutations_list(items):
    if len(items) <= 1:
        return [list(items)]
    result = []
    for i, x in enumerate(items):
        for p in permutations_list(items[:i] + items[i + 1:]):
            result.append([x] + p)
    return result
""", [("permutations_list([1, 2, 3])", [[1, 2, 3], [1, 3, 2], [2, 1, 3], [2, 3, 1], [3, 1, 2], [3, 2, 1]])], "Pick each element as the first one and recursively permute the rest. n items give n! results, so use it only for small n.")
g("gcd_list", "compute the gcd of a whole list of integers", """
from functools import reduce
from math import gcd

def gcd_list(nums):
    return reduce(gcd, nums)
""", [("gcd_list([12, 18, 24])", 6), ("gcd_list([7])", 7)], "`reduce` folds `gcd` across the list, since gcd(a, b, c) = gcd(gcd(a, b), c).")
g("count_islands", "count islands (groups of connected 1s) in a grid of 0s and 1s", """
def count_islands(grid):
    if not grid:
        return 0
    rows, cols = len(grid), len(grid[0])
    seen = set()

    def dfs(r, c):
        stack = [(r, c)]
        while stack:
            y, x = stack.pop()
            if (y, x) in seen or not (0 <= y < rows and 0 <= x < cols) or grid[y][x] == 0:
                continue
            seen.add((y, x))
            stack.extend([(y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)])

    count = 0
    for r in range(rows):
        for c in range(cols):
            if grid[r][c] == 1 and (r, c) not in seen:
                dfs(r, c)
                count += 1
    return count
""", [("count_islands([[1,1,0,0],[0,1,0,1],[0,0,0,1],[1,0,0,0]])", 3), ("count_islands([])", 0)], "Each time it finds unvisited land, it floods through all connected land (an explicit stack avoids recursion limits) and counts one island.")
g("parse_csv_line", "parse one CSV line that may contain quoted fields with commas", """
import csv
import io

def parse_csv_line(line):
    return next(csv.reader(io.StringIO(line)))
""", [("parse_csv_line('a,\"b,c\",d')", ["a", "b,c", "d"])], "Use the `csv` module instead of `split(',')`, because quoted fields can contain commas and quotes.")
g("human_size", "format a byte count as a human-readable size like 1.5 MB", """
def human_size(n):
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(n)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
""", [("human_size(512)", "512 B"), ("human_size(1536)", "1.5 KB"), ("human_size(5 * 1024 ** 2)", "5.0 MB")], "Divide by 1024 until the value is small enough to read comfortably.")
g("business_days_between", "count weekdays between two dates inclusive of the start, exclusive of the end", """
from datetime import date, timedelta

def business_days_between(start, end):
    d = date.fromisoformat(start)
    stop = date.fromisoformat(end)
    count = 0
    while d < stop:
        if d.weekday() < 5:
            count += 1
        d += timedelta(days=1)
    return count
""", [("business_days_between('2024-01-01', '2024-01-08')", 5), ("business_days_between('2024-01-06', '2024-01-08')", 0)], "`weekday()` returns 0 for Monday through 6 for Sunday, so values below 5 are weekdays. It ignores public holidays.")
g("sliding_window_max", "return the maximum of every window of size k in a list", """
from collections import deque

def sliding_window_max(nums, k):
    dq, out = deque(), []
    for i, x in enumerate(nums):
        while dq and nums[dq[-1]] <= x:
            dq.pop()
        dq.append(i)
        if dq[0] <= i - k:
            dq.popleft()
        if i >= k - 1:
            out.append(nums[dq[0]])
    return out
""", [("sliding_window_max([1, 3, -1, -3, 5, 3, 6, 7], 3)", [3, 3, 5, 5, 6, 7])], "A deque of indexes keeps candidates in decreasing value order, so the front is always the window's maximum. Each index is added and removed once: O(n).")
g("validate_email_simple", "do a basic sanity check on an email address", """
import re

def validate_email_simple(email):
    return bool(re.fullmatch(r"[^@\\s]+@[^@\\s]+\\.[^@\\s]+", email))
""", [("validate_email_simple('ada@example.com')", True), ("validate_email_simple('ada@example')", False), ("validate_email_simple('a b@c.com')", False)], "This only rejects obviously malformed addresses. The only reliable validation of an email is sending a confirmation message.")
g("timer_decorator", "write a decorator that records how long a function took", """
import time
from functools import wraps

def timed(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        try:
            return func(*args, **kwargs)
        finally:
            wrapper.last_duration = time.perf_counter() - start
    wrapper.last_duration = None
    return wrapper
""", [("(lambda: (lambda w: (w(2), w.last_duration is not None))(timed(lambda x: x * 2)))()", (4, True))], "A decorator wraps the function; `@wraps` preserves its name and docstring, and `perf_counter` is the right clock for measuring durations.")
g("context_manager_cd", "write a context manager that temporarily changes an object's attribute and restores it afterwards", """
from contextlib import contextmanager

@contextmanager
def temporarily(obj, name, value):
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield obj
    finally:
        setattr(obj, name, old)
""", [("(lambda ns: (lambda cm: (cm.__enter__() is ns, ns.x, cm.__exit__(None, None, None), ns.x))(temporarily(ns, 'x', 2)))(__import__('types').SimpleNamespace(x=1))[1::2]", (2, 1))], "The `finally` block restores the old value even if the body raises, which is the main reason to use a context manager.")
g("dataclass_point", "define a Point dataclass with a distance method", """
from dataclasses import dataclass
import math

@dataclass(frozen=True)
class Point:
    x: float
    y: float

    def distance_to(self, other):
        return math.hypot(self.x - other.x, self.y - other.y)
""", [("Point(0, 0).distance_to(Point(3, 4))", 5.0), ("Point(1, 2) == Point(1, 2)", True)], "`@dataclass` generates `__init__`, `__repr__` and `__eq__`; `frozen=True` makes instances immutable and hashable.")
g("counter_class", "implement a simple Counter class with increment, decrement and reset", """
class Counter:
    def __init__(self, start=0):
        self.value = start

    def increment(self, by=1):
        self.value += by
        return self.value

    def decrement(self, by=1):
        self.value -= by
        return self.value

    def reset(self):
        self.value = 0
""", [("(lambda c: (c.increment(), c.increment(5), c.decrement(2)))(Counter())", (1, 6, 4))], "State lives on the instance in `self.value`; each method updates it and returns the new value.")
g("queue_two_stacks", "implement a queue using two stacks", """
class Queue:
    def __init__(self):
        self._in, self._out = [], []

    def enqueue(self, x):
        self._in.append(x)

    def dequeue(self):
        if not self._out:
            while self._in:
                self._out.append(self._in.pop())
        if not self._out:
            raise IndexError("dequeue from empty queue")
        return self._out.pop()
""", [("(lambda q: (q.enqueue(1), q.enqueue(2), q.dequeue(), q.enqueue(3), q.dequeue(), q.dequeue()))(Queue())[2:]", (1, None, 2, 3))], "Items are pushed onto the input stack; when the output stack is empty everything is reversed over into it, which restores first-in-first-out order. Each item moves at most once, so operations are O(1) amortised.")
g("trie_class", "implement a Trie with insert and starts_with", """
class Trie:
    def __init__(self):
        self.root = {}

    def insert(self, word):
        node = self.root
        for ch in word:
            node = node.setdefault(ch, {})
        node["$"] = True

    def starts_with(self, prefix):
        node = self.root
        for ch in prefix:
            if ch not in node:
                return False
            node = node[ch]
        return True
""", [("(lambda t: (t.insert('apple'), t.insert('app'), t.starts_with('ap'), t.starts_with('b')))(Trie())[2:]", (True, False))], "Each node is a dictionary of child letters; `$` marks the end of a word. Looking up a prefix takes time proportional to its length, regardless of how many words are stored.")
g("rate_limiter", "implement a simple fixed-window rate limiter class", """
class RateLimiter:
    def __init__(self, limit, window_seconds):
        self.limit = limit
        self.window = window_seconds
        self.counts = {}

    def allow(self, key, now):
        bucket = int(now // self.window)
        k = (key, bucket)
        self.counts[k] = self.counts.get(k, 0) + 1
        return self.counts[k] <= self.limit
""", [("(lambda r: [r.allow('u', t) for t in (0, 1, 2, 3, 61)])(RateLimiter(3, 60))", [True, True, True, False, True])], "Requests are counted per key and per time window. The time is passed in so the class is easy to test. A production limiter would also expire old buckets and might use a sliding window.")
g("json_pretty", "load a JSON string and pretty print it with sorted keys", """
import json

def json_pretty(text):
    return json.dumps(json.loads(text), indent=2, sort_keys=True)
""", [("json_pretty('{\"b\": 1, \"a\": [1, 2]}')", '{\n  "a": [\n    1,\n    2\n  ],\n  "b": 1\n}')], "`json.loads` parses and `json.dumps` with `indent` and `sort_keys` produces stable, readable output.")
g("env_bool", "parse a boolean from an environment-variable style string", """
def env_bool(value, default=False):
    if value is None:
        return default
    v = value.strip().lower()
    if v in {"1", "true", "yes", "on"}:
        return True
    if v in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"not a boolean: {value!r}")
""", [("env_bool('Yes')", True), ("env_bool(None, True)", True), ("env_bool('off')", False)], "Environment values are always strings, and `bool('false')` is True, so they need explicit parsing. Unknown values raise instead of silently choosing.")
g("exponential_backoff", "compute exponential backoff delays with a cap", """
def backoff_delays(attempts, base=0.5, cap=8.0):
    return [min(cap, base * 2 ** i) for i in range(attempts)]
""", [("backoff_delays(6)", [0.5, 1.0, 2.0, 4.0, 8.0, 8.0])], "Each retry waits twice as long as the previous one up to a cap. In real systems add random jitter so many clients don't retry in lockstep.")
g("paginate", "return the items for a given page number and page size, plus the total pages", """
import math

def paginate(items, page, size):
    total_pages = max(1, math.ceil(len(items) / size))
    page = min(max(page, 1), total_pages)
    start = (page - 1) * size
    return items[start:start + size], total_pages
""", [("paginate(list(range(10)), 2, 4)", ([4, 5, 6, 7], 3)), ("paginate(list(range(10)), 99, 4)", ([8, 9], 3))], "The page number is clamped into the valid range and then sliced; the total page count is rounded up.")
g("levenshtein", "compute the edit distance between two strings", """
def levenshtein(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]
""", [("levenshtein('kitten', 'sitting')", 3), ("levenshtein('', 'abc')", 3), ("levenshtein('same', 'same')", 0)], "Dynamic programming over prefixes: each cell is the cheapest of insert, delete or substitute. Only two rows are kept, so memory is O(len(b)).")
g("lcs_length", "compute the length of the longest common subsequence of two strings", """
def lcs_length(a, b):
    dp = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    return dp[-1][-1]
""", [("lcs_length('ABCBDAB', 'BDCABA')", 4), ("lcs_length('abc', 'xyz')", 0)], "`dp[i][j]` is the answer for the first i and j characters: extend a match diagonally, otherwise take the better of dropping a character from either string.")
g("knapsack", "solve the 0/1 knapsack problem and return the best total value", """
def knapsack(items, capacity):
    best = [0] * (capacity + 1)
    for weight, value in items:
        for c in range(capacity, weight - 1, -1):
            best[c] = max(best[c], best[c - weight] + value)
    return best[capacity]
""", [("knapsack([(1, 1), (3, 4), (4, 5), (5, 7)], 7)", 9), ("knapsack([], 10)", 0)], "`best[c]` is the best value with capacity c. Iterating capacity downwards ensures each item is used at most once.")
g("n_queens_count", "count solutions to the N-Queens problem", """
def n_queens_count(n):
    cols, d1, d2 = set(), set(), set()

    def place(r):
        if r == n:
            return 1
        total = 0
        for c in range(n):
            if c in cols or (r - c) in d1 or (r + c) in d2:
                continue
            cols.add(c); d1.add(r - c); d2.add(r + c)
            total += place(r + 1)
            cols.remove(c); d1.remove(r - c); d2.remove(r + c)
        return total

    return place(0)
""", [("n_queens_count(4)", 2), ("n_queens_count(6)", 4), ("n_queens_count(8)", 92)], "Backtracking: place one queen per row, tracking occupied columns and diagonals, and undo the placement when returning. Sets make the conflict checks O(1).")
