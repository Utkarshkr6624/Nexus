"""Phase 2.1 programming: intermediate/advanced SQL (CTEs, windows, upsert, recursive CTE, self-join, CASE, EXPLAIN QUERY PLAN ...).
Every query is executed on SQLite and the real result table is shown."""
from gen21_common import *
from gen21_run import run_sql, table
import sqlite3

SCHEMAS = {
 "hr": ("employees(id, name, dept, salary, manager_id, hired)", """
CREATE TABLE employees(id INTEGER PRIMARY KEY, name TEXT, dept TEXT, salary INTEGER, manager_id INTEGER, hired TEXT);
INSERT INTO employees VALUES (1,'Maya','Eng',150,NULL,'2019-03-01'),(2,'Omar','Eng',120,1,'2020-06-15'),(3,'Lena','Eng',110,1,'2021-01-10'),
(4,'Raj','Sales',90,1,'2018-09-20'),(5,'Sofia','Sales',95,4,'2022-02-14'),(6,'Tom','Sales',70,4,'2023-05-30'),(7,'Ivy','Ops',80,1,'2020-11-02'),(8,'Ken','Ops',65,7,'2024-01-08');"""),
 "log": ("events(id, user_id, kind, at)", """
CREATE TABLE events(id INTEGER PRIMARY KEY, user_id INTEGER, kind TEXT, at TEXT);
INSERT INTO events VALUES (1,10,'login','2025-01-01'),(2,10,'view','2025-01-01'),(3,11,'login','2025-01-02'),(4,10,'login','2025-01-03'),(5,12,'login','2025-01-03'),
(6,11,'purchase','2025-01-04'),(7,10,'purchase','2025-01-05'),(8,12,'view','2025-01-05'),(9,10,'login','2025-01-06'),(10,11,'login','2025-01-06');"""),
 "inv": ("products(id, name, stock, price) and sales(id, product_id, qty, day)", """
CREATE TABLE products(id INTEGER PRIMARY KEY, name TEXT, stock INTEGER, price REAL);
CREATE TABLE sales(id INTEGER PRIMARY KEY, product_id INTEGER, qty INTEGER, day TEXT);
INSERT INTO products VALUES (1,'pen',100,1.5),(2,'notebook',40,3.0),(3,'stapler',5,12.0),(4,'tape',0,2.25);
INSERT INTO sales VALUES (1,1,30,'2025-02-01'),(2,2,10,'2025-02-01'),(3,1,25,'2025-02-02'),(4,3,2,'2025-02-02'),(5,2,15,'2025-02-03'),(6,1,40,'2025-02-03');"""),
}
Q = []


def q(schema, ask, sql, why, level="medium", sub="sql_advanced"):
    Q.append((schema, ask, sql.strip(), why, level, sub))


q("hr", "Show each employee with the average salary of their department next to it.",
  "SELECT name, dept, salary,\n       ROUND(AVG(salary) OVER (PARTITION BY dept), 1) AS dept_avg\nFROM employees\nORDER BY dept, salary DESC;",
  "A window function keeps every employee row and adds the department average beside it. A plain GROUP BY would collapse each department to one row and lose the individual salaries.")
q("hr", "Who earns more than the average salary of the company?",
  "SELECT name, salary\nFROM employees\nWHERE salary > (SELECT AVG(salary) FROM employees)\nORDER BY salary DESC;",
  "The scalar subquery computes the company-wide average once, and the outer query compares each salary to it.")
q("hr", "List each employee with their manager's name (employees without a manager should still appear).",
  "SELECT e.name AS employee, m.name AS manager\nFROM employees e\nLEFT JOIN employees m ON m.id = e.manager_id\nORDER BY e.id;",
  "This is a self-join: the same table appears twice under different aliases. LEFT JOIN keeps the top person (no manager), who gets NULL.", "medium", "sql_selfjoin")
q("hr", "Show the whole reporting chain below Raj, with depth.",
  "WITH RECURSIVE chain(id, name, depth) AS (\n  SELECT id, name, 0 FROM employees WHERE name = 'Raj'\n  UNION ALL\n  SELECT e.id, e.name, c.depth + 1\n  FROM employees e JOIN chain c ON e.manager_id = c.id\n)\nSELECT name, depth FROM chain ORDER BY depth, name;",
  "A recursive CTE starts from an anchor row (Raj) and repeatedly joins in rows whose manager is already in the result. It stops when no new rows are found.", "hard", "sql_recursive")
q("hr", "Give the top earner of each department.",
  "SELECT dept, name, salary\nFROM (\n  SELECT dept, name, salary,\n         ROW_NUMBER() OVER (PARTITION BY dept ORDER BY salary DESC) AS rn\n  FROM employees\n)\nWHERE rn = 1\nORDER BY dept;",
  "ROW_NUMBER numbers employees within each department by salary, and the outer filter keeps number 1. If you want ties kept, use RANK instead.", "medium", "sql_window")
q("hr", "Label employees as junior, mid or senior by salary (below 80, 80 to 119, 120 and above).",
  "SELECT name, salary,\n       CASE\n         WHEN salary < 80 THEN 'junior'\n         WHEN salary < 120 THEN 'mid'\n         ELSE 'senior'\n       END AS band\nFROM employees\nORDER BY salary;",
  "CASE evaluates the conditions in order and returns the first match, so each later WHEN only applies to values not caught earlier.", "easy", "sql_case")
q("hr", "How many people were hired each year?",
  "SELECT substr(hired, 1, 4) AS year, COUNT(*) AS hires\nFROM employees\nGROUP BY year\nORDER BY year;",
  "SQLite stores dates as text here, so `substr(hired, 1, 4)` extracts the year. In other databases you would use EXTRACT or YEAR instead.", "easy", "sql_dates")
q("hr", "Which departments have a total payroll above 200, and what is it?",
  "SELECT dept, SUM(salary) AS payroll, COUNT(*) AS people\nFROM employees\nGROUP BY dept\nHAVING SUM(salary) > 200\nORDER BY payroll DESC;",
  "HAVING filters after aggregation, which WHERE cannot do because the sums do not exist yet when WHERE runs.", "medium", "sql_group")
q("hr", "List each department's employees on one line.",
  "SELECT dept, GROUP_CONCAT(name, ', ') AS people\nFROM employees\nGROUP BY dept\nORDER BY dept;",
  "GROUP_CONCAT (SQLite; STRING_AGG in PostgreSQL and SQL Server) joins the values of each group into one string. The order inside the string is not guaranteed unless the database lets you specify it.", "easy", "sql_group")
q("hr", "Show the salary gap between each employee and the next lower earner in their department.",
  "SELECT dept, name, salary,\n       salary - LEAD(salary) OVER (PARTITION BY dept ORDER BY salary DESC) AS gap_to_next\nFROM employees\nORDER BY dept, salary DESC;",
  "LEAD looks at the following row inside the window. The last person in each department has no next row, so the gap is NULL.", "hard", "sql_window")
q("log", "How many distinct users logged in each day?",
  "SELECT at, COUNT(DISTINCT user_id) AS users\nFROM events\nWHERE kind = 'login'\nGROUP BY at\nORDER BY at;",
  "COUNT(DISTINCT user_id) counts each user once per day, even if they logged in several times.", "easy", "sql_group")
q("log", "Which users logged in but never purchased?",
  "SELECT DISTINCT user_id\nFROM events\nWHERE kind = 'login'\n  AND user_id NOT IN (SELECT user_id FROM events WHERE kind = 'purchase')\nORDER BY user_id;",
  "NOT IN with a subquery removes users who appear among the purchasers. Be careful: if the subquery can return NULL, NOT IN yields no rows at all, and NOT EXISTS is the safer pattern.", "medium", "sql_subquery")
q("log", "Show each user's first and last event date and how many events they had.",
  "SELECT user_id, MIN(at) AS first_day, MAX(at) AS last_day, COUNT(*) AS events\nFROM events\nGROUP BY user_id\nORDER BY user_id;",
  "MIN and MAX work on text dates because ISO 8601 (YYYY-MM-DD) strings sort in chronological order.", "easy", "sql_group")
q("log", "For each user, what was the date of their previous event?",
  "SELECT user_id, at, LAG(at) OVER (PARTITION BY user_id ORDER BY at, id) AS previous_event\nFROM events\nORDER BY user_id, at, id;",
  "LAG returns a value from the previous row in the window, here the previous event for the same user. The first event per user gets NULL.", "medium", "sql_window")
q("log", "Count events per kind as columns (one row for the whole table).",
  "SELECT SUM(kind = 'login') AS logins,\n       SUM(kind = 'view') AS views,\n       SUM(kind = 'purchase') AS purchases\nFROM events;",
  "In SQLite a comparison yields 1 or 0, so summing it counts matches. This is a simple pivot; in other databases write `SUM(CASE WHEN kind = 'login' THEN 1 ELSE 0 END)`.", "medium", "sql_pivot")
q("log", "Find days when there were more than 2 events.",
  "WITH per_day AS (\n  SELECT at, COUNT(*) AS n FROM events GROUP BY at\n)\nSELECT at, n FROM per_day WHERE n > 2 ORDER BY at;",
  "A CTE gives the intermediate result a name, so the second step reads like plain filtering. It does the same job as HAVING here, but scales better to multi-step logic.", "medium", "sql_cte")
q("inv", "Which products are low on stock (under 10) and what have they sold?",
  "SELECT p.name, p.stock, COALESCE(SUM(s.qty), 0) AS sold\nFROM products p\nLEFT JOIN sales s ON s.product_id = p.id\nWHERE p.stock < 10\nGROUP BY p.id, p.name\nORDER BY p.name;",
  "LEFT JOIN keeps products that never sold, and COALESCE turns the resulting NULL sum into 0.", "medium", "sql_join")
q("inv", "Compute revenue per product, highest first.",
  "SELECT p.name, ROUND(SUM(s.qty * p.price), 2) AS revenue\nFROM sales s\nJOIN products p ON p.id = s.product_id\nGROUP BY p.id, p.name\nORDER BY revenue DESC;",
  "Revenue is quantity times price per sale row, summed per product. Joining first makes the price available on every sale row.", "easy", "sql_join")
q("inv", "Which products have never been sold?",
  "SELECT name\nFROM products p\nWHERE NOT EXISTS (SELECT 1 FROM sales s WHERE s.product_id = p.id);",
  "NOT EXISTS checks, for each product, that no matching sale row is found. It handles NULLs more safely than NOT IN.", "medium", "sql_subquery")
q("inv", "Show a running total of units sold per product by day.",
  "SELECT product_id, day, qty,\n       SUM(qty) OVER (PARTITION BY product_id ORDER BY day, id) AS running_qty\nFROM sales\nORDER BY product_id, day, id;",
  "SUM as a window function with an ORDER BY accumulates from the start of the partition up to the current row.", "hard", "sql_window")
q("inv", "What share of total units sold does each product account for?",
  "SELECT p.name,\n       SUM(s.qty) AS units,\n       ROUND(100.0 * SUM(s.qty) / (SELECT SUM(qty) FROM sales), 1) AS pct\nFROM sales s JOIN products p ON p.id = s.product_id\nGROUP BY p.id, p.name\nORDER BY units DESC;",
  "The subquery supplies the grand total. Multiplying by 100.0 (not 100) forces decimal division instead of integer division.", "medium", "sql_subquery")
q("inv", "Find the day with the highest total quantity sold.",
  "SELECT day, SUM(qty) AS total\nFROM sales\nGROUP BY day\nORDER BY total DESC\nLIMIT 1;",
  "Group by day, sort the totals in descending order and keep the first row. Add a tie-break column if two days can share the top total.", "easy", "sql_group")
q("inv", "Restock: set stock to at least 20 for every product that is below 20, and show the result.",
  "UPDATE products SET stock = MAX(stock, 20) WHERE stock < 20;\nSELECT name, stock FROM products ORDER BY id;",
  "In SQLite, scalar MAX with two arguments returns the larger value (other databases use GREATEST). Always put a WHERE clause on UPDATE, and try it first as a SELECT.", "medium", "sql_update")

recs = []
ASKFORM = ["Schema: {s}\n\n{q}", "I have {s}. {q}", "{q}\n\nTables: {s} (SQLite)", "Using SQLite and these tables: {s}.\n\nQuestion: {q}"]
for i, (sc, ask, sql, why, level, sub) in enumerate(Q):
    sdesc, ddl = SCHEMAS[sc]
    try:
        con = sqlite3.connect(":memory:")
        con.executescript(ddl)
        last = None
        for stmt in [s for s in sql.split(";") if s.strip()]:
            cur = con.execute(stmt)
            last = cur
        cols = [c[0] for c in last.description]
        rows = last.fetchall()
    except Exception as ex:
        print("SQL FAIL", ask, ex)
        continue
    tbl = table(cols, rows)
    user = ASKFORM[i % 4].format(s=sdesc, q=ask)
    asst = f"```sql\n{sql}\n```\n\n{why}\n\nOn a small sample dataset this returns:\n\n```\n{tbl}\n```"
    recs.append(mk("programming", sub, [("user", user), ("assistant", asst)], f"p21sql-{i}", diff=level, src="verified"))

# concept/design questions around SQL (authored, no code to run besides tiny snippets)
C = [
 ("My query is slow on a 5-million-row table: SELECT * FROM orders WHERE customer_id = 42 ORDER BY created_at DESC LIMIT 10. What should I check?",
  "Check the plan with EXPLAIN first. The usual fix is a composite index on `(customer_id, created_at)`: the database can jump to the customer's rows and read them already sorted, so it can stop after ten. Without it, it may scan the table or sort every matching row. Also avoid `SELECT *` if you need only a few columns, and confirm statistics are up to date. Measure before and after, since each index slows writes a little."),
 ("When should I use a CTE instead of a subquery?",
  "Use a CTE when the query has several steps, when you reuse the same intermediate result, or when readability matters: it names each step. Use a subquery for a short one-off filter. Performance is usually equivalent in modern databases, but some engines materialise CTEs and others inline them, so check your database's behaviour if a query is slow."),
 ("Explain the difference between RANK, DENSE_RANK and ROW_NUMBER.",
  "With scores 90, 90, 80: ROW_NUMBER gives 1, 2, 3 (always unique, ties broken arbitrarily). RANK gives 1, 1, 3 (ties share a rank and the next rank is skipped). DENSE_RANK gives 1, 1, 2 (ties share a rank, no gaps). Pick ROW_NUMBER to select exactly one row per group, RANK for competition-style ranking, and DENSE_RANK for 'top N distinct values'."),
 ("How do I do an upsert in SQLite or PostgreSQL?",
  "Both support `INSERT ... ON CONFLICT`. Example: `INSERT INTO counters(name, n) VALUES ('hits', 1) ON CONFLICT(name) DO UPDATE SET n = n + 1;`. The table needs a unique constraint or primary key on `name` for the conflict target. MySQL uses `ON DUPLICATE KEY UPDATE`, and SQL Server uses `MERGE`."),
 ("UNION vs UNION ALL?",
  "UNION combines two result sets and removes duplicate rows, which requires extra work (a sort or hash). UNION ALL just appends them and keeps duplicates, and is faster. Use UNION ALL unless you actually need de-duplication. Both need the same number of columns with compatible types."),
 ("Should I store money as FLOAT?",
  "No. Binary floating point can't represent many decimal fractions exactly, so sums drift (0.1 + 0.2 is not exactly 0.3). Store money as an integer number of the smallest unit (cents) or as a fixed-point DECIMAL/NUMERIC type, and keep the currency in a separate column."),
 ("What is the N+1 query problem?",
  "It happens when code runs one query to fetch N parent rows, then one more query per parent to fetch its children, for N+1 round trips. It looks fine in development and becomes slow with real data. Fix it by fetching the related rows in one JOIN or with a single `WHERE parent_id IN (...)` query, or use your ORM's eager loading (for example `joinedload` or `selectinload` in SQLAlchemy)."),
 ("Is it okay to use SELECT * in production code?",
  "Avoid it in application queries. It transfers columns you don't need, breaks when columns are added or reordered, can defeat covering indexes, and may expose sensitive columns. It is fine for ad-hoc exploration in a console."),
 ("How do I safely run a big DELETE or UPDATE?",
  "Write it first as a SELECT with the same WHERE clause and check the row count. Run it inside a transaction so you can roll back, and check the affected rows before committing. For very large tables, work in batches to avoid long locks, and make sure you have a recent backup. Never run an UPDATE or DELETE without a WHERE clause unless you mean it."),
 ("What does a foreign key constraint buy me?",
  "It makes the database enforce referential integrity: you can't insert an order for a customer that doesn't exist, and by default you can't delete a customer who still has orders (or you choose CASCADE, SET NULL, or RESTRICT). That moves a class of bugs out of application code. Note that SQLite only enforces foreign keys if you run `PRAGMA foreign_keys = ON` on each connection."),
]
for i, (qq, aa) in enumerate(C):
    recs.append(mk("programming", "sql_design", [("user", qq), ("assistant", aa)], f"p21sqlc-{i}", diff="hard" if i < 3 else "medium", src="authored"))

if __name__ == "__main__":
    save("prog21_sql", recs)
