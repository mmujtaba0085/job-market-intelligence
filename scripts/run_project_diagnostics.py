"""Run comprehensive project diagnostics across major subsystems.

This script executes safe checks and dry-run variants of mutating scripts,
then writes a consolidated markdown and JSON report under outputs/diagnostics/.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
OUT_ROOT = ROOT / "outputs" / "diagnostics"


@dataclass
class CheckResult:
    system: str
    name: str
    command: list[str]
    return_code: int
    duration_s: float
    stdout_file: str
    stderr_file: str


def _run_command(system: str, name: str, command: list[str], out_dir: Path, timeout_s: int = 240) -> CheckResult:
    start = time.time()
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
        shell=False,
        env=env,
    )
    duration = time.time() - start

    safe_name = name.lower().replace(" ", "_").replace("/", "_")
    stdout_path = out_dir / f"{safe_name}.stdout.txt"
    stderr_path = out_dir / f"{safe_name}.stderr.txt"
    stdout_path.write_text(proc.stdout or "", encoding="utf-8")
    stderr_path.write_text(proc.stderr or "", encoding="utf-8")

    return CheckResult(
        system=system,
        name=name,
        command=command,
        return_code=proc.returncode,
        duration_s=round(duration, 2),
        stdout_file=str(stdout_path.relative_to(ROOT)),
        stderr_file=str(stderr_path.relative_to(ROOT)),
    )


def _build_checks(py: str) -> list[tuple[str, str, list[str]]]:
    return [
        # Core and analytics tests
        ("core", "pytest core tests", [py, "-m", "pytest", "tests", "-v"]),
        ("core", "pytest system diagnostics", [py, "-m", "pytest", "tests/test_system_diagnostics.py", "-v"]),

        # Collectors
        ("collectors", "adzuna setup test", [py, "test_adzuna.py"]),
        ("collectors", "arbeitnow api test", [py, "scripts/test_arbeitnow_api.py"]),
        ("collectors", "arbeitnow fix test", [py, "scripts/test_arbeitnow_fix.py"]),

        # Normalization and titles
        ("normalization", "check confidence", [py, "scripts/check_confidence.py"]),
        ("normalization", "check unknown titles", [py, "scripts/check_unknown_titles.py"]),
        ("normalization", "check titles", [py, "scripts/check_titles.py"]),
        ("normalization", "backfill normalized titles dry run", [py, "scripts/backfill_normalized_titles.py", "--dry-run"]),
        ("normalization", "backfill confidence dry run", [py, "scripts/backfill_normalization_confidence.py", "--dry-run"]),

        # Dates and parsing
        ("dates", "test current date parsing", [py, "scripts/test_current_date_parsing.py"]),
        ("dates", "check missing dates", [py, "scripts/check_missing_dates.py"]),
        ("dates", "check arbeitnow dates", [py, "scripts/check_arbeitnow_dates.py"]),
        ("dates", "diagnose arbeitnow", [py, "scripts/diagnose_arbeitnow.py"]),

        # Web and Sheets
        ("web", "test sheets routes", [py, "scripts/test_sheets_routes.py"]),
        ("web", "test title admin", [py, "scripts/test_title_admin.py"]),

        # Storage and migration
        ("storage", "run migrations", [py, "scripts/run_migrations.py"]),
        ("storage", "check db", [py, "check_db.py"]),
        ("storage", "inspect db", [py, "inspect_db.py"]),

        # Analytics reporting checks
        ("analytics", "check data coverage", [py, "scripts/check_data_coverage.py"]),
        ("analytics", "confidence summary", [py, "scripts/confidence_summary.py"]),
        ("analytics", "analytics query test", [py, "test_analytics_query.py"]),

        # Country detector and clicks
        ("country", "country detector test", [py, "test_detector.py"]),
        ("tracking", "click tracking test", [py, "test_click_tracking.py"]),
        ("tracking", "job click tracking test", [py, "test_job_click_tracking.py"]),

        # Config/source visibility
        ("config", "show sources", [py, "show_sources.py"]),
    ]


def _write_report(run_dir: Path, results: list[CheckResult], skipped: list[str]) -> None:
    total = len(results)
    passed = sum(1 for r in results if r.return_code == 0)
    failed = total - passed

    by_system: dict[str, list[CheckResult]] = {}
    for result in results:
        by_system.setdefault(result.system, []).append(result)

    lines = []
    lines.append("# Project Diagnostic Report")
    lines.append("")
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Total checks: {total}")
    lines.append(f"- Passed: {passed}")
    lines.append(f"- Failed: {failed}")
    lines.append("")
    lines.append("## Skipped (safety)")
    lines.append("")
    for item in skipped:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## Results By System")
    lines.append("")

    for system in sorted(by_system):
        lines.append(f"### {system}")
        lines.append("")
        for r in by_system[system]:
            status = "PASS" if r.return_code == 0 else "FAIL"
            lines.append(f"- [{status}] {r.name} ({r.duration_s}s)")
            lines.append(f"  - Command: `{' '.join(r.command)}`")
            lines.append(f"  - Stdout: `{r.stdout_file}`")
            lines.append(f"  - Stderr: `{r.stderr_file}`")
        lines.append("")

    (run_dir / "diagnostic_report.md").write_text("\n".join(lines), encoding="utf-8")
    (run_dir / "results.json").write_text(
        json.dumps([asdict(r) for r in results], indent=2),
        encoding="utf-8",
    )


def main() -> int:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = OUT_ROOT / f"run_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    py = sys.executable
    checks = _build_checks(py)

    skipped = [
        "scripts/delete_arbeitnow_jobs.py (destructive delete)",
        "scripts/reupload_all_staged.py (clear-and-replace Sheets upload)",
        "scripts/restore_from_backup.py (interactive + writes to Sheets)",
        "scripts/restage_uploaded_jobs.py (interactive DB update)",
        "scripts/test_staging_population.py (clears sheets_staging table)",
        "clear_db.py (destructive)",
        "delete_github_data.py (destructive)",
        "test_single_normalization.py (updates jobs table)",
    ]

    results: list[CheckResult] = []
    for system, name, command in checks:
        try:
            result = _run_command(system, name, command, run_dir)
        except subprocess.TimeoutExpired as exc:
            safe_name = name.lower().replace(" ", "_").replace("/", "_")
            (run_dir / f"{safe_name}.stdout.txt").write_text(exc.stdout or "", encoding="utf-8")
            (run_dir / f"{safe_name}.stderr.txt").write_text((exc.stderr or "") + "\n[TIMEOUT]", encoding="utf-8")
            result = CheckResult(
                system=system,
                name=name,
                command=command,
                return_code=124,
                duration_s=240.0,
                stdout_file=str((run_dir / f"{safe_name}.stdout.txt").relative_to(ROOT)),
                stderr_file=str((run_dir / f"{safe_name}.stderr.txt").relative_to(ROOT)),
            )
        results.append(result)

    _write_report(run_dir, results, skipped)

    passed = sum(1 for r in results if r.return_code == 0)
    print(f"Diagnostics complete: {passed}/{len(results)} checks passed")
    print(f"Report: {run_dir / 'diagnostic_report.md'}")
    print(f"JSON:   {run_dir / 'results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
