"""Legacy single-path cleanup smoke test (offline, synthetic, temporary DB).

After the candidate eligibility / handover / profile_v1 runtime was removed,
this hand-written test proves the surviving single legacy
path still works end to end on a synthetic temporary database:

  * the real `cmd_analyze` runs the legacy `filters.evaluate` path;
  * keep and exclude partitioning lands in `decisions` and `report.load`;
  * the CSV report and the five panel views plus detail consume the same rows;
  * original-text eligibility snippet and manual labels / notes survive;
  * no `eligibility_evidence` table is created, read or written;
  * the removed candidate modules are not importable;
  * a failure mid-analysis rolls back the derived rows, and `RunTimeout`
    still propagates instead of being swallowed.

Everything is synthetic and inside `tempfile.TemporaryDirectory()`; the test
never reads the production database, corpus, exports or private configuration.

Usage:
    PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/test_legacy_cleanup.py
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jobs import RunTimeout                        # noqa: E402
from jobs import cli                               # noqa: E402
from jobs import db as database                    # noqa: E402
from jobs import labels as labels_mod              # noqa: E402
from jobs import panel                             # noqa: E402
from jobs import report                            # noqa: E402

REMOVED_MODULES = (
    "jobs.eligibility_extraction",
    "jobs.clearance_extraction",
    "jobs.eligibility_assessment",
    "jobs.eligibility_combination",
    "jobs.eligibility_adapter",
    "jobs.eligibility_comparison",
    "jobs.handover_policy",
    "jobs.handover_orchestration",
    "jobs.eligibility_runtime",
)

SYNTHETIC_RULES = r"""version: 999
exclude:
  - id: synth.named_clearance
    any:
      - '\bNV1\b'
overrides: []
tiers:
  T1:
    title: ["engineer"]
    weight: 3
  T3:
    weight: 0
level:
  positive: []
  negative: []
job_level: {}
experience: {}
adjust: []
location: {}
dedupe:
  include_city: false
  title_similarity: 0.9
salary:
  enabled: false
"""

SYNTHETIC_SKILLS = """\
version: 1
skills:
  - name: Python
    aliases: []
    not: []
"""

KEEP_DESC = "Applicants must be Australian citizens. Python and AWS."
EXCLUDE_DESC = "This role requires NV1 clearance and is open to citizens."

failures: list = []
checks = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{('  ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


class Fixture:
    """A synthetic temp config / rules / skills set and its database path."""

    def __init__(self, root: Path):
        self.dir = root
        self.rules = root / "rules.yaml"
        self.skills = root / "skills.yaml"
        self.config_path = root / "config.yaml"
        self.out_dir = root / "out"
        self.db = root / "jobs.db"
        self.rules.write_text(SYNTHETIC_RULES, encoding="utf-8")
        self.skills.write_text(SYNTHETIC_SKILLS, encoding="utf-8")
        self.config = {
            "db_path": str(self.db),
            "filtering": {"mode": "legacy"},
            "report": {"out_dir": str(self.out_dir), "top_n": 10},
            "panel": {"page_size": 40},
        }
        self.write_config()

    def write_config(self) -> None:
        self.config_path.write_text(
            "db_path: " + json.dumps(self.config["db_path"]) + "\n"
            "filtering:\n  mode: legacy\n"
            "report:\n  out_dir: " + json.dumps(self.config["report"]["out_dir"]) + "\n"
            "  top_n: 10\n"
            "panel:\n  page_size: 40\n",
            encoding="utf-8")

    def seed(self) -> None:
        conn = database.connect(self.db)
        # Record one fetch batch so the "new" view has a since baseline.
        database.log_run(conn, "fetch", database.utcnow(), {})
        for uid, title, desc, classification in (
            ("seek:keep", "Synthetic Support Engineer", KEEP_DESC,
             "Help Desk & IT Support"),
            ("seek:excluded", "Synthetic Data Analyst", EXCLUDE_DESC,
             "Data Analysts"),
        ):
            source, source_id = uid.split(":", 1)
            database.upsert_job(conn, {
                "source": source, "source_id": source_id, "title": title,
                "company": "Example Co", "location": "Melbourne VIC",
                "classification": classification, "description": desc,
                "url": f"https://example.invalid/job/{source_id}"})
        labels_mod.set_label(conn, "seek:keep", source="test",
                             eligibility="eligible", interest="want",
                             note="优先投递")
        conn.commit()
        conn.close()

    def namespace(self) -> argparse.Namespace:
        return argparse.Namespace(config=str(self.config_path),
                                  rules=str(self.rules), skills=str(self.skills))


def run_analyze(fx: Fixture) -> int:
    return cli.cmd_analyze(fx.namespace())


def test_pipeline() -> None:
    print("== 1. legacy analyze -> report -> views -> detail (synthetic temp DB) ==")
    with tempfile.TemporaryDirectory() as d:
        fx = Fixture(Path(d))
        fx.seed()
        rc = run_analyze(fx)
        check("cmd_analyze exits 0", rc == 0, f"rc={rc}")

        conn = database.connect(fx.db)
        try:
            decided = {r["uid"]: r["is_excluded"]
                       for r in conn.execute("SELECT uid, is_excluded FROM decisions")}
            check("excluded job is excluded", decided.get("seek:excluded") == 1,
                  str(decided))
            check("kept job is not excluded", decided.get("seek:keep") == 0, str(decided))
            scored = {r["uid"] for r in conn.execute("SELECT uid FROM scores")}
            check("both jobs are scored", scored == {"seek:keep", "seek:excluded"},
                  str(sorted(scored)))

            labels = labels_mod.get_labels(conn)
            check("manual label survives analyze",
                  labels.get("seek:keep", {}).get("eligibility") == "eligible")
            check("manual note survives analyze",
                  labels.get("seek:keep", {}).get("note") == "优先投递；",
                  repr(labels.get("seek:keep", {}).get("note")))

            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'")}
            check("no eligibility_evidence table is created",
                  "eligibility_evidence" not in tables, str(sorted(tables)))

            kept, excluded, _, index = report.load(conn, fx.config)
            check("report keeps the kept job",
                  [r["uid"] for r in kept] == ["seek:keep"],
                  str([r["uid"] for r in kept]))
            check("report partitions the excluded job",
                  [r["uid"] for r in excluded] == ["seek:excluded"],
                  str([r["uid"] for r in excluded]))

            row = report.decorate([kept[0]], labels, index)[0]
            check("original-text eligibility snippet is preserved",
                  "Australian citizens" in (row.get("eligibility_snippet") or ""),
                  repr(row.get("eligibility_snippet")))
            check("eligibility cue is preserved", row.get("eligibility_cue") == "公民/PR",
                  repr(row.get("eligibility_cue")))
            check("no machine-eligibility view field remains",
                  "eligibility_view" not in row and "eligibility_mode" not in row,
                  str(sorted(k for k in row if k.startswith("eligibility"))))

            info = report.write_report(conn, fx.config)
            csv_text = (Path(info["out_dir"]) /
                        f"{info['stamp']}-scored.csv").read_text(encoding="utf-8-sig")
            check("CSV carries the legacy snippet column",
                  "eligibility_snippet" in csv_text)
            check("CSV has no candidate evidence columns",
                  not any(k in csv_text for k in (
                      "eligibility_mode", "eligibility_status", "eligibility_review",
                      "eligibility_coverage", "eligibility_versions",
                      "eligibility_evidence")))

            views = list(panel.VIEWS)
            check("five panel views are registered", len(views) == 5, str(views))
            expected = {
                # The kept job is labelled, so "recommend" (unlabelled only) is
                # legitimately empty; it still has to render without error.
                "recommend": None,
                "all": "Synthetic Support Engineer",
                "new": "Synthetic Support Engineer",
                "excluded": "Synthetic Data Analyst",
                "labelled": "Synthetic Support Engineer",
            }
            for view in views:
                try:
                    html = panel.render_page(conn, fx.config, view, 0, 40,
                                             None, {}, "zh", None)
                    if expected[view] is None:
                        ok = isinstance(html, str) and "<!doctype html>" in html
                    else:
                        ok = isinstance(html, str) and expected[view] in html
                except Exception as exc:            # noqa: BLE001
                    ok = False
                    html = type(exc).__name__ + ": " + str(exc)
                check(f"panel view {view!r} renders synthetic rows", ok,
                      "" if ok else str(html)[:80])

            detail_row = panel._find(conn, fx.config, "seek:keep")
            detail = panel._detail_html(detail_row, "zh")
            check("detail shows the original snippet",
                  "Australian citizens" in detail)
            check("detail shows the manual note", "优先投递" in detail)
            check("detail no longer renders the machine block",
                  "机器资格" not in detail)
        finally:
            conn.close()


def test_removed_modules() -> None:
    print("== 2. candidate modules and evidence API are gone ==")
    found = [name for name in REMOVED_MODULES
             if importlib.util.find_spec(name) is not None]
    check("no removed module is importable", not found, str(found))
    evidence_api = [name for name in (
        "evidence_map", "ensure_evidence_schema", "replace_evidence",
        "clear_evidence", "evidence_table_exists", "EVIDENCE_COLUMNS",
        "EVIDENCE_TABLE") if hasattr(database, name)]
    check("no evidence helper remains in jobs.db", not evidence_api, str(evidence_api))


def test_rollback_and_timeout() -> None:
    print("== 3. failure rollback and RunTimeout propagation ==")
    with tempfile.TemporaryDirectory() as d:
        fx = Fixture(Path(d))
        fx.seed()
        check("first analyze succeeds", run_analyze(fx) == 0)

        # Add a new job, then fail during the same transaction. The clear +
        # reinsert must roll back to the previous committed decision set.
        conn = database.connect(fx.db)
        database.upsert_job(conn, {
            "source": "seek", "source_id": "late", "title": "Synthetic Late Job",
            "company": "Example Co", "location": "Melbourne VIC",
            "classification": "Data Analysts", "description": "No requirement.",
            "url": "https://example.invalid/job/late"})
        conn.commit()
        conn.close()

        original = cli._sync_skills_transactional

        def boom(*_args, **_kwargs):
            raise ValueError("synthetic failure")

        cli._sync_skills_transactional = boom
        try:
            rc = run_analyze(fx)
        finally:
            cli._sync_skills_transactional = original
        check("a mid-analysis failure returns non-zero", rc != 0, f"rc={rc}")

        conn = database.connect(fx.db)
        try:
            uids = {r["uid"] for r in conn.execute("SELECT uid FROM decisions")}
            check("failed run rolled back to the previous decision set",
                  uids == {"seek:keep", "seek:excluded"}, str(sorted(uids)))
        finally:
            conn.close()

        def timed_out(*_args, **_kwargs):
            raise RunTimeout()

        cli._sync_skills_transactional = timed_out
        try:
            try:
                run_analyze(fx)
                outcome = "returned"
            except RunTimeout:
                outcome = "propagated"
            except Exception as exc:                # noqa: BLE001
                outcome = f"wrong-exception {type(exc).__name__}"
        finally:
            cli._sync_skills_transactional = original
        check("RunTimeout propagates instead of being swallowed",
              outcome == "propagated", outcome)


def main() -> int:
    test_pipeline()
    test_removed_modules()
    test_rollback_and_timeout()
    print()
    if failures:
        print(f"失败 {len(failures)} 项：")
        for item in failures:
            print(f"  - {item}")
        return 2
    print(f"全部通过（{checks} 项检查）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
