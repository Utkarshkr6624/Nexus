"""Category 4b: Python function bank. Every function is executed against its tests before any example is produced."""
import textwrap
from lib import *

CAT = "programming"
R = seeded("prog_py")
out = []

# (name, description, code, [(call, expected)], explanation)
F = []


def f(name, desc, code, tests, expl):
    F.append((name, desc, textwrap.dedent(code).strip("\n"), tests, expl))


f("chunk_list", "split a list into chunks of a given size", """
def chunk_list(items, size):
    if size <= 0:
        raise ValueError("size must be positive")
    return [items[i:i + size] for i in range(0, len(items), size)]
""", [("chunk_list([1,2,3,4,5], 2)", [[1, 2], [3, 4], [5]]), ("chunk_list([], 3)", [])], "It steps through the list in jumps of `size` and slices out each piece; the last chunk may be shorter. A non-positive size raises instead of looping forever.")
f("flatten", "flatten an arbitrarily nested list", """
def flatten(nested):
    result = []
    for item in nested:
        if isinstance(item, list):
            result.extend(flatten(item))
        else:
            result.append(item)
    return result
""", [("flatten([1,[2,[3,4]],5])", [1, 2, 3, 4, 5]), ("flatten([])", [])], "It recurses into sublists and appends plain values, so any nesting depth works (until Python's recursion limit).")
f("word_frequency", "count how often each word appears, ignoring case and punctuation", """
import re
from collections import Counter

def word_frequency(text):
    words = re.findall(r"[a-z']+", text.lower())
    return dict(Counter(words))
""", [("word_frequency('The cat. The hat!')", {"the": 2, "cat": 1, "hat": 1})], "A regular expression pulls out the words from the lower-cased text and `Counter` tallies them.")
f("is_balanced", "check whether brackets in a string are balanced", """
def is_balanced(s):
    pairs = {")": "(", "]": "[", "}": "{"}
    stack = []
    for ch in s:
        if ch in "([{":
            stack.append(ch)
        elif ch in pairs:
            if not stack or stack.pop() != pairs[ch]:
                return False
    return not stack
""", [("is_balanced('{[()]}')", True), ("is_balanced('([)]')", False), ("is_balanced('((')", False)], "A stack remembers the open brackets. Each closer must match the most recent opener, and the stack must be empty at the end.")
f("merge_sorted", "merge two sorted lists into one sorted list", """
def merge_sorted(a, b):
    i = j = 0
    merged = []
    while i < len(a) and j < len(b):
        if a[i] <= b[j]:
            merged.append(a[i])
            i += 1
        else:
            merged.append(b[j])
            j += 1
    merged.extend(a[i:])
    merged.extend(b[j:])
    return merged
""", [("merge_sorted([1,4,6],[2,3,7,9])", [1, 2, 3, 4, 6, 7, 9]), ("merge_sorted([], [1])", [1])], "Two pointers walk both lists, always taking the smaller front item; whatever is left in either list is already sorted and is appended at the end.")
f("group_by_length", "group words into a dictionary keyed by their length", """
from collections import defaultdict

def group_by_length(words):
    groups = defaultdict(list)
    for w in words:
        groups[len(w)].append(w)
    return dict(groups)
""", [("group_by_length(['a','to','be','cat'])", {1: ["a"], 2: ["to", "be"], 3: ["cat"]})], "`defaultdict(list)` creates the list the first time a key is used, so each word is appended to the bucket for its length.")
f("rotate_list", "rotate a list to the right by k positions", """
def rotate_list(items, k):
    if not items:
        return []
    k %= len(items)
    return items[-k:] + items[:-k] if k else list(items)
""", [("rotate_list([1,2,3,4,5], 2)", [4, 5, 1, 2, 3]), ("rotate_list([1,2,3], 6)", [1, 2, 3]), ("rotate_list([], 3)", [])], "`k` is reduced modulo the length so large rotations wrap around; the last k items move to the front.")
f("longest_common_prefix", "find the longest common prefix of a list of strings", """
def longest_common_prefix(strs):
    if not strs:
        return ""
    prefix = strs[0]
    for s in strs[1:]:
        while not s.startswith(prefix):
            prefix = prefix[:-1]
            if not prefix:
                return ""
    return prefix
""", [("longest_common_prefix(['flower','flow','flight'])", "fl"), ("longest_common_prefix(['dog','car'])", "")], "Start with the first word as the prefix and shorten it until every other word starts with it.")
f("to_snake_case", "convert a camelCase or PascalCase string to snake_case", """
import re

def to_snake_case(name):
    s = re.sub(r"(?<!^)(?=[A-Z])", "_", name)
    return s.lower()
""", [("to_snake_case('userProfileName')", "user_profile_name"), ("to_snake_case('HTTPServer')", "h_t_t_p_server")], "The regex inserts an underscore before every capital letter that is not at the start, then lower-cases the result. Note it splits acronyms letter by letter, which is a limitation worth knowing about.")
f("is_leap_year", "check whether a year is a leap year", """
def is_leap_year(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
""", [("is_leap_year(2000)", True), ("is_leap_year(1900)", False), ("is_leap_year(2024)", True), ("is_leap_year(2023)", False)], "A year is a leap year if divisible by 4, except century years, which must also be divisible by 400.")
f("days_between", "return the number of days between two ISO date strings", """
from datetime import date

def days_between(a, b):
    return abs((date.fromisoformat(b) - date.fromisoformat(a)).days)
""", [("days_between('2024-01-01','2024-03-01')", 60), ("days_between('2024-03-01','2024-01-01')", 60)], "`date.fromisoformat` parses each string, subtracting dates gives a `timedelta`, and `abs` makes the order irrelevant.")
f("celsius_to_fahrenheit", "convert a list of Celsius temperatures to Fahrenheit, rounded to one decimal", """
def celsius_to_fahrenheit(temps):
    return [round(c * 9 / 5 + 32, 1) for c in temps]
""", [("celsius_to_fahrenheit([0, 100, 36.6])", [32.0, 212.0, 97.9])], "A list comprehension applies F = C × 9/5 + 32 to each value and rounds the result.")
f("moving_average", "compute the moving average of a list with a window size", """
def moving_average(values, window):
    if window <= 0 or window > len(values):
        return []
    total = sum(values[:window])
    result = [total / window]
    for i in range(window, len(values)):
        total += values[i] - values[i - window]
        result.append(total / window)
    return result
""", [("moving_average([1,2,3,4,5], 3)", [2.0, 3.0, 4.0]), ("moving_average([1,2], 5)", [])], "Instead of re-summing every window, it adds the new value and subtracts the one that left, which makes the whole thing O(n).")
f("invert_dict", "swap keys and values in a dictionary, collecting duplicate values in lists", """
def invert_dict(d):
    inverted = {}
    for k, v in d.items():
        inverted.setdefault(v, []).append(k)
    return inverted
""", [("invert_dict({'a':1,'b':2,'c':1})", {1: ["a", "c"], 2: ["b"]})], "Because several keys can share a value, each new key maps to a list of the original keys.")
f("find_duplicates", "return the values that appear more than once in a list", """
def find_duplicates(items):
    seen, dups = set(), set()
    for x in items:
        if x in seen:
            dups.add(x)
        seen.add(x)
    return sorted(dups)
""", [("find_duplicates([1,2,3,2,1,4])", [1, 2]), ("find_duplicates([1,2])", [])], "One pass with two sets: the first time a value is seen it goes into `seen`; later sightings mark it as a duplicate.")
f("roman_to_int", "convert a Roman numeral to an integer", """
def roman_to_int(s):
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    for i, ch in enumerate(s):
        v = values[ch]
        if i + 1 < len(s) and v < values[s[i + 1]]:
            total -= v
        else:
            total += v
    return total
""", [("roman_to_int('MCMXCIV')", 1994), ("roman_to_int('IX')", 9)], "Normally you add the symbol's value, but if a smaller value comes before a larger one (like IV), it is subtracted instead.")
f("int_to_binary", "convert a non-negative integer to a binary string without using bin()", """
def int_to_binary(n):
    if n == 0:
        return "0"
    bits = []
    while n > 0:
        bits.append(str(n % 2))
        n //= 2
    return "".join(reversed(bits))
""", [("int_to_binary(10)", "1010"), ("int_to_binary(0)", "0"), ("int_to_binary(255)", "11111111")], "Repeatedly divide by 2 and collect the remainders; they come out least-significant first, so the list is reversed at the end.")
f("caesar_cipher", "apply a Caesar cipher shift to a string, keeping case and leaving other characters alone", """
def caesar_cipher(text, shift):
    out = []
    for ch in text:
        if ch.isalpha():
            base = ord("A") if ch.isupper() else ord("a")
            out.append(chr((ord(ch) - base + shift) % 26 + base))
        else:
            out.append(ch)
    return "".join(out)
""", [("caesar_cipher('Hello, World!', 3)", "Khoor, Zruog!"), ("caesar_cipher('Khoor', -3)", "Hello")], "Each letter is shifted within its own 26-letter alphabet using modulo arithmetic. This is a toy cipher, not security.")
f("transpose", "transpose a matrix stored as a list of lists", """
def transpose(matrix):
    return [list(row) for row in zip(*matrix)]
""", [("transpose([[1,2,3],[4,5,6]])", [[1, 4], [2, 5], [3, 6]])], "`zip(*matrix)` pairs up the first elements of each row, then the second, and so on, which is exactly a transpose.")
f("pascal_row", "return the n-th row of Pascal's triangle", """
def pascal_row(n):
    row = [1]
    for _ in range(n):
        row = [a + b for a, b in zip([0] + row, row + [0])]
    return row
""", [("pascal_row(0)", [1]), ("pascal_row(4)", [1, 4, 6, 4, 1])], "Each row is built by adding neighbouring values of the previous row, padding with zeros at the ends.")
f("sieve", "list all primes up to n using the Sieve of Eratosthenes", """
def sieve(n):
    if n < 2:
        return []
    is_prime = [True] * (n + 1)
    is_prime[0] = is_prime[1] = False
    for i in range(2, int(n ** 0.5) + 1):
        if is_prime[i]:
            for j in range(i * i, n + 1, i):
                is_prime[j] = False
    return [i for i, p in enumerate(is_prime) if p]
""", [("sieve(30)", [2, 3, 5, 7, 11, 13, 17, 19, 23, 29]), ("sieve(1)", [])], "It marks multiples of each prime as composite, starting at i², so what remains unmarked is prime. It runs in about O(n log log n).")
f("first_unique_char", "return the index of the first non-repeating character in a string, or -1", """
from collections import Counter

def first_unique_char(s):
    counts = Counter(s)
    for i, ch in enumerate(s):
        if counts[ch] == 1:
            return i
    return -1
""", [("first_unique_char('leetcode')", 0), ("first_unique_char('aabb')", -1), ("first_unique_char('loveleetcode')", 2)], "First count every character, then scan the string in order and return the first index whose character occurs exactly once.")
f("deep_merge", "merge two nested dictionaries recursively, with the second taking priority", """
def deep_merge(a, b):
    result = dict(a)
    for key, value in b.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result
""", [("deep_merge({'a':1,'b':{'x':1,'y':2}}, {'b':{'y':3,'z':4},'c':5})", {"a": 1, "b": {"x": 1, "y": 3, "z": 4}, "c": 5})], "Where both sides have a dictionary under the same key it merges them recursively; otherwise the value from `b` wins. The inputs are not mutated.")
f("running_total", "return the running (cumulative) totals of a list of numbers", """
from itertools import accumulate

def running_total(nums):
    return list(accumulate(nums))
""", [("running_total([1,2,3,4])", [1, 3, 6, 10]), ("running_total([])", [])], "`itertools.accumulate` adds each element to the running sum and yields it.")
f("safe_divide", "divide two numbers and return None instead of raising when dividing by zero", """
def safe_divide(a, b):
    try:
        return a / b
    except ZeroDivisionError:
        return None
""", [("safe_divide(10, 4)", 2.5), ("safe_divide(1, 0)", None)], "It catches only `ZeroDivisionError`, so other mistakes (like passing a string) still surface instead of being hidden.")
f("top_n", "return the n largest values of a list in descending order", """
import heapq

def top_n(items, n):
    return heapq.nlargest(n, items)
""", [("top_n([5, 1, 9, 3, 7], 3)", [9, 7, 5]), ("top_n([2], 5)", [2])], "`heapq.nlargest` keeps a small heap, which is faster than sorting everything when n is much smaller than the list.")
f("capitalize_words", "capitalise the first letter of every word in a sentence", """
def capitalize_words(sentence):
    return " ".join(w[:1].upper() + w[1:] for w in sentence.split(" "))
""", [("capitalize_words('hello big world')", "Hello Big World"), ("capitalize_words('')", "")], "Splitting on single spaces keeps the original spacing; `w[:1]` is safe for empty strings.")
f("is_sorted", "check whether a list is sorted in non-decreasing order", """
def is_sorted(items):
    return all(items[i] <= items[i + 1] for i in range(len(items) - 1))
""", [("is_sorted([1,2,2,5])", True), ("is_sorted([3,1])", False), ("is_sorted([])", True)], "It compares each neighbouring pair and stops at the first violation thanks to `all`.")
f("count_pairs_with_sum", "count the pairs of numbers in a list that add up to a target", """
from collections import Counter

def count_pairs_with_sum(nums, target):
    seen = Counter()
    pairs = 0
    for x in nums:
        pairs += seen[target - x]
        seen[x] += 1
    return pairs
""", [("count_pairs_with_sum([1,5,7,-1,5], 6)", 3), ("count_pairs_with_sum([], 3)", 0)], "For each number it adds how many earlier numbers complement it to the target, then records the number itself. That is O(n).")
f("title_case_snake", "convert a snake_case string to Title Case with spaces", """
def title_case_snake(s):
    return " ".join(part.capitalize() for part in s.split("_") if part)
""", [("title_case_snake('user_profile_name')", "User Profile Name")], "It splits on underscores, ignores empty parts from doubled underscores, and capitalises each piece.")
f("mean_median_mode", "return the mean, median and mode of a non-empty list of numbers", """
from statistics import mean, median, multimode

def mean_median_mode(nums):
    return mean(nums), median(nums), sorted(multimode(nums))
""", [("mean_median_mode([1,2,2,3,4])", (2.4, 2, [2]))], "The `statistics` module does the work. `multimode` returns every value tied for most common, so the mode is a list.")
f("valid_ipv4", "validate an IPv4 address string", """
def valid_ipv4(s):
    parts = s.split(".")
    if len(parts) != 4:
        return False
    for p in parts:
        if not p.isdigit() or (len(p) > 1 and p[0] == "0") or int(p) > 255:
            return False
    return True
""", [("valid_ipv4('192.168.1.1')", True), ("valid_ipv4('256.1.1.1')", False), ("valid_ipv4('1.2.3')", False), ("valid_ipv4('01.2.3.4')", False)], "It needs four dot-separated parts, each all digits, no leading zeros, and at most 255.")
f("compress_string", "compress a string with run-length encoding, e.g. aaabcc -> a3b1c2", """
from itertools import groupby

def compress_string(s):
    return "".join(f"{ch}{len(list(group))}" for ch, group in groupby(s))
""", [("compress_string('aaabcc')", "a3b1c2"), ("compress_string('')", "")], "`groupby` collects consecutive equal characters, and each run is written as the character followed by its length.")
f("lru_cache_class", "implement a small LRU cache class with get and put", """
from collections import OrderedDict

class LRUCache:
    def __init__(self, capacity):
        self.capacity = capacity
        self.data = OrderedDict()

    def get(self, key):
        if key not in self.data:
            return -1
        self.data.move_to_end(key)
        return self.data[key]

    def put(self, key, value):
        if key in self.data:
            self.data.move_to_end(key)
        self.data[key] = value
        if len(self.data) > self.capacity:
            self.data.popitem(last=False)
""", [("(lambda c: (c.put(1, 1), c.put(2, 2), c.get(1), c.put(3, 3), c.get(2), c.get(3)))(LRUCache(2))[2:]", (1, None, -1, 3))], "An `OrderedDict` keeps usage order: touching a key moves it to the end, and when capacity is exceeded the oldest item (the front) is evicted.")
f("stack_class", "implement a simple Stack class with push, pop, peek and is_empty", """
class Stack:
    def __init__(self):
        self._items = []

    def push(self, item):
        self._items.append(item)

    def pop(self):
        if not self._items:
            raise IndexError("pop from empty stack")
        return self._items.pop()

    def peek(self):
        if not self._items:
            raise IndexError("peek at empty stack")
        return self._items[-1]

    def is_empty(self):
        return not self._items
""", [("(lambda s: (s.push(1), s.push(2), s.pop(), s.peek(), s.is_empty()))(Stack())[2:]", (2, 1, False))], "A list does the storage: its end is the top of the stack, so push and pop are O(1). Empty-stack access raises a clear error.")
f("binary_tree_depth", "compute the maximum depth of a binary tree given as nested tuples (value, left, right)", """
def tree_depth(node):
    if node is None:
        return 0
    _, left, right = node
    return 1 + max(tree_depth(left), tree_depth(right))
""", [("tree_depth((1, (2, None, None), (3, (4, None, None), None)))", 3), ("tree_depth(None)", 0)], "The depth of a node is one plus the deeper of its subtrees, and an empty tree has depth zero.")
f("retry", "write a retry helper that calls a function up to n times", """
def retry(func, attempts=3):
    last_error = None
    for _ in range(attempts):
        try:
            return func()
        except Exception as exc:
            last_error = exc
    raise last_error
""", [("(lambda state: retry(lambda: (state.append(1), 1/0 if len(state) < 3 else 'ok')[1], 3))([])", "ok")], "It re-calls the function until it succeeds and re-raises the final error if every attempt fails. In real code add a delay (backoff) and retry only errors that can be transient.")
f("parse_kv", "parse a string like 'a=1;b=2' into a dict of strings", """
def parse_kv(s):
    result = {}
    for pair in s.split(";"):
        if "=" in pair:
            key, value = pair.split("=", 1)
            result[key.strip()] = value.strip()
    return result
""", [("parse_kv('a=1; b=2;c=x=y')", {"a": "1", "b": "2", "c": "x=y"}), ("parse_kv('')", {})], "Splitting on the first '=' only keeps values that themselves contain '='. Pieces without '=' are skipped instead of crashing.")
f("slugify", "turn a title into a URL slug", """
import re

def slugify(title):
    s = re.sub(r"[^a-z0-9]+", "-", title.lower())
    return s.strip("-")
""", [("slugify('Hello, World! 2024')", "hello-world-2024"), ("slugify('  --Edge Case--  ')", "edge-case")], "Anything that is not a letter or digit becomes a single hyphen, and leading or trailing hyphens are trimmed.")
f("matrix_diagonal_sum", "sum the main and anti diagonals of a square matrix without counting the centre twice", """
def matrix_diagonal_sum(m):
    n = len(m)
    total = sum(m[i][i] + m[i][n - 1 - i] for i in range(n))
    if n % 2:
        total -= m[n // 2][n // 2]
    return total
""", [("matrix_diagonal_sum([[1,2,3],[4,5,6],[7,8,9]])", 25), ("matrix_diagonal_sum([[1,1],[1,1]])", 4)], "Both diagonals are added per row; in an odd-sized matrix the centre cell lies on both, so it is subtracted once.")
f("anagram_groups", "group a list of words into anagram groups", """
from collections import defaultdict

def anagram_groups(words):
    groups = defaultdict(list)
    for w in words:
        groups["".join(sorted(w.lower()))].append(w)
    return sorted(sorted(g) for g in groups.values())
""", [("anagram_groups(['eat','tea','tan','ate','nat','bat'])", [["ate", "eat", "tea"], ["bat"], ["nat", "tan"]])], "Words that are anagrams share the same sorted-letters key, so the key groups them.")
f("remove_none", "remove None values from a dict, including nested dicts", """
def remove_none(d):
    return {k: remove_none(v) if isinstance(v, dict) else v for k, v in d.items() if v is not None}
""", [("remove_none({'a':1,'b':None,'c':{'d':None,'e':2}})", {"a": 1, "c": {"e": 2}})], "A dictionary comprehension skips None values and recurses into nested dictionaries.")
f("fizz_list", "return the FizzBuzz sequence for 1..n as a list of strings", """
def fizz_list(n):
    out = []
    for i in range(1, n + 1):
        s = ("Fizz" if i % 3 == 0 else "") + ("Buzz" if i % 5 == 0 else "")
        out.append(s or str(i))
    return out
""", [("fizz_list(5)", ["1", "2", "Fizz", "4", "Buzz"]), ("fizz_list(15)[-1]", "FizzBuzz")], "Building the string from both parts handles FizzBuzz naturally; an empty string falls back to the number.")
f("clamp", "clamp a number between a minimum and a maximum", """
def clamp(x, lo, hi):
    if lo > hi:
        raise ValueError("lo must not exceed hi")
    return max(lo, min(x, hi))
""", [("clamp(15, 0, 10)", 10), ("clamp(-3, 0, 10)", 0), ("clamp(5, 0, 10)", 5)], "`min` caps the value at the upper bound and `max` lifts it to the lower bound.")
f("unique_preserve_order", "remove duplicates from a list while preserving order", """
def unique_preserve_order(items):
    return list(dict.fromkeys(items))
""", [("unique_preserve_order([3,1,3,2,1])", [3, 1, 2])], "Dictionary keys are unique and, since Python 3.7, keep insertion order, so `dict.fromkeys` is a compact order-preserving de-duplication for hashable items.")
f("binary_to_int", "convert a binary string to an integer without int(s, 2)", """
def binary_to_int(s):
    n = 0
    for ch in s:
        if ch not in "01":
            raise ValueError(f"invalid binary digit: {ch!r}")
        n = n * 2 + int(ch)
    return n
""", [("binary_to_int('1010')", 10), ("binary_to_int('0')", 0)], "Each new bit doubles the running value and adds the bit, which is how positional notation works.")
f("sum_of_squares_odd", "sum the squares of the odd numbers in a list", """
def sum_of_squares_odd(nums):
    return sum(x * x for x in nums if x % 2 != 0)
""", [("sum_of_squares_odd([1,2,3,4,5])", 35), ("sum_of_squares_odd([2,4])", 0)], "A generator expression filters odd values and squares them lazily, and `sum` adds them up.")
f("read_lines_clean", "clean a list of text lines by stripping whitespace and dropping blank lines and comments starting with #", """
def read_lines_clean(lines):
    cleaned = []
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#"):
            cleaned.append(line)
    return cleaned
""", [("read_lines_clean(['  a  ', '', '# c', 'b'])", ["a", "b"])], "Each line is stripped first, then kept only if something is left and it does not start with `#`.")
f("percent_change", "calculate the percentage change from an old value to a new value, rounded to two decimals", """
def percent_change(old, new):
    if old == 0:
        raise ZeroDivisionError("old value is zero")
    return round((new - old) / abs(old) * 100, 2)
""", [("percent_change(50, 75)", 50.0), ("percent_change(80, 60)", -25.0)], "The change divided by the original, times 100. A zero baseline has no meaningful percentage, so it raises.")
f("find_missing_number", "find the missing number in a list containing 0..n with one number missing", """
def find_missing_number(nums):
    n = len(nums)
    return n * (n + 1) // 2 - sum(nums)
""", [("find_missing_number([3,0,1])", 2), ("find_missing_number([0,1])", 2)], "The sum of 0..n is n(n+1)/2; subtracting the actual sum leaves exactly the missing number, in O(n) time and O(1) space.")
f("is_pangram", "check whether a sentence uses every letter of the English alphabet", """
import string

def is_pangram(s):
    return set(string.ascii_lowercase) <= set(s.lower())
""", [("is_pangram('The quick brown fox jumps over the lazy dog')", True), ("is_pangram('Hello world')", False)], "Set containment: the 26 letters must be a subset of the letters in the sentence.")
f("batch_dict_items", "yield dictionary items in batches of a given size", """
from itertools import islice

def batch_items(d, size):
    it = iter(d.items())
    while True:
        batch = list(islice(it, size))
        if not batch:
            return
        yield batch
""", [("list(batch_items({'a':1,'b':2,'c':3}, 2))", [[("a", 1), ("b", 2)], [("c", 3)]])], "A generator keeps one iterator over the items and uses `islice` to take `size` at a time, so it never builds the whole list of batches.")
f("memoized_fib", "compute Fibonacci numbers with memoisation", """
from functools import lru_cache

@lru_cache(maxsize=None)
def memoized_fib(n):
    if n < 2:
        return n
    return memoized_fib(n - 1) + memoized_fib(n - 2)
""", [("memoized_fib(30)", 832040), ("memoized_fib(1)", 1)], "`lru_cache` remembers earlier results, turning the exponential naive recursion into linear time. Very large n will still hit the recursion limit.")
f("temperature_stats", "return the minimum, maximum and range of a non-empty list of temperatures", """
def temperature_stats(temps):
    lo, hi = min(temps), max(temps)
    return {"min": lo, "max": hi, "range": hi - lo}
""", [("temperature_stats([12, 18, 9, 21])", {"min": 9, "max": 21, "range": 12})], "A single call each to `min` and `max`, with the range derived from them.")

from py_bank2 import F2
from py_bank3 import F3
F.extend(F2)
F.extend(F3)

# ----- run every function against its tests -----
bad = 0
VER = []
for name, desc, code, tests, expl in F:
    env = {}
    try:
        exec(code, env)
        for call, exp in tests:
            got = eval(call, env)
            if got != exp:
                raise AssertionError(f"{call} -> {got!r}, expected {exp!r}")
        VER.append((name, desc, code, tests, expl, env))
    except Exception as e:
        bad += 1
        print("FAILED", name, e)
print(f"python bank: {len(VER)} verified, {bad} failed")

ASK_WRITE = ["Write a Python function to {d}.", "How would I {d} in Python?", "python: {d}", "Can you write a function that will {d}?", "I need to {d}. Can you show me a clean Python way?"]
ASK_EXPL = ["What does this code do?\n\n```python\n{c}\n```", "Explain this Python function to me.\n\n```python\n{c}\n```", "Can you walk me through this code? I'm trying to understand it.\n\n```python\n{c}\n```"]
ASK_TEST = ["Write pytest tests for this function.\n\n```python\n{c}\n```", "Can you add unit tests for this?\n\n```python\n{c}\n```"]
ASK_RUN = ["What does this print?\n\n```python\n{c}\n\nprint({call})\n```", "What's the output of this snippet?\n\n```python\n{c}\n\nprint({call})\n```"]

for name, desc, code, tests, expl, env in VER:
    shown = "\n".join(f"{call} -> {eval(call, env)!r}" for call, _ in tests[:3])
    out.append(mk(CAT, "python_write", [("user", R.choice(ASK_WRITE).format(d=desc)), ("assistant",
        f"```python\n{code}\n```\n\n{expl}\n\nExample results:\n```\n{shown}\n```")], name, "medium", "verified", pick(R, SYS_GENERAL)))
    out.append(mk(CAT, "python_explain", [("user", R.choice(ASK_EXPL).format(c=code)), ("assistant", f"This function is meant to {desc}.\n\n{expl}\n\nFor example, running it gives:\n```\n{shown}\n```")], name, "easy", "verified", pick(R, SYS_GENERAL)))
    # pytest tests
    if not code.lstrip().startswith("class") and "lambda" not in tests[0][0]:
        tcases = "\n".join(f"    assert {call} == {exp!r}" for call, exp in tests)
        imp = f"from solution import {name}" if not name.endswith("_class") else ""
        # test function name is the real defined callable
        fn = [k for k in env if not k.startswith("__") and callable(env[k]) and k not in ("Counter", "defaultdict", "date", "accumulate", "islice", "groupby", "lru_cache", "OrderedDict", "re", "heapq", "string", "mean", "median", "multimode")]
        fn = fn[-1] if fn else name
        tcases = "\n".join(f"    assert {call} == {exp!r}" for call, exp in tests)
        test_src = f"from solution import {fn}\n\n\ndef test_{fn}_examples():\n{tcases}\n"
        # verify the generated test source by executing its asserts
        ok = True
        try:
            for call, exp in tests:
                assert eval(call, env) == exp
        except Exception:
            ok = False
        if ok:
            out.append(mk(CAT, "python_tests", [("user", R.choice(ASK_TEST).format(c=code)), ("assistant",
                f"Here's a small pytest file. It assumes the code above lives in `solution.py`.\n\n```python\n{test_src}```\n\nThe cases cover the normal path and the edge cases the function handles. Worth adding for your own data: empty input, very large input, and anything your callers might pass by mistake.")], name, "medium", "verified", pick(R, SYS_GENERAL)))
    call, exp = tests[0]
    if "lambda" not in call and not code.lstrip().startswith("class"):
        out.append(mk(CAT, "python_trace", [("user", R.choice(ASK_RUN).format(c=code, call=call)), ("assistant",
            f"It prints `{eval(call, env)!r}`.\n\n{expl}")], name, "easy", "verified", pick(R, SYS_GENERAL)))

# ----- refactor / improve pairs (both versions verified equal on tests) -----
PAIRS = [
 ("def total_even(nums):\n    total = 0\n    for i in range(len(nums)):\n        if nums[i] % 2 == 0:\n            total = total + nums[i]\n    return total", "def total_even(nums):\n    return sum(x for x in nums if x % 2 == 0)", "total_even([1,2,3,4,6])", 12, "Looping over indexes isn't needed here; iterate over the values directly and let `sum` do the accumulating."),
 ("def has_dup(items):\n    for i in range(len(items)):\n        for j in range(i + 1, len(items)):\n            if items[i] == items[j]:\n                return True\n    return False", "def has_dup(items):\n    return len(set(items)) != len(items)", "has_dup([1,2,3,1])", True, "The nested loop is O(n²). Putting the items in a set is O(n), at the cost of requiring hashable items."),
 ("def squares(n):\n    result = []\n    for i in range(n):\n        result.append(i * i)\n    return result", "def squares(n):\n    return [i * i for i in range(n)]", "squares(5)", [0, 1, 4, 9, 16], "A list comprehension says what you're building in one line and is slightly faster than repeated `append` calls."),
 ("def get_name(user):\n    if 'name' in user:\n        return user['name']\n    else:\n        return 'unknown'", "def get_name(user):\n    return user.get('name', 'unknown')", "get_name({})", "unknown", "`dict.get` with a default replaces the if/else membership check."),
 ("def join_words(words):\n    s = ''\n    for w in words:\n        s = s + w + ' '\n    return s.strip()", "def join_words(words):\n    return ' '.join(words)", "join_words(['a','b','c'])", "a b c", "Repeated string concatenation creates a new string each time; `str.join` builds the result once."),
 ("def count_chars(s):\n    counts = {}\n    for ch in s:\n        if ch in counts:\n            counts[ch] += 1\n        else:\n            counts[ch] = 1\n    return counts", "from collections import Counter\n\ndef count_chars(s):\n    return dict(Counter(s))", "count_chars('aab')", {"a": 2, "b": 1}, "`Counter` is built for exactly this tally."),
 ("def read_all(path):\n    f = open(path)\n    data = f.read()\n    f.close()\n    return data", "def read_all(path):\n    with open(path) as f:\n        return f.read()", None, None, "A `with` block closes the file even if `read()` raises, so the cleanup can't be forgotten."),
 ("def index_of_max(nums):\n    best = 0\n    for i in range(1, len(nums)):\n        if nums[i] > nums[best]:\n            best = i\n    return best", "def index_of_max(nums):\n    return max(range(len(nums)), key=nums.__getitem__)", "index_of_max([3,9,2,9])", 1, "Using `max` with a `key` expresses the intent directly. Both versions return the first index when there are ties."),
]
for old, new, call, exp, why in PAIRS:
    if call is not None:
        e1, e2 = {}, {}
        exec(old, e1)
        exec(new, e2)
        assert eval(call, e1) == exp == eval(call, e2), call
    q = R.choice(["Can you make this more Pythonic?\n\n```python\n{c}\n```", "Is there a cleaner way to write this?\n\n```python\n{c}\n```", "Please refactor this Python code.\n\n```python\n{c}\n```"]).format(c=old)
    out.append(mk(CAT, "python_refactor", [("user", q), ("assistant", f"Yes, here's a tidier version with the same behaviour:\n\n```python\n{new}\n```\n\n{why}")], "refactor_" + old[:20], "medium", "verified", pick(R, SYS_GENERAL)))

save("prog_py", out)
