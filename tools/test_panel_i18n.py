"""面板界面语言层 + 新布局的回归（合成数据，不联网、不写真实库）。

覆盖：
  - 中英文字典键一致；前端脚本用到的每个 T('key') 都有译文且会注入
  - 语言 / 外观偏好的解析顺序：cookie > Accept-Language > 中文
  - <html lang> 与实际语言一致；英文页面的界面文案里没有中文
    （数据——公司、备注、技能名——照原样输出，不在检查范围内）
  - 备注：默认标签按界面语言显示译名、canonical 原值存储；自定义内容不翻译
  - 卡片固定三个圆点，未标注为空心
  - 每个视图的筛选摆放（常驻 + 更多筛选 + 搜索 + 排序）恰好等于允许的筛选

用法：
    .venv/bin/python tools/test_panel_i18n.py
"""

from __future__ import annotations

import datetime as dt
import os
import re
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jobs import db as database          # noqa: E402
from jobs import i18n, panel, panel_views, run_state  # noqa: E402
from jobs import labels as labels_mod    # noqa: E402

failures: list[str] = []
CJK = re.compile(r"[㐀-鿿]")


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{('  ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


def visible(html: str) -> str:
    """去掉 <script> / <style> 之后的标记 —— 嵌入的数据（备注候选等）在 script 里。"""
    html = re.sub(r"<script>.*?</script>", "", html, flags=re.S)
    return re.sub(r"<style>.*?</style>", "", html, flags=re.S)


def test_dictionary() -> None:
    print("== 字典 ==")
    zh, en = i18n.STRINGS["zh"], i18n.STRINGS["en"]
    check("中英文键完全一致", set(zh) == set(en),
          str(sorted(set(zh) ^ set(en))[:6]))
    holes = lambda text: sorted(re.findall(r"\{(\w+)\}", text))
    zh_js, en_js = i18n.js_strings("zh"), i18n.js_strings("en")
    check("同一个键的中英文占位符（{name}）一致，页面字典与注入前端的文案都是",
          not [k for k in zh if holes(zh[k]) != holes(en[k])]
          and set(zh_js) == set(en_js)
          and not [k for k in zh_js if holes(zh_js[k]) != holes(en_js[k])])
    check("没有空译文（js.note_gap 中文刻意为空：中文标签间不加空格）",
          all(v != "" for t in (zh, en) for k, v in t.items() if k != "js.note_gap"))
    check("英文译文里没有中文（「中文」切换按钮与备注标签示例除外）",
          not [k for k, v in en.items()
               if CJK.search(v.replace("中文", "").replace("优先投递", "").replace("；", ""))
               and not k.startswith("cue.") and k != "lang.switch"])
    js = (ROOT / "jobs/panel_assets/panel.js").read_text(encoding="utf-8")
    js += (ROOT / "jobs/panel_assets/settings.js").read_text(encoding="utf-8")
    used = set(re.findall(r"\bT\('([a-z_.]+)'", js))
    injected = set(i18n.js_strings("en"))
    missing = sorted(k for k in used if k not in injected and not k.endswith((".", "_")))
    check("前端用到的文案键都会注入 window.__I18N__", not missing, str(missing))
    check("动态资格来源文案也会注入中英文前端",
          all(i18n.js_strings(lang).get("qualification." + suffix) ==
              i18n.t(lang, "qualification." + suffix)
              for lang in ("zh", "en") for suffix in ("manual", "machine", "unknown")))
    check("标注枚举显示名走字典、存储值不变",
          i18n.value_label("en", "interest", "want") == "Want"
          and i18n.value_label("zh", "interest", "want") == "想投"
          and labels_mod.VALID["interest"] == ("want", "maybe", "no"))
    check("资格线索按内部值映射，认不得的原样显示",
          i18n.cue_label("en", "公民/PR") == "Citizen / PR"
          and i18n.cue_label("en", "未知线索") == "未知线索")


def test_preferences() -> None:
    print("\n== 语言 / 外观偏好 ==")
    check("没有任何信号 -> 中文", i18n.resolve_lang(None, None) == "zh")
    check("浏览器英文 -> 英文", i18n.resolve_lang(None, "en-AU,en;q=0.9") == "en")
    check("按 q 值取最优先的", i18n.resolve_lang(None, "en;q=0.2, zh-CN;q=0.8") == "zh")
    check("不认识的语言 -> 中文", i18n.resolve_lang(None, "fr-FR,fr") == "zh")
    check("cookie 优先于浏览器", i18n.resolve_lang("a=1; jobs_lang=zh", "en-US") == "zh")
    check("外观只认 light / dark", i18n.resolve_theme("jobs_theme=dark") == "dark"
          and i18n.resolve_theme("jobs_theme=pink") is None and i18n.resolve_theme(None) is None)
    check("设置页显示明确选过的语言，没选过是 auto",
          i18n.lang_pref("jobs_lang=en") == "en" and i18n.lang_pref("") == "auto")


def test_layout() -> None:
    print("\n== 筛选摆放 ==")
    for view, allowed in panel.VIEW_FILTER_KEYS.items():
        primary, groups = panel.filter_layout(view)
        placed = set(primary) | set(groups["label"]) | set(groups["job"])
        rest = set(allowed) - {"q", "sort", "interest"}
        check(f"{view}：常驻 + 更多筛选 = 可见筛选", placed == rest,
              f"{sorted(placed)} vs {sorted(rest)}")
        check(f"{view}：界面不展示旧兴趣筛选", "interest" not in placed)
    check("推荐视图没有「更多筛选」", not any(panel.filter_layout("recommend")[1].values()))
    check("已标注视图按地点、处理状态、优先待投、技能排列",
          panel.filter_layout("labelled")[0] == ("geo", "action", "priority", "skill"))


def test_render() -> None:
    print("\n== 渲染（合成数据）==")
    with tempfile.TemporaryDirectory() as d:
        conn = database.connect(os.path.join(d, "t.db"))
        today = dt.date.today().isoformat()
        # 合成占位岗位：公司 / URL 都是占位值（example.invalid 保留域名）
        for sid, title in (("a", "Synthetic Data Engineer"), ("b", "Synthetic Backend Developer")):
            database.upsert_job(conn, {
                "source": "seek", "source_id": sid, "title": title, "company": "Example Co",
                "location": "Melbourne VIC", "listing_date": today,
                "description": "Applicants must be Australian citizens. Python and AWS.",
                "url": f"https://example.invalid/job/synthetic_{sid}"})
            database.replace_decision(conn, f"seek:{sid}", 0)
            database.replace_score(conn, f"seek:{sid}", "T1", 3, 0, 0, 5, "[]", 5)
        conn.commit()
        labels_mod.set_label(conn, "seek:a", source="panel", eligibility="eligible", note="优先投递")
        conn.commit()

        en = panel.render_page(conn, {}, "all", 0, 40, None, {}, "en", "dark")
        zh = panel.render_page(conn, {}, "all", 0, 40, None, {}, "zh", None)
        check("英文页 <html lang=en>", '<html lang="en"' in en)
        check("中文页 <html lang=zh-CN>", '<html lang="zh-CN"' in zh)
        check("选了深色 -> data-theme=dark；没选 -> 跟随系统",
              'data-theme="dark"' in en and "data-theme=" not in zh.split("<head>")[0])
        # 切换语言的按钮用目标语言写（「中文」「切换到中文界面」），这是有意的
        shown = visible(en).replace(i18n.t("en", "lang.switch"), "").replace("中文", "")
        leftovers = sorted(set(CJK.findall(shown)))
        check("英文页界面文案里没有中文", not leftovers, "".join(leftovers[:20]))

        check("与分析一致的人工可投不在卡片上占位", 'class="tag corr"' not in en)

        row = panel._find(conn, {}, "seek:a")
        detail_en = panel._detail_html(row, "en")
        check("英文详情：处理步骤与常驻一句说明", "Decide" in detail_en and 'class="hint decide-hint"' in detail_en
              and 'id="help-panel"' not in detail_en)
        check("英文详情：星标是独立轻量开关", 'class="star-btn"' in detail_en and 'data-field="priority"' in detail_en)
        check("英文详情：资格纠错默认折叠且摘要给出来源",
              '<details class="qualification"' in detail_en and "<details class=\"qualification\" open" not in detail_en
              and "Eligible · manual correction" in detail_en)
        check("英文详情：默认备注标签显示英文译名", 'value="Prioritize;"' in detail_en)
        check("中文详情：默认备注标签保持原文",
              'value="优先投递；"' in panel._detail_html(row, "zh"))
        check("资格原文摘录原样输出、不翻译",
              "Applicants must be Australian citizens" in detail_en and 'lang="en"' in detail_en)
        check("标注按钮仍提交英文枚举",
              "setLabel('seek:a','eligibility','eligible'" in detail_en)

        settings_en = panel.render_settings(conn, {}, "en", None, "display", "en")
        check("设置页：语言与外观选项", "setLang('auto')" in settings_en
              and "setTheme('dark')" in settings_en and '<html lang="en"' in settings_en)
        labels_mod.set_label(conn, "seek:a", source="panel", action="skipped")
        conn.commit()
        skipped_page = panel.render_page(conn, {}, "all", 0, 40, None, {}, "en", "dark")
        skipped_detail = panel._detail_html(panel._find(conn, {}, "seek:a"), "en")
        check("跳过：卡片状态是中性色", 'class="prog mid"' in skipped_page)
        check("跳过：详情按钮中性选中样式，不再用红色", 'class="seg on mid"' in skipped_detail
              and 'class="seg on bad"' not in skipped_detail)
        check("跳过：状态行说明已处理", "Processed: Skipped" in skipped_detail)

        # v1.2.3 C: legacy database values stay compatible but no longer appear in the UI.
        database.replace_decision(conn, "seek:b", 1, "synthetic.rule")
        conn.commit()
        labels_mod.set_label(conn, "seek:b", source="cli", interest="maybe", note="legacy")
        conn.execute("UPDATE labels SET eligibility='unsure' WHERE uid='seek:b'")
        conn.commit()
        legacy = panel._detail_html(panel._find(conn, {}, "seek:b"), "zh")
        stored = conn.execute(
            "SELECT interest, eligibility FROM labels WHERE uid='seek:b'").fetchone()
        check("旧待定/旧兴趣仍原样保存在后端",
              tuple(stored) == ("maybe", "unsure"), str(tuple(stored)))
        check("旧待定/旧兴趣及历史块不再出现在详情",
              all(text not in legacy for text in
                  ("资格：待定", "兴趣：也许", "有旧记录", "旧版记录", 'class="history"', 'class="q-hist"'))
              and "'eligibility','unsure'" not in legacy and "'interest'," not in legacy)
        check("旧待定不算人工纠正：摘要按分析结果，排除说明给出纠正方式",
              'data-corrected="false"' in legacy and "不可投 · 分析结果" in legacy
              and i18n.t("zh", "qualification.machine_excluded") in legacy)
        check("只有备注/旧记录：仍是未处理", ">未处理</span>" in legacy)
        labels_mod.set_label(conn, "seek:b", source="panel", eligibility="eligible", priority=True)
        conn.commit()
        page = panel.render_page(conn, {}, "all", 0, 40, None, {}, "zh", None)
        card_b = page[page.index('data-uid="seek:b"'):]
        card_b = card_b[:card_b.find('<div class="gutter"')]
        check("与排除结果相反的人工可投在卡片上提示", '>人工判定：可投</span>' in card_b)
        check("星标在卡片上可见（未处理也可有星标）",
              re.search(r'class="card-star" data-star-uid="seek:b"[^>]*>★', card_b) is not None
              and re.search(r'class="prog "[^>]* hidden>', card_b) is not None)
        check("全部视图不把已处理卡片变淡", 'class="card done"' not in page)
        conn.close()


def test_settings_profile() -> None:
    print("\n== 设置页移除重复 Profile 摘要（合成库）==")
    now = dt.datetime.now(dt.timezone.utc).isoformat()

    def rec(profile):
        return {"rule_profile": {"profile": profile, "config_source": "x",
                                 "exclude": {"synthetic.rule": False}, "overrides": {}}}

    with tempfile.TemporaryDirectory() as d:
        conn = database.connect(os.path.join(d, "settings.db"))
        database.log_run(conn, "analyze", now, rec("485"))
        conn.commit()
        for lang in ("zh", "en"):
            for section in ("fetch", "display"):
                with patch.object(run_state, "generation", return_value="rendered-generation"):
                    html = panel.render_settings(conn, {}, lang, None, section, lang)
                check(f"{lang}/{section}：嵌入渲染时结果版本",
                      'window.__SETTINGS_GENERATION__="rendered-generation"' in html)
                content = visible(html)
                check(f"{lang}/{section}：没有重复的分析规则/Profile摘要",
                      i18n.t(lang, "set.profile_title") not in content
                      and i18n.t(lang, "set.profile_used") not in content
                      and i18n.t(lang, "set.profile_label", name="485") not in content)
                if section == "fetch":
                    check(f"{lang}：保留分区配置与更新入口",
                          'id="config-editor"' in html
                          and i18n.t(lang, "js.update_title") in content)
        conn.close()

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "p.db")
        conn = database.connect(path)
        database.log_run(conn, "analyze", now, rec("485"))
        conn.commit()
        conn.close()
        before = Path(path).read_bytes()
        conn = database.connect(path)
        for section in ("fetch", "display"):
            panel.render_settings(conn, {}, "zh", None, section, "zh")
        conn.close()
        check("渲染设置页不写库", Path(path).read_bytes() == before)

        conn = database.connect(path)
        database.upsert_job(conn, {
            "source": "seek", "source_id": "z", "title": "Synthetic Role", "company": "Example Co",
            "location": "Melbourne VIC", "listing_date": dt.date.today().isoformat(),
            "description": "Python.", "url": "https://example.invalid/job/synthetic_z"})
        database.replace_decision(conn, "seek:z", 0)
        database.replace_score(conn, "seek:z", "T1", 3, 0, 0, 5, "[]", 5)
        conn.commit()
        leaks = [v for v in panel.VIEW_FILTER_KEYS
                 if re.search(r"Profile|规则开关|Rule switches|最近一次分析使用",
                              visible(panel.render_page(conn, {}, v, 0, 40, None, {}, "zh", None))
                              + visible(panel.render_page(conn, {}, v, 0, 40, None, {}, "en", None)))]
        check("主页面各视图没有规则状态摘要", not leaks, str(leaks))
        conn.close()


def test_stale_fetch_warning() -> None:
    print("\n== 抓取过期提示 ==")
    now = dt.datetime.now(dt.timezone.utc)
    for lang in ("zh", "en"):
        for state in ("success", "partial", "error"):
            for age in (1, 3):
                fact = {"state": state, "finished_at": (now - dt.timedelta(days=age)).isoformat()}
                with patch.object(run_state, "latest", return_value=fact):
                    html = panel_views._last_run_note(None, lang, {})
                stale = age >= panel_views.STALE_AFTER_DAYS
                check(f"{lang}/{state}/{age}天：正确显示过期警告",
                      ('class="run stale"' in html) == stale
                      and ('class="dot bad"' in html) == stale
                      and (i18n.t(lang, "run.stale") in html) == stale)


def test_salary_tag_and_empty_jd() -> None:
    print("\n== 薪资 tag 后缀与空正文占位 ==")
    from jobs import panel_views
    tag = panel_views._tag_text
    check("英文：/年 -> /yr，（正文）-> (from description)，数字与 ≈ 不变",
          tag("salary", "≈A$55k–59k/年（正文）", "en") == "≈A$55k–59k/yr (from description)"
          and tag("salary", "A$75k–85k/年", "en") == "A$75k–85k/yr")
    check("中文：薪资 tag 原样", tag("salary", "A$75k–85k/年（正文）", "zh") == "A$75k–85k/年（正文）")
    check("英文：⚠ 前缀保留", tag("salary", "⚠ A$180k–200k/年", "en") == "⚠ A$180k–200k/yr")
    with tempfile.TemporaryDirectory() as d:
        conn = database.connect(os.path.join(d, "s.db"))
        database.upsert_job(conn, {
            "source": "seek", "source_id": "e", "title": "Synthetic Empty", "company": "Example Co",
            "location": "Melbourne VIC", "listing_date": dt.date.today().isoformat(),
            "description": "", "url": "https://example.invalid/job/synthetic_e"})
        database.replace_decision(conn, "seek:e", 0)
        database.replace_score(conn, "seek:e", "T1", 3, 0, 0, 5, "[]", 5)
        conn.commit()
        row = panel._find(conn, {}, "seek:e")
        en = panel._detail_html(row, "en")
        zh = panel._detail_html(row, "zh")
        check("英文详情：空正文占位是英文、没有中文",
              "No description was captured" in en and not CJK.findall(en.replace("优先投递", "")))
        check("中文详情：空正文占位保持原文", "这份 JD 没有抓到正文" in zh)
        conn.close()


def test_note_gap() -> None:
    print("\n== 英文标签边界空格（显示层）==")
    d = i18n.display_note
    check("默认 + 自定义 + emoji", d("en", "搬迁；follow up；🎉 great team；")
          == "Relocation; follow up; 🎉 great team;")
    check("结尾仍是单个 ;，没有结尾空格", d("en", "搬迁；").endswith("Relocation;"))
    check("已有空白不叠加", d("en", "A； B；") == "A; B;" and d("en", "A；  B；") == "A;  B;")
    check("不新增空标签", d("en", "A；；B；") == "A;; B;")
    check("词内空白、大小写、emoji 原样", d("en", "Ab  Cd；😀 x；") == "Ab  Cd; 😀 x;")
    check("中文不变", d("zh", "搬迁；follow up；") == "搬迁；follow up；"
          and d("zh", "A;B") == "A；B")
    check("js.note_sep 仍是单字符（结尾判断不受影响）",
          i18n.STRINGS["en"]["js.note_sep"] == ";" and i18n.STRINGS["en"]["js.note_gap"] == " "
          and i18n.STRINGS["zh"]["js.note_gap"] == "")


def test_note_separators() -> None:
    """备注分隔符：存储始终是全角 canonical，界面按语言显示，只替换分隔字符。"""
    print("\n== 备注分隔符（显示 vs 存储）==")
    check("中文显示分隔符是全角，且等于存储契约",
          i18n.note_sep("zh") == labels_mod.NOTE_SEP == "；")
    check("英文显示分隔符是半角", i18n.note_sep("en") == ";")
    check("中文显示把半角换成全角",
          i18n.display_note("zh", "Alpha;Beta") == "Alpha；Beta")
    check("英文显示把全角换成半角",
          i18n.display_note("en", "Alpha；Beta；") == "Alpha; Beta;")
    check("混合分隔符只替换、不合并、不删除",
          i18n.display_note("en", "Alpha；;Beta") == "Alpha;; Beta")
    check("半角 / 未收尾历史输入不清洗内容、不补尾（英文只补标签边界空格）",
          i18n.display_note("en", "Alpha;Beta") == "Alpha; Beta"
          and i18n.display_note("zh", "Alpha") == "Alpha"
          and i18n.display_note("zh", "Alpha  Beta") == "Alpha  Beta")
    check("空备注显示为空串", i18n.display_note("en", None) == "")
    check("前端能拿到当前语言的显示分隔符",
          i18n.js_strings("zh")["js.note_sep"] == "；"
          and i18n.js_strings("en")["js.note_sep"] == ";")
    check("英文提示说明分隔符（半角）",
          "semicolon (;)" in i18n.t("en", "detail.note_hint")
          and "；" not in i18n.t("en", "detail.note_hint"))
    check("中文提示文案与实际一致（全角）",
          "「；」" in i18n.t("zh", "detail.note_hint"))

    with tempfile.TemporaryDirectory() as d:
        conn = database.connect(os.path.join(d, "t.db"))
        today = dt.date.today().isoformat()
        database.upsert_job(conn, {
            "source": "seek", "source_id": "sep", "title": "Synthetic Separators",
            "company": "Example Co", "location": "Melbourne VIC", "listing_date": today,
            "description": "Synthetic description.",
            "url": "https://example.invalid/job/synthetic_sep"})
        database.replace_decision(conn, "seek:sep", 0)
        database.replace_score(conn, "seek:sep", "T1", 3, 0, 0, 5, "[]", 5)
        conn.commit()
        # 直接落库，绕开 set_label 规范化：模拟含半角分隔符、未收尾且带 HTML
        # 特殊字符的历史自由备注。只验证显示转换与转义，不改存储。
        conn.execute("INSERT INTO labels (uid, note) VALUES (?,?)",
                     ("seek:sep", "Alpha;Beta <b>&</b>"))
        conn.commit()
        row = panel._find(conn, {}, "seek:sep")
        zh = panel._detail_html(row, "zh")
        en = panel._detail_html(row, "en")
        check("中文详情：半角历史输入显示为全角，内容与顺序保真",
              'value="Alpha；Beta &lt;b&gt;&amp;&lt;/b&gt;"' in zh)
        check("英文详情：显示半角，内容与顺序保真",
              'value="Alpha; Beta &lt;b&gt;&amp;&lt;/b&gt;"' in en)
        check("详情输入框仍带 IME 与候选交互钩子",
              'oncompositionend="noteComposed(this)"' in zh
              and 'onkeydown="noteKeys(event,this)"' in zh)
        conn.close()


def test_note_tag_localization() -> None:
    """默认备注标签：canonical 存储、界面按语言显示译名；自定义不翻译。"""
    print("\n== 默认备注标签本地化 ==")
    defaults = list(labels_mod.NOTE_TAG_DEFAULTS)
    check("默认词表恰好 22 条", len(defaults) == 22, str(len(defaults)))
    check("译表覆盖全部默认标签、无多余项",
          set(i18n.NOTE_TAG_EN) == set(defaults),
          str(sorted(set(i18n.NOTE_TAG_EN) ^ set(defaults))))
    check("英文译名唯一", len(set(i18n.NOTE_TAG_EN.values())) == len(defaults))
    check("中文显示名就是 canonical",
          all(i18n.note_tag_display("zh", t) == t for t in defaults))
    check("英文显示名为译名",
          all(i18n.note_tag_display("en", t) == i18n.NOTE_TAG_EN[t] for t in defaults))
    check("自定义标签不翻译",
          i18n.note_tag_display("en", "My own note") == "My own note"
          and i18n.note_tag_display("en", "Prioritize") == "Prioritize")
    check("前端只拿英文映射；中文用 canonical",
          i18n.note_tag_labels("en") == i18n.NOTE_TAG_EN
          and i18n.note_tag_labels("zh") == {})
    check("只翻译完整默认标签，子串不动",
          i18n.display_note("en", "优先投递给某公司；") == "优先投递给某公司;"
          and i18n.display_note("en", "My Prioritize note；") == "My Prioritize note;")
    check("混合串：默认翻译、自定义与 emoji 保真",
          i18n.display_note("en", "优先投递；My custom note；😀；")
          == "Prioritize; My custom note; 😀;")
    check("中英显示各自闭合，canonical 不变",
          i18n.display_note("en", "优先投递；My custom note；") == "Prioritize; My custom note;"
          and i18n.display_note("zh", "优先投递；My custom note；") == "优先投递；My custom note；")
    check("展示不 trim / 不补尾",
          i18n.display_note("en", "优先投递；  未收尾") == "Prioritize;  未收尾")
    # v1.2.2 UI-4：备注提示只留操作说明；「默认标签按界面语言显示、自定义不翻译」
    # 这条边界改由设置 → 显示的「切换语言不会修改已保存的内容」说明。
    check("英文：显示设置说明默认本地化、自定义不翻译",
          "default tags display in the UI language" in i18n.t("en", "set.keep2")
          and "custom notes stay as written" in i18n.t("en", "set.keep2"))
    check("中文：显示设置同样说明该边界",
          "默认标签按界面语言显示" in i18n.t("zh", "set.keep2")
          and "自定义备注保持原样" in i18n.t("zh", "set.keep2"))
    check("备注提示不再暴露存储细节",
          not any(w in i18n.t(lang, "detail.note_hint")
                  for lang in ("zh", "en") for w in ("后台", "规范化", "stored", "normalized")))

    with tempfile.TemporaryDirectory() as d:
        conn = database.connect(os.path.join(d, "t.db"))
        today = dt.date.today().isoformat()
        database.upsert_job(conn, {
            "source": "seek", "source_id": "tag", "title": "Synthetic Tag Localization",
            "company": "Example Co", "location": "Melbourne VIC", "listing_date": today,
            "description": "Synthetic description.",
            "url": "https://example.invalid/job/synthetic_tag"})
        database.replace_decision(conn, "seek:tag", 0)
        database.replace_score(conn, "seek:tag", "T1", 3, 0, 0, 5, "[]", 5)
        conn.commit()
        labels_mod.set_label(conn, "seek:tag", source="panel",
                             note="优先投递；My custom note；😀；")
        conn.commit()
        row = panel._find(conn, {}, "seek:tag")
        en = panel._detail_html(row, "en")
        zh = panel._detail_html(row, "zh")
        check("英文详情：默认译名 + 自定义原文",
              'value="Prioritize; My custom note; 😀;"' in en)
        check("中文详情：canonical 原样",
              'value="优先投递；My custom note；😀；"' in zh)
        check("详情嵌入 canonical 供 JS 身份对齐",
              'data-note-stored="优先投递；My custom note；😀；"' in en)
        page = panel.render_page(conn, {}, "all", 0, 40, None, {}, "en", None)
        check("英文页面注入默认标签英文映射",
              '"搬迁": "Relocation"' in page and "__NOTE_TAG_LABELS__" in page)
        check("存储仍是 canonical 原值",
              labels_mod.note_tags(row["_label"]["note"])
              == ["优先投递", "My custom note", "😀"])
        conn.close()


def main() -> int:
    test_dictionary()
    test_preferences()
    test_layout()
    test_render()
    test_settings_profile()
    test_stale_fetch_warning()
    test_salary_tag_and_empty_jd()
    test_note_gap()
    test_note_separators()
    test_note_tag_localization()
    print()
    if failures:
        print(f"失败 {len(failures)} 项：" + "；".join(failures))
        return 2
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
