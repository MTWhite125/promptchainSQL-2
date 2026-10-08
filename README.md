# MiniDB

A database engine written from scratch in Python, with no runtime dependencies.
Includes a SQL tokenizer and parser, relational tables, typed columns, validation,
transactions, persistent JSON storage, and a command-line shell. It does not wrap SQLite.

## Run

Requires Python 3.10+ on macOS or Linux (file locking uses `fcntl`).

```sh
cd /Users/mtwhite/python_database_engine
python3 example.py
python3 -m minidb database.json
python3 -m unittest discover -s tests -v
```

Optional installation: `python3 -m pip install -e .` exposes the `minidb` command.

## Web server

Start the browser interface with no additional dependencies:

```sh
python3 -m minidb.server database.json --port 8000
```

Open **http://127.0.0.1:8000** in your browser. After optional installation,
`minidb-server database.json --port 8000` runs the same server.

The page includes a SQL editor, table browser with schemas, a scrollable results
grid, CSV export, and Begin / Commit / Rollback buttons. Run one statement at a
time, select a statement to run only that selection, or use Command/Ctrl+Enter.
Clicking a table runs `SELECT * ... LIMIT 1000`. Start a different server command
with a different database path to open another database.

Writes outside transactions save immediately. Transactions belong to the browser
client that started them; other clients receive HTTP 409 until commit or rollback.
After five minutes without a request from the owner, the next authenticated
request rolls back the expired transaction. Closing the tab warns about pending
changes. Stopping the server with Ctrl+C rolls back uncommitted changes.

The server listens on loopback only. It is a local desktop tool, not an Internet
hosting service. Host/origin checks and a per-start API token protect browser
requests. All database operations are serialized, including requests from multiple
tabs. Close the CLI or desktop GUI connection before using the same database file.

### HTTP API

- `GET /api/state` returns the database path, transaction status, tables, and schemas.
- `POST /api/query` accepts JSON such as `{"sql": "SELECT * FROM people"}`.
  SELECT returns `rows`, `columns`, and `state`; writes return `affected` and `state`.
- API requests require `X-MiniDB-Token` (the page's `minidb-token` meta tag) and
  `X-MiniDB-Client` (a stable identifier for your client). The browser sets these
  automatically. Tokens change when the server restarts; reload the page then.
- Errors return JSON with an `error` field. SQL errors use HTTP 400, authorization
  errors 403, transaction conflicts 409, and storage errors 500.

CSV exports NULL as an empty field. Large result sets can take time to render;
use LIMIT when exploring large tables. Run the engine and HTTP tests with
`python3 -m unittest discover -s tests -v` (requires local socket access).

## Desktop GUI

```sh
python3 -m minidb.gui
# Or open a persistent database immediately:
python3 -m minidb.gui database.json
```

The GUI uses Python's Tkinter, with no extra Python packages. Your Python build
must include Tk support. After optional installation, `minidb-gui` also launches it.

- Use **New database** to create a persistent JSON file, or **Open database**
  to open an existing one. Starting without a file uses an in-memory database.
- Enter one SQL statement and click **Run SQL**, or press Command+Enter on
  macOS / Ctrl+Enter. Select a statement to run only that selection.
- Select a table to inspect its schema; click **Browse selected table** to
  display up to 1,000 rows. Use a SELECT query for different filters or limits.
- Read query results in the scrollable grid and save them with **Export CSV**.
  NULL displays as `⟨NULL⟩` and exports as an empty CSV field.
- Writes save automatically outside transactions. **Begin**, **Commit**, and
  **Rollback** control transactions. Closing or switching databases during a
  transaction prompts to commit, roll back, or cancel.

SQL errors appear in a dialog and the status bar. Browsing uses the same engine
and lock rules as the CLI, so close another connection before opening its file.
Large queries run on the GUI thread and can temporarily pause the interface.
The SQL editor starts with an example CREATE statement; run it, then replace it
with INSERT or SELECT statements from the examples below.

To verify the desktop integration on a machine with GUI access:

```sh
python3 tests/gui_smoke.py
```

## SQL example

Enter one statement per line in the shell:

```sql
CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, score REAL);
INSERT INTO users VALUES (1, 'Ada', 9.5);
INSERT INTO users (id, name) VALUES (2, 'Grace');
SELECT name, score FROM users WHERE id >= 1 ORDER BY score DESC LIMIT 10;
UPDATE users SET score = 10 WHERE id = 2;
DELETE FROM users WHERE id = 1;
BEGIN;
INSERT INTO users VALUES (3, 'Linus', 8);
ROLLBACK;
```

Use `.quit` to exit and `.help` for a reminder of supported statements.
Queries return JSON arrays of rows. Writes return the number of affected rows;
DDL and transaction commands return zero. `--execute` / `-e` runs one statement
and returns a nonzero exit status on errors.

## Python API

```python
from minidb import Database, DatabaseError

with Database('app.json') as db:
    db.execute('CREATE TABLE tasks (id INTEGER PRIMARY KEY, title TEXT NOT NULL)')
    with db.transaction():
        db.execute("INSERT INTO tasks VALUES (1, 'Learn databases')")
    rows = db.execute('SELECT * FROM tasks')
```

Use `Database()` for an in-memory database. The connection context closes the
connection; `db.transaction()` commits on success and rolls back on exceptions.
Closing a connection rolls back any active transaction. Failed statements restore
their previous state while leaving an enclosing transaction usable.

## Supported behavior

- `CREATE TABLE`, `DROP TABLE`, `INSERT`, `SELECT`, `UPDATE`, `DELETE`.
- `INTEGER`, `REAL`, `TEXT`, `NULL`, `PRIMARY KEY`, and `NOT NULL`.
- One primary key per table, with uniqueness and non-null enforcement.
- Optional INSERT column lists; omitted nullable columns become NULL.
- WHERE comparisons `=`, `!=`, `<>`, `<`, `>`, `<=`, `>=`, joined by `AND`.
- `IS NULL` and `IS NOT NULL`. Ordinary comparisons involving NULL do not match.
- Single-column `ORDER BY ... ASC|DESC`, followed by optional `LIMIT`.
- Case-insensitive identifiers; string literals use single quotes, with `''`
  to escape a quote. NULL sorts first ascending and last descending.
- Explicit `BEGIN`, `COMMIT`, `ROLLBACK`; writes otherwise commit immediately.

## Storage and scope

The complete database lives in memory and is saved as versioned JSON. Saving
flushes and fsyncs a temporary file before atomically replacing the database file.
A sidecar advisory lock permits one open disk connection at a time and prevents
lost updates between cooperating connections. Keep the database and lock file
on a local filesystem; do not remove the lock file while a connection is open.

This is an educational engine for small datasets. Queries scan rows, writes copy
the database for statement rollback, and commits rewrite the whole file. There
are no indexes, joins, aggregates, foreign keys, parameter binding, SQL comments,
multiline shell statements, nested transactions, or multi-statement execution.
Use trusted SQL; do not interpolate untrusted input. Connections are not thread
safe. Atomic replacement protects against partial file writes, but this engine
has no write-ahead log or full power-loss durability guarantee.
