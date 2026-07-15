# Rotating 3-DB Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop job-data ingestion/classification writes from ever contending with live read traffic by rotating which of two SQLite files (`serving_a.sqlite` / `serving_b.sqlite`) serves reads, replacing the single-file-plus-load-aware-scheduling approach.

**Architecture:** A plain-text pointer file names the currently-Serving file. Ingestion writes to a third file, `buffer.sqlite`. Classification writes to whichever of `serving_a`/`serving_b` is currently NOT Serving ("Free"). A `rotate()` step merges Buffer into Free, flips the pointer, then refreshes the now-demoted file via `sqlite3.Connection.backup()` + `os.replace()` (atomic rename — no lock needed against readers, since an already-open file handle keeps reading its own consistent snapshot after the path is replaced). A small, separate `operational.sqlite` holds `pipeline_config`/`pipeline_runs`, which never rotate.

**Tech Stack:** Python stdlib only — `sqlite3`, `contextvars`, `os.replace`, `fcntl` (Unix; no-op on Windows, same guarded-import pattern already in `src/storage/db.py`).

## Global Constraints

- Never write "Co-Authored-By: Claude" into any commit in this repo.
- Windows dev machine: pytest's default temp dir intermittently throws `PermissionError` — every `pytest` command in this plan uses `--basetemp=<scratch dir>` (use `C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp` — create it once, reuse across tasks).
- Ponytail mode: reuse existing patterns exactly (the `fcntl` lock shape from `run_migrations()`, the `.backup()` call shape from `scripts/warehouse_rollout.py::_sqlite_backup()`, the `pipeline_config` get/set-config shape from `src/pipeline_monitor.py`). No new abstractions beyond what's specified below.
- `get_connection()`'s signature stays `get_connection() -> sqlite3.Connection` — zero call-site changes for ordinary (non-ingestion, non-classification) callers anywhere in the app.
- **Verified critical fact (not obvious from the spec, confirmed by reading the actual codebase before writing this plan):** `web_viewer.py` has its OWN independent connection function, `get_db_connection()` (≈54 call sites — the dominant read path for the whole live app: dashboard, jobs list, skills, companies, titles, all `/admin/*` pages), which calls `sqlite3.connect(DB_PATH)` directly and does **not** go through `src/storage/db.py` at all. `web_viewer.py`'s `/healthz` route also opens `DB_PATH` directly. Both must be repointed at the dynamic Serving path in Task 1, or they will silently keep reading a frozen, never-updated snapshot after rotation goes live.
- **Verified critical fact:** auth (`users`, `api_keys`, `access_logs`, `login_attempts`) already lives in a completely separate, already-independent file, `data/auth.sqlite`, via `src/auth/models.py::get_auth_db()`. It is untouched by this plan — it was never part of `DB_PATH` and needs no changes.
- **Scope decision (this plan's own call, since the spec's Scope section only named `pipeline_config`/`pipeline_runs`/"admin audit logs" as non-rotating, and auth already covers the audit-log-adjacent tables independently):** the ONLY non-rotating tables are `pipeline_config` and `pipeline_runs`. Every other table currently in `DB_PATH` (`jobs`, `job_locations`, `skills`, `weekly_metrics`, `job_categories`, `job_category_assignments`, `groq_classification_queue`, `classification_runs`, `sheets_staging`, `sheets_click_tracking`, `sheets_targets`, `sheets_target_countries`, `skill_combinations_summary`, `top_titles_summary`, the `active_jobs` view) rotates together as one group. Reasoning: `sheets_targets`/`sheets_target_countries` are FK-referenced from the rotating `sheets_staging` table — splitting them into a separate physical file would require cross-file FK handling SQLite doesn't support without `ATTACH`, which the spec never asked for. Keeping them together avoids that entirely.
- Out of scope, unaffected by this plan: `scripts/warehouse_rollout.py` (already takes `--source`/`--shadow` as explicit CLI path args; if run after this plan ships, pass `--source` pointing at whichever `serving_a.sqlite`/`serving_b.sqlite` is currently live), the warehouse-only tables it creates (`markets`, `job_market_assignments`, `source_records`, `job_source_links`, `enrichment_events` — never present in the live `DB_PATH` file), the weekly-schedule admin fix, and the per-source ingestion toggle (both explicitly deferred follow-ups per the approved spec).

---

## Task 1: Rotating-file schema split + dynamic connection resolution

This is the foundational task — nothing else in this plan works until the app can resolve "which file is Serving/Free/Buffer/Operational right now" and until the two independent read paths (`src/storage/db.py` and `web_viewer.py`) both use that resolution.

**Files:**
- Modify: `src/storage/db.py` (add pointer/path helpers, split `_run_migrations_impl()` into operational + rotating halves, add `_bootstrap_rotation_files()`, add `get_free_connection()`/`get_buffer_connection()`/`get_operational_connection()`/`use_buffer_connection()`/`use_free_connection()`/`serving_db_path()`)
- Modify: `web_viewer.py` (`get_db_connection()` at line 217, `healthz()` at line 574)
- Modify: `src/pipeline_monitor.py` (one-line import swap)
- Test: `tests/test_db_rotation_paths.py` (new)

**Interfaces:**
- Produces: `db.get_connection() -> sqlite3.Connection` (unchanged signature; now resolves dynamically — defaults to Serving, or Buffer/Free if `use_buffer_connection()`/`use_free_connection()` is active in the current context)
- Produces: `db.get_free_connection() -> sqlite3.Connection` (always the non-Serving of `serving_a`/`serving_b`)
- Produces: `db.get_buffer_connection() -> sqlite3.Connection` (always `buffer.sqlite`)
- Produces: `db.get_operational_connection() -> sqlite3.Connection` (always `operational.sqlite`; holds only `pipeline_config`/`pipeline_runs`)
- Produces: `db.use_buffer_connection()` / `db.use_free_connection()` — context managers; while active, `get_connection()` resolves to Buffer/Free instead of Serving
- Produces: `db.serving_db_path() -> Path` — the raw path of the currently-Serving file, for callers (like `web_viewer.py::get_db_connection()`) that need a path, not a ready-made connection
- Produces: `db._read_pointer() -> str` ("a" or "b"), `db._write_pointer(which: str) -> None` (atomic via `.tmp` + `os.replace`), `db._serving_path_for(which: str) -> Path`
- Consumes: nothing from other tasks (this is Task 1)

- [ ] **Step 1: Write failing tests for pointer read/write and path resolution**

Create `tests/test_db_rotation_paths.py`:

```python
"""
tests/test_db_rotation_paths.py
────────────────────────────────
Covers the pointer file (atomic read/write, atomic-replace-under-open-handle)
and the four connection-resolution functions added to src/storage/db.py for
the rotating-DB architecture (see
docs/superpowers/specs/2026-07-15-rotating-db-architecture-design.md).
"""
import sqlite3

import pytest

import src.storage.db as db


@pytest.fixture()
def isolated_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "_OPERATIONAL_DB_PATH", tmp_path / "operational.sqlite")
    monkeypatch.setattr(db, "_SERVING_A_PATH", tmp_path / "serving_a.sqlite")
    monkeypatch.setattr(db, "_SERVING_B_PATH", tmp_path / "serving_b.sqlite")
    monkeypatch.setattr(db, "_BUFFER_DB_PATH", tmp_path / "buffer.sqlite")
    monkeypatch.setattr(db, "_POINTER_PATH", tmp_path / "serving_pointer.txt")
    monkeypatch.setattr(db, "_ROTATION_LOCK_PATH", tmp_path / ".rotation.lock")
    monkeypatch.setattr(db, "_MIGRATION_LOCK_PATH", tmp_path / ".migrations.lock")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jobs.sqlite")  # legacy path, absent = fresh install
    return tmp_path


def test_read_pointer_defaults_to_a_when_missing(isolated_paths):
    assert db._read_pointer() == "a"


def test_write_then_read_pointer_round_trips(isolated_paths):
    db._write_pointer("b")
    assert db._read_pointer() == "b"


def test_write_pointer_is_atomic_replace_not_in_place_edit(isolated_paths):
    db._write_pointer("a")
    # Open a handle before the replace - on POSIX this keeps reading the old
    # inode's content even after the path is replaced (the whole reason this
    # plan uses os.replace() instead of an in-place write for the pointer
    # file, matching the same reasoning used for the demoted DB file in
    # Task 2). On Windows this same test still passes because nothing here
    # depends on POSIX-only semantics - it just confirms _write_pointer()
    # never truncates the file callers might have open.
    with open(db._POINTER_PATH) as still_open:
        db._write_pointer("b")
        assert still_open.read().strip() == "a"
    assert db._read_pointer() == "b"


def test_serving_path_for_maps_a_and_b(isolated_paths):
    assert db._serving_path_for("a") == db._SERVING_A_PATH
    assert db._serving_path_for("b") == db._SERVING_B_PATH


def test_get_connection_resolves_to_current_pointer(isolated_paths):
    db.run_migrations()
    db._write_pointer("a")
    conn = db.get_connection()
    conn.execute("INSERT INTO jobs (market_id, source_name, url, url_hash, canonical_hash, description_hash, title, raw_description, first_seen_at, last_seen_at, ingested_at) VALUES ('m','s','u','h1','c1','d1','t','','n','n','n')")
    conn.commit()
    conn.close()

    db._write_pointer("b")
    conn_b = db.get_connection()
    count_b = conn_b.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn_b.close()
    assert count_b == 0  # serving_b is a separate, empty file

    db._write_pointer("a")
    conn_a = db.get_connection()
    count_a = conn_a.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn_a.close()
    assert count_a == 1


def test_get_free_connection_is_always_the_other_file(isolated_paths):
    db.run_migrations()
    db._write_pointer("a")
    conn = db.get_free_connection()
    conn.execute("SELECT 1")  # just confirm it opens without error
    conn.close()
    assert db._free_path() == db._SERVING_B_PATH

    db._write_pointer("b")
    assert db._free_path() == db._SERVING_A_PATH


def test_use_buffer_connection_redirects_get_connection(isolated_paths):
    db.run_migrations()
    with db.use_buffer_connection():
        conn = db.get_connection()
        conn.execute("INSERT INTO jobs (market_id, source_name, url, url_hash, canonical_hash, description_hash, title, raw_description, first_seen_at, last_seen_at, ingested_at) VALUES ('m','s','u','h2','c2','d2','t','','n','n','n')")
        conn.commit()
        conn.close()

    # Outside the context manager, get_connection() is back to Serving and
    # must NOT see the row written while buffer was active.
    conn = db.get_connection()
    count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()
    assert count == 0

    buffer_conn = db.get_buffer_connection()
    buffer_count = buffer_conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    buffer_conn.close()
    assert buffer_count == 1


def test_use_free_connection_redirects_get_connection(isolated_paths):
    db.run_migrations()
    db._write_pointer("a")
    with db.use_free_connection():
        conn = db.get_connection()
        conn.execute("INSERT INTO jobs (market_id, source_name, url, url_hash, canonical_hash, description_hash, title, raw_description, first_seen_at, last_seen_at, ingested_at) VALUES ('m','s','u','h3','c3','d3','t','','n','n','n')")
        conn.commit()
        conn.close()

    free_conn = db.get_free_connection()  # should be serving_b
    free_count = free_conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    free_conn.close()
    assert free_count == 1


def test_operational_connection_has_pipeline_tables_not_jobs(isolated_paths):
    db.run_migrations()
    conn = db.get_operational_connection()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert "pipeline_config" in tables
    assert "pipeline_runs" in tables
    assert "jobs" not in tables


def test_serving_files_have_no_pipeline_config_table(isolated_paths):
    db.run_migrations()
    conn = db.get_connection()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert "jobs" in tables
    assert "pipeline_config" not in tables


def test_bootstrap_migrates_legacy_single_file_data_into_serving_a_and_operational(isolated_paths):
    # Simulate an existing pre-rotation production DB at the legacy DB_PATH,
    # with real data in both a rotating table and an operational table.
    legacy_conn = sqlite3.connect(db.DB_PATH)
    legacy_conn.executescript("""
        CREATE TABLE jobs (job_id INTEGER PRIMARY KEY, market_id TEXT, source_name TEXT, url TEXT,
            url_hash TEXT UNIQUE, canonical_hash TEXT, description_hash TEXT, title TEXT,
            raw_description TEXT, first_seen_at TEXT, last_seen_at TEXT, ingested_at TEXT);
        INSERT INTO jobs (market_id, source_name, url, url_hash, canonical_hash, description_hash, title, raw_description, first_seen_at, last_seen_at, ingested_at)
            VALUES ('m','s','u','legacy-hash','c','d','Legacy Job','','n','n','n');
        CREATE TABLE pipeline_config (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
        INSERT INTO pipeline_config (key, value, updated_at) VALUES ('ingest_interval_hours', '6', 'n');
    """)
    legacy_conn.commit()
    legacy_conn.close()

    db.run_migrations()  # triggers _bootstrap_rotation_files() since pointer file doesn't exist yet

    assert db._read_pointer() == "a"

    serving_conn = db.get_connection()
    row = serving_conn.execute("SELECT title FROM jobs WHERE url_hash = 'legacy-hash'").fetchone()
    serving_conn.close()
    assert row is not None and row["title"] == "Legacy Job"

    op_conn = db.get_operational_connection()
    cfg_row = op_conn.execute("SELECT value FROM pipeline_config WHERE key = 'ingest_interval_hours'").fetchone()
    op_conn.close()
    assert cfg_row is not None and cfg_row["value"] == "6"


def test_bootstrap_is_a_noop_once_pointer_already_exists(isolated_paths):
    db.run_migrations()
    db._write_pointer("b")  # simulate a rotation having already happened
    conn = db.get_connection()  # currently serving_b
    conn.execute("INSERT INTO jobs (market_id, source_name, url, url_hash, canonical_hash, description_hash, title, raw_description, first_seen_at, last_seen_at, ingested_at) VALUES ('m','s','u','h4','c4','d4','t','','n','n','n')")
    conn.commit()
    conn.close()

    db.run_migrations()  # must NOT re-bootstrap and must NOT flip the pointer back to 'a'

    assert db._read_pointer() == "b"
    conn = db.get_connection()
    count = conn.execute("SELECT COUNT(*) FROM jobs WHERE url_hash = 'h4'").fetchone()[0]
    conn.close()
    assert count == 1
```

Delete the unused `free_path_when_a` line in `test_get_free_connection_is_always_the_other_file` before running — it was left in accidentally; the test body below it is what matters. (Clean this up as part of writing the file, not a separate step.)

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `pytest tests/test_db_rotation_paths.py -v --basetemp=<scratch>/pytest-basetemp`
Expected: FAIL — `AttributeError: module 'src.storage.db' has no attribute '_DATA_DIR'` (none of these names exist yet).

- [ ] **Step 3: Add path constants, pointer helpers, and the four connection functions to `src/storage/db.py`**

Add near the top, after the existing `_MIGRATION_LOCK_PATH` line (`src/storage/db.py:46`):

```python
import contextvars
import os
from contextlib import contextmanager

_DATA_DIR = DB_PATH.parent
_OPERATIONAL_DB_PATH = _DATA_DIR / "operational.sqlite"
_SERVING_A_PATH = _DATA_DIR / "serving_a.sqlite"
_SERVING_B_PATH = _DATA_DIR / "serving_b.sqlite"
_BUFFER_DB_PATH = _DATA_DIR / "buffer.sqlite"
_POINTER_PATH = _DATA_DIR / "serving_pointer.txt"
_ROTATION_LOCK_PATH = _DATA_DIR / ".rotation.lock"

# Which logical target get_connection() resolves to. Defaults to "serving" -
# only orchestrator.py's ingest-only path (use_buffer_connection) and
# db_rotation.py's merge step (use_free_connection) ever change this, each
# scoped to a `with` block so it can never leak into an unrelated request.
_connection_target: contextvars.ContextVar[str] = contextvars.ContextVar(
    "connection_target", default="serving"
)


def _serving_path_for(which: str) -> Path:
    return _SERVING_A_PATH if which == "a" else _SERVING_B_PATH


def _read_pointer() -> str:
    if not _POINTER_PATH.exists():
        return "a"
    value = _POINTER_PATH.read_text(encoding="utf-8").strip()
    return value if value in ("a", "b") else "a"


def _write_pointer(which: str) -> None:
    # Atomic: a reader that calls _read_pointer() mid-write either sees the
    # old value or the new one, never a truncated/partial file.
    tmp_path = _POINTER_PATH.with_suffix(_POINTER_PATH.suffix + ".tmp")
    tmp_path.write_text(which, encoding="utf-8")
    os.replace(tmp_path, _POINTER_PATH)


def _serving_path() -> Path:
    return _serving_path_for(_read_pointer())


def _free_path() -> Path:
    other = "b" if _read_pointer() == "a" else "a"
    return _serving_path_for(other)


def serving_db_path() -> Path:
    """Public accessor for callers (web_viewer.py's own get_db_connection())
    that need the raw path rather than a ready-made connection."""
    return _serving_path()


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def get_free_connection() -> sqlite3.Connection:
    """Always the non-Serving of serving_a/serving_b. Used by the
    classification pipeline and db_rotation.py's merge step."""
    return _connect(_free_path())


def get_buffer_connection() -> sqlite3.Connection:
    """Always buffer.sqlite. Used by ingest-only ingestion writes and
    db_rotation.py's merge-then-clear step."""
    return _connect(_BUFFER_DB_PATH)


def get_operational_connection() -> sqlite3.Connection:
    """pipeline_config / pipeline_runs only - never rotates."""
    conn = sqlite3.connect(_OPERATIONAL_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def use_buffer_connection():
    token = _connection_target.set("buffer")
    try:
        yield
    finally:
        _connection_target.reset(token)


@contextmanager
def use_free_connection():
    token = _connection_target.set("free")
    try:
        yield
    finally:
        _connection_target.reset(token)
```

Replace the existing `get_connection()` (`src/storage/db.py:51-58`):

```python
def get_connection() -> sqlite3.Connection:
    """Return a SQLite connection with foreign keys enabled.

    Resolves dynamically: Serving by default, or Buffer/Free while
    use_buffer_connection()/use_free_connection() is active in the current
    context (see docs/superpowers/specs/2026-07-15-rotating-db-architecture-design.md).
    """
    target = _connection_target.get()
    if target == "buffer":
        path = _BUFFER_DB_PATH
    elif target == "free":
        path = _free_path()
    else:
        path = _serving_path()
    return _connect(path)
```

- [ ] **Step 4: Run the connection-resolution tests (not the bootstrap ones yet) to verify they still fail on the migration split**

Run: `pytest tests/test_db_rotation_paths.py -v --basetemp=<scratch>/pytest-basetemp -k "not bootstrap"`
Expected: FAIL at `db.run_migrations()` — the four files don't have any schema in them yet, since `run_migrations()` still only touches the single legacy `DB_PATH`.

- [ ] **Step 5: Split `_run_migrations_impl()` into operational + rotating halves, add bootstrap, rewrite `run_migrations()`**

In `src/storage/db.py`, cut the pipeline monitoring block (currently lines 293–330, from `# Migration 008: Pipeline monitoring tables` through the end of the `defaults` seeding loop) out of `_run_migrations_impl()` and move it into a new function:

```python
def _run_operational_migrations_impl(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS pipeline_runs (
            run_id      TEXT PRIMARY KEY,
            mode        TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'running',
            trigger     TEXT NOT NULL DEFAULT 'schedule',
            started_at  TEXT NOT NULL,
            finished_at TEXT,
            duration_seconds INTEGER,
            jobs_fetched    INTEGER DEFAULT 0,
            jobs_inserted   INTEGER DEFAULT 0,
            jobs_deduped    INTEGER DEFAULT 0,
            skills_extracted INTEGER DEFAULT 0,
            error       TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_pipeline_runs_started ON pipeline_runs(started_at DESC);

        CREATE TABLE IF NOT EXISTS pipeline_config (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)
    defaults = [
        ("ingest_interval_hours",       "12"),
        ("crawl_interval_hours",        "4"),
        ("crawl_max_runtime_minutes",   "30"),
        ("weekly_day",                  "Sunday"),
        ("weekly_time",                 "03:00"),
        ("show_source_names",           "true"),
    ]
    for key, value in defaults:
        conn.execute(
            "INSERT OR IGNORE INTO pipeline_config (key, value, updated_at) VALUES (?, ?, datetime('now'))",
            (key, value),
        )
```

Rename the existing `_run_migrations_impl()` to `_run_rotating_migrations_impl()`, remove the block you just moved out of it, and change its signature to take `conn` as a parameter instead of calling `get_connection()`/`conn.close()` itself (it currently opens its own connection at the top and closes it at the bottom — delete those two lines; the caller now owns the connection lifecycle):

```python
def _run_rotating_migrations_impl(conn: sqlite3.Connection) -> None:
    migration_files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    with conn:
        # ... EXACT existing body of the old _run_migrations_impl(), unchanged,
        # MINUS the "Migration 008: Pipeline monitoring tables" block and its
        # defaults-seeding loop (now in _run_operational_migrations_impl above)
        # and MINUS the trailing `conn.close()` (caller's responsibility now).
```

Replace `run_migrations()` itself:

```python
def run_migrations() -> None:
    """
    Idempotently apply all migrations across operational.sqlite and the three
    rotating files (serving_a, serving_b, buffer), plus the one-time legacy
    split on first run. File-locked on Unix (see _run_migrations_impl's
    original docstring reasoning, still accurate - gunicorn's N workers each
    call this at startup).
    """
    if fcntl is None:
        _run_all_migrations()
        return

    _MIGRATION_LOCK_PATH.touch(exist_ok=True)
    with open(_MIGRATION_LOCK_PATH, "r+") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            _run_all_migrations()
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _run_all_migrations() -> None:
    _bootstrap_rotation_files()

    op_conn = sqlite3.connect(_OPERATIONAL_DB_PATH)
    op_conn.row_factory = sqlite3.Row
    try:
        with op_conn:
            _run_operational_migrations_impl(op_conn)
    finally:
        op_conn.close()

    for path in (_SERVING_A_PATH, _SERVING_B_PATH, _BUFFER_DB_PATH):
        rotating_conn = sqlite3.connect(path)
        rotating_conn.row_factory = sqlite3.Row
        try:
            _run_rotating_migrations_impl(rotating_conn)
        finally:
            rotating_conn.close()


def _bootstrap_rotation_files() -> None:
    """One-time split of the legacy single-file DB_PATH into operational.sqlite
    (pipeline_config/pipeline_runs) + serving_a.sqlite (everything else),
    run once under run_migrations()'s lock. Guarded by _POINTER_PATH existing
    - once that's written, bootstrap already happened and this is a no-op.
    serving_b.sqlite and buffer.sqlite are always created fresh (empty) by
    _run_rotating_migrations_impl() below, never copied from legacy data.
    The legacy DB_PATH file is left on disk untouched (not deleted) - nothing
    writes to it after this point, but it remains as a manual recovery copy
    and stays usable by scripts/warehouse_rollout.py's --source argument."""
    if _POINTER_PATH.exists():
        return

    if DB_PATH.exists() and DB_PATH.stat().st_size > 0:
        if not _SERVING_A_PATH.exists():
            _sqlite_file_backup(DB_PATH, _SERVING_A_PATH)
        if not _OPERATIONAL_DB_PATH.exists():
            _sqlite_file_backup(DB_PATH, _OPERATIONAL_DB_PATH)

    _write_pointer("a")
    logger.info("[db] Bootstrapped rotating DB files")


def _sqlite_file_backup(source: Path, destination: Path) -> None:
    """Whole-file consistent snapshot via SQLite's Online Backup API - same
    call shape as scripts/warehouse_rollout.py::_sqlite_backup()."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_conn = sqlite3.connect(source)
    destination_conn = sqlite3.connect(destination)
    try:
        source_conn.backup(destination_conn)
    finally:
        destination_conn.close()
        source_conn.close()
```

- [ ] **Step 6: Run the full test file to verify it passes**

Run: `pytest tests/test_db_rotation_paths.py -v --basetemp=<scratch>/pytest-basetemp`
Expected: PASS (13 tests).

- [ ] **Step 7: Point `web_viewer.py`'s independent connection path at the dynamic Serving file**

In `web_viewer.py`, replace `get_db_connection()` (lines 217-233):

```python
def get_db_connection():
    """Get SQLite database connection. Falls back to .shadow.sqlite if main is unavailable."""
    from pathlib import Path as _Path
    from src.storage.db import serving_db_path
    serving_path = serving_db_path()
    candidates = [serving_path, _Path(str(serving_path).replace(".sqlite", ".shadow.sqlite"))]
    last_err = None
    for p in candidates:
        if not _Path(str(p)).exists():
            continue
        try:
            conn = sqlite3.connect(str(p), timeout=30)
            conn.execute("PRAGMA busy_timeout = 30000")
            conn.row_factory = sqlite3.Row
            conn.execute("SELECT 1 FROM active_jobs LIMIT 1")
            return conn
        except sqlite3.OperationalError as e:
            last_err = e
    raise sqlite3.OperationalError(f"Cannot open any DB: {last_err}")
```

Replace the `/healthz` route's direct connect (`web_viewer.py:574-584`):

```python
@app.route("/healthz")
def healthz():
    """Container/web health probe with a lightweight SQLite check."""
    try:
        from src.storage.db import serving_db_path
        conn = sqlite3.connect(serving_db_path(), timeout=5)
        conn.execute("SELECT 1")
        conn.close()
        return jsonify({"status": "ok", "db": "ok"}), 200
    except Exception as exc:  # noqa: BLE001
        logger.warning("[healthz] DB check failed: %s", exc)
        return jsonify({"status": "degraded", "db": "error", "error": str(exc)}), 503
```

- [ ] **Step 8: Point `pipeline_monitor.py` at the operational connection**

In `src/pipeline_monitor.py`, change the import at line 15:

```python
from src.storage.db import get_operational_connection as get_connection
```

Every other line in that file already just calls `get_connection()` — this one-line alias swap is the entire fix; no other line in `src/pipeline_monitor.py` changes.

- [ ] **Step 9: Run the existing migration-lock tests to confirm nothing regressed**

Run: `pytest tests/test_migration_lock.py -v --basetemp=<scratch>/pytest-basetemp`
Expected: 4 tests, all still PASS. (`db._run_migrations_impl` no longer exists as a name — this test file monkeypatches `db._run_migrations_impl`; since Step 5 renamed it, update `test_migration_lock.py`'s `monkeypatch.setattr(db, "_run_migrations_impl", ...)` call at line 96 to `monkeypatch.setattr(db, "_run_all_migrations", ...)` instead, matching the new top-level function `run_migrations()` actually calls. Also add `monkeypatch.setattr(db, "_POINTER_PATH", tmp_path / "serving_pointer.txt")` and the other rotation-path constants to that file's `isolated_db` fixture, same as `tests/test_db_rotation_paths.py`'s fixture, so bootstrap doesn't try to touch the real `data/` directory during this test.)

- [ ] **Step 10: Run the full existing suite to confirm nothing else regressed**

Run: `pytest tests -v --basetemp=<scratch>/pytest-basetemp -x --ignore=tests/test_auth_security.py`
Expected: all pass except the one pre-existing unrelated failure documented in this session's history (`test_login_rejects_external_next_target`, excluded above by convention — if other tests fail, they are real regressions from this task and must be fixed before continuing).

- [ ] **Step 11: Commit**

```bash
git add src/storage/db.py web_viewer.py src/pipeline_monitor.py tests/test_db_rotation_paths.py tests/test_migration_lock.py
git commit -m "feat: split DB into rotating serving files + operational.sqlite, add pointer-based dynamic connection resolution"
```

---

## Task 2: Rotation logic (`src/db_rotation.py`)

**Files:**
- Create: `src/db_rotation.py`
- Test: `tests/test_db_rotation.py`

**Interfaces:**
- Consumes: `db.get_buffer_connection()`, `db.get_free_connection()`, `db.use_free_connection()`, `db._read_pointer()`, `db._write_pointer(which)`, `db._serving_path_for(which)`, `db._sqlite_file_backup(source, destination)`, `db.fcntl`, `db._ROTATION_LOCK_PATH`, `db.upsert_job(job: JobNormalized) -> tuple[int|None, str]`, `db.insert_skills(signals: list[SkillSignal]) -> int` — all from Task 1 / pre-existing `src/storage/db.py`.
- Consumes: `src.classification.scheduling.should_process_chunk(last_request_at, other_run_active, now, idle_seconds_threshold=300) -> bool` (pre-existing, unchanged).
- Consumes: `src.pipeline_monitor.set_config(key, value)` (pre-existing, now backed by `operational.sqlite` per Task 1 Step 8).
- Produces: `rotate(last_request_at: datetime | None = None, now: datetime | None = None) -> dict` — the only public entry point. Returns `{"merged": int, "rotated": bool, "new_serving": "a"|"b"}` (`rotated=False, new_serving=<unchanged>` if skipped because the site is busy — see Step 5).

- [ ] **Step 1: Write failing tests for the merge + rotate + lock behavior**

Read `src/storage/models.py` in full first (`JobNormalized` and `SkillSignal` dataclasses — Task 2's implementation needs every field, not just the subset shown in this plan) before writing `_row_to_job_normalized()` in Step 4.

Create `tests/test_db_rotation.py`:

```python
"""
tests/test_db_rotation.py
──────────────────────────
rotate() end-to-end: Buffer merges into Free (reusing db.upsert_job()'s
existing url_hash dedup, not reinventing it), the pointer flips, and the
newly-demoted file is refreshed via backup()+os.replace() - an already-open
handle on the demoted file keeps reading ITS OWN consistent old snapshot
after the replace, which is the whole reader-safety point of this mechanism
(see docs/superpowers/specs/2026-07-15-rotating-db-architecture-design.md,
"Safety" section - this is deliberately NOT a lock against readers).
"""
from datetime import datetime, timezone

import pytest

import src.storage.db as db
import src.db_rotation as db_rotation
from src.storage.models import JobNormalized


@pytest.fixture()
def isolated_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "_OPERATIONAL_DB_PATH", tmp_path / "operational.sqlite")
    monkeypatch.setattr(db, "_SERVING_A_PATH", tmp_path / "serving_a.sqlite")
    monkeypatch.setattr(db, "_SERVING_B_PATH", tmp_path / "serving_b.sqlite")
    monkeypatch.setattr(db, "_BUFFER_DB_PATH", tmp_path / "buffer.sqlite")
    monkeypatch.setattr(db, "_POINTER_PATH", tmp_path / "serving_pointer.txt")
    monkeypatch.setattr(db, "_ROTATION_LOCK_PATH", tmp_path / ".rotation.lock")
    monkeypatch.setattr(db, "_MIGRATION_LOCK_PATH", tmp_path / ".migrations.lock")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jobs.sqlite")
    db.run_migrations()
    return tmp_path


def _job(url_hash: str, title: str) -> JobNormalized:
    return JobNormalized(
        url_hash=url_hash, canonical_hash=f"c-{url_hash}", description_hash=f"d-{url_hash}",
        job_group_id=f"g-{url_hash}"[:16], market_id="m", source_name="s",
        title=title, normalized_title=title, normalization_confidence=1.0,
        company="Acme", country="US", location="Remote", remote_type="remote",
        posted_date=None, salary_min=None, salary_max=None, currency=None,
        description_text="desc", url=f"https://example.com/{url_hash}",
    )


def test_rotate_merges_buffer_jobs_into_free_and_clears_buffer(isolated_paths):
    with db.use_buffer_connection():
        db.upsert_job(_job("buf-1", "Software Engineer"))

    result = db_rotation.rotate()

    assert result["merged"] == 1
    assert result["rotated"] is True

    free_conn = db.get_buffer_connection()
    remaining = free_conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    free_conn.close()
    assert remaining == 0  # buffer cleared after merge

    serving_conn = db.get_connection()  # now points at the newly-promoted file
    row = serving_conn.execute("SELECT title FROM jobs WHERE url_hash = 'buf-1'").fetchone()
    serving_conn.close()
    assert row is not None and row["title"] == "Software Engineer"


def test_rotate_skips_jobs_free_already_has_by_url_hash(isolated_paths):
    with db.use_free_connection():
        db.upsert_job(_job("dup-1", "Existing Title"))
    with db.use_buffer_connection():
        db.upsert_job(_job("dup-1", "Would-be Duplicate"))  # same url_hash

    result = db_rotation.rotate()

    assert result["merged"] == 0  # already present, not counted as newly merged
    serving_conn = db.get_connection()
    row = serving_conn.execute("SELECT title FROM jobs WHERE url_hash = 'dup-1'").fetchone()
    serving_conn.close()
    assert row["title"] == "Existing Title"  # untouched, not overwritten


def test_rotate_flips_the_pointer(isolated_paths):
    db._write_pointer("a")
    db_rotation.rotate()
    assert db._read_pointer() == "b"
    db_rotation.rotate()
    assert db._read_pointer() == "a"


def test_rotate_refreshes_demoted_file_and_open_handle_survives_replace(isolated_paths):
    with db.use_buffer_connection():
        db.upsert_job(_job("refresh-1", "New Job"))

    demoted_path_before = db._serving_path_for(db._read_pointer())  # current Serving, about to be demoted

    # Simulate a reader that opened the about-to-be-demoted file a moment
    # before rotation flips the pointer - it must keep working throughout.
    still_open_conn = __import__("sqlite3").connect(demoted_path_before)
    still_open_conn.execute("SELECT 1")

    db_rotation.rotate()

    # The stale handle must still be usable (reading its own old snapshot,
    # not raise "no such table" or "database is locked").
    still_open_conn.execute("SELECT 1")
    still_open_conn.close()

    # The now-demoted file on disk (same path) has been refreshed to match
    # the new Serving contents.
    demoted_conn = __import__("sqlite3").connect(demoted_path_before)
    row = demoted_conn.execute("SELECT title FROM jobs WHERE url_hash = 'refresh-1'").fetchone()
    demoted_conn.close()
    assert row is not None and row[0] == "New Job"


def test_rotate_lock_prevents_double_merge(isolated_paths, monkeypatch):
    if db.fcntl is None:
        pytest.skip("fcntl is Unix-only")

    with db.use_buffer_connection():
        db.upsert_job(_job("lock-1", "Locked Job"))

    call_order = []
    real_flock = db.fcntl.flock

    def tracking_flock(fd, operation):
        if operation == db.fcntl.LOCK_EX:
            call_order.append("lock")
        elif operation == db.fcntl.LOCK_UN:
            call_order.append("unlock")
        return real_flock(fd, operation)

    monkeypatch.setattr(db.fcntl, "flock", tracking_flock)

    db_rotation.rotate()

    assert call_order == ["lock", "unlock"]


def test_rotate_skips_when_site_busy(isolated_paths):
    with db.use_buffer_connection():
        db.upsert_job(_job("busy-1", "Should Not Merge Yet"))

    now = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
    last_request_at = datetime(2026, 7, 15, 11, 59, 55, tzinfo=timezone.utc)  # 5s ago, well under threshold

    result = db_rotation.rotate(last_request_at=last_request_at, now=now)

    assert result == {"merged": 0, "rotated": False, "new_serving": db._read_pointer()}
    buffer_conn = db.get_buffer_connection()
    remaining = buffer_conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    buffer_conn.close()
    assert remaining == 1  # untouched, still pending for next attempt


def test_rotate_proceeds_without_last_request_at(isolated_paths):
    # Callers that don't track site traffic (e.g. orchestrator.py's
    # post-ingestion trigger) simply don't pass last_request_at/now - rotate()
    # must proceed unconditionally in that case.
    with db.use_buffer_connection():
        db.upsert_job(_job("no-gate-1", "Orchestrator Triggered"))
    result = db_rotation.rotate()
    assert result["rotated"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_db_rotation.py -v --basetemp=<scratch>/pytest-basetemp`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.db_rotation'`.

- [ ] **Step 3: Implement `src/db_rotation.py`**

```python
"""
src/db_rotation.py
────────────────────
Merges Buffer into Free, flips the Serving pointer, then refreshes the
newly-demoted file - see
docs/superpowers/specs/2026-07-15-rotating-db-architecture-design.md.

rotate() is the only public entry point. Two callers: src/orchestrator.py
(right after an ingest-only run's finish_run() succeeds - no site-traffic
awareness needed there, so it calls rotate() with no arguments) and
web_viewer.py's _auto_scheduler_loop 60s fallback tick (which DOES track
site traffic via _last_request_at, and passes it through so rotate() can
defer to should_process_chunk() the same way the classification scheduler
already does - this is the "still cares about not fighting an admin doing
manual tagging mid-merge" case from the spec's Classification pipeline
changes section).
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.classification.scheduling import should_process_chunk
from src.storage import db

logger = logging.getLogger(__name__)


def rotate(last_request_at: "datetime | None" = None, now: "datetime | None" = None) -> dict:
    if last_request_at is not None or now is not None:
        check_now = now or datetime.now(timezone.utc)
        if not should_process_chunk(last_request_at, other_run_active=False, now=check_now):
            logger.info("[db_rotation] Skipped: site busy")
            return {"merged": 0, "rotated": False, "new_serving": db._read_pointer()}

    if db.fcntl is None:
        return _rotate_impl()

    db._ROTATION_LOCK_PATH.touch(exist_ok=True)
    with open(db._ROTATION_LOCK_PATH, "r+") as lock_file:
        db.fcntl.flock(lock_file, db.fcntl.LOCK_EX)
        try:
            return _rotate_impl()
        finally:
            db.fcntl.flock(lock_file, db.fcntl.LOCK_UN)


def _rotate_impl() -> dict:
    merged = _merge_buffer_into_free()

    which_before = db._read_pointer()
    which_after = "b" if which_before == "a" else "a"
    db._write_pointer(which_after)

    demoted_path = db._serving_path_for(which_before)   # was Serving, now demoted
    new_serving_path = db._serving_path_for(which_after)
    _refresh_demoted_file(source=new_serving_path, destination=demoted_path)

    from src.pipeline_monitor import set_config
    set_config("last_rotation_at", datetime.now(timezone.utc).isoformat())

    logger.info(
        "[db_rotation] Rotated %s -> %s, merged %d buffered job(s)",
        which_before, which_after, merged,
    )
    return {"merged": merged, "rotated": True, "new_serving": which_after}


def _merge_buffer_into_free() -> int:
    """Copies Buffer's jobs (+ their skills) into Free, skipping anything
    Free already has by url_hash - reuses db.upsert_job()'s existing dedup
    check rather than reimplementing it, per the spec."""
    from src.storage.models import JobNormalized, SkillSignal

    buffer_conn = db.get_buffer_connection()
    try:
        buffer_jobs = buffer_conn.execute("SELECT * FROM jobs").fetchall()
        skills_by_job_id = {}
        for job_row in buffer_jobs:
            skills_by_job_id[job_row["job_id"]] = buffer_conn.execute(
                "SELECT * FROM skills WHERE job_id = ?", (job_row["job_id"],)
            ).fetchall()
    finally:
        buffer_conn.close()

    merged = 0
    with db.use_free_connection():
        for job_row in buffer_jobs:
            job = _row_to_job_normalized(job_row)
            free_job_id, status = db.upsert_job(job)
            if status != "inserted":
                continue
            merged += 1
            signals = [
                SkillSignal(
                    job_id=free_job_id, market_id=s["market_id"],
                    raw_detected_skill=s["raw_detected_skill"], normalized_skill=s["normalized_skill"],
                    category=s["category"], confidence_score=s["confidence_score"],
                    extraction_method=s["method"],
                )
                for s in skills_by_job_id.get(job_row["job_id"], [])
            ]
            if signals:
                db.insert_skills(signals)

    buffer_conn = db.get_buffer_connection()
    try:
        with buffer_conn:
            buffer_conn.execute("DELETE FROM skills")
            buffer_conn.execute("DELETE FROM jobs")
            buffer_conn.execute("DELETE FROM job_locations")
    finally:
        buffer_conn.close()

    return merged


def _row_to_job_normalized(row: sqlite3.Row):
    """Reconstructs a JobNormalized from a raw `jobs` table row. Field names
    below must match src/storage/models.py::JobNormalized exactly - read
    that file (not just this comment) if a column is missing here, since the
    schema has grown several optional fields (salary_period, newspaper,
    ad_image_url, apply_url, all_locations) since JobNormalized was first
    written."""
    from datetime import date as _date
    from src.storage.models import JobNormalized

    posted_date = None
    if row["posted_date"]:
        posted_date = _date.fromisoformat(row["posted_date"])

    return JobNormalized(
        url_hash=row["url_hash"], canonical_hash=row["canonical_hash"],
        description_hash=row["description_hash"], job_group_id=row["job_group_id"],
        market_id=row["market_id"], source_name=row["source_name"],
        title=row["title"], normalized_title=row["normalized_title"] or row["title"],
        normalization_confidence=row["normalization_confidence"] or 0.0,
        company=row["company"], country=row["country"], location=row["location"],
        remote_type=row["remote_type"], posted_date=posted_date,
        salary_min=row["salary_min"], salary_max=row["salary_max"], currency=row["currency"],
        description_text=row["raw_description"] or "", url=row["url"],
    )


def _refresh_demoted_file(source: Path, destination: Path) -> None:
    """sqlite3.Connection.backup() (same API scripts/warehouse_rollout.py
    already uses) into a temp file, then os.replace() over the demoted file.
    Atomic rename - any request that already opened the demoted file (read
    the pointer a moment before the flip) keeps reading its own consistent
    snapshot until it closes; nothing blocks, nothing errors. This is
    deliberately NOT a lock and NOT an in-place overwrite - see the spec's
    Safety section for why."""
    import os
    tmp_path = destination.with_suffix(destination.suffix + ".tmp")
    source_conn = sqlite3.connect(source)
    tmp_conn = sqlite3.connect(tmp_path)
    try:
        source_conn.backup(tmp_conn)
    finally:
        tmp_conn.close()
        source_conn.close()
    os.replace(tmp_path, destination)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_db_rotation.py -v --basetemp=<scratch>/pytest-basetemp`
Expected: PASS (8 tests). If `_row_to_job_normalized` raises a `TypeError` about a missing/unexpected field, fix the field mapping against the actual `JobNormalized` dataclass in `src/storage/models.py` (read it in full — this plan's Step 1 note said to do this before writing the function; do it now if you skipped it).

- [ ] **Step 5: Commit**

```bash
git add src/db_rotation.py tests/test_db_rotation.py
git commit -m "feat: add rotate() - merge Buffer into Free, flip pointer, refresh demoted file"
```

---

## Task 3: Trigger wiring (config key, orchestrator hook, scheduler fallback, Buffer-routed ingestion)

**Files:**
- Modify: `src/storage/db.py` (add `rotation_max_interval_hours` default)
- Modify: `src/orchestrator.py` (`run_pipeline_for_market()`, `main()`)
- Modify: `web_viewer.py` (`_auto_scheduler_loop()`)
- Test: `tests/test_orchestrator_buffer_routing.py` (new), extend `tests/test_db_rotation.py`

**Interfaces:**
- Consumes: `db.use_buffer_connection()`, `db.get_buffer_connection()` (Task 1); `db_rotation.rotate(last_request_at=None, now=None) -> dict` (Task 2).
- Consumes: `src.pipeline_monitor.get_config() -> dict[str, str]`, `set_config(key, value)` (pre-existing, now operational-backed via Task 1).
- No new public functions produced — this task is pure call-site wiring.

- [ ] **Step 1: Write a failing test proving ingest-only writes land in Buffer, not Serving**

Add to `tests/test_db_rotation.py` (reuses the same `isolated_paths` fixture already in that file):

```python
def test_ingest_only_pipeline_writes_land_in_buffer_not_serving(isolated_paths, monkeypatch):
    from datetime import date
    from src.orchestrator import run_pipeline_for_market
    from src.storage.models import JobNormalized

    monkeypatch.setattr(
        "src.orchestrator.run_ingestion",
        lambda market, run: db.upsert_job(_job("ingest-only-1", "Buffer Bound")),
    )

    market = {"market_id": "m", "display_name": "M"}
    run_pipeline_for_market(market=market, mode="ingest-only", week_start=date(2026, 7, 13))

    buffer_conn = db.get_buffer_connection()
    buffer_count = buffer_conn.execute("SELECT COUNT(*) FROM jobs WHERE url_hash = 'ingest-only-1'").fetchone()[0]
    buffer_conn.close()
    assert buffer_count == 1

    serving_conn = db.get_connection()
    serving_count = serving_conn.execute("SELECT COUNT(*) FROM jobs WHERE url_hash = 'ingest-only-1'").fetchone()[0]
    serving_conn.close()
    assert serving_count == 0


def test_weekly_mode_pipeline_writes_land_in_serving_unchanged(isolated_paths, monkeypatch):
    from datetime import date
    from src.storage.models import JobNormalized

    monkeypatch.setattr(
        "src.orchestrator.run_ingestion",
        lambda market, run: db.upsert_job(_job("weekly-1", "Serving Bound")),
    )
    monkeypatch.setattr("src.orchestrator.run_analytics_and_report", lambda *a, **kw: None)

    from src.orchestrator import run_pipeline_for_market
    market = {"market_id": "m", "display_name": "M"}
    run_pipeline_for_market(market=market, mode="weekly", week_start=date(2026, 7, 13))

    serving_conn = db.get_connection()
    serving_count = serving_conn.execute("SELECT COUNT(*) FROM jobs WHERE url_hash = 'weekly-1'").fetchone()[0]
    serving_conn.close()
    assert serving_count == 1  # weekly mode is unchanged by this plan - still writes Serving directly
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_db_rotation.py -v --basetemp=<scratch>/pytest-basetemp -k "buffer_routing or weekly_mode"`
Expected: FAIL — both counts come out backwards (currently everything writes to whatever `get_connection()`'s default resolves to, since `run_pipeline_for_market()` doesn't distinguish ingest-only from weekly yet).

- [ ] **Step 3: Add `rotation_max_interval_hours` default to the operational migration**

In `src/storage/db.py`'s `_run_operational_migrations_impl()` (added in Task 1 Step 5), add one line to the `defaults` list:

```python
    defaults = [
        ("ingest_interval_hours",       "12"),
        ("crawl_interval_hours",        "4"),
        ("crawl_max_runtime_minutes",   "30"),
        ("weekly_day",                  "Sunday"),
        ("weekly_time",                 "03:00"),
        ("show_source_names",           "true"),
        ("rotation_max_interval_hours", "12"),
    ]
```

- [ ] **Step 4: Route ingest-only mode's ingestion through Buffer in `src/orchestrator.py`**

In `run_pipeline_for_market()` (`src/orchestrator.py:459-498`), replace:

```python
    try:
        if mode in ("weekly", "ingest-only"):
            run_ingestion(market, run)
```

with:

```python
    try:
        if mode == "ingest-only":
            from src.storage.db import use_buffer_connection
            with use_buffer_connection():
                run_ingestion(market, run)
        elif mode == "weekly":
            run_ingestion(market, run)
```

- [ ] **Step 5: Trigger `rotate()` after a successful ingest-only run in `main()`**

In `src/orchestrator.py::main()` (around line 626), replace:

```python
    finish_run(run_id, status="success", **stats)
```

with:

```python
    finish_run(run_id, status="success", **stats)

    if mode == "ingest-only":
        from src.db_rotation import rotate
        from src.storage.db import get_buffer_connection
        buffer_conn = get_buffer_connection()
        try:
            has_buffered = buffer_conn.execute("SELECT 1 FROM jobs LIMIT 1").fetchone() is not None
        finally:
            buffer_conn.close()
        if has_buffered:
            rotate()
```

- [ ] **Step 6: Run the orchestrator tests to verify they pass**

Run: `pytest tests/test_db_rotation.py -v --basetemp=<scratch>/pytest-basetemp -k "buffer_routing or weekly_mode"`
Expected: PASS.

- [ ] **Step 7: Add the 60s scheduler fallback trigger in `web_viewer.py`**

In `_auto_scheduler_loop()` (`web_viewer.py:3198-3228`), after the existing classification-scheduler block (right after the `finally: classification_conn.close()` you'll see at the end of that function), add:

```python
            from src.db_rotation import rotate
            from src.pipeline_monitor import get_config as _get_rotation_cfg
            rotation_cfg = _get_rotation_cfg()
            last_rotation_at = rotation_cfg.get("last_rotation_at")
            max_interval_hours = int(rotation_cfg.get("rotation_max_interval_hours", 12))
            rotation_due = True
            if last_rotation_at:
                from datetime import timedelta
                last_dt = datetime.fromisoformat(last_rotation_at.replace("Z", "+00:00"))
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=_tz.utc)
                rotation_due = now >= last_dt + timedelta(hours=max_interval_hours)
            if rotation_due:
                rotate(last_request_at=_last_request_at, now=now)
```

Note: this block must be indented to sit inside the same `try:` as the existing scheduler tick body (same level as the `from src.classification.scheduling import run_scheduler_tick` line above it), so a rotation-check exception doesn't take down the whole 60s loop — the existing `try/except` wrapping the entire loop body already covers it.

- [ ] **Step 8: Run the full test suite to confirm no regressions**

Run: `pytest tests -v --basetemp=<scratch>/pytest-basetemp -x --ignore=tests/test_auth_security.py`
Expected: all pass (except the one documented pre-existing failure).

- [ ] **Step 9: Commit**

```bash
git add src/storage/db.py src/orchestrator.py web_viewer.py tests/test_db_rotation.py
git commit -m "feat: wire rotation triggers - post-ingest finish_run hook + 60s scheduler fallback, route ingest-only writes through Buffer"
```

---

## Task 4: Classification pipeline switch to Free

**Files:**
- Modify: `web_viewer.py` (`_auto_scheduler_loop()`'s classification tick, 5 admin classification routes, `admin_classification()` dashboard route)
- Modify: `src/classification/scheduling.py` (drop idle-gating for `groq_backlog` and `local_full_backfill`)
- Test: extend `tests/test_classification_scheduling.py`, `tests/test_admin_classification_routes.py`

**Interfaces:**
- Consumes: `db.get_free_connection()` (Task 1).
- No signature changes to `src/classification/local_stage.py` or `src/classification/groq_stage.py` — both already take `conn` as a parameter from their caller and never call `get_connection()` internally (verified by reading both files; confirm this is still true before starting, in case another task changed them).

- [ ] **Step 1: Write failing tests for scheduling.py's idle-gating removal**

Add to `tests/test_classification_scheduling.py` (read the existing file first for its fixture conventions — it already has an in-memory or tmp-path DB fixture with `classification_runs`/`groq_classification_queue`/`jobs` tables seeded; reuse that fixture, don't build a new one):

```python
def test_groq_backlog_runs_without_idle_gating(seeded_conn):
    # seeded_conn fixture already has a pending groq_classification_queue row
    # (read the existing fixture to confirm the exact seed data - if it
    # doesn't have one, add: conn.execute("INSERT INTO groq_classification_queue (job_id, status, created_at) VALUES (1, 'pending', datetime('now'))"))
    from datetime import datetime, timezone
    from unittest.mock import patch
    from src.classification.scheduling import run_scheduler_tick

    now = datetime.now(timezone.utc)
    # last_request_at is "now" (site NOT idle) - before this task, groq_backlog
    # would be blocked by should_process_chunk's idle gate; after, it must run anyway.
    with patch("src.classification.groq_stage.process_groq_queue", return_value={"processed": 1, "succeeded": 1, "failed_technical": 0, "no_match": 0}) as mock_process:
        run_scheduler_tick(seeded_conn, last_request_at=now, now=now)
    assert mock_process.called


def test_local_full_backfill_runs_without_idle_gating(seeded_conn):
    from datetime import datetime, timezone
    from unittest.mock import patch
    from src.classification.scheduling import run_scheduler_tick

    seeded_conn.execute(
        "INSERT INTO classification_runs (run_id, run_type, trigger, status, started_at) VALUES ('r1', 'local_full_backfill', 'manual', 'running', datetime('now'))"
    )
    seeded_conn.commit()

    now = datetime.now(timezone.utc)
    with patch("src.classification.local_stage.reclassify_all", return_value={"processed": 0, "classified": 0, "queued_groq": 0}) as mock_reclassify:
        run_scheduler_tick(seeded_conn, last_request_at=now, now=now)
    assert mock_reclassify.called
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_classification_scheduling.py -v --basetemp=<scratch>/pytest-basetemp -k "without_idle_gating"`
Expected: FAIL — `mock_process`/`mock_reclassify` not called, because `last_request_at=now` (0 seconds idle) currently fails `should_process_chunk`'s 300s threshold and blocks both branches.

- [ ] **Step 3: Remove the idle-gating from `groq_backlog` and `local_full_backfill` in `src/classification/scheduling.py`**

Replace the `groq_backlog` block (`src/classification/scheduling.py:124-140`):

```python
    # groq_backlog: auto-starts when there's a backlog, chunked. No longer
    # load-gated - Free is never serving live traffic, so there's nothing to
    # protect it from (see spec's Classification pipeline changes section).
    if _has_pending_groq_backlog(conn) and not _any_run_active(conn, "groq_backlog"):
        _start_run(conn, "groq_backlog", trigger="backfill_idle")
        # Falls through to the continuation branch below on this same tick.

    if _any_run_active(conn, "groq_backlog"):
        run = conn.execute("SELECT run_id FROM classification_runs WHERE run_type = 'groq_backlog' AND status = 'running' LIMIT 1").fetchone()
        run_id = run["run_id"]
        process_groq_queue(conn, run_id=run_id, statuses=("pending",), limit=groq_chunk_size)
        if not _has_pending_groq_backlog(conn):
            _finish_run(conn, run_id, status="success")
```

Replace the `local_full_backfill` block (`src/classification/scheduling.py:142-158`):

```python
    # local_full_backfill: manual-start only (admin action creates the 'running'
    # row elsewhere); this tick only ever CONTINUES an already-started one.
    # No longer load-gated, same reasoning as groq_backlog above.
    if _any_run_active(conn, "local_full_backfill"):
        run = conn.execute("SELECT run_id, cursor_job_id FROM classification_runs WHERE run_type = 'local_full_backfill' AND status = 'running' LIMIT 1").fetchone()
        run_id = run["run_id"]
        cursor_job_id = run["cursor_job_id"]
        remaining = conn.execute("SELECT COUNT(*) FROM jobs WHERE job_id > ?", (cursor_job_id or 0,)).fetchone()[0]
        reclassify_all(conn, run_id=run_id, limit=local_chunk_size, after_job_id=cursor_job_id)
        if remaining <= local_chunk_size:
            _finish_run(conn, run_id, status="success")
```

Leave `should_process_chunk()` itself, `_groq_retry_due()`, and the `groq_retry` block entirely unchanged — `should_process_chunk` is still tested directly and is now also called from `src/db_rotation.py::rotate()` (Task 2).

- [ ] **Step 4: Run to verify the new tests pass**

Run: `pytest tests/test_classification_scheduling.py -v --basetemp=<scratch>/pytest-basetemp`
Expected: all PASS, including the two new tests and every pre-existing test in the file (re-check any pre-existing test that asserted the OLD idle-gated behavior for `groq_backlog`/`local_full_backfill` — update or remove those specific assertions, since the behavior they tested no longer exists; do not leave a test asserting the old behavior, that's a false-negative regression trap).

- [ ] **Step 5: Point the scheduler's classification tick at Free in `web_viewer.py`**

In `_auto_scheduler_loop()` (`web_viewer.py:3223-3228`), change:

```python
            from src.classification.scheduling import run_scheduler_tick
            from src.storage.db import get_connection as _get_classification_conn
            classification_conn = _get_classification_conn()
```

to:

```python
            from src.classification.scheduling import run_scheduler_tick
            from src.storage.db import get_free_connection as _get_classification_conn
            classification_conn = _get_classification_conn()
```

(The rest of that block — `try: run_scheduler_tick(...) finally: classification_conn.close()` — is unchanged.)

- [ ] **Step 6: Point the 5 admin classification action routes and the dashboard route at Free**

In `web_viewer.py`, in each of these 6 routes, change `from src.storage.db import get_connection` to `from src.storage.db import get_free_connection as get_connection` (keeps every other line in each route body unchanged, since they all just call `get_connection()`):

- `admin_classification_run_local()` (`web_viewer.py:3074-3100`)
- `admin_classification_full_reclassify_preview()` (`web_viewer.py:3103-3119`)
- `admin_classification_full_reclassify_confirm()` (`web_viewer.py:3122-3139`)
- `admin_classification_groq_run_now()` (`web_viewer.py:3142-3164`)
- `admin_classification_queue_delete()` (`web_viewer.py:3167-3175`)

And in `admin_classification()` (`web_viewer.py:3025-3071`), change:

```python
def admin_classification():
    conn = get_db_connection()
```

to:

```python
def admin_classification():
    from src.storage.db import get_free_connection
    conn = get_free_connection()
```

(This makes the admin dashboard show live classification progress on Free, matching where the work is actually happening now — reading it from Serving via `get_db_connection()` would show stale numbers until the next rotation merges Free's work back.)

- [ ] **Step 7: Write a test confirming the admin dashboard reads Free, not Serving**

Add to `tests/test_admin_classification_routes.py` (read the existing fixture conventions first — it already monkeypatches `src.storage.db.DB_PATH` per this session's earlier data-safety fix; extend that fixture with the same rotation-path monkeypatches used in `tests/test_db_rotation.py`'s `isolated_paths` fixture, then call `db.run_migrations()` in the fixture so `serving_a`/`serving_b`/`buffer`/`operational` all exist before each test):

```python
def test_admin_classification_dashboard_reads_free_not_serving(client, admin_session):
    import src.storage.db as db
    with db.use_free_connection():
        conn = db.get_connection()
        conn.execute(
            "UPDATE jobs SET field_classification_method = 'local_hybrid_v1' WHERE job_id = 1"
        )
        conn.commit()
        conn.close()

    resp = client.get("/admin/classification")
    assert resp.status_code == 200
    assert b"1" in resp.data  # classified_local count reflects the Free-side write
```

- [ ] **Step 8: Run the full classification + admin test files**

Run: `pytest tests/test_classification_scheduling.py tests/test_admin_classification_routes.py tests/test_local_classification_stage.py tests/test_groq_classification_stage.py -v --basetemp=<scratch>/pytest-basetemp`
Expected: all PASS.

- [ ] **Step 9: Run the full suite**

Run: `pytest tests -v --basetemp=<scratch>/pytest-basetemp -x --ignore=tests/test_auth_security.py`
Expected: all pass (except the one documented pre-existing failure).

- [ ] **Step 10: Commit**

```bash
git add web_viewer.py src/classification/scheduling.py tests/test_classification_scheduling.py tests/test_admin_classification_routes.py
git commit -m "feat: classification pipeline operates on Free unconditionally, drop idle-gating for groq_backlog and local_full_backfill"
```

---

## Task 5: Admin UI — Rotate Now button + interval config

**Files:**
- Modify: `web_viewer.py` (`admin_pipeline()`, `admin_pipeline_config()`, new `admin_pipeline_rotate()` route)
- Modify: `templates/admin_pipeline.html`
- Test: `tests/test_admin_pipeline_rotate_route.py` (new)

**Interfaces:**
- Consumes: `db_rotation.rotate() -> dict` (Task 2), `pipeline_monitor.get_config()`/`set_config()` (pre-existing).

- [ ] **Step 1: Write a failing test for the new route**

Create `tests/test_admin_pipeline_rotate_route.py` (match the auth/fixture pattern of `tests/test_admin_classification_routes.py` — read it first for the exact `client`/`admin_session` fixture setup, including the `DB_PATH`/rotation-path monkeypatches, and reuse it rather than redefining it):

```python
def test_rotate_now_route_calls_rotate_and_returns_result(client, admin_session, monkeypatch):
    from unittest.mock import Mock
    mock_rotate = Mock(return_value={"merged": 3, "rotated": True, "new_serving": "b"})
    monkeypatch.setattr("src.db_rotation.rotate", mock_rotate)

    resp = client.post("/admin/pipeline/rotate")

    assert resp.status_code == 200
    assert resp.get_json() == {"merged": 3, "rotated": True, "new_serving": "b"}
    mock_rotate.assert_called_once_with()


def test_rotate_now_route_requires_admin(client):
    resp = client.post("/admin/pipeline/rotate")
    assert resp.status_code in (302, 401, 403)


def test_pipeline_config_accepts_rotation_max_interval_hours(client, admin_session):
    resp = client.post("/admin/pipeline/config", data={"rotation_max_interval_hours": "6"})
    assert resp.status_code == 200
    assert "rotation_max_interval_hours" in resp.get_json()["updated"]

    from src.pipeline_monitor import get_config
    assert get_config()["rotation_max_interval_hours"] == "6"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_admin_pipeline_rotate_route.py -v --basetemp=<scratch>/pytest-basetemp`
Expected: FAIL — 404 (`/admin/pipeline/rotate` doesn't exist), and `rotation_max_interval_hours` isn't in `admin_pipeline_config()`'s `allowed` set yet.

- [ ] **Step 3: Add the route and extend the config allowlist in `web_viewer.py`**

Add a new route right after `admin_pipeline_run()` (`web_viewer.py:2955-2967`):

```python
@app.route("/admin/pipeline/rotate", methods=["POST"])
@require_admin
def admin_pipeline_rotate():
    from src.db_rotation import rotate
    result = rotate()
    return jsonify(result)
```

In `admin_pipeline_config()` (`web_viewer.py:2970-2981`), change:

```python
    allowed = {"ingest_interval_hours", "crawl_interval_hours", "crawl_max_runtime_minutes"}
```

to:

```python
    allowed = {"ingest_interval_hours", "crawl_interval_hours", "crawl_max_runtime_minutes", "rotation_max_interval_hours"}
```

- [ ] **Step 4: Run the route tests to verify they pass**

Run: `pytest tests/test_admin_pipeline_rotate_route.py -v --basetemp=<scratch>/pytest-basetemp`
Expected: PASS.

- [ ] **Step 5: Add the "Rotate Now" button and interval field to `templates/admin_pipeline.html`**

In the "Adjust Intervals" form (`templates/admin_pipeline.html:55-82`), add a fourth field to the 3-column grid — change the grid to 4 columns and add the field:

```html
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:0.5rem;margin-bottom:0.75rem">
          <label style="font-size:0.8rem">
            Ingest (h)
            <input name="ingest_interval_hours" type="number" min="1" max="168"
              value="{{ config.ingest_interval_hours }}"
              style="width:100%;margin-top:0.25rem;padding:0.35rem;border:1px solid #d1d5db;border-radius:4px">
          </label>
          <label style="font-size:0.8rem">
            Crawl (h)
            <input name="crawl_interval_hours" type="number" min="1" max="48"
              value="{{ config.crawl_interval_hours }}"
              style="width:100%;margin-top:0.25rem;padding:0.35rem;border:1px solid #d1d5db;border-radius:4px">
          </label>
          <label style="font-size:0.8rem">
            Crawl max (min)
            <input name="crawl_max_runtime_minutes" type="number" min="5" max="120"
              value="{{ config.crawl_max_runtime_minutes }}"
              style="width:100%;margin-top:0.25rem;padding:0.35rem;border:1px solid #d1d5db;border-radius:4px">
          </label>
          <label style="font-size:0.8rem">
            Rotation (h)
            <input name="rotation_max_interval_hours" type="number" min="1" max="72"
              value="{{ config.rotation_max_interval_hours }}"
              style="width:100%;margin-top:0.25rem;padding:0.35rem;border:1px solid #d1d5db;border-radius:4px">
          </label>
        </div>
```

Add a "Rotate Now" button to the "Run Now" card (`templates/admin_pipeline.html:85-115`), right after the closing `</button>` of the "Report-only" button and before the closing `</div>` of that button group:

```html
        <button onclick="triggerRotate()" class="run-btn"
          style="background:#0891b2;color:#fff;border:none;border-radius:6px;padding:0.75rem 1rem;cursor:pointer;text-align:left;font-size:0.875rem">
          <strong>Rotate Now</strong>
          <span style="display:block;font-size:0.75rem;opacity:.8;margin-top:2px">Merge Buffer into Free, flip Serving pointer</span>
        </button>
```

Add the JS handler, right after the existing `triggerRun()` function (`templates/admin_pipeline.html:182-213`):

```html
<script>
async function triggerRotate() {
  const btn = document.querySelector('button[onclick="triggerRotate()"]');
  btn.disabled = true;
  btn.style.opacity = '0.6';
  const msg = document.getElementById('run-msg');
  msg.style.display = 'block';
  msg.style.color = '#6b7280';
  msg.textContent = 'Rotating…';

  try {
    const r = await fetch('/admin/pipeline/rotate', { method: 'POST', headers: {'X-CSRFToken': csrf} });
    const data = await r.json();
    if (r.ok) {
      msg.style.color = '#059669';
      msg.textContent = data.rotated
        ? `Rotated: merged ${data.merged} job(s), now serving ${data.new_serving}.`
        : 'Skipped: site busy, try again shortly.';
    } else {
      msg.style.color = '#dc2626';
      msg.textContent = data.error || 'Failed to rotate';
    }
  } catch(e) {
    msg.style.color = '#dc2626';
    msg.textContent = 'Network error';
  } finally {
    btn.disabled = false;
    btn.style.opacity = '1';
  }
}
</script>
```

Place this `<script>` block immediately before the existing closing `</script>` tag at the bottom of the file (inside the same block, not a second separate `<script>` tag — merge it into the existing script rather than adding a new tag), so `csrf` (already declared `const csrf = "{{ csrf_token() }}";` at the top of the existing script) is in scope.

- [ ] **Step 6: Manually verify the page renders (this repo's convention for UI changes — start the dev server and check the feature in a browser)**

Run: `python web_viewer.py` (or the project's existing dev-server launch command — check `README.md`/`docs/SETUP_AND_OPERATIONS.md` if `python web_viewer.py` isn't it), sign in as admin, open `/admin/pipeline`, confirm:
- The "Adjust Intervals" form now shows 4 fields including "Rotation (h)" with the seeded default value.
- A "Rotate Now" button appears in the "Run Now" card.
- Clicking "Rotate Now" shows a status message and does not throw a JS console error (check browser dev tools).

- [ ] **Step 7: Run the full suite one final time**

Run: `pytest tests -v --basetemp=<scratch>/pytest-basetemp -x --ignore=tests/test_auth_security.py`
Expected: all pass (except the one documented pre-existing failure).

- [ ] **Step 8: Commit**

```bash
git add web_viewer.py templates/admin_pipeline.html tests/test_admin_pipeline_rotate_route.py
git commit -m "feat: add Rotate Now button and rotation interval config to admin pipeline UI"
```

---

## Post-plan verification (manual, on the VPS, after deploy)

Not a task with its own commit — a checklist for whoever deploys this:

1. Deploy, then check `docker compose logs web | grep -i bootstrap` for `"Bootstrapped rotating DB files"` exactly once across all 4 workers (the `fcntl` lock should prevent duplicate bootstrap attempts — same verification approach already used for the migration-lock fix this session: `docker inspect jobmarket-web --format '{{.RestartCount}}'` should read `0`).
2. Confirm `/healthz` returns 200.
3. Confirm `/jobs` and `/dashboard` still show the full existing job count (proves `serving_a.sqlite` correctly inherited all legacy data from the bootstrap split).
4. Confirm `/admin/pipeline` shows the pre-existing run history (proves `operational.sqlite` correctly inherited `pipeline_runs`).
5. Trigger "Ingest-only" from `/admin/pipeline`, wait for it to finish, then check `/admin/pipeline/logs/<run_id>` for a `"[db_rotation] Rotated"` log line (proves the post-`finish_run()` trigger fired).
6. Confirm `/jobs` still serves correctly during and immediately after that rotation (proves the atomic-rename reader safety actually holds under real gunicorn worker concurrency, not just the tmp_path test).
