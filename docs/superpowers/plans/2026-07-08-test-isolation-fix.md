# Test Isolation Fix (AUTH_DB_PATH leak) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `tests/test_auth_security.py` and `tests/test_full_suite.py` from permanently leaking a mutated `src.auth.models.AUTH_DB_PATH` into every test that runs after them in the same pytest session.

**Architecture:** Both files redirect the module-global `AUTH_DB_PATH` to a temp file for isolation, via raw attribute assignment instead of something that reverts. `test_auth_security.py`'s fixture already receives `monkeypatch` — swap the raw assignment for `monkeypatch.setattr`. `test_full_suite.py`'s `TestAuthModels` uses classic `setup_method`/`teardown_method`, which pytest doesn't inject fixtures into, so it gets a manual save/restore instead, with the restore ordered first in teardown so a failure in later cleanup steps can't skip it.

**Tech Stack:** pytest (`monkeypatch` fixture, `setup_method`/`teardown_method` xunit-style hooks).

## Global Constraints

- Only two files touch `AUTH_DB_PATH` in the test suite (confirmed via `grep -rn "AUTH_DB_PATH" tests/`) — don't touch any other file
- No refactor of `TestAuthModels`'s other 20+ test methods — only `setup_method`/`teardown_method` change
- `python -m pytest tests -q` must show no new failures beyond whatever this repo's pre-existing baseline is (there is a separate, unrelated known-flaky Windows pytest temp-dir permission issue seen repeatedly this session — if it appears, retry with an explicit `--basetemp` pointed at a writable directory rather than treating it as a regression)

---

### Task 1: Fix both leaks + add a regression test proving the fix

**Files:**
- Modify: `tests/test_auth_security.py:8-14` (the `secured_app` fixture)
- Modify: `tests/test_full_suite.py:46-53` (`TestAuthModels.setup_method`/`teardown_method`)
- Modify: `tests/test_full_suite.py` (add a new regression test function right after the `TestAuthModels` class, before the `# 2. LOCATION EXTRACTION` section comment)

**Interfaces:** None — this task doesn't produce anything later tasks consume; it's a single, complete fix.

- [ ] **Step 1: Write the regression test (before fixing anything)**

Find, in `tests/test_full_suite.py` (currently lines 126-129, right before the `# 2. LOCATION EXTRACTION` section comment):

```python
    def test_check_password(self):
        h = self.m._hash_password("secret")
        assert self.m._check_password("secret", h) and not self.m._check_password("wrong", h)


# ===========================================================================
# 2. LOCATION EXTRACTION
# ===========================================================================
```

Replace with:

```python
    def test_check_password(self):
        h = self.m._hash_password("secret")
        assert self.m._check_password("secret", h) and not self.m._check_password("wrong", h)


def test_auth_db_path_restored_after_test_auth_models_teardown():
    """
    Regression test for a real leak: TestAuthModels.setup_method used to
    overwrite the module-global AUTH_DB_PATH permanently (raw assignment,
    no restore), and teardown_method deleted the temp file without ever
    putting the original path back — leaving a dangling path for whatever
    test ran next in the same pytest session. Proves the fix actually
    restores it, rather than trusting the reorder by inspection.
    """
    import src.auth.models as m
    original = m.AUTH_DB_PATH

    instance = TestAuthModels()
    instance.setup_method()
    assert m.AUTH_DB_PATH != original  # sanity: setup really did redirect it
    instance.teardown_method()

    assert m.AUTH_DB_PATH == original


# ===========================================================================
# 2. LOCATION EXTRACTION
# ===========================================================================
```

- [ ] **Step 2: Run the new test to verify it fails against the current (unfixed) code**

Run: `"D:\vs code\Job Market Intelligence\.venv\Scripts\python.exe" -m pytest tests/test_full_suite.py::test_auth_db_path_restored_after_test_auth_models_teardown -v`
Expected: FAIL — the final `assert m.AUTH_DB_PATH == original` fails, because `teardown_method` doesn't restore it yet (this is expected; it proves the test is actually exercising the bug, not vacuously passing)

- [ ] **Step 3: Fix `test_full_suite.py`'s `TestAuthModels` lifecycle hooks**

Find (lines 46-53):

```python
class TestAuthModels:
    def setup_method(self):
        self.conn, self.tmp, self.m = _fresh_auth_db()

    def teardown_method(self):
        self.conn.close()
        try: os.unlink(self.tmp)
        except Exception: pass
```

Replace with:

```python
class TestAuthModels:
    def setup_method(self):
        import src.auth.models as m
        self._original_auth_db_path = m.AUTH_DB_PATH
        self.conn, self.tmp, self.m = _fresh_auth_db()

    def teardown_method(self):
        self.m.AUTH_DB_PATH = self._original_auth_db_path
        self.conn.close()
        try: os.unlink(self.tmp)
        except Exception: pass
```

(Restoration is the first statement — if `self.conn.close()` or `os.unlink(self.tmp)` ever raised, `AUTH_DB_PATH` is already back to its original value by that point, so a failure there can no longer leave the global dangling.)

- [ ] **Step 4: Run the regression test again to verify it passes**

Run: `"D:\vs code\Job Market Intelligence\.venv\Scripts\python.exe" -m pytest tests/test_full_suite.py::test_auth_db_path_restored_after_test_auth_models_teardown -v`
Expected: PASS

- [ ] **Step 5: Fix `test_auth_security.py`'s fixture**

Find (lines 8-14):

```python
@pytest.fixture()
def secured_app(tmp_path, monkeypatch):
    import src.auth.models as models

    models.AUTH_DB_PATH = Path(tmp_path) / "auth.sqlite"
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass-123")
    models.init_auth_db()
```

Replace with:

```python
@pytest.fixture()
def secured_app(tmp_path, monkeypatch):
    import src.auth.models as models

    monkeypatch.setattr(models, "AUTH_DB_PATH", Path(tmp_path) / "auth.sqlite")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass-123")
    models.init_auth_db()
```

- [ ] **Step 6: Verify the original bug's exact reproduction case now passes**

These are the exact file-order combinations that originally reproduced the leak — run each to confirm the fix holds in the real scenario, not just the isolated regression test:

Run: `"D:\vs code\Job Market Intelligence\.venv\Scripts\python.exe" -m pytest tests/test_full_suite.py tests/test_jobs_list_sort.py -v`
Expected: all tests PASS (previously, every test in `test_jobs_list_sort.py` failed with "no such table: users" when run after `test_full_suite.py`)

Run: `"D:\vs code\Job Market Intelligence\.venv\Scripts\python.exe" -m pytest tests/test_auth_security.py tests/test_jobs_list_sort.py -v`
Expected: all tests PASS

- [ ] **Step 7: Run the full test suite**

Run: `"D:\vs code\Job Market Intelligence\.venv\Scripts\python.exe" -m pytest tests -q`
Expected: no failures caused by this change. (If you see `PermissionError` / `WinError 5` on a pytest temp directory, that's a known, unrelated Windows environment issue seen throughout this project's history — retry with `--basetemp` pointed at a writable directory, e.g. `--basetemp="C:/Users/<you>/AppData/Local/Temp/pytest_basetemp"`, rather than treating it as caused by this fix.)

- [ ] **Step 8: Commit**

```bash
git add tests/test_auth_security.py tests/test_full_suite.py
git commit -m "fix: stop AUTH_DB_PATH test mutations from leaking across test files"
```
