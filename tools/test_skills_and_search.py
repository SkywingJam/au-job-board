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
    check("单术语解释带技能名与条数", "技术词「C」" in one and "67" in one, one)
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
        html = panel.render_settings(conn, {})
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
        check("编辑窗：主要展示名用拖拽，并标明暂未实现",
              "addEventListener('drop'" in html and "set.not_yet" in html)
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
