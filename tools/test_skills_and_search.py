r"""技能词表 + 搜索匹配器的回归测试（不联网、不写库）。

覆盖三件「看一眼觉得对、用起来才发现不对」的事：

  边界     \\b 在 + / # 前也算边界，于是 \\bC\\b 会吃掉 C#/.NET 与 C++。
           统一改用非词字符边界 (?<![\\w+#])token(?![\\w+#])。
  佐证     Go 是常见英文词（go to the gym / go-to-market 之类）；
           歧义词必须同篇还出现另一个技能词才认。
  匹配器   java 不能吃 javascript（词表整词）、cyber 仍要命中 cybersecurity
           （非词表前缀）、+3 里的 3 不能被当成整词。

这里出现的「真实词表」指**实际配置词表**（skills.yaml 经
jobs.skills.load_skills() 与本地覆盖层合并），不是真实 JD；测试只读它，
从不写回配置。其余输入都是手写合成短句与占位行。

用法：
    .venv/bin/python tools/test_skills_and_search.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jobs import panel, skills                      # noqa: E402
from jobs.render import tags_for                    # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{('  ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


# 独立的迷你词表：不依赖 skills.yaml 的内容，只验证匹配语义。
CFG = {"skills": [
    {"name": "C", "aliases": [], "not": []},
    {"name": "C#", "aliases": ["C Sharp"], "not": []},
    {"name": "C++", "aliases": ["Cpp"], "not": []},
    {"name": "Java", "aliases": [], "not": []},
    {"name": "JavaScript", "aliases": ["JS"], "not": []},
    {"name": "Go", "aliases": ["Golang"], "not": [], "ambiguous": True},
    {"name": "Swift", "aliases": [],
     "not": [r"\bswift\b[^.\n]{0,20}\b(message|payment|reaction)\b"]},
    {"name": "React", "aliases": ["ReactJS"], "not": []},
    {"name": "React Native", "aliases": [], "not": []},
    {"name": "Spring", "aliases": ["Spring Boot"], "not": []},
    {"name": "Kubernetes", "aliases": ["K8s"], "not": []},
    {"name": "Docker", "aliases": [], "not": []},
    {"name": "Python", "aliases": [], "not": []},
    {"name": "AWS", "aliases": ["Amazon Web Services"], "not": []},
]}


def test_extract() -> None:
    print("== 抽取：边界 / 佐证 / 最长优先 ==")
    m = skills.matcher(CFG)
    cases = [
        ("C# 不等于 C", "C# and .NET", {"C#"}),
        ("C++ 不等于 C", "C++ engineer", {"C++"}),
        ("纯 C", "C developer", {"C"}),
        ("go to the gym 无佐证 -> 不认", "Go to the gym", set()),
        ("go-to-market 无佐证 -> 不认", "own the go-to-market motion", set()),
        ("Go + Docker 有佐证 -> 认", "We use Go and Docker", {"Go", "Docker"}),
        ("golang 别名 + 佐证", "golang with kubernetes", {"Go", "Kubernetes"}),
        ("SWIFT 报文 -> 不认 Swift", "SWIFT message processing", set()),
        ("日常英语 swift reaction -> 不认", "swift reaction times", set()),
        ("真 Swift + 佐证", "Swift and Docker", {"Swift", "Docker"}),
        ("React Native 不重复打 React", "React Native developer", {"React Native"}),
        ("Spring Boot 归 Spring", "Spring Boot microservices", {"Spring"}),
        ("k8s 别名归 Kubernetes", "deploy on K8s", {"Kubernetes"}),
    ]
    for label, text, want in cases:
        got = set(m.extract(text))
        check(label, got == want, f"{sorted(got)}")


def test_term_matcher() -> None:
    print("\n== 搜索词分档 ==")
    got = {t: skills.term_matcher(t, CFG)[1] for t in
           ("c", "c++", "java", "k8s", "cyber", "python", "t1", "ai", "3")}
    check("c 是词表词", got["c"] == "skill", got["c"])
    check("c++ 是词表词", got["c++"] == "skill", got["c++"])
    check("java 是词表词", got["java"] == "skill")
    check("k8s 是别名（也算词表词）", got["k8s"] == "skill")
    check("cyber 非词表词 -> 前缀", got["cyber"] == "prefix")
    check("t1 短词 -> 整词", got["t1"] == "word")
    check("ai 短词 -> 整词", got["ai"] == "word")
    check("3 短词 -> 整词", got["3"] == "word")


def test_search() -> None:
    print("\n== 搜索：术语 AND + 三档匹配 ==")

    def row(uid, title):
        return {"uid": uid, "title": title, "company": "", "location": "",
                "salary": "", "description": "", "skills": [], "reasons": []}

    rows = [
        row("c", "C developer"), row("csharp", "C# .NET developer"),
        row("cpp", "C++ engineer"), row("java", "Java developer"),
        row("js", "JavaScript developer"), row("cyber", "Cybersecurity Analyst"),
        row("t1", "T1 SOC Analyst"), row("plus3", "Graduate +3"),
        row("tier3", "Tier 3 Support"), row("email", "email available training"),
        row("ai", "AI platform engineer"), row("both", "Python and AWS engineer"),
        row("py", "Python developer"),
    ]

    def found(q):
        return {r["uid"] for r in panel._search(rows, q, {}, {}, CFG)}

    check("c 只命中 C（不吃 C#/C++/cybersecurity）", found("c") == {"c"}, str(sorted(found("c"))))
    check("java 不吃 javascript", found("java") == {"java"}, str(sorted(found("java"))))
    check("cyber 前缀仍命中 cybersecurity", found("cyber") == {"cyber"})
    check("t1 整词保留", found("t1") == {"t1"})
    check("3 不匹配 +3，但匹配 Tier 3", found("3") == {"tier3"}, str(sorted(found("3"))))
    check("ai 不吃 email/available/training", found("ai") == {"ai"}, str(sorted(found("ai"))))
    check("多词是 AND", found("python aws") == {"both"}, str(sorted(found("python aws"))))
    check("多词无交集就是空", found("java python") == set())


def test_filters_and_link() -> None:
    print("\n== 技能/层级筛选与链接 ==")
    parsed = panel.parse_filters({"skill": ["Python", "AWS"], "tier": ["T1"]})
    check("skill 多值保留", parsed.get("skill") == ["Python", "AWS"], str(parsed))
    check("tier 解析", parsed.get("tier") == "T1")
    check("非法 tier 丢弃", "tier" not in panel.parse_filters({"tier": ["T9"]}))

    hit = {"uid": "x", "tier": "T1", "skills": ["Python", "Docker"]}
    miss = {"uid": "y", "tier": "T2", "skills": ["Docker"]}
    check("技能多选 = 任一命中", panel.passes_filters(hit, {}, parsed))
    check("层级不符被筛掉", not panel.passes_filters(miss, {}, parsed))
    only_py = panel.parse_filters({"skill": ["Python"]})
    check("没有该技能的岗位被筛掉",
          not panel.passes_filters({"uid": "z", "skills": ["Docker"]}, {}, only_py))

    link = panel._link("all", 40, {"q": "c++", "skill": ["Python", "AWS"]})
    check("搜索词被 urlencode（c++ 不再是空格）", "q=c%2B%2B" in link, link)
    check("多选展开成多个同名参数", link.count("skill=") == 2)
    check("offset 保留", "offset=40" in link)


def test_explanation_and_edit() -> None:
    print("\n== 搜索解释 & 词表编辑 ==")
    one = panel._search_explanation({"q": "c"}, CFG, 67)
    check("单术语解释带技能名与条数", "技能词「C」" in one and "67" in one, one)
    multi = panel._search_explanation({"q": "cyber python"}, CFG, 5)
    check("多术语解释说明 AND", "前缀「cyber」" in multi and "全部满足" in multi, multi)

    base = {"version": 1, "skills": [{"name": "Docker", "aliases": [], "not": []}]}
    local = {"version": 1, "add": [], "alias": {}}
    merged = skills.merge(base, local)
    skills.apply_edit(local, merged, "Podman", "add")
    merged = skills.merge(base, local)
    check("新增技能进入合并词表", "Podman" in skills.skill_names(merged))
    skills.apply_edit(local, merged, "ContainerD", "alias", "Docker")
    merged = skills.merge(base, local)
    check("别名归到已有技能",
          skills.canonical_tokens(merged).get("containerd") == "Docker")
    check("重复别名不写两遍",
          [a for a in merged["skills"][0]["aliases"]].count("ContainerD") == 1)

    def fails(token, action, target=None):
        try:
            skills.apply_edit(local, merged, token, action, target)
        except ValueError:
            return True
        return False

    check("已属于别的技能，不能再加新技能", fails("docker", "add"))
    check("已属于别的技能，不能再做别名", fails("Podman", "alias", "Docker"))
    check("别名目标必须存在", fails("Nomad", "alias", "NoSuchSkill"))
    check("空词被打回", fails("   ", "add"))
    check("超长词被打回", fails("x" * 41, "add"))


def test_tags_and_real_vocab() -> None:
    print("\n== tag 封顶 & 真实词表 ==")
    tags = [t for k, t in tags_for({"reasons": [], "skills": [f"S{i}" for i in range(10)]})
            if k == "skill"]
    check("列表卡片技能 tag 封顶 8 + 溢出计数", len(tags) == 9 and tags[-1] == "+2", str(tags))
    all_tags = [t for k, t in tags_for({"reasons": [], "skills": [f"S{i}" for i in range(10)]},
                                       skill_limit=None) if k == "skill"]
    check("详情页技能 tag 不封顶", len(all_tags) == 10 and "+2" not in all_tags, str(all_tags))

    # 「真实词表」= 实际配置词表（种子 + 本地覆盖层），不是真实 JD；只读，不写回。
    real = skills.load_skills()
    m = skills.matcher(real)
    check("真实词表：C# 不触发 C", set(m.extract("C# and .NET")) == {"C#", ".NET"})
    check("真实词表：Go 需要佐证", m.extract("Go to the gym") == [])
    names = set(skills.skill_names(real))
    check("真实词表包含种子词", {"Python", "AWS", "Kubernetes", "C"} <= names)


def test_detail_editor_html() -> None:
    print("\n== 详情技能编辑器 ==")
    # 合成占位行：uid / 公司 / 地点都是占位值；URL 用保留域名 example.invalid，
    # 仅用于渲染测试，不联网、不代表任何真实岗位。
    row = {"uid": "u1", "title": "T", "company": "C", "location": "L", "source": "seek",
           "description": "uses Python", "skills": ["Python"], "reasons": [],
           "url": "https://example.invalid/job/synthetic_001"}
    html = panel._detail_html(row)
    check("有保存状态位（正在保存 / 已保存）", 'id="skill-status"' in html)
    check("输入框失焦时收起候选", 'onblur="skillBlur()"' in html)
    check("候选菜单存在", 'id="skill-menu"' in html)
    check("不再重复列「本条命中」", "本条命中" not in html)
    row10 = dict(row, skills=[f"S{i}" for i in range(10)])
    check("详情页技能 tag 全展开（无 +N）", "+2" not in panel._detail_html(row10))


def test_skill_tree_and_settings() -> None:
    print("\n== 设置页数据与渲染 ==")
    import os
    import tempfile

    from jobs import db as database
    from jobs import panel

    with tempfile.TemporaryDirectory() as d:
        conn = database.connect(os.path.join(d, "t.db"))
        database.upsert_job(conn, {"source": "t", "source_id": "a",
                                   "title": "GraphQL API", "description": "graphql"})
        conn.commit()
        # 用实际配置词表把 job_skills 落到临时 DB；只读词表，不写回配置。
        skills.sync(conn, skills.load_skills())
        tree = panel.skill_tree(conn)
        check("skill_tree 有 skills / remove 两个键", set(tree) == {"skills", "remove"})
        want = {"name", "hits", "origin", "aliases", "ambiguous", "has_not"}
        check("每条含全部字段", all(want <= set(s) for s in tree["skills"]))
        names = [s["name"] for s in tree["skills"]]
        check("按字母顺序返回（方便查找）", names == sorted(names, key=str.lower))
        html = panel.render_settings(conn, {}, section="skills")
        check("设置页有左导航与右列表容器",
              'class="set-nav"' in html and 'id="skill-tree"' in html)
        check("返回是独立图标按钮，不是文字链接",
              'class="backlink"' in html and "← 返回面板" not in html)
        check("设置页有前端搜索框",
              'id="skill-filter"' in html and "function currentFilter" in html)
        check("失焦关的是详情候选菜单，不是筛选面板",
              "function skillMenuClose" in html
              and "function skillBlur(){ skillMenuClose(); }" in html)
        check("候选标签不再展开别名", "'（'+aliases+'）'" not in html)
        check("菜单定位改用 visualViewport（iOS 键盘）",
              "function placeMenu" in html and "window.visualViewport" in html)
        check("聚焦时的滚动只重定位、不收起候选",
              "function syncMenusOnScroll" in html
              and "active===si) placeMenu" in html)
        check("设置页嵌入初始数据", "window.__SKILL_TREE__" in html)
        check("设置页加载设置脚本", "armConfirm" in html)
        # 回归：渲染时不得调用 armConfirm，否则一进页面所有 × 都是「确认删除」
        check("删除按钮点击时才进入确认态",
              "del.onclick=()=>armConfirm(del," in html)
        check("技能行只留铅笔按钮，别名与删除在编辑窗里",
              "openEditor(s.name)" in html and "skBtn(" not in html)
        check("编辑窗：主要展示名支持拖拽并写入补充层",
              "addEventListener('drop'" in html and "action:'display_name'" in html)
        check("来源三态与按来源着色的泡泡",
              "srcpair" in html and "alias_origins" in html)
        want2 = {"source", "alias_origins"}
        check("skill_tree 给出条目来源三态与别名来源",
              all(want2 <= set(s) for s in tree["skills"])
              and {s["source"] for s in tree["skills"]} <= {"seed", "local", "mixed"})
        check("滚动在列表上，标题/新增条固定",
              ".set-pane{display:flex" in html and ".skill-tree{flex:1 1 auto" in html)
        check("技术栈多选面板自身滚动不会被收起",
              "menus.some" in html and "getElementById('skill-panel')" in html)
        conn.close()


def test_remove_edit() -> None:
    print("\n== 删除（remove）==")
    base = {"skills": [
        {"name": "Agile", "aliases": ["Scrum"], "not": []},
        {"name": "Python", "aliases": [], "not": []},
    ]}
    local = {"version": 1, "add": [], "alias": {}, "remove": []}
    skills.apply_edit(local, skills.merge(base, local), "Agile", "remove")
    after = skills.merge(base, local)
    check("删种子技能 -> 整条摘掉（连别名）",
          "Agile" not in skills.skill_names(after),
          str(skills.skill_names(after)))
    check("别名一起没了",
          all("Scrum" not in (e.get("aliases") or []) for e in after["skills"]))
    check("种子文件本身不动", any(e["name"] == "Agile" for e in base["skills"]))
    check("抑制记在补充层", local["remove"] == ["Agile"])

    base2 = {"skills": [{"name": "Kubernetes", "aliases": ["K8s"], "not": []}]}
    local2 = {"version": 1, "add": [], "alias": {}, "remove": []}
    skills.apply_edit(local2, skills.merge(base2, local2), "K8s", "remove")
    m2 = skills.merge(base2, local2)
    check("删种子别名只摘别名，技能还在",
          skills.skill_names(m2) == ["Kubernetes"] and m2["skills"][0]["aliases"] == [])
    check("种子别名靠 remove 抑制", local2["remove"] == ["K8s"])
    skills.apply_edit(local2, skills.merge(base2, local2),
                      "k8s", "alias", "Kubernetes", base=base2)
    check("恢复种子别名清除大小写不敏感的抑制",
          skills.extract("K8s", skills.merge(base2, local2)) == ["Kubernetes"]
          and not local2["remove"])

    legacy_base = {"skills": [
        {"name": "React", "aliases": ["ReactJS"], "not": []},
        {"name": "JavaScript", "aliases": ["JS"], "not": []},
        {"name": "Java", "aliases": [], "not": []},
    ]}
    legacy = {"remove": ["React", "JS"],
              "add": [{"name": "React", "aliases": [], "not": []}],
              "alias": {"Java": ["JS"]}}
    legacy_merged = skills.merge(legacy_base, legacy)
    check("旧overlay冲突不在升级时自动激活",
          "React" not in skills.skill_names(legacy_merged)
          and "js" not in skills.canonical_tokens(legacy_merged))
    skills.apply_edit(legacy, legacy_merged, "react", "add", base=legacy_base)
    check("旧shadow add显式恢复后保留种子别名",
          skills.extract("ReactJS", skills.merge(legacy_base, legacy)) == ["React"]
          and not legacy["add"])

    reassigned_base = {"skills": [
        {"name": "JavaScript", "aliases": ["JS"], "not": []},
        {"name": "Java", "aliases": [], "not": []},
    ]}
    reassigned = {"version": 1, "add": [], "alias": {}, "remove": []}
    skills.apply_edit(reassigned, skills.merge(reassigned_base, reassigned), "JS", "remove")
    skills.apply_edit(reassigned, skills.merge(reassigned_base, reassigned),
                      "JS", "alias", "Java", base=reassigned_base)
    reassigned_merged = skills.merge(reassigned_base, reassigned)
    reassigned_tokens = skills.canonical_tokens(reassigned_merged)
    check("重新分配已删除种子别名时保留原主体抑制",
          not reassigned["remove"]
          and reassigned_tokens.get("js") == "Java"
          and all("js" not in {str(a).lower() for a in e["aliases"]}
                  for e in reassigned_merged["skills"] if e["name"] == "JavaScript"))

    skills.apply_edit(reassigned, reassigned_merged, "JS", "remove", base=reassigned_base)
    check("移除重分配别名不复活种子归属",
          "js" not in skills.canonical_tokens(skills.merge(reassigned_base, reassigned)))

    canonical_reassigned = {"add": [], "alias": {}, "remove": ["JavaScript"]}
    skills.apply_edit(canonical_reassigned, skills.merge(reassigned_base, canonical_reassigned),
                      "JavaScript", "alias", "Java", base=reassigned_base)
    canonical_merged = skills.merge(reassigned_base, canonical_reassigned)
    check("删除后的种子规范名重分配时只有新归属",
          skills.canonical_tokens(canonical_merged).get("javascript") == "Java"
          and "JavaScript" not in skills.skill_names(canonical_merged))
    skills.apply_edit(canonical_reassigned, canonical_merged, "JavaScript", "remove", base=reassigned_base)
    check("移除重分配的规范名不复活原技能或其别名",
          "javascript" not in skills.canonical_tokens(skills.merge(reassigned_base, canonical_reassigned))
          and "js" not in skills.canonical_tokens(skills.merge(reassigned_base, canonical_reassigned)))

    latent = {"remove": ["JS"], "alias": {"Java": ["JS"], "Kotlin": ["js"]},
              "add": [{"name": "Kotlin", "aliases": [], "not": []}]}
    skills.apply_edit(latent, skills.merge(reassigned_base, latent), "JS", "alias", "Java", base=reassigned_base)
    check("新归属覆盖全部旧的潜伏别名声明",
          skills.canonical_tokens(skills.merge(reassigned_base, latent))["js"] == "Java"
          and "Kotlin" not in latent["alias"])

    local_target = {"add": [{"name": "LocalSkill", "aliases": [], "not": []}],
                    "alias": {}, "remove": ["JS"]}
    skills.apply_edit(local_target, skills.merge(reassigned_base, local_target),
                      "JS", "alias", "LocalSkill", base=reassigned_base)
    skills.apply_edit(local_target, skills.merge(reassigned_base, local_target),
                      "LocalSkill", "remove", base=reassigned_base)
    check("移除本地目标技能不复活已重分配的种子别名",
          "js" not in skills.canonical_tokens(skills.merge(reassigned_base, local_target)))

    skills.apply_edit(local, skills.merge(base, local), "Agile", "add", base=base)
    check("恢复种子技能及原有别名",
          skills.extract("Scrum", skills.merge(base, local)) == ["Agile"]
          and not local["remove"] and not local["add"])

    local3 = {"version": 1, "add": [{"name": "Podman", "aliases": [], "not": []}],
              "alias": {}, "remove": []}
    skills.apply_edit(local3, skills.merge({"skills": []}, local3), "Podman", "remove")
    check("删本地新增 -> 直接删条目，不留残渣",
          local3["add"] == [] and local3["remove"] == [])

    local4 = {"version": 1, "add": [], "alias": {"Docker": ["Podman"]}, "remove": []}
    merged4 = skills.merge({"skills": [{"name": "Docker", "aliases": [], "not": []}]}, local4)
    skills.apply_edit(local4, merged4, "Podman", "remove")
    check("删本地别名 -> 从 alias 里去掉", "Docker" not in local4["alias"])

    try:
        skills.apply_edit(local4, skills.merge({"skills": []}, local4), "Nope", "remove")
        raised = False
    except ValueError:
        raised = True
    check("删不存在的词被打回", raised)


def test_incremental_sync() -> None:
    print("\n== 增量重算（面板 add / alias 用）==")
    import os
    import tempfile

    from jobs import db as database

    with tempfile.TemporaryDirectory() as d:
        conn = database.connect(os.path.join(d, "t.db"))
        database.upsert_job(conn, {"source": "t", "source_id": "a",
                                   "title": "GraphQL API",
                                   "description": "GraphQL and REST"})
        database.upsert_job(conn, {"source": "t", "source_id": "b",
                                   "title": "Python dev",
                                   "description": "python only"})
        conn.commit()

        base = {"skills": [{"name": "Python", "aliases": [], "not": []}]}
        skills.sync(conn, base)

        local = {"version": 1, "add": [], "alias": {}}
        skills.apply_edit(local, base, "GraphQL", "add")
        merged = skills.merge(base, local)

        updated = skills.sync_incremental(conn, merged, ["GraphQL"])
        got = database.skills_map(conn)

        skills.sync(conn, merged)            # 全量作为对照
        full = database.skills_map(conn)

        check("只重抽含新 token 的文档", updated == 1, f"updated={updated}")
        check("命中新 token 的文档拿到新技能", "GraphQL" in got.get("t:a", []), str(got))
        check("增量结果与全量重建一致", got == full)
        check("不含新 token 的文档不受影响", "GraphQL" not in got.get("t:b", []))
        conn.close()


def test_primary_display_names() -> None:
    """Labels persist without changing canonical extraction, filtering or DB."""
    import tempfile
    from unittest.mock import patch
    from jobs import db as database, report, panel_views

    print("\n== 可选主要展示名与兼容性 ==")
    base = {"version": 1, "skills": [
        {"name": "Kubernetes", "aliases": ["K8s"], "not": []},
        {"name": "Docker", "aliases": [], "not": []}]}
    check("旧词表默认规范名", skills.display_names(skills.merge(base, {})) ==
          {"Kubernetes": "Kubernetes", "Docker": "Docker"})
    local = {}
    skills.apply_edit(local, skills.merge(base, local), "k8s", "display_name", "Kubernetes")
    merged = skills.merge(base, local)
    check("别名展示不改变规范抽取", skills.extract("K8s", merged) == ["Kubernetes"])
    check("展示名使用已有 token 的原大小写", skills.display_names(merged)["Kubernetes"] == "K8s")
    check("搜索解释用主要展示名", skills.term_matcher("kubernetes", merged)[2] == "K8s")
    picker = panel_views._skill_picker({"skill": ["Kubernetes"]}, {"Kubernetes": 3},
                                      "en", skills.display_names(merged))
    check("筛选展示别名但提交规范名", 'value="Kubernetes" checked' in picker
          and "<span>K8s</span>" in picker and '<span class="v">K8s</span>' in picker)
    chip = panel_views._chip_text("skill", {"skill": ["Kubernetes"]}, "en",
                                  skills.display_names(merged))
    check("已选筛选 chip 展示主要名称", "K8s" in chip and "Kubernetes" not in chip)
    check("合并不修改种子对象", "display_name" not in base["skills"][0])
    for invalid in ("missing", 12, [], {"x": "K8s"}, None):
        stale = skills.merge(base, {"display_names": {"Kubernetes": invalid}})
        check(f"无效展示名 {invalid!r} 回退", skills.display_names(stale)["Kubernetes"] == "Kubernetes")
    removed = skills.merge(base, dict(local, remove=["K8s"]))
    check("删除展示别名后回退规范名", skills.display_names(removed)["Kubernetes"] == "Kubernetes")
    for alias, alias_overlay in (("K8s", {}), ("Kube", {"Kubernetes": ["Kube"]})):
        edited = {"alias": alias_overlay, "display_names": {"Kubernetes": alias}}
        skills.apply_edit(edited, skills.merge(base, edited), alias.swapcase(), "remove")
        check(f"删除主要展示别名 {alias} 清除覆盖选择",
              "Kubernetes" not in edited["display_names"])
        check(f"删除主要展示别名 {alias} 后回退规范名",
              skills.display_names(skills.merge(base, edited))["Kubernetes"] == "Kubernetes")
    with tempfile.TemporaryDirectory() as directory:
        vocab = Path(directory) / "skills.yaml"
        local_alias = {"alias": {"Kubernetes": ["Kube", "kubectl"]},
                       "display_names": {"Kubernetes": "Kube"}}
        skills.apply_edit(local_alias, skills.merge(base, local_alias), "kubectl", "remove")
        check("删除其他别名保留主要展示名", local_alias["display_names"]["Kubernetes"] == "Kube")
        skills.apply_edit(local_alias, skills.merge(base, local_alias), "Kube", "remove")
        skills.save_local(local_alias, vocab)
        restored = skills.load_local(vocab)
        skills.apply_edit(restored, skills.merge(base, restored),
                          "Kube", "alias", "Kubernetes", base=base)
        skills.save_local(restored, vocab)
        reloaded = skills.merge(base, skills.load_local(vocab))
        check("重新添加已删除别名后仍可正常抽取", skills.extract("Kube", reloaded) == ["Kubernetes"])
        check("删除并重加别名不会复活旧主要展示名",
              skills.display_names(reloaded)["Kubernetes"] == "Kubernetes")
    seeded = {"skills": [dict(base["skills"][0], display_name="K8s")]}
    check("种子可选字段生效", skills.display_names(skills.merge(seeded, {}))["Kubernetes"] == "K8s")
    for token, target in (("Docker", "Kubernetes"), ("K8s", "missing")):
        try:
            skills.apply_edit({}, merged, token, "display_name", target)
            rejected = False
        except ValueError:
            rejected = True
        check(f"拒绝错误展示名目标 {token}/{target}", rejected)

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        vocab = root / "skills.yaml"
        import yaml
        vocab.write_text(yaml.safe_dump(base), encoding="utf-8")
        seed_bytes = vocab.read_bytes()
        config = {"skills_path": str(vocab), "db_path": str(root / "test.db")}
        conn = database.connect(config["db_path"])
        database.upsert_job(conn, {"source": "synthetic", "source_id": "label",
                                   "title": "K8s developer", "description": "K8s"})
        conn.commit()
        skills.sync(conn, skills.load_skills(vocab))
        before = database.skills_map(conn)
        conn.close()
        handler = panel.Handler.__new__(panel.Handler)
        handler.config = config
        replies = []
        handler._json = lambda data, status, started: replies.append((data, status))
        with patch.object(skills, "sync", side_effect=AssertionError("unexpected recompute")), \
             patch.object(skills, "sync_incremental", side_effect=AssertionError("unexpected recompute")):
            handler._post_skills({"action": "display_name", "target": "Kubernetes", "token": "K8s"}, 0)
        check("API 展示编辑不重算", replies[-1][1] == 200 and replies[-1][0].get("docs") == 0)
        fresh = skills.load_skills(vocab)
        check("主要展示名写入补充层并可重载", skills.display_names(fresh)["Kubernetes"] == "K8s")
        check("种子文件字节保持原样", vocab.read_bytes() == seed_bytes)
        conn = database.connect(config["db_path"])
        check("DB 技能身份保持原样", database.skills_map(conn) == before)
        rows, _excluded, _labels, _index = report.load(conn, config)
        row = rows[0]
        check("列表和详情 tag 展示别名", ("skill", "K8s") in tags_for(row))
        check("旧规范名筛选继续匹配", panel.passes_filters(row, {}, {"skill": ["Kubernetes"]}))
        check("旧规范名搜索继续匹配", bool(panel._search(rows, "Kubernetes", {}, {}, fresh)))
        tree = panel.skill_tree(conn, str(vocab))
        entry = next(s for s in tree["skills"] if s["name"] == "Kubernetes")
        check("设置数据含展示名和本地来源", entry["display_name"] == "K8s"
              and entry["display_name_origin"] == "local" and entry["source"] == "mixed")
        conn.close()


def test_cli_report_vocabulary() -> None:
    """CLI report labels follow the vocabulary selected for CLI analysis."""
    import csv
    import tempfile
    from types import SimpleNamespace
    from unittest.mock import patch
    import yaml
    from jobs import cli, db as database

    print("\n== CLI 报告沿用分析词表 ==")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        selected = root / "selected.yaml"
        configured = root / "configured.yaml"
        default = root / "default.yaml"
        for path, label in ((selected, "CLI label"), (configured, "Config label"),
                            (default, "Default label")):
            path.write_text(yaml.safe_dump({"skills": [
                {"name": "SyntheticSkill", "aliases": [label], "display_name": label}]}),
                encoding="utf-8")
        conn = database.connect(root / "test.db")
        database.upsert_job(conn, {"source": "synthetic", "source_id": "report",
                                   "title": "SyntheticSkill developer"})
        database.replace_score(conn, "synthetic:report", "T1", 10, 0, 0, 10, [], "test")
        conn.commit()
        config = {"skills_path": str(configured), "report": {"out_dir": str(root / "out")}}
        for path, label in ((str(selected), "CLI label"), (None, "Default label")):
            skills.sync(conn, skills.load_skills(path or default))
            with patch.object(cli, "_open", side_effect=lambda _args: (dict(config), database.connect(root / "test.db"))), \
                 patch.object(skills, "DEFAULT_PATH", default), \
                 patch.object(skills, "LOCAL_PATH", root / "skills.local.yaml"):
                result = cli.cmd_report(SimpleNamespace(skills=path))
            csv_path = next((root / "out").glob("*-scored.csv"))
            with csv_path.open(encoding="utf-8-sig", newline="") as handle:
                row = next(csv.DictReader(handle))
            markdown = next((root / "out").glob("*-top.md")).read_text(encoding="utf-8")
            check(f"报告使用 {label}，不读取 config 词表", result == 0
                  and row["tags"] == label and f"`{label}`" in markdown
                  and "Config label" not in markdown)
        conn.close()


def main() -> int:
    test_extract()
    test_term_matcher()
    test_search()
    test_filters_and_link()
    test_explanation_and_edit()
    test_tags_and_real_vocab()
    test_detail_editor_html()
    test_skill_tree_and_settings()
    test_remove_edit()
    test_incremental_sync()
    test_primary_display_names()
    test_cli_report_vocabulary()
    print()
    if failures:
        print(f"失败 {len(failures)} 项：")
        for f in failures:
            print(f"  - {f}")
        return 2
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
