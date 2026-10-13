"""Phase 2.1 programming: 'explain this compiler/runtime error'. Every buggy snippet is compiled/run so the quoted error is the real one,
and every fixed snippet is run too (with its expected output asserted).  Entries that fail verification are dropped and counted."""
import re
from gen21_common import *
from gen21_run import *

rng = seeded("prog21_errors")
LABEL = {"python": "Python", "javascript": "JavaScript", "c": "C", "cpp": "C++", "java": "Java", "typescript": "TypeScript"}
FENCE = {"python": "python", "javascript": "javascript", "c": "c", "cpp": "cpp", "java": "java", "typescript": "typescript"}
E = []


def e(lang, level, ask, code, expect, why, fix, fix_out=None):
    E.append((lang, level, ask, code.strip("\n"), expect, why, fix.strip("\n"), fix_out))


# ---------------- Python ----------------
e("python", "easy", "Why does this crash?", 'nums = [1, 2, 3]\nprint(nums[3])', "IndexError: list index out of range",
  "Python lists are zero-indexed, so a list of three items has valid indexes 0, 1 and 2. Index 3 is one past the end. Use `nums[2]` or `nums[-1]` for the last item.", 'nums = [1, 2, 3]\nprint(nums[-1])', "3")
e("python", "easy", "I'm getting a TypeError, no idea why", 'age = 30\nprint("Age: " + age)', "TypeError",
  "You can't concatenate a `str` and an `int` with `+`. Convert the number with `str(age)` or, better, use an f-string.", 'age = 30\nprint(f"Age: {age}")', "Age: 30")
e("python", "easy", "What does KeyError mean?", 'prices = {"apple": 1.2, "pear": 0.9}\nprint(prices["banana"])', "KeyError: 'banana'",
  "The dictionary has no key `'banana'`. Use `prices.get(\"banana\")` to get `None` (or a default) instead of an exception, or check `if \"banana\" in prices` first.", 'prices = {"apple": 1.2, "pear": 0.9}\nprint(prices.get("banana", 0.0))', "0.0")
e("python", "easy", "Help, IndentationError?", 'def greet(name):\nprint("Hello", name)\n\ngreet("Ana")', "IndentationError",
  "The body of a function must be indented. Python uses indentation to know which lines belong to the `def`, so the `print` line needs to be indented (four spaces is the convention).", 'def greet(name):\n    print("Hello", name)\n\ngreet("Ana")', "Hello Ana")
e("python", "easy", "NameError on a variable I definitely made", 'def compute():\n    total = 10\n\ncompute()\nprint(total)', "NameError: name 'total' is not defined",
  "`total` is a local variable that exists only inside `compute()`. Once the function returns it is gone. Return the value and store it: `total = compute()`.", 'def compute():\n    total = 10\n    return total\n\ntotal = compute()\nprint(total)', "10")
e("python", "medium", "Why does my function fail the second time I call it?", 'def add_item(item, bucket=[]):\n    bucket.append(item)\n    return bucket\n\nprint(add_item(1))\nprint(add_item(2))', None,
  "This is not an error but a classic trap: the default list is created once, when the function is defined, and shared by every call. The second call appends to the same list. Use `None` as the default and create the list inside.", 'def add_item(item, bucket=None):\n    if bucket is None:\n        bucket = []\n    bucket.append(item)\n    return bucket\n\nprint(add_item(1))\nprint(add_item(2))', "[1]\n[2]")
e("python", "medium", "UnboundLocalError??", 'count = 0\n\ndef bump():\n    count += 1\n\nbump()', "UnboundLocalError",
  "Assigning to `count` inside the function makes Python treat it as a local variable, but it has no value yet when `+= 1` reads it. Declare `global count` if you really need a module-level counter, or better, pass the value in and return the new one.", 'count = 0\n\ndef bump(n):\n    return n + 1\n\ncount = bump(count)\nprint(count)', "1")
e("python", "medium", "ValueError when converting input", 'text = "12.5"\nprint(int(text))', "ValueError: invalid literal for int()",
  "`int()` accepts only whole-number strings. `\"12.5\"` has a decimal point, so use `float(text)` and convert afterwards if you need an integer.", 'text = "12.5"\nprint(int(float(text)))', "12")
e("python", "medium", "Why 'NoneType' has no attribute?", 'def find(words, target):\n    for w in words:\n        if w == target:\n            return w\n\nresult = find(["a", "b"], "z")\nprint(result.upper())', "AttributeError: 'NoneType' object has no attribute 'upper'",
  "`find` returns `None` implicitly when the loop ends without a match, and `None` has no `.upper()`. Handle the missing case explicitly before using the result.", 'def find(words, target):\n    for w in words:\n        if w == target:\n            return w\n    return None\n\nresult = find(["a", "b"], "z")\nprint(result.upper() if result else "not found")', "not found")
e("python", "medium", "Modifying a list while looping gives odd results", 'items = [1, 2, 3, 4, 5, 6]\nfor x in items:\n    if x % 2 == 0:\n        items.remove(x)\nprint(items)', None,
  "Removing from a list while iterating shifts the remaining elements, so the loop skips some. Build a new list instead.", 'items = [1, 2, 3, 4, 5, 6]\nitems = [x for x in items if x % 2 != 0]\nprint(items)', "[1, 3, 5]")
e("python", "medium", "ZeroDivisionError in my average function", 'def average(values):\n    return sum(values) / len(values)\n\nprint(average([]))', "ZeroDivisionError: division by zero",
  "With an empty list `len(values)` is 0. Decide what an empty average should mean (return `None`, raise a clearer error, or use a default) and check for it first.", 'def average(values):\n    if not values:\n        return None\n    return sum(values) / len(values)\n\nprint(average([]))', "None")
e("python", "hard", "What does 'RecursionError' mean here?", 'def countdown(n):\n    print(n)\n    return countdown(n - 1)\n\ncountdown(3)', "RecursionError",
  "There is no base case, so the function calls itself forever (here the output goes into negative numbers) until Python's recursion limit is reached and it raises `RecursionError`. Add a stopping condition.", 'def countdown(n):\n    if n < 0:\n        return\n    print(n)\n    countdown(n - 1)\n\ncountdown(3)', "3\n2\n1\n0")
e("python", "hard", "TypeError: unhashable type", 'seen = set()\nseen.add([1, 2])', "TypeError: unhashable type: 'list'",
  "Set members and dictionary keys must be hashable, and lists are mutable, so they aren't. Use a tuple instead: `seen.add((1, 2))`.", 'seen = set()\nseen.add((1, 2))\nprint(seen)', "{(1, 2)}")
e("python", "medium", "'str' object does not support item assignment", 's = "hello"\ns[0] = "H"\nprint(s)', "TypeError: 'str' object does not support item assignment",
  "Strings are immutable. Build a new string instead: `s = \"H\" + s[1:]`, or use `s.capitalize()` here.", 's = "hello"\ns = "H" + s[1:]\nprint(s)', "Hello")
e("python", "medium", "Why is my loop variable always the last value?", 'funcs = [lambda: i for i in range(3)]\nprint([f() for f in funcs])', None,
  "The lambdas close over the variable `i`, not its value at the time they were created. By the time they run, `i` is 2. Bind the current value with a default argument.", 'funcs = [lambda i=i: i for i in range(3)]\nprint([f() for f in funcs])', "[0, 1, 2]")
e("python", "easy", "ModuleNotFoundError for my own file", 'import utilz\nprint(utilz.helper())', "ModuleNotFoundError: No module named 'utilz'",
  "Python can't find a module called `utilz`. Check the spelling against the file name, that the file is in the same folder (or on `sys.path`), and that you run the script from the right directory.", 'import math\nprint(math.sqrt(16))', "4.0")

# ---------------- JavaScript ----------------
e("javascript", "easy", "What does 'x is not defined' mean?", 'function total() {\n  const sum = 5;\n}\ntotal();\nconsole.log(sum);', "ReferenceError: sum is not defined",
  "`sum` is declared with `const` inside the function, so it only exists in that scope. Return it and capture the result outside.", 'function total() {\n  const sum = 5;\n  return sum;\n}\nconsole.log(total());', "5")
e("javascript", "easy", "Cannot read properties of undefined?", 'const user = {};\nconsole.log(user.address.city);', "TypeError: Cannot read properties of undefined (reading 'city')",
  "`user.address` is `undefined`, so reading `.city` on it throws. Use optional chaining (`user.address?.city`) or check for the object first.", 'const user = {};\nconsole.log(user.address?.city);', "undefined")
e("javascript", "easy", "Assignment to constant variable??", 'const count = 1;\ncount = 2;\nconsole.log(count);', "TypeError: Assignment to constant variable.",
  "A `const` binding can't be reassigned. If the value must change, declare it with `let`. (A `const` object or array can still be mutated; only the binding is fixed.)", 'let count = 1;\ncount = 2;\nconsole.log(count);', "2")
e("javascript", "medium", "x is not a function", 'const nums = [1, 2, 3];\nconst doubled = nums.map = (n) => n * 2;\nconsole.log(nums.map((n) => n));', None,
  "The chained assignment overwrote `nums.map` with your arrow function, so the later `nums.map(...)` call runs your function instead of `Array.prototype.map`. Assign the result of calling `map` instead.", 'const nums = [1, 2, 3];\nconst doubled = nums.map((n) => n * 2);\nconsole.log(doubled);', "[ 2, 4, 6 ]")
e("javascript", "medium", "Why does this print NaN?", 'const price = "12 dollars";\nconsole.log(Number(price) * 2);', None,
  "`Number(\"12 dollars\")` can't parse the whole string, so it returns `NaN`, and anything times `NaN` is `NaN`. Extract the digits first (for example `parseFloat(price)`, which stops at the first non-numeric character) or validate the input.", 'const price = "12 dollars";\nconsole.log(parseFloat(price) * 2);', "24")
e("javascript", "medium", "SyntaxError: Unexpected token", 'const data = JSON.parse("{name: \\"Ana\\"}");\nconsole.log(data);', "SyntaxError",
  "JSON requires double-quoted keys. `{name: ...}` is a JavaScript object literal, not valid JSON. Quote the key (`{\"name\": \"Ana\"}`) or build the object directly.", 'const data = JSON.parse(\'{"name": "Ana"}\');\nconsole.log(data);', "{ name: 'Ana' }")
e("javascript", "medium", "forEach with await doesn't wait", 'const wait = (ms, v) => new Promise((r) => setTimeout(() => r(v), ms));\n[1, 2, 3].forEach(async (n) => {\n  console.log(await wait(10, n));\n});\nconsole.log("done");', None,
  "`forEach` ignores the promises returned by async callbacks, so `\"done\"` prints first. Use a `for...of` loop with `await` (sequential) or `await Promise.all(items.map(...))` (parallel).", 'const wait = (ms, v) => new Promise((r) => setTimeout(() => r(v), ms));\n(async () => {\n  for (const n of [1, 2, 3]) {\n    console.log(await wait(10, n));\n  }\n  console.log("done");\n})();', "1\n2\n3\ndone")
e("javascript", "hard", "Why does `this` come out undefined?", '"use strict";\nconst counter = {\n  n: 0,\n  inc() { this.n++; return this.n; },\n};\nconst f = counter.inc;\nconsole.log(f());', "TypeError",
  "Copying the method into `f` detaches it from `counter`, so when it is called as a plain function `this` is `undefined` in strict mode. Call it as `counter.inc()` or bind it: `counter.inc.bind(counter)`.", 'const counter = {\n  n: 0,\n  inc() { this.n++; return this.n; },\n};\nconst f = counter.inc.bind(counter);\nconsole.log(f());', "1")
e("javascript", "medium", "Comparing arrays gives false", 'console.log([1, 2] === [1, 2]);', None,
  "`===` on objects and arrays compares references, not contents, and these are two different array objects. Compare element by element, or serialise both: `JSON.stringify(a) === JSON.stringify(b)` works for simple data.", 'const a = [1, 2];\nconst b = [1, 2];\nconsole.log(JSON.stringify(a) === JSON.stringify(b));', "true")
e("javascript", "easy", "Why does 0.1 + 0.2 not equal 0.3?", 'console.log(0.1 + 0.2 === 0.3);\nconsole.log(0.1 + 0.2);', None,
  "JavaScript numbers are binary floating-point, and 0.1 and 0.2 can't be represented exactly, so the sum is 0.30000000000000004. Compare with a tolerance (`Math.abs(a - b) < Number.EPSILON`) or work in integers such as cents.", 'console.log(Math.abs(0.1 + 0.2 - 0.3) < Number.EPSILON);', "true")

# ---------------- C ----------------
e("c", "easy", "gcc says 'implicit declaration'", '#include <stdio.h>\n\nint main(void) {\n    int n = strlen("hello");\n    printf("%d\\n", n);\n    return 0;\n}', "strlen",
  "You are using `strlen` without including its header. Add `#include <string.h>` so the compiler knows its signature.", '#include <stdio.h>\n#include <string.h>\n\nint main(void) {\n    int n = (int)strlen("hello");\n    printf("%d\\n", n);\n    return 0;\n}', "5")
e("c", "easy", "expected ';' error in C", '#include <stdio.h>\n\nint main(void) {\n    int x = 5\n    printf("%d\\n", x);\n    return 0;\n}', "expected ',' or ';'",
  "A statement is missing its terminating semicolon. The compiler usually reports the error on the line *after* the real problem, because that's where it realised the statement hadn't ended.", '#include <stdio.h>\n\nint main(void) {\n    int x = 5;\n    printf("%d\\n", x);\n    return 0;\n}', "5")
e("c", "medium", "undeclared identifier in my loop (C)", '#include <stdio.h>\n\nint main(void) {\n    for (int i = 0; i < 3; i++) {\n        printf("%d ", i);\n    }\n    printf("\\n%d\\n", i);\n    return 0;\n}', "'i' undeclared",
  "`i` is declared in the `for` statement, so it only exists inside the loop. To use it afterwards, declare it before the loop.", '#include <stdio.h>\n\nint main(void) {\n    int i;\n    for (i = 0; i < 3; i++) {\n        printf("%d ", i);\n    }\n    printf("\\n%d\\n", i);\n    return 0;\n}', "0 1 2 \n3")
e("c", "medium", "incompatible pointer type warning/error", '#include <stdio.h>\n\nint main(void) {\n    int value = 7;\n    char *p = &value;\n    printf("%d\\n", *p);\n    return 0;\n}', "incompatible pointer type",
  "You are storing the address of an `int` in a `char *`. The types differ, so dereferencing would read just one byte. Use `int *p = &value;`.", '#include <stdio.h>\n\nint main(void) {\n    int value = 7;\n    int *p = &value;\n    printf("%d\\n", *p);\n    return 0;\n}', "7")
e("c", "medium", "too few arguments to function", '#include <stdio.h>\n\nint add(int a, int b) { return a + b; }\n\nint main(void) {\n    printf("%d\\n", add(1));\n    return 0;\n}', "too few arguments",
  "`add` takes two parameters but the call passes only one. C doesn't have default arguments, so supply both values.", '#include <stdio.h>\n\nint add(int a, int b) { return a + b; }\n\nint main(void) {\n    printf("%d\\n", add(1, 2));\n    return 0;\n}', "3")
e("c", "medium", "assignment to an array in C", '#include <stdio.h>\n\nint main(void) {\n    char name[10];\n    name = "Ana";\n    printf("%s\\n", name);\n    return 0;\n}', "assignment to expression with array type",
  "You can't assign to an array with `=`. Copy the characters with `strcpy` (with a large enough buffer), or initialise at declaration: `char name[10] = \"Ana\";`.", '#include <stdio.h>\n#include <string.h>\n\nint main(void) {\n    char name[10];\n    strcpy(name, "Ana");\n    printf("%s\\n", name);\n    return 0;\n}', "Ana")
e("c", "hard", "conflicting types for a function in C", '#include <stdio.h>\n\nint main(void) {\n    printf("%d\\n", square(4));\n    return 0;\n}\n\nint square(int n) { return n * n; }', None,
  "The function is used before it is declared, so the compiler assumes an implicit signature (which newer standards reject, and which can conflict with the real definition). Add a prototype before `main`, or define the function earlier.", '#include <stdio.h>\n\nint square(int n);\n\nint main(void) {\n    printf("%d\\n", square(4));\n    return 0;\n}\n\nint square(int n) { return n * n; }', "16")

# ---------------- C++ ----------------
e("cpp", "easy", "C++ 'cout' was not declared", '#include <iostream>\n\nint main() {\n    cout << "hi" << endl;\n    return 0;\n}', "'cout' was not declared",
  "`cout` lives in the `std` namespace. Write `std::cout` and `std::endl`, or add `using namespace std;` (fine for small programs, but avoid it in headers).", '#include <iostream>\n\nint main() {\n    std::cout << "hi" << std::endl;\n    return 0;\n}', "hi")
e("cpp", "medium", "no match for operator in C++", '#include <iostream>\n#include <string>\n\nint main() {\n    std::string s = "n=";\n    int n = 3;\n    std::cout << (s - n) << std::endl;\n    return 0;\n}', "no match for 'operator-'",
  "There is no `-` operator for `std::string` and `int`. If you meant concatenation, use `+` with `std::to_string(n)`.", '#include <iostream>\n#include <string>\n\nint main() {\n    std::string s = "n=";\n    int n = 3;\n    std::cout << (s + std::to_string(n)) << std::endl;\n    return 0;\n}', "n=3")
e("cpp", "medium", "const correctness error", '#include <iostream>\n\nvoid bump(const int &x) {\n    x++;\n}\n\nint main() {\n    int a = 1;\n    bump(a);\n    std::cout << a << std::endl;\n}', "read-only reference",
  "The parameter is a reference to `const int`, so the function promises not to change it, and `x++` breaks that promise. Take it as `int &x` if the function should modify the caller's variable.", '#include <iostream>\n\nvoid bump(int &x) {\n    x++;\n}\n\nint main() {\n    int a = 1;\n    bump(a);\n    std::cout << a << std::endl;\n}', "2")
e("cpp", "medium", "vector index out of range, no error shown", '#include <iostream>\n#include <vector>\n\nint main() {\n    std::vector<int> v = {1, 2, 3};\n    try {\n        std::cout << v.at(5) << std::endl;\n    } catch (const std::out_of_range &e) {\n        std::cout << "caught out_of_range" << std::endl;\n    }\n}', None,
  "`operator[]` does no bounds checking and out-of-range access is undefined behaviour, while `at()` checks and throws `std::out_of_range`, which you can catch. Use `at()` while debugging or whenever the index comes from outside.", '#include <iostream>\n#include <vector>\n\nint main() {\n    std::vector<int> v = {1, 2, 3};\n    std::size_t i = 5;\n    if (i < v.size()) std::cout << v[i] << std::endl;\n    else std::cout << "index out of range" << std::endl;\n}', "index out of range")
e("cpp", "hard", "template deduction failure", '#include <iostream>\n\ntemplate <typename T>\nT biggest(T a, T b) { return a > b ? a : b; }\n\nint main() {\n    std::cout << biggest(3, 4.5) << std::endl;\n}', "no matching function",
  "Both arguments must deduce to the same `T`, but `3` is an `int` and `4.5` is a `double`, so deduction fails. Pass two values of one type, cast one of them, or specify the type: `biggest<double>(3, 4.5)`.", '#include <iostream>\n\ntemplate <typename T>\nT biggest(T a, T b) { return a > b ? a : b; }\n\nint main() {\n    std::cout << biggest<double>(3, 4.5) << std::endl;\n}', "4.5")
e("cpp", "medium", "missing return type", '#include <iostream>\n\nsquare(int n) { return n * n; }\n\nint main() {\n    std::cout << square(5) << std::endl;\n}', "ISO C++ forbids declaration of 'square' with no type",
  "Unlike old C, C++ requires a return type on every function. Write `int square(int n)`.", '#include <iostream>\n\nint square(int n) { return n * n; }\n\nint main() {\n    std::cout << square(5) << std::endl;\n}', "25")

# ---------------- Java ----------------
e("java", "easy", "cannot find symbol in Java", 'public class Main {\n    public static void main(String[] args) {\n        String name = "Ana";\n        System.out.println(nam);\n    }\n}', "cannot find symbol",
  "`nam` is a typo for `name`, so the compiler can't find a variable with that name in scope. The message shows the symbol and its location.", 'public class Main {\n    public static void main(String[] args) {\n        String name = "Ana";\n        System.out.println(name);\n    }\n}', "Ana")
e("java", "easy", "incompatible types: possible lossy conversion", 'public class Main {\n    public static void main(String[] args) {\n        double price = 9.99;\n        int whole = price;\n        System.out.println(whole);\n    }\n}', "incompatible types: possible lossy conversion from double to int",
  "Assigning a `double` to an `int` could lose the fractional part, so Java requires an explicit cast: `int whole = (int) price;` (which truncates to 9). Use `Math.round` if you want rounding.", 'public class Main {\n    public static void main(String[] args) {\n        double price = 9.99;\n        int whole = (int) price;\n        System.out.println(whole);\n    }\n}', "9")
e("java", "easy", "missing return statement", 'public class Main {\n    static int sign(int n) {\n        if (n > 0) {\n            return 1;\n        } else if (n < 0) {\n            return -1;\n        }\n    }\n    public static void main(String[] args) {\n        System.out.println(sign(5));\n    }\n}', "missing return statement",
  "Not every path returns a value: when `n` is 0 neither branch runs and the method falls off the end. Add a final `return 0;` or restructure the conditions.", 'public class Main {\n    static int sign(int n) {\n        if (n > 0) {\n            return 1;\n        } else if (n < 0) {\n            return -1;\n        }\n        return 0;\n    }\n    public static void main(String[] args) {\n        System.out.println(sign(5));\n    }\n}', "1")
e("java", "medium", "unreported exception in Java", 'import java.io.FileReader;\n\npublic class Main {\n    public static void main(String[] args) {\n        FileReader r = new FileReader("data.txt");\n    }\n}', "unreported exception FileNotFoundException",
  "`FileReader`'s constructor throws a checked exception, and Java forces you to either catch it or declare it with `throws`. Wrap the call in `try/catch`, or add `throws Exception` to `main`.", 'import java.io.FileReader;\nimport java.io.FileNotFoundException;\n\npublic class Main {\n    public static void main(String[] args) {\n        try {\n            FileReader r = new FileReader("data.txt");\n        } catch (FileNotFoundException e) {\n            System.out.println("file not found");\n        }\n    }\n}', "file not found")
e("java", "medium", "non-static method cannot be referenced from a static context", 'public class Main {\n    int twice(int n) { return n * 2; }\n    public static void main(String[] args) {\n        System.out.println(twice(4));\n    }\n}', "non-static method twice(int) cannot be referenced from a static context",
  "`main` is static, so there is no `Main` instance to call the instance method on. Either mark `twice` as `static`, or create an object: `new Main().twice(4)`.", 'public class Main {\n    static int twice(int n) { return n * 2; }\n    public static void main(String[] args) {\n        System.out.println(twice(4));\n    }\n}', "8")
e("java", "medium", "NullPointerException explained", 'public class Main {\n    public static void main(String[] args) {\n        String s = null;\n        System.out.println(s.length());\n    }\n}', "NullPointerException",
  "`s` holds `null`, meaning no object, so calling `.length()` on it throws. Check for null first, or make sure the value is assigned before use.", 'public class Main {\n    public static void main(String[] args) {\n        String s = null;\n        System.out.println(s == null ? 0 : s.length());\n    }\n}', "0")
e("java", "medium", "ArrayIndexOutOfBounds", 'public class Main {\n    public static void main(String[] args) {\n        int[] a = new int[3];\n        for (int i = 0; i <= a.length; i++) {\n            a[i] = i;\n        }\n    }\n}', "ArrayIndexOutOfBoundsException",
  "The loop condition `i <= a.length` lets `i` reach 3, but valid indexes are 0 to 2. Use `i < a.length`.", 'public class Main {\n    public static void main(String[] args) {\n        int[] a = new int[3];\n        for (int i = 0; i < a.length; i++) {\n            a[i] = i;\n        }\n        System.out.println(a[2]);\n    }\n}', "2")
e("java", "medium", "string compare with == not working in Java", 'public class Main {\n    public static void main(String[] args) {\n        String a = new String("hi");\n        String b = new String("hi");\n        System.out.println(a == b);\n    }\n}', None,
  "`==` compares object references, and `new String` creates two distinct objects, so it prints false. Use `a.equals(b)` to compare contents.", 'public class Main {\n    public static void main(String[] args) {\n        String a = new String("hi");\n        String b = new String("hi");\n        System.out.println(a.equals(b));\n    }\n}', "true")
e("java", "hard", "generic type error: int can't be a type argument", 'import java.util.*;\n\npublic class Main {\n    public static void main(String[] args) {\n        List<int> xs = new ArrayList<>();\n        xs.add(1);\n    }\n}', "unexpected type",
  "Generics work only with reference types, not primitives. Use the wrapper class `Integer`: `List<Integer>`. Autoboxing converts `int` values automatically.", 'import java.util.*;\n\npublic class Main {\n    public static void main(String[] args) {\n        List<Integer> xs = new ArrayList<>();\n        xs.add(1);\n        System.out.println(xs);\n    }\n}', "[1]")

# ---------------- TypeScript (runtime via type stripping; only runtime-visible issues) ----------------
e("typescript", "medium", "Why does my TypeScript code crash with 'undefined'?", 'interface User { name: string; address?: { city: string } }\nconst u: User = { name: "Ana" };\nconsole.log(u.address!.city);', "TypeError",
  "The `!` non-null assertion only silences the type checker; it doesn't change what happens at runtime. `address` is optional and missing here, so reading `.city` throws. Handle the case with optional chaining: `u.address?.city ?? \"unknown\"`.", 'interface User { name: string; address?: { city: string } }\nconst u: User = { name: "Ana" };\nconsole.log(u.address?.city ?? "unknown");', "unknown")
e("typescript", "medium", "type assertion didn't validate my data", 'type Point = { x: number; y: number };\nconst p = JSON.parse(\'{"x": 1}\') as Point;\nconsole.log(p.y.toFixed(2));', "TypeError",
  "`as Point` tells the compiler to trust you; it adds no runtime check. The JSON has no `y`, so `p.y` is `undefined` and `.toFixed` throws. Validate parsed data (by hand or with a schema library) before treating it as a typed value.", 'type Point = { x: number; y: number };\nconst raw = JSON.parse(\'{"x": 1}\');\nconst ok = typeof raw.x === "number" && typeof raw.y === "number";\nconsole.log(ok ? "valid" : "invalid point");', "invalid point")


def first_error_lines(lang, out, n=4):
    lines = [l for l in out.splitlines() if l.strip()]
    if lang in ("javascript", "typescript"):
        hit = [l for l in lines if re.match(r"^(\w*Error)\b", l)]
        return hit[0] if hit else (lines[0] if lines else "")
    if lang == "python":
        return "\n".join(lines[-n:]) if len(lines) > n else "\n".join(lines)
    return "\n".join(lines[:n])


def runner(lang):
    return {"python": run_py, "javascript": run_js, "typescript": run_ts, "c": run_c, "cpp": lambda c: run_c(c, True), "java": run_java}[lang]


def norm_out(s):
    return "\n".join(l.rstrip() for l in s.strip().splitlines())


recs, dropped = [], []
for i, (lang, level, ask, code, expect, why, fix, fix_out) in enumerate(E):
    run = runner(lang)
    ok, out = run(code)
    if expect is None:
        # behavioural (non-crashing) bug: just require that the buggy code runs; show its real output
        if not ok:
            dropped.append((lang, ask, "expected to run"))
            continue
        shown_label = "Output"
    else:
        if ok or expect not in out:
            dropped.append((lang, ask, f"expected error {expect!r}, got ok={ok}: {out[:120]}"))
            continue
        shown_label = "Error"
    okf, outf = run(fix)
    if not okf or (fix_out is not None and norm_out(outf) != norm_out(fix_out)):
        dropped.append((lang, ask, f"fix mismatch: {outf[:100]!r} vs {fix_out!r}"))
        continue
    err = first_error_lines(lang, out) if expect is not None else norm_out(out)
    L, f = LABEL[lang], FENCE[lang]
    form = i % 4
    if form == 0:
        user = f"{ask}\n\n```{f}\n{code}\n```\n\n{shown_label}:\n```\n{err}\n```"
    elif form == 1:
        user = f"I'm learning {L} and ran into this.\n\n```{f}\n{code}\n```\n\nThe {shown_label.lower()} is:\n```\n{err}\n```\n\n{ask}"
    elif form == 2:
        user = f"```{f}\n{code}\n```\n\n{err if expect is not None else 'Output: ' + norm_out(out).replace(chr(10), ' / ')}\n\n{ask}"
    else:
        user = f"{ask} ({L})\n\n```{f}\n{code}\n```\nResult:\n```\n{err}\n```"
    outs = norm_out(outf)
    closing = f"\n\nCorrected version:\n\n```{f}\n{fix}\n```\n\nThis prints:\n\n```\n{outs}\n```" if outs else f"\n\nCorrected version:\n\n```{f}\n{fix}\n```"
    asst = why + closing
    recs.append(mk("programming", f"debug_{lang}_realerror", [("user", user), ("assistant", asst)], f"p21err-{i}", diff=level, src="verified"))

if __name__ == "__main__":
    save("prog21_errors", recs)
    print("dropped", len(dropped))
    for x in dropped:
        print("  ", x)
