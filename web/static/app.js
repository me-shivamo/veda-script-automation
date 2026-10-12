// Page logic: load a book and a request, start a run, follow it, approve the plan, read the output.
// Book text is always put on the page with textContent, never as HTML.

const $ = (id) => document.getElementById(id);

const PHASE_LABEL = {
  starting: "Starting",
  analysing: "Reading the book",
  awaiting_approval: "Waiting for your approval",
  labelling: "Labelling",
  reviewing: "Second look",
  assembling: "Assembling the rows",
  done: "Done",
  needs_review: "Done, with items to review",
  stopped: "Stopped",
  failed: "Failed",
  interrupted: "Interrupted",
};
const STEP_OF = { starting: 0, analysing: 0, awaiting_approval: 1, labelling: 2, reviewing: 3, assembling: 3, done: 5, needs_review: 5 };
const ISSUE_LABEL = {
  printed_number_differs: "Verse number corrected",
  flagged: "Flagged by an agent",
  duplicate_page: "Duplicated page",
  missing_field: "Missing part",
  dropped_content: "Possibly dropped text",
  kept_furniture: "Possible page header in text",
  attached_mid_line: "Possible heading mistake",
  attached_sentence: "Possible heading mistake",
  text_after_number: "Numbering doubt",
  number_gap: "Number missing",
  number_out_of_order: "Numbers out of order",
  field_number_backwards: "Numbers out of order",
  section_start_number: "Section does not start at 1",
  trailing_text: "Text at the end",
  nothing_before: "Text at the start",
  unlabelled: "Line not labelled",
};
const CHANGE_LABEL = {
  quotes_changed: "Quotes changed",
  verse_number_fixed: "Verse number fixed",
  leading_noise_removed: "Stray mark removed",
  hyphen_joined: "Hyphen joined",
};
const PAGE = 100;

const state = { id: null, timer: null, result: null, shown: PAGE, query: "", lastStep: 0, proposalKey: "", wasActive: false };

async function api(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = "";
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : (body.detail || []).map((d) => d.msg).join("; ");
    } catch {
      detail = response.statusText;
    }
    throw new Error(detail || `The request failed (${response.status}).`);
  }
  return response.json();
}

const post = (path, body) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  node.append(...children.filter((c) => c !== null && c !== undefined));
  return node;
}

function duration(seconds) {
  if (seconds === null || seconds === undefined) return "";
  const s = Math.round(seconds);
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`;
}

const money = (value) => (value === null || value === undefined ? "–" : value < 0.01 ? "< $0.01" : `$${value.toFixed(2)}`);
const when = (created) => (created ? new Date(created * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "");

// ---------- Step 1 and 2: the book and the request ----------

function updateStats() {
  const text = $("input").value;
  if (!text.trim()) {
    $("inputStats").textContent = "No text loaded.";
    return;
  }
  const lines = text.split("\n");
  const pages = text.split(/^\s*-{3,}\s*$/m).filter((page) => page.trim()).length;
  $("inputStats").textContent =
    `${pages.toLocaleString()} page${pages === 1 ? "" : "s"} · ${lines.length.toLocaleString()} lines · ${text.length.toLocaleString()} characters`;
}

function readFile(file, target, after) {
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    $(target).value = reader.result;
    if (after) after(file);
  };
  reader.readAsText(file, "utf-8");
}

function bookLoaded(file) {
  updateStats();
  if (!$("name").value.trim()) $("name").value = file.name.replace(/\.[^.]+$/, "");
}

async function loadSample(id) {
  const sample = await api(`/api/samples/${id}`);
  $("input").value = sample.input;
  $("request").value = sample.request;
  $("examples").value = sample.examples;
  $("name").value = sample.name;
  updateStats();
}

async function startRun() {
  const body = {
    name: $("name").value.trim() || "book",
    input: $("input").value,
    request: $("request").value.trim(),
    examples: $("examples").value.trim(),
    model: $("model").value,
    ask_approval: $("askApproval").checked,
  };
  $("formError").textContent = "";
  if (!body.input.trim()) return void ($("formError").textContent = "Load or paste the book text first.");
  if (!body.request) return void ($("formError").textContent = "Write your request first.");
  $("runButton").disabled = true;
  try {
    const { id } = await post("/api/runs", body);
    await openRun(id, { fill: false });
    $("run").scrollIntoView({ block: "start" });
  } catch (error) {
    $("formError").textContent = error.message;
  } finally {
    $("runButton").disabled = false;
  }
}

// ---------- A run in progress ----------

function renderStatus(status) {
  const active = status.active;
  const finished = status.phase === "done" || status.phase === "needs_review";
  $("run").hidden = finished;
  $("runTitle").textContent = status.name;
  $("runPhase").textContent = PHASE_LABEL[status.phase] || status.phase;
  $("runPhase").className = `status status--${status.phase}`;
  $("runElapsed").textContent = [status.model, duration(status.elapsed)].filter(Boolean).join(" · ");
  $("stopButton").hidden = !active;

  if (status.phase in STEP_OF) state.lastStep = STEP_OF[status.phase];
  document.querySelectorAll("#stepper li").forEach((item, index) => {
    item.classList.toggle("is-done", index < state.lastStep);
    item.classList.toggle("is-active", active && index === state.lastStep);
    item.classList.toggle("is-waiting", status.phase === "awaiting_approval");
  });
  $("chunkProgress").textContent =
    status.chunks_total && status.phase === "labelling"
      ? `${status.chunks_done} of ${status.chunks_total} parts done`
      : "Every line is marked";

  $("runError").hidden = !(status.error || status.phase === "interrupted" || status.phase === "stopped");
  $("runError").textContent =
    status.error ||
    (status.phase === "interrupted" ? "This run was cut off before it finished, probably because the server restarted. Start it again." : "This run was stopped.");

  const proposal = status.phase === "awaiting_approval" ? status.proposal : null;
  $("proposal").hidden = !proposal;
  const key = proposal ? proposal.summary : "";
  if (proposal && key !== state.proposalKey) renderProposal(proposal);
  state.proposalKey = key;

  $("log").textContent = status.log.join("\n");
}

function renderProposal(proposal) {
  $("proposalSummary").textContent = proposal.summary;
  $("proposalLimits").hidden = proposal.limitations.length === 0;
  $("proposalLimitList").replaceChildren(...proposal.limitations.map((item) => el("li", { text: item })));
  $("proposalRules").textContent = proposal.rules;
  $("feedback").value = "";
  $("decisionError").textContent = "";

  const fields = proposal.columns.slice(1);
  const head = el("thead", {}, el("tr", {}, el("th", { text: "Sample" }), ...fields.map((name) => el("th", { text: name }))));
  const body = el("tbody", {}, ...proposal.rows.map((row) =>
    el("tr", { onclick: (event) => event.currentTarget.classList.toggle("is-open") },
      el("td", { class: "id", text: row.label }),
      ...fields.map((name) => textCell(row[name] || "")))));
  $("proposalTable").replaceChildren(head, body);
  $("proposal").scrollIntoView({ block: "nearest" });
}

async function decide(action) {
  const feedback = $("feedback").value.trim();
  $("decisionError").textContent = "";
  if (action === "revise" && !feedback) return void ($("decisionError").textContent = "Say what should change first.");
  try {
    await post(`/api/runs/${state.id}/decision`, { action, feedback });
    $("proposal").hidden = true;
    poll();
  } catch (error) {
    $("decisionError").textContent = error.message;
  }
}

async function poll() {
  clearTimeout(state.timer);
  if (!state.id) return;
  let status;
  try {
    status = await api(`/api/runs/${state.id}`);
  } catch {
    state.timer = setTimeout(poll, 4000);
    return;
  }
  renderStatus(status);
  if (status.active) {
    state.wasActive = true;
    state.timer = setTimeout(poll, 1500);
    return;
  }
  loadRuns();
  if (status.has_output) await loadResult(state.id, { scroll: state.wasActive });
  state.wasActive = false;
}

async function openRun(id, { fill = true } = {}) {
  clearTimeout(state.timer);
  Object.assign(state, { id, result: null, shown: PAGE, query: "", lastStep: 0, proposalKey: "", wasActive: false });
  $("search").value = "";
  $("output").hidden = true;
  $("run").hidden = false;
  history.replaceState(null, "", `?run=${id}`);
  const source = await api(`/api/runs/${id}/input`);
  if (fill) {
    $("input").value = source.input;
    $("request").value = source.request;
    $("examples").value = source.examples;
    $("name").value = source.name;
    updateStats();
  }
  $("sourceText").textContent = source.input;
  $("requestText").textContent = source.request;
  await poll();
  loadRuns();
}

// ---------- Output ----------

function textCell(text, query = "") {
  const box = el("div", { class: "clamp" });
  if (!text) return el("td", { class: "text" }, el("span", { class: "empty", text: "(empty)" }));
  if (!query) box.textContent = text;
  else {
    // Mark every match of the search text.
    let from = 0;
    for (let at = text.indexOf(query); at !== -1; at = text.indexOf(query, from)) {
      box.append(text.slice(from, at), el("mark", { text: query }));
      from = at + query.length;
    }
    box.append(text.slice(from));
  }
  return el("td", { class: "text", lang: "sa" }, box);
}

function matches(row, query) {
  return !query || row.id.includes(query) || row.cells.some((cell) => cell.includes(query));
}

function renderRows() {
  const { columns, rows } = state.result;
  const found = rows.filter((row) => matches(row, state.query));
  const head = el("thead", {}, el("tr", {}, ...columns.map((name) => el("th", { text: name }))));
  const body = el("tbody", {}, ...found.slice(0, state.shown).map((row) => {
    const id = el("td", { class: "id", text: row.id });
    if (row.notes.length) id.append(el("span", { class: "note-dot", title: "This row has something to review" }));
    const tr = el("tr", { "data-id": row.id, onclick: (event) => event.currentTarget.classList.toggle("is-open") },
      id, ...row.cells.map((cell) => textCell(cell, state.query)));
    if (row.notes.length) {
      const notes = [...new Set(row.notes.map(friendly))];
      tr.lastChild.append(el("ul", { class: "row-notes" }, ...notes.map((note) => el("li", {}, "To review: ", ...mixed(note)))));
    }
    return tr;
  }));
  $("rowsTable").replaceChildren(head, body);
  $("rowsShown").textContent = state.query
    ? `${found.length.toLocaleString()} of ${rows.length.toLocaleString()} rows match. Click a row to open it.`
    : `Showing ${Math.min(state.shown, found.length).toLocaleString()} of ${rows.length.toLocaleString()} rows. Click a row to open it.`;
  $("moreRows").hidden = found.length <= state.shown;
}

// Checker messages are written for the agents; reword the common ones for a reader.
function friendly(message) {
  const number = message.match(/prints number \[(\d+)\] but is labelled \[(\d+)\]/);
  if (number) return `The OCR text has verse number ${number[1]} here, but the sequence says ${number[2]}, so it was written as ${number[2]}. Please confirm.`;
  return message.replace(/\bP\d{4}[a-z]*\b/g, "this line").replace(/Fine if .*$/, "");
}

// Put runs of Devanagari inside a sentence in the Sanskrit typeface.
function mixed(text) {
  const parts = text.split(/([\u0900-\u097F][\u0900-\u097F\s]*[\u0900-\u097F]|[\u0900-\u097F])/);
  return parts.filter(Boolean).map((part) => (/[\u0900-\u097F]/.test(part) ? el("span", { class: "sa", lang: "sa", text: part }) : part));
}

// One note per row and message: the same doubt on a mantra line and its commentary line is shown once.
function reviewItems() {
  const items = new Map();
  for (const issue of state.result.issues) {
    const text = friendly(issue.message);
    const key = `${issue.row}|${issue.kind}|${text}`;
    if (items.has(key)) items.get(key).places += 1;
    else items.set(key, { ...issue, text, places: 1 });
  }
  return [...items.values()];
}

function goToRow(id) {
  if (!id) return;
  state.query = "";
  $("search").value = "";
  state.shown = Math.max(state.shown, state.result.rows.findIndex((row) => row.id === id) + 1);
  showTab("rows");
  renderRows();
  const target = document.querySelector(`#rowsTable tr[data-id="${CSS.escape(id)}"]`);
  if (target) {
    target.classList.add("is-open");
    target.scrollIntoView({ block: "center" });
  }
}

function rowLink(id) {
  return id ? el("button", { class: "link-button item__row", type: "button", text: `Row ${id}`, onclick: () => goToRow(id) }) : null;
}

function renderReview() {
  const issues = reviewItems();
  $("reviewCount").textContent = issues.length ? `(${issues.length})` : "";
  if (!issues.length) {
    $("reviewList").replaceChildren(el("p", { class: "empty-state", text: "Nothing to review. The agents and the checks found no doubts in this book." }));
    return;
  }
  $("reviewList").replaceChildren(...issues.map((issue) =>
    el("div", { class: "item" },
      el("span", { class: `item__tag${issue.severity === "error" ? " item__tag--error" : ""}`, text: ISSUE_LABEL[issue.kind] || issue.kind }),
      el("div", { class: "item__body" }, rowLink(issue.row), ...mixed(issue.text),
        issue.places > 1 ? el("span", { class: "muted", text: ` (${issue.places} places in this row)` }) : null))));
}

function difference(before, after) {
  // Show a short window around the first character that differs.
  let at = 0;
  while (at < before.length && at < after.length && before[at] === after[at]) at += 1;
  let from = Math.max(at - 18, 0);
  while (from > 0 && !/\s/.test(before[from - 1])) from -= 1;
  const cut = (text) => {
    let to = Math.min(at + 22, text.length);
    while (to < text.length && !/\s/.test(text[to])) to += 1;
    return (from > 0 ? "… " : "") + text.slice(from, to) + (to < text.length ? " …" : "");
  };
  return [cut(before), cut(after)];
}

function renderChanges() {
  const { changes } = state.result;
  $("changeCount").textContent = changes.length ? `(${changes.length})` : "";
  if (!changes.length) {
    $("changeList").replaceChildren(el("p", { class: "empty-state", text: "No characters were changed. The output is the book text exactly." }));
    return;
  }
  $("changeList").replaceChildren(...changes.map((change) => {
    const [before, after] = difference(change.before, change.after);
    return el("div", { class: "item" },
      el("span", { class: "item__tag item__tag--change", text: CHANGE_LABEL[change.kind] || change.kind }),
      el("div", { class: "item__body" }, rowLink(change.row),
        el("span", { class: "sa", lang: "sa", text: before }), " → ", el("span", { class: "sa", lang: "sa", text: after })));
  }));
}

function renderTiles() {
  const { rows, changes, summary } = state.result;
  const issues = reviewItems();
  const tiles = [
    ["Rows", rows.length.toLocaleString(), false],
    ["To review", issues.length.toLocaleString(), issues.length > 0],
    ["Text changes", changes.length.toLocaleString(), false],
    ["Time", duration(summary.seconds) || "–", false],
    ["Cost", money(summary.cost_usd), false],
  ];
  $("tiles").replaceChildren(...tiles.map(([label, value, attention]) =>
    el("div", { class: `tile${attention ? " tile--attention" : ""}` }, el("b", { text: value }), el("span", { text: label }))));
}

function showTab(name) {
  document.querySelectorAll(".tab").forEach((tab) => tab.setAttribute("aria-selected", String(tab.dataset.tab === name)));
  document.querySelectorAll(".pane").forEach((pane) => (pane.hidden = pane.dataset.pane !== name));
  $("search").hidden = name !== "rows";
}

async function loadResult(id, { scroll = false } = {}) {
  state.result = await api(`/api/runs/${id}/result`);
  state.shown = PAGE;
  $("output").hidden = false;
  $("outputTitle").textContent = state.result.card.name;
  $("downloadCsv").href = `/api/runs/${id}/download/csv`;
  $("downloadJson").href = `/api/runs/${id}/download/json`;
  renderTiles();
  renderRows();
  renderReview();
  renderChanges();
  showTab(state.openTab || "rows");
  state.openTab = null;
  if (scroll) $("output").scrollIntoView({ block: "start" });
}

// ---------- Past runs ----------

async function loadRuns() {
  const runs = await api("/api/runs");
  if (!runs.length) {
    $("runList").replaceChildren(el("p", { class: "empty-state", text: "No runs yet. Your first run will appear here." }));
    return;
  }
  $("runList").replaceChildren(...runs.map((run) =>
    el("button", { class: `run-card lift${run.id === state.id ? " is-current" : ""}`, type: "button", onclick: () => openRun(run.id).then(() => scrollToCurrent()) },
      el("span", { class: `status status--${run.phase}`, text: PHASE_LABEL[run.phase] || run.phase }),
      el("h3", { text: run.name }),
      el("p", { text: [when(run.created), run.rows !== null && run.rows !== undefined ? `${run.rows} rows` : null, run.cost_usd ? money(run.cost_usd) : null].filter(Boolean).join(" · ") }))));
}

function scrollToCurrent() {
  ($("output").hidden ? $("run") : $("output")).scrollIntoView({ block: "start" });
}

// ---------- Start ----------

async function init() {
  const config = await api("/api/config");
  $("keyWarning").hidden = config.key_configured;
  $("model").replaceChildren(...config.models.map((model) =>
    el("option", { value: model.id, text: `${model.label} (${model.note})`, ...(model.id === config.default_model ? { selected: "" } : {}) })));
  $("samples").replaceChildren(...config.samples.map((sample) =>
    el("button", { class: "link-button", type: "button", text: `Use the sample book: ${sample.name}`, onclick: () => loadSample(sample.id) })));

  $("input").addEventListener("input", updateStats);
  $("inputFile").addEventListener("change", (event) => readFile(event.target.files[0], "input", bookLoaded));
  $("examplesFile").addEventListener("change", (event) => readFile(event.target.files[0], "examples"));
  const zone = $("dropzone");
  zone.addEventListener("dragover", (event) => { event.preventDefault(); zone.classList.add("is-over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("is-over"));
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    zone.classList.remove("is-over");
    readFile(event.dataTransfer.files[0], "input", bookLoaded);
  });

  $("runButton").addEventListener("click", startRun);
  $("approveButton").addEventListener("click", () => decide("approve"));
  $("reviseButton").addEventListener("click", () => decide("revise"));
  $("rejectButton").addEventListener("click", () => decide("stop"));
  $("stopButton").addEventListener("click", () => post(`/api/runs/${state.id}/stop`).then(poll, poll));
  $("moreRows").addEventListener("click", () => { state.shown += PAGE; renderRows(); });
  $("search").addEventListener("input", (event) => { state.query = event.target.value.trim(); state.shown = PAGE; renderRows(); });
  document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => showTab(tab.dataset.tab)));

  const bar = $("scrollBar");
  const onScroll = () => {
    const room = document.documentElement.scrollHeight - window.innerHeight;
    bar.style.transform = `scaleX(${room > 0 ? window.scrollY / room : 0})`;
  };
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  await loadRuns();
  const params = new URLSearchParams(location.search);
  const wanted = params.get("run");
  // A link can open a run on a given tab: ?run=<id>&tab=review
  state.openTab = ["review", "changes", "source", "request"].includes(params.get("tab")) ? params.get("tab") : null;
  if (wanted) openRun(wanted).catch(() => history.replaceState(null, "", location.pathname));
}

init();
