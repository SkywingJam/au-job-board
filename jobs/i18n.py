"""面板的界面语言层（中文 / English）。

**只管界面文案**：按钮、筛选、提示、空状态、错误反馈和页面 lang 声明。
以下内容的存储值不随语言切换而改变：

  - 岗位正文、公司名、地点、资格原文摘录
  - 用户备注、标注与投递记录；默认备注标签仅在显示层翻译，自定义备注原样显示
  - 技能规范名与别名（同时用于提取、搜索与编辑）
  - 数据库里的英文枚举（eligible / want / saved …）—— 显示名在这里，存储值不迁移
  - render.py 里的资格线索名（「公民/PR」等）是内部键，这里只做显示映射

语言选择：cookie `jobs_lang`（zh / en）> 浏览器 Accept-Language > 中文。
外观：cookie `jobs_theme`（light / dark），没有就跟随系统。
"""

from __future__ import annotations

import re

LANGS = ("zh", "en")
DEFAULT_LANG = "zh"
LANG_COOKIE = "jobs_lang"
THEME_COOKIE = "jobs_theme"
THEMES = ("light", "dark")
HTML_LANG = {"zh": "zh-CN", "en": "en"}

STRINGS: dict[str, dict[str, str]] = {
    "zh": {
        # 页面与顶栏
        "app.title": "澳洲岗位面板",
        "settings.title": "设置",
        "nav.views": "视图",
        "nav.menu": "打开菜单",
        "nav.menu_close": "关闭菜单",
        "nav.settings": "设置",
        "nav.back": "返回面板",
        "lang.current": "中文",
        "lang.other": "EN",
        "lang.switch": "Switch to English",
        "theme.to_dark": "切换到深色模式",
        "theme.to_light": "切换到浅色模式",
        # 视图
        "view.recommend": "推荐",
        "view.all": "全部",
        "view.new": "新增",
        "view.excluded": "被排除",
        "view.labelled": "已标注",
        "viewhint.recommend": "未标注，分数优先",
        "viewhint.all": "全库，含被排除的",
        "viewhint.new": "最近一次抓取",
        "viewhint.excluded": "按规则分组",
        "viewhint.labelled": "你标过的",
        "viewdesc.recommend": "还没标注的岗位，分数高的在前；每 10 条混入 1 条低分岗位做校准。挂出超过 30 天的不进这里。",
        "viewdesc.all": "去重后的全部岗位，也包括被规则排除的（卡片上标「已排除」）。",
        "viewdesc.new": "最近一次抓取新增的岗位，包括被排除的。",
        "viewdesc.excluded": "被硬规则排除的岗位，按规则分组。怀疑排错了（漏杀 / 误杀），就在这里找。",
        "viewdesc.labelled": "你标注过的岗位，包括被排除的。勾上「只看优先投递」就是待投队列。",
        # 统计
        "stat.labelled": "已标注",
        "stat.saved": "待投",
        "stat.applied": "已投递",
        "stat.miss": "漏杀候选",
        "stat.false_excl": "误杀候选",
        "stat.batch": "本批",
        "stat.rules": "条被排除",
        # 搜索与筛选
        "search.placeholder": "搜索标题、公司、地点、备注、tag、正文…",
        "search.label": "搜索",
        "search.explain": "搜索：<b>{query}</b> → {terms}{all}，共 {total} 条",
        "search.all_match": "（全部满足）",
        "term.skill": "技术词「{label}」",
        "term.prefix": "前缀「{label}」",
        "term.word": "整词「{label}」",
        "filter.more": "更多筛选",
        "filter.button": "筛选",
        "filter.done": "完成",
        "filter.clear_hidden": "清除这些筛选",
        "filter.clear_all": "清除全部筛选",
        "filter.active": "已启用：",
        "filter.remove": "移除筛选",
        "filter.group.label": "标注",
        "filter.group.label_hint": "按你的判断筛",
        "filter.group.job": "岗位条件",
        "filter.group.job_hint": "按岗位本身筛",
        "filter.any": "不限",
        "filter.none": "未标这一项",
        "filter.salary": "只看标了薪资的",
        "filter.salary_chip": "有薪资",
        "filter.fresh": "只看 30 天内的",
        "filter.priority": "只看优先投递",
        "filter.skill": "技能",
        "filter.skill_hint": "任一命中",
        "filter.tier": "层级",
        "filter.sort": "排序",
        "filter.labeled": "标注状态",
        "sort.score": "分数",
        "sort.date": "发布时间",
        "labeled.any": "全部",
        "labeled.no": "未标注",
        "labeled.yes": "已标注",
        # 列表
        "list.label": "岗位列表",
        "list.empty": "这个视图是空的",
        "list.empty_filtered": "没有符合条件的岗位",
        "list.empty_hint": "试试清除筛选，或去「全部」看看。",
        "list.prev": "← 上一页",
        "list.next": "下一页 →",
        "list.page": "第 {page} / {pages} 页",
        "list.count": "本页 {n} / {total}",
        "card.excluded": "已排除",
        "card.rule": "规则 {rule}",
        "card.score": "总分：越高越匹配",
        "age.tag": "挂出 {days} 天",
        "tag.raw": "原文 {text}",
        "tag.experience": "经验 {years}+ 年 {delta}",
        "tag.salary_year": "/年",
        "tag.salary_from_text": "（正文）",
        # 上次抓取
        "run.never": "尚未跑过完整流程",
        "run.at": "上次抓取 {stamp}",
        "run.ago": "上次抓取 {ago}",
        "run.stale": "　⚠ 定时任务可能已停",
        # 规则开关（最近一次成功 analyze 使用的 profile / 开关）
        "rule.title": "规则开关",
        "rule.used": "最近一次分析使用",
        "rule.unavailable": "旧分析未记录 profile",
        "rule.profile": "profile",
        "rule.source": "来源",
        "rule.disabled": "已关闭",
        "rule.explicit": "显式覆盖",
        "rule.none": "无",
        "rule.default_profile": "缺省（全部规则开启）",
        "rule.note": "软件规则开关，非法律资格判断",
        "ago.minutes": "{n} 分钟前",
        "ago.hours": "{n} 小时前",
        "ago.days": "{n} 天前",
        # 详情
        "detail.label": "岗位详情",
        "detail.placeholder": "← 从左侧选一条岗位，正文会显示在这里",
        "detail.back": "← 返回列表",
        "detail.open": "打开原文",
        "detail.posted": "发布 {date}",
        "detail.dupes": "另有 {n} 条重复（{sources}）",
        "detail.rule": "被规则排除：",
        "detail.matched": "命中：",
        "detail.step1": "先看资格线索",
        "detail.original": "原文摘录 · 不翻译",
        "detail.evidence_note": "这只是提取到的局部要求，不代表整份岗位已确认可投。",
        "detail.step2": "做标记",
        "detail.step3": "写备注",
        "detail.optional": "可选",
        "detail.decide": "判断与标注",
        "detail.jd": "岗位正文",
        "detail.jd_note": "原文",
        "detail.jd_empty": "（这份 JD 没有抓到正文）",
        "detail.skill_edit": "编辑技能",
        "detail.skill_head": "技能词表",
        "detail.skill_placeholder": "输入一个词：回车新增，或从候选选一个作为别名",
        "detail.skill_hint": "点候选 = 把该词设为已有技能的别名；回车 = 新增技能。改动会立即重算全库技能并刷新",
        "detail.note_placeholder": "备注…（点一下出候选，可滚动）",
        "detail.note_hint": "点候选即追加；默认标签按界面语言显示、后台存中文原值，自己写的内容不翻译。用「；」分隔标签，回车保存；备注只在保存时规范化分隔符。",
        "help.button": "这三个标记有什么区别？",
        "help.eligibility": "按资格门槛判断你能不能投，例如公民 / PR、保密许可、签证。只记事实判断，不代表想不想投。",
        "help.interest": "不管能不能投，你想不想要这份工作。",
        "help.action": "实际做到哪一步：待投、已投或跳过。",
        "help.foot": "三个标记互相独立，比如「可投 + 不投」也是正常组合。再点一次已选中的按钮可以清除。列表卡片右下角的三个圆点按同样顺序显示这三项，空心表示还没标。",
        # 标注三轴
        "field.eligibility": "可投性",
        "field.interest": "兴趣",
        "field.action": "投递进度",
        "field.note": "备注",
        "value.eligibility.eligible": "可投",
        "value.eligibility.ineligible": "不可投",
        "value.eligibility.unsure": "待定",
        "value.interest.want": "想投",
        "value.interest.maybe": "也许",
        "value.interest.no": "不投",
        "value.action.saved": "待投",
        "value.action.applied": "已投",
        "value.action.skipped": "跳过",
        "dots.unset": "未标注",
        # 资格线索（render.ELIGIBILITY_CUES 的显示名；键是内部值）
        "cue.保密等级": "保密等级",
        "cue.保密审查": "保密审查",
        "cue.公民/PR": "公民/PR",
        "cue.工作权": "工作权",
        "cue.警察检查": "警察检查",
        "cue.原住民专属": "原住民专属",
        "cue.驾照": "驾照",
        # 设置页
        "set.sections": "设置分类",
        "set.skills": "技术栈",
        "set.display": "语言与显示",
        "set.skills_title": "技术栈词表",
        "set.skills_hint": "技能是筛选维度，不进打分。点铅笔编辑别名与展示名。改动只写入 skills.local.yaml 补充层，手写的 skills.yaml 保持只读。",
        "set.add_placeholder": "新增技能，如 Splunk",
        "set.add": "新增",
        "set.filter_placeholder": "搜索技能或别名…",
        "set.legend": "颜色：",
        "set.legend_hint": "鼠标停在名称或别名上可看来源说明",
        "set.col_name": "技能",
        "set.col_alias": "别名",
        "set.col_hits": "命中",
        "set.col_source": "来源",
        "set.seed": "种子",
        "set.local": "本地",
        "set.tip_seed": "种子关键词：来自手写的 skills.yaml，随仓库共享；界面不会改动这个文件。",
        "set.tip_local": "本地关键词：在面板或 CLI 里添加，记在 skills.local.yaml 补充层。",
        "set.tip_mixed": "种子 + 本地：条目来自 skills.yaml，并在 skills.local.yaml 里做过修改（新增或移除了别名）。",
        "set.flag_ambiguous": "歧义词：需要同篇有其他技术词佐证",
        "set.flag_not": "带排除规则（not）",
        "set.edit": "编辑",
        "set.no_match": "没有匹配的技能",
        "set.empty": "词表是空的",
        "set.no_alias": "没有别名",
        "set.footnote": "技能名与别名同时用于提取、搜索和展示，不随界面语言翻译。",
        "set.edit_title": "编辑技能",
        "set.close": "关闭",
        "set.primary": "主要展示名",
        "set.not_yet": "功能暂未实现",
        "set.primary_now": "卡片和筛选里显示这个名字；匹配仍使用全部名称",
        "set.drop_here": "松手，设为主要展示名",
        "set.reset_to": "恢复为 {name}",
        "set.guide1": "按住下方任一名称",
        "set.guide2": "拖进上面的虚线框",
        "set.guide3": "松手即替换",
        "set.guide_note": "原展示名会回到下方列表，不会被删除。触屏或键盘：直接点一下名称也能设为展示名。",
        "set.primary_unsaved": "主要展示名暂未实现，这次没有保存",
        "set.others": "其他名称",
        "set.drag_tip": "可拖到上方设为主要展示名",
        "set.set_primary": "设为主要展示名",
        "set.remove_alias": "删除别名",
        "set.alias_placeholder": "新别名，回车保存",
        "set.alias_hint": "移除种子别名会记为本地「抑制」，不会改动 skills.yaml。",
        "set.delete_skill": "删除技能（连同别名）",
        "set.confirm_delete": "确认删除",
        "set.done": "完成",
        "set.lang_title": "界面语言",
        "set.lang_desc": "只影响按钮、筛选、提示等界面文案，以及页面的 lang 声明。",
        "set.lang_auto": "跟随浏览器",
        "set.lang_auto_desc": "按浏览器语言，默认中文",
        "set.lang_zh_desc": "简体中文界面",
        "set.lang_en_desc": "英文界面",
        "set.theme_title": "外观",
        "set.theme_auto": "跟随系统",
        "set.theme_light": "浅色",
        "set.theme_dark": "深色",
        "set.profile_title": "分析规则",
        "set.profile_label": "Profile：{name}",
        "set.profile_used": "最近一次分析使用",
        "set.profile_citizen": "公民",
        "set.profile_permanent_resident": "永久居民",
        "set.profile_485": "485",
        "set.profile_student_visa": "学生",
        "set.profile_custom": "自定义",
        "set.profile_default": "默认 / 全部规则开启",
        "set.profile_unrecorded": "未记录 / 重新分析后显示",
        "set.profile_unknown": "未知",
        "set.keep_title": "切换语言不会修改已保存的内容",
        "set.keep1": "岗位正文、公司名、地点与资格原文摘录",
        "set.keep2": "你的备注、标注与投递记录（自定义备注保持原样，默认标签按界面语言显示）",
        "set.keep3": "技能名称、别名与数据库里的枚举值",
        # 前端脚本用到的反馈（window.__I18N__）
        "js.loading": "载入中…",
        "js.load_failed": "载入失败：",
        "js.label_failed": "标注失败: ",
        "js.labelled": "已标注",
        "js.unlabelled": "已取消",
        "js.miss_hint": "已标为不可投 — 若它未被规则排除，这就是漏杀",
        "js.note_saved": "备注已保存",
        "js.note_failed": "备注保存失败: ",
        "js.note_no_match": "无匹配候选 — 回车即保存为自定义标签",
        "js.note_no_more": "没有更多候选，直接输入即可",
        "js.note_count": "共 {n} 项 — 可滚动，或继续输入筛选",
        "js.skill_add": "+ 新增技能「{token}」",
        "js.skill_as_alias": "作为「{name}」的别名",
        "js.skill_empty": "输入一个词，回车即加入词表",
        "js.skill_saving": "正在保存…",
        "js.skill_added": "已加入技能：{token}",
        "js.skill_aliased": "「{token}」→「{name}」别名",
        "js.skill_recalc": "已保存，正在重算全库…",
        "js.skill_failed": "技能词表保存失败: ",
        "js.saved": "已保存",
        "js.deleted": "已删除",
        "js.save_failed": "保存失败: ",
        "js.dots_sep": "　",
        "js.colon": "：",
        "js.note_sep": "；",
        "js.note_gap": "",
    },
    "en": {
        "app.title": "AU Job Board",
        "settings.title": "Settings",
        "nav.views": "Views",
        "nav.menu": "Open menu",
        "nav.menu_close": "Close menu",
        "nav.settings": "Settings",
        "nav.back": "Back to board",
        "lang.current": "English",
        "lang.other": "中文",
        "lang.switch": "切换到中文界面",
        "theme.to_dark": "Switch to dark mode",
        "theme.to_light": "Switch to light mode",
        "view.recommend": "For you",
        "view.all": "All",
        "view.new": "New",
        "view.excluded": "Excluded",
        "view.labelled": "Labelled",
        "viewhint.recommend": "Unlabelled, best score first",
        "viewhint.all": "Everything, incl. excluded",
        "viewhint.new": "Latest fetch",
        "viewhint.excluded": "Grouped by rule",
        "viewhint.labelled": "Jobs you labelled",
        "viewdesc.recommend": "Jobs you haven’t labelled, highest score first. One low-score job in every 10 is mixed in for calibration. Listings older than 30 days are left out.",
        "viewdesc.all": "Every job after de-duplication, including ones excluded by rules (marked “Excluded”).",
        "viewdesc.new": "Jobs added by the most recent fetch, including excluded ones.",
        "viewdesc.excluded": "Jobs blocked by hard rules, grouped by rule. Look here for missed or wrong exclusions.",
        "viewdesc.labelled": "Jobs you’ve labelled, including excluded ones. Turn on “Priority only” for your apply queue.",
        "stat.labelled": "Labelled",
        "stat.saved": "To apply",
        "stat.applied": "Applied",
        "stat.miss": "Possible misses",
        "stat.false_excl": "Possible false excludes",
        "stat.batch": "In this batch",
        "stat.rules": "excluded",
        "search.placeholder": "Search title, company, location, notes, tags, description…",
        "search.label": "Search",
        "search.explain": "Search: <b>{query}</b> → {terms}{all}, {total} results",
        "search.all_match": " (all must match)",
        "term.skill": "skill “{label}”",
        "term.prefix": "prefix “{label}”",
        "term.word": "whole word “{label}”",
        "filter.more": "More filters",
        "filter.button": "Filters",
        "filter.done": "Done",
        "filter.clear_hidden": "Clear these filters",
        "filter.clear_all": "Clear all filters",
        "filter.active": "Active:",
        "filter.remove": "Remove filter",
        "filter.group.label": "Labels",
        "filter.group.label_hint": "by your judgement",
        "filter.group.job": "Job details",
        "filter.group.job_hint": "by the listing",
        "filter.any": "Any",
        "filter.none": "Not set",
        "filter.salary": "Salary stated only",
        "filter.salary_chip": "Has salary",
        "filter.fresh": "Last 30 days only",
        "filter.priority": "Priority only",
        "filter.skill": "Skills",
        "filter.skill_hint": "match any",
        "filter.tier": "Tier",
        "filter.sort": "Sort",
        "filter.labeled": "Label status",
        "sort.score": "Score",
        "sort.date": "Date posted",
        "labeled.any": "All",
        "labeled.no": "Unlabelled",
        "labeled.yes": "Labelled",
        "list.label": "Job list",
        "list.empty": "This view is empty",
        "list.empty_filtered": "No jobs match these filters",
        "list.empty_hint": "Try clearing filters, or check All.",
        "list.prev": "← Previous",
        "list.next": "Next →",
        "list.page": "Page {page} of {pages}",
        "list.count": "{n} of {total} on this page",
        "card.excluded": "Excluded",
        "card.rule": "Rule {rule}",
        "card.score": "Total score: higher is a better match",
        "age.tag": "Listed {days} days",
        "tag.raw": "Posted as {text}",
        "tag.experience": "Experience {years}+ yrs {delta}",
        "tag.salary_year": "/yr",
        "tag.salary_from_text": " (from description)",
        "run.never": "Full pipeline has not run yet",
        "run.at": "Last fetched {stamp}",
        "run.ago": "Last fetched {ago}",
        "run.stale": " ⚠ scheduled job may have stopped",
        # Rule switches (profile / switches used by the last successful analyze)
        "rule.title": "Rule switches",
        "rule.used": "Used by the last analyze",
        "rule.unavailable": "Old analyze did not record a profile",
        "rule.profile": "profile",
        "rule.source": "source",
        "rule.disabled": "disabled",
        "rule.explicit": "explicit",
        "rule.none": "none",
        "rule.default_profile": "default (all rules on)",
        "rule.note": "Software rule switch, not a legal eligibility determination",
        "ago.minutes": "{n} min ago",
        "ago.hours": "{n} h ago",
        "ago.days": "{n} days ago",
        "detail.label": "Job details",
        "detail.placeholder": "← Pick a job on the left to read it here",
        "detail.back": "← Back to list",
        "detail.open": "Open listing",
        "detail.posted": "Posted {date}",
        "detail.dupes": "{n} duplicates ({sources})",
        "detail.rule": "Excluded by rule: ",
        "detail.matched": "Matched: ",
        "detail.step1": "Check the eligibility cue",
        "detail.original": "Original excerpt · not translated",
        "detail.evidence_note": "This is one extracted requirement. It does not confirm you are eligible for the whole role.",
        "detail.step2": "Label it",
        "detail.step3": "Add a note",
        "detail.optional": "optional",
        "detail.decide": "Review and label",
        "detail.jd": "Job description",
        "detail.jd_note": "original text",
        "detail.jd_empty": "(No description was captured for this posting)",
        "detail.skill_edit": "Edit skills",
        "detail.skill_head": "Skill vocabulary",
        "detail.skill_placeholder": "Type a term: Enter to add, or pick a skill to add it as an alias",
        "detail.skill_hint": "Pick a skill = add the term as its alias; Enter = add a new skill. Skill tags are recomputed right away",
        "detail.note_placeholder": "Notes… (click for suggestions, scrollable)",
        "detail.note_hint": "Pick a suggestion to append it; default tags are shown in the UI language but stored as their original Chinese values, and your own text is never translated. Use a semicolon (;) to separate tags. Notes are normalized only when you save.",
        "help.button": "How are these three labels different?",
        "help.eligibility": "Whether the role’s requirements let you apply, such as citizenship / PR, clearance or visa. A factual call, not whether you want it.",
        "help.interest": "Whether you want this job, regardless of eligibility.",
        "help.action": "Where you are with it: saved, applied or skipped.",
        "help.foot": "The three labels are independent: “Eligible + No” is a normal combination. Click a selected button again to clear it. The three dots on each card show these labels in the same order; hollow means not set.",
        "field.eligibility": "Eligibility",
        "field.interest": "Interest",
        "field.action": "Progress",
        "field.note": "Notes",
        "value.eligibility.eligible": "Eligible",
        "value.eligibility.ineligible": "Ineligible",
        "value.eligibility.unsure": "Unsure",
        "value.interest.want": "Want",
        "value.interest.maybe": "Maybe",
        "value.interest.no": "No",
        "value.action.saved": "Saved",
        "value.action.applied": "Applied",
        "value.action.skipped": "Skipped",
        "dots.unset": "not set",
        "cue.保密等级": "Clearance level",
        "cue.保密审查": "Security clearance",
        "cue.公民/PR": "Citizen / PR",
        "cue.工作权": "Work rights",
        "cue.警察检查": "Police check",
        "cue.原住民专属": "Identified position",
        "cue.驾照": "Driver’s licence",
        "set.sections": "Settings sections",
        "set.skills": "Skills",
        "set.display": "Language & display",
        "set.skills_title": "Skill vocabulary",
        "set.skills_hint": "Skills are a filter, not part of the score. Use the pencil to edit aliases and the display name. Changes go only to the skills.local.yaml overlay; the hand-written skills.yaml stays read-only.",
        "set.add_placeholder": "New skill, e.g. Splunk",
        "set.add": "Add",
        "set.filter_placeholder": "Search skills or aliases…",
        "set.legend": "Colours:",
        "set.legend_hint": "Hover a name or alias to see where it comes from",
        "set.col_name": "Skill",
        "set.col_alias": "Aliases",
        "set.col_hits": "Hits",
        "set.col_source": "Source",
        "set.seed": "Seed",
        "set.local": "Local",
        "set.tip_seed": "Seed term: from the hand-written skills.yaml, shared with the repo. The UI never edits that file.",
        "set.tip_local": "Local term: added in the panel or CLI, stored in the skills.local.yaml overlay.",
        "set.tip_mixed": "Seed + local: the entry comes from skills.yaml and has local changes in skills.local.yaml (aliases added or removed).",
        "set.flag_ambiguous": "Ambiguous: needs another skill term in the same listing",
        "set.flag_not": "Has exclusion patterns (not)",
        "set.edit": "Edit",
        "set.no_match": "No matching skills",
        "set.empty": "The vocabulary is empty",
        "set.no_alias": "No aliases",
        "set.footnote": "Skill names and aliases drive extraction, search and display, so they are not translated with the UI.",
        "set.edit_title": "Edit skill",
        "set.close": "Close",
        "set.primary": "Display name",
        "set.not_yet": "Not implemented yet",
        "set.primary_now": "Shown on cards and filters; matching still uses every name",
        "set.drop_here": "Drop to make this the display name",
        "set.reset_to": "Reset to {name}",
        "set.guide1": "Press and hold a name below",
        "set.guide2": "Drag it into the dashed box",
        "set.guide3": "Release to replace",
        "set.guide_note": "The previous display name moves back to the list below; nothing is deleted. On touch or keyboard, tap a name to set it.",
        "set.primary_unsaved": "Display name isn’t implemented yet, so it wasn’t saved",
        "set.others": "Other names",
        "set.drag_tip": "Drag up to make it the display name",
        "set.set_primary": "Set as display name",
        "set.remove_alias": "Remove alias",
        "set.alias_placeholder": "New alias, Enter to save",
        "set.alias_hint": "Removing a seed alias is recorded as a local suppression; skills.yaml is not edited.",
        "set.delete_skill": "Delete skill and aliases",
        "set.confirm_delete": "Confirm delete",
        "set.done": "Done",
        "set.lang_title": "Interface language",
        "set.lang_desc": "Affects buttons, filters, hints and the page lang attribute only.",
        "set.lang_auto": "Match browser",
        "set.lang_auto_desc": "Uses the browser language, Chinese by default",
        "set.lang_zh_desc": "Simplified Chinese UI",
        "set.lang_en_desc": "English UI",
        "set.theme_title": "Appearance",
        "set.theme_auto": "Match system",
        "set.theme_light": "Light",
        "set.theme_dark": "Dark",
        "set.profile_title": "Analysis rules",
        "set.profile_label": "Profile: {name}",
        "set.profile_used": "Used by the last analyze",
        "set.profile_citizen": "Citizen",
        "set.profile_permanent_resident": "PR",
        "set.profile_485": "485",
        "set.profile_student_visa": "Student",
        "set.profile_custom": "Custom",
        "set.profile_default": "Default / all rules on",
        "set.profile_unrecorded": "Not recorded / shown after re-analysis",
        "set.profile_unknown": "Unknown",
        "set.keep_title": "Changing language does not modify your saved data",
        "set.keep1": "Job descriptions, company names, locations and eligibility excerpts",
        "set.keep2": "Your notes, labels and application history (custom notes stay as written; default tags display in the UI language)",
        "set.keep3": "Skill names, aliases and stored enum values",
        "js.loading": "Loading…",
        "js.load_failed": "Couldn’t load: ",
        "js.label_failed": "Couldn’t save label: ",
        "js.labelled": "Saved",
        "js.unlabelled": "Cleared",
        "js.miss_hint": "Marked ineligible — if rules didn’t exclude it, that’s a miss",
        "js.note_saved": "Note saved",
        "js.note_failed": "Couldn’t save note: ",
        "js.note_no_match": "No suggestions — press Enter to save as a custom tag",
        "js.note_no_more": "No more suggestions; just type",
        "js.note_count": "{n} items — scroll, or keep typing to filter",
        "js.skill_add": "+ Add skill “{token}”",
        "js.skill_as_alias": "As an alias of “{name}”",
        "js.skill_empty": "Type a term and press Enter to add it",
        "js.skill_saving": "Saving…",
        "js.skill_added": "Added skill: {token}",
        "js.skill_aliased": "“{token}” → alias of “{name}”",
        "js.skill_recalc": "Saved, recomputing…",
        "js.skill_failed": "Couldn’t save vocabulary: ",
        "js.saved": "Saved",
        "js.deleted": "Deleted",
        "js.save_failed": "Couldn’t save: ",
        "js.dots_sep": " · ",
        "js.colon": ": ",
        "js.note_sep": ";",
        "js.note_gap": " ",
    },
}


def norm_lang(value: str | None) -> str | None:
    value = (value or "").strip().lower()
    if value.startswith("zh"):
        return "zh"
    if value.startswith("en"):
        return "en"
    return None


def parse_cookies(header: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (header or "").split(";"):
        if "=" in part:
            key, _, value = part.strip().partition("=")
            out[key.strip()] = value.strip()
    return out


def resolve_lang(cookie_header: str | None = None, accept_language: str | None = None) -> str:
    """cookie > Accept-Language（按 q 值取第一个认得的）> 中文。"""
    chosen = norm_lang(parse_cookies(cookie_header).get(LANG_COOKIE))
    if chosen:
        return chosen
    ranked = []
    for i, item in enumerate((accept_language or "").split(",")):
        tag, _, params = item.strip().partition(";")
        q = 1.0
        if params.strip().startswith("q="):
            try:
                q = float(params.strip()[2:])
            except ValueError:
                q = 0.0
        ranked.append((-q, i, tag))
    for _q, _i, tag in sorted(ranked):
        if (lang := norm_lang(tag)):
            return lang
    return DEFAULT_LANG


def resolve_theme(cookie_header: str | None = None) -> str | None:
    """明确选过浅色/深色才返回；None = 跟随系统。"""
    value = parse_cookies(cookie_header).get(THEME_COOKIE)
    return value if value in THEMES else None


def lang_pref(cookie_header: str | None = None) -> str:
    """设置页显示用：用户明确选过的语言，没选过是 auto。"""
    return norm_lang(parse_cookies(cookie_header).get(LANG_COOKIE)) or "auto"


def t(lang: str, key: str, **kwargs) -> str:
    table = STRINGS.get(lang) or STRINGS[DEFAULT_LANG]
    text = table.get(key)
    if text is None:
        text = STRINGS[DEFAULT_LANG].get(key, key)
    return text.format(**kwargs) if kwargs else text


def value_label(lang: str, field: str, value: str | None) -> str:
    """标注枚举的显示名。存储值不变，未知值原样显示。"""
    if not value:
        return ""
    key = f"value.{field}.{value}"
    return t(lang, key) if key in STRINGS[DEFAULT_LANG] else str(value)


def cue_label(lang: str, cue: str | None) -> str:
    """资格线索的显示名。cue 是 render.py 的内部值，认不得就原样显示。"""
    if not cue:
        return ""
    key = f"cue.{cue}"
    return t(lang, key) if key in STRINGS[DEFAULT_LANG] else str(cue)


# 备注分隔符。存储契约固定为全角「；」（jobs/labels.py NOTE_SEP）；面板只在
# **显示边界**按当前语言替换：中文「；」，英文「;」。不改存储、不做格式清洗。
NOTE_SEP_STORE = "；"

# 默认备注标签的英文显示名。canonical 值在 labels.NOTE_TAG_DEFAULTS；这里只做
# 显示映射，是 Python 与前端共用的**唯一数据来源**（经 __NOTE_TAG_LABELS__ 注入）。
NOTE_TAG_EN = {
    "搬迁": "Relocation",
    "地区偏远": "Remote location",
    "通勤远": "Long commute",
    "描述模糊": "Unclear description",
    "技术栈模糊": "Unclear tech stack",
    "技术栈不匹配": "Tech stack mismatch",
    "信息量低": "Limited information",
    "中介广告": "Agency listing",
    "非IT岗位": "Non-IT role",
    "本质IT Support（T2）": "Primarily IT Support (T2)",
    "资历略高": "Slightly above experience level",
    "经验要求偏高": "High experience requirement",
    "优先投递": "Prioritize",
    "之前已投": "Previously applied",
    "重复挂出": "Reposted listing",
    "需驾照": "Driver’s licence required",
    "需保密审查": "Security clearance required",
    "需公民或PR": "Citizenship or PR required",
    "时间冲突": "Schedule conflict",
    "资格句误判": "Eligibility requirement misclassified",
    "大厂": "Large company",
    "技术栈匹配": "Tech stack match",
}


def note_sep(lang: str) -> str:
    """当前语言的备注显示分隔符。"""
    return ";" if lang == "en" else NOTE_SEP_STORE


def note_gap(lang: str) -> str:
    """相邻完整标签之间、分隔符后的显示间隔：英文一个空格，中文无。

    只影响显示：结尾的分隔符后不补空格，已有空白不重复叠加，不产生空标签；
    存储与保存仍是 canonical「标签；标签；」。
    """
    return STRINGS[lang]["js.note_gap"] if lang in STRINGS else ""


def note_tag_display(lang: str, tag: str | None) -> str:
    """默认标签的显示名：中文原值、英文译名，认不得的自定义内容原样。"""
    if not tag:
        return tag or ""
    return NOTE_TAG_EN.get(tag, tag) if lang == "en" else tag


def note_tag_labels(lang: str) -> dict[str, str]:
    """注入前端的 canonical -> 当前语言显示名映射（只含默认标签）。"""
    return dict(NOTE_TAG_EN) if lang == "en" else {}


def display_note(lang: str, value: str | None) -> str:
    """把存储的备注转成当前语言的显示形态。

    **只动完整标签与分隔符**：按「；/;」切出的完整片段若是默认 canonical，换成
    当前语言显示名；其余片段与空白原样保留。不做全文/子串替换，不 trim、不折叠、
    不补尾 —— 真正的格式化只在用户显式保存时（jobs.labels.normalize_note）。
    """
    if not value:
        return ""
    sep = note_sep(lang)
    parts = re.split(r"([；;])", str(value))
    for i in range(0, len(parts), 2):
        seg = parts[i]
        tag = seg.strip()
        if not tag:
            continue
        display = note_tag_display(lang, tag)
        if display != tag and tag in seg:
            parts[i] = seg.replace(tag, display, 1)
    gap = note_gap(lang)
    for i in range(1, len(parts), 2):
        nxt = parts[i + 1] if i + 1 < len(parts) else ""
        parts[i] = sep + (gap if gap and nxt and not nxt[0].isspace() else "")
    return "".join(parts)


def js_strings(lang: str) -> dict[str, str]:
    """前端脚本用的那部分文案（去掉 js. 前缀），注入为 window.__I18N__。"""
    keys = [k for k in STRINGS[DEFAULT_LANG] if k.startswith(("js.", "set.", "field.", "value.", "dots.", "theme.", "filter.", "lang."))]
    return {k: t(lang, k) for k in keys}
