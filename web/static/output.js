// A finished run: tiles, downloads, and tabs for the output, the review list, removed lines, the plan used and the inputs.

import { renderPlan } from "./plan.js";
import { api, duration, el, mixed, money, nice, textCell } from "./ui.js";

const ISSUE_LABEL = {
  not_verbatim: "Not an exact copy",
  printed_number_differs: "Printed number differs",
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
  section_gap: "Section missing",
  position_conflict: "Place disagrees with page header",
  locator_out_of_sequence: "Page header ignored",
  marker_not_last: "Text possibly out of order",
  no_such_section: "Section not found",
  trailing_text: "Text at the end",
  nothing_before: "Text at the start",
  unlabelled: "Line not labelled",
};
const PAGE = 100;
const TABS = [["output", "Output"], ["review", "To review"], ["removed", "Lines left out"], ["plan", "Plan used"], ["source", "Book text"], ["request", "Request"]];

// Checker messages are written for the agents; reword the common ones for a reader.
function friendly(message) {
  const number = message.match(/prints number \[(\d+)\] but is placed as \S+ \[(\d+)\]/);
  if (number) return `The OCR text has verse number ${number[1]} here, but the sequence says ${number[2]}, so the row is placed as ${number[2]}. The text keeps the printed number. Please confirm.`;
  return message.replace(/\bP\d{4}[a-z]*\b/g, "this line").replace(/Fine if .*$/, "");
}

export async function renderOutput(root, id, { tab = "output", source = null } = {}) {
  const result = await api(`/api/runs/${id}/result`);
  const state = { view: result.shape || "table", query: "", shown: PAGE };
  const rows = result.rows;
  const sections = result.sections || [];

  // ----- review items: one per row and message -----
  const items = new Map();
  for (const issue of result.issues) {
    const text = friendly(issue.message);
    const key = `${issue.row}|${issue.kind}|${text}`;
    if (items.has(key)) items.get(key).places += 1;
    else items.set(key, { ...issue, text, places: 1 });
  }
  const review = [...items.values()];

  const summary = result.summary;
  const tiles = el("div", { class: "tiles" }, [
    ["Rows", rows.length.toLocaleString(), false],
    ["To review", review.length.toLocaleString(), review.length > 0],
    ["Lines left out", result.removed.length.toLocaleString(), false],
    ["Time", duration(summary.seconds) || "–", false],
    ["Cost", String(summary.models?.labeler || "").startsWith("subscription/") ? "Claude plan" : money(summary.cost_usd), false],
  ].map(([label, value, attention]) => el("div", { class: `tile${attention ? " tile--attention" : ""}` }, el("b", { text: value }), el("span", { text: label }))));

  const files = [
    ["csv", "Table (CSV)", "One row per unit", result.shapes?.table !== false],
    ["blocks", "Blocks (CSV)", "One row per piece of text", result.shapes?.blocks],
    ["tree", "Tree (JSON)", "Nested in the book's shape", result.shapes?.tree],
    ["json", "Rows with line IDs (JSON)", "For checking against the book", true],
  ].filter(([, , , ok]) => ok);
  const main = { table: "csv", blocks: "blocks", tree: "tree" }[result.shape] || "csv";
  const menu = el("div", { class: "menu", hidden: true }, files.map(([kind, title, detail]) =>
    el("a", { href: `/api/runs/${id}/download/${kind}` }, title, el("small", { text: detail }))));
  const mainFile = files.find(([kind]) => kind === main) || files[0];
  const downloads = el("div", { class: "downloads page-head__side" },
    el("a", { class: "button button--primary", href: `/api/runs/${id}/download/${mainFile[0]}`, text: `Download ${mainFile[1]}` }),
    el("button", { class: "button button--outline", type: "button", "aria-haspopup": "true", text: "Other formats ▾", onclick: (e) => { e.stopPropagation(); menu.hidden = !menu.hidden; } }),
    menu);
  document.addEventListener("click", () => (menu.hidden = true), { once: true });

  // ----- tabs -----
  const panes = {};
  const counts = { review: review.length, removed: result.removed.length };
  const search = el("input", { class: "field search", type: "search", placeholder: "Search the rows", "aria-label": "Search the rows" });
  const tabBar = el("div", { class: "tabs", role: "tablist", "aria-label": "Output views" },
    TABS.map(([key, label]) => el("button", { class: "tab", role: "tab", type: "button", "data-tab": key, onclick: () => show(key) },
      label, counts[key] ? el("span", { class: "count", text: ` (${counts[key]})` }) : null)),
    search);

  panes.output = el("div", { class: "pane" });
  panes.review = el("div", { class: "pane" }, reviewList(review, (rowId) => goToRow(rowId)));
  panes.removed = el("div", { class: "pane" },
    el("p", { class: "muted", text: "Every line left out of the output, such as page headers and footnotes. Everything else is copied exactly as printed: no character is added, removed or changed." }),
    removedList(result.removed));
  panes.plan = el("div", { class: "pane" });
  if (result.plan) renderPlan(panes.plan, result.plan, { readOnly: true });
  else panes.plan.append(el("p", { class: "empty-state", text: "This run was made before plans were kept." }));
  panes.source = el("div", { class: "pane" }, el("pre", { class: "source", lang: "sa", text: source ? source.input : "" }));
  panes.request = el("div", { class: "pane" }, el("pre", { class: "source source--plain", text: source ? source.request : "" }));

  root.replaceChildren(
    el("div", { class: "page-head" }, el("div", {}, el("h2", { text: "Output" }), el("p", { class: "muted", text: `${rows.length.toLocaleString()} rows${sections.length ? `, ${sections.length} sections` : ""}. Click a row to open it.` })), downloads),
    tiles, tabBar, ...Object.values(panes));

  search.addEventListener("input", () => { state.query = search.value.trim(); state.shown = PAGE; drawOutput(); });
  drawOutput();
  show(TABS.some(([key]) => key === tab) ? tab : "output");

  function show(key) {
    tabBar.querySelectorAll(".tab").forEach((t) => t.setAttribute("aria-selected", String(t.dataset.tab === key)));
    Object.entries(panes).forEach(([name, pane]) => (pane.hidden = name !== key));
    search.hidden = key !== "output" || state.view === "tree";
  }

  function goToRow(rowId) {
    if (!rowId) return;
    state.query = "";
    search.value = "";
    state.view = "table";
    state.shown = Math.max(state.shown, rows.findIndex((r) => r.id === rowId) + 1);
    drawOutput();
    show("output");
    const target = panes.output.querySelector(`tr[data-id="${CSS.escape(rowId)}"]`);
    if (target) {
      target.classList.add("is-open");
      target.scrollIntoView({ block: "center" });
    }
  }

  function drawOutput() {
    const switcher = el("div", { class: "control-row" }, el("span", { text: "Show as" }),
      el("div", { class: "seg", role: "group", "aria-label": "Show as" }, [["table", "Table"], ["blocks", "Blocks"], ["tree", "Tree"]].map(([value, label]) =>
        el("button", { type: "button", "aria-pressed": String(state.view === value), text: label, onclick: () => { state.view = value; drawOutput(); show("output"); } }))));
    let body;
    if (state.view === "tree") body = treeView(result.tree);
    else body = state.view === "blocks" ? blocksView() : tableView();
    panes.output.replaceChildren(switcher, body);
  }

  function matches(row) {
    const q = state.query;
    return !q || row.id.includes(q) || row.cells.some((cell) => cell.includes(q));
  }

  function tableView() {
    const found = rows.filter(matches);
    const shown = found.slice(0, state.shown);
    const head = el("thead", {}, el("tr", {}, result.columns.map((name) => el("th", { text: nice(name) }))));
    const body = el("tbody");
    // A section's own text is shown where the book prints it: a title before its first unit, a closing line after its last.
    const sectionRow = (s) => {
      const text = Object.entries(s.fields).filter(([, v]) => v).map(([name, v]) => `${nice(name)}: ${v}`).join("  ·  ");
      return text ? el("tr", { class: "is-section" }, el("td", { class: "id", text: `${nice(s.level)} ${s.id}` }), el("td", { class: "text", colspan: String(result.columns.length - 1), lang: "sa", text })) : null;
    };
    const inSection = (rowId, s) => rowId.startsWith(`${s.id}.`);
    const lastOf = new Map(sections.map((s) => [s.id, rows.filter((r) => inSection(r.id, s)).at(-1)?.id]));
    const placed = new Set();
    for (const row of shown) {
      if (!state.query) {
        for (const s of sections) {
          if (!s.after && !placed.has(s.id) && inSection(row.id, s)) {
            placed.add(s.id);
            body.append(sectionRow(s) || "");
          }
        }
      }
      const idCell = el("td", { class: "id", text: row.id });
      if (row.notes.length) idCell.append(el("span", { class: "note-dot", title: "This row has something to review" }));
      const tr = el("tr", { "data-id": row.id, onclick: (event) => event.currentTarget.classList.toggle("is-open") },
        idCell, row.cells.map((cell) => textCell(cell, state.query)));
      if (row.notes.length) {
        const notes = [...new Set(row.notes.map(friendly))];
        tr.lastChild.append(el("ul", { class: "row-notes" }, notes.map((note) => el("li", {}, "To review: ", ...mixed(note)))));
      }
      body.append(tr);
      if (!state.query) {
        for (const s of sections) if (s.after && lastOf.get(s.id) === row.id) body.append(sectionRow(s) || "");
      }
    }
    const more = el("button", { class: "button button--outline", type: "button", text: "Show 100 more", hidden: found.length <= state.shown,
      onclick: () => { state.shown += PAGE; drawOutput(); } });
    const foot = el("div", { class: "pane__foot" },
      el("span", { class: "muted", text: state.query
        ? `${found.length.toLocaleString()} of ${rows.length.toLocaleString()} rows match.`
        : `Showing ${Math.min(state.shown, found.length).toLocaleString()} of ${rows.length.toLocaleString()} rows.` }), more);
    return el("div", {}, el("div", { class: "table-wrap" }, el("table", { class: "rows" }, head, body)), foot);
  }

  function blocksView() {
    const fields = result.columns.slice(1);
    const blocks = [];
    for (const row of rows.filter(matches)) row.cells.forEach((cell, i) => cell && blocks.push([row.id, fields[i], cell]));
    const shown = blocks.slice(0, state.shown * 2);
    const head = el("thead", {}, el("tr", {}, ["path", "layer", "text"].map((name) => el("th", { text: name }))));
    const body = el("tbody", {}, shown.map(([rowId, layer, text]) =>
      el("tr", { "data-id": rowId, onclick: (e) => e.currentTarget.classList.toggle("is-open") },
        el("td", { class: "id", text: rowId }), el("td", { class: "layer", text: nice(layer) }), textCell(text, state.query))));
    const more = el("button", { class: "button button--outline", type: "button", text: "Show more", hidden: blocks.length <= shown.length,
      onclick: () => { state.shown += PAGE; drawOutput(); } });
    return el("div", {}, el("div", { class: "table-wrap" }, el("table", { class: "rows" }, head, body)),
      el("div", { class: "pane__foot" }, el("span", { class: "muted", text: `Showing ${shown.length.toLocaleString()} of ${blocks.length.toLocaleString()} blocks. The downloaded file also has a column for each level.` }), more));
  }
}

function treeView(tree) {
  if (!tree) return el("p", { class: "empty-state", text: "This run was made before the tree output existed." });
  const { structure } = tree;
  const unitKey = structure.unit.endsWith("s") ? structure.unit : `${structure.unit}s`;
  const keyOf = (name) => (name.endsWith("s") ? name : `${name}s`);
  const fieldNames = structure.fields.map((f) => f.name);

  function unitsList(units) {
    return units.map((unit) => el("li", { class: "tree__unit" },
      el("h4", { text: `${nice(structure.unit)} ${unit.id}` }),
      fieldNames.filter((name) => unit[name]).map((name) => el("div", { class: "tree__field" }, el("b", { text: nice(name) }), el("div", { lang: "sa", text: unit[name] })))));
  }

  function level(node, depth) {
    const out = [];
    const lv = structure.levels[depth];
    if (lv && node[keyOf(lv.name)]) {
      const siblings = node[keyOf(lv.name)];
      for (const child of siblings) {
        const count = countUnits(child);
        const sectionText = lv.fields.filter((f) => child[f]).map((f) => el("div", { class: "tree__field" }, el("b", { text: nice(f) }), el("div", { lang: "sa", text: child[f] })));
        out.push(el("li", {}, el("details", { open: (depth === 0 && (siblings.length <= 3 || count < 40)) || (depth === 1 && siblings.length === 1) ? true : undefined },
          el("summary", {}, `${nice(lv.name)} ${child[lv.name]} `, el("span", { class: "muted", text: `· ${count} ${nice(structure.unit)}${count === 1 ? "" : "s"}` })),
          el("ul", {}, sectionText.length ? el("li", {}, sectionText) : null, level(child, depth + 1)))));
      }
    }
    // Units directly under this node (always for the innermost level; also where an optional level is missing).
    if (node[unitKey]) out.push(...unitsList(node[unitKey]));
    if (lv && !node[keyOf(lv.name)] && depth + 1 < structure.levels.length) out.push(...level(node, depth + 1));
    return out;
  }

  function countUnits(node) {
    let n = (node[unitKey] || []).length;
    for (const lv of structure.levels) for (const child of node[keyOf(lv.name)] || []) n += countUnits(child);
    return n;
  }

  return el("ul", { class: "tree" }, level(tree, 0));
}

function reviewList(review, goToRow) {
  if (!review.length) return el("p", { class: "empty-state", text: "Nothing to review. The agents and the checks found no doubts in this book." });
  return el("div", { class: "list" }, review.map((issue) =>
    el("div", { class: "item" },
      el("span", { class: `item__tag${issue.severity === "error" ? " item__tag--error" : ""}`, text: ISSUE_LABEL[issue.kind] || nice(issue.kind) }),
      el("div", { class: "item__body" },
        issue.row ? el("button", { class: "link-button item__row", type: "button", text: `Row ${issue.row}`, onclick: () => goToRow(issue.row) }) : null,
        ...mixed(issue.text),
        issue.places > 1 ? el("span", { class: "muted", text: ` (${issue.places} places in this row)` }) : null))));
}

function removedList(removed) {
  if (!removed.length) return el("p", { class: "empty-state", text: "No lines were left out." });
  return el("div", { class: "list" }, removed.map((line) =>
    el("div", { class: "item" },
      el("span", { class: "item__tag item__tag--change", text: nice(line.role) }),
      el("div", { class: "item__body" }, el("span", { class: "muted", text: `Page ${line.page} ` }), el("span", { class: "sa", lang: "sa", text: line.text })))));
}
