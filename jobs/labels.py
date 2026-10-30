"""Manual application progress, priority and explicit eligibility corrections.

Raw machine decisions remain separate. Interest and unsure are retained for
legacy reading; only application action marks a job as processed.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import re
from pathlib import Path

from . import db as database
from .render import cue_is_rule_owned, eligibility_snippet

VALID = {
    "eligibility": ("eligible", "ineligible"),
    "interest": ("want", "maybe", "no"),
    "action": ("saved", "applied", "skipped"),
}

# 存的是英文枚举（稳定、不会因为改文案而动），显示用中文。
# 改文案只改这里，不动数据，也不用迁移。
DISPLAY = {
    "eligibility": {"eligible": "可投", "ineligible": "不可投", "unsure": "待定"},
    "interest": {"want": "想投", "maybe": "也许", "no": "不投"},
    "action": {"saved": "想投", "applied": "已投", "skipped": "跳过"},
}
FIELD_LABEL = {"eligibility": "可投性", "interest": "意向", "action": "进度", "note": "备注"}


def display(field: str, value: str | None) -> str:
    if not value:
        return ""
    return DISPLAY.get(field, {}).get(value, value)

# 可写入的列（不含 uid / labeled_at / rules_version，那些由代码维护）
WRITABLE = ("eligibility", "ineligible_reason", "interest", "action", "note", "priority")

# 备注候选词表。
#
# 备注一直是自由文本，但实际用起来，人的判断往往反复落在少数几类上。
# 候选表让同一个意思用同一个词，**但不强制**：不匹配就照原样存自定义内容。
# 词条只是一份通用起点，不是固定的分类体系 ——
# 用户自己用过的说法优先（见 note_tag_candidates）。
NOTE_TAG_DEFAULTS = (
    # 地点
    "搬迁", "地区偏远", "通勤远",
    # 岗位本身
    "描述模糊", "技术栈模糊", "技术栈不匹配", "信息量低", "中介广告", "非IT岗位",
    "本质IT Support（T2）",
    # 资历
    "资历略高", "经验要求偏高",
    # 进度
    "优先投递", "之前已投", "重复挂出",
    # 资格／个人条件
    "需驾照", "需保密审查", "需公民或PR", "时间冲突", "资格句误判",
    # 正向
    "大厂", "技术栈匹配",
)

# 标签分隔符。与面板一致：**一段标签以「；」收尾**，于是「已选中」和
# 「正在输入」在文本上就能区分（面板只按最后一段过滤、按整段去重）。
NOTE_SEP = "；"


def note_tags(note: str | None) -> list[str]:
    """把一条备注拆成标签列表（去掉分隔符与空白）。"""
    if not note:
        return []
    return [seg.strip() for seg in note.replace(";", NOTE_SEP).split(NOTE_SEP) if seg.strip()]


def normalize_note(value: str | None) -> str | None:
    """把一条备注规范化成「标签；标签；」的形态。**这是落库前的唯一入口。**

    规则：
      - 半角 `;` 统一成全角 `；`；连续分隔符折叠；各段去空白
      - 末尾一定补一个 `；`，表示**最后一段标签也已收尾**
      - 空 / 只有分隔符 一律变成 `None`（不是空串 —— 空串会让「已标注」判断出错）

    为什么末尾必须补：面板靠这个标记区分「已选中」和「正在输入」。
    不补的话，手工输入的备注存进去是未收尾状态，下次点候选会先补一个分隔符
    再追加（原文粘成一段），刷新之后看起来也和点选出来的不一样。
    写入口统一走这里，CLI / 面板 / 整理三条路径就不会各行其是。
    """
    if value is None:
        return None
    tags = [seg.strip() for seg in re.split(r"[；;]", str(value)) if seg.strip()]
    if not tags:
        return None
    return NOTE_SEP.join(tags) + NOTE_SEP


def tidy_notes(conn, apply: bool = False) -> dict:
    """把历史备注做**格式**规范化。**默认只预演，不写库。**

    返回 {"planned": [(uid, title, old, new)],
          "freeform": [old, ...],   # 需要人工决定是否补词表的备注
          "clean": [old, ...]}      # 本来就是规范标签的

    只调用 `normalize_note` 统一分隔符与空白（半角转全角、折叠重复分隔符、
    补尾随 `；`），**不改写词义、不合并同义说法、不新增或删除标签** ——
    一段自由文本整理后仍是同一段文本，只是分隔与空白更规范。
    手工输入的历史备注没有尾随 `；`，靠它补齐。

    `freeform` 和 `clean` 分开报：前者是要不要补词表的线索，
    后者只是"已经整理过了" —— 混在一起报，第二次跑就会刷出几十行噪音。
    唯一不可再生的数据，所以：可预演、每次改写都进 label_events。
    """
    planned, freeform, clean = [], [], []
    for row in conn.execute(
            """SELECT l.uid, l.note, j.title FROM labels l
               LEFT JOIN jobs j ON j.uid = l.uid
               WHERE l.note IS NOT NULL AND TRIM(l.note) <> ''
               ORDER BY j.title"""):
        old = row["note"].strip()
        new = normalize_note(old)
        if new and new != old:
            planned.append((row["uid"], row["title"], old, new))
        elif all(tag in NOTE_TAG_DEFAULTS for tag in note_tags(old)):
            clean.append(old)
        else:
            freeform.append(old)

    if apply:
        for uid, _title, _old, new in planned:
            # touch=False：整理只是把已有的判断换一种写法，不是重新判断，
            # 不该把 labeled_at 全部刷新成今天
            set_label(conn, uid, source="tidy", touch=False, note=new)

    return {"planned": planned, "freeform": freeform, "clean": clean}



def note_tag_candidates(conn, limit: int = 400) -> list[str]:
    """候选备注标签 = 默认词表 + 库里实际用过的标签（按使用次数降序）。

    **按「；」拆成单个标签再统计，不是整条备注。** 一条备注常常是多因素的
    （「搬迁；技术栈模糊；」），整条拿去当候选等于把各种组合也塞进词表，
    词条只会越长越乱；拆开之后每个词只出现一次，自然收敛。

    从 DB 里捞历史写法是刻意的：规范化不该由代码单方面规定，
    已经写过的说法要优先出现。
    """
    used: dict[str, int] = {}
    for row in conn.execute(
            "SELECT note, COUNT(*) n FROM labels "
            "WHERE note IS NOT NULL AND TRIM(note) <> '' GROUP BY note"):
        for tag in note_tags(row["note"]):
            used[tag] = used.get(tag, 0) + row["n"]

    out: list[str] = []
    for tag in NOTE_TAG_DEFAULTS:
        out.append(tag)
        used.pop(tag, None)
    # 词表没覆盖到的自定义写法排在后面，按出现次数
    out.extend(tag for tag, _ in sorted(used.items(), key=lambda kv: (-kv[1], kv[0])))
    return out[:limit]

# 回流用例的默认落点。out/ 已被 gitignore，所以默认导出**不会**把真实
# 标注写进随仓库分发的公共测试数据。公共回归测试用的是手写合成 fixture
# （tools/fixtures/synthetic_citizenship.json）；要拿私人导出做一次额外
# 回归，必须显式把路径传给 tools/test_citizenship_rules.py --private-fixtures。
FIXTURE_PATH = (Path(__file__).resolve().parent.parent
                / "out" / "fixtures" / "labeled_citizenship.json")

# fixtures 用的摘录窗口要放宽，避免把 "…or hold a valid visa" 这样的
# override 触发条件截掉，导致本该 keep 的样本在测试里被判 exclude。
FIXTURE_SNIPPET_LEN = 600


def _validate(field: str, value) -> str | None:
    if value in (None, ""):
        return None
    if field == "priority":
        if not isinstance(value, (bool, int)) or value not in (0, 1):
            raise ValueError("priority must be true, false or null")
        return int(value)
    if field not in VALID:
        return value
    if value not in VALID[field]:
        raise ValueError(f"{field} 的取值必须是 {VALID[field]} 之一，收到 {value!r}")
    return value


def set_label(conn, uid: str, rules_version: int | None = None, source: str = "cli",
              touch: bool = True, **fields) -> dict:
    """部分更新：只写传入的字段，其余保持原样。

    每次写入都会在 `label_events` 留一条流水（含旧值）。
    标注不可再生，而面板的按钮再点一次就会清空 —— 留痕是唯一的兜底。

    `touch=False` 保留原来的 `labeled_at`。给「把已有判断换一种写法」的
    整理操作用（见 tidy_notes）：那不是在重新判断，不该把时间戳刷成今天。
    """
    # The read belongs to the write transaction too: concurrent partial edits
    # must not replace fields captured before another writer committed.
    try:
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        result = _set_label(conn, uid, rules_version, source, touch, fields)
        conn.commit()
        return result
    except BaseException:
        conn.rollback()
        raise


def _set_label(conn, uid, rules_version, source, touch, fields):
    existing = conn.execute("SELECT * FROM labels WHERE uid = ?", (uid,)).fetchone()
    current = dict(existing) if existing else {k: None for k in
                                               ("eligibility", "ineligible_reason",
                                                "interest", "action", "applied_at",
                                                "note", "rules_version", "labeled_at", "priority",
                                                "action_at", "eligibility_source", "eligibility_at",
                                                "eligibility_analysis")}

    for key in fields:
        if key not in WRITABLE:
            raise ValueError(f"不可标注的字段: {key}（可选 {WRITABLE}）")

    now = database.utcnow()
    previous_action = current.get("action")
    analysis = None
    if "eligibility" in fields:
        decision = conn.execute("SELECT * FROM decisions WHERE uid=?", (uid,)).fetchone()
        if decision is not None:
            snapshot = dict(decision)
            job = conn.execute("SELECT description FROM jobs WHERE uid=?", (uid,)).fetchone()
            snippet, cue = eligibility_snippet(job["description"] if job else None)
            snapshot.update(eligibility_snippet=snippet, eligibility_cue=cue)
            analysis = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    if existing is None and conn.execute(
            "SELECT 1 FROM label_events WHERE uid = ? LIMIT 1", (uid,)).fetchone():
        # A deleted row's field ownership must not attach to a new row just
        # because a value (especially NULL) happens to match. Retain its audit
        # trail, but mark a new generation in the same transaction as recreation.
        conn.execute("""INSERT INTO label_events (uid, field, source, at)
                        VALUES (?, '__row__', 'row_reset', ?)""", (uid, now))
    if existing and touch:
        known = {r[0] for r in conn.execute(
            "SELECT DISTINCT field FROM label_events WHERE uid = ? "
            "AND (source IS NULL OR source != 'tidy') "
            "AND NOT (field='note' AND COALESCE(source,'')='migration') "
            "AND event_id > COALESCE((SELECT MAX(event_id) FROM label_events "
            "WHERE uid = ? AND field = '__row__' AND source = 'row_reset'), 0)",
            (uid, uid))}
        for key in WRITABLE:
            if current.get(key) and key not in known and key not in fields:
                # Preserve the known pre-edit legacy state before touching the
                # row timestamp. This is a snapshot, not an invented user edit.
                conn.execute("""INSERT INTO label_events
                    (uid, field, old_value, new_value, source, rules_version, at)
                    VALUES (?,?,NULL,?,?,?,?)""",
                    (uid, key, current[key], "legacy_snapshot", current.get("rules_version"),
                     current.get("labeled_at") or ""))
    for key, value in fields.items():
        # note 走专门的规范化（补齐尾随分隔符），其余字段只做取值校验
        new_value = normalize_note(value) if key == "note" else _validate(key, value)
        old_value = current.get(key)
        # An explicit clear is meaningful even when this UID inherited the
        # displayed value from another group member and its own field is null.
        if old_value != new_value or new_value is None or touch:
            conn.execute(
                """INSERT INTO label_events
                       (uid, field, old_value, new_value, source, rules_version, at, analysis_snapshot)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (uid, key, old_value, new_value, source, rules_version, now,
                 analysis if key == "eligibility" else None),
            )
        current[key] = new_value
    if touch:
        current["labeled_at"] = now
    if rules_version is not None:
        current["rules_version"] = rules_version
    if "action" in fields:
        current["action_at"] = now
        if current.get("action") == "applied" and previous_action != "applied":
            current["applied_at"] = now
        elif current.get("action") != "applied":
            current["applied_at"] = None
    if "eligibility" in fields:
        active = current.get("eligibility") in ("eligible", "ineligible")
        current["eligibility_source"] = source if active else None
        current["eligibility_at"] = now if active else None
        current["eligibility_analysis"] = analysis if active else None

    columns = ("uid", "eligibility", "ineligible_reason", "interest", "action",
               "applied_at", "note", "rules_version", "labeled_at", "priority",
               "action_at", "eligibility_source", "eligibility_at", "eligibility_analysis")
    current["uid"] = uid
    conn.execute("INSERT OR REPLACE INTO labels (" + ",".join(columns) + ") VALUES ("
                 + ",".join("?" for _ in columns) + ")",
                 tuple(current.get(key) for key in columns))
    return current


def clear_label(conn, uid: str) -> bool:
    """Legacy clear-all command; field cancellations use set_label instead."""
    try:
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        exists = conn.execute("SELECT 1 FROM labels WHERE uid=?", (uid,)).fetchone()
        if exists:
            _set_label(conn, uid, None, "clear", True, {key: None for key in WRITABLE})
            conn.execute("DELETE FROM labels WHERE uid=?", (uid,))
        conn.commit()
        return bool(exists)
    except BaseException:
        conn.rollback()
        raise


MAX_BATCH_ITEMS = 100
MAX_UID_LENGTH = 512
PROGRESS_KEYS = ("uid", "action", "priority")


class BatchLabelError(ValueError):
    """A whole progress batch was rejected; nothing was written.

    `code` is a fixed vocabulary (never echoes request content) and `index`
    is the offending item's position when one item is to blame.
    """

    def __init__(self, code: str, index: int | None = None):
        super().__init__(code)
        self.code, self.index = code, index


def parse_progress_items(raw) -> list[dict]:
    """Validate a batch request strictly and return only the keys given.

    Omitted `action`/`priority` mean "keep"; `action: None` explicitly cancels
    progress. `priority` must be a real bool. Unknown keys, empty or overlong
    UIDs, duplicate UIDs and items without any writable field are rejected.
    """
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_BATCH_ITEMS:
        raise BatchLabelError("invalid_request")
    items, seen = [], set()
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict) or set(entry) - set(PROGRESS_KEYS):
            raise BatchLabelError("invalid_item", index)
        uid = entry.get("uid")
        if not isinstance(uid, str) or not 1 <= len(uid) <= MAX_UID_LENGTH:
            raise BatchLabelError("invalid_item", index)
        if "action" not in entry and "priority" not in entry:
            raise BatchLabelError("invalid_item", index)
        if "action" in entry and entry["action"] is not None and (
                not isinstance(entry["action"], str) or entry["action"] not in VALID["action"]):
            raise BatchLabelError("invalid_item", index)
        if "priority" in entry and type(entry["priority"]) is not bool:
            raise BatchLabelError("invalid_item", index)
        if uid in seen:
            raise BatchLabelError("duplicate_uid", index)
        seen.add(uid)
        items.append({key: entry[key] for key in PROGRESS_KEYS if key in entry})
    return items


def set_progress_batch(conn, items, *, source: str = "api",
                       rules_version: int | None = None) -> list[dict]:
    """Absolutely assign action/priority for several UIDs in one transaction.

    Same transaction ownership as `set_label` (BEGIN IMMEDIATE unless the caller
    already opened one, one commit at the end, rollback on any error), but the
    group resolution, old-state reads, every field write and its audit events
    commit together. Each UID's *effective* group state (what `report.label_of`
    shows) is compared with the request; a field that already matches is not
    written at all, so it adds no event and refreshes no timestamp. A changed
    field goes through `_set_label`, keeping its time, history, legacy-snapshot
    and explicit-clear semantics. Fields not requested are never passed on.
    Returns one {uid, action, priority, changed} per item, from the state the
    transaction ends with.
    """
    items = parse_progress_items(items)
    try:
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        results = _set_progress_batch(conn, items, rules_version, source)
        conn.commit()
        return results
    except BaseException:
        conn.rollback()
        raise


def _set_progress_batch(conn, items, rules_version, source):
    from . import report  # report imports this module; resolve lazily

    groups, members = {}, {}
    for uid, group in conn.execute(
            "SELECT uid, COALESCE(NULLIF(dedupe_group, ''), uid) FROM jobs"):
        groups[uid] = group
        members.setdefault(group, []).append(uid)
    claimed = set()
    for index, item in enumerate(items):
        group = groups.get(item["uid"])
        if group is None:
            raise BatchLabelError("unknown_uid", index)
        if group in claimed:
            raise BatchLabelError("duplicate_group", index)
        claimed.add(group)

    def effective(labels, item):
        row = {"uid": item["uid"], "dedupe_group": groups[item["uid"]]}
        merged = report.label_of(row, labels, members)
        return merged.get("action") or None, bool(merged.get("priority"))

    before = get_labels(conn, with_events=True)
    changed = set()
    for index, item in enumerate(items):
        action, priority = effective(before, item)
        fields = {}
        if "action" in item and item["action"] != action:
            fields["action"] = item["action"]
        if "priority" in item and item["priority"] != priority:
            fields["priority"] = item["priority"]
        if fields:
            _set_label(conn, item["uid"], rules_version, source, True, fields)
            if fields.get("action") == "applied":
                # This is a real transition of the effective group state. The
                # requested UID may still carry an older applied value after
                # a peer superseded it; its stale applied_at must not return.
                conn.execute("UPDATE labels SET applied_at=action_at WHERE uid=?",
                             (item["uid"],))
            changed.add(index)

    after = get_labels(conn, with_events=True)
    results = []
    for index, item in enumerate(items):
        action, priority = effective(after, item)
        if (("action" in item and item["action"] != action)
                or ("priority" in item and item["priority"] != priority)):
            # E.g. a peer event dated in the future would still win the merge.
            raise BatchLabelError("label_not_effective", index)
        results.append({"uid": item["uid"], "action": action, "priority": priority,
                        "changed": index in changed})
    return results


def get_labels(conn, *, with_events: bool = False) -> dict[str, dict]:
    rows = {r["uid"]: dict(r) for r in conn.execute("SELECT * FROM labels")}
    if with_events:
        # Formatting migration is an audit fact, not a new human field edit.
        # Compare pre-migration note events in the converted text form, retaining
        # their original time/id so a later group clear still wins (even same-second).
        migrated_notes = dict(conn.execute("SELECT uid,MAX(event_id) FROM label_events "
                                          "WHERE field='note' AND source='migration' GROUP BY uid"))
        # Internal merge metadata only; exports and ordinary callers retain
        # the historical public row shape. Existing databases need no migration.
        for event in conn.execute("""
                SELECT e.uid, e.field, e.new_value, e.at, e.event_id, e.source
                FROM label_events e JOIN (
                    SELECT uid, field, MAX(event_id) AS last_id
                    FROM label_events e
                    WHERE (source IS NULL OR source != 'tidy')
                    AND NOT (field='note' AND COALESCE(source,'')='migration')
                    AND event_id > COALESCE((SELECT MAX(b.event_id)
                        FROM label_events b WHERE b.uid = e.uid
                        AND b.field = '__row__' AND b.source = 'row_reset'), 0)
                    GROUP BY uid, field
                ) latest ON latest.last_id = e.event_id"""):
            row = rows.get(event["uid"])
            if row is not None and event["field"] in WRITABLE:
                info = dict(event)
                if event["field"] == "note":
                    if event["event_id"] < migrated_notes.get(event["uid"], 0):
                        from .migrations import priority_note_conversion
                        _, info["new_value"] = priority_note_conversion(info["new_value"])
                    info["new_value"] = normalize_note(info["new_value"])
                elif event["field"] == "priority" and info["new_value"] is not None:
                    info["new_value"] = int(info["new_value"])
                    if info["source"] == "migration":
                        # Migration priority inherits pre-upgrade note ownership,
                        # including same-second event order and snapshot fallback.
                        owner = conn.execute("""SELECT at,event_id,source FROM label_events
                            WHERE uid=? AND field='note' AND event_id < ?
                            AND (source IS NULL OR source NOT IN ('tidy','migration'))
                            ORDER BY event_id DESC LIMIT 1""",
                            (event["uid"], event["event_id"])).fetchone()
                        if owner:
                            info.update(dict(owner))
                row.setdefault("_field_events", {})[event["field"]] = info
    return rows


def merge_labels(candidates: list[dict]) -> dict | None:
    """把一个去重组内多条标注合并成一条显示用记录。

    有事件的字段按最后一次字段编辑合并，包括显式清空。旧记录没有事件时
    保留最近非空值的兼容行为；其他字段的新编辑不会让旧值复活。
    """
    live = [c for c in candidates if c]
    if not live:
        return None
    live.sort(key=lambda c: c.get("labeled_at") or "", reverse=True)
    merged = {}
    owners = {}
    for key in WRITABLE:
        choices = []
        for candidate in live:
            value = candidate.get(key)
            event = (candidate.get("_field_events") or {}).get(key)
            # Legacy rows may retain a note without its trailing separator.
            # Compare semantic text without rewriting the original peer row.
            comparable = normalize_note(value) if key == "note" else value
            if event and event.get("new_value") == comparable:
                order = 0 if event.get("source") == "legacy_snapshot" else event["event_id"]
                choices.append(((event.get("at") or "", order), candidate))
            elif value:
                choices.append(((candidate.get("labeled_at") or "", 0), candidate))
        owner = max(choices, key=lambda choice: choice[0])[1] if choices else {}
        owners[key] = owner
        merged[key] = owner.get(key)
    merged["applied_at"] = (owners["action"].get("applied_at")
                            if merged["action"] == "applied" else None)
    merged["action_at"] = owners["action"].get("action_at")
    for key in ("eligibility_source", "eligibility_at", "eligibility_analysis"):
        merged[key] = owners["eligibility"].get(key)
    merged["labeled_at"] = live[0].get("labeled_at")
    return merged


def _analysis_snapshot(value):
    try:
        return json.loads(value) if value else None
    except (ValueError, TypeError):
        return None


def qualification(row: dict, label: dict | None = None) -> dict:
    """Pure presentation: selecting a machine default never writes a label."""
    label = label or {}
    analysis = None
    if row.get("is_excluded") in (0, 1):
        analysis = "ineligible" if row["is_excluded"] else "eligible"
    manual = label.get("eligibility")
    corrected = manual in ("eligible", "ineligible")
    return {"analysis": analysis, "effective": manual if corrected else analysis,
            "corrected": corrected,
            "source": (label.get("eligibility_source") or "legacy") if corrected else None,
            "at": label.get("eligibility_at") if corrected else None,
            "analysis_snapshot": _analysis_snapshot(label.get("eligibility_analysis")) if corrected else None}


def freeze_marker(conn) -> str:
    """Event watermark distinguishes entry from an edit in the same second."""
    event_id = conn.execute("SELECT COALESCE(MAX(event_id),0) FROM label_events").fetchone()[0]
    return f"{database.utcnow()}|{event_id}"


def action_states_at(conn, since: str | None) -> dict[str, dict]:
    """Reconstruct per-source action at entry, including later clears/deletes.

    Return the same internal field-owner shape that merge_labels consumes.
    Generic note/correction timestamps never change the frozen membership.
    """
    if not since:
        return get_labels(conn, with_events=True)
    stamp, separator, marker = since.partition("|")
    watermark = int(marker) if separator and marker.isdigit() else None
    events = conn.execute("""SELECT * FROM label_events
        WHERE field='action' OR (field='__row__' AND source='row_reset')
        ORDER BY event_id""").fetchall()
    touched = {e["uid"] for e in events if e["field"] == "action"}
    states = {}
    for row in conn.execute("SELECT uid,action,action_at,labeled_at FROM labels"):
        at = row["action_at"] or row["labeled_at"] or ""
        if row["uid"] not in touched and (not at or at <= stamp):
            states[row["uid"]] = {"action": row["action"], "action_at": at,
                                  "labeled_at": at}
    for event in events:
        legacy = event["source"] == "legacy_snapshot"
        included = ((event["at"] or "") <= stamp if watermark is None or legacy
                    else event["event_id"] <= watermark)
        if not included:
            continue
        value = event["new_value"] if event["field"] == "action" else None
        info = {"new_value": value, "at": event["at"], "event_id": event["event_id"],
                "source": event["source"]}
        states[event["uid"]] = {"action": value, "action_at": event["at"],
                                "labeled_at": event["at"],
                                "_field_events": {"action": info}}
    return states


def find_jobs(conn, query: str, limit: int = 40) -> list[dict]:
    """按标题/公司模糊查找，方便拿到 uid 去标注。"""
    like = f"%{query}%"
    sql = """
        SELECT j.uid, j.title, j.company, j.source, j.location, j.url,
               d.is_excluded, d.rule_id, s.total_score,
               CASE WHEN l.uid IS NULL THEN 0 ELSE 1 END AS labeled
        FROM jobs j
        LEFT JOIN decisions d ON d.uid = j.uid
        LEFT JOIN scores    s ON s.uid = j.uid
        LEFT JOIN labels    l ON l.uid = j.uid
        WHERE j.title LIKE ? OR j.company LIKE ?
        ORDER BY j.title LIMIT ?
    """
    return [dict(r) for r in conn.execute(sql, (like, like, limit))]


def summary(conn) -> dict:
    def one(sql, *args):
        return conn.execute(sql, *args).fetchone()[0]

    return {
        "已标注": one("SELECT COUNT(*) FROM labels"),
        "不可投": one("SELECT COUNT(*) FROM labels WHERE eligibility='ineligible'"),
        "可投": one("SELECT COUNT(*) FROM labels WHERE eligibility='eligible'"),
        "待定": one("SELECT COUNT(*) FROM labels WHERE eligibility='unsure'"),
        "想投": one("SELECT COUNT(*) FROM labels WHERE action='saved'"),
        "已处理": one("SELECT COUNT(*) FROM labels WHERE action IN ('saved','applied','skipped')"),
        "优先待投": one("SELECT COUNT(*) FROM labels WHERE action='saved' AND priority=1"),
        "待投": one("SELECT COUNT(*) FROM labels WHERE action='saved'"),
        "已投递": one("SELECT COUNT(*) FROM labels WHERE action='applied'"),
        # 标了"不可投"但当前并未被硬排除 —— 这些就是漏杀，最有价值
        "漏杀候选": one("""SELECT COUNT(*) FROM labels l
                           JOIN decisions d ON d.uid = l.uid
                           WHERE l.eligibility='ineligible' AND d.is_excluded=0"""),
        # 反方向：被判为不可投、但人工确认可投 —— 误杀
        "误杀候选": one("""SELECT COUNT(*) FROM labels l
                           JOIN decisions d ON d.uid = l.uid
                           WHERE l.eligibility='eligible' AND d.is_excluded=1"""),
    }


def export(conn, out_dir: Path) -> dict:
    """导出人可读的标注清单 —— 便于备份，也防止 SQLite 损坏后判断丢失。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.date.today().isoformat()

    rows = [dict(r) for r in conn.execute("""
        SELECT l.*, j.title, j.company, j.source, j.location, j.url, j.listing_date,
               d.is_excluded, d.rule_id, s.total_score, s.tier
        FROM labels l
        LEFT JOIN jobs      j ON j.uid = l.uid
        LEFT JOIN decisions d ON d.uid = l.uid
        LEFT JOIN scores    s ON s.uid = l.uid
        ORDER BY l.labeled_at DESC
    """)]

    csv_path = out_dir / "labels.csv"
    columns = ["uid", "eligibility", "ineligible_reason", "interest", "action",
               "applied_at", "note", "title", "company", "location", "source",
               "listing_date", "total_score", "tier", "is_excluded", "rule_id",
               "rules_version", "labeled_at", "url", "priority", "action_at",
               "eligibility_source", "eligibility_at", "eligibility_analysis"]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    md = [f"# 岗位标注（{stamp}）", "", f"共 {len(rows)} 条。", ""]
    stats = summary(conn)
    md.append("　".join(f"{k} {v}" for k, v in stats.items()))
    md.append("")
    for row in rows:
        bits = []
        if row.get("eligibility"):
            extra = f"（{row['ineligible_reason']}）" if row.get("ineligible_reason") else ""
            bits.append(f"可投性={display('eligibility', row['eligibility'])}{extra}")
        if row.get("interest"):
            bits.append(f"意向={display('interest', row['interest'])}")
        if row.get("action"):
            bits.append(f"进度={display('action', row['action'])}")
        if row.get("priority"):
            bits.append("优先投递")
        md.append(f"### {row.get('title')} — {row.get('company')}")
        md.append("- " + " ｜ ".join(bits) if bits else "- （仅备注）")
        if row.get("note"):
            md.append(f"- 备注：{row['note']}")
        if row.get("is_excluded"):
            md.append(f"- 当前被规则排除：{row.get('rule_id')}")
        else:
            md.append(f"- 当前通过过滤，分数 {row.get('total_score')}")
        md.append(f"- {row.get('url') or ''}")
        md.append("")
    md_path = out_dir / "labels.md"
    md_path.write_text("\n".join(md), encoding="utf-8")

    return {"count": len(rows), "csv": str(csv_path), "md": str(md_path)}


def history(conn, uid: str | None = None, limit: int = 60) -> list[dict]:
    """标注变更流水。用来回答「这条标注是什么时候没的、被谁改的」。"""
    sql = """SELECT e.*, j.title, j.company
             FROM label_events e LEFT JOIN jobs j ON j.uid = e.uid"""
    args: list = []
    if uid:
        sql += " WHERE e.uid = ?"
        args.append(uid)
    sql += " ORDER BY e.event_id DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, args)]


def export_fixtures(conn, path: Path | None = None) -> dict:
    """把人工标注回流成回归用例 —— 这是「漏杀没有出口」的出口。

    eligibility=ineligible → 期望被排除
    eligibility=eligible   → 期望被保留
    其余跳过（unsure 不该进测试）。

    默认落点是 `FIXTURE_PATH`（项目根下 out/fixtures/，已被 gitignore），
    所以真实标注**不再默认写进随 Git 分发的测试数据**；公共回归测试用的是
    手写合成 fixture。要拿私人导出做一次额外回归，必须把路径显式传给
    `tools/test_citizenship_rules.py --private-fixtures`。

    即便落在 ignored 路径，这里仍刻意**不写入 `note`**（自由备注最容易
    夹带私人内容），只保留描述 JD 本身的字段；导出文件仍需自己备份 ——
    out/ 不进 Git。要放私人笔记请留在 `labels.note` 里，它只存在本机 DB。
    """
    target = Path(path) if path else FIXTURE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    fixtures, no_snippet, unowned = [], [], []
    sql = """
        SELECT l.uid, l.eligibility, l.ineligible_reason, l.labeled_at,
               l.eligibility_source,l.eligibility_at,l.eligibility_analysis,
               j.title, j.company, j.description
        FROM labels l JOIN jobs j ON j.uid = l.uid
        WHERE l.eligibility IN ('eligible', 'ineligible')
        ORDER BY l.labeled_at
    """
    for row in conn.execute(sql):
        snippet, cue = eligibility_snippet(row["description"], max_len=FIXTURE_SNIPPET_LEN)
        if not snippet:
            no_snippet.append(row["title"])
            continue
        # 「不可投」并不总等于「规则该排除它」。驾照、时间冲突这类**个人条件**
        # 是刻意不做硬排除的（见 render.ELIGIBILITY_CUES 的第三个字段）；
        # 若照样回流成「必须排除」，测试会一直红，并逼着我们写一条会误杀
        # 一整片 T2 支持岗的规则。所以这类只记录、不生成用例。
        if row["eligibility"] == "ineligible" and not cue_is_rule_owned(cue):
            unowned.append((row["title"], cue))
            continue
        fixtures.append({
            "uid": row["uid"],
            "expected": "exclude" if row["eligibility"] == "ineligible" else "keep",
            "cue": cue,
            "text": snippet,
            "title": row["title"],
            "company": row["company"],
            "reason": row["ineligible_reason"],
            "labeled_at": row["labeled_at"],
            "correction_source": row["eligibility_source"] or "legacy",
            "correction_at": row["eligibility_at"],
            "analysis_snapshot": _analysis_snapshot(row["eligibility_analysis"]),
        })

    target.write_text(json.dumps(fixtures, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"written": len(fixtures), "skipped": len(no_snippet) + len(unowned),
            "no_snippet": no_snippet, "unowned": unowned, "path": str(target)}
