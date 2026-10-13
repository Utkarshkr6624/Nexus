"""Independent arithmetic verifier (v2). For every '=' (or approx sign) in assistant prose outside code fences, take the maximal arithmetic expression to its left
(digits, + - x * / ^ parentheses, thousands separators, $ and %), parse it with a real recursive-descent parser (Fractions, correct precedence,
parentheses, unary minus, exponent) and compare with the number to its right allowing: rounding to the decimals shown, 'about' precision up to 0.6%
relative, and percentage presentation (result*100 when a % follows the stated number).  Flags go to preflight/arith_flags.json for manual reading."""
import json
import re
import sys
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from triage_risk import load  # noqa: E402

LEFT = re.compile(r"[\d$%.,()\s+\-x×*/^−]+$")
RIGHT = re.compile(r"\s*(\$?)(-?\d[\d,]*(?:\.\d+)?)\s*(%?)")


class P:
    def __init__(self, s):
        self.t = re.findall(r"\d[\d,]*(?:\.\d+)?|[()+\-x*/^]", s.replace("×", "x").replace("−", "-").replace("$", ""))
        self.i = 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def take(self):
        v = self.peek()
        self.i += 1
        return v

    def expr(self):
        v = self.term()
        while self.peek() in ("+", "-"):
            o = self.take()
            r = self.term()
            v = v + r if o == "+" else v - r
        return v

    def term(self):
        v = self.unary()
        while self.peek() in ("x", "*", "/"):
            o = self.take()
            r = self.unary()
            if o == "/":
                if r == 0:
                    raise ZeroDivisionError
                v = v / r
            else:
                v = v * r
        return v

    def unary(self):
        if self.peek() == "-":
            self.take()
            return -self.unary()
        return self.power()

    def power(self):
        b = self.atom()
        if self.peek() == "^":
            self.take()
            e = self.unary()
            if e.denominator != 1 or abs(e) > 40:
                raise ValueError
            return b ** int(e)
        return b

    def atom(self):
        t = self.take()
        if t is None:
            raise ValueError
        if t == "(":
            v = self.expr()
            if self.take() != ")":
                raise ValueError
            return v
        return Fraction(t.replace(",", ""))


def parse_all(s):
    p = P(s)
    v = p.expr()
    if p.i != len(p.t):
        raise ValueError
    return v


def candidates(left):
    """suffix expressions of `left`, longest sensible first; balanced parentheses; must contain an operator and end in digit or ')' """
    m = LEFT.search(left[-120:])
    if not m:
        return
    s = m.group(0).strip()
    # try progressively shorter starts so that a stray leading '(' or text-adjacent punctuation doesn't break parsing
    for k in range(len(s)):
        sub = s[k:].strip()
        if not sub or sub[0] in ")+x*/^%,.":
            continue
        if sub.count("(") != sub.count(")"):
            continue
        if not re.search(r"\d", sub) or not re.search(r"[+\-x*/^×−]", sub[1:]):
            continue
        if not re.search(r"[\d)]$", sub):
            continue
        yield sub


def decimals(s):
    s = s.replace(",", "")
    return len(s.split(".")[1]) if "." in s else 0


def check_text(txt):
    out = []
    for mo in re.finditer(r"(=|≈)", txt):
        left = txt[:mo.start()]
        rm = RIGHT.match(txt, mo.end())
        if not rm:
            continue
        digits, pct = rm.group(2), rm.group(3)
        # reject if the right number is the start of a longer expression (e.g. '= 3 x 4')
        tail = txt[rm.end():rm.end() + 4]
        if re.match(r"\s*[+\-x*/^×]\s*[\d(]", tail):
            continue
        try:
            stated = Fraction(digits.replace(",", ""))
        except Exception:
            continue
        done = False
        for sub in candidates(left):
            try:
                got = parse_all(sub)
            except Exception:
                continue
            done = True
            d = decimals(digits)
            tol = Fraction(1, 10 ** d) / 2 if d else Fraction(1, 2)
            ok = abs(got - stated) <= tol
            if not ok and stated != 0 and abs(got - stated) / abs(stated) <= Fraction(6, 1000):
                ok = True                                      # 'about' precision
            if not ok and (pct or "percent" in txt[rm.end():rm.end() + 12]):
                if abs(got * 100 - stated) <= tol or (stated != 0 and abs(got * 100 - stated) / abs(stated) <= Fraction(6, 1000)):
                    ok = True
            if not ok and abs(got - stated) <= max(tol, Fraction(1, 100)) * 2 and d >= 1:
                ok = True                                       # sloppy half-ulp from intermediate rounding
            out.append((sub, digits + pct, float(got), ok))
            break
    return out


if __name__ == "__main__":
    recs = load()
    flags, n = [], 0
    for r in recs:
        for mi, m in enumerate(r["messages"]):
            if m["role"] != "assistant":
                continue
            txt = re.sub(r"```.*?```", " ", m["content"], flags=re.S)
            for sub, st, got, ok in check_text(txt):
                n += 1
                if not ok:
                    flags.append({"id": r["id"], "cat": r["metadata"]["category"], "expr": sub, "stated": st, "computed": got})
    json.dump(flags, open(HERE / "arith_flags.json", "w"), indent=0)
    print("equations checked", n, "flagged", len(flags))
    for f in flags[:120]:
        print(f["id"], f["cat"], "|", f["expr"], "=", f["stated"], "(computed", round(f["computed"], 5), ")")
