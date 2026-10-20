/* minidb web UI */

const state = {
  collections: [],
  collection: null,
  documents: [],
  selected: null,
  mode: "browse",
  rawJson: false,
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await response.text();
  let payload;
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    payload = { error: text };
  }
  if (!response.ok) {
    throw new Error(payload.error || `request failed (${response.status})`);
  }
  return payload;
}

function status(message, kind = "info") {
  const element = $("status");
  element.textContent = message;
  element.className = `status ${kind}`;
  if (kind === "ok") {
    clearTimeout(status.timer);
    status.timer = setTimeout(() => {
      element.textContent = "";
      element.className = "status";
    }, 4000);
  }
}

/* ---------- rendering ---------- */

function highlight(value) {
  const json = JSON.stringify(value, null, 2) ?? "null";
  return json.replace(
    /("(?:\\.|[^"\\])*"\s*:)|("(?:\\.|[^"\\])*")|(\b-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)|(\btrue\b|\bfalse\b)|(\bnull\b)|([{}[\]])/g,
    (match, key, string, number, bool, nil, bracket) => {
      if (key) return `<span class="key">${key}</span>`;
      if (string) return `<span class="string">${string}</span>`;
      if (number) return `<span class="number">${number}</span>`;
      if (bool) return `<span class="bool">${bool}</span>`;
      if (nil) return `<span class="null">${nil}</span>`;
      return `<span class="bracket">${bracket}</span>`;
    }
  );
}

function displayValue(value) {
  if (value === null || value === undefined) return "null";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function valueClass(value) {
  if (typeof value === "number") return "number";
  if (typeof value === "boolean") return "bool";
  if (typeof value === "string") return "string";
  return "";
}

function documentTitle(doc) {
  // A document has no required title, so fall back to whatever reads best.
  for (const key of ["title", "name", "body", "slug", "email", "label"]) {
    const value = doc[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  for (const [key, value] of Object.entries(doc)) {
    if (key === "_id") continue;
    if (typeof value === "string" && value.trim()) return value;
  }
  const identifier = doc._id?.$oid;
  return identifier ? identifier.slice(-8) : "(untitled)";
}

function renderResults(documents, meta = {}) {
  const container = $("results");
  container.textContent = "";

  if (!documents.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No documents matched.";
    container.appendChild(empty);
    return;
  }

  for (const doc of documents) {
    const card = document.createElement("div");
    card.className = "doc";

    const head = document.createElement("div");
    head.className = "doc-head";

    const title = document.createElement("span");
    title.className = "doc-title";
    title.textContent = documentTitle(doc);
    head.appendChild(title);

    const id = document.createElement("span");
    id.className = "doc-id";
    id.textContent = doc._id && doc._id.$oid ? doc._id.$oid : "";
    head.appendChild(id);
    card.appendChild(head);

    const fields = document.createElement("div");
    fields.className = "doc-fields";

    const skipped = new Set(["_id", "title", "name"]);
    let shown = 0;
    for (const [key, value] of Object.entries(doc)) {
      if (skipped.has(key)) continue;
      if (shown >= 6) break;
      shown += 1;

      const chip = document.createElement("span");
      chip.className = "field-chip";

      const name = document.createElement("span");
      name.className = "k";
      name.textContent = key;

      const item = document.createElement("span");
      item.className = `v ${valueClass(value)}`;
      item.textContent = displayValue(value);

      chip.append(name, item);
      fields.appendChild(chip);
    }
    if (shown === 0) {
      const chip = document.createElement("span");
      chip.className = "field-chip";
      const name = document.createElement("span");
      name.className = "k";
      name.textContent = "_id";
      const item = document.createElement("span");
      item.className = "v string";
      item.textContent = doc._id?.$oid ?? "";
      chip.append(name, item);
      fields.appendChild(chip);
    }
    card.appendChild(fields);

    card.addEventListener("click", () => openDrawer(doc));
    container.appendChild(card);
  }
}

function renderCollections() {
  const list = $("collection-list");
  list.textContent = "";

  for (const collection of state.collections) {
    const item = document.createElement("li");
    if (state.collection === collection.name) item.classList.add("active");

    const name = document.createElement("span");
    name.className = "name";
    name.textContent = collection.name;

    const count = document.createElement("span");
    count.className = "count";
    count.textContent = collection.count;

    item.append(name, count);
    item.addEventListener("click", () => selectCollection(collection.name));
    list.appendChild(item);
  }
}

function renderIndexes() {
  const list = $("index-list");
  list.textContent = "";

  const current = state.collections.find((c) => c.name === state.collection);
  const indexes = current?.indexes ?? [];

  if (!indexes.length) {
    const item = document.createElement("li");
    item.textContent = "none";
    list.appendChild(item);
    return;
  }

  for (const index of indexes) {
    const item = document.createElement("li");
    const fields = Object.entries(index.key || {})
      .map(([field, direction]) => `${field}:${direction}`)
      .join(", ");
    item.textContent = `${index.name}  (${fields})`;
    if (index.unique) {
      const badge = document.createElement("span");
      badge.className = "unique";
      badge.textContent = "  unique";
      item.appendChild(badge);
    }
    list.appendChild(item);
  }
}

/* ---------- actions ---------- */

async function loadInfo() {
  const info = await api("/api/info");
  $("db-path").textContent = info.path;
  $("version").textContent = `v${info.version}`;
}

async function loadCollections() {
  state.collections = await api("/api/collections");
  renderCollections();
  renderIndexes();
}

async function selectCollection(name) {
  state.collection = name;
  renderCollections();
  renderIndexes();

  $("collection-title").textContent = name;
  const info = await api(`/api/collections/${encodeURIComponent(name)}`);
  $("collection-meta").textContent =
    `${info.count} document${info.count === 1 ? "" : "s"} · ` +
    `${info.indexes.length} index${info.indexes.length === 1 ? "" : "es"}`;

  await runBrowse();
}

function parseJson(text, fallback) {
  const trimmed = (text || "").trim();
  if (!trimmed) return fallback;
  try {
    return JSON.parse(trimmed);
  } catch (error) {
    throw new Error(`that is not valid JSON: ${error.message}`);
  }
}

function parseSort(text) {
  const trimmed = (text || "").trim();
  if (!trimmed) return null;
  if (trimmed.startsWith("-")) return [[trimmed.slice(1), -1]];
  if (trimmed.startsWith("+")) return [[trimmed.slice(1), 1]];
  return [[trimmed, 1]];
}

async function runBrowse() {
  if (!state.collection) return;
  try {
    const filter = parseJson($("browse-filter").value, {});
    const sort = parseSort($("browse-sort").value);
    const limit = Number($("browse-limit").value) || 50;

    const result = await api(
      `/api/collections/${encodeURIComponent(state.collection)}/query`,
      {
        method: "POST",
        body: JSON.stringify({ filter, sort, limit }),
      }
    );
    state.documents = result.documents;
    renderResults(result.documents, result);
    status(
      `${result.count} of ${result.total} documents · ${result.milliseconds} ms`,
      "info"
    );
  } catch (error) {
    status(error.message, "error");
  }
}

async function runQuery() {
  if (!state.collection) return;
  try {
    const filter = parseJson($("query-filter").value, {});
    const projection = parseJson($("query-projection").value, null);
    const sort = parseSort($("query-sort").value);
    const limit = Number($("query-limit").value) || 50;
    const skip = Number($("query-skip").value) || 0;

    const result = await api(
      `/api/collections/${encodeURIComponent(state.collection)}/query`,
      {
        method: "POST",
        body: JSON.stringify({ filter, projection, sort, limit, skip }),
      }
    );
    state.documents = result.documents;
    renderResults(result.documents, result);
    status(
      `${result.count} of ${result.total} documents · ${result.milliseconds} ms`,
      "info"
    );
  } catch (error) {
    status(error.message, "error");
  }
}

async function runAggregate() {
  if (!state.collection) return;
  try {
    const pipeline = parseJson($("aggregate-pipeline").value, []);
    if (!Array.isArray(pipeline)) throw new Error("a pipeline must be an array");

    const result = await api(
      `/api/collections/${encodeURIComponent(state.collection)}/aggregate`,
      { method: "POST", body: JSON.stringify({ pipeline }) }
    );
    state.documents = result.documents;
    renderResults(result.documents, result);
    status(`${result.count} results · ${result.milliseconds} ms`, "info");
  } catch (error) {
    status(error.message, "error");
  }
}

async function runInsert() {
  if (!state.collection) return;
  try {
    const document = parseJson($("insert-document").value, {});
    const result = await api(
      `/api/collections/${encodeURIComponent(state.collection)}/insert`,
      { method: "POST", body: JSON.stringify({ document }) }
    );
    status(`inserted ${result.insertedId?.$oid ?? ""}`, "ok");
    await loadCollections();
    await selectCollection(state.collection);
  } catch (error) {
    status(error.message, "error");
  }
}

function openDrawer(doc) {
  state.selected = doc;
  state.rawJson = false;
  $("drawer-title").textContent = documentTitle(doc);
  $("drawer-json").innerHTML = highlight(doc);
  $("drawer-toggle").textContent = "{}";
  $("drawer").classList.remove("hidden");
  document.querySelector(".layout").classList.add("with-drawer");
}

function toggleRawJson() {
  if (!state.selected) return;
  const body = $("drawer-json");
  state.rawJson = !state.rawJson;
  if (state.rawJson) {
    body.textContent = JSON.stringify(state.selected, null, 2);
    $("drawer-toggle").textContent = "highlight";
  } else {
    body.innerHTML = highlight(state.selected);
    $("drawer-toggle").textContent = "{}";
  }
}

function closeDrawer() {
  state.selected = null;
  $("drawer").classList.add("hidden");
  document.querySelector(".layout").classList.remove("with-drawer");
}

async function deleteSelected() {
  if (!state.selected?._id?.$oid) return;
  const id = state.selected._id.$oid;
  if (!window.confirm(`Delete ${id}?`)) return;

  try {
    await api(
      `/api/collections/${encodeURIComponent(state.collection)}/documents/${id}`,
      { method: "DELETE" }
    );
    status("deleted", "ok");
    closeDrawer();
    await loadCollections();
    await selectCollection(state.collection);
  } catch (error) {
    status(error.message, "error");
  }
}

/* ---------- wiring ---------- */

function switchTab(mode) {
  state.mode = mode;
  for (const tab of document.querySelectorAll(".tab")) {
    tab.classList.toggle("active", tab.dataset.tab === mode);
  }
  for (const panel of document.querySelectorAll(".panel")) {
    panel.classList.toggle("hidden", panel.id !== `panel-${mode}`);
  }
}

async function boot() {
  try {
    await loadInfo();
    await loadCollections();
    if (state.collections.length) {
      await selectCollection(state.collections[0].name);
    } else {
      $("collection-title").textContent = "This database is empty";
      $("collection-meta").textContent =
        "Create a collection, then refresh this page";
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "No collections yet.";
      $("results").appendChild(empty);
    }
  } catch (error) {
    status(error.message, "error");
  }
}

$("tabs").addEventListener("click", (event) => {
  const tab = event.target.closest(".tab");
  if (tab) switchTab(tab.dataset.tab);
});
$("browse-run").addEventListener("click", runBrowse);
$("query-run").addEventListener("click", runQuery);
$("aggregate-run").addEventListener("click", runAggregate);
$("insert-run").addEventListener("click", runInsert);
$("browse-filter").addEventListener("keydown", (event) => {
  if (event.key === "Enter") runBrowse();
});
$("query-filter").addEventListener("keydown", (event) => {
  if (event.key === "Enter") runQuery();
});
$("drawer-close").addEventListener("click", closeDrawer);
$("drawer-toggle").addEventListener("click", toggleRawJson);
$("drawer-delete").addEventListener("click", deleteSelected);
$("refresh-all").addEventListener("click", () => boot());

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeDrawer();
});

boot();
