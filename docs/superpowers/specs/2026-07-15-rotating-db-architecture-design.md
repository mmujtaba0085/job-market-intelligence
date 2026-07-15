# Rotating 3-DB Architecture — Design Spec

## Goal

Stop job-data writes (ingestion, classification/tagging) from ever contending with live read traffic, by rotating which of two SQLite files serves reads, instead of the current single-file-plus-load-aware-scheduling approach. Directly motivated by two real incidents this session: a live "database is locked" migration crash, and the classification pipeline needing idle-detection gymnastics just to write safely.

## Scope

**Rotates** (job data only): `jobs`, `skills`, `weekly_metrics`, `job_categories`, `job_category_assignments`, `groq_classification_queue`, `classification_runs`.

**Does not rotate** (stays in one fixed `operational.sqlite`): auth users/sessions/api_keys, `pipeline_config`, `pipeline_runs`, admin audit logs. These have no read/write contention problem worth solving and rotating them would just add risk (e.g. admin sessions resetting).

## Files and pointer

Three files under the existing data volume: `serving_a.sqlite`, `serving_b.sqlite`, `buffer.sqlite`. A plain-text pointer file, `data/serving_pointer.txt`, contains either `a` or `b` — whichever is currently Serving. Plain text, not JSON: it's one value, no parser needed.

`src/storage/db.py`:
- `get_connection()` — **signature unchanged**, every existing caller across the app keeps working untouched. Internally now resolves the Serving path by reading the pointer file fresh each call (a few bytes, negligible cost) instead of a static `DB_PATH`.
- `get_free_connection()` — new, resolves to whichever of `serving_a`/`serving_b` the pointer does *not* currently point at. Used only by the classification pipeline and any future admin "fix/tag" tooling.
- `_read_pointer()` / `_write_pointer(which)` — `_write_pointer` writes to `serving_pointer.txt.tmp` then `os.replace()`s over the real file (atomic — no reader ever sees a half-written pointer).

Ingestion (`src/orchestrator.py`) runs its existing collect→normalize→dedupe→store pipeline unchanged, just pointed at `buffer.sqlite` via a new `get_buffer_connection()` instead of Serving. Buffer's `jobs` table is already fully normalized and self-deduplicated by the time rotation looks at it — no re-normalization needed at merge time, just a cross-check against what Free already has.

## Rotation

**Trigger:** admin-configurable `rotation_max_interval_hours` (default 12, same `pipeline_config` table/pattern as `ingest_interval_hours`), checked two ways: (1) right after an `ingest-only` run's `finish_run()` succeeds, if Buffer has unmerged rows; (2) the existing scheduler thread's 60s tick, as a fallback so rotation still happens even if ingestion is skipped. Also a manual "Rotate Now" button in the admin panel, matching the existing Run Now convention.

**Steps** (`src/db_rotation.py`, new, small):
1. Merge Buffer into Free: Buffer's `jobs` rows are already normalized/self-deduplicated (ingestion wrote them there via the unchanged pipeline). Copy each row into Free only if its `url_hash` isn't already there — reuses the same dedup check `upsert_job()` already does, just against Free instead of the live DB. Clear Buffer once confirmed merged.
2. Flip the pointer: `_write_pointer()` to the other letter. Free is now Serving; old Serving is demoted.
3. Refresh the demoted file: `sqlite3.Connection.backup()` (the same API `scripts/warehouse_rollout.py` already uses) from new-Serving into `<demoted>.sqlite.tmp`, then `os.replace()` over the demoted file. Atomic rename — any request that already opened the demoted file (read the pointer a moment before the flip) keeps reading its own consistent snapshot until it closes; nothing blocks, nothing errors.

**Safety:**
- Reader-vs-writer: solved by the atomic-rename pattern above, not a lock.
- Writer-vs-writer (ingestion-finish trigger and 12h-fallback trigger firing close together, or two gunicorn workers both deciding to rotate): an `fcntl` file lock around the rotation steps only (reused from the migration-lock fix earlier this session — same pattern, same file), a no-op on Windows same as before.

## Classification pipeline changes

`src/classification/*` switches from `get_connection()` to `get_free_connection()`. Since Free is never serving live traffic, `local_incremental` and `groq_backlog` drop their idle-gating entirely — `should_process_chunk`'s load check stays but its only remaining caller is the Buffer→Free merge step in `db_rotation.py`, which still cares about not fighting an admin doing manual tagging mid-merge.

## Testing

- Pointer read/write round-trip, atomic-replace-under-concurrent-open (open old file, replace it, assert the open handle still reads old content).
- Rotation end-to-end against tmp_path fixtures: Buffer rows land in the new Serving after one rotation; demoted file matches new Serving byte-for-byte after refresh.
- Lock prevents two concurrent `rotate()` calls from both merging Buffer (second call finds nothing left to merge, doesn't error).
- `get_connection()` returns a working connection to whichever file the pointer currently names; `get_free_connection()` always returns the other one.

## Out of scope (separate follow-ups, not part of this spec)

- Weekly-schedule admin fix (`weekly_day`/`weekly_time` currently decorative).
- Per-source ingestion enable/disable toggle with Active/Deactivated tabs.
