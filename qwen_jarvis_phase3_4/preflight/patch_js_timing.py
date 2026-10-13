"""A8: rewrite wall-clock-dependent examples as deterministic ones. Every new code block is executed here and the quoted output is the real output.
Writes patch ops to preflight/patches_js_timing.jsonl (op=replace). Original ids are those of dataset v2."""
import json
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
tmp = Path(tempfile.gettempdir()) / "p34_js"
tmp.mkdir(exist_ok=True)


def run(lang, code):
    f = tmp / ("t.py" if lang == "python" else "t.mjs")
    f.write_text(code, encoding="utf-8")
    cmd = ["python", str(f)] if lang == "python" else ["node", str(f)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


ops = []

# 1. list vs set membership (was a timeit comparison printed as True)
code = '''class Item:
    comparisons = 0
    def __init__(self, v):
        self.v = v
    def __hash__(self):
        return hash(self.v)
    def __eq__(self, other):
        Item.comparisons += 1
        return self.v == other.v

items = [Item(i) for i in range(1000)]
as_set = set(items)

Item.comparisons = 0
Item(999) in items
print("list scan comparisons:", Item.comparisons)

Item.comparisons = 0
Item(999) in as_set
print("set lookup comparisons:", Item.comparisons)'''
out = run("python", code)
ops.append(("qj2-741e6cdb0873", "timing-dependent comparison (timeit) replaced by deterministic comparison counting", [
    ("user", "Which is faster for membership tests, a list or a set? Show me.\n\n```python\n" + code + "\n```"),
    ("assistant", "Rather than timing it (timings vary by machine and load), count the work. This prints:\n\n```\n" + out +
     "\n```\n\nA list membership test scans element by element, so looking for the last of 1,000 items costs 1,000 equality comparisons: O(n). A set uses the hash to jump straight to the right bucket and compares only the candidate with the matching hash, so it needs about one comparison: O(1) on average. Building the set costs O(n) once, so it pays off when you do many lookups. On your machine, `timeit` will show the same trend, but the exact numbers will differ.")]))

# 2. TTL cache using Date.now + setTimeout
code = '''class TtlCache {
  constructor(ttlMs, now = () => Date.now()) {
    this.ttl = ttlMs;
    this.now = now;          // injectable clock, so expiry can be tested without waiting
    this.map = new Map();
  }
  set(k, v) { this.map.set(k, { v, exp: this.now() + this.ttl }); }
  get(k) {
    const e = this.map.get(k);
    if (!e) return undefined;
    if (e.exp <= this.now()) { this.map.delete(k); return undefined; }
    return e.v;
  }
}

let t = 1000;                       // fake clock
const c = new TtlCache(30, () => t);
c.set("a", 1);
console.log(c.get("a"));            // still fresh
t += 29;
console.log(c.get("a"));            // 1 ms before expiry
t += 1;
console.log(c.get("a"));            // exactly at expiry'''
out = run("js", code)
ops.append(("qj2-8d46069de3f2", "wall-clock (Date.now/setTimeout) example made deterministic with an injected clock", [
    ("user", "How do I make a simple in-memory cache with expiry in Node?\n\n```javascript\n" + code.split("\n\nlet t")[0] + "\n```\n\nHow can I test the expiry without sleeping in the test?"),
    ("assistant",
     "Store each value with an expiry timestamp, check it on read, and delete expired entries lazily. To test it without waiting, inject the clock instead of calling `Date.now()` directly, so a test can move time forward by changing a variable:\n\n```javascript\nlet t" + code.split("\n\nlet t")[1] + "\n```\n\nOutput:\n\n```\n" + out +
     "\n```\n\nIn production, leave the default clock. This cache has no size limit (add an eviction policy such as LRU for real use), and with several server instances each keeps its own copy, so use a shared cache like Redis when consistency matters.")]))

# 3. sequential vs parallel awaits (was Date.now thresholds)
code = '''const wait = (ms) => new Promise((r) => setTimeout(r, ms));
let active = 0, peak = 0;

async function request(ms) {
  active++;
  peak = Math.max(peak, active);
  await wait(ms);
  active--;
}

(async () => {
  await request(20);
  await request(20);
  console.log("sequential, peak concurrent requests:", peak);

  peak = 0;
  await Promise.all([request(20), request(20)]);
  console.log("Promise.all, peak concurrent requests:", peak);
})();'''
out = run("js", code)
ops.append(("qj2-b156e4c1035c", "elapsed-time thresholds (Date.now) replaced by a concurrency counter", [
    ("user", "Does this async code run the two requests in parallel or one after another?\n\n```javascript\n" + code + "\n```"),
    ("assistant", "Awaiting one request after the other runs them sequentially: the second does not start until the first has finished. `Promise.all` starts both before waiting on either, so they overlap. Counting how many requests are active at once shows this without depending on the clock:\n\n```\n" + out +
     "\n```\n\nSequential awaits take the sum of the durations; `Promise.all` takes roughly the longest one. Use `Promise.all` for independent operations, and keep sequential awaits when the second depends on the first.")]))

# 4. busy loop that blocks the timer (was Date.now busy-wait)
code = '''let fired = false;
setTimeout(() => { fired = true; }, 0);

(async () => {
  for (let i = 0; i < 5e7; i++) {}       // synchronous work inside an async function
  console.log("timer fired during the loop:", fired);
  await new Promise((r) => setTimeout(r, 0));   // yield to the event loop
  console.log("timer fired after yielding:", fired);
})();'''
out = run("js", code)
ops.append(("qj2-e74fe2c541a7", "busy-wait on Date.now replaced by a fixed-iteration loop", [
    ("user", "Why does this loop block the server even though I used async/await?\n\n```javascript\n" + code + "\n```"),
    ("assistant", "`async` does not make code run in the background; it only lets `await` yield. A synchronous loop runs to completion on the single thread, so the timer callback cannot run while it spins, however short the timer's delay. Output:\n\n```\n" + out +
     "\n```\n\nOnce the code awaits, the event loop regains control and the timer callback runs. For CPU-heavy work, use a worker thread or a separate process.")]))

with open(HERE / "patches_js_timing.jsonl", "w", encoding="utf-8") as f:
    for oid, reason, turns in ops:
        f.write(json.dumps({"op": "replace", "id": oid, "reason": reason, "category_tag": "js_timing",
                            "new_turns": [{"role": r, "content": c} for r, c in turns]}, ensure_ascii=False) + "\n")
print("wrote", len(ops), "js-timing patches")
