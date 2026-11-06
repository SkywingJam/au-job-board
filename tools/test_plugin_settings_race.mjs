#!/usr/bin/env node
/* Plugin settings: a slow initial GET /api/plugins must not overwrite a switch
   the user already saved on this page. Synthetic data, Node standard library
   and a minimal DOM stub; executes the real settings.js.

   Usage: node tools/test_plugin_settings_race.mjs
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

class El {
  constructor(props = {}) {
    Object.assign(this, { dataset: {}, attrs: {}, listeners: {}, hidden: false, textContent: "",
      classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } }, children: {} });
    Object.assign(this, props);
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); }
  querySelector(sel) { return this.children[sel] || null; }
  querySelectorAll() { return []; }
  click() { (this.listeners.click || []).forEach((fn) => fn({})); }
}

function item(enabled) {
  return { id: "sample", enabled, status: enabled ? "enabled" : "disabled", error_count: 0,
           consent: { status: "granted", fingerprint: "f".repeat(64), permissions: [] } };
}
const reply = (body) => ({ ok: true, json: async () => body });

async function scenario({ toggleFirst }) {
  const button = new El({ dataset: { pluginToggle: "sample", enabled: "true" } });
  button.setAttribute("aria-checked", "true");
  const row = new El({ dataset: { pluginId: "sample" } });
  row.classList.add = () => {};
  row.children = { "[data-plugin-toggle]": button, ".plugin-state": new El(), ".plugin-row-message": new El() };
  const document = {
    querySelectorAll(sel) {
      if (sel === "[data-plugin-toggle]" || sel === "[data-plugin-toggle],[data-plugin-detail]") return [button];
      if (sel === ".plugin-row") return [row];
      return [];
    },
    querySelector(sel) { return sel === "[data-plugin-toggle],[data-plugin-detail]" ? button : null; },
    getElementById() { return null; },
    addEventListener() {},
    createElement() { return new El(); },
  };
  // The page-load GET is held back until the end; later requests answer at once.
  let releaseInitial;
  const initial = new Promise((resolve) => { releaseInitial = () => resolve(reply({ ok: true, digest: "d0", plugins: [item(true)] })); });
  let gets = 0;
  const fetch = async (url, options) => {
    if (options && options.method === "POST") {
      const body = JSON.parse(options.body);
      return reply({ ok: true, plugins: [item(body.enabled)] });
    }
    gets += 1;
    return gets === 1 ? initial : reply({ ok: true, digest: "d1", plugins: [item(true)] });
  };
  const window = { __LANG__: "en", addEventListener() {} };
  const context = vm.createContext({ document, window, fetch, T: (key) => key, console, setTimeout, clearTimeout });
  vm.runInContext(SOURCE, context);
  if (toggleFirst) {
    button.click();             // turn the enabled plugin off; GET + POST complete
    await settle();
  }
  releaseInitial();             // the original page-load read finally answers "enabled"
  await settle();
  return button;
}

console.log("-- plugin settings: stale initial read --");
const saved = await scenario({ toggleFirst: true });
check("saved 'off' survives a late initial read", saved.dataset.enabled === "false", `enabled=${saved.dataset.enabled}`);
check("switch state stays unchecked", saved.getAttribute("aria-checked") === "false", `aria-checked=${saved.getAttribute("aria-checked")}`);
const untouched = await scenario({ toggleFirst: false });
check("without a change the initial read still applies", untouched.dataset.enabled === "true");

if (failures.length) { console.log(`\n${failures.length} failed`); process.exit(1); }
console.log("\nall passed");
