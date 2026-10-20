/* minidb web UI */

const state = {
  databases: [],
  database: null,
  collection: null,
  documents: [],
  selected: null,
  mode: "browse",
  rawJson: false,
  expanded: {},
};

const $ = (id) => document.getElementById(id);

// The path of a collection, for the API.
function collectionPath() {
  return `/api/databases/${encodeURIComponent(state.database)}/collections/${encodeURIComponent(
    state.collection
  )}`;
}

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

  if (!state.databases.length) {
    const empty = document.createElement("li");
    empty.className = "muted small";
    empty.textContent = "no databases found";
    list.appendChild(empty);
    return;
  }

  for (const database of state.databases) {
    const item = document.createElement("li");
    item.className = "database";

    const head = document.createElement("div");
    head.className = "database-head";

    const caret = document.createElement("span");
    caret.className = "caret";
    caret.textContent = state.expanded[database.name] ? "▾" : "▸";

    const name = document.createElement("span");
    name.className = "name";
    name.textContent = database.name;

    const count = document.createElement("span");
    count.className = "count";
    count.textContent = database.collections.length;

    head.append(caret, name, count);

    const title = document.createElement("span");
    title.className = "db-path";
    title.textContent = database.path;

    item.append(head, title);
    head.addEventListener("click", () => toggleDatabase(database.name));
    list.appendChild(item);

    if (state.expanded[database.name]) {
      const children = document.createElement("ul");
      children.className = "collection-children";

      for (const collection of database.collections) {
        const child = document.createElement("li");
        const childName = document.createElement("span");
        childName.className = "name";
        childName.textContent = collection.name;
        const childCount = document.createElement("span");
        childCount.className = "count";
        childCount.textContent = collection.count;
        child.append(childName, childCount);

        if (
          state.database === database.name &&
          state.collection === collection.name
        ) {
          child.classList.add("active");
        }
        child.addEventListener("click", () =>
          selectCollection(database.name, collection.name)
        );
        children.appendChild(child);
      }
      list.appendChild(children);
    }
  }
}

function toggleDatabase(name) {
  state.expanded[name] = !state.expanded[name];
  renderCollections();
}

function renderIndexes() {
  const list = $("index-list");
  list.textContent = "";

  const database = state.databases.find((d) => d.name === state.database);
  const collection = database?.collections.find((c) => c.name === state.collection);
  const indexes = collection?.indexes ?? [];

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
  $("db-path").textContent = info.roots.join(", ");
  $("version").textContent = `v${info.version}`;
}

async function loadDatabases() {
  state.databases = await api("/api/databases");
  renderCollections();
  renderIndexes();
}

async function selectCollection(databaseName, collectionName) {
  state.database = databaseName;
  state.collection = collectionName;
  state.expanded[databaseName] = true;
  renderCollections();
  renderIndexes();

  $("collection-title").textContent = collectionName;
  const info = await api(
    `/api/databases/${encodeURIComponent(databaseName)}/collections/${encodeURIComponent(
      collectionName
    )}`
  );
  $("collection-meta").textContent =
    `${info.count} document${info.count === 1 ? "" : "s"} · ` +
    `${info.indexes.length} index${info.indexes.length === 1 ? "" : "es"}`;

  const crumbs = $("crumbs");
  crumbs.textContent = "";
  for (const [index, part] of [databaseName, collectionName].entries()) {
    if (index) {
      const separator = document.createElement("span");
      separator.className = "sep";
      separator.textContent = "/";
      crumbs.appendChild(separator);
    }
    const crumb = document.createElement("span");
    crumb.className = "crumb";
    crumb.textContent = part;
    crumbs.appendChild(crumb);
  }

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
      `${collectionPath()}/query`,
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
      `${collectionPath()}/query`,
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
      `${collectionPath()}/aggregate`,
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
      `${collectionPath()}/insert`,
      { method: "POST", body: JSON.stringify({ document }) }
    );
    status(`inserted ${result.insertedId?.$oid ?? ""}`, "ok");
    await loadDatabases();
    await selectCollection(state.database, state.collection);
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
      `${collectionPath()}/documents/${id}`,
      { method: "DELETE" }
    );
    status("deleted", "ok");
    closeDrawer();
    await loadDatabases();
    await selectCollection(state.database, state.collection);
  } catch (error) {
    status(error.message, "error");
  }
}

async function addDatabase() {
  const path = window.prompt("Open a database file:");
  if (!path) return;
  try {
    state.databases = await api("/api/databases/remember", {
      method: "POST",
      body: JSON.stringify({ path: path.trim() }),
    });
    renderCollections();
    status("opened", "ok");
  } catch (error) {
    status(error.message, "error");
  }
}

/* ---------- wiring ---------- */

async function runExplain() {
  if (!state.collection) return;
  try {
    const filter = parseJson(
      state.mode === "query" ? $("query-filter").value : $("browse-filter").value,
      {}
    );
    const plan = await api(
      `${collectionPath()}/explain`,
      { method: "POST", body: JSON.stringify({ filter }) }
    );
    renderPlan(plan);
  } catch (error) {
    status(error.message, "error");
  }
}

function renderPlan(plan) {
  const panel = $("plan");
  panel.textContent = "";

  const items = [
    ["strategy", plan.strategy, plan.strategy === "IXSCAN" ? "good" : "warn"],
    ["index", plan.index || "none", ""],
    ["examined", `${plan.documentsExamined} of ${plan.totalDocuments}`, ""],
    ["matched", plan.documentsMatched, "good"],
  ];

  for (const [label, value, kind] of items) {
    const item = document.createElement("div");
    item.className = "plan-item";

    const caption = document.createElement("span");
    caption.className = "plan-label";
    caption.textContent = label;

    const text = document.createElement("span");
    text.className = `plan-value ${kind}`;
    text.textContent = value;

    item.append(caption, text);
    panel.appendChild(item);
  }

  panel.classList.remove("hidden");
}

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "kB", "MB", "GB"];
  let size = bytes;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

async function loadCollectionInfo() {
  if (!state.collection) return;
  const stats = await api(`${collectionPath()}/stats`);

  const summary = $("stats");
  summary.textContent = "";
  const figures = [
    ["Documents", stats.count],
    ["Data size", formatBytes(stats.dataSize)],
    ["Indexes", stats.indexCount],
    ["Fields", stats.schema.length],
  ];
  for (const [label, value] of figures) {
    const stat = document.createElement("div");
    stat.className = "stat";
    const number = document.createElement("span");
    number.className = "value";
    number.textContent = value;
    const caption = document.createElement("span");
    caption.className = "label";
    caption.textContent = label;
    stat.append(number, caption);
    summary.appendChild(stat);
  }

  const schemaBody = $("schema-body");
  schemaBody.textContent = "";
  for (const field of stats.schema) {
    const row = document.createElement("tr");

    const name = document.createElement("td");
    name.textContent = field.name;

    const type = document.createElement("td");
    type.className = "type";
    type.textContent = field.types.join(" | ");

    const present = document.createElement("td");
    present.className = field.optional ? "optional" : "required";
    present.textContent = field.optional
      ? `${field.count} of ${stats.count}`
      : "always";

    row.append(name, type, present);
    schemaBody.appendChild(row);
  }

  const indexesBody = $("indexes-body");
  indexesBody.textContent = "";
  for (const index of stats.indexes) {
    const row = document.createElement("tr");

    const name = document.createElement("td");
    name.textContent = index.name;

    const key = document.createElement("td");
    key.className = "type";
    key.textContent = Object.entries(index.key || {})
      .map(([field, direction]) => `${field}: ${direction}`)
      .join(", ");

    const unique = document.createElement("td");
    unique.className = index.unique ? "required" : "optional";
    unique.textContent = index.unique ? "yes" : "no";

    row.append(name, key, unique);
    indexesBody.appendChild(row);
  }

  const validatorSection = $("validator-section");
  if (stats.validator) {
    validatorSection.classList.remove("hidden");
    $("validator-json").innerHTML = highlight(stats.validator);
  } else {
    validatorSection.classList.add("hidden");
  }
}

function switchTab(mode) {
  state.mode = mode;
  for (const tab of document.querySelectorAll(".tab")) {
    tab.classList.toggle("active", tab.dataset.tab === mode);
  }
  for (const panel of document.querySelectorAll(".panel")) {
    panel.classList.toggle("hidden", panel.id !== `panel-${mode}`);
  }
  if (mode === "info") {
    loadCollectionInfo().catch((error) => status(error.message, "error"));
  }
}

async function boot() {
  try {
    await loadInfo();
    await loadDatabases();

    // Deep links: ?database=app&collection=users&tab=info
    const params = new URLSearchParams(window.location.search);
    const wantedDatabase = params.get("database");
    const wantedCollection = params.get("collection");
    const tab = params.get("tab");

    const database =
      (wantedDatabase &&
        state.databases.find((d) => d.name === wantedDatabase)) ||
      state.databases[0];

    if (database) {
      state.expanded[database.name] = true;
      const collection =
        (wantedCollection &&
          database.collections.find((c) => c.name === wantedCollection)) ||
        database.collections[0];

      if (collection) {
        await selectCollection(database.name, collection.name);
        if (tab) switchTab(tab);
      } else {
        $("collection-title").textContent = database.name;
        $("collection-meta").textContent = "This database has no collections yet.";
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = "No collections yet.";
        $("results").appendChild(empty);
      }
    } else {
      $("collection-title").textContent = "No databases found";
      $("collection-meta").textContent =
        "Point minidb-ui at a directory, or use “+ open” to add one";
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "No databases found.";
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
$("browse-explain").addEventListener("click", runExplain);
$("query-run").addEventListener("click", runQuery);
$("query-explain").addEventListener("click", runExplain);
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
$("add-database").addEventListener("click", addDatabase);

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeDrawer();
});

boot();
