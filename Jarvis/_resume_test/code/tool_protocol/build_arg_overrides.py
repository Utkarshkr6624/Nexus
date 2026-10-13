"""Writes arg_overrides.json: hand annotations for the 44 Phase 2 'direct call' flows whose assistant turn only said "I'll use `tool`" and never stated the arguments.
Each entry matches on (original user text, tool, tool-result prefix) and supplies the call arguments. Where the original user message did not contain the
information a real call needs (file content, URL, code), the entry also supplies a replacement user message that does; the converter marks such records
edited=true. Every annotation is consistent with the recorded tool result and the original final assistant answer."""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "arg_overrides.json"
E = []


def add(user, tool, result_prefix, arguments, new_user=None):
    d = {"match": {"user": user, "tool": tool, "result_prefix": result_prefix}, "arguments": arguments}
    if new_user:
        d["new_user"] = new_user
    E.append(d)


add("Find the changelog for Libfoo 9.2.", "web.search", "No results", {"query": "Libfoo 9.2 changelog"})
add("Add 'Review pull request 42' to my tasks.", "app.create_task", "ERROR 403", {"title": "Review pull request 42"})
add("Show me the last errors from the app log.", "shell.run", "PermissionError", {"command": "tail -n 20 /var/log/app.log"})
add("Did the tests pass?", "shell.run", "exit code 1", {"command": "pytest -q"})
add("Run the full test suite.", "shell.run", "ERROR: command timed out", {"command": "pytest"})
add("Read config/settings.yaml and tell me the port.", "fs.read_file", "ERROR: FileNotFoundError", {"path": "config/settings.yaml"})
add("Add 'Book flights' to my tasks.", "app.create_task", "ERROR 403", {"title": "Book flights"})
add("Read app.log and tell me the first line.", "fs.read_file", "ERROR: FileNotFoundError", {"path": "app.log"})
add("Add 'call the bank' to my tasks.", "app.create_task", "ERROR 403", {"title": "call the bank"})
add("Save my notes to resume.docx.", "fs.write_file", "ERROR: permission denied",
    {"path": "resume.docx", "content": "Experience: 5 years in logistics coordination.\nSkills: SQL, Excel, route planning.\nEducation: BSc Supply Chain Management.\n"},
    "Save my notes to resume.docx:\n\nExperience: 5 years in logistics coordination.\nSkills: SQL, Excel, route planning.\nEducation: BSc Supply Chain Management.")
add("Save the report to reports/q3.md.", "fs.write_file", "ERROR: disk quota",
    {"path": "reports/q3.md", "content": "# Q3 summary\n\nRevenue grew 8%. Churn stayed flat at 2.1%. Two launches slipped to Q4.\n"},
    "Save the report to reports/q3.md:\n\n# Q3 summary\n\nRevenue grew 8%. Churn stayed flat at 2.1%. Two launches slipped to Q4.")
add("Push my latest commit.", "shell.run", "ERROR: remote rejected", {"command": "git push"})
add("Add 'Prepare slides' to my tasks.", "app.create_task", "ERROR 403", {"title": "Prepare slides"})
add("Show me orders from yesterday.", "db.query", "rows: []", {"sql": "SELECT * FROM orders WHERE date(created_at) = date('now', '-1 day')"})
add("Count the orders from this month.", "db.query", "ERROR: relation", {"sql": "SELECT COUNT(*) FROM orders WHERE created_at >= date('now', 'start of month')"})
add("Run the tests and tell me if they pass.", "shell.run", "exit code 1", {"command": "pytest -q"})
# defective Phase 2 record: the user asked for the tests but the recorded result and answer are about a rejected git push. Repair the request to match.
add("Run the tests and tell me if they pass.", "shell.run", "ERROR: remote rejected", {"command": "git push"}, "Push my latest commit.")
add("Fetch the status page for me.", "http.request", "HTTP 503", {"method": "GET", "url": "https://status.example.com"},
    "Fetch the status page at https://status.example.com for me.")
add("Search for electric cars.", "web.search", "No results", {"query": "electric cars"})
add("Save my notes to budget.xlsx.", "fs.write_file", "ERROR: permission denied",
    {"path": "budget.xlsx", "content": "Rent 1200\nGroceries 350\nTransport 90\nSavings 400\n"},
    "Save my notes to budget.xlsx:\n\nRent 1200\nGroceries 350\nTransport 90\nSavings 400")
add("Save my notes to ideas.txt.", "fs.write_file", "ERROR: permission denied",
    {"path": "ideas.txt", "content": "- podcast about local history\n- tiny herb garden on the balcony\n- weekend pottery class\n"},
    "Save my notes to ideas.txt:\n\n- podcast about local history\n- tiny herb garden on the balcony\n- weekend pottery class")
add("Save my notes to expenses.csv.", "fs.write_file", "ERROR: permission denied",
    {"path": "expenses.csv", "content": "date,item,amount\n2025-03-02,train ticket,18.50\n2025-03-03,lunch,9.20\n"},
    "Save my notes to expenses.csv:\n\ndate,item,amount\n2025-03-02,train ticket,18.50\n2025-03-03,lunch,9.20")
add("Check if the service answers.", "http.request", "HTTP 502", {"method": "GET", "url": "http://localhost:8080/health"},
    "Check if the service at http://localhost:8080/health answers.")
add("Add 'Renew passport' to my tasks.", "app.create_task", "ERROR 403", {"title": "Renew passport"})
add("Save my notes to changelog.md.", "fs.write_file", "ERROR: permission denied",
    {"path": "changelog.md", "content": "## 1.4.0\n- Faster startup\n- Fixed login redirect\n"},
    "Save my notes to changelog.md:\n\n## 1.4.0\n- Faster startup\n- Fixed login redirect")
add("Fetch that page for me.", "web.fetch", "ERROR: timed out", {"url": "https://example.org/docs"}, "Fetch https://example.org/docs for me.")
add("Book 'Back up the laptop' for tomorrow at 10.", "app.create_event", "ERROR: conflict", {"title": "Back up the laptop", "start": "tomorrow at 10"})
add("Book a call at 10 tomorrow.", "app.create_event", "ERROR: conflict", {"title": "Call", "start": "tomorrow at 10"})
add("What does Acme charge?", "web.fetch", "Fetched 48 KB", {"url": "https://acme.example.com/pricing"},
    "What does Acme charge? Their pricing page is https://acme.example.com/pricing.")
add("Read build.log and tell me the first line.", "fs.read_file", "ERROR: FileNotFoundError", {"path": "build.log"})
add("Show me yesterday's orders.", "db.query", "0 rows", {"sql": "SELECT * FROM orders WHERE date(created_at) = date('now', '-1 day')"})
add("Book 'Prepare slides' for tomorrow at 10.", "app.create_event", "ERROR: conflict", {"title": "Prepare slides", "start": "tomorrow at 10"})
add("Add 'Back up the laptop' to my tasks.", "app.create_task", "ERROR 403", {"title": "Back up the laptop"})
add("When was Python 3.13 released?", "web.search", "Search results for", {"query": "Python 3.13 release date"})
add("Book 'Pay the electricity bill' for tomorrow at 10.", "app.create_event", "ERROR: conflict", {"title": "Pay the electricity bill", "start": "tomorrow at 10"})
add("Read thesis_outline.md and tell me the first line.", "fs.read_file", "ERROR: FileNotFoundError", {"path": "thesis_outline.md"})

# the successful write reports exactly 412 bytes, so the content written must be exactly 412 bytes (UTF-8)
plan = ("# Plan\n\n1. Outline chapter 2 (sources, structure, argument).\n2. Draft the introduction and the methods section.\n"
        "3. Email the supervisor with the revised timeline.\n4. Book the library room for Thursday afternoon.\n"
        "5. Collect the survey data and clean the missing values.\n6. Write a one-page summary of open questions.\n")
filler = ["7. Review the reference list formatting.\n", "8. Back up the project folder.\n", "9. Reply to the committee.\n", "10. Plan next week.\n"]
for f in filler:
    if len(plan.encode("utf-8")) >= 412:
        break
    plan += f
if len(plan.encode("utf-8")) > 412:
    plan = plan.encode("utf-8")[:411].decode("utf-8", "ignore").rstrip("\n") + "\n"
pad = 412 - len(plan.encode("utf-8"))
assert pad >= 0, pad
plan = plan[:-1] + (" " * pad if pad else "") + "\n" if pad else plan
assert len(plan.encode("utf-8")) == 412, len(plan.encode("utf-8"))
add("Save my plan to todo.md.", "fs.write_file", "OK: wrote 412 bytes", {"path": "todo.md", "content": plan},
    "Save my plan to todo.md:\n\n" + plan.rstrip("\n"))
add("Search for the housing market.", "web.search", "No results", {"query": "housing market"})
code = "values = []\ntotal = sum(values)\naverage = total / len(values)\nprint(average)"
add("Run this and give me the average.", "code.run_python", "Traceback", {"code": code},
    "Run this and give me the average:\n\n```python\n" + code + "\n```")
add("Save my notes to main.py.", "fs.write_file", "ERROR: permission denied",
    {"path": "main.py", "content": "def main():\n    print(\"hello\")\n\n\nif __name__ == \"__main__\":\n    main()\n"},
    "Save my notes to main.py:\n\ndef main():\n    print(\"hello\")\n\n\nif __name__ == \"__main__\":\n    main()")
add("Hit my local API and tell me what the version is.", "http.request", "ERROR: connection refused", {"method": "GET", "url": "http://localhost:8000/version"},
    "Hit my local API at http://localhost:8000/version and tell me what the version is.")
add("Book 'Write the weekly update' for tomorrow at 10.", "app.create_event", "ERROR: conflict", {"title": "Write the weekly update", "start": "tomorrow at 10"})
add("Save my notes to package.json.", "fs.write_file", "ERROR: permission denied",
    {"path": "package.json", "content": "{\n  \"name\": \"demo\",\n  \"version\": \"1.0.0\"\n}\n"},
    "Save my notes to package.json:\n\n{\n  \"name\": \"demo\",\n  \"version\": \"1.0.0\"\n}")
add("Save my notes to shopping.txt.", "fs.write_file", "ERROR: permission denied",
    {"path": "shopping.txt", "content": "milk\neggs\nspinach\nrice\n"}, "Save my notes to shopping.txt:\n\nmilk\neggs\nspinach\nrice")

# flow3 placeholder defect: "Check if the API is responding." proposed http.request(url=<health endpoint>) and then 'ran' it. Give the request a real target.
for _prefix in ("ERROR: connection refused", "HTTP 503", "HTTP 200"):
    add("Check if the API is responding.", "http.request", _prefix, {"method": "GET", "url": "http://localhost:8000/health"},
        "Check if the API at http://localhost:8000/health is responding.")

if __name__ == "__main__":
    OUT.write_text(json.dumps({"description": __doc__.strip().splitlines()[0], "entries": E}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(len(E), "overrides ->", OUT.name)
