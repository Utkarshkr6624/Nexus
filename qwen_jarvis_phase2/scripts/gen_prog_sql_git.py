"""Category 4d: SQL (executed on SQLite fixtures, results shown) and Git (sequences replayed in a throwaway repo)."""
import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from lib import *

CAT = "programming"
R = seeded("sql_git")
out = []

FIX = {
"company": """
CREATE TABLE departments(id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE employees(id INTEGER PRIMARY KEY, name TEXT, dept_id INTEGER, salary INTEGER, hired TEXT, manager_id INTEGER);
INSERT INTO departments VALUES (1,'Engineering'),(2,'Sales'),(3,'Support'),(4,'Legal');
INSERT INTO employees VALUES
(1,'Asha',1,120000,'2019-03-01',NULL),(2,'Brian',1,95000,'2020-07-15',1),(3,'Carla',2,70000,'2021-01-10',1),
(4,'Dev',2,64000,'2022-05-20',3),(5,'Elena',1,105000,'2018-11-02',1),(6,'Farid',3,52000,'2023-02-01',3),
(7,'Gita',3,58000,'2021-09-09',3),(8,'Hugo',2,72000,'2019-12-12',3);
""",
"shop": """
CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT, city TEXT);
CREATE TABLE orders(id INTEGER PRIMARY KEY, customer_id INTEGER, total REAL, status TEXT, ordered TEXT);
INSERT INTO customers VALUES (1,'Mia','Pune'),(2,'Noah','Delhi'),(3,'Olga','Pune'),(4,'Pablo','Mumbai'),(5,'Quinn','Delhi');
INSERT INTO orders VALUES
(1,1,250.0,'shipped','2024-01-05'),(2,1,80.5,'shipped','2024-02-11'),(3,2,430.0,'pending','2024-02-14'),(4,3,19.99,'cancelled','2024-03-01'),
(5,3,120.0,'shipped','2024-03-09'),(6,2,60.0,'shipped','2024-03-20'),(7,4,999.0,'shipped','2024-04-02'),(8,1,45.25,'pending','2024-04-05');
""",
"school": """
CREATE TABLE students(id INTEGER PRIMARY KEY, name TEXT, year INTEGER);
CREATE TABLE courses(id INTEGER PRIMARY KEY, title TEXT, credits INTEGER);
CREATE TABLE enrollments(student_id INTEGER, course_id INTEGER, grade INTEGER);
INSERT INTO students VALUES (1,'Ana',1),(2,'Bao',2),(3,'Cleo',2),(4,'Dmitri',3),(5,'Esi',1);
INSERT INTO courses VALUES (1,'Databases',4),(2,'Algorithms',4),(3,'Poetry',2),(4,'Statistics',3);
INSERT INTO enrollments VALUES (1,1,88),(1,2,92),(2,1,75),(2,3,81),(3,2,67),(3,4,90),(4,1,59),(4,2,71),(4,4,84),(5,3,95);
""",
}
SCHEMA_TXT = {
 "company": "departments(id, name), employees(id, name, dept_id, salary, hired, manager_id)",
 "shop": "customers(id, name, city), orders(id, customer_id, total, status, ordered)",
 "school": "students(id, name, year), courses(id, title, credits), enrollments(student_id, course_id, grade)",
}
Q = [
 ("company", "List every employee's name and salary, highest salary first.", "SELECT name, salary\nFROM employees\nORDER BY salary DESC;", "ORDER BY ... DESC sorts from largest to smallest."),
 ("company", "How many employees are in each department? Show the department name.", "SELECT d.name AS department, COUNT(*) AS headcount\nFROM employees e\nJOIN departments d ON d.id = e.dept_id\nGROUP BY d.name\nORDER BY headcount DESC, department;", "The JOIN attaches each employee to its department; GROUP BY then counts rows per department."),
 ("company", "What is the average salary per department?", "SELECT d.name, ROUND(AVG(e.salary), 0) AS avg_salary\nFROM employees e\nJOIN departments d ON d.id = e.dept_id\nGROUP BY d.id, d.name\nORDER BY avg_salary DESC;", "AVG is an aggregate, so non-aggregated columns must appear in GROUP BY."),
 ("company", "Which departments have no employees?", "SELECT d.name\nFROM departments d\nLEFT JOIN employees e ON e.dept_id = d.id\nWHERE e.id IS NULL;", "A LEFT JOIN keeps every department; the ones with no match have NULL employee columns, which the WHERE clause picks out."),
 ("company", "Show employees earning more than the company average.", "SELECT name, salary\nFROM employees\nWHERE salary > (SELECT AVG(salary) FROM employees)\nORDER BY salary DESC;", "The subquery computes the overall average once, and the outer query filters against it."),
 ("company", "List each employee next to their manager's name.", "SELECT e.name AS employee, m.name AS manager\nFROM employees e\nLEFT JOIN employees m ON m.id = e.manager_id\nORDER BY e.id;", "A self-join treats the same table as two roles. LEFT JOIN keeps people with no manager."),
 ("company", "Find the highest-paid employee in each department.", "SELECT d.name AS department, e.name, e.salary\nFROM employees e\nJOIN departments d ON d.id = e.dept_id\nWHERE e.salary = (SELECT MAX(salary) FROM employees WHERE dept_id = e.dept_id)\nORDER BY department;", "A correlated subquery finds the maximum salary within the same department for each row."),
 ("company", "Which departments have more than two employees?", "SELECT d.name, COUNT(*) AS headcount\nFROM employees e\nJOIN departments d ON d.id = e.dept_id\nGROUP BY d.name\nHAVING COUNT(*) > 2;", "WHERE filters rows before grouping; HAVING filters groups after aggregation."),
 ("company", "Who was hired in 2021 or later?", "SELECT name, hired\nFROM employees\nWHERE hired >= '2021-01-01'\nORDER BY hired;", "ISO dates (YYYY-MM-DD) sort and compare correctly as text."),
 ("company", "Give me the top 3 earners.", "SELECT name, salary\nFROM employees\nORDER BY salary DESC\nLIMIT 3;", "Sort first, then LIMIT keeps only the first three rows."),
 ("company", "What's the total salary cost of the Sales department?", "SELECT SUM(e.salary) AS total_cost\nFROM employees e\nJOIN departments d ON d.id = e.dept_id\nWHERE d.name = 'Sales';", "SUM adds up the salaries of the rows that survive the filter."),
 ("shop", "How much has each customer spent on shipped orders?", "SELECT c.name, ROUND(SUM(o.total), 2) AS spent\nFROM customers c\nJOIN orders o ON o.customer_id = c.id\nWHERE o.status = 'shipped'\nGROUP BY c.id, c.name\nORDER BY spent DESC;", "Filter to shipped orders first, then total them per customer."),
 ("shop", "Which customers have never placed an order?", "SELECT c.name\nFROM customers c\nWHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.id);", "NOT EXISTS keeps customers for whom the subquery finds no matching order."),
 ("shop", "Show the number of orders and average order value per city.", "SELECT c.city, COUNT(o.id) AS orders, ROUND(AVG(o.total), 2) AS avg_value\nFROM customers c\nJOIN orders o ON o.customer_id = c.id\nGROUP BY c.city\nORDER BY orders DESC;", "Grouping by city after the join aggregates all customers in each city together."),
 ("shop", "Count orders by status.", "SELECT status, COUNT(*) AS n\nFROM orders\nGROUP BY status\nORDER BY n DESC;", "GROUP BY collapses rows with the same status into one, and COUNT(*) counts the rows in each."),
 ("shop", "What was the biggest single order, and who placed it?", "SELECT c.name, o.total\nFROM orders o\nJOIN customers c ON c.id = o.customer_id\nORDER BY o.total DESC\nLIMIT 1;", "Order by total descending and keep one row."),
 ("shop", "Show monthly revenue from shipped orders.", "SELECT substr(ordered, 1, 7) AS month, ROUND(SUM(total), 2) AS revenue\nFROM orders\nWHERE status = 'shipped'\nGROUP BY month\nORDER BY month;", "substr extracts the YYYY-MM prefix of the date to use as the grouping key."),
 ("shop", "List customers with more than one order.", "SELECT c.name, COUNT(*) AS n\nFROM customers c\nJOIN orders o ON o.customer_id = c.id\nGROUP BY c.id, c.name\nHAVING COUNT(*) > 1\nORDER BY n DESC, c.name;", "HAVING applies to the grouped counts."),
 ("shop", "Which pending orders are over 100?", "SELECT id, customer_id, total\nFROM orders\nWHERE status = 'pending' AND total > 100;", "Both conditions must be true for a row to be returned."),
 ("shop", "Give each order a running total of revenue by date.", "SELECT id, ordered, total,\n       ROUND(SUM(total) OVER (ORDER BY ordered, id), 2) AS running_total\nFROM orders\nORDER BY ordered, id;", "A window function (SUM ... OVER) accumulates without collapsing the rows."),
 ("school", "What is each student's average grade?", "SELECT s.name, ROUND(AVG(e.grade), 1) AS avg_grade\nFROM students s\nJOIN enrollments e ON e.student_id = s.id\nGROUP BY s.id, s.name\nORDER BY avg_grade DESC;", "Join students to their enrollments and average the grade per student."),
 ("school", "Which courses has Ana taken, with her grade?", "SELECT c.title, e.grade\nFROM enrollments e\nJOIN courses c ON c.id = e.course_id\nJOIN students s ON s.id = e.student_id\nWHERE s.name = 'Ana';", "Two joins link enrollments to both the course and the student."),
 ("school", "Which course has the highest average grade?", "SELECT c.title, ROUND(AVG(e.grade), 1) AS avg_grade\nFROM courses c\nJOIN enrollments e ON e.course_id = c.id\nGROUP BY c.id, c.title\nORDER BY avg_grade DESC\nLIMIT 1;", "Average per course, sort descending, take the top."),
 ("school", "List students who scored above 90 in any course.", "SELECT DISTINCT s.name\nFROM students s\nJOIN enrollments e ON e.student_id = s.id\nWHERE e.grade > 90\nORDER BY s.name;", "DISTINCT removes duplicates for students with several qualifying grades."),
 ("school", "Which courses has nobody enrolled in?", "SELECT c.title\nFROM courses c\nLEFT JOIN enrollments e ON e.course_id = c.id\nWHERE e.student_id IS NULL;", "LEFT JOIN plus an IS NULL test finds courses without matches."),
 ("school", "Total credits each student is enrolled in.", "SELECT s.name, SUM(c.credits) AS credits\nFROM students s\nJOIN enrollments e ON e.student_id = s.id\nJOIN courses c ON c.id = e.course_id\nGROUP BY s.id, s.name\nORDER BY credits DESC, s.name;", "Credits live on courses, so join through enrollments and sum per student."),
 ("school", "Rank students by grade within each course.", "SELECT c.title, s.name, e.grade,\n       RANK() OVER (PARTITION BY c.id ORDER BY e.grade DESC) AS rank_in_course\nFROM enrollments e\nJOIN students s ON s.id = e.student_id\nJOIN courses c ON c.id = e.course_id\nORDER BY c.title, rank_in_course;", "PARTITION BY restarts the ranking for every course."),
 ("school", "How many students are in each year, counting only those with at least one enrollment?", "SELECT s.year, COUNT(DISTINCT s.id) AS students\nFROM students s\nJOIN enrollments e ON e.student_id = s.id\nGROUP BY s.year\nORDER BY s.year;", "COUNT(DISTINCT ...) avoids counting a student once per enrollment."),
]
ASK = ["I have these tables: {s}. {q}", "SQL question. Schema: {s}\n\n{q}", "{q} (tables: {s})", "Using SQLite with {s}, how do I answer this: {q}"]
nq = 0
for key, q, sql, why in Q:
    con = sqlite3.connect(":memory:")
    con.executescript(FIX[key])
    try:
        cur = con.execute(sql.rstrip(";"))
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    except Exception as e:
        print("SQL FAIL", q, e)
        continue
    nq += 1
    table = " | ".join(cols) + "\n" + "\n".join(" | ".join(str(v) for v in r) for r in rows[:8])
    out.append(mk(CAT, "sql_query", [("user", R.choice(ASK).format(s=SCHEMA_TXT[key], q=q)), ("assistant",
        f"```sql\n{sql}\n```\n\n{why}\n\nOn a small sample dataset it returns:\n```\n{table}\n```")], "sql_" + q[:20], "medium", "verified", pick(R, SYS_GENERAL)))
    out.append(mk(CAT, "sql_explain", [("user", f"Explain what this SQL does, line by line.\n\n```sql\n{sql}\n```"), ("assistant",
        f"This query answers: \"{q[0].lower() + q[1:]}\"\n\n{why}\n\nRead it top to bottom: SELECT lists the columns returned, FROM/JOIN says where the rows come from and how tables connect, and any WHERE, GROUP BY, HAVING, ORDER BY or LIMIT then filter, aggregate, sort and trim the result. On a small test dataset it returns:\n```\n{table}\n```")], "sql_" + q[:20], "easy", "verified", pick(R, SYS_GENERAL)))
print("sql verified:", nq)

SQLC = [
 ("What's the difference between WHERE and HAVING?", "WHERE filters individual rows before any grouping happens; HAVING filters groups after aggregation. So `WHERE salary > 50000` limits which rows are counted, while `HAVING COUNT(*) > 5` keeps only groups with more than five rows. You can't use an aggregate like COUNT inside WHERE."),
 ("INNER JOIN vs LEFT JOIN?", "An INNER JOIN returns only rows with a match in both tables. A LEFT JOIN returns every row from the left table and fills the right side's columns with NULL when there's no match. Use LEFT JOIN when you want to keep things like customers who have no orders."),
 ("What is an index and when should I add one?", "An index is a separate sorted structure that lets the database find rows without scanning the whole table, like a book's index. Add one on columns you filter, join or sort by frequently, such as foreign keys. The costs are extra storage and slower writes, so don't index everything; check query plans with EXPLAIN to see whether it's used."),
 ("What does SQL injection mean and how do I prevent it?", "SQL injection happens when user input is concatenated into a query string, so input like `'; DROP TABLE users; --` becomes part of the SQL. Prevent it by using parameterised queries (placeholders like `?` or `%s`) so the driver sends the data separately from the command, and by giving the database user only the permissions it needs."),
 ("What is a primary key versus a foreign key?", "A primary key uniquely identifies each row in a table and cannot be NULL. A foreign key is a column that references another table's primary key, which lets the database enforce that, for example, every order points to a customer that exists."),
 ("What is database normalisation?", "Normalisation is organising tables so each fact is stored once: no repeated groups, every column depends on the key, and there are no redundant copies of data that can drift out of sync. The usual target is third normal form. Sometimes you denormalise deliberately for read performance, but you should know the trade-off."),
 ("What does a transaction do?", "A transaction groups several statements so they succeed or fail together (atomicity). If something fails midway, you roll back and the database is left as it was. Combined with consistency, isolation and durability, these are the ACID properties. Example: moving money means both the debit and the credit must happen, or neither."),
 ("COUNT(*) vs COUNT(column)?", "COUNT(*) counts rows. COUNT(column) counts rows where that column is not NULL. COUNT(DISTINCT column) counts unique non-NULL values. They return different numbers whenever the column contains NULLs."),
 ("How do I paginate results in SQL?", "Use ORDER BY with LIMIT and OFFSET: `SELECT * FROM posts ORDER BY id LIMIT 20 OFFSET 40` gives page 3 of 20. OFFSET gets slow on large tables because the database still skips the earlier rows; keyset pagination (`WHERE id > last_seen_id ORDER BY id LIMIT 20`) scales better."),
 ("Why does `WHERE col = NULL` return nothing?", "NULL means unknown, and any comparison with NULL using `=` yields unknown rather than true. Use `WHERE col IS NULL` or `IS NOT NULL` instead."),
]
for q, a in SQLC:
    out.append(mk(CAT, "sql_concept", [("user", q), ("assistant", a)], "sqlc_" + q[:15], "medium", "authored", pick(R, SYS_GENERAL)))

# ---------- Git ----------
GIT = [
 ("How do I undo my last commit but keep the changes?", "git reset --soft HEAD~1", "`--soft` moves the branch back one commit but leaves your changes staged. Use `--mixed` (the default) to unstage them as well, and avoid `--hard` unless you really want to throw the changes away.", ["git commit --allow-empty -m a", "git commit --allow-empty -m b", "git reset --soft HEAD~1"]),
 ("How do I see what changed before I commit?", "git status\ngit diff            # unstaged changes\ngit diff --staged   # staged changes", "`git status` lists files, `git diff` shows unstaged edits, and `git diff --staged` shows exactly what the next commit will contain.", ["git status", "git diff", "git diff --staged"]),
 ("How do I create a new branch and switch to it?", "git switch -c feature/login", "`git switch -c` creates the branch and moves you onto it. The older equivalent is `git checkout -b feature/login`.", ["git switch -c feature/login"]),
 ("How do I rename the current branch?", "git branch -m new-name", "`-m` moves (renames) the current branch. If it's already pushed you must also push the new name and delete the old one on the remote.", ["git branch -m new-name"]),
 ("How can I delete a local branch that has been merged?", "git branch -d old-branch", "`-d` refuses to delete a branch with unmerged work, which is a safety net. Use `-D` only when you're sure you want to discard it.", ["git switch -c old-branch", "git switch -", "git branch -d old-branch"]),
 ("How do I stash my work temporarily?", "git stash push -m \"wip\"\ngit switch other-branch\n# ...later...\ngit stash pop", "Stash saves your uncommitted changes and gives you a clean working tree. `git stash list` shows saved stashes and `git stash pop` reapplies the newest one.", ["git stash list"]),
 ("How do I fix the message of my last commit?", "git commit --amend -m \"Better message\"", "Amend rewrites the most recent commit. That's fine locally, but if you've already pushed it you'll need a force push, which can disrupt collaborators.", ["git commit --allow-empty -m wrong", "git commit --amend --allow-empty -m right"]),
 ("What does git pull actually do?", "git fetch origin\ngit merge origin/main   # what `git pull` does by default", "`git pull` is a `git fetch` (download new commits) followed by a merge (or rebase, if configured) into your current branch. Running the two steps separately lets you inspect what arrived first.", ["git fetch --help"]),
 ("Merge vs rebase - when should I use each?", "git merge main        # preserves history with a merge commit\ngit rebase main       # replays your commits on top of main", "Merge keeps the true history and is safe on shared branches. Rebase makes a linear history by rewriting your commits, which is tidy for local feature branches, but you should never rebase commits others have based work on.", None),
 ("How do I see the history in a compact form?", "git log --oneline --graph --decorate --all", "`--oneline` shows one line per commit, `--graph` draws the branch structure, `--decorate` shows branch and tag names, and `--all` includes every branch.", ["git commit --allow-empty -m one", "git log --oneline --graph --decorate --all"]),
 ("How do I discard changes to one file?", "git restore path/to/file", "`git restore` replaces the file with the last committed version. This is destructive: the uncommitted edits can't be recovered. To unstage without discarding, use `git restore --staged path/to/file`.", None),
 ("How do I ignore a file that's already tracked?", "echo \"secrets.env\" >> .gitignore\ngit rm --cached secrets.env\ngit commit -m \"Stop tracking secrets.env\"", ".gitignore only affects untracked files. `git rm --cached` removes the file from the index but keeps it on disk. If it contained real secrets, rotate them, because they remain in history.", None),
 ("How do I find which commit introduced a bug?", "git bisect start\ngit bisect bad            # current commit is broken\ngit bisect good v1.2.0    # a known good commit\n# test, then mark each step good/bad until it finds the culprit\ngit bisect reset", "Bisect does a binary search through history, so even 1,000 commits need only about 10 checks.", None),
 ("How do I undo a commit that I already pushed?", "git revert <commit-sha>\ngit push", "`git revert` creates a new commit that reverses the old one, so shared history isn't rewritten. Resetting and force-pushing is possible but risky on shared branches.", None),
 ("How do I see who last changed each line of a file?", "git blame path/to/file", "`git blame` annotates every line with the commit and author that last modified it. Add `-L 10,20` to limit the range.", None),
 ("How do I copy one commit from another branch?", "git cherry-pick <commit-sha>", "Cherry-pick applies that commit's changes as a new commit on your current branch. Resolve conflicts if they appear, then `git cherry-pick --continue`.", None),
 ("How do I resolve a merge conflict?", "git status                 # see conflicted files\n# edit the files, remove <<<<<<< ======= >>>>>>> markers\ngit add path/to/file\ngit commit                 # or git merge --continue", "The markers show your version and theirs. Edit to the final text you want, mark the file resolved with `git add`, and finish the merge. `git merge --abort` returns to the state before the merge.", None),
 ("How do I set up a remote and push for the first time?", "git remote add origin https://github.com/you/project.git\ngit push -u origin main", "`remote add` names the remote, and `push -u` uploads the branch and records the upstream so later you can just run `git push`.", None),
 ("What is the difference between git reset and git revert?", "git reset --hard HEAD~1   # rewrites history, moves the branch back\ngit revert HEAD           # adds a new commit that undoes HEAD", "Reset moves the branch pointer and (depending on mode) your files, rewriting history, so use it for local-only commits. Revert adds a new commit, so it's the safe choice for anything already shared.", None),
 ("How do I tag a release?", "git tag -a v1.0.0 -m \"First release\"\ngit push origin v1.0.0", "Annotated tags (`-a`) store a message and author, and tags aren't pushed automatically, so push them explicitly.", ["git commit --allow-empty -m rel", "git tag -a v1.0.0 -m first"]),
 ("How do I recover a commit I lost after a reset?", "git reflog\ngit switch -c rescue <sha-from-reflog>", "The reflog records where HEAD has been for a while, even for commits no branch points to now. Find the commit there and create a branch at it.", ["git reflog"]),
 ("How can I clone only the latest commit to save time?", "git clone --depth 1 https://github.com/you/project.git", "A shallow clone downloads just the most recent snapshot. You can deepen it later with `git fetch --unshallow`.", None),
 ("How do I list all branches including remote ones?", "git branch -a", "Local branches are listed plainly and remote-tracking branches appear under `remotes/`.", ["git branch -a"]),
 ("How do I add only part of a file's changes to a commit?", "git add -p", "Patch mode walks through each hunk and lets you stage it (y), skip it (n) or split it (s), so you can make small, focused commits.", None),
]
ng = 0
tmp = Path(tempfile.mkdtemp(dir=os.environ.get("P2_SCRATCH", tempfile.gettempdir())))
for q, cmds, why, verify in GIT:
    if verify:
        repo = tmp / f"r{abs(hash(q))}"
        repo.mkdir(exist_ok=True)
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, env=env)
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"], cwd=repo, env=env)
        ok = True
        for c in verify:
            r = subprocess.run(c, shell=True, cwd=repo, env=env, capture_output=True, text=True)
            if r.returncode and "--help" not in c:
                ok = False
                print("GIT FAIL", c, r.stderr[:100])
        if not ok:
            continue
    ng += 1
    out.append(mk(CAT, "git", [("user", q), ("assistant", f"```bash\n{cmds}\n```\n\n{why}")], "git_" + q[:20], "easy", "verified" if verify else "authored", pick(R, SYS_GENERAL)))
shutil.rmtree(tmp, ignore_errors=True)
print("git entries:", ng)
save("prog_sql_git", out)
