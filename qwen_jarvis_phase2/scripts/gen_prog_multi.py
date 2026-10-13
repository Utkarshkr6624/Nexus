"""Category 4a: multi-language algorithm examples. Every code block is COMPILED/RUN and its output is compared with a Python reference.
Languages: python, javascript, typescript, java, cpp, c.  Snippets that fail verification are dropped (and counted)."""
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from lib import *

CAT = "programming"
rng = seeded("prog_multi")
R = rng
SCRATCH = Path(os.environ.get("P2_SCRATCH", tempfile.gettempdir())) / "p2_verify"
SCRATCH.mkdir(parents=True, exist_ok=True)
LANGS = ["python", "javascript", "typescript", "java", "cpp", "c"]
LABEL = {"python": "Python", "javascript": "JavaScript", "typescript": "TypeScript", "java": "Java", "cpp": "C++", "c": "C"}


def camel(s):
    p = s.split("_")
    return p[0] + "".join(x.capitalize() for x in p[1:])


# Each algo: desc, expl, cx, shown(call text), expect (normalised tokens), code{lang}, main{lang}
A = {}

A["is_palindrome"] = dict(
    desc="check whether a string is a palindrome, ignoring letter case", cx="O(n) time, O(n) space for the lowercase copy (O(1) extra if you compare in place).",
    expl="Lower-case the string, then compare it with its reverse (or walk two pointers inward).", shown='is_palindrome("Racecar")', expect="true",
    code={
     "python": 'def is_palindrome(s):\n    t = s.lower()\n    return t == t[::-1]',
     "javascript": 'function isPalindrome(s) {\n  const t = s.toLowerCase();\n  return t === t.split("").reverse().join("");\n}',
     "typescript": 'function isPalindrome(s: string): boolean {\n  const t = s.toLowerCase();\n  return t === t.split("").reverse().join("");\n}',
     "java": 'static boolean isPalindrome(String s) {\n    String t = s.toLowerCase();\n    return new StringBuilder(t).reverse().toString().equals(t);\n}',
     "cpp": 'bool isPalindrome(string s) {\n    transform(s.begin(), s.end(), s.begin(), ::tolower);\n    string r(s.rbegin(), s.rend());\n    return s == r;\n}',
     "c": 'int is_palindrome(const char *s) {\n    int i = 0, j = (int)strlen(s) - 1;\n    while (i < j) {\n        if (tolower((unsigned char)s[i]) != tolower((unsigned char)s[j])) return 0;\n        i++; j--;\n    }\n    return 1;\n}'},
    main={"python": 'print(is_palindrome("Racecar"))', "javascript": 'console.log(isPalindrome("Racecar"));', "typescript": 'console.log(isPalindrome("Racecar"));',
          "java": 'System.out.println(isPalindrome("Racecar"));', "cpp": 'cout << boolalpha << isPalindrome("Racecar") << endl;', "c": 'printf("%s\\n", is_palindrome("Racecar") ? "true" : "false");'})

A["fibonacci"] = dict(
    desc="return the n-th Fibonacci number (0, 1, 1, 2, 3, ...) iteratively", cx="O(n) time, O(1) space.",
    expl="Keep the previous two values and roll them forward n times; this avoids the exponential cost of naive recursion.", shown="fibonacci(10)", expect="55",
    code={
     "python": 'def fibonacci(n):\n    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a + b\n    return a',
     "javascript": 'function fibonacci(n) {\n  let a = 0, b = 1;\n  for (let i = 0; i < n; i++) {\n    [a, b] = [b, a + b];\n  }\n  return a;\n}',
     "typescript": 'function fibonacci(n: number): number {\n  let a = 0, b = 1;\n  for (let i = 0; i < n; i++) {\n    [a, b] = [b, a + b];\n  }\n  return a;\n}',
     "java": 'static long fibonacci(int n) {\n    long a = 0, b = 1;\n    for (int i = 0; i < n; i++) {\n        long next = a + b;\n        a = b;\n        b = next;\n    }\n    return a;\n}',
     "cpp": 'long long fibonacci(int n) {\n    long long a = 0, b = 1;\n    for (int i = 0; i < n; i++) {\n        long long next = a + b;\n        a = b;\n        b = next;\n    }\n    return a;\n}',
     "c": 'long long fibonacci(int n) {\n    long long a = 0, b = 1;\n    for (int i = 0; i < n; i++) {\n        long long next = a + b;\n        a = b;\n        b = next;\n    }\n    return a;\n}'},
    main={"python": 'print(fibonacci(10))', "javascript": 'console.log(fibonacci(10));', "typescript": 'console.log(fibonacci(10));', "java": 'System.out.println(fibonacci(10));',
          "cpp": 'cout << fibonacci(10) << endl;', "c": 'printf("%lld\\n", fibonacci(10));'})

A["gcd"] = dict(
    desc="compute the greatest common divisor of two integers", cx="O(log(min(a, b))) time, O(1) space.",
    expl="Euclid's algorithm: replace (a, b) with (b, a mod b) until b is zero; the remaining a is the gcd.", shown="gcd(48, 18)", expect="6",
    code={
     "python": 'def gcd(a, b):\n    while b:\n        a, b = b, a % b\n    return abs(a)',
     "javascript": 'function gcd(a, b) {\n  while (b !== 0) {\n    [a, b] = [b, a % b];\n  }\n  return Math.abs(a);\n}',
     "typescript": 'function gcd(a: number, b: number): number {\n  while (b !== 0) {\n    [a, b] = [b, a % b];\n  }\n  return Math.abs(a);\n}',
     "java": 'static int gcd(int a, int b) {\n    while (b != 0) {\n        int t = a % b;\n        a = b;\n        b = t;\n    }\n    return Math.abs(a);\n}',
     "cpp": 'int gcd(int a, int b) {\n    while (b != 0) {\n        int t = a % b;\n        a = b;\n        b = t;\n    }\n    return abs(a);\n}',
     "c": 'int gcd(int a, int b) {\n    while (b != 0) {\n        int t = a % b;\n        a = b;\n        b = t;\n    }\n    return abs(a);\n}'},
    main={"python": 'print(gcd(48, 18))', "javascript": 'console.log(gcd(48, 18));', "typescript": 'console.log(gcd(48, 18));', "java": 'System.out.println(gcd(48, 18));',
          "cpp": 'cout << gcd(48, 18) << endl;', "c": 'printf("%d\\n", gcd(48, 18));'})

A["is_prime"] = dict(
    desc="test whether an integer is prime", cx="O(sqrt(n)) time, O(1) space.",
    expl="Reject numbers below 2, then try divisors up to the square root of n; if none divides n evenly it is prime.", shown="is_prime(97)", expect="true",
    code={
     "python": 'def is_prime(n):\n    if n < 2:\n        return False\n    i = 2\n    while i * i <= n:\n        if n % i == 0:\n            return False\n        i += 1\n    return True',
     "javascript": 'function isPrime(n) {\n  if (n < 2) return false;\n  for (let i = 2; i * i <= n; i++) {\n    if (n % i === 0) return false;\n  }\n  return true;\n}',
     "typescript": 'function isPrime(n: number): boolean {\n  if (n < 2) return false;\n  for (let i = 2; i * i <= n; i++) {\n    if (n % i === 0) return false;\n  }\n  return true;\n}',
     "java": 'static boolean isPrime(int n) {\n    if (n < 2) return false;\n    for (int i = 2; (long) i * i <= n; i++) {\n        if (n % i == 0) return false;\n    }\n    return true;\n}',
     "cpp": 'bool isPrime(int n) {\n    if (n < 2) return false;\n    for (long long i = 2; i * i <= n; i++) {\n        if (n % i == 0) return false;\n    }\n    return true;\n}',
     "c": 'int is_prime(int n) {\n    if (n < 2) return 0;\n    for (long long i = 2; i * i <= n; i++) {\n        if (n % i == 0) return 0;\n    }\n    return 1;\n}'},
    main={"python": 'print(is_prime(97))', "javascript": 'console.log(isPrime(97));', "typescript": 'console.log(isPrime(97));', "java": 'System.out.println(isPrime(97));',
          "cpp": 'cout << boolalpha << isPrime(97) << endl;', "c": 'printf("%s\\n", is_prime(97) ? "true" : "false");'})

A["reverse_string"] = dict(
    desc="reverse a string", cx="O(n) time and space.",
    expl="Build the characters in the opposite order. Most languages have a built-in or idiom for this; the explicit loop shows what is happening.", shown='reverse_string("hello")', expect="olleh",
    code={
     "python": 'def reverse_string(s):\n    return s[::-1]',
     "javascript": 'function reverseString(s) {\n  return s.split("").reverse().join("");\n}',
     "typescript": 'function reverseString(s: string): string {\n  return s.split("").reverse().join("");\n}',
     "java": 'static String reverseString(String s) {\n    return new StringBuilder(s).reverse().toString();\n}',
     "cpp": 'string reverseString(string s) {\n    reverse(s.begin(), s.end());\n    return s;\n}',
     "c": 'void reverse_string(char *s) {\n    int i = 0, j = (int)strlen(s) - 1;\n    while (i < j) {\n        char t = s[i];\n        s[i++] = s[j];\n        s[j--] = t;\n    }\n}'},
    main={"python": 'print(reverse_string("hello"))', "javascript": 'console.log(reverseString("hello"));', "typescript": 'console.log(reverseString("hello"));',
          "java": 'System.out.println(reverseString("hello"));', "cpp": 'cout << reverseString("hello") << endl;', "c": 'char s[] = "hello";\n    reverse_string(s);\n    printf("%s\\n", s);'})

A["binary_search"] = dict(
    desc="find the index of a target in a sorted array, or -1 if it is missing", cx="O(log n) time, O(1) space.",
    expl="Keep a low and high bound, check the middle element, and discard the half that cannot contain the target. The list must already be sorted.", shown="binary_search([1, 3, 5, 7, 9, 11], 7)", expect="3",
    code={
     "python": 'def binary_search(arr, target):\n    lo, hi = 0, len(arr) - 1\n    while lo <= hi:\n        mid = (lo + hi) // 2\n        if arr[mid] == target:\n            return mid\n        if arr[mid] < target:\n            lo = mid + 1\n        else:\n            hi = mid - 1\n    return -1',
     "javascript": 'function binarySearch(arr, target) {\n  let lo = 0, hi = arr.length - 1;\n  while (lo <= hi) {\n    const mid = Math.floor((lo + hi) / 2);\n    if (arr[mid] === target) return mid;\n    if (arr[mid] < target) lo = mid + 1;\n    else hi = mid - 1;\n  }\n  return -1;\n}',
     "typescript": 'function binarySearch(arr: number[], target: number): number {\n  let lo = 0, hi = arr.length - 1;\n  while (lo <= hi) {\n    const mid = Math.floor((lo + hi) / 2);\n    if (arr[mid] === target) return mid;\n    if (arr[mid] < target) lo = mid + 1;\n    else hi = mid - 1;\n  }\n  return -1;\n}',
     "java": 'static int binarySearch(int[] arr, int target) {\n    int lo = 0, hi = arr.length - 1;\n    while (lo <= hi) {\n        int mid = lo + (hi - lo) / 2;\n        if (arr[mid] == target) return mid;\n        if (arr[mid] < target) lo = mid + 1;\n        else hi = mid - 1;\n    }\n    return -1;\n}',
     "cpp": 'int binarySearch(const vector<int>& arr, int target) {\n    int lo = 0, hi = (int)arr.size() - 1;\n    while (lo <= hi) {\n        int mid = lo + (hi - lo) / 2;\n        if (arr[mid] == target) return mid;\n        if (arr[mid] < target) lo = mid + 1;\n        else hi = mid - 1;\n    }\n    return -1;\n}',
     "c": 'int binary_search(const int *arr, int n, int target) {\n    int lo = 0, hi = n - 1;\n    while (lo <= hi) {\n        int mid = lo + (hi - lo) / 2;\n        if (arr[mid] == target) return mid;\n        if (arr[mid] < target) lo = mid + 1;\n        else hi = mid - 1;\n    }\n    return -1;\n}'},
    main={"python": 'print(binary_search([1, 3, 5, 7, 9, 11], 7))', "javascript": 'console.log(binarySearch([1, 3, 5, 7, 9, 11], 7));', "typescript": 'console.log(binarySearch([1, 3, 5, 7, 9, 11], 7));',
          "java": 'System.out.println(binarySearch(new int[]{1, 3, 5, 7, 9, 11}, 7));', "cpp": 'cout << binarySearch({1, 3, 5, 7, 9, 11}, 7) << endl;',
          "c": 'int arr[] = {1, 3, 5, 7, 9, 11};\n    printf("%d\\n", binary_search(arr, 6, 7));'})

A["factorial"] = dict(
    desc="compute n factorial", cx="O(n) time, O(1) space. Watch for overflow: 20! is the largest that fits in a signed 64-bit integer.",
    expl="Multiply the numbers from 2 up to n. 0! and 1! are both 1.", shown="factorial(10)", expect="3628800",
    code={
     "python": 'def factorial(n):\n    result = 1\n    for i in range(2, n + 1):\n        result *= i\n    return result',
     "javascript": 'function factorial(n) {\n  let result = 1;\n  for (let i = 2; i <= n; i++) result *= i;\n  return result;\n}',
     "typescript": 'function factorial(n: number): number {\n  let result = 1;\n  for (let i = 2; i <= n; i++) result *= i;\n  return result;\n}',
     "java": 'static long factorial(int n) {\n    long result = 1;\n    for (int i = 2; i <= n; i++) result *= i;\n    return result;\n}',
     "cpp": 'long long factorial(int n) {\n    long long result = 1;\n    for (int i = 2; i <= n; i++) result *= i;\n    return result;\n}',
     "c": 'long long factorial(int n) {\n    long long result = 1;\n    for (int i = 2; i <= n; i++) result *= i;\n    return result;\n}'},
    main={"python": 'print(factorial(10))', "javascript": 'console.log(factorial(10));', "typescript": 'console.log(factorial(10));', "java": 'System.out.println(factorial(10));',
          "cpp": 'cout << factorial(10) << endl;', "c": 'printf("%lld\\n", factorial(10));'})

A["count_vowels"] = dict(
    desc="count the vowels in a string", cx="O(n) time, O(1) space.",
    expl="Walk the characters and count those that are a, e, i, o or u, in either case.", shown='count_vowels("Programming Language")', expect="7",
    code={
     "python": 'def count_vowels(s):\n    return sum(1 for ch in s.lower() if ch in "aeiou")',
     "javascript": 'function countVowels(s) {\n  let count = 0;\n  for (const ch of s.toLowerCase()) {\n    if ("aeiou".includes(ch)) count++;\n  }\n  return count;\n}',
     "typescript": 'function countVowels(s: string): number {\n  let count = 0;\n  for (const ch of s.toLowerCase()) {\n    if ("aeiou".includes(ch)) count++;\n  }\n  return count;\n}',
     "java": 'static int countVowels(String s) {\n    int count = 0;\n    for (char ch : s.toLowerCase().toCharArray()) {\n        if ("aeiou".indexOf(ch) >= 0) count++;\n    }\n    return count;\n}',
     "cpp": 'int countVowels(const string& s) {\n    int count = 0;\n    for (char ch : s) {\n        if (string("aeiou").find((char)tolower(ch)) != string::npos) count++;\n    }\n    return count;\n}',
     "c": 'int count_vowels(const char *s) {\n    int count = 0;\n    for (; *s; s++) {\n        if (strchr("aeiou", tolower((unsigned char)*s)) && *s != 0) count++;\n    }\n    return count;\n}'},
    main={"python": 'print(count_vowels("Programming Language"))', "javascript": 'console.log(countVowels("Programming Language"));', "typescript": 'console.log(countVowels("Programming Language"));',
          "java": 'System.out.println(countVowels("Programming Language"));', "cpp": 'cout << countVowels("Programming Language") << endl;', "c": 'printf("%d\\n", count_vowels("Programming Language"));'})

A["max_in_array"] = dict(
    desc="find the largest value in a non-empty array", cx="O(n) time, O(1) space.",
    expl="Start with the first element as the best so far and update it whenever a larger value appears.", shown="max_in_array([4, 17, 9, 2, 25, 8])", expect="25",
    code={
     "python": 'def max_in_array(arr):\n    best = arr[0]\n    for x in arr[1:]:\n        if x > best:\n            best = x\n    return best',
     "javascript": 'function maxInArray(arr) {\n  let best = arr[0];\n  for (const x of arr) {\n    if (x > best) best = x;\n  }\n  return best;\n}',
     "typescript": 'function maxInArray(arr: number[]): number {\n  let best = arr[0];\n  for (const x of arr) {\n    if (x > best) best = x;\n  }\n  return best;\n}',
     "java": 'static int maxInArray(int[] arr) {\n    int best = arr[0];\n    for (int x : arr) {\n        if (x > best) best = x;\n    }\n    return best;\n}',
     "cpp": 'int maxInArray(const vector<int>& arr) {\n    int best = arr[0];\n    for (int x : arr) {\n        if (x > best) best = x;\n    }\n    return best;\n}',
     "c": 'int max_in_array(const int *arr, int n) {\n    int best = arr[0];\n    for (int i = 1; i < n; i++) {\n        if (arr[i] > best) best = arr[i];\n    }\n    return best;\n}'},
    main={"python": 'print(max_in_array([4, 17, 9, 2, 25, 8]))', "javascript": 'console.log(maxInArray([4, 17, 9, 2, 25, 8]));', "typescript": 'console.log(maxInArray([4, 17, 9, 2, 25, 8]));',
          "java": 'System.out.println(maxInArray(new int[]{4, 17, 9, 2, 25, 8}));', "cpp": 'cout << maxInArray({4, 17, 9, 2, 25, 8}) << endl;',
          "c": 'int arr[] = {4, 17, 9, 2, 25, 8};\n    printf("%d\\n", max_in_array(arr, 6));'})

A["bubble_sort"] = dict(
    desc="sort an array of integers with bubble sort", cx="O(n²) time, O(1) extra space; fine for learning, but use the standard library sort in real code.",
    expl="Repeatedly sweep through the array, swapping neighbours that are out of order; each pass pushes the largest remaining value to the end. Stop early if a pass makes no swaps.", shown="bubble_sort([5, 2, 9, 1, 7])", expect="1 2 5 7 9",
    code={
     "python": 'def bubble_sort(arr):\n    arr = list(arr)\n    for end in range(len(arr) - 1, 0, -1):\n        swapped = False\n        for i in range(end):\n            if arr[i] > arr[i + 1]:\n                arr[i], arr[i + 1] = arr[i + 1], arr[i]\n                swapped = True\n        if not swapped:\n            break\n    return arr',
     "javascript": 'function bubbleSort(input) {\n  const arr = [...input];\n  for (let end = arr.length - 1; end > 0; end--) {\n    let swapped = false;\n    for (let i = 0; i < end; i++) {\n      if (arr[i] > arr[i + 1]) {\n        [arr[i], arr[i + 1]] = [arr[i + 1], arr[i]];\n        swapped = true;\n      }\n    }\n    if (!swapped) break;\n  }\n  return arr;\n}',
     "typescript": 'function bubbleSort(input: number[]): number[] {\n  const arr = [...input];\n  for (let end = arr.length - 1; end > 0; end--) {\n    let swapped = false;\n    for (let i = 0; i < end; i++) {\n      if (arr[i] > arr[i + 1]) {\n        [arr[i], arr[i + 1]] = [arr[i + 1], arr[i]];\n        swapped = true;\n      }\n    }\n    if (!swapped) break;\n  }\n  return arr;\n}',
     "java": 'static int[] bubbleSort(int[] input) {\n    int[] arr = input.clone();\n    for (int end = arr.length - 1; end > 0; end--) {\n        boolean swapped = false;\n        for (int i = 0; i < end; i++) {\n            if (arr[i] > arr[i + 1]) {\n                int t = arr[i];\n                arr[i] = arr[i + 1];\n                arr[i + 1] = t;\n                swapped = true;\n            }\n        }\n        if (!swapped) break;\n    }\n    return arr;\n}',
     "cpp": 'vector<int> bubbleSort(vector<int> arr) {\n    for (int end = (int)arr.size() - 1; end > 0; end--) {\n        bool swapped = false;\n        for (int i = 0; i < end; i++) {\n            if (arr[i] > arr[i + 1]) {\n                swap(arr[i], arr[i + 1]);\n                swapped = true;\n            }\n        }\n        if (!swapped) break;\n    }\n    return arr;\n}',
     "c": 'void bubble_sort(int *arr, int n) {\n    for (int end = n - 1; end > 0; end--) {\n        int swapped = 0;\n        for (int i = 0; i < end; i++) {\n            if (arr[i] > arr[i + 1]) {\n                int t = arr[i];\n                arr[i] = arr[i + 1];\n                arr[i + 1] = t;\n                swapped = 1;\n            }\n        }\n        if (!swapped) break;\n    }\n}'},
    main={"python": 'print(bubble_sort([5, 2, 9, 1, 7]))', "javascript": 'console.log(bubbleSort([5, 2, 9, 1, 7]));', "typescript": 'console.log(bubbleSort([5, 2, 9, 1, 7]));',
          "java": 'System.out.println(Arrays.toString(bubbleSort(new int[]{5, 2, 9, 1, 7})));', "cpp": 'for (int x : bubbleSort({5, 2, 9, 1, 7})) cout << x << " ";\n    cout << endl;',
          "c": 'int arr[] = {5, 2, 9, 1, 7};\n    bubble_sort(arr, 5);\n    for (int i = 0; i < 5; i++) printf("%d ", arr[i]);\n    printf("\\n");'})

A["sum_digits"] = dict(
    desc="add up the digits of a non-negative integer", cx="O(d) time where d is the number of digits, O(1) space.",
    expl="Repeatedly take the last digit with n % 10 and drop it with integer division by 10.", shown="sum_digits(98765)", expect="35",
    code={
     "python": 'def sum_digits(n):\n    total = 0\n    while n > 0:\n        total += n % 10\n        n //= 10\n    return total',
     "javascript": 'function sumDigits(n) {\n  let total = 0;\n  while (n > 0) {\n    total += n % 10;\n    n = Math.floor(n / 10);\n  }\n  return total;\n}',
     "typescript": 'function sumDigits(n: number): number {\n  let total = 0;\n  while (n > 0) {\n    total += n % 10;\n    n = Math.floor(n / 10);\n  }\n  return total;\n}',
     "java": 'static int sumDigits(int n) {\n    int total = 0;\n    while (n > 0) {\n        total += n % 10;\n        n /= 10;\n    }\n    return total;\n}',
     "cpp": 'int sumDigits(int n) {\n    int total = 0;\n    while (n > 0) {\n        total += n % 10;\n        n /= 10;\n    }\n    return total;\n}',
     "c": 'int sum_digits(int n) {\n    int total = 0;\n    while (n > 0) {\n        total += n % 10;\n        n /= 10;\n    }\n    return total;\n}'},
    main={"python": 'print(sum_digits(98765))', "javascript": 'console.log(sumDigits(98765));', "typescript": 'console.log(sumDigits(98765));', "java": 'System.out.println(sumDigits(98765));',
          "cpp": 'cout << sumDigits(98765) << endl;', "c": 'printf("%d\\n", sum_digits(98765));'})

A["is_power_of_two"] = dict(
    desc="check whether a positive integer is a power of two", cx="O(1) time and space.",
    expl="A power of two has exactly one bit set, so n & (n - 1) clears that bit and leaves zero. Also require n > 0.", shown="is_power_of_two(64)", expect="true",
    code={
     "python": 'def is_power_of_two(n):\n    return n > 0 and (n & (n - 1)) == 0',
     "javascript": 'function isPowerOfTwo(n) {\n  return n > 0 && (n & (n - 1)) === 0;\n}',
     "typescript": 'function isPowerOfTwo(n: number): boolean {\n  return n > 0 && (n & (n - 1)) === 0;\n}',
     "java": 'static boolean isPowerOfTwo(int n) {\n    return n > 0 && (n & (n - 1)) == 0;\n}',
     "cpp": 'bool isPowerOfTwo(int n) {\n    return n > 0 && (n & (n - 1)) == 0;\n}',
     "c": 'int is_power_of_two(int n) {\n    return n > 0 && (n & (n - 1)) == 0;\n}'},
    main={"python": 'print(is_power_of_two(64))', "javascript": 'console.log(isPowerOfTwo(64));', "typescript": 'console.log(isPowerOfTwo(64));', "java": 'System.out.println(isPowerOfTwo(64));',
          "cpp": 'cout << boolalpha << isPowerOfTwo(64) << endl;', "c": 'printf("%s\\n", is_power_of_two(64) ? "true" : "false");'})

A["two_sum"] = dict(
    desc="find two indices whose values add up to a target", cx="O(n) time, O(n) space using a hash map (the brute-force double loop is O(n²)).",
    expl="Store each value's index in a hash map as you scan. For each number, check whether target minus that number has already been seen.", shown="two_sum([2, 7, 11, 15], 9)", expect="0 1",
    code={
     "python": 'def two_sum(nums, target):\n    seen = {}\n    for i, x in enumerate(nums):\n        if target - x in seen:\n            return [seen[target - x], i]\n        seen[x] = i\n    return []',
     "javascript": 'function twoSum(nums, target) {\n  const seen = new Map();\n  for (let i = 0; i < nums.length; i++) {\n    const need = target - nums[i];\n    if (seen.has(need)) return [seen.get(need), i];\n    seen.set(nums[i], i);\n  }\n  return [];\n}',
     "typescript": 'function twoSum(nums: number[], target: number): number[] {\n  const seen = new Map<number, number>();\n  for (let i = 0; i < nums.length; i++) {\n    const need = target - nums[i];\n    if (seen.has(need)) return [seen.get(need)!, i];\n    seen.set(nums[i], i);\n  }\n  return [];\n}',
     "java": 'static int[] twoSum(int[] nums, int target) {\n    Map<Integer, Integer> seen = new HashMap<>();\n    for (int i = 0; i < nums.length; i++) {\n        Integer j = seen.get(target - nums[i]);\n        if (j != null) return new int[]{j, i};\n        seen.put(nums[i], i);\n    }\n    return new int[0];\n}',
     "cpp": 'vector<int> twoSum(const vector<int>& nums, int target) {\n    unordered_map<int, int> seen;\n    for (int i = 0; i < (int)nums.size(); i++) {\n        auto it = seen.find(target - nums[i]);\n        if (it != seen.end()) return {it->second, i};\n        seen[nums[i]] = i;\n    }\n    return {};\n}',
     "c": 'int two_sum(const int *nums, int n, int target, int *out) {\n    for (int i = 0; i < n; i++) {\n        for (int j = i + 1; j < n; j++) {\n            if (nums[i] + nums[j] == target) {\n                out[0] = i;\n                out[1] = j;\n                return 1;\n            }\n        }\n    }\n    return 0;\n}'},
    main={"python": 'print(two_sum([2, 7, 11, 15], 9))', "javascript": 'console.log(twoSum([2, 7, 11, 15], 9));', "typescript": 'console.log(twoSum([2, 7, 11, 15], 9));',
          "java": 'System.out.println(Arrays.toString(twoSum(new int[]{2, 7, 11, 15}, 9)));', "cpp": 'for (int x : twoSum({2, 7, 11, 15}, 9)) cout << x << " ";\n    cout << endl;',
          "c": 'int nums[] = {2, 7, 11, 15};\n    int out[2];\n    if (two_sum(nums, 4, 9, out)) printf("%d %d\\n", out[0], out[1]);'})

A["remove_duplicates"] = dict(
    desc="remove duplicates from a list of integers while keeping the first occurrence order", cx="O(n) time, O(n) space.",
    expl="Track the values already seen in a set and keep only the first appearance of each.", shown="remove_duplicates([3, 1, 3, 2, 1, 4])", expect="3 1 2 4",
    code={
     "python": 'def remove_duplicates(items):\n    seen = set()\n    result = []\n    for x in items:\n        if x not in seen:\n            seen.add(x)\n            result.append(x)\n    return result',
     "javascript": 'function removeDuplicates(items) {\n  return [...new Set(items)];\n}',
     "typescript": 'function removeDuplicates(items: number[]): number[] {\n  return [...new Set(items)];\n}',
     "java": 'static List<Integer> removeDuplicates(int[] items) {\n    Set<Integer> seen = new LinkedHashSet<>();\n    for (int x : items) seen.add(x);\n    return new ArrayList<>(seen);\n}',
     "cpp": 'vector<int> removeDuplicates(const vector<int>& items) {\n    unordered_set<int> seen;\n    vector<int> result;\n    for (int x : items) {\n        if (seen.insert(x).second) result.push_back(x);\n    }\n    return result;\n}',
     "c": 'int remove_duplicates(const int *items, int n, int *out) {\n    int m = 0;\n    for (int i = 0; i < n; i++) {\n        int dup = 0;\n        for (int j = 0; j < m; j++) {\n            if (out[j] == items[i]) { dup = 1; break; }\n        }\n        if (!dup) out[m++] = items[i];\n    }\n    return m;\n}'},
    main={"python": 'print(remove_duplicates([3, 1, 3, 2, 1, 4]))', "javascript": 'console.log(removeDuplicates([3, 1, 3, 2, 1, 4]));', "typescript": 'console.log(removeDuplicates([3, 1, 3, 2, 1, 4]));',
          "java": 'System.out.println(removeDuplicates(new int[]{3, 1, 3, 2, 1, 4}));', "cpp": 'for (int x : removeDuplicates({3, 1, 3, 2, 1, 4})) cout << x << " ";\n    cout << endl;',
          "c": 'int items[] = {3, 1, 3, 2, 1, 4};\n    int out[6];\n    int m = remove_duplicates(items, 6, out);\n    for (int i = 0; i < m; i++) printf("%d ", out[i]);\n    printf("\\n");'})

A["is_anagram"] = dict(
    desc="check whether two words are anagrams of each other (case-insensitive)", cx="O(n) time with counting, O(1) extra space for a fixed alphabet (sorting is O(n log n)).",
    expl="Two strings are anagrams if they use exactly the same letters the same number of times. Count the letters of one and cancel them with the other.", shown='is_anagram("Listen", "Silent")', expect="true",
    code={
     "python": 'from collections import Counter\n\ndef is_anagram(a, b):\n    return Counter(a.lower()) == Counter(b.lower())',
     "javascript": 'function isAnagram(a, b) {\n  const norm = (s) => s.toLowerCase().split("").sort().join("");\n  return norm(a) === norm(b);\n}',
     "typescript": 'function isAnagram(a: string, b: string): boolean {\n  const norm = (s: string) => s.toLowerCase().split("").sort().join("");\n  return norm(a) === norm(b);\n}',
     "java": 'static boolean isAnagram(String a, String b) {\n    char[] x = a.toLowerCase().toCharArray();\n    char[] y = b.toLowerCase().toCharArray();\n    Arrays.sort(x);\n    Arrays.sort(y);\n    return Arrays.equals(x, y);\n}',
     "cpp": 'bool isAnagram(string a, string b) {\n    transform(a.begin(), a.end(), a.begin(), ::tolower);\n    transform(b.begin(), b.end(), b.begin(), ::tolower);\n    sort(a.begin(), a.end());\n    sort(b.begin(), b.end());\n    return a == b;\n}',
     "c": 'int is_anagram(const char *a, const char *b) {\n    int counts[256] = {0};\n    for (; *a; a++) counts[tolower((unsigned char)*a)]++;\n    for (; *b; b++) counts[tolower((unsigned char)*b)]--;\n    for (int i = 0; i < 256; i++) {\n        if (counts[i] != 0) return 0;\n    }\n    return 1;\n}'},
    main={"python": 'print(is_anagram("Listen", "Silent"))', "javascript": 'console.log(isAnagram("Listen", "Silent"));', "typescript": 'console.log(isAnagram("Listen", "Silent"));',
          "java": 'System.out.println(isAnagram("Listen", "Silent"));', "cpp": 'cout << boolalpha << isAnagram("Listen", "Silent") << endl;', "c": 'printf("%s\\n", is_anagram("Listen", "Silent") ? "true" : "false");'})

A["word_count"] = dict(
    desc="count the words in a sentence separated by whitespace", cx="O(n) time, O(1) extra space when scanning in place.",
    expl="Split on runs of whitespace and count the pieces, taking care that extra spaces do not create empty words.", shown='word_count("the quick  brown fox")', expect="4",
    code={
     "python": 'def word_count(s):\n    return len(s.split())',
     "javascript": 'function wordCount(s) {\n  const trimmed = s.trim();\n  return trimmed === "" ? 0 : trimmed.split(/\\s+/).length;\n}',
     "typescript": 'function wordCount(s: string): number {\n  const trimmed = s.trim();\n  return trimmed === "" ? 0 : trimmed.split(/\\s+/).length;\n}',
     "java": 'static int wordCount(String s) {\n    String trimmed = s.trim();\n    return trimmed.isEmpty() ? 0 : trimmed.split("\\\\s+").length;\n}',
     "cpp": 'int wordCount(const string& s) {\n    istringstream in(s);\n    string w;\n    int count = 0;\n    while (in >> w) count++;\n    return count;\n}',
     "c": 'int word_count(const char *s) {\n    int count = 0, inWord = 0;\n    for (; *s; s++) {\n        if (isspace((unsigned char)*s)) inWord = 0;\n        else if (!inWord) { inWord = 1; count++; }\n    }\n    return count;\n}'},
    main={"python": 'print(word_count("the quick  brown fox"))', "javascript": 'console.log(wordCount("the quick  brown fox"));', "typescript": 'console.log(wordCount("the quick  brown fox"));',
          "java": 'System.out.println(wordCount("the quick  brown fox"));', "cpp": 'cout << wordCount("the quick  brown fox") << endl;', "c": 'printf("%d\\n", word_count("the quick  brown fox"));'})

A["fizzbuzz"] = dict(
    desc="print FizzBuzz for 1 to n", cx="O(n) time, O(1) space.",
    expl="For each number print Fizz if divisible by 3, Buzz if by 5, FizzBuzz if by both, otherwise the number. Check the combined case first (or build the string from both parts).", shown="fizzbuzz(15)", expect="1 2 fizz 4 buzz fizz 7 8 fizz buzz 11 fizz 13 14 fizzbuzz",
    code={
     "python": 'def fizzbuzz(n):\n    for i in range(1, n + 1):\n        if i % 15 == 0:\n            print("FizzBuzz")\n        elif i % 3 == 0:\n            print("Fizz")\n        elif i % 5 == 0:\n            print("Buzz")\n        else:\n            print(i)',
     "javascript": 'function fizzbuzz(n) {\n  for (let i = 1; i <= n; i++) {\n    if (i % 15 === 0) console.log("FizzBuzz");\n    else if (i % 3 === 0) console.log("Fizz");\n    else if (i % 5 === 0) console.log("Buzz");\n    else console.log(i);\n  }\n}',
     "typescript": 'function fizzbuzz(n: number): void {\n  for (let i = 1; i <= n; i++) {\n    if (i % 15 === 0) console.log("FizzBuzz");\n    else if (i % 3 === 0) console.log("Fizz");\n    else if (i % 5 === 0) console.log("Buzz");\n    else console.log(i);\n  }\n}',
     "java": 'static void fizzbuzz(int n) {\n    for (int i = 1; i <= n; i++) {\n        if (i % 15 == 0) System.out.println("FizzBuzz");\n        else if (i % 3 == 0) System.out.println("Fizz");\n        else if (i % 5 == 0) System.out.println("Buzz");\n        else System.out.println(i);\n    }\n}',
     "cpp": 'void fizzbuzz(int n) {\n    for (int i = 1; i <= n; i++) {\n        if (i % 15 == 0) cout << "FizzBuzz\\n";\n        else if (i % 3 == 0) cout << "Fizz\\n";\n        else if (i % 5 == 0) cout << "Buzz\\n";\n        else cout << i << "\\n";\n    }\n}',
     "c": 'void fizzbuzz(int n) {\n    for (int i = 1; i <= n; i++) {\n        if (i % 15 == 0) printf("FizzBuzz\\n");\n        else if (i % 3 == 0) printf("Fizz\\n");\n        else if (i % 5 == 0) printf("Buzz\\n");\n        else printf("%d\\n", i);\n    }\n}'},
    main={"python": 'fizzbuzz(15)', "javascript": 'fizzbuzz(15);', "typescript": 'fizzbuzz(15);', "java": 'fizzbuzz(15);', "cpp": 'fizzbuzz(15);', "c": 'fizzbuzz(15);'})

A["second_largest"] = dict(
    desc="find the second largest distinct value in an array", cx="O(n) time, O(1) space in one pass.",
    expl="Track the largest and second largest in one pass. Duplicates of the maximum must not count as the second largest.", shown="second_largest([12, 35, 1, 10, 34, 35])", expect="34",
    code={
     "python": 'def second_largest(arr):\n    first = second = None\n    for x in arr:\n        if first is None or x > first:\n            first, second = x, first\n        elif x != first and (second is None or x > second):\n            second = x\n    return second',
     "javascript": 'function secondLargest(arr) {\n  let first = -Infinity, second = -Infinity;\n  for (const x of arr) {\n    if (x > first) {\n      second = first;\n      first = x;\n    } else if (x > second && x !== first) {\n      second = x;\n    }\n  }\n  return second;\n}',
     "typescript": 'function secondLargest(arr: number[]): number {\n  let first = -Infinity, second = -Infinity;\n  for (const x of arr) {\n    if (x > first) {\n      second = first;\n      first = x;\n    } else if (x > second && x !== first) {\n      second = x;\n    }\n  }\n  return second;\n}',
     "java": 'static int secondLargest(int[] arr) {\n    long first = Long.MIN_VALUE, second = Long.MIN_VALUE;\n    for (int x : arr) {\n        if (x > first) {\n            second = first;\n            first = x;\n        } else if (x > second && x != first) {\n            second = x;\n        }\n    }\n    return (int) second;\n}',
     "cpp": 'int secondLargest(const vector<int>& arr) {\n    long long first = LLONG_MIN, second = LLONG_MIN;\n    for (int x : arr) {\n        if (x > first) {\n            second = first;\n            first = x;\n        } else if (x > second && x != first) {\n            second = x;\n        }\n    }\n    return (int)second;\n}',
     "c": 'int second_largest(const int *arr, int n) {\n    long long first = LLONG_MIN, second = LLONG_MIN;\n    for (int i = 0; i < n; i++) {\n        if (arr[i] > first) {\n            second = first;\n            first = arr[i];\n        } else if (arr[i] > second && arr[i] != first) {\n            second = arr[i];\n        }\n    }\n    return (int)second;\n}'},
    main={"python": 'print(second_largest([12, 35, 1, 10, 34, 35]))', "javascript": 'console.log(secondLargest([12, 35, 1, 10, 34, 35]));', "typescript": 'console.log(secondLargest([12, 35, 1, 10, 34, 35]));',
          "java": 'System.out.println(secondLargest(new int[]{12, 35, 1, 10, 34, 35}));', "cpp": 'cout << secondLargest({12, 35, 1, 10, 34, 35}) << endl;',
          "c": 'int arr[] = {12, 35, 1, 10, 34, 35};\n    printf("%d\\n", second_largest(arr, 6));'})

# ---------------------------------------------------------------- harness
HEAD = {
    "cpp": '#include <bits/stdc++.h>\nusing namespace std;\n',
    "c": '#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n#include <ctype.h>\n#include <limits.h>\n',
    "java": 'import java.util.*;\n',
}


def norm(out):
    return " ".join(re.findall(r"-?\w+", out.lower()))


def run(lang, code, main):
    d = Path(tempfile.mkdtemp(dir=SCRATCH))
    try:
        if lang == "python":
            f = d / "t.py"
            f.write_text(code + "\n\n" + main + "\n", encoding="utf-8")
            r = subprocess.run(["python", str(f)], capture_output=True, text=True, timeout=30)
        elif lang in ("javascript", "typescript"):
            f = d / ("t.js" if lang == "javascript" else "t.ts")
            f.write_text(code + "\n\n" + main + "\n", encoding="utf-8")
            r = subprocess.run(["node", str(f)], capture_output=True, text=True, timeout=30)
        elif lang == "java":
            f = d / "Main.java"
            f.write_text(HEAD["java"] + "public class Main {\n" + code + "\n    public static void main(String[] args) {\n        " + main + "\n    }\n}\n", encoding="utf-8")
            c = subprocess.run(["javac", str(f)], capture_output=True, text=True, timeout=60)
            if c.returncode:
                return None, c.stderr
            r = subprocess.run(["java", "-cp", str(d), "Main"], capture_output=True, text=True, timeout=30)
        elif lang in ("cpp", "c"):
            f = d / ("t.cpp" if lang == "cpp" else "t.c")
            f.write_text(HEAD[lang] + code + "\nint main() {\n    " + main + "\n    return 0;\n}\n", encoding="utf-8")
            exe = d / "t.exe"
            c = subprocess.run(["g++" if lang == "cpp" else "gcc", "-std=c++17" if lang == "cpp" else "-std=c11", str(f), "-o", str(exe)], capture_output=True, text=True, timeout=90)
            if c.returncode:
                return None, c.stderr
            r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
        if r.returncode:
            return None, r.stderr
        return r.stdout, ""
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


VERIFIED = {}
failed = []
for name, a in A.items():
    for lang in LANGS:
        out, err = run(lang, a["code"][lang], a["main"][lang])
        if out is not None and norm(out) == norm(a["expect"]):
            VERIFIED[(name, lang)] = True
        else:
            failed.append((name, lang, (err or out or "")[:200]))
print(f"verified {len(VERIFIED)} / {len(A) * len(LANGS)} programs; failed {len(failed)}")
for f in failed:
    print("  FAIL", f)

LANG_NOTE = {
    "python": "Python's `{}` handle the heavy lifting, so there is little boilerplate.",
    "javascript": "In JavaScript, numbers are all floating point, so this is exact only for integers within about 9 quadrillion.",
    "typescript": "The type annotations are erased at runtime; they just let the compiler catch mistakes like passing a string where a number is expected.",
    "java": "The methods are `static` so you can call them from `main` without creating an object; wrap them in a class when you use them.",
    "cpp": "Pass large containers by const reference (as here) to avoid copying them.",
    "c": "C has no built-in array length or string type, so the length is passed explicitly or the string is NUL-terminated.",
}
ASKS = [
    "Write a {L} function to {d}.",
    "How do I {d} in {L}?",
    "Can you show me {L} code to {d}?",
    "I need a {L} snippet that will {d}.",
    "{L}: {d_cap}",
    "Give me a clean {L} implementation to {d}, with a short explanation.",
]
out_recs = []
for name, a in A.items():
    for lang in LANGS:
        if (name, lang) not in VERIFIED:
            continue
        L = LABEL[lang]
        q = R.choice(ASKS).format(L=L, d=a["desc"], d_cap=a["desc"][0].upper() + a["desc"][1:] + ".")
        fence = {"cpp": "cpp", "c": "c", "javascript": "javascript", "typescript": "typescript", "java": "java", "python": "python"}[lang]
        note = LANG_NOTE[lang].format("built-ins") if lang == "python" else LANG_NOTE[lang]
        shown_call = a["main"][lang].split("\n")[0]
        ans = (f"Here's a {L} version:\n\n```{fence}\n{a['code'][lang]}\n```\n\n{a['expl']} {note}\n\n"
               f"**Complexity:** {a['cx']}\n\nFor example, `{a['shown']}` gives `{a['expect']}`.")
        out_recs.append(mk(CAT, "algorithm_" + lang, [("user", q), ("assistant", ans)], f"algo_{name}", "easy" if name in ("reverse_string", "factorial", "fibonacci", "sum_digits", "max_in_array") else "medium", "verified", pick(R, SYS_GENERAL)))

# conversion requests: python -> other language (verified code reused)
CONV = ["Convert this Python to {L}:\n\n```python\n{src}\n```", "Port this Python function to {L}.\n\n```python\n{src}\n```", "I have this in Python. What's the equivalent in {L}?\n\n```python\n{src}\n```"]
for name, a in A.items():
    for lang in LANGS[1:]:
        if (name, lang) not in VERIFIED or ("python" not in [l for l in LANGS if (name, l) in VERIFIED]):
            continue
        L = LABEL[lang]
        q = R.choice(CONV).format(L=L, src=a["code"]["python"])
        ans = (f"Here's the {L} equivalent:\n\n```{lang}\n{a['code'][lang]}\n```\n\nThe logic is the same as the Python version. {LANG_NOTE[lang]} "
               f"For example, `{a['shown']}` gives `{a['expect']}` in both versions.")
        out_recs.append(mk(CAT, "code_conversion", [("user", q), ("assistant", ans)], f"conv_{name}", "medium", "verified", pick(R, SYS_GENERAL)))

save("prog_multi", out_recs)
json.dump({"verified": len(VERIFIED), "attempted": len(A) * len(LANGS), "failed": [list(f[:2]) for f in failed]}, open(GEN.parent.parent / "reports" / "code_verification_multi.json", "w"), indent=1)
