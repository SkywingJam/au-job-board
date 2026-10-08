#!/usr/bin/env node
/* 备注分隔符与默认标签本地化的合成 JS 行为检查
（Node 标准库 + 最小 DOM stub，无第三方依赖）。

覆盖：
  - UI-02：中文生成/展示全角「；」，英文生成/展示半角「;」，两种/混合输入
  - 存储契约仍是后台 canonical 全角；显示与存储分开
  - 末段过滤、按 canonical 身份去重、候选追加不粘词/不补重复分隔符
  - 保存响应回填保持当前语言；仅聚焦失焦零写入；实际编辑仍写
  - 详情载入的历史半角 / 未收尾输入只替换分隔字符
  - UI-03：默认标签按界面语言显示、点选携带 canonical、自定义内容不翻译
  - 默认与同显自定义共存不合并；相邻编辑/删除后身份仍正确；鼠标与键盘一致
  - UI-051：constructor / toString / __proto__ / hasOwnProperty 不被当译表内容
    （中英各自：原样显示、过滤不抛错、鼠标/键盘点选与保存保真、默认仍翻译）
  - emoji 保真、IME 组字保护不退化
  - 英文相邻完整标签显示「; 」（结尾单个 ;、不叠加空格、不新增空标签），中文不变

这些断言直接执行 panel.js 里的真实函数（在 vm 里加载），不是静态源码检查。
面板依赖的 window / document / fetch 用最小 stub 顶替，不连网、不碰真实库。

用法：
    /usr/local/bin/node tools/test_panel_note_separators.mjs
*/

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
/* 默认读工作区 panel.js；PANEL_JS_PATH 可指向另一份文件，用来在隔离进程里
   验证某个历史提交的行为（不改工作区、不建 worktree）。 */
const PANEL_JS = process.env.PANEL_JS_PATH
  ? path.resolve(process.env.PANEL_JS_PATH)
  : path.join(ROOT, "jobs", "panel_assets", "panel.js");
const TAGS = ["Alpha", "Beta", "合成甲", "😀", "Gamma"];
/* UI-03 合成语料：两条默认标签 + 自定义（含与默认同显的 Prioritize） + emoji。 */
const LOCAL_TAGS = ["优先投递", "搬迁", "Prioritize", "My custom note", "😀"];
const EN_LABELS = { "优先投递": "Prioritize", "搬迁": "Relocation" };
/* 原型链上存在的键名，作为自定义候选时必须是普通字符串。 */
const PROTO_TAGS = ["constructor", "toString", "__proto__", "hasOwnProperty", "优先投递"];

const failures = [];
function check(label, ok, detail = "") {
  console.log(`  [${ok ? "PASS" : "FAIL"}] ${label}${detail ? "  " + detail : ""}`);
  if (!ok) failures.push(label);
}
function eq(label, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  check(label, ok, ok ? "" : `got=${JSON.stringify(got)} want=${JSON.stringify(want)}`);
}
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const itemEls = (menu) => menu.children.filter((c) => c.className === "note-item");
const itemTexts = (menu) => itemEls(menu).map((c) => c.textContent);

/* 模拟后台 labels.normalize_note 的最终落库形态。 */
function normalize(v) {
  if (v == null) return null;
  const tags = String(v).split(/[；;]/).map((s) => s.trim()).filter(Boolean);
  return tags.length ? tags.join("；") + "；" : null;
}

class ClassList {
  constructor() { this.set = new Set(); }
  add(...names) { names.forEach((n) => this.set.add(n)); }
  remove(...names) { names.forEach((n) => this.set.delete(n)); }
  contains(name) { return this.set.has(name); }
  toggle(name, force) {
    const on = force === undefined ? !this.set.has(name) : !!force;
    on ? this.set.add(name) : this.set.delete(name);
    return on;
  }
}

class FakeEl {
  constructor(tag = "div") {
    this.tagName = (tag || "div").toUpperCase();
    this.children = [];
    this.style = {};
    this.classList = new ClassList();
    this.dataset = {};
    this._attrs = {};
    this._html = "";
    this.value = "";
    this.textContent = "";
    this.offsetHeight = 0;
    this.offsetWidth = 0;
    this.scrollTop = 0;
    this.hidden = false;
    this.disabled = false;
    this._className = "";
    this._listeners = {};
  }
  set className(value) {
    this._className = String(value);
    this.classList = new ClassList();
    this._className.split(/\s+/).filter(Boolean).forEach((n) => this.classList.add(n));
  }
  get className() { return this._className; }
  set innerHTML(value) {
    this._html = String(value);
    if (value === "" || value == null) this.children = [];
  }
  get innerHTML() { return this._html; }
  appendChild(child) { this.children.push(child); return child; }
  setAttribute(key, value) { this._attrs[key] = String(value); }
  getAttribute(key) { return this._attrs[key]; }
  addEventListener(type, fn) {
    (this._listeners[type] = this._listeners[type] || []).push(fn);
  }
  removeEventListener(type, fn) {
    const list = this._listeners[type];
    if (list) this._listeners[type] = list.filter((f) => f !== fn);
  }
  dispatch(type, ev) {
    const event = Object.assign({ type, target: this, preventDefault() {} }, ev || {});
    (this._listeners[type] || []).forEach((fn) => fn(event));
  }
  querySelector(selector) { return this._query(selector)[0] || null; }
  querySelectorAll(selector) { return this._query(selector); }
  _query(selector) {
    const cls = String(selector).replace(/^\./, "");
    return this.children.filter((c) => c.classList && c.classList.contains(cls));
  }
  getBoundingClientRect() {
    return { left: 0, top: 0, right: 200, bottom: 20, width: 200, height: 20 };
  }
  focus() { this._focused = true; }
  blur() { this._focused = false; }
  contains() { return false; }
  scrollIntoView() {}
}

/* 在最小 DOM stub 里加载真实 panel.js，并把备注相关函数暴露出来。 */
function loadPanel(lang, tags, noteTagLabels) {
  const menu = new FakeEl("div");
  const toast = new FakeEl("div");
  const byId = new Map([["note-menu", menu], ["toast", toast]]);
  const document = {
    documentElement: { style: { setProperty() {} }, dataset: {} },
    body: { classList: new ClassList() },
    activeElement: null,
    cookie: "",
    getElementById: (id) => byId.get(id) || null,
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: (tag) => new FakeEl(tag),
    addEventListener() {},
    removeEventListener() {},
  };
  const sandbox = {
    console,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    setImmediate,
    Promise,
    Date,
    Math,
    JSON,
    String,
    Number,
    Object,
    Array,
    RegExp,
    Error,
    URLSearchParams,
    Node: function Node() {},
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    history: { replaceState() {} },
    location: { hash: "", href: "", reload() {} },
    document,
    matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
    innerWidth: 1000,
    innerHeight: 800,
    addEventListener() {},
    removeEventListener() {},
    fetch: async () => { throw new Error("fetch 未配置"); },
  };
  sandbox.window = sandbox;
  sandbox.window.__I18N__ = { "js.note_sep": lang === "en" ? ";" : "；", "js.note_gap": lang === "en" ? " " : "" };
  sandbox.window.__LABELS__ = {};
  sandbox.window.__NOTE_TAGS__ = tags || [];
  sandbox.window.__NOTE_TAG_LABELS__ = noteTagLabels || {};
  sandbox.window.__SKILLS__ = [];
  sandbox.window.__VIEW__ = "all";
  vm.createContext(sandbox);
  const src = fs.readFileSync(PANEL_JS, "utf8") + `
;globalThis.__PANEL__ = { noteSegments, noteCurrent, noteClosed, notePick, noteToDisplay,
  noteToStore, noteTagDisplay, noteTagTranslate, noteReconcile, noteCanonicalSegments,
  noteCanonicalize, noteAdopt, noteSync, noteRender, noteBlur, setNote, noteKeys,
  imeComposing, imeStart, imeEnd, imeBusy, imeJustComposed, NOTE_TAGS, NOTE_TAG_LABELS, LABELS };
`;
  vm.runInContext(src, sandbox, { filename: "panel.js" });
  const panel = { api: sandbox.__PANEL__, sandbox, menu, calls: [] };
  panel.respondWith = (responder) => {
    panel.calls.length = 0;
    sandbox.fetch = async (url, opts) => {
      const body = JSON.parse(opts.body);
      panel.calls.push({ url, body });
      const value = typeof responder === "function" ? responder(body, panel.calls.length) : responder;
      return {
        ok: true,
        async json() { return { ok: true, field: body.field, value }; },
        async text() { return ""; },
      };
    };
  };
  return panel;
}

function inputOf(uid, value) {
  const input = new FakeEl("input");
  input.dataset.uid = uid;
  input.value = value;
  return input;
}

function pick(panel, value, tag, typing) {
  const input = new FakeEl("input");
  input.value = value;
  input.dataset.typing = typing || "";
  panel.api.notePick(input, tag);
  return input.value;
}

async function testChinese() {
  const panel = loadPanel("zh", TAGS);
  console.log("\n-- 中文界面：显示 / 拆段 / 过滤 / 追加 --");
  eq("显示：全角保持", panel.api.noteToDisplay("Alpha；Beta；"), "Alpha；Beta；");
  eq("显示：半角转全角", panel.api.noteToDisplay("Alpha;Beta"), "Alpha；Beta");
  eq("显示：混合只替换不合并", panel.api.noteToDisplay("Alpha；;Beta"), "Alpha；；Beta");
  eq("显示：未收尾不补尾", panel.api.noteToDisplay("Alpha"), "Alpha");
  eq("存储：收回全角 canonical", panel.api.noteToStore("Alpha;Beta"), "Alpha；Beta");

  eq("拆段：全角", panel.api.noteSegments("Alpha；Beta"), ["Alpha", "Beta"]);
  eq("拆段：混合", panel.api.noteSegments("Alpha;Beta；Gamma"), ["Alpha", "Beta", "Gamma"]);
  eq("末段过滤：只看最后一段", panel.api.noteCurrent("Alpha；Beta"), "Beta");
  eq("末段过滤：收尾段为空", panel.api.noteCurrent("Alpha；Beta；"), "");
  eq("去重：只看已收尾整段", panel.api.noteClosed("Alpha;Beta；Gamma"), ["Alpha", "Beta"]);

  {
    const input = new FakeEl("input");
    input.value = "Alpha；"; input.dataset.typing = "";
    panel.menu.children = [];
    panel.api.noteRender(input, false);
    eq("候选：聚焦显示全部未被选中的标签", itemTexts(panel.menu),
       ["Beta", "合成甲", "😀", "Gamma"]);
  }
  {
    const input = new FakeEl("input");
    input.value = "Alpha；Be"; input.dataset.typing = "1";
    panel.menu.children = [];
    panel.api.noteRender(input, true);
    eq("候选：末段过滤不粘整串", itemTexts(panel.menu), ["Beta"]);
  }
  {
    const input = new FakeEl("input");
    input.value = "Alpha;合成；B"; input.dataset.typing = "1";
    panel.menu.children = [];
    panel.api.noteRender(input, true);
    eq("候选：混合分隔符下仍按末段过滤、按整段去重", itemTexts(panel.menu), ["Beta"]);
  }

  eq("追加：收尾段直接追加", pick(panel, "Alpha；", "Beta"), "Alpha；Beta；");
  eq("追加：替换正在输入的半截", pick(panel, "Alpha；需", "需驾照", "1"), "Alpha；需驾照；");
  eq("追加：未收尾段先补分隔符，避免粘词",
     pick(panel, "Alpha；需", "需驾照", ""), "Alpha；需；需驾照；");
  eq("追加：旧半角结尾不因全角补重复", pick(panel, "Alpha;", "Beta"), "Alpha;Beta；");
  eq("追加：两种分隔符都已收尾时不重复", pick(panel, "Alpha；;", "B"), "Alpha；;B；");

  console.log("\n-- 中文界面：保存回填与零写入 --");
  {
    const input = inputOf("t:save", "Alpha;Beta");
    panel.respondWith("Alpha；Beta；");
    await panel.api.setNote(input);
    eq("保存：服务端全角 canonical 回填为全角显示", input.value, "Alpha；Beta；");
    eq("保存：LABELS 存的是后台 canonical 值",
       panel.api.LABELS["t:save"].note, "Alpha；Beta；");
    eq("保存：请求体已按 canonical 形态发送", panel.calls.at(-1).body.value, "Alpha；Beta");
  }
  {
    const input = inputOf("t:blur", "Alpha；Beta；");
    panel.api.noteSync(input);
    panel.respondWith((body) => body.value);
    panel.api.noteBlur(input);
    await tick();
    check("零写入：仅聚焦后失焦不发请求", panel.calls.length === 0, `calls=${panel.calls.length}`);
    input.value = "Alpha；Gamma；";
    panel.api.noteBlur(input);
    await tick();
    eq("写入：实际编辑发一次请求", panel.calls.length, 1);
    eq("写入：请求体是编辑后的显示值", panel.calls[0].body.value, "Alpha；Gamma；");
    eq("写入：保存后基线更新，再失焦不再写", (panel.api.noteBlur(input), panel.calls.length), 1);
  }
}

async function testEnglish() {
  const panel = loadPanel("en", TAGS);
  console.log("\n-- 英文界面：显示 / 拆段 / 过滤 / 追加 --");
  eq("显示：全角转半角", panel.api.noteToDisplay("Alpha；Beta；"), "Alpha;Beta;");
  eq("显示：半角保持", panel.api.noteToDisplay("Alpha;Beta"), "Alpha;Beta");
  eq("显示：混合只替换不合并", panel.api.noteToDisplay("Alpha；;Beta"), "Alpha;;Beta");
  eq("存储：收回全角 canonical", panel.api.noteToStore("Alpha;Beta;"), "Alpha；Beta；");
  eq("末段过滤：只看最后一段", panel.api.noteCurrent("Alpha;Beta"), "Beta");
  eq("去重：只看已收尾整段", panel.api.noteClosed("Alpha；Beta;Gamma"), ["Alpha", "Beta"]);
  eq("追加：用半角分隔符", pick(panel, "Alpha;", "Beta"), "Alpha; Beta;");
  eq("追加：旧全角结尾不因半角补重复", pick(panel, "Alpha；;", "Beta"), "Alpha；; Beta;");

  {
    const input = new FakeEl("input");
    input.value = "Alpha;"; input.dataset.typing = "";
    panel.menu.children = [];
    panel.api.noteRender(input, false);
    eq("候选：英文界面同样按整段去重", itemTexts(panel.menu),
       ["Beta", "合成甲", "😀", "Gamma"]);
  }

  console.log("\n-- 英文界面：详情载入 / 保存回填 / 零写入 --");
  {
    const legacy = inputOf("t:legacy", "Alpha；Beta");
    panel.api.noteSync(legacy);
    eq("详情载入：历史全角转半角显示", legacy.value, "Alpha; Beta");
    eq("详情载入：基线等于显示值", legacy.dataset.noteLoaded, "Alpha; Beta");
  }
  {
    const input = inputOf("t:en", "Alpha;");
    panel.respondWith("Alpha；Beta；");
    await panel.api.setNote(input);
    eq("保存：后台全角响应回填为半角显示", input.value, "Alpha; Beta;");
    eq("保存：LABELS 仍是全角 canonical",
       panel.api.LABELS["t:en"].note, "Alpha；Beta；");
  }
  {
    const input = inputOf("t:en2", "Alpha；Beta；");
    panel.api.noteSync(input);
    eq("详情载入：英文界面把存储全角转成半角并在标签间补空格", input.value, "Alpha; Beta;");
    panel.respondWith((body) => body.value);
    panel.calls.length = 0;
    panel.api.noteBlur(input);
    await tick();
    check("零写入：英文界面仅聚焦失焦也不发请求", panel.calls.length === 0,
          `calls=${panel.calls.length}`);
  }
}

async function testLocalizedDefaults() {
  const panel = loadPanel("en", LOCAL_TAGS, EN_LABELS);
  console.log("\n-- 默认标签本地化：候选显示与过滤 --");
  {
    const input = new FakeEl("input");
    input.dataset.noteStored = "";
    panel.api.noteSync(input);
    panel.menu.children = [];
    panel.api.noteRender(input, false);
    const els = itemEls(panel.menu);
    eq("候选：默认标签显示英文译名", itemTexts(panel.menu),
       ["Prioritize  ·  优先投递", "Relocation", "Prioritize", "My custom note", "😀"]);
    eq("候选：默认项携带 canonical", els[0].dataset.tag, "优先投递");
    eq("候选：默认项携带显示名", els[0].dataset.display, "Prioritize");
    eq("候选：同显自定义不被合并", els[2].dataset.tag, "Prioritize");
    eq("候选：自定义项显示原文", els[3].textContent, "My custom note");
  }
  {
    const input = new FakeEl("input");
    input.dataset.noteStored = "";
    panel.api.noteSync(input);
    input.value = "Rel"; input.dataset.typing = "1";
    panel.menu.children = [];
    panel.api.noteRender(input, true);
    eq("候选：英文前缀匹配译名", itemTexts(panel.menu), ["Relocation"]);
  }
  {
    const input = new FakeEl("input");
    input.dataset.noteStored = "";
    panel.api.noteSync(input);
    input.value = "Pri"; input.dataset.typing = "1";
    panel.menu.children = [];
    panel.api.noteRender(input, true);
    eq("候选：默认与同显自定义都按译名出现", itemTexts(panel.menu),
       ["Prioritize  ·  优先投递", "Prioritize"]);
  }

  console.log("\n-- 默认标签本地化：载入 / 点选 / 保存闭合 --");
  {
    const input = new FakeEl("input");
    input.dataset.uid = "t:load";
    input.dataset.noteStored = "优先投递；My custom note；";
    input.value = "Prioritize; My custom note;";
    panel.api.noteSync(input);
    eq("载入：默认译名 + 自定义原文", input.value, "Prioritize; My custom note;");
    eq("载入：canonical 身份映射正确", panel.api.noteCanonicalize(input),
       "优先投递；My custom note；");
    panel.respondWith((body) => normalize(body.value));
    panel.calls.length = 0;
    panel.api.noteBlur(input);
    await tick();
    check("载入后仅聚焦失焦零写", panel.calls.length === 0, `calls=${panel.calls.length}`);
    input.value = "Prioritize;My custom note 2;";
    await panel.api.setNote(input);
    eq("编辑相邻自定义：默认仍映射 canonical",
       panel.calls.at(-1).body.value, "优先投递；My custom note 2；");
    eq("编辑相邻自定义：英文回填", input.value, "Prioritize; My custom note 2;");
    eq("LABELS 存 canonical", panel.api.LABELS["t:load"].note,
       "优先投递；My custom note 2；");
  }
  {
    const input = new FakeEl("input");
    input.dataset.uid = "t:pick";
    input.dataset.noteStored = "";
    panel.api.noteSync(input);
    panel.respondWith((body) => normalize(body.value));
    panel.api.notePick(input, "优先投递", "Prioritize");
    eq("点选默认：插入英文译名", input.value, "Prioritize;");
    await panel.api.setNote(input);
    eq("点选默认：保存 canonical", panel.calls.at(-1).body.value, "优先投递；");
    eq("点选默认：英文回填", input.value, "Prioritize;");
    eq("点选默认：LABELS canonical", panel.api.LABELS["t:pick"].note, "优先投递；");
  }
  {
    const input = new FakeEl("input");
    input.dataset.uid = "t:typed";
    input.dataset.noteStored = "";
    panel.api.noteSync(input);
    input.value = "Prioritize";
    panel.respondWith((body) => normalize(body.value));
    await panel.api.setNote(input);
    eq("手输英文：不反向映射", panel.calls.at(-1).body.value, "Prioritize");
    eq("手输英文：按自定义存储", panel.api.LABELS["t:typed"].note, "Prioritize；");
  }

  console.log("\n-- 默认 / 自定义冲突边界 --");
  {
    // 自定义在前、默认在后：同显不合并，顺序与 canonical 都保留。
    const input = new FakeEl("input");
    input.dataset.uid = "t:both";
    input.dataset.noteStored = "Prioritize；优先投递；";
    input.value = "Prioritize;Prioritize;";
    panel.api.noteSync(input);
    panel.respondWith((body) => normalize(body.value));
    await panel.api.setNote(input);
    eq("同显共存：各自 canonical 与顺序保留",
       panel.calls.at(-1).body.value, "Prioritize；优先投递；");
  }
  {
    // 删除默认标签后重打同显英文，不再继承旧身份。
    const input = new FakeEl("input");
    input.dataset.uid = "t:del";
    input.dataset.noteStored = "优先投递；";
    input.value = "Prioritize;";
    panel.api.noteSync(input);
    input.value = "";
    panel.api.noteRender(input, false);          // 模拟删除后的 input 事件
    input.value = "Prioritize";                  // 手动重打
    panel.respondWith((body) => normalize(body.value));
    await panel.api.setNote(input);
    eq("删除后重打同显英文：按自定义", panel.calls.at(-1).body.value, "Prioritize");
  }
  {
    // 混合默认 + 自定义 + emoji：只默认片段翻译。
    const input = new FakeEl("input");
    input.dataset.uid = "t:mix";
    input.dataset.noteStored = "搬迁；😀；优先投递；";
    input.value = "Relocation; 😀; Prioritize;";
    panel.api.noteSync(input);
    eq("混合串：只翻译默认片段", input.value, "Relocation; 😀; Prioritize;");
    eq("混合串：canonical 全保真", panel.api.noteCanonicalize(input),
       "搬迁；😀；优先投递；");
  }
  {
    // 键盘选择也必须携带 canonical，而不是 textContent。
    const input = new FakeEl("input");
    input.dataset.uid = "t:kb";
    input.dataset.noteStored = "";
    panel.api.noteSync(input);
    panel.api.noteRender(input, false);
    panel.menu.dataset.hl = "0";
    panel.api.noteKeys({ key: "Enter", isComposing: false, keyCode: 0 }, input);
    eq("键盘选择默认：插入英文译名", input.value, "Prioritize;");
    panel.respondWith((body) => normalize(body.value));
    await panel.api.setNote(input);
    eq("键盘选择默认：保存 canonical", panel.calls.at(-1).body.value, "优先投递；");
  }

  console.log("\n-- 中文界面不受译名影响 --");
  {
    const zh = loadPanel("zh", LOCAL_TAGS, {});
    const input = new FakeEl("input");
    input.dataset.noteStored = "优先投递；My custom note；";
    zh.api.noteSync(input);
    eq("中文载入：默认显示原文", input.value, "优先投递；My custom note；");
    const q = new FakeEl("input");
    q.dataset.noteStored = "";
    zh.api.noteSync(q);
    q.value = "搬"; q.dataset.typing = "1";
    zh.menu.children = [];
    zh.api.noteRender(q, true);
    eq("中文候选：仍按中文过滤", itemTexts(zh.menu), ["搬迁"]);
  }
}

async function testEnglishBoundarySpace() {
  const panel = loadPanel("en", LOCAL_TAGS, EN_LABELS);
  const zh = loadPanel("zh", LOCAL_TAGS, {});
  console.log("\n-- 英文：相邻标签之间显示「; 」 --");
  const shown = (v) => { const i = new FakeEl("input"); i.dataset.noteStored = v; panel.api.noteSync(i); return i.value; };
  eq("canonical 显示：默认 + 自定义 + emoji",
     shown("搬迁；follow up；🎉 great team；"), "Relocation; follow up; 🎉 great team;");
  eq("末尾仍是单个分隔符，没有结尾空格", shown("搬迁；优先投递；"), "Relocation; Prioritize;");
  eq("已有空格不叠加", shown("A； B；"), "A; B;");
  eq("多个空格原样保留", shown("A；  B；"), "A;  B;");
  eq("空标签不新增", shown("A；；B；"), "A;; B;");
  eq("自定义大小写 / 词内空白 / emoji 原样", shown("Ab  Cd；😀 x；"), "Ab  Cd; 😀 x;");
  eq("单个标签：无边界不加空格", shown("搬迁；"), "Relocation;");
  {
    const input = new FakeEl("input"); input.dataset.uid = "t:gap"; input.dataset.noteStored = "";
    panel.api.noteSync(input);
    panel.respondWith((b) => normalize(b.value));
    panel.api.notePick(input, "搬迁", "Relocation");
    eq("追加第一个", input.value, "Relocation;");
    panel.api.notePick(input, "优先投递", "Prioritize");
    eq("追加第二个：补一个空格", input.value, "Relocation; Prioritize;");
    panel.api.notePick(input, "My custom note", "My custom note");
    eq("追加第三个", input.value, "Relocation; Prioritize; My custom note;");
    input.value = "Relocation; Prio"; input.dataset.typing = "1";
    panel.api.notePick(input, "优先投递", "Prioritize");
    eq("替换正在输入的半截：保留空格不叠加", input.value, "Relocation; Prioritize;");
    input.value = "Relocation;"; input.dataset.typing = "";
    panel.api.notePick(input, "优先投递", "Prioritize");
    eq("上一段以分隔符收尾：补空格再追加", input.value, "Relocation; Prioritize;");
    input.value = "Relocation; "; input.dataset.typing = "";
    panel.api.notePick(input, "优先投递", "Prioritize");
    eq("已有空格：追加不叠加", input.value, "Relocation; Prioritize;");
    input.value = "Relocation"; input.dataset.typing = "";
    panel.api.notePick(input, "优先投递", "Prioritize");
    eq("上一段未收尾：补分隔符与空格", input.value, "Relocation; Prioritize;");
  }
  for (const typed of ["Relocation;follow up", "Relocation; follow up", "Relocation；follow up",
                       "Relocation;  follow up；", "Relocation;；follow up"]) {
    const input = new FakeEl("input"); input.dataset.uid = "t:typed"; input.dataset.noteStored = "";
    panel.api.noteSync(input);
    panel.respondWith((b) => normalize(b.value));
    input.value = typed;
    await panel.api.setNote(input);
    // 手打的 "Relocation" 不是点选的默认标签，按自定义原文保存；服务端规范化后一致
    eq(`保存 ${JSON.stringify(typed)}：规范化后 canonical`, normalize(panel.calls.at(-1).body.value), "Relocation；follow up；");
    eq(`保存 ${JSON.stringify(typed)}：回填`, input.value, "Relocation; follow up;");
  }
  {
    const input = new FakeEl("input"); input.dataset.uid = "t:typed2"; input.dataset.noteStored = "";
    panel.api.noteSync(input);
    panel.respondWith((b) => normalize(b.value));
    input.value = "Prioritize;Prioritize";
    input._noteIds = [{ display: "Prioritize", canonical: "优先投递" }, null];
    await panel.api.setNote(input);
    eq("同显：默认身份仍是 canonical，自定义不被翻译", panel.calls.at(-1).body.value, "优先投递；Prioritize");
  }
  {
    const input = new FakeEl("input"); input.dataset.uid = "t:zero"; input.dataset.noteStored = "搬迁；follow up；";
    panel.api.noteSync(input);
    panel.respondWith((b) => b.value);
    panel.calls.length = 0;
    panel.api.noteRender(input, false);
    panel.api.noteBlur(input);
    await tick();
    eq("载入 + 聚焦失焦：零写入", panel.calls.length, 0);
  }
  {
    const input = new FakeEl("input"); input.dataset.noteStored = "搬迁；follow up；";
    zh.api.noteSync(input);
    eq("中文显示不加空格", input.value, "搬迁；follow up；");
    zh.api.notePick(input, "优先投递", "优先投递");
    eq("中文追加不加空格", input.value, "搬迁；follow up；优先投递；");
  }
}

async function testPrototypeKeys() {
  console.log("\n-- UI-051：原型链键名不作为译表内容 --");
  const cases = [["co", "constructor"], ["toS", "toString"], ["__p", "__proto__"], ["hasOwn", "hasOwnProperty"]];

  for (const lang of ["zh", "en"]) {
    const labels = lang === "en" ? { "优先投递": "Prioritize" } : {};
    const panel = loadPanel(lang, PROTO_TAGS, labels);
    const defaultDisplay = lang === "en" ? "Prioritize" : "优先投递";
    const sep = lang === "en" ? ";" : "；";

    // 1) 已有备注：原型键片段按自定义原文显示，不泄漏函数/原型文本。
    const stored = "constructor；toString；__proto__；hasOwnProperty；优先投递；";
    const gap = lang === "en" ? " " : "";
    const expected = ["constructor", "toString", "__proto__", "hasOwnProperty", defaultDisplay]
      .join(sep + gap) + sep;
    {
      const input = new FakeEl("input");
      input.dataset.noteStored = stored;
      panel.api.noteSync(input);
      eq(`${lang} 已有备注：原型键自定义原样显示`, input.value, expected);
      check(`${lang} 已有备注：不出现函数 / 原型文本`,
            !/function/i.test(input.value) && !/native code/i.test(input.value)
            && !/\[object Object\]/.test(input.value));
      eq(`${lang} 已有备注：canonical 全保真`,
         panel.api.noteCanonicalize(input), stored);
    }

    // 2) 候选前缀过滤：每个原型键前缀都必须命中且不抛错。
    for (const [prefix, tagName] of cases) {
      const input = new FakeEl("input");
      input.dataset.noteStored = "";
      panel.api.noteSync(input);
      input.value = prefix; input.dataset.typing = "1";
      panel.menu.children = [];
      let threw = null;
      try { panel.api.noteRender(input, true); } catch (e) { threw = e; }
      check(`${lang} 候选过滤：${tagName} 前缀不抛错`, threw === null, threw ? String(threw) : "");
      eq(`${lang} 候选过滤：命中 ${tagName}`, itemTexts(panel.menu), [tagName]);
    }

    // 3) 鼠标点选：走真实 mousedown handler，保留原文（含保存）。
    {
      const input = new FakeEl("input");
      input.dataset.uid = `t:proto:m:${lang}`;
      input.dataset.noteStored = "";
      panel.api.noteSync(input);
      panel.menu.children = [];
      let threw = null;
      try { panel.api.noteRender(input, false); } catch (e) { threw = e; }
      const el = itemEls(panel.menu).find((x) => x.dataset.tag === "constructor");
      if (el) { try { el.dispatch("mousedown"); } catch (e) { threw = e; } }
      check(`${lang} 鼠标点选：不抛错且命中`,
            el !== undefined && threw === null, threw ? String(threw) : (el ? "" : "no item"));
      eq(`${lang} 鼠标点选：保留原文`, input.value, `constructor${sep}`);
      panel.respondWith((body) => normalize(body.value));
      await panel.api.setNote(input);
      eq(`${lang} 鼠标点选：保存原文`,
         panel.calls.at(-1).body.value, "constructor；");
      eq(`${lang} 鼠标点选：LABELS 原文`,
         panel.api.LABELS[`t:proto:m:${lang}`].note, "constructor；");
    }

    // 4) 键盘点选：高亮 Enter 也保留原文。
    {
      const input = new FakeEl("input");
      input.dataset.uid = `t:proto:k:${lang}`;
      input.dataset.noteStored = "";
      panel.api.noteSync(input);
      input.value = "toS"; input.dataset.typing = "1";
      panel.menu.children = [];
      let threw = null;
      try { panel.api.noteRender(input, true); } catch (e) { threw = e; }
      if (threw === null) {
        panel.menu.dataset.hl = "0";
        try { panel.api.noteKeys({ key: "Enter", isComposing: false, keyCode: 0 }, input); }
        catch (e) { threw = e; }
      }
      check(`${lang} 键盘点选：不抛错`, threw === null, threw ? String(threw) : "");
      eq(`${lang} 键盘点选：替换半截为原文`, input.value, `toString${sep}`);
      panel.respondWith((body) => normalize(body.value));
      await panel.api.setNote(input);
      eq(`${lang} 键盘点选：保存原文`, panel.calls.at(-1).body.value, "toString；");
    }

    // 5) 正常默认标签仍翻译并回 canonical（原型修复不影响本职）。
    {
      const input = new FakeEl("input");
      input.dataset.uid = `t:proto:d:${lang}`;
      input.dataset.noteStored = "优先投递；";
      panel.api.noteSync(input);
      eq(`${lang} 默认标签：仍显示译名`, input.value, `${defaultDisplay}${sep}`);
      panel.respondWith((body) => normalize(body.value));
      await panel.api.setNote(input);
      eq(`${lang} 默认标签：仍回 canonical`,
         panel.calls.at(-1).body.value, "优先投递；");
    }
  }

  // 6) 同显冲突统计不能让 __proto__ 影响重复判定。
  {
    const panel = loadPanel("en", ["优先投递", "__proto__"], { "优先投递": "__proto__" });
    const input = new FakeEl("input");
    input.dataset.noteStored = "";
    panel.api.noteSync(input);
    panel.menu.children = [];
    let threw = null;
    try { panel.api.noteRender(input, false); } catch (e) { threw = e; }
    check("同显冲突：__proto__ 重复判定不抛错", threw === null, threw ? String(threw) : "");
    const els = itemEls(panel.menu);
    eq("同显冲突：__proto__ 重复判定生效（默认加提示）",
       els.map((e) => e.textContent), ["__proto__  ·  优先投递", "__proto__"]);
    eq("同显冲突：默认 canonical 正确", els[0] && els[0].dataset.tag, "优先投递");
    eq("同显冲突：自定义原文正确", els[1] && els[1].dataset.tag, "__proto__");
  }
}

function testEmojiAndIme() {
  const panel = loadPanel("zh", TAGS);
  console.log("\n-- emoji 与 IME 保护 --");
  eq("emoji：拆段保真", panel.api.noteSegments("😀；Alpha"), ["😀", "Alpha"]);
  eq("emoji：追加保真", pick(panel, "😀；", "合成甲"), "😀；合成甲；");
  {
    const input = new FakeEl("input");
    input.value = "😀；😀 副本"; input.dataset.typing = "1";
    panel.menu.children = [];
    panel.api.noteRender(input, true);
    eq("emoji：候选过滤按末段", itemTexts(panel.menu), []);
  }

  panel.menu.classList.remove("open");
  let composingBlur = false;
  {
    const input = new FakeEl("input");
    input.blur = () => { composingBlur = true; };
    panel.api.imeStart(input);
    check("IME：组字中 imeBusy=true", panel.api.imeBusy(input) === true);
    check("IME：isComposing 直接识别", panel.api.imeComposing({ isComposing: true }) === true);
    panel.api.noteKeys({ key: "Enter", isComposing: false, keyCode: 0 }, input);
    check("IME：组字期间回车不触发 blur/保存", composingBlur === false);
    panel.api.imeEnd(input);
    check("IME：组字结束 imeBusy=false", panel.api.imeBusy(input) === false);
    check("IME：刚组字结束的时间戳兜底生效",
          panel.api.imeJustComposed(input, 5000) === true);
    panel.api.noteKeys({ key: "Enter", isComposing: false, keyCode: 0 }, input);
    check("IME：组字刚结束的回车仍被挡（时间戳兜底）", composingBlur === false);
  }
  {
    const input = new FakeEl("input");
    let plainBlur = false;
    input.blur = () => { plainBlur = true; };
    panel.api.noteKeys({ key: "Enter", isComposing: false, keyCode: 0 }, input);
    check("IME：未组字时回车沿用原保存路径（blur）", plainBlur === true);
  }
}

async function main() {
  console.log("== 备注分隔符 / 默认标签本地化 JS 交互（合成数据，Node vm + 最小 DOM stub）==");
  console.log(`Node ${process.version}；加载 ${path.relative(ROOT, PANEL_JS)}`);
  await testChinese();
  await testEnglish();
  await testLocalizedDefaults();
  await testEnglishBoundarySpace();
  await testPrototypeKeys();
  testEmojiAndIme();
  console.log();
  if (failures.length) {
    console.log(`失败 ${failures.length} 项：\n  - ${failures.join("\n  - ")}`);
    process.exit(2);
  }
  console.log("全部通过");
  process.exit(0);
}

main().catch((err) => {
  console.error("harness 出错：", err);
  process.exit(3);
});
