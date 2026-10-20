/* A tiny DOM shim + smoke test for the web UI's JavaScript.
 *
 * Run with:  node tests/frontend_smoke.mjs
 *
 * It stubs just enough of the browser to load app.js, exercise the render
 * paths and check that nothing throws. That catches the kind of bug a
 * Python test cannot see -- a renamed variable, a typo in an element id.
 */

import fs from "node:fs";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const source = fs.readFileSync(path.join(here, "../minidb/webui/static/app.js"), "utf8");

/* ------------------------------------------------------------------ */
/* the shim                                                            */
/* ------------------------------------------------------------------ */

class ClassList {
  constructor(element) {
    this.element = element;
    this.set = new Set();
  }
  add(...names) { names.forEach((n) => this.set.add(n)); }
  remove(...names) { names.forEach((n) => this.set.delete(n)); }
  contains(name) { return this.set.has(name); }
  toggle(name, force) {
    const on = force === undefined ? !this.set.has(name) : force;
    if (on) this.set.add(name);
    else this.set.delete(name);
    return on;
  }
}

class Element {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.listeners = {};
    this.classList = new ClassList(this);
    this.dataset = {};
    this.style = {};
    this.parent = null;
    this._text = "";
    this._html = "";
    this.value = "";
  }
  get textContent() { return this._text; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get innerHTML() { return this._html; }
  set innerHTML(value) { this._html = String(value); }
  // The browser keeps className and classList in step; so does this.
  get className() { return [...this.classList.set].join(" "); }
  set className(value) {
    this.classList.set.clear();
    String(value)
      .split(/\s+/)
      .filter(Boolean)
      .forEach((name) => this.classList.set.add(name));
  }
  append(...nodes) { this.children.push(...nodes); }
  appendChild(node) { this.children.push(node); return node; }
  addEventListener(name, handler) {
    (this.listeners[name] ||= []).push(handler);
  }
  closest(selector) {
    if (selector === ".tab" && this.classList.contains("tab")) return this;
    return null;
  }
  click() {
    // Events bubble, so a click on a child also reaches its ancestors.
    let node = this;
    while (node) {
      for (const handler of node.listeners.click || []) handler({ target: this });
      node = node.parent;
    }
  }
  containsText(text) {
    if (this._text.includes(text)) return true;
    return this.children.some((child) => child.containsText?.(text));
  }
}

const elements = new Map();
const tabs = [];

function makeElement(tag) {
  return new Element(tag);
}

const layout = new Element("div");
layout.classList.add("layout");

// The tab buttons live in index.html, which this shim does not parse, so
// they are created up front.
const tabBar = new Element("div");
tabBar.id = "tabs";
for (const name of ["browse", "query", "aggregate", "insert", "info"]) {
  const tab = new Element("button");
  tab.classList.add("tab");
  tab.dataset.tab = name;
  tab.parent = tabBar;
  tabs.push(tab);
}

const documentShim = {
  getElementById(id) {
    if (id === "tabs") return tabBar;
    if (!elements.has(id)) {
      const element = makeElement("div");
      element.id = id;
      elements.set(id, element);
    }
    return elements.get(id);
  },
  createElement(tag) {
    return makeElement(tag);
  },
  querySelector(selector) {
    if (selector === ".layout") return layout;
    return documentShim.getElementById(selector);
  },
  querySelectorAll(selector) {
    if (selector === ".tab") return tabs;
    if (selector === ".panel") {
      return ["panel-browse", "panel-query", "panel-aggregate", "panel-insert"].map(
        (id) => documentShim.getElementById(id)
      );
    }
    return [];
  },
  addEventListener() {},
};

/* ------------------------------------------------------------------ */
/* the fake server                                                     */
/* ------------------------------------------------------------------ */

const DATABASES = [
  {
    name: "shop",
    path: "/tmp/shop.db",
    collections: [
      {
        name: "orders",
        count: 2,
        indexes: [{ name: "_id_", key: { _id: 1 }, unique: true }],
      },
    ],
  },
  {
    name: "notes",
    path: "/tmp/notes.db",
    collections: [
      {
        name: "ideas",
        count: 1,
        indexes: [{ name: "_id_", key: { _id: 1 }, unique: true }],
      },
    ],
  },
];

const DOCUMENTS = [
  { name: "Ada", age: 36, _id: { $oid: "abc123" } },
  { name: "Alan", age: 41, _id: { $oid: "def456" } },
];

const COLLECTION_STATS = {
  count: 2,
  dataSize: 2048,
  indexCount: 2,
  indexes: [
    { name: "_id_", key: { _id: 1 }, unique: true },
    { name: "email_1", key: { email: 1 }, unique: true },
  ],
  schema: [
    { name: "age", types: ["int"], count: 2, optional: false },
    { name: "name", types: ["string"], count: 1, optional: true },
  ],
  validator: null,
};

async function fakeFetch(url) {
  const route = url.split("?")[0];

  if (route === "/api/info") {
    return {
      ok: true,
      status: 200,
      text: async () =>
        JSON.stringify({ roots: ["/tmp"], workspace: "/tmp/registry.json", version: "0.1.0" }),
    };
  }
  if (route === "/api/databases") {
    return { ok: true, status: 200, text: async () => JSON.stringify(DATABASES) };
  }
  if (route === "/api/databases/shop") {
    return { ok: true, status: 200, text: async () => JSON.stringify(DATABASES[0]) };
  }
  if (route === "/api/databases/shop/collections/orders") {
    return {
      ok: true,
      status: 200,
      text: async () =>
        JSON.stringify({
          name: "orders",
          count: 2,
          indexes: [{ name: "_id_", key: { _id: 1 }, unique: true }],
          validator: null,
        }),
    };
  }
  if (route.endsWith("/stats")) {
    return { ok: true, status: 200, text: async () => JSON.stringify(COLLECTION_STATS) };
  }
  if (route.endsWith("/query")) {
    return {
      ok: true,
      status: 200,
      text: async () =>
        JSON.stringify({ documents: DOCUMENTS, count: DOCUMENTS.length, total: 2, milliseconds: 1 }),
    };
  }
  if (route.endsWith("/aggregate")) {
    return {
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ documents: DOCUMENTS, count: 2, milliseconds: 1 }),
    };
  }
  if (route.endsWith("/explain")) {
    return {
      ok: true,
      status: 200,
      text: async () =>
        JSON.stringify({
          strategy: "IXSCAN",
          index: "email_1",
          documentsExamined: 1,
          totalDocuments: 2,
          documentsMatched: 1,
        }),
    };
  }
  return { ok: false, status: 404, text: async () => JSON.stringify({ error: "not found" }) };
}

/* ------------------------------------------------------------------ */
/* run                                                                 */
/* ------------------------------------------------------------------ */

const failures = [];

const el = (id) => documentShim.getElementById(id);

function check(name, condition, detail = "") {
  if (condition) {
    console.log(`  ok   ${name}`);
  } else {
    failures.push(`${name}${detail ? ` -- ${detail}` : ""}`);
    console.log(`  FAIL ${name}${detail ? ` -- ${detail}` : ""}`);
  }
}

const sandbox = {
  document: documentShim,
  window: { confirm: () => true, location: { search: "" } },
  fetch: fakeFetch,
  setTimeout,
  clearTimeout,
  console,
  JSON,
  Object,
  Number,
  String,
  Set,
  Math,
  Error,
  URLSearchParams,
};
sandbox.globalThis = sandbox;

const context = vm.createContext(sandbox);
try {
  vm.runInContext(source, context, { filename: "app.js" });
} catch (error) {
  console.log(`  FAIL app.js threw while loading: ${error.stack}`);
  process.exit(1);
}

// Let boot() finish.
await new Promise((resolve) => setTimeout(resolve, 50));

console.log("\nfrontend smoke test");

check("the workspace path is shown", el("db-path").textContent === "/tmp");
check("the version is shown", el("version").textContent === "v0.1.0");
const databaseItems = () =>
  el("collection-list").children.filter((child) => child.classList.contains("database"));

check(
  "the databases are listed",
  databaseItems().length === 2,
  `got ${databaseItems().length}`
);
check(
  "a database is selected",
  el("collection-title").textContent === "orders",
  el("collection-title").textContent
);
check(
  "documents are rendered",
  el("results").children.length === 2,
  `got ${el("results").children.length}`
);
check(
  "a document shows its name",
  el("results").children[0].containsText("Ada")
);
check(
  "the breadcrumb shows the database",
  el("crumbs").containsText("shop")
);
check(
  "the indexes are listed",
  el("index-list").children.length === 1,
  `got ${el("index-list").children.length}`
);

// Click a document card: the drawer should open.
el("results").children[0].click();
check("the drawer opens", !el("drawer").classList.contains("hidden"));
check(
  "the drawer shows the document",
  el("drawer-json").innerHTML.includes("Ada")
);
check(
  "the drawer highlights the JSON",
  el("drawer-json").innerHTML.includes('class="key"')
);

// The raw/highlight toggle.
el("drawer-toggle").click();
check(
  "the raw view shows plain JSON",
  el("drawer-json").textContent.includes('"name": "Ada"')
);
el("drawer-toggle").click();
check(
  "the highlighted view comes back",
  el("drawer-json").innerHTML.includes('class="key"')
);

// Switching tabs.
tabs[1].click();
check("the query panel opens", !el("panel-query").classList.contains("hidden"));
check("the browse panel closes", el("panel-browse").classList.contains("hidden"));

// The info tab: statistics, inferred schema and indexes.
tabs[4].click();
await new Promise((resolve) => setTimeout(resolve, 30));
check("the info panel opens", !el("panel-info").classList.contains("hidden"));
check(
  "the statistics are shown",
  el("stats").children.length === 4,
  `got ${el("stats").children.length}`
);
check(
  "the schema is listed",
  el("schema-body").children.length === 2,
  `got ${el("schema-body").children.length}`
);
check(
  "the indexes are listed",
  el("indexes-body").children.length === 2,
  `got ${el("indexes-body").children.length}`
);

// The explain button shows the query plan.
el("browse-explain").click();
await new Promise((resolve) => setTimeout(resolve, 30));
check("the plan is shown", !el("plan").classList.contains("hidden"));
check(
  "the plan names the index",
  el("plan").children.some((child) => child.children.some((part) => part._text === "email_1"))
);

console.log("");
if (failures.length) {
  console.log(`${failures.length} check(s) failed`);
  process.exit(1);
}
console.log("all checks passed");
