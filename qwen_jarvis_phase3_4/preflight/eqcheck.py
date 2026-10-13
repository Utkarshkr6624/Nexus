"""Independent arithmetic-consistency checker.  For every '=' or '≈' in assistant text it takes the maximal purely-numeric expression on the
left and on the right (digits, + - * / ^ x × ÷ ( ) . ,) and, when both sides evaluate, requires them to agree (within the rounding implied
by the right-hand side).  Anything touching a letter/variable is skipped.  Independent of the Phase 2 generators (safe AST evaluation)."""
import ast
import math
import operator
import re

OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Pow: operator.pow}


def _ev(n):
    if isinstance(n, ast.Expression):
        return _ev(n.body)
    if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
        return n.value
    if isinstance(n, ast.BinOp) and type(n.op) in OPS:
        a, b = _ev(n.left), _ev(n.right)
        if isinstance(n.op, ast.Pow) and (abs(b) > 64 or abs(a) > 1e9):
            raise ValueError("big pow")
        return OPS[type(n.op)](a, b)
    if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
        v = _ev(n.operand)
        return -v if isinstance(n.op, ast.USub) else v
    raise ValueError("unsupported")


LEFT_CHARS = set("0123456789.,+-*/^()×÷−· ")
RIGHT_CHARS = set("0123456789.,+-*/^()×÷−· ")


def prep(e):
    e = e.strip()
    e = e.replace("×", "*").replace("·", "*").replace("÷", "/").replace("−", "-").replace("^", "**")
    e = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", e)
    return e


def evaluate(e):
    e = prep(e)
    if not e or not re.search(r"\d", e):
        return None
    try:
        return _ev(ast.parse(e, mode="eval"))
    except Exception:
        return None


def left_run(t, i):
    j = i
    while j > 0:
        c = t[j - 1]
        if c in LEFT_CHARS:
            j -= 1
        elif c == "x" and j - 2 >= 0 and t[j - 2].isdigit() or (c == "x" and j - 2 >= 0 and t[j - 2] == " " and j - 3 >= 0 and t[j - 3].isdigit()):
            j -= 1       # "3 x 4"
        else:
            break
    return j


def right_run(t, i):
    k = i
    while k < len(t):
        c = t[k]
        if c in RIGHT_CHARS and not (c == "." and (k + 1 >= len(t) or not t[k + 1].isdigit())):
            k += 1
        elif c == "x" and k + 1 < len(t) and (t[k + 1].isdigit() or t[k + 1] == " "):
            nxt = t[k + 1:].lstrip()
            if nxt[:1].isdigit() and t[k - 1:k].strip() == "" or (k > 0 and t[k - 1].isdigit()):
                k += 1
            else:
                break
        else:
            break
    return k


def check_text(text):
    """returns list of (lhs, rhs, lhs_value, rhs_value) that disagree"""
    t = text.replace("**", "").replace("$", "").replace("`", "")
    t = re.sub(r"(?<=\d) x (?=\d)", " * ", t)
    bad = []
    for m in re.finditer(r"=|≈", t):
        i = m.start()
        a = left_run(t, i)
        b = right_run(t, m.end())
        lhs, rhs = t[a:i].strip(), t[m.end():b].strip()
        if not lhs or not rhs:
            continue
        # left run must start at a boundary: previous char is not a letter / digit / ')' etc.
        prev = t[a - 1] if a > 0 else " "
        if prev.isalnum() or prev in "_]})":
            continue
        nxt = t[b] if b < len(t) else " "
        if nxt.isalpha() and nxt not in "x" and not nxt.isspace():
            # right side continues with letters (units like 'cm', 'km' are fine) but "x"/"y" variables are not numeric claims
            pass
        if nxt == "%":
            rhs_pct = True
        else:
            rhs_pct = False
        # skip leading '+', '-' or '*' artefacts
        lhs = lhs.lstrip("+*/ ")
        rhs = rhs.rstrip("+-*/^ ,.")
        if not re.search(r"[\+\-\*/\^×÷−]", lhs) and "(" not in lhs:
            continue           # bare number on the left: nothing to compute
        lv, rv = evaluate(lhs), evaluate(rhs)
        if lv is None or rv is None:
            continue
        dec = len(rhs.split(".")[1]) if re.fullmatch(r"-?[\d,]*\.\d+", rhs) else 0
        tol = 0.5 * 10 ** (-dec) * (1.0001)
        ok = abs(lv - rv) <= tol or abs(lv - rv) <= 1e-9 * max(1.0, abs(lv))
        if not ok and rhs_pct:
            ok = abs(lv * 100 - rv) <= tol * 100 or abs(lv - rv) <= tol
        if not ok and re.fullmatch(r"-?\d+", rhs):       # "= 34" claimed for 33.6 etc. is only OK if it is genuinely a rounding (<0.5)
            ok = abs(lv - rv) < 0.5
        if not ok:
            bad.append((lhs, rhs + ("%" if rhs_pct else ""), lv, rv))
    return bad
