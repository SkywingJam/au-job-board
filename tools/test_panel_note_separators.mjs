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
  - v1.2 数据版本自动同步、A→B/关闭/目标导航、草稿/保存保护和修饰键语义

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
function loadPanel(lang, tags, noteTagLabels, settingsGeneration) {
  const menu = new FakeEl("div");
  const toast = new FakeEl("div");
  const byId = new Map([["note-menu", menu], ["toast", toast]]);
  const listeners = new Map();
  const windowListeners = new Map();
  const document = {
    documentElement: { style: { setProperty() {} }, dataset: {} },
    body: new FakeEl("body"),
    activeElement: null,
    cookie: "",
    getElementById: (id) => byId.get(id) || null,
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: (tag) => new FakeEl(tag),
    addEventListener(type, fn) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(fn);
    },
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
    URL,
    Node: function Node() {},
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    history: { replaceState() {} },
    location: { hash: "", href: "", reload() {} },
    document,
    matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
    innerWidth: 1000,
    innerHeight: 800,
    addEventListener(type,fn){if(!windowListeners.has(type))windowListeners.set(type,[]);windowListeners.get(type).push(fn);},
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
  sandbox.window.__STALE_AFTER_DAYS__ = 2;
  if(settingsGeneration!==undefined)sandbox.window.__SETTINGS_GENERATION__=settingsGeneration;
  vm.createContext(sandbox);
  const src = fs.readFileSync(PANEL_JS, "utf8") + `
;globalThis.__PANEL__ = { noteSegments, noteCurrent, noteClosed, notePick, noteToDisplay,
  noteToStore, noteTagDisplay, noteTagTranslate, noteReconcile, noteCanonicalSegments,
  noteCanonicalize, noteAdopt, noteSync, noteRender, noteBlur, setNote, noteKeys,
  imeComposing, imeStart, imeEnd, imeBusy, imeJustComposed, NOTE_TAGS, NOTE_TAG_LABELS, LABELS,
  renderUpdateState, openJob, closeDetail, navigatePanel, applyLatestResults, skillPick,
  requestJobUpdate, closeUpdateConfirm, refreshDot, relativeRunTime,
  setUpdateTestState(v){OPEN_UID=v.uid||null;UPDATE_BASELINE=v.baseline||'';UPDATE_PENDING=v.pending||null;UPDATE_APPLYING=false;},
  getUpdateTestState(){return {uid:OPEN_UID,baseline:UPDATE_BASELINE,pending:UPDATE_PENDING};},
  addPendingNote(promise){NOTE_REQUESTS.add(promise);promise.finally(()=>NOTE_REQUESTS.delete(promise));} };
`;
  vm.runInContext(src, sandbox, { filename: "panel.js" });
  const panel = { api: sandbox.__PANEL__, sandbox, menu, calls: [], elements: byId,
    ready: () => (listeners.get("DOMContentLoaded") || []).forEach((fn) => fn()),
    dispatch: (type,event) => (listeners.get(type)||[]).forEach(fn=>fn(event)),
    dispatchWindow: (type,event) => (windowListeners.get(type)||[]).forEach(fn=>fn(event)) };
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

async function testUpdateRefreshWithDetailHash() {
  console.log("\n-- v1.2 版本刷新与导航保护 --");
  function fixture(){
    const panel=loadPanel('en',TAGS);
    let reloads=0;
    Object.assign(panel.sandbox.location,{href:'http://localhost/?view=all&q=synthetic&offset=40#A',
      pathname:'/',search:'?view=all&q=synthetic&offset=40',hash:'#A',reload(){reloads++;}});
    panel.sandbox.history.replaceState=(_state,_title,url)=>{
      const next=new URL(url,panel.sandbox.location.href);
      Object.assign(panel.sandbox.location,{href:next.href,pathname:next.pathname,search:next.search,hash:next.hash});
    };
    panel.sandbox.confirm=()=>false;
    panel.elements.set('update-start',new FakeEl('button'));
    panel.elements.set('update-status',new FakeEl('span'));
    panel.elements.set('update-meta',new FakeEl('span'));
    panel.api.setUpdateTestState({uid:'A',baseline:'generation-1'});
    return {panel,reloads:()=>reloads};
  }
  {
    const panel=loadPanel('en',TAGS);
    panel.sandbox.document.body.classList.add('settings');
    const target='http://localhost/settings?section=fetch#retain-target';
    Object.assign(panel.sandbox.location,{href:target,pathname:'/settings',search:'?section=fetch',hash:'#retain-target'});
    let requests=0;panel.sandbox.fetch=async()=>{requests++;throw new Error('unexpected settings detail request');};
    panel.ready();await tick();
    eq('设置页含hash：初始载入不请求岗位详情',requests,0);
    eq('设置页含hash：完整导航目标不变',panel.sandbox.location.href,target);
    panel.sandbox.location.hash='#another-settings-anchor';
    panel.dispatchWindow('hashchange',{});await tick();
    eq('设置页hashchange：不按JD路由、不抛错',requests,0);
    eq('设置页hashchange：保留页面锚点',panel.sandbox.location.hash,'#another-settings-anchor');
  }
  {
    const {panel,reloads}=fixture();
    const input=new FakeEl('input');input.value='synthetic skill';panel.elements.set('skill-q',input);
    let release;panel.sandbox.fetch=()=>new Promise(resolve=>{release=()=>resolve({ok:true,json:async()=>({ok:true})});});
    const saving=panel.api.skillPick('add','');
    eq('技能保存开始后输入已清空',input.value,'');
    let prevented=0;
    const target='http://localhost/settings?section=fetch#editor';
    panel.dispatch('click',{target:{closest:()=>({href:target})},button:0,preventDefault(){prevented++;}});
    eq('技能请求在途：普通链接阻止立即跳转',prevented,1);
    check('技能请求在途：保持原详情URL',panel.sandbox.location.href.includes('#A'));
    release();await saving;await tick();
    eq('技能保存完成：继续完整目的URL',panel.sandbox.location.href,target);
    eq('技能保存完成：不插入旧页reload',reloads(),0);
  }
  const completed={state:'success',task_id:'task-2',generation:'generation-2',finished_at:'2026-10-17T00:00:00Z'};
  {
    for(const baseline of ['generation-1','']){
      const panel=loadPanel('en',TAGS,undefined,baseline);
      panel.sandbox.document.body.classList.add('settings');
      panel.elements.set('update-start',new FakeEl('button'));
      panel.elements.set('update-status',new FakeEl('span'));
      let updates=0;
      panel.sandbox.CustomEvent=class {constructor(type){this.type=type;}};
      panel.sandbox.document.dispatchEvent=event=>{if(event.type==='job-results-updated')updates++;};
      panel.api.renderUpdateState(completed,true);
      eq('设置页首次轮询比较渲染时版本 '+JSON.stringify(baseline),updates,1);
      panel.api.renderUpdateState(completed,true);
      eq('设置页重复轮询同版本不重复更新 '+JSON.stringify(baseline),updates,1);
    }
  }
  {
    const {panel}=fixture();
    const run=new FakeEl('span');run.className='run';
    panel.sandbox.document.querySelectorAll=selector=>selector==='[data-run-time]'?[run]:[];
    const old=new Date(Date.now()-3*86400000).toISOString();
    panel.api.renderUpdateState({...completed,finished_at:old});
    check('过期抓取轮询：保留警告、红点和stale样式',run.classList.contains('stale')
      && run.children.some(el=>el.className==='dot bad')
      && run.children.some(el=>el.textContent.includes('run.stale')));
    // FakeEl has no browser textContent child-clearing semantics.
    run.children=[];
    panel.api.renderUpdateState({...completed,finished_at:new Date().toISOString()});
    check('新抓取完成轮询：清除过期警告和红点',!run.classList.contains('stale')
      && !run.children.some(el=>el.className==='dot bad')
      && !run.children.some(el=>el.textContent.includes('run.stale')));
    run.children=[];
    panel.api.renderUpdateState({...completed,state:'running',finished_at:old});
    check('正在更新：不把旧终态误标为过期',!run.classList.contains('stale'));
  }

  {
    const {panel,reloads}=fixture();
    panel.api.renderUpdateState({...completed,generation:'generation-1'});
    eq('首次旧终态：不刷新、不重播完成提示',reloads(),0);
    eq('首次旧终态：没有完成toast',panel.elements.get('toast').textContent,'');
    panel.api.renderUpdateState(completed);
    eq('已开A：新版本保持当前详情',reloads(),0);
    eq('已开A：记录待刷新版本',panel.api.getUpdateTestState().pending,'generation-2');
    await panel.api.openJob('B');
    eq('待刷新A→B：新文档只加载一次',reloads(),1);
    eq('待刷新A→B：保留目的B以及筛选/分页',panel.sandbox.location.href,'http://localhost/?view=all&q=synthetic&offset=40#B');
    panel.api.renderUpdateState(completed);
    eq('重复轮询同版本：不再次应用',reloads(),1);
  }
  {
    const {panel,reloads}=fixture();panel.api.renderUpdateState(completed);
    await panel.api.closeDetail();
    eq('关闭A：刷新一次',reloads(),1);
    eq('关闭A：移除旧hash并保留全部query',panel.sandbox.location.href,'http://localhost/?view=all&q=synthetic&offset=40');
  }
  {
    const {panel,reloads}=fixture();panel.api.setUpdateTestState({baseline:'generation-1'});
    panel.api.renderUpdateState(completed);
    eq('无JD：新版本自动应用',reloads(),1);
    eq('无JD：新版本不重播完成toast',panel.elements.get('toast').textContent,'');
  }
  {
    const {panel,reloads}=fixture();panel.api.setUpdateTestState({baseline:'generation-1'});
    panel.sandbox.document.hidden=true;panel.api.renderUpdateState(completed);
    eq('后台：推迟自动应用',reloads(),0);
    panel.sandbox.document.hidden=false;panel.api.renderUpdateState(completed);
    eq('恢复可见：应用待刷新版本',reloads(),1);
  }
  {
    const {panel,reloads}=fixture();panel.api.renderUpdateState(completed);
    const input=new FakeEl('input');input.dataset.noteLoaded='saved';input.value='draft';
    panel.sandbox.document.querySelectorAll=selector=>selector==='.note input'?[input]:[];
    await panel.api.openJob('B');
    eq('草稿取消：不刷新、不改目的',reloads(),0);
    eq('草稿取消：保留A hash',panel.sandbox.location.hash,'#A');
    panel.sandbox.confirm=()=>true;await panel.api.openJob('B');
    eq('草稿确认：继续最初目的B',panel.sandbox.location.hash,'#B');
    eq('草稿确认：允许刷新',reloads(),1);
    let prompts=0;panel.dispatchWindow('beforeunload',{preventDefault(){prompts++;}});
    eq('草稿已确认：beforeunload不重复询问',prompts,0);
  }
  {
    const {panel,reloads}=fixture();panel.api.renderUpdateState(completed);
    const target='/settings?section=fetch#config-editor';
    let release;panel.api.addPendingNote(new Promise(resolve=>{release=resolve;}));
    const navigation=panel.api.navigatePanel(target);await tick();
    check('保存中：等待请求完成，保留原位置',panel.sandbox.location.href.includes('#A'));
    release();await navigation;
    eq('保存完成：保留完整设置页目标',panel.sandbox.location.href,target);
    eq('去另一URL：不插入旧页刷新',reloads(),0);
  }
  {
    const {panel}=fixture();
    const input=new FakeEl('input');input.dataset.noteLoaded='saved';input.value='draft';
    panel.sandbox.document.querySelectorAll=selector=>selector==='.note input'?[input]:[];
    let prevented=0;const link={href:'http://localhost/settings?section=fetch#editor'};
    for(const modifiers of [{metaKey:true},{ctrlKey:true},{shiftKey:true},{altKey:true},{button:1}]){
      panel.dispatch('click',{target:{closest:()=>link},button:0,...modifiers,preventDefault(){prevented++;}});
    }
    eq('修饰键/中键：保持原生导航，不消费目标',prevented,0);
    panel.sandbox.confirm=()=>true;
    panel.dispatch('click',{target:{closest:()=>link},button:0,preventDefault(){prevented++;}});await tick();
    eq('普通链接草稿确认：继续原URL含query/hash',panel.sandbox.location.href,link.href);
  }
  {
    const {panel,reloads}=fixture();panel.api.setUpdateTestState({baseline:'generation-1'});
    panel.api.renderUpdateState({...completed,state:'error',generation:'generation-1'});
    eq('最新失败无新版本：不伪造可用结果',reloads(),0);
    panel.api.renderUpdateState({...completed,state:'partial'});
    eq('部分完成有新版本：可以应用',reloads(),1);
  }
}

async function testPendingNoteEdits() {
  console.log("\n-- delayed note responses preserve later drafts --");
  const panel=loadPanel("en", [], {});
  const input=inputOf("synthetic:pending", "");
  panel.api.noteAdopt(input,"Original；");
  const requests=[];
  panel.sandbox.fetch=(url,opts)=>new Promise(resolve=>{
    requests.push({body:JSON.parse(opts.body),resolve});
  });
  input.value="First edit";
  const first=panel.api.setNote(input);
  input.value="Second draft";
  requests[0].resolve({ok:true,json:async()=>({value:"First edit；"})});
  await first;
  eq("old response preserves newer unsaved draft",input.value,"Second draft");
  eq("acknowledged baseline remains accurate",input.dataset.noteStored,"First edit；");
  input.value="Second edit";
  const second=panel.api.setNote(input);
  input.value="Third edit";
  const third=panel.api.setNote(input);
  eq("same UID writes are serialized",requests.length,2);
  requests[1].resolve({ok:true,json:async()=>({value:"Second edit；"})});
  await second; await tick();
  eq("queued request keeps its original payload",requests[2].body.value,"Third edit");
  eq("older response preserves queued edit",input.value,"Third edit");
  requests[2].resolve({ok:true,json:async()=>({value:"Third edit；"})});
  await third;
  eq("latest acknowledged edit adopted",input.value,"Third edit;");
  eq("latest canonical value retained",panel.api.LABELS["synthetic:pending"].note,"Third edit；");
}

async function testRevertedPendingNote() {
  for (const initial of ["Original；", "", "搬迁；"]) {
    const panel=loadPanel("en", LOCAL_TAGS, EN_LABELS);
    const input=inputOf("synthetic:revert", "");
    panel.api.noteAdopt(input,initial);
    const original=input.value;
    const requests=[];
    panel.sandbox.fetch=(url,opts)=>new Promise(resolve=>requests.push({body:JSON.parse(opts.body),resolve}));
    input.value="Temporary";
    panel.api.noteBlur(input);
    input.value=original;
    panel.api.noteBlur(input);
    requests[0].resolve({ok:true,json:async()=>({value:"Temporary；"})});
    await tick();
    eq("reverting to loaded value queues a write "+JSON.stringify(initial),requests.length,2);
    if(requests.length===2){
      eq("revert payload retains original canonical "+JSON.stringify(initial),normalize(requests[1].body.value),initial||null);
      // Repeated blur of the same pending value must not enqueue duplicates.
      panel.api.noteBlur(input);
      requests[1].resolve({ok:true,json:async()=>({value:initial||null})});
      await tick();
      eq("revert response adopts original display "+JSON.stringify(initial),input.value,original);
      eq("revert persisted canonical "+JSON.stringify(initial),panel.api.LABELS["synthetic:revert"].note,initial||null);
      eq("same pending value does not create duplicate write",requests.length,2);
    }
  }
}

/* v1.2.2：「更新岗位」先确认；进度文字随标注就地更新；状态行的「结果未知」保持未知。 */
async function testUpdateConfirmAndProgress() {
  console.log("\n-- v1.2.2 更新确认 / 进度文字 / 状态行 --");
  function fixture(withConfirm){
    const panel=loadPanel('zh',TAGS);
    const button=new FakeEl('button'), box=new FakeEl('div');
    box.hidden=true;
    panel.elements.set('update-start',button);
    const status=new FakeEl('span');
    panel.elements.set('update-status',status);
    if(withConfirm) panel.elements.set('update-confirm',box);
    panel.sandbox.AbortController=AbortController;   // updateJson 的超时控制（实现未改）
    const posts=[];
    panel.sandbox.fetch=async(url,opts)=>{
      if(opts && opts.method==='POST') posts.push(url);
      return {ok:true,json:async()=>({state:'running',accepted:true,task_id:'t-1'})};
    };
    return {panel,button,box,posts,status};
  }
  {
    const {panel,box,posts}=fixture(true);
    panel.api.requestJobUpdate();await tick();
    check('点「更新岗位」只打开确认窗', box.hidden===false && posts.length===0);
    panel.api.closeUpdateConfirm(false);await tick();
    eq('取消：不发起更新', posts.length, 0);
    check('取消：确认窗收起', box.classList.contains('leaving') || box.hidden===true);
  }
  {
    const {panel,posts}=fixture(true);
    panel.api.requestJobUpdate();
    panel.api.closeUpdateConfirm(true);await tick();
    eq('确认：走原来的 POST /api/update 一次', posts, ['/api/update']);
  }
  {
    const {panel,status,box}=fixture(true);
    panel.api.requestJobUpdate();
    check('确认前焦点不在状态文字上', status._focused!==true);
    panel.api.closeUpdateConfirm(true);await tick();
    check('确认：焦点移到可见的更新状态文字（按钮运行期间禁用）', status._focused===true);
    const {panel:p2,status:s2}=fixture(true);
    p2.api.requestJobUpdate();p2.api.closeUpdateConfirm(false);await tick();
    check('取消：焦点不移到状态文字', s2._focused!==true);
  }
  {
    const {panel,button,posts}=fixture(true);
    panel.api.requestJobUpdate();
    button.disabled=true;            // 期间别处已开始更新：按钮被状态机禁用
    panel.api.closeUpdateConfirm(true);await tick();
    eq('确认时按钮已禁用：不再发起', posts.length, 0);
  }
  {
    const {panel,posts}=fixture(false);
    panel.api.requestJobUpdate();await tick();
    eq('页面没有确认窗：保持原行为直接发起', posts, ['/api/update']);
  }
  {
    const panel=loadPanel('zh',TAGS);
    const prog=new FakeEl('span');prog.hidden=true;
    panel.sandbox.document.querySelectorAll=sel=>sel==='.prog[data-prog-uid="u1"]'?[prog]:[];
    panel.api.LABELS.u1={action:'applied'};
    panel.api.refreshDot('u1');
    check('标为已投：卡片进度文字出现', prog.hidden===false && prog.textContent==='value.action.applied'
      && prog.className==='prog good');
    panel.api.LABELS.u1={action:null};
    panel.api.refreshDot('u1');
    check('清除进度：进度文字隐藏', prog.hidden===true && prog.textContent==='');
  }
  {
    // v1.2.3 C：星标与状态行跟随 LABELS，跳过是中性色；只有星标仍是未处理
    const panel=loadPanel('zh',TAGS);
    const prog=new FakeEl('span'), star=new FakeEl('span'), state=new FakeEl('span');
    const reg={'.prog[data-prog-uid="u1"]':[prog],'.card-star[data-star-uid="u1"]':[star],'[data-state-uid="u1"]':[state]};
    panel.sandbox.document.querySelectorAll=sel=>reg[sel]||[];
    panel.api.LABELS.u1={action:'skipped',priority:1};
    panel.api.refreshDot('u1');
    check('跳过 + 星标：中性色、星标出现、状态行两者都写', prog.className==='prog mid' && star.hidden===false
      && state.textContent==='state.done · state.star', state.textContent);
    panel.api.LABELS.u1={action:null,priority:1,note:'x'};
    panel.api.refreshDot('u1');
    check('只有星标/备注：状态行仍是未处理', prog.hidden===true && star.hidden===false
      && state.textContent==='state.open · state.star', state.textContent);
    panel.api.LABELS.u1={action:'saved',priority:null};
    panel.api.refreshDot('u1');
    check('取消星标：卡片星标隐藏', star.hidden===true && prog.className==='prog want');
  }
  {
    const panel=loadPanel('zh',TAGS);
    Object.assign(panel.sandbox.window.__I18N__,{'run.state_unknown':'结果未记录（旧版本运行）',
      'js.update_unknown':'FULL','run.ago':'上次抓取 {ago}','ago.minutes':'{n} 分钟前'});
    const text=panel.api.relativeRunTime({state:'unknown',finished_at:new Date().toISOString(),task_id:'legacy-2'});
    check('状态行：旧记录显示「结果未记录」，不露任务 ID',
      text.endsWith(' · 结果未记录（旧版本运行）') && !text.includes('legacy-2'), text);
    const partial=panel.api.relativeRunTime({state:'partial',finished_at:new Date().toISOString()});
    check('没有短文案的状态退回完整说明', partial.endsWith(' · js.update_partial'), partial);
  }
}

async function main() {
  console.log("== 备注分隔符 / 默认标签本地化 JS 交互（合成数据，Node vm + 最小 DOM stub）==");
  console.log(`Node ${process.version}；加载 ${path.relative(ROOT, PANEL_JS)}`);
  await testPendingNoteEdits();
  await testRevertedPendingNote();
  await testChinese();
  await testEnglish();
  await testLocalizedDefaults();
  await testEnglishBoundarySpace();
  await testPrototypeKeys();
  testEmojiAndIme();
  await testUpdateRefreshWithDetailHash();
  await testUpdateConfirmAndProgress();
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
