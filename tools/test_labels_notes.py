"""通用备注整理回归：只做格式规范化，不做语义替换。

覆盖：
  - 空值与纯分隔符一律变成 None
  - 半角 / 全角及重复分隔符统一、折叠
  - 尾随分隔符补齐
  - 未知自由文本保留（词义与词段顺序不变）
  - 候选标签（默认词表在前，自定义按使用次数在后）
  - dry-run 不写 labels / label_events
  - apply 经 set_label(source="tidy", touch=False) 留痕
  - apply 保留 labeled_at / applied_at / rules_version 等字段
  - 重复 apply 幂等，不再产生计划或审计事件

全部使用标准库 unittest、内存 SQLite 与 jobs.db.SCHEMA；备注和 UID 均为
原创合成数据，不读取生产数据库或真实语料。

运行：
    PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/test_labels_notes.py
"""

from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jobs import db as database          # noqa: E402
from jobs import labels as labels_mod    # noqa: E402


def make_conn() -> sqlite3.Connection:
    """全新的内存数据库，表结构与生产一致（复用 jobs.db.SCHEMA）。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(database.SCHEMA)
    return conn


def add_job(conn: sqlite3.Connection, uid: str, title: str) -> None:
    source, source_id = uid.split(":", 1)
    database.upsert_job(conn, {"source": source, "source_id": source_id, "title": title})
    conn.commit()


def seed_label(conn: sqlite3.Connection, uid: str, note, **fields) -> None:
    """直接落库，绕开 set_label 的规范化，用来模拟历史遗留的自由备注。"""
    columns = ["uid", "note"] + list(fields)
    values = [uid, note] + [fields[key] for key in fields]
    placeholders = ", ".join("?" for _ in columns)
    conn.execute(
        f"INSERT INTO labels ({', '.join(columns)}) VALUES ({placeholders})", values)
    conn.commit()


def table_rows(conn: sqlite3.Connection, table: str) -> list[tuple]:
    return sorted(tuple(row) for row in conn.execute(f"SELECT * FROM {table}"))


class NormalizeNoteTests(unittest.TestCase):
    """normalize_note 只调整分隔符与空白。"""

    def test_empty_values_become_none(self):
        for value in (None, "", "   ", "\t\n"):
            self.assertIsNone(labels_mod.normalize_note(value), repr(value))

    def test_separator_only_values_become_none(self):
        for value in ("；", ";", "；；", ";;", "；;；", " ; ; "):
            self.assertIsNone(labels_mod.normalize_note(value), repr(value))

    def test_halfwidth_repeats_and_whitespace_are_normalized(self):
        self.assertEqual(labels_mod.normalize_note("搬迁; 技术栈模糊"),
                         "搬迁；技术栈模糊；")
        self.assertEqual(labels_mod.normalize_note("搬迁；；技术栈模糊；"),
                         "搬迁；技术栈模糊；")
        self.assertEqual(labels_mod.normalize_note("  搬迁 ;; 技术栈模糊  "),
                         "搬迁；技术栈模糊；")

    def test_trailing_separator_is_added_once(self):
        self.assertEqual(labels_mod.normalize_note("搬迁"), "搬迁；")
        self.assertEqual(labels_mod.normalize_note("搬迁；"), "搬迁；")

    def test_freeform_segment_order_is_preserved(self):
        self.assertEqual(labels_mod.normalize_note("zeta;alpha;beta"),
                         "zeta；alpha；beta；")
        self.assertEqual(labels_mod.normalize_note("zeta；alpha；beta；"),
                         "zeta；alpha；beta；")


class TidyNotesTests(unittest.TestCase):
    """tidy_notes 只调用 normalize_note；默认预演，apply 才落库。"""

    def setUp(self):
        self.conn = make_conn()
        self.addCleanup(self.conn.close)

    def _seed_mixed_fixture(self):
        add_job(self.conn, "synth:1000", "Synth Alpha")
        add_job(self.conn, "synth:1001", "Synth Beta")
        add_job(self.conn, "synth:1002", "Synth Gamma")
        add_job(self.conn, "synth:1003", "Synth Delta")
        add_job(self.conn, "synth:1004", "Synth Epsilon")
        seed_label(self.conn, "synth:1000", note="Synth Alpha ; Synth Beta ;; ")
        seed_label(self.conn, "synth:1001", note="搬迁；技术栈模糊；")
        seed_label(self.conn, "synth:1002", note="Synth Gamma；")
        seed_label(self.conn, "synth:1003", note="Synth Delta")
        seed_label(self.conn, "synth:1004", note="；")

    def test_dry_run_reports_plan_and_writes_nothing(self):
        self._seed_mixed_fixture()
        before_labels = table_rows(self.conn, "labels")
        before_events = table_rows(self.conn, "label_events")

        info = labels_mod.tidy_notes(self.conn, apply=False)

        self.assertEqual(set(info), {"planned", "freeform", "clean"})
        self.assertEqual(
            info["planned"],
            [("synth:1000", "Synth Alpha",
              "Synth Alpha ; Synth Beta ;;", "Synth Alpha；Synth Beta；"),
             ("synth:1003", "Synth Delta", "Synth Delta", "Synth Delta；")],
        )
        self.assertIn("Synth Gamma；", info["freeform"])
        self.assertIn("搬迁；技术栈模糊；", info["clean"])
        # dry-run 必须对 labels 与 label_events 都不产生任何变化
        self.assertEqual(table_rows(self.conn, "labels"), before_labels)
        self.assertEqual(table_rows(self.conn, "label_events"), before_events)

    def test_blank_and_separator_only_notes_are_not_planned(self):
        add_job(self.conn, "synth:5000", "Blank")
        add_job(self.conn, "synth:5001", "Separators")
        seed_label(self.conn, "synth:5000", note="   ")
        seed_label(self.conn, "synth:5001", note="；;；")

        info = labels_mod.tidy_notes(self.conn, apply=True)

        self.assertEqual(info["planned"], [])
        notes = {row["uid"]: row["note"]
                 for row in self.conn.execute("SELECT uid, note FROM labels")}
        self.assertEqual(notes["synth:5000"], "   ")
        self.assertEqual(notes["synth:5001"], "；;；")

    def test_apply_writes_audit_and_preserves_existing_fields(self):
        add_job(self.conn, "synth:2000", "Audit Target")
        seed_label(
            self.conn, "synth:2000",
            note="  Raw tag ; Other tag ;; ",
            eligibility="eligible",
            ineligible_reason="合成原因",
            interest="want",
            action="applied",
            applied_at="2024-02-03T04:05:06+00:00",
            rules_version=11,
            labeled_at="2024-02-03T04:05:06+00:00",
        )
        before = dict(self.conn.execute(
            "SELECT * FROM labels WHERE uid = 'synth:2000'").fetchone())

        info = labels_mod.tidy_notes(self.conn, apply=True)

        self.assertEqual(
            info["planned"],
            [("synth:2000", "Audit Target",
              "Raw tag ; Other tag ;;", "Raw tag；Other tag；")],
        )
        row = dict(self.conn.execute(
            "SELECT * FROM labels WHERE uid = 'synth:2000'").fetchone())
        self.assertEqual(row["note"], "Raw tag；Other tag；")
        for field in ("eligibility", "ineligible_reason", "interest", "action",
                      "applied_at", "rules_version", "labeled_at"):
            self.assertEqual(row[field], before[field], field)

        events = [dict(r) for r in self.conn.execute(
            "SELECT * FROM label_events WHERE uid = 'synth:2000' AND field = 'note'")]
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["source"], "tidy")
        # 审计记录落库时的原始文本（含首尾空白），new 是规范化结果
        self.assertEqual(event["old_value"], "  Raw tag ; Other tag ;; ")
        self.assertEqual(event["new_value"], "Raw tag；Other tag；")

    def test_freeform_text_kept_intact_and_order_stable(self):
        add_job(self.conn, "synth:3000", "Freeform")
        seed_label(self.conn, "synth:3000", note="zeta; alpha; omega")

        info = labels_mod.tidy_notes(self.conn, apply=True)

        self.assertEqual(
            info["planned"],
            [("synth:3000", "Freeform", "zeta; alpha; omega", "zeta；alpha；omega；")],
        )
        row = self.conn.execute(
            "SELECT note FROM labels WHERE uid = 'synth:3000'").fetchone()
        # 只补/统一分隔符，词义与词段顺序都不动
        self.assertEqual(row["note"], "zeta；alpha；omega；")
        self.assertEqual(labels_mod.note_tags(row["note"]), ["zeta", "alpha", "omega"])

    def test_second_apply_is_idempotent(self):
        add_job(self.conn, "synth:6000", "Idempotent")
        seed_label(self.conn, "synth:6000", note="Legacy ; Format")

        first = labels_mod.tidy_notes(self.conn, apply=True)
        self.assertEqual(len(first["planned"]), 1)
        labels_after = table_rows(self.conn, "labels")
        events_after = table_rows(self.conn, "label_events")

        second = labels_mod.tidy_notes(self.conn, apply=True)

        self.assertEqual(second["planned"], [])
        self.assertEqual(table_rows(self.conn, "labels"), labels_after)
        self.assertEqual(table_rows(self.conn, "label_events"), events_after)


class NoteTagCandidateTests(unittest.TestCase):
    """默认词表在先，用户自定义标签其后，按使用次数降序。"""

    def setUp(self):
        self.conn = make_conn()
        self.addCleanup(self.conn.close)

    def test_defaults_first_custom_by_use(self):
        for i, uid in enumerate(("synth:4000", "synth:4001",
                                 "synth:4002", "synth:4003")):
            add_job(self.conn, uid, f"Candidate {i}")
        seed_label(self.conn, "synth:4000", note="搬迁；合成甲；")
        seed_label(self.conn, "synth:4001", note="合成乙；")
        seed_label(self.conn, "synth:4002", note="合成乙；合成甲；")
        seed_label(self.conn, "synth:4003", note="合成乙；")

        candidates = labels_mod.note_tag_candidates(self.conn)
        defaults = list(labels_mod.NOTE_TAG_DEFAULTS)

        self.assertEqual(candidates[:len(defaults)], defaults)
        self.assertIn("合成甲", candidates)
        self.assertIn("合成乙", candidates)
        # 合成乙 用了 3 次、合成甲 2 次 -> 自定义标签里 合成乙 在前
        self.assertLess(candidates.index("合成乙"), candidates.index("合成甲"))
        for tag in defaults:
            self.assertEqual(candidates.count(tag), 1)
        # limit 生效，且仍然默认词表优先
        self.assertEqual(labels_mod.note_tag_candidates(self.conn, limit=3),
                         defaults[:3])


if __name__ == "__main__":
    unittest.main(verbosity=2)
