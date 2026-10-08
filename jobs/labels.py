"""人工标注：能不能投 / 想不想投 / 投没投。

**这是项目里唯一不可再生的数据** —— 岗位可以重抓，人的判断不能。因此：

  - 存在独立的 `labels` 表，`analyze` 永不触碰（见 db.clear_decisions_and_scores）
  - 键用 `uid`（`source:id`），**不用去重组** —— 去重组会随规则变化漂移，
    用它做键会让标注的归属悄悄跑掉
  - 标注时记录 `rules_version`，回看时才知道那条判断是在哪版规则下做的

标注的三个字段刻意正交，因为「要不要投」这一个 bool 会混淆四件不同的事：

  eligibility  能不能投   → 喂给硬过滤（ineligible 即漏杀，变成回归用例）
  interest     想不想投   → 喂给打分权重
  action       投没投     → 你实际的求职进度

漏杀没有自动出口：过滤挡掉的岗位你看不到，也就永远不会去纠正它。
`ineligible` 的标注会被回流成 fixtures，直接进入回归测试。
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
    "eligibility": ("eligible", "ineligible", "unsure"),
    "interest": ("want", "maybe", "no"),
    "action": ("saved", "applied", "skipped"),
}

# 存的是英文枚举（稳定、不会因为改文案而动），显示用中文。
# 改文案只改这里，不动数据，也不用迁移。
DISPLAY = {
    "eligibility": {"eligible": "可投", "ineligible": "不可投", "unsure": "待定"},
    "interest": {"want": "想投", "maybe": "也许", "no": "不投"},
    "action": {"saved": "待投", "applied": "已投", "skipped": "跳过"},
}
FIELD_LABEL = {"eligibility": "可投性", "interest": "意向", "action": "进度", "note": "备注"}


def display(field: str, value: str | None) -> str:
    if not value:
        return ""
    return DISPLAY.get(field, {}).get(value, value)

# 可写入的列（不含 uid / labeled_at / rules_version，那些由代码维护）
WRITABLE = ("eligibility", "ineligible_reason", "interest", "action", "note")

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
    existing = conn.execute("SELECT * FROM labels WHERE uid = ?", (uid,)).fetchone()
    current = dict(existing) if existing else {k: None for k in
                                               ("eligibility", "ineligible_reason",
                                                "interest", "action", "applied_at",
                                                "note", "rules_version", "labeled_at")}

    for key in fields:
        if key not in WRITABLE:
            raise ValueError(f"不可标注的字段: {key}（可选 {WRITABLE}）")

    now = database.utcnow()
    for key, value in fields.items():
        # note 走专门的规范化（补齐尾随分隔符），其余字段只做取值校验
        new_value = normalize_note(value) if key == "note" else _validate(key, value)
        old_value = current.get(key)
        if old_value != new_value:
            conn.execute(
                """INSERT INTO label_events
                       (uid, field, old_value, new_value, source, rules_version, at)
                   VALUES (?,?,?,?,?,?,?)""",
                (uid, key, old_value, new_value, source, rules_version, now),
            )
        current[key] = new_value
    if touch:
        current["labeled_at"] = now
    if rules_version is not None:
        current["rules_version"] = rules_version
    # 首次标记为已投递时，补上时间戳
    if current.get("action") == "applied" and not current.get("applied_at"):
        current["applied_at"] = now
    if current.get("action") != "applied":
        current["applied_at"] = None

    conn.execute(
        """INSERT OR REPLACE INTO labels
               (uid, eligibility, ineligible_reason, interest, action,
                applied_at, note, rules_version, labeled_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (uid, current["eligibility"], current["ineligible_reason"], current["interest"],
         current["action"], current["applied_at"], current["note"],
         current["rules_version"], current["labeled_at"]),
    )
    conn.commit()
    return current


def clear_label(conn, uid: str) -> bool:
    cur = conn.execute("DELETE FROM labels WHERE uid = ?", (uid,))
    conn.commit()
    return cur.rowcount > 0


def get_labels(conn) -> dict[str, dict]:
    return {r["uid"]: dict(r) for r in conn.execute("SELECT * FROM labels")}


def merge_labels(candidates: list[dict]) -> dict | None:
    """把一个去重组内多条标注合并成一条显示用记录。

    同一字段取**最近标注**的非空值 —— 不要按"更严格/更宽松"排序，
    那样会让后来的修正被先前的一致性压住。
    """
    live = [c for c in candidates if c]
    if not live:
        return None
    live.sort(key=lambda c: c.get("labeled_at") or "", reverse=True)
    merged = {}
    for key in WRITABLE + ("applied_at", "labeled_at"):
        merged[key] = next((c.get(key) for c in live if c.get(key)), None)
    return merged


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
        "想投": one("SELECT COUNT(*) FROM labels WHERE interest='want'"),
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
               "rules_version", "labeled_at", "url"]
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
        })

    target.write_text(json.dumps(fixtures, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"written": len(fixtures), "skipped": len(no_snippet) + len(unowned),
            "no_snippet": no_snippet, "unowned": unowned, "path": str(target)}
