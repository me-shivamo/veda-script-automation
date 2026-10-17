// Shared helpers. Book text is always put on the page with textContent, never as HTML.

export const $ = (id) => document.getElementById(id);

export async function api(path, options) {
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

export const post = (path, body) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  node.append(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
  return node;
}

export function duration(seconds) {
  if (seconds === null || seconds === undefined) return "";
  const s = Math.round(seconds);
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`;
}

export const money = (value) => (value === null || value === undefined ? "–" : value < 0.01 ? "< $0.01" : `$${value.toFixed(2)}`);
export const when = (created) => (created ? new Date(created * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "");
export const plural = (n, word, many = word + "s") => `${n.toLocaleString()} ${n === 1 ? word : many}`;
export const nice = (name) => String(name || "").replaceAll("_", " ");

export const PHASE_LABEL = {
  starting: "Starting",
  analysing: "Reading the book",
  awaiting_approval: "Your approval needed",
  labelling: "Labelling",
  reviewing: "Second look",
  assembling: "Assembling",
  done: "Done",
  needs_review: "Done, with items to review",
  stopped: "Stopped",
  failed: "Failed",
  interrupted: "Interrupted",
};

export const status = (phase) => el("span", { class: `status status--${phase}`, text: PHASE_LABEL[phase] || phase });

// Put runs of Devanagari inside a sentence in the Sanskrit typeface.
export function mixed(text) {
  const parts = String(text).split(/([ऀ-ॿ][ऀ-ॿ\s]*[ऀ-ॿ]|[ऀ-ॿ])/);
  return parts.filter(Boolean).map((part) => (/[ऀ-ॿ]/.test(part) ? el("span", { class: "sa", lang: "sa", text: part }) : part));
}

// A table cell of book text: clamped to four lines until the row is opened, with search matches marked.
export function textCell(text, query = "") {
  if (!text) return el("td", { class: "text" }, el("span", { class: "empty", text: "(empty)" }));
  const box = el("div", { class: "clamp" });
  if (!query) box.textContent = text;
  else {
    let from = 0;
    for (let at = text.indexOf(query); at !== -1; at = text.indexOf(query, from)) {
      box.append(text.slice(from, at), el("mark", { text: query }));
      from = at + query.length;
    }
    box.append(text.slice(from));
  }
  return el("td", { class: "text", lang: "sa" }, box);
}

// A plain table of cells, used for previews and the blocks view. idColumns: how many leading columns are IDs.
export function simpleTable(columns, rows, { idColumns = 1, textFrom = idColumns, className = "" } = {}) {
  const head = el("thead", {}, el("tr", {}, columns.map((name) => el("th", { text: nice(name) }))));
  const body = el("tbody", {}, rows.map((row) =>
    el("tr", { onclick: (event) => event.currentTarget.classList.toggle("is-open") },
      row.map((cell, i) => (i < idColumns ? el("td", { class: "id", text: cell }) : i < textFrom ? el("td", { class: "layer", text: nice(cell) }) : textCell(cell))))));
  return el("div", { class: `table-wrap ${className}` }, el("table", { class: "rows" }, head, body));
}

// A small JSON view of the tree output.
export const jsonBlock = (data) => el("pre", { class: "json", lang: "sa", text: JSON.stringify(data, null, 1) });

export function rail(steps, activeIndex, { busy = false, done = -1 } = {}) {
  const list = el("ol", { class: "rail", style: `--steps:${steps.length}` });
  steps.forEach(([title, detail], index) => {
    const finished = index <= done || index < activeIndex;
    list.append(el("li", { class: [finished ? "is-done" : "", index === activeIndex ? "is-active" : "", index === activeIndex && busy ? "is-busy" : ""].join(" ") },
      el("span", { class: "rail__dot", text: finished ? "✓" : String(index + 1) }),
      el("span", {}, el("strong", { text: title }), el("small", { text: detail }))));
  });
  return list;
}
