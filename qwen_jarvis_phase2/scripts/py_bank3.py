"""Third batch of verified Python functions (consumed by gen_prog_py.py)."""
import textwrap

F3 = []


def g(name, desc, code, tests, expl):
    F3.append((name, desc, textwrap.dedent(code).strip("\n"), tests, expl))


g("is_isogram", "check whether a word has no repeating letters, ignoring case", """
def is_isogram(word):
    letters = [c for c in word.lower() if c.isalpha()]
    return len(letters) == len(set(letters))
""", [("is_isogram('Lumberjack')", True), ("is_isogram('Banana')", False), ("is_isogram('')", True)], "It lowercases the letters and compares the count with the number of distinct letters.")
g("longest_word", "return the longest word in a sentence, first one on ties", """
def longest_word(sentence):
    words = sentence.split()
    return max(words, key=len) if words else ""
""", [("longest_word('a quick brown fox')", "quick"), ("longest_word('')", "")], "`max` with `key=len` returns the first longest word because it keeps the first maximum it sees.")
g("sum_of_multiples", "sum all multiples of 3 or 5 below a limit", """
def sum_of_multiples(limit):
    return sum(i for i in range(limit) if i % 3 == 0 or i % 5 == 0)
""", [("sum_of_multiples(10)", 23), ("sum_of_multiples(1)", 0), ("sum_of_multiples(20)", 78)], "A generator expression filters numbers divisible by 3 or 5, so each number is counted once even if it's a multiple of both.")
g("vowel_ratio", "compute the fraction of letters that are vowels in a text, rounded to 2 decimals", """
def vowel_ratio(text):
    letters = [c for c in text.lower() if c.isalpha()]
    if not letters:
        return 0.0
    return round(sum(c in "aeiou" for c in letters) / len(letters), 2)
""", [("vowel_ratio('hello')", 0.4), ("vowel_ratio('')", 0.0), ("vowel_ratio('aeiou')", 1.0)], "It counts vowels among letters only, and avoids division by zero for empty text.")
g("range_summary", "turn a sorted list of integers into ranges like '1-3, 5, 7-8'", """
def range_summary(nums):
    if not nums:
        return ""
    parts, start, prev = [], nums[0], nums[0]
    for n in nums[1:] + [None]:
        if n is not None and n == prev + 1:
            prev = n
            continue
        parts.append(str(start) if start == prev else f"{start}-{prev}")
        if n is not None:
            start = prev = n
    return ", ".join(parts)
""", [("range_summary([1, 2, 3, 5, 7, 8])", "1-3, 5, 7-8"), ("range_summary([4])", "4"), ("range_summary([])", "")], "It tracks the start of the current run and closes it whenever the next number isn't consecutive.")
g("is_perfect_number", "check whether a number equals the sum of its proper divisors", """
def is_perfect_number(n):
    if n < 2:
        return False
    total = 1
    i = 2
    while i * i <= n:
        if n % i == 0:
            total += i
            if i != n // i:
                total += n // i
        i += 1
    return total == n
""", [("is_perfect_number(28)", True), ("is_perfect_number(12)", False), ("is_perfect_number(6)", True)], "Divisors are found in pairs up to the square root, and a perfect square's root is counted only once.")
g("title_to_initials", "turn a full name into initials", """
def title_to_initials(name):
    return "".join(part[0].upper() + "." for part in name.split() if part)
""", [("title_to_initials('ada lovelace')", "A.L."), ("title_to_initials('  Grace  Brewster Hopper ')", "G.B.H.")], "Splitting on whitespace ignores extra spaces, and each part contributes its first letter.")
g("rotate_matrix", "rotate a square matrix 90 degrees clockwise", """
def rotate_matrix(m):
    return [list(row) for row in zip(*m[::-1])]
""", [("rotate_matrix([[1,2],[3,4]])", [[3, 1], [4, 2]]), ("rotate_matrix([[1,2,3],[4,5,6],[7,8,9]])", [[7, 4, 1], [8, 5, 2], [9, 6, 3]])], "Reversing the rows and then transposing with `zip(*...)` rotates the matrix clockwise.")
g("spiral_order", "return the elements of a matrix in spiral order", """
def spiral_order(m):
    out = []
    while m:
        out += m.pop(0)
        m = [list(r) for r in zip(*m)][::-1]
    return out
""", [("spiral_order([[1,2,3],[4,5,6],[7,8,9]])", [1, 2, 3, 6, 9, 8, 7, 4, 5]), ("spiral_order([[1,2],[3,4]])", [1, 2, 4, 3])], "Take the top row, then rotate the remainder counter-clockwise so the next edge becomes the top row, and repeat.")
g("is_valid_sudoku_row", "check whether a list of 9 numbers has no repeated digit 1-9 (ignoring 0 for blanks)", """
def valid_row(row):
    digits = [x for x in row if x != 0]
    return len(digits) == len(set(digits)) and all(1 <= x <= 9 for x in digits)
""", [("valid_row([5, 3, 0, 0, 7, 0, 0, 0, 0])", True), ("valid_row([5, 3, 5, 0, 7, 0, 0, 0, 0])", False)], "Zeros are blanks, so the remaining digits must all be unique and between 1 and 9.")
g("binary_gap", "find the longest run of zeros between ones in the binary form of a number", """
def binary_gap(n):
    gaps = bin(n)[2:].strip("0").split("1")
    return max((len(g) for g in gaps), default=0)
""", [("binary_gap(9)", 2), ("binary_gap(529)", 4), ("binary_gap(15)", 0), ("binary_gap(32)", 0)], "Trailing zeros are stripped because a gap must be closed by a one on both sides; then splitting on '1' leaves the gaps.")
g("count_substring", "count overlapping occurrences of a substring", """
def count_substring(text, sub):
    if not sub:
        return 0
    return sum(text.startswith(sub, i) for i in range(len(text) - len(sub) + 1))
""", [("count_substring('aaaa', 'aa')", 3), ("count_substring('banana', 'ana')", 2), ("count_substring('abc', '')", 0)], "`str.count` skips overlaps; checking `startswith` at each index finds them all.")
g("two_sorted_median", "find the median of two sorted lists combined", """
def combined_median(a, b):
    merged = sorted(a + b)
    n = len(merged)
    mid = n // 2
    return merged[mid] if n % 2 else (merged[mid - 1] + merged[mid]) / 2
""", [("combined_median([1, 3], [2])", 2), ("combined_median([1, 2], [3, 4])", 2.5)], "Merging and sorting is the simplest approach; an O(log n) version exists but is much harder to get right.")
g("camel_to_words", "split a camelCase identifier into lowercase words", """
import re

def camel_to_words(name):
    return [w.lower() for w in re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", name)]
""", [("camel_to_words('parseHTTPResponse')", ["parse", "http", "response"]), ("camel_to_words('userId')", ["user", "id"])], "The regex finds either a capitalised/lowercase chunk or a run of capitals that isn't followed by a lowercase letter, which keeps acronyms together.")
g("flatten_once", "flatten a list of lists by one level", """
from itertools import chain

def flatten_once(lists):
    return list(chain.from_iterable(lists))
""", [("flatten_once([[1, 2], [3], [4, 5]])", [1, 2, 3, 4, 5]), ("flatten_once([])", [])], "`chain.from_iterable` lazily joins the sublists and is faster than repeated list addition.")
g("most_common_char", "return the most common character in a string and its count (first one on ties)", """
from collections import Counter

def most_common_char(s):
    if not s:
        return None
    ch, n = Counter(s).most_common(1)[0]
    return ch, n
""", [("most_common_char('abracadabra')", ("a", 5)), ("most_common_char('')", None)], "`Counter.most_common` sorts by count and keeps insertion order for ties, so the first-seen character wins.")
g("fizz_sum", "sum the numbers from 1 to n that are not divisible by 3", """
def sum_not_divisible_by_3(n):
    return sum(i for i in range(1, n + 1) if i % 3)
""", [("sum_not_divisible_by_3(10)", 37), ("sum_not_divisible_by_3(0)", 0)], "`i % 3` is truthy for non-multiples, so it doubles as the filter.")
g("pair_up", "pair consecutive items of a list into tuples, dropping a trailing odd item", """
def pair_up(items):
    return list(zip(items[::2], items[1::2]))
""", [("pair_up([1, 2, 3, 4, 5])", [(1, 2), (3, 4)]), ("pair_up([])", [])], "Zipping the even-position items with the odd-position items makes pairs and stops at the shorter list.")
g("difference_of_squares", "compute the difference between the square of the sum and the sum of the squares of the first n numbers", """
def difference_of_squares(n):
    return sum(range(1, n + 1)) ** 2 - sum(i * i for i in range(1, n + 1))
""", [("difference_of_squares(10)", 2640), ("difference_of_squares(1)", 0)], "For n = 10 the square of the sum is 3025 and the sum of squares is 385, giving 2640.")
g("leap_years_between", "list the leap years between two years inclusive", """
def leap_years_between(a, b):
    return [y for y in range(a, b + 1) if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)]
""", [("leap_years_between(1896, 1912)", [1896, 1904, 1908, 1912]), ("leap_years_between(1999, 2001)", [2000])], "It applies the full Gregorian rule, so 1900 is excluded and 2000 is included.")
g("hex_to_rgb", "convert a hex colour like #1a2b3c to an (r, g, b) tuple", """
def hex_to_rgb(color):
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if len(c) != 6:
        raise ValueError("invalid hex colour")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
""", [("hex_to_rgb('#1a2b3c')", (26, 43, 60)), ("hex_to_rgb('fff')", (255, 255, 255))], "It expands the 3-digit shorthand, then reads each pair of hex digits as a base-16 number.")
g("rgb_to_hex", "convert (r, g, b) values to a hex colour string", """
def rgb_to_hex(r, g, b):
    for v in (r, g, b):
        if not 0 <= v <= 255:
            raise ValueError("components must be 0-255")
    return f"#{r:02x}{g:02x}{b:02x}"
""", [("rgb_to_hex(26, 43, 60)", "#1a2b3c"), ("rgb_to_hex(0, 0, 0)", "#000000")], "The format spec `02x` writes two lowercase hex digits with a leading zero if needed.")
g("simple_interest_table", "return the balance after each year of compound interest as a list", """
def balance_by_year(principal, rate, years):
    out = []
    balance = principal
    for _ in range(years):
        balance = round(balance * (1 + rate), 2)
        out.append(balance)
    return out
""", [("balance_by_year(1000, 0.1, 3)", [1100.0, 1210.0, 1331.0])], "Each year multiplies the previous balance by 1 + rate; rounding to cents at each step mirrors how accounts post interest.")
g("histogram_text", "build a text histogram of counts, one line per item", """
def histogram_text(counts):
    width = max(len(str(k)) for k in counts) if counts else 0
    return "\\n".join(f"{str(k).ljust(width)} | {'#' * v}" for k, v in counts.items())
""", [("histogram_text({'a': 3, 'bb': 1})", "a  | ###\nbb | #")], "Labels are padded to the same width so the bars line up.")
g("validate_time", "check that a string is a valid 24-hour time HH:MM", """
import re

def validate_time(s):
    m = re.fullmatch(r"([01]\\d|2[0-3]):([0-5]\\d)", s)
    return bool(m)
""", [("validate_time('23:59')", True), ("validate_time('24:00')", False), ("validate_time('9:30')", False)], "The regex restricts hours to 00-23 and minutes to 00-59 with two digits each.")
g("minutes_to_hhmm", "format a number of minutes as H:MM", """
def minutes_to_hhmm(total):
    if total < 0:
        raise ValueError("negative minutes")
    return f"{total // 60}:{total % 60:02d}"
""", [("minutes_to_hhmm(135)", "2:15"), ("minutes_to_hhmm(60)", "1:00"), ("minutes_to_hhmm(5)", "0:05")], "`divmod`-style arithmetic splits hours and minutes, and `:02d` pads the minutes.")
g("find_all_indices", "return every index at which a value occurs in a list", """
def find_all_indices(items, target):
    return [i for i, x in enumerate(items) if x == target]
""", [("find_all_indices([1, 2, 1, 3, 1], 1)", [0, 2, 4]), ("find_all_indices([], 5)", [])], "`enumerate` provides index-value pairs, and the comprehension keeps indices where the value matches.")
g("clamp_list", "clamp every number in a list to a range", """
def clamp_list(nums, lo, hi):
    return [max(lo, min(x, hi)) for x in nums]
""", [("clamp_list([-5, 3, 12], 0, 10)", [0, 3, 10])], "Each value is limited to be no lower than `lo` and no higher than `hi`.")
g("weighted_average", "compute a weighted average of values", """
def weighted_average(values, weights):
    if len(values) != len(weights) or not values:
        raise ValueError("values and weights must be non-empty and equal length")
    total = sum(weights)
    if total == 0:
        raise ValueError("weights sum to zero")
    return sum(v * w for v, w in zip(values, weights)) / total
""", [("weighted_average([80, 90], [1, 3])", 87.5), ("weighted_average([5], [2])", 5.0)], "Multiply each value by its weight, sum the products and divide by the total weight. Zero total weight raises instead of dividing by zero.")
g("stack_min", "implement a stack that can return its minimum in constant time", """
class MinStack:
    def __init__(self):
        self._items = []
        self._mins = []

    def push(self, x):
        self._items.append(x)
        self._mins.append(x if not self._mins else min(x, self._mins[-1]))

    def pop(self):
        self._mins.pop()
        return self._items.pop()

    def minimum(self):
        return self._mins[-1]
""", [("(lambda s: (s.push(3), s.push(1), s.push(2), s.minimum(), s.pop(), s.pop(), s.minimum()))(MinStack())[3:]", (1, 2, 1, 3))], "A second stack records the minimum at each depth, so the current minimum is always on top of it.")
g("event_emitter", "implement a tiny event emitter class with on and emit", """
class Emitter:
    def __init__(self):
        self._handlers = {}

    def on(self, event, fn):
        self._handlers.setdefault(event, []).append(fn)

    def emit(self, event, *args):
        return [fn(*args) for fn in self._handlers.get(event, [])]
""", [("(lambda e: (e.on('x', lambda a: a + 1), e.on('x', lambda a: a * 2), e.emit('x', 5), e.emit('y')))(Emitter())[2:]", ([6, 10], []))], "Handlers are stored per event name, and `emit` calls them in registration order, returning their results.")
g("simple_cache_dict", "implement a function wrapper that caches results by argument", """
def cached(func):
    store = {}

    def wrapper(*args):
        if args not in store:
            store[args] = func(*args)
        return store[args]

    wrapper.store = store
    return wrapper
""", [("(lambda c: (c(2), c(2), len(c.store)))(cached(lambda x: x * x))", (4, 4, 1))], "Arguments become the dictionary key, so repeated calls with the same arguments reuse the stored result. Arguments must be hashable.")
g("run_length_decode", "decode a run-length encoded string like 'a3b1c2'", """
import re

def run_length_decode(s):
    return "".join(ch * int(n) for ch, n in re.findall(r"(\\D)(\\d+)", s))
""", [("run_length_decode('a3b1c2')", "aaabcc"), ("run_length_decode('')", "")], "The regex pairs each non-digit character with its count, and string multiplication repeats it.")
g("count_vowels_consonants", "count vowels and consonants in a string", """
def count_vowels_consonants(s):
    letters = [c for c in s.lower() if c.isalpha()]
    vowels = sum(c in "aeiou" for c in letters)
    return vowels, len(letters) - vowels
""", [("count_vowels_consonants('Hello, World!')", (3, 7)), ("count_vowels_consonants('')", (0, 0))], "Non-letters are ignored, and consonants are the letters that aren't vowels.")
g("next_power_of_two", "return the smallest power of two greater than or equal to n", """
def next_power_of_two(n):
    if n < 1:
        return 1
    return 1 << (n - 1).bit_length()
""", [("next_power_of_two(5)", 8), ("next_power_of_two(16)", 16), ("next_power_of_two(1)", 1)], "`(n - 1).bit_length()` gives the number of bits needed, and shifting 1 left by that many places gives the power of two.")
g("gcd_lcm_pair", "return both gcd and lcm of two numbers", """
from math import gcd

def gcd_lcm(a, b):
    g = gcd(a, b)
    return g, (a // g * b if g else 0)
""", [("gcd_lcm(12, 18)", (6, 36)), ("gcd_lcm(0, 5)", (5, 0))], "The lcm is a * b / gcd; dividing first avoids a huge intermediate product.")
g("age_from_birthdate", "compute a person's age in years from ISO birthdate and a reference date", """
from datetime import date

def age_on(birth, today):
    b = date.fromisoformat(birth)
    t = date.fromisoformat(today)
    return t.year - b.year - ((t.month, t.day) < (b.month, b.day))
""", [("age_on('2000-06-15', '2024-06-14')", 23), ("age_on('2000-06-15', '2024-06-15')", 24)], "Subtract the years, then take one off if the birthday hasn't happened yet this year.")
g("unique_letters_sorted", "return the unique letters of a string in alphabetical order", """
def unique_letters_sorted(s):
    return "".join(sorted(set(c for c in s.lower() if c.isalpha())))
""", [("unique_letters_sorted('Hello World')", "dehlorw")], "A set removes duplicates, and sorting gives alphabetical order.")
g("chunked_sum", "sum a list in chunks of n and return the chunk totals", """
def chunk_sums(nums, n):
    return [sum(nums[i:i + n]) for i in range(0, len(nums), n)]
""", [("chunk_sums([1, 2, 3, 4, 5], 2)", [3, 7, 5])], "Slicing every n items gives the chunks; the last may be shorter.")
