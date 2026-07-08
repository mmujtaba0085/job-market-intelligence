# Test Isolation Fix (AUTH_DB_PATH leak) — Design Spec

## Context

`src.auth.models.AUTH_DB_PATH` is a module-level global, read fresh on every
`get_auth_db()` call. Two test files redirect it to a temp file for isolation,
but both do it via raw Python attribute assignment instead of pytest's
`monkeypatch` fixture — so the change is never reverted and leaks into every
test that runs afterward in the same pytest process:

- `tests/test_auth_security.py:12` — `models.AUTH_DB_PATH = Path(tmp_path) /
  "auth.sqlite"`, inside the `secured_app` fixture (which already receives
  `monkeypatch` as a parameter, just doesn't use it for this line)
- `tests/test_full_suite.py:38` — `m.AUTH_DB_PATH = Path(tmp)`, inside a
  `_fresh_auth_db()` helper called from `TestAuthModels.setup_method`

`test_full_suite.py` compounds it: `TestAuthModels.teardown_method` (lines
50-53) deletes the temp file (`os.unlink`) without ever restoring
`AUTH_DB_PATH`, leaving the global pointing at a path that no longer exists
on disk. Any later test in the same run that does session-based auth against
a route needing the `users` table hits "no such table: users," since SQLite
auto-creates an empty file at the dangling path rather than erroring.
Discovered via `tests/test_jobs_list_sort.py` and `tests/test_orchestrator.py`
(feed-diversity work, already merged), which are simply the first tests to
combine session-auth with no `AUTH_DB_PATH` override of their own — the bug
itself predates and is unrelated to that work.

Verified scope: `grep -rn "AUTH_DB_PATH" tests/` confirms only these two
files touch it — no other test file needs a fix.

## Fix

**`tests/test_auth_security.py`** — one-line change. Replace the raw
assignment with `monkeypatch.setattr(models, "AUTH_DB_PATH", Path(tmp_path) /
"auth.sqlite")`. `monkeypatch`'s fixture finalizer guarantees the revert runs
even if the test itself fails.

**`tests/test_full_suite.py`** — `TestAuthModels` uses classic
`setup_method`/`teardown_method`, which pytest does not inject fixtures into,
so `monkeypatch` isn't directly available. Fix via manual save/restore
instead: `setup_method` records the pre-existing `AUTH_DB_PATH` value before
overwriting it; `teardown_method` restores it as the **first** statement,
before `self.conn.close()` or `os.unlink(self.tmp)` — since neither of those
is currently guaranteed not to raise (`conn.close()` isn't wrapped in
try/except today), ordering the restore first means a failure in either
cleanup step can no longer prevent `AUTH_DB_PATH` from being put back.

A full class-level refactor to pytest fixtures was considered and rejected:
it would touch the structure of all 20+ existing test methods in
`TestAuthModels` for a change a two-line reorder already fully solves —
unwarranted scope for this fix.

## Regression test

Add one test proving the leak is actually gone, not just fixed by
inspection: instantiate `TestAuthModels`, manually invoke `setup_method` then
`teardown_method` (simulating what pytest does around every test in that
class), and assert `src.auth.models.AUTH_DB_PATH` equals whatever it was
before — i.e., prove restoration happens, rather than trusting the reorder
was correct.

## Testing / validation

- The new regression test (above) must pass
- Run `pytest tests/test_full_suite.py tests/test_jobs_list_sort.py -v` —
  this exact file ordering (alphabetically, `test_full_suite.py` before
  `test_jobs_list_sort.py`) is what originally reproduced the bug; it must
  now pass cleanly with both files in the same run
- Run `pytest tests/test_auth_security.py tests/test_jobs_list_sort.py -v` —
  same check for the other previously-leaking file
- Run the full suite (`pytest tests -q`) and confirm no new failures beyond
  whatever pre-existing baseline exists independent of this fix

## Out of scope

- Any other test file — confirmed via grep that only these two touch
  `AUTH_DB_PATH`
- Refactoring `TestAuthModels`'s test structure beyond the two lifecycle
  hooks
