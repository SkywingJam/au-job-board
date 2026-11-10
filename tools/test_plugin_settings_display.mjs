#!/usr/bin/env node
/* Settings > Plugins display: origin, install record and incompatible plugins as
   the client applies them from GET /api/plugins. Synthetic data, Node standard
   library and a small DOM stub; executes the real settings.js.

   Checks: an incompatible plugin shows one sentence and loses its switch and
   review entry; old-shape items (no new fields) behave as before; the review
   dialog shows the install record as text only (innerHTML/outerHTML are traps);
   a missing or damaged record renders nothing and breaks nothing.

   Usage: node tools/test_plugin_settings_display.mjs
*/
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SOURCE = fs.readFileSync(path.join(ROOT, "jobs", "panel_assets", "settings.js"), "utf8");
const failures = [];
function check(label, ok, detail = "") {
  console.log(`  [${ok ? "PASS" : "FAIL"}] ${label}${detail ? "  " + detail : ""}`);
  if (!ok) failures.push(label);
}
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
async function settle() { for (let i = 0; i < 20; i++) await tick(); }

const camel = (s) => s.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
function matches(el, simple) {
  const tag = /^[a-z0-9]+/i.exec(simple);
  if (tag && el.tag !== tag[0].toLowerCase()) return false;
  for (const cls of simple.match(/\.[\w-]+/g) || []) if (!el.className.split(/\s+/).includes(cls.slice(1))) return false;
  for (const attr of simple.match(/\[[\w-]+\]/g) || []) if (el.dataset[camel(attr.slice(6, -1))] === undefined) return false;
  return true;
}
class El {
  constructor(tag = "div") {
    Object.assign(this, { tag, dataset: {}, attrs: {}, listeners: {}, hidden: false, className: "", kids: [], _text: "",
      classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } } });
  }
  get textContent() { return this._text + this.kids.map((k) => k.textContent).join(""); }
  set textContent(v) { this._text = String(v); this.kids = []; }
  set innerHTML(_v) { throw new Error("innerHTML must never be used"); }
  set outerHTML(_v) { throw new Error("outerHTML must never be used"); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); }
  append(...nodes) { nodes.forEach((n) => this.kids.push(n)); }
  replaceChildren(...nodes) { this.kids = [...nodes]; this._text = ""; }
  focus() {}
  getClientRects() { return [1]; }
  all() { return this.kids.flatMap((k) => [k, ...k.all()]); }
  querySelectorAll(sel) {
    return this.all().filter((el) => sel.split(",").some((one) => {
      const chain = one.trim().split(/\s+/);
      return matches(el, chain[chain.length - 1]);
    }));
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  click() { (this.listeners.click || []).forEach((fn) => fn({})); }
}
const el = (tag, props = {}, kids = []) => { const e = Object.assign(new El(tag), props); e.kids = kids; return e; };
const reply = (body) => ({ ok: true, json: async () => body });
const T = (key, params) => (params ? key + "|" + JSON.stringify(params) : key);

function makeRow(item, withSwitch) {
  const toggle = el("button", { dataset: { pluginToggle: item.id, enabled: String(!!item.enabled) } });
  toggle.setAttribute("aria-checked", String(!!item.enabled));
  const state = el("small", { className: "plugin-state" });
  const count = el("small", { className: "plugin-error-count" });
  const review = el("button", { dataset: { pluginReview: item.id }, hidden: true });
  const message = el("p", { className: "plugin-row-message" });
  const row = el("div", { className: "plugin-row", dataset: { pluginId: item.id } },
    [state, count, ...(withSwitch ? [toggle] : []), review, message]);
  return { toggle, state, review, row, message };
}

function page(item, { withSwitch = true, others = [] } = {}) {
  const first = makeRow(item, withSwitch);
  const rest = others.map((o) => makeRow(o, true));
  const modal = el("div", { className: "modal" });
  const root = el("div", {}, [first.row, ...rest.map((r) => r.row)]);
  const document = {
    querySelectorAll: (sel) => root.querySelectorAll(sel),
    querySelector: (sel) => root.querySelector(sel),
    getElementById: (id) => (id === "plugin-consent" ? modal : null),
    addEventListener() {},
    createElement: (tag) => new El(tag),
    contains: () => true,
  };
  const fetch = async (url, options) => {
    if (options && options.method === "POST") throw new Error("this test never writes");
    return reply({ ok: true, digest: "d0", plugins: [item, ...others] });
  };
  const window = { __LANG__: "en", addEventListener() {} };
  const context = vm.createContext({ document, window, fetch, T, console, setTimeout, clearTimeout });
  vm.runInContext(SOURCE, context);
  return { ...first, modal, rest };
}

const base = { id: "sample", name: { en: "Sample" }, version: "1.0.0", enabled: true, status: "enabled", error_count: 0 };
const granted = { status: "granted", fingerprint: "f".repeat(64), permissions: [] };
const required = { status: "required", fingerprint: "e".repeat(64), permissions: [] };

console.log("-- incompatible plugin --");
{
  const item = { ...base, status: "incompatible", origin: "installed", consent: granted,
    compatibility: { host_api: 1, core: ">=9.0.0" }, incompatible: { core: "1.4.0", requires: ">=9.0.0" } };
  const p = page(item);
  await settle();
  check("one sentence with both versions", p.state.textContent === 'plugin.incompatible|{"requires":"plugin.range_min|{\\"a\\":\\"9.0.0\\"}","core":"1.4.0"}', p.state.textContent);
  check("sentence is visible and not an error tone", p.state.hidden === false);
  check("no switch", p.toggle.hidden === true);
  check("no review entry", p.review.hidden === true);
  check("does not say permission is required or granted",
    !/consent_(required|changed|granted)/.test(p.state.textContent) && p.state.dataset.consent === "");
}
{
  const p = page({ ...base, status: "incompatible", consent: required, incompatible: { core: 7 } });
  await settle();
  check("missing or damaged reason falls back to the generic sentence", p.state.textContent === "plugin.incompatible_generic", p.state.textContent);
  check("a plugin that needs consent is still not offered a review while incompatible", p.review.hidden === true);
}
{
  const other = { ...base, id: "other", consent: granted };
  const p = page({ ...base, status: "incompatible", origin: "installed", incompatible: { core: "1.4.0", requires: ">=2" } },
    { withSwitch: false, others: [other] });
  await settle();
  check("a row rendered without a switch is filled from the API and breaks nothing",
    p.state.textContent === 'plugin.incompatible|{"requires":"plugin.range_min|{\\"a\\":\\"2\\"}","core":"1.4.0"}', p.state.textContent);
  check("the other plugin on the page is still applied", p.rest[0].state.textContent === "plugin.consent_granted_short");
}
{
  const p = page({ ...base, status: "incompatible" }, { withSwitch: false });
  await settle();
  check("a page whose only row has no switch makes no client read and keeps the server text", p.state.textContent === "");
}
{
  const p = page({ ...base, status: "incompatible", origin: "installed", incompatible: { core: "1", requires: ">=2" } });
  p.toggle.attrs["aria-disabled"] = "false";
  await settle();
  p.toggle.click();               // a stale page: the switch is hidden, but a click must still be harmless
  await settle();
  check("a stale click on a now-incompatible plugin saves nothing", p.message.textContent === "" && p.toggle.getAttribute("aria-busy") === undefined);
}

console.log("-- old-shape and compatible items --");
{
  const p = page({ ...base, consent: granted });
  await settle();
  check("old-shape item keeps its granted state line", p.state.textContent === "plugin.consent_granted_short");
  check("old-shape item keeps its switch", p.toggle.hidden === false);
  check("granted item shows no review entry", p.review.hidden === true);
}
{
  const p = page({ ...base, enabled: false, status: "disabled", origin: "builtin", consent: required,
    compatibility: { host_api: 2, core: null } });
  await settle();
  check("builtin item needing consent still offers the review", p.review.hidden === false);
  check("builtin item shows the consent line", p.state.textContent === "plugin.consent_required_short");
}

console.log("-- review dialog shows the install record as text --");
const hostile = '<img src=x onerror=alert(1)>';
async function dialogFor(item) {
  const p = page(item);
  await settle();
  p.review.click();
  await settle();
  return p;
}
{
  const record = { version: "1.0.0", sha256: "0123456789abcdef".repeat(4), source: hostile, installed_at: "2026-11-10T01:02:03+00:00" };
  const p = await dialogFor({ ...base, enabled: false, status: "disabled", origin: "installed", install: record, consent: required });
  const text = p.modal.textContent;
  check("dialog opened", p.modal.kids.length === 1);
  check("origin is in the dialog meta line", /1\.0\.0.*plugin\.origin_installed.*sample/.test(text.replace(/plugin\.version\|\{"version":"1\.0\.0"\}/, "1.0.0")));
  check("hostile source is kept as text", text.includes(hostile));
  check("only the first 12 characters of the hash", text.includes("0123456789ab") && !text.includes("0123456789abc"));
  check("only the date of the install time", text.includes("2026-11-10") && !text.includes("01:02:03"));
  const dds = p.modal.querySelectorAll("dd").map((d) => d.className);
  check("hash is marked for monospace", dds.includes("plugin-sha"));
}
{
  const p = await dialogFor({ ...base, enabled: false, status: "disabled", origin: "installed", install: null, consent: required });
  check("installed without a record: dialog opens, no install section", p.modal.kids.length === 1 && !p.modal.textContent.includes("plugin.provenance_title"));
}
{
  const p = await dialogFor({ ...base, enabled: false, status: "disabled", origin: "installed",
    install: { source: 5, sha256: 7, installed_at: { x: 1 } }, consent: required });
  check("damaged record renders nothing", p.modal.kids.length === 1 && !p.modal.textContent.includes("plugin.provenance_title"));
}
{
  const p = await dialogFor({ ...base, enabled: false, status: "disabled", origin: "builtin", consent: required });
  check("builtin dialog has no install section", !p.modal.textContent.includes("plugin.provenance_title") && p.modal.textContent.includes("plugin.origin_builtin"));
}
{
  const p = await dialogFor({ ...base, enabled: false, status: "disabled", consent: required });
  check("old-shape dialog is unchanged: no origin, no install section",
    !p.modal.textContent.includes("plugin.origin_") && !p.modal.textContent.includes("plugin.provenance_title"));
}
{
  const long = "https://example.invalid/" + "a".repeat(500);
  const p = await dialogFor({ ...base, enabled: false, status: "disabled", origin: "installed", install: { source: long }, consent: required });
  check("a very long source is cut", !p.modal.textContent.includes("a".repeat(300)));
}

if (failures.length) { console.log(`\n${failures.length} failed`); process.exit(1); }
console.log("\nall passed");
