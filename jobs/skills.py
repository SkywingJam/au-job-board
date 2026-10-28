r"""技能词表（skills.yaml）的加载、抽取、落库与编辑。

它**不是判断**，是词汇表：可再生、随手可改、不进打分。因此独立于
`rules.yaml`（那是评分面，改它要 bump version），也**不参与 total_score** ——
第一版它只做三件事：卡片打 tag、筛选栏多选、搜索时把一个词认成「技能词」。

设计要点：

  - **边界用非词字符** `(?<![\w+#])token(?![\w+#])`，不是 `\b`：
    `\b` 在 + / # 前也算边界，`\bC\b` 会把 C#/.NET 与 C++ 一起吃掉。
  - **最长优先 + 区间抑制**：先 React Native 再 React、先 Spring Boot 再
    Spring，否则同一处命中会打两个重叠 tag。
  - **ambiguous 词要佐证**：同篇还出现另一个技能词才认（例如 Go 出现在普通
    英文里、Swift 出现在银行业务里）。
  - **not 是全篇负向守卫**：命中则整条技能不计。用词要克制。

面板上的「加入词表 / 作为别名」写进 `skills.local.yaml`（本地补充层），
**不碰** `skills.yaml` —— 后者是手写的、带完整注释的种子词表，机器重写会把
注释冲掉。两层在 `load_skills()` 里合并。
"""

from __future__ import annotations

import os
import re
import stat
import tempfile
from pathlib import Path

import yaml

from .config import ROOT
from .file_lock import acquire_file_lock
from .file_security import apply_file_mode

DEFAULT_PATH = ROOT / "skills.yaml"
LOCAL_PATH = ROOT / "skills.local.yaml"

# 非词字符边界。见模块头：不是 \b。
_BOUNDARY_HEAD = r"(?<![\w+#])"
_BOUNDARY_TAIL = r"(?![\w+#])"


# ---------------------------------------------------------------------------
# 加载与合并
# ---------------------------------------------------------------------------

def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_base(path: str | Path | None = None) -> dict:
    """只读手写的 skills.yaml。"""
    return _read_yaml(Path(path) if path else DEFAULT_PATH)


def local_path_for(path: str | Path | None = None) -> Path:
    """补充层固定放在种子词表旁边，便于整体迁移/备份。"""
    if path is None:
        return LOCAL_PATH
    return Path(path).with_name("skills.local.yaml")


def load_local(path: str | Path | None = None) -> dict:
    """本地补充层。缺文件 = 空补充层，不是错误。"""
    cfg = _read_yaml(local_path_for(path))
    return {
        "version": cfg.get("version", 1),
        "add": list(cfg.get("add") or []),
        "alias": {k: list(v or []) for k, v in (cfg.get("alias") or {}).items()},
        # 删除是「相对种子的差异」，必须记在补充层，绝不能回头改种子文件
        "remove": [str(r) for r in (cfg.get("remove") or [])],
        "display_names": dict(cfg["display_names"])
        if isinstance(cfg.get("display_names"), dict) else {},
    }


def merge(base: dict, local: dict) -> dict:
    """把 local 的 add / alias 合进 base，返回一个新 dict。

    - `add`：追加新技能条目（同名条目存在则跳过，避免重复）
    - `alias`：给已有技能追加别名；找不到目标就跳过（写入口已经校验过，
      手改出错时宁可静默跳过，也不要让整个词表加载失败）
    - `remove`：最后应用抑制，保留旧 overlay 的行为；只有明确编辑才解除。
    - 显式本地别名优先于种子别名，重分配时保持唯一归属。
    - `display_names`：展示覆盖层；只接受仍属于该条目的 token，不改规范名
    """
    entries = [dict(e, aliases=list(e.get("aliases") or []))
               for e in (base.get("skills") or []) if e.get("name")]

    by_name = {e["name"]: e for e in entries}

    for raw in local.get("add") or []:
        name = str((raw or {}).get("name") or "").strip()
        if not name or name in by_name:
            continue
        entry = {"name": name, "aliases": [str(a) for a in (raw.get("aliases") or [])],
                 "not": [str(n) for n in (raw.get("not") or [])]}
        if raw.get("ambiguous"):
            entry["ambiguous"] = True
        if "display_name" in raw:
            entry["display_name"] = raw["display_name"]
        entries.append(entry)
        by_name[name] = entry

    for name, aliases in (local.get("alias") or {}).items():
        entry = by_name.get(name)
        if entry is None:
            continue
        have = {str(name).lower()} | {str(a).lower() for a in (entry.get("aliases") or [])}
        for alias in aliases:
            alias = str(alias).strip()
            if alias:
                # A local assignment replaces the old token owner. A removed
                # seed name may itself be reassigned; retire that seed entry
                # rather than leaving two canonical lookup results.
                entries[:] = [other for other in entries
                              if other is entry or str(other["name"]).lower() != alias.lower()]
                for other in entries:
                    if other is not entry:
                        other["aliases"] = [a for a in other.get("aliases") or []
                                            if str(a).lower() != alias.lower()]
                if alias.lower() not in have:
                    entry.setdefault("aliases", []).append(alias)
                    have.add(alias.lower())

    removed = {str(r).strip().lower() for r in (local.get("remove") or []) if str(r).strip()}
    if removed:
        kept = []
        for entry in entries:
            if str(entry.get("name")).lower() in removed:
                continue
            entry["aliases"] = [a for a in (entry.get("aliases") or [])
                                if str(a).strip().lower() not in removed]
            kept.append(entry)
        entries = kept

    overrides = local.get("display_names")
    if isinstance(overrides, dict):
        for entry in entries:
            chosen = _valid_display_name(entry, overrides.get(entry["name"]))
            if chosen:
                entry["display_name"] = chosen

    return {"version": base.get("version", 1), "skills": entries}


def load_skills(path: str | Path | None = None) -> dict:
    """合并后的词表（种子 + 本地补充）。所有使用方都走这个入口。"""
    return merge(load_base(path), load_local(path))


def save_local(local: dict, path: str | Path | None = None) -> Path:
    """写回补充层。**整体覆盖** —— 所以它刻意是机器管理的、不放注释。"""
    selected = local_path_for(path)
    target = selected.resolve()
    payload = {
        "version": local.get("version", 1),
        "add": list(local.get("add") or []),
        "alias": {k: list(v or []) for k, v in (local.get("alias") or {}).items()},
        "remove": [str(r) for r in (local.get("remove") or [])],
        "display_names": dict(local.get("display_names") or {}),
    }
    encoded = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False,
                             default_flow_style=False).encode("utf-8")
    mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o600
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix="." + target.name + ".",
                                         suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            apply_file_mode(handle, mode)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return selected


# ---------------------------------------------------------------------------
# 抽取
# ---------------------------------------------------------------------------

def canonical_tokens(cfg: dict) -> dict[str, str]:
    """token（小写）-> 规范名。搜索和编辑都靠它判断「这是不是一个技能词」。"""
    out: dict[str, str] = {}
    for entry in cfg.get("skills") or []:
        name = entry.get("name")
        if not name:
            continue
        for token in [name] + [str(a) for a in (entry.get("aliases") or [])]:
            out.setdefault(token.lower(), name)
    return out


def _token_pattern(token: str, prefix: bool = False) -> re.Pattern:
    tail = r"\w*" if prefix else _BOUNDARY_TAIL
    return re.compile(_BOUNDARY_HEAD + re.escape(token) + tail, re.IGNORECASE)


class Matcher:
    """编译好的词表匹配器。一次编译、多次抽取（sync 要跑全库）。

    **所有 token 合成一个 alternation，正文只扫一遍。** 每个 token 单独
    finditer 会把每篇正文重扫很多遍；合并成一个 alternation 后只扫一遍，
    面板加个别名也能更快拿到结果。

    最长优先由 alternation 的顺序保证（Python 正则取第一个能匹配的分支），
    而 finditer 本身不重叠，于是不用再手动记录已占区间。
    """

    def __init__(self, cfg: dict):
        self.names: list[str] = []
        self.lookup: dict[str, tuple[str, int, list, bool]] = {}
        entries = []   # (token, name, order, nots, ambiguous)
        for order, entry in enumerate(cfg.get("skills") or []):
            name = entry.get("name")
            if not name:
                continue
            self.names.append(name)
            nots = [re.compile(p, re.IGNORECASE) for p in (entry.get("not") or [])]
            ambiguous = bool(entry.get("ambiguous"))
            for token in [name] + [str(a) for a in (entry.get("aliases") or [])]:
                key = token.lower()
                entries.append((key, name, order, nots, ambiguous))
        # 最长优先：先 React Native 再 React、先 Spring Boot 再 Spring
        entries.sort(key=lambda item: (-len(item[0]), item[0]))
        for key, name, order, nots, ambiguous in entries:
            self.lookup.setdefault(key, (name, order, nots, ambiguous))
        if entries:
            alts = "|".join(re.escape(key) for key, *_ in entries)
            self.rx: re.Pattern | None = re.compile(
                _BOUNDARY_HEAD + "(?:" + alts + ")" + _BOUNDARY_TAIL, re.IGNORECASE)
        else:
            self.rx = None

    def extract(self, text: str | None) -> list[str]:
        """返回这篇 JD 命中的规范技能名（按词表顺序，不是出现顺序）。"""
        body = text or ""
        if not body or self.rx is None:
            return []
        matched: dict[str, tuple[int, bool]] = {}
        for m in self.rx.finditer(body):
            entry = self.lookup.get(m.group(0).lower())
            if entry is None:
                continue
            name, order, nots, ambiguous = entry
            if name in matched:
                continue
            # not 是**全篇**守卫：命中任一则该技能整条不计
            if nots and any(rx.search(body) for rx in nots):
                continue
            matched[name] = (order, ambiguous)

        found = set(matched)
        out = []
        for name, (order, ambiguous) in matched.items():
            # 歧义词要另一个技能词佐证。只剩它自己 -> 大概率是英文歧义。
            if ambiguous and len(found) < 2:
                continue
            out.append((order, name))
        out.sort()
        return [name for _, name in out]


def matcher(cfg: dict) -> Matcher:
    return Matcher(cfg)


def extract(text: str | None, cfg: dict) -> list[str]:
    """单条抽取的便捷入口（测试用）。批量抽取请复用 Matcher。"""
    return Matcher(cfg).extract(text)


# ---------------------------------------------------------------------------
# 搜索词编译
# ---------------------------------------------------------------------------

def _valid_display_name(entry: dict, value) -> str | None:
    """A label must still be a canonical token or one of this skill's aliases."""
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    for token in [entry.get("name")] + list(entry.get("aliases") or []):
        if isinstance(token, str) and token.strip().lower() == value:
            return token
    return None


def display_name(entry: dict) -> str:
    """Optional, presentation-only label; old and stale configs retain the name."""
    return _valid_display_name(entry, entry.get("display_name")) or str(entry.get("name") or "")


def display_names(cfg: dict) -> dict[str, str]:
    return {str(e["name"]): display_name(e) for e in cfg.get("skills") or [] if e.get("name")}

def term_matcher(term: str, cfg: dict) -> tuple[re.Pattern, str, str]:
    r"""把一个搜索词编译成匹配器。

    返回 `(compiled, kind, label)`：

      skill   词表里的词（含别名）-> 整词匹配。这是承重的一层：
              `java` 只匹配 Java（不是 javascript）、`c` 只匹配 C
              （不是 C#/C++）。没有它，下面按长度分的档会把 java 变成
              前缀 `java\w*`，照样吃掉 javascript。
      prefix  非词表词、长度 >= 3 -> 前缀匹配（`cyber` 命中 cybersecurity）
      word    长度 <= 2     -> 整词匹配（`t1` 保留；`+3` 被 + 挡住）
    """
    term = (term or "").strip()
    tokens = canonical_tokens(cfg)
    owner = tokens.get(term.lower())
    if owner:
        return _token_pattern(term), "skill", display_names(cfg).get(owner, owner)
    if len(term) >= 3:
        return _token_pattern(term, prefix=True), "prefix", term
    return _token_pattern(term), "word", term


def search_terms(query: str, cfg: dict) -> list[tuple[re.Pattern, str, str]]:
    return [term_matcher(term, cfg) for term in (query or "").split() if term.strip()]


def explain_term(kind: str, label: str) -> str:
    """搜索解释里每个词怎么读 —— 「决策要可追溯」的同一条原则。"""
    if kind == "skill":
        return f"技术词「{label}」"
    if kind == "prefix":
        return f"前缀「{label}」"
    return f"整词「{label}」"


# ---------------------------------------------------------------------------
# 落库
# ---------------------------------------------------------------------------

def sync(conn, cfg: dict) -> dict:
    """按当前词表重算全库技能，返回 {技能: 命中篇数}。

    analyze 与面板编辑词表后都会调它。抽取是纯函数，重建成本只在「扫一遍正文」，
    所以面板加个别名也应该立刻拿到结果。
    """
    from . import db as database
    match = Matcher(cfg)
    rows = conn.execute("SELECT uid, title, teaser, description FROM jobs").fetchall()
    database.clear_skills(conn)
    pairs: list[tuple[str, str]] = []
    counts: dict[str, int] = {}
    for row in rows:
        text = " \n ".join(str(p) for p in
                           (row["title"], row["teaser"], row["description"]) if p)
        for skill in match.extract(text):
            pairs.append((row["uid"], skill))
            counts[skill] = counts.get(skill, 0) + 1
    # 一次性批量插入。逐行 DELETE + INSERT 里的 DELETE 是空操作
    # （开头已经清空整表），纯属白付开销。
    database.add_skills(conn, pairs)
    conn.commit()
    return counts


def sync_incremental(conn, cfg: dict, tokens) -> int:
    """只重算「正文里出现新 token」的文档，返回更新的文档数。

    面板编辑器只会 add / alias，是**纯增量**，没必要每次全量重建。
    而且新 token 只会影响含它的文档：重叠抑制（React Native vs React）和
    ambiguous 的佐证，都只可能在这些文档里发生变化。所以候选集就是
    「tight boundary 命中任一新 token」的那些文档，逐个重抽即可。

    返回的计数是**更新了几篇**，不是全库技能数 —— 全量口径仍由 sync() 给。
    """
    from . import db as database
    toks = [str(t).strip() for t in (tokens or []) if str(t).strip()]
    if not toks:
        return 0
    match = Matcher(cfg)
    rx = re.compile(_BOUNDARY_HEAD + "(?:" + "|".join(re.escape(t) for t in toks)
                    + ")" + _BOUNDARY_TAIL, re.IGNORECASE)
    updated = 0
    for row in conn.execute("SELECT uid, title, teaser, description FROM jobs"):
        text = " \n ".join(str(p) for p in
                           (row["title"], row["teaser"], row["description"]) if p)
        if not rx.search(text):
            continue
        database.replace_skills(conn, row["uid"], match.extract(text))
        updated += 1
    conn.commit()
    return updated


# ---------------------------------------------------------------------------
# 编辑（面板用）
# ---------------------------------------------------------------------------

def _clean(token: str | None) -> str:
    return " ".join(str(token or "").split())


def apply_edit(local: dict, merged: dict, token: str, action: str,
               target: str | None = None, *, base: dict | None = None) -> dict:
    """在**补充层**上做一次编辑。校验基于合并后的词表，写回只写补充层。

    返回 `{"kind": "skill"|"alias", "skill": ..., "token": ...}`。
    校验失败抛 ValueError，由调用方翻译成 400。
    传入 base 可在恢复种子技能时保留原规范名，且不产生遮蔽的本地 add；
    持久化编辑统一由 edit_and_sync 读取 base 并传入。
    """
    token = _clean(token)
    if not token:
        raise ValueError("技能词不能为空")
    if len(token) > 40:
        raise ValueError("技能词最长 40 个字符")

    tokens = canonical_tokens(merged)
    owner = tokens.get(token.lower())

    if action == "display_name":
        target = _clean(target)
        entry = next((e for e in merged.get("skills") or [] if e.get("name") == target), None)
        if entry is None:
            raise ValueError(f"没有名为「{target}」的已有技能")
        chosen = _valid_display_name(entry, token)
        if chosen is None:
            raise ValueError("展示名必须是该技能的规范名或现有别名")
        local.setdefault("display_names", {})[target] = chosen
        return {"kind": "display_name", "skill": target, "token": chosen}

    if action == "add":
        if owner:
            raise ValueError(f"「{token}」已经属于「{owner}」，不能再加一个新技能")
        suppressed = {str(t).strip().lower() for t in local.get("remove") or []}
        seed = next((e for e in (base or {}).get("skills") or []
                     if str(e.get("name") or "").lower() == token.lower()), None)
        local["remove"] = [t for t in local.get("remove") or []
                           if str(t).strip().lower() != token.lower()]
        if seed is not None and token.lower() in suppressed:
            # Restore the seed identity and all its aliases, without a shadow add.
            token = seed["name"]
            local["add"] = [e for e in local.get("add") or []
                            if str(e.get("name") or "").lower() != token.lower()]
            return {"kind": "skill", "skill": token, "token": token}
        local.setdefault("add", []).append({"name": token, "aliases": [], "not": []})
        return {"kind": "skill", "skill": token, "token": token}

    if action == "alias":
        target = _clean(target)
        names = {str(e.get("name")) for e in merged.get("skills") or []}
        if target not in names:
            raise ValueError(f"没有名为「{target}」的已有技能")
        if owner == target:
            raise ValueError(f"「{token}」已经是「{target}」的词")
        if owner:
            raise ValueError(f"「{token}」已属于「{owner}」，先从那里移除")
        local["remove"] = [t for t in local.get("remove") or []
                           if str(t).strip().lower() != token.lower()]
        # Historical overlays may contain several suppressed claims. A fresh
        # edit chooses one target and must supersede those latent assignments.
        for previous, values in list((local.get("alias") or {}).items()):
            if previous != target:
                values[:] = [a for a in values if str(a).strip().lower() != token.lower()]
                if not values:
                    del local["alias"][previous]
        aliases = local.setdefault("alias", {}).setdefault(target, [])
        if token not in aliases:
            aliases.append(token)
        return {"kind": "alias", "skill": target, "token": token}

    if action == "remove":
        if not owner:
            raise ValueError(f"词表里没有「{token}」，无需移除")
        display_overrides = local.get("display_names") or {}
        if (token.lower() == owner.lower()
                or _clean(display_overrides.get(owner)).lower() == token.lower()):
            display_overrides.pop(owner, None)
        if token.lower() == owner.lower():
            entry = next(e for e in merged["skills"] if e["name"] == owner)
            owned = {str(a).strip().lower() for a in entry.get("aliases") or []}
            suppressed = local.setdefault("remove", [])
            for seed in (base or {}).get("skills") or []:
                if seed.get("name") == owner:
                    continue
                for original in [seed.get("name"), *(seed.get("aliases") or [])]:
                    if original and str(original).strip().lower() in owned:
                        if str(original).lower() not in {str(t).lower() for t in suppressed}:
                            suppressed.append(original)
        # 优先从补充层直接删，让它只记「相对种子的差异」；只有种子里的词
        # 才写进 remove 抑制列表。这样补充层始终可读、可整体丢弃。
        for entry in list(local.get("add") or []):
            if str(entry.get("name") or "").strip().lower() == token.lower():
                local["add"].remove(entry)
                return {"kind": "remove", "skill": owner, "token": token}
        for target, aliases in list((local.get("alias") or {}).items()):
            hit = [a for a in aliases if str(a).strip().lower() == token.lower()]
            if hit:
                aliases.remove(hit[0])
                if not aliases:
                    del local["alias"][target]
                # Removing a reassigned seed alias must not revive its old owner.
                if any(str(a).strip().lower() == token.lower()
                       for e in (base or {}).get("skills") or []
                       for a in [e.get("name"), *(e.get("aliases") or [])] if a):
                    local.setdefault("remove", []).append(token)
                return {"kind": "remove", "skill": owner, "token": token}
        removed = local.setdefault("remove", [])
        if token not in removed:
            removed.append(token)
        return {"kind": "remove", "skill": owner, "token": token}

    raise ValueError("action 只能是 add / alias / remove / display_name")


def skill_names(cfg: dict) -> list[str]:
    return [str(e.get("name")) for e in cfg.get("skills") or [] if e.get("name")]


def edit_and_sync(conn, path, token, action, target=None) -> dict:
    """Serialize a complete edit by overlay identity, across DBs and processes.

    Callers must use this entry instead of loading and saving a stale local copy.
    The persistent sidecar lock is never unlinked (replacing its inode would let
    later writers bypass an existing waiter). File errors propagate as OSError;
    edit validation retains ValueError. Full rebuilds return counts, other edits
    return counts=None; docs is the number of jobs recomputed.
    """
    overlay = local_path_for(path).resolve()
    lock_path = overlay.with_name("." + overlay.name + ".lock")
    with acquire_file_lock(lock_path, blocking=True, mode=0o600) as handle:
        base = load_base(path)
        local = load_local(path)
        merged = merge(base, local)
        removed = {str(t).strip().lower() for t in local.get("remove") or []}
        restoring_seed = action == "add" and _clean(token).lower() in removed and any(
            str(e.get("name") or "").lower() == _clean(token).lower()
            for e in base.get("skills") or [])
        reassigning_seed = action == "alias" and any(
            str(e.get("name") or "").lower() == _clean(token).lower()
            for e in base.get("skills") or [])
        info = apply_edit(local, merged, token, action, target, base=base)
        previous = overlay.read_bytes() if overlay.exists() else None
        previous_mode = stat.S_IMODE(overlay.stat().st_mode) if overlay.exists() else 0o600
        published = False
        try:
            if info["kind"] != "display_name" and not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            save_local(local, path)
            published = True
            fresh = merge(base, local)
            counts = None
            if info["kind"] == "display_name":
                docs = 0
            elif info["kind"] == "remove" or restoring_seed or reassigning_seed:
                docs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
                counts = sync(conn, fresh)
            else:
                docs = sync_incremental(conn, fresh, [info["token"]])
            return {**info, "docs": docs, "counts": counts}
        except BaseException:
            conn.rollback()
            if published:
                if previous is None:
                    overlay.unlink(missing_ok=True)
                else:
                    temporary = None
                    try:
                        with tempfile.NamedTemporaryFile(dir=overlay.parent, prefix="." + overlay.name + ".",
                                                         suffix=".tmp", delete=False) as stream:
                            temporary = Path(stream.name)
                            apply_file_mode(stream, previous_mode)
                            stream.write(previous)
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.replace(temporary, overlay)
                    finally:
                        if temporary is not None:
                            temporary.unlink(missing_ok=True)
            raise
