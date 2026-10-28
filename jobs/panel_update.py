"""Local panel update orchestration; adapters and CLI remain the execution path.

The panel exposes only whitelisted configuration fields and bounded state codes.
Runtime snapshots are private and scoped to the active database. One flock is
inherited by the child so a panel restart cannot launch a duplicate worker.
The CLI and configuration editor use that same database-scoped persistent lock.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import uuid

import yaml

from .config import ROOT, db_path, read_config_root, resolve_filtering
from .db import utcnow
from . import run_state, db as database
from .file_security import set_private

TIMEOUT_SECONDS = 1800
TERMINAL_STATES = {"success", "partial", "error", "busy", "timeout", "interrupted"}
STATES = TERMINAL_STATES | {"idle", "running", "disabled", "unknown"}
STAGES = {"fetch", "details", "analyze", "report"}
ERRORS = {"config_invalid", "config_unavailable", "reload_required", "no_sources",
          "demo_disabled", "run_failed", "start_failed", "source_failed", "run_busy",
          "details_incomplete", "run_timeout", "run_interrupted", "storage_unavailable"}
SOURCES = ("seek", "indeed", "linkedin")


def _path(value):
    path = Path(value)
    return (path if path.is_absolute() else ROOT / path).resolve()


def _atomic_json(path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            set_private(temporary)
            json.dump(value, handle)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _empty(state="idle", error=None):
    return {"state": state, "stage": None, "started_at": None, "finished_at": None,
            "counts": {"seen": 0, "inserted": 0, "updated": 0, "details_fetched": 0},
            "failed_sources": [], "error": error, "refresh_available": False}


def _safe_state(value):
    """Do not forward arbitrary persisted content to HTTP clients."""
    if not isinstance(value, dict) or not isinstance(value.get("state"), str) or value["state"] not in STATES:
        return _empty("interrupted", "run_interrupted")
    error, stage = value.get("error"), value.get("stage")
    result = _empty(value["state"], error if isinstance(error, str) and error in ERRORS else None)
    result["stage"] = stage if isinstance(stage, str) and stage in STAGES else None
    for key in ("started_at", "finished_at"):
        item = value.get(key)
        if isinstance(item, str) and len(item) <= 40 and all(c in "0123456789-:T+. Z" for c in item):
            result[key] = item
    counts = value.get("counts")
    if counts is None:
        result["counts"] = None
    if isinstance(counts, dict):
        for field in result["counts"]:
            item = counts.get(field)
            if isinstance(item, int) and not isinstance(item, bool) and item >= 0:
                result["counts"][field] = item
    failed_sources = value.get("failed_sources")
    if isinstance(failed_sources, list):
        result["failed_sources"] = [name for name in SOURCES if name in failed_sources]
    for key in ("task_id", "generation"):
        item = value.get(key)
        if isinstance(item, str) and len(item) <= 80 and all(c.isalnum() or c == "-" for c in item):
            result[key] = item
    result["trigger"] = value.get("trigger") if value.get("trigger") in ("manual", "scheduled", "cli", "unknown") else "unknown"
    result["kind"] = value.get("kind") if value.get("kind") in ("run", "fetch") else None
    result["refresh_available"] = value.get("refresh_available") is True
    result["completion_ready"] = value.get("completion_ready") is True
    return result


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None


def _positive_int(value, *, zero=False):
    if isinstance(value, bool) or not isinstance(value, int) or value < (0 if zero else 1):
        raise ValueError("invalid limit")
    return value


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 1000:
        raise ValueError("invalid text")
    return value


def _section(config, key):
    value = config.get(key) or {}
    if not isinstance(value, dict):
        raise ValueError("invalid section")
    return value


def _enabled(config):
    value = config.get("enabled", True)
    if not isinstance(value, bool):
        raise ValueError("invalid enabled")
    return value


def _summary(config):
    if not isinstance(config, dict):
        raise ValueError("invalid config")
    resolve_filtering(config)
    queries = config.get("queries")
    if not isinstance(queries, list) or not queries:
        raise ValueError("invalid queries")
    queries = [_text(value) for value in queries]
    seek, jobspy = _section(config, "seek"), _section(config, "jobspy")
    locations = seek.get("locations") or ["All Australia"]
    if not isinstance(locations, list):
        raise ValueError("invalid locations")
    daterange = seek.get("daterange")
    if daterange is not None:
        _positive_int(daterange, zero=True)
    sources = [{"name": "seek", "enabled": _enabled(seek),
                "locations": [_text(value) for value in locations],
                "pages_per_query": _positive_int(seek.get("pages_per_query", 2)),
                "daterange": daterange}]
    for name in ("indeed", "linkedin"):
        source = _section(jobspy, name)
        item = {"name": name, "enabled": _enabled(source),
                "locations": [_text(source.get("location") or "Melbourne VIC")],
                "results_wanted": _positive_int(source.get("results_wanted", 40))}
        if name == "linkedin":
            description = source.get("fetch_description", True)
            if not isinstance(description, bool):
                raise ValueError("invalid fetch_description")
            item["fetch_description"] = description
        else:
            item["country_indeed"] = _text(source.get("country_indeed", "australia"))
        sources.append(item)
    return {"ok": True, "error": None, "queries": queries, "sources": sources}


class UpdateManager:
    def __init__(self, config, config_path=None, *, rules_path=None, skills_path=None):
        self.config = copy.deepcopy(config)
        self.demo = "demo" in config
        self.launch_dir = Path.cwd()
        self.config_path = Path(config_path).resolve() if config_path is not None else ROOT / "config.yaml"
        # Explicit CLI paths and panel skills paths historically use launch cwd;
        # database/report paths separately use the repository root.
        self.rules_path = Path(rules_path).resolve() if rules_path is not None else ROOT / "rules.yaml"
        self.skills_override = Path(skills_path).resolve() if skills_path is not None else None
        self.skills_path = self._skills_target(config)
        self.targets = self._targets(config)
        active_db = db_path(config).resolve()
        # No directories or files are created until an actual update request.
        self.runtime = active_db.parent / ("." + active_db.name + ".panel-update")
        self.state_path = self.runtime / "state.json"
        self.result_path = self.runtime / "result.json"
        self.lock_path = run_state.lock_path(config)
        self._mutex = threading.RLock()
        self._process = None

    def _skills_target(self, config):
        if self.skills_override is not None:
            return self.skills_override
        configured = config.get("skills_path")
        if not configured:
            return ROOT / "skills.yaml"
        path = Path(configured)
        return (path if path.is_absolute() else self.launch_dir / path).resolve()

    def _targets(self, config):
        report = _section(config, "report")
        return (db_path(config).resolve(), _path(report.get("out_dir", "out")),
                self._skills_target(config))

    def _fresh(self):
        config = read_config_root(self.config_path)
        preview = _summary(config)
        if self._targets(config) != self.targets:
            return None, {**preview, "ok": False, "error": "reload_required"}
        if not any(source["enabled"] for source in preview["sources"]):
            return None, {**preview, "ok": False, "error": "no_sources"}
        return config, preview

    def preview(self):
        empty = {"ok": False, "error": None, "queries": [], "sources": [], "changed_since_start": False}
        if self.demo:
            return {**empty, "error": "demo_disabled"}
        try:
            _, result = self._fresh()
            # Compare the displayed execution fields, unaffected by host/port overrides.
            # A repaired file must not remain invalid because the startup
            # configuration had no queries or malformed fetch settings.
            try:
                original = _summary(self.config)
            except (ValueError, TypeError, AttributeError):
                original = {}
            result["changed_since_start"] = any(
                result.get(key) != original.get(key) for key in ("queries", "sources"))
            return result
        except (ValueError, TypeError, AttributeError, yaml.YAMLError):
            return {**empty, "error": "config_invalid"}
        except (OSError, UnicodeError):
            return {**empty, "error": "config_unavailable"}

    def status(self):
        if self.demo:
            return _empty("disabled", "demo_disabled")
        try:
            conn = database.connect(db_path(self.config))
            try:
                return _safe_state(run_state.latest(self.config, conn))
            finally:
                conn.close()
        except (OSError, ValueError):
            return _empty("error", "storage_unavailable")

    def start(self):
        if self.demo:
            return {**_empty("disabled", "demo_disabled"), "accepted": False}
        with self._mutex:
            # Read-only preflight keeps invalid requests free of runtime writes;
            # the authoritative read is repeated under the shared lock below.
            preview = self.preview()
            if not preview['ok']:
                return {**_empty("error", preview['error']), "accepted": False}
            try:
                handle = run_state.acquire_lock(self.config)
                if handle is None:
                    return {**self.status(), "state": "running", "accepted": False}
                try:
                    config, preview = self._fresh()
                except (ValueError, TypeError, AttributeError, yaml.YAMLError):
                    handle.close()
                    return {**_empty("error", "config_invalid"), "accepted": False}
                except (OSError, UnicodeError):
                    handle.close()
                    return {**_empty("error", "config_unavailable"), "accepted": False}
                if config is None:
                    handle.close()
                    return {**_empty("error", preview["error"]), "accepted": False}
            except OSError:
                return {**_empty("error", "storage_unavailable"), "accepted": False}
            snapshot = self.runtime / "config.yaml"
            process = None
            try:
                with snapshot.open("w", encoding="utf-8") as stream:
                    set_private(snapshot)
                    yaml.safe_dump(config, stream, allow_unicode=True)
                self.result_path.unlink(missing_ok=True)
                state = _empty("running")
                state["started_at"] = utcnow()
                state.update(task_id=uuid.uuid4().hex, trigger="manual", kind="run")
                run_state.publish(self.config, state)
                _atomic_json(self.state_path, state)
                command = [sys.executable, "-m", "jobs.cli", "--config", str(snapshot),
                           "--rules", str(self.rules_path), "--skills", str(self.skills_path),
                           "run", "--timeout", str(TIMEOUT_SECONDS), "--result-file", str(self.result_path),
                           "--result-started-at", state["started_at"], "--task-id", state["task_id"],
                           "--trigger", "manual"]
                if os.name == 'nt':
                    from .windows_process import spawn_python
                    process, job = spawn_python(command[3:], handle, cwd=ROOT,
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    process._jobs_job = job
                else:
                    command += ['--run-lock-fd', str(handle.fileno())]
                    process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                               start_new_session=True, pass_fds=(handle.fileno(),))
                self._process = process
                # The child uses the same started timestamp; stale results were
                # removed while holding the exclusive lock.
                thread = threading.Thread(target=self._watch, args=(process, handle, snapshot, state), daemon=True)
                thread.start()
                return {**state, "accepted": True}
            except (OSError, ValueError, RuntimeError):
                try:
                    if process is not None:
                        self._stop(process)
                    snapshot.unlink(missing_ok=True)
                    state = _empty("error", "start_failed")
                    state["finished_at"] = utcnow()
                    try:
                        _atomic_json(self.state_path, state)
                        run_state.publish(self.config, state)
                    except OSError:
                        pass
                    return {**state, "accepted": False}
                finally:
                    # Persist failure before another runner can replace latest.
                    handle.close()

    @staticmethod
    def _stop(process):
        job = getattr(process, '_jobs_job', None)
        if job is not None:
            try:
                job.stop(process)
            finally:
                job.close()
                process.close()
                process._jobs_job = None
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()

    def _watch(self, process, handle, snapshot, initial):
        result = None
        try:
            try:
                code = process.wait(timeout=TIMEOUT_SECONDS + 10)
            except subprocess.TimeoutExpired:
                self._stop(process)
                progress = _read_json(self.result_path)
                result = _safe_state(progress) if isinstance(progress, dict) else dict(initial)
                result.update(state="timeout", error="run_timeout", finished_at=utcnow(), refresh_available=False)
            if result is None:
                raw = _read_json(self.result_path)
                result = _safe_state(raw) if isinstance(raw, dict) else None
                if (result is None or result["state"] not in TERMINAL_STATES or
                        (code != 0 and result["state"] in ("success", "partial"))):
                    result = result or dict(initial)
                    result.update(state="error", error="run_failed", finished_at=utcnow(), refresh_available=False)
            if getattr(process, '_jobs_job', None) is not None:
                self._stop(process)
            with self._mutex:
                _atomic_json(self.state_path, result)
                run_state.publish(self.config, result)
        except OSError:
            # status can still reconstruct from result + the inherited lock.
            pass
        finally:
            try:
                snapshot.unlink(missing_ok=True)
            finally:
                handle.close()
