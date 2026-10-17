// The plan screen: what the Analyst understood, and the output file as it will look.
// The user approves it, corrects it in words (sent to the Analyst), or edits the columns and shape (done by code).

import { el, jsonBlock, mixed, nice, simpleTable } from "./ui.js";

const SHAPES = [["table", "Table"], ["blocks", "Blocks"], ["tree", "Tree"]];
const ID_STYLES = [["dotted", "One ID (1.3.5)"], ["columns", "A column per level"]];
const SECTION_TEXT = [["rows", "A row per section"], ["columns", "Columns on each row"]];

const label = (options, value) => (options.find(([v]) => v === value) || [value, value])[1];

function seg(options, current, onpick, label) {
  return el("div", { class: "seg", role: "group", "aria-label": label },
    options.map(([value, text]) => el("button", { type: "button", "aria-pressed": String(current === value), text, onclick: () => onpick(value) })));
}

function describeEdit(edit) {
  switch (edit.op) {
    case "rename_column": return `rename ${edit.column} → ${edit.to}`;
    case "move_column": return `move ${edit.column} to place ${edit.index + 1}`;
    case "merge_column": return `put ${edit.column} into ${edit.to}`;
    case "set_output": return Object.entries(edit).filter(([k, v]) => k !== "op" && v).map(([k, v]) => `${nice(k)}: ${v}`).join(", ");
    default: return edit.op;
  }
}

// ---------- left: what was understood ----------

function structureCard(plan) {
  const s = plan.structure;
  const chain = el("div", { class: "structure" });
  s.levels.forEach((level) => {
    chain.append(el("span", { class: "node" }, nice(level.name), level.optional ? el("small", { text: "(not everywhere)" }) : null), el("span", { class: "arrow", text: "→" }));
  });
  chain.append(el("span", { class: "node node--unit", text: nice(s.unit) }));
  const layers = el("div", { class: "layers" }, s.fields.map((f) =>
    el("span", { class: "node node--layer", title: f.description }, nice(f.name), f.about ? el("small", { text: `on ${nice(f.about)}` }) : null, f.required ? null : el("small", { text: "(optional)" }))));
  const sectionFields = s.levels.flatMap((level) => level.fields.map((f) => `${nice(level.name)} ${nice(f.name)}`));
  return el("section", { class: "card" },
    el("div", { class: "card__head" }, el("h2", { text: "What I found in this book" })),
    el("p", { text: plan.summary }),
    el("h3", { class: "section-title", text: "Structure" }),
    chain,
    el("p", { class: "hint", text: `Each ${nice(s.unit)} holds:` }),
    layers,
    el("dl", { class: "facts" },
      el("dt", { text: "Rows placed by" }), el("dd", { text: s.position === "printed" ? "the location printed on each page (the OCR order is not trusted)" : "counting the lines that open and close sections" }),
      el("dt", { text: "Numbering" }), el("dd", { text: s.levels.length ? `${nice(s.unit)} numbers ${s.restart_numbering ? "start again in each" : "run on across each"} ${nice(s.levels.at(-1).name)}` : "straight through the book" }),
      el("dt", { text: "Shared commentary" }), el("dd", { text: s.shared_text === "copy" ? "copied into each row it explains" : "kept in the first row only" }),
      sectionFields.length ? [el("dt", { text: "Kept per section" }), el("dd", { text: sectionFields.join(", ") })] : null),
    outlineList(plan));
}

function outlineList(plan) {
  if (!plan.outline.length) return null;
  const items = plan.outline.map((o) => {
    const depth = Math.min(4, Math.max(0, String(o.path).split(".").length - 1));
    return el("li", { class: `depth-${depth}` },
      el("span", { class: "outline__path", text: o.path }), " ",
      el("span", { class: "outline__label", lang: "sa", text: o.label }),
      o.units ? el("span", { class: "outline__units", text: `${nice(plan.structure.unit)} ${o.units}` }) : null,
      o.evidence.map((e) => el("span", { class: "evidence" }, `page ${e.page ?? "?"}: `, e.found ? el("span", { class: "sa", lang: "sa", text: e.text }) : `${e.id} (not found)`)));
  });
  return el("div", {},
    el("h3", { class: "section-title" }, "Sections found", el("span", { class: "count", text: `${plan.outline.length}` })),
    el("details", { class: "more", open: plan.outline.length <= 14 ? true : undefined },
      el("summary", { text: "Show the outline with the lines that show each section" }),
      el("ul", { class: "outline" }, items)));
}

function kindsCard(plan) {
  return el("section", { class: "card" },
    el("div", { class: "card__head" }, el("h2", { text: "Each kind of text, and where it goes" })),
    el("div", { class: "kinds" }, plan.kinds.map((k) => {
      const out = k.kind === "drop" || k.kind === "locator";
      return el("div", { class: "kind" },
        el("span", { class: "kind__name", text: nice(k.role) }),
        el("span", { class: `dest${out ? " dest--out" : ""}`, text: out ? k.goes_to : `→ ${nice(k.goes_to)}` }),
        el("span", { class: "kind__desc" }, ...mixed(k.description)),
        k.examples.filter((e) => e.found).slice(0, 1).map((e) => el("span", { class: "kind__example", lang: "sa", title: `page ${e.page}`, text: e.text })));
    })));
}

// ---------- right: the output file ----------

function outputCard(plan, staged, onchange, readOnly) {
  const s = plan.structure;
  const want = { ...s.output };
  for (const edit of staged) if (edit.op === "set_output") Object.assign(want, Object.fromEntries(Object.entries(edit).filter(([k, v]) => k !== "op" && v)));
  const setOutput = (key, value) => {
    const rest = staged.filter((e) => !(e.op === "set_output" && e[key]));
    if (s.output[key] !== value) rest.push({ op: "set_output", [key]: value });
    onchange(rest);
  };

  // The columns as they will be after the staged edits.
  let columns = s.fields.map((f) => ({ name: f.name, original: f.name }));
  for (const e of staged) {
    if (e.op === "rename_column") columns = columns.map((c) => (c.name === e.column ? { ...c, name: e.to } : c));
    if (e.op === "merge_column") columns = columns.filter((c) => c.name !== e.column);
    if (e.op === "move_column") {
      const i = columns.findIndex((c) => c.name === e.column);
      const [item] = columns.splice(i, 1);
      columns.splice(e.index, 0, item);
    }
  }
  const edited = new Set(staged.flatMap((e) => [e.column, e.to]).filter(Boolean));
  const chips = el("div", { class: "colchips" },
    want.id_style === "columns"
      ? [...s.levels.map((l) => l.name), s.unit].map((n) => el("span", { class: "colchip colchip--id", text: nice(n) }))
      : el("span", { class: "colchip colchip--id", title: `for example ${s.id_example}`, text: s.id_column }),
    columns.map((c, index) => readOnly ? el("span", { class: "colchip colchip--id", text: c.name }) : columnChip(c, index, columns, edited, staged, onchange)));

  const p = plan.preview;
  let preview;
  if (!p.available) preview = el("p", { class: "empty-state", text: "No trial rows are available for this plan." });
  else if (want.shape === "tree") preview = jsonBlock(p.tree);
  else if (want.shape === "blocks") preview = simpleTable(p.blocks.columns, p.blocks.rows, { idColumns: 1 + s.levels.length + 1, textFrom: 2 + s.levels.length + 1, className: "table-wrap--short" });
  else preview = simpleTable(p.table.columns, p.table.rows, { idColumns: p.table.columns.indexOf(columnsAfterId(p.table.columns, s)) , className: "table-wrap--short" });
  const outdated = staged.some((e) => e.op !== "set_output" || e.id_style || e.section_text);
  const hasSectionFields = s.levels.some((l) => l.fields.length);

  return el("section", { class: "card" },
    el("div", { class: "card__head" }, el("h2", { text: "Your output file" }), el("p", { class: "muted", text: "All three shapes can be downloaded; this one is shown first." })),
    el("div", { class: "control-row" }, el("span", { text: "Shape" }), readOnly ? el("b", { text: label(SHAPES, want.shape) }) : seg(SHAPES, want.shape, (v) => setOutput("shape", v), "Output shape")),
    s.levels.length ? el("div", { class: "control-row" }, el("span", { text: "Row ID" }), readOnly ? el("b", { text: label(ID_STYLES, want.id_style) }) : seg(ID_STYLES, want.id_style, (v) => setOutput("id_style", v), "Row ID")) : null,
    hasSectionFields ? el("div", { class: "control-row" }, el("span", { text: "Section text" }), readOnly ? el("b", { text: label(SECTION_TEXT, want.section_text) }) : seg(SECTION_TEXT, want.section_text, (v) => setOutput("section_text", v), "Section text")) : null,
    el("div", { class: "control-row" }, el("span", { text: "Columns" }), chips),
    readOnly ? null : el("p", { class: "hint", text: "Rename, reorder or merge columns here. These changes are made straight away, without the AI. To change what goes into a column, write to the Analyst below." }),
    el("h3", { class: "section-title" }, "Preview", p.available ? el("span", { class: "count", text: `trial on page${p.pages[0] === p.pages[1] ? "" : "s"} ${p.pages.join("–")} · ${p.total_rows} rows` }) : null),
    p.available && !p.ids_reliable ? el("p", { class: "preview-note", text: "The trial pages are from the middle of the book, so only the unit numbers are shown here. The full run works out the section numbers." }) : null,
    outdated ? el("p", { class: "preview-note", text: "The preview shows the plan as it is. Apply your changes to see them in the preview." }) : null,
    p.available ? el("p", { class: "preview-note", text: "Rows at the edges of the trial pages may be incomplete. Click a row to open it." }) : null,
    preview);
}

function columnsAfterId(columns, s) {
  // The first text column of the table preview: after the dotted ID or after the level and unit columns.
  return columns.find((c) => s.fields.some((f) => f.name === c)) ?? columns[1];
}

function columnChip(column, index, columns, edited, staged, onchange) {
  const chip = el("span", { class: `colchip${edited.has(column.name) ? " colchip--edited" : ""}` }, el("span", { text: column.name }));
  const add = (edit) => onchange([...staged, edit]);
  chip.append(
    el("button", { type: "button", title: "Move left", "aria-label": `Move ${column.name} left`, text: "‹", disabled: index === 0, onclick: () => add({ op: "move_column", column: column.name, index: index - 1 }) }),
    el("button", { type: "button", title: "Move right", "aria-label": `Move ${column.name} right`, text: "›", disabled: index === columns.length - 1, onclick: () => add({ op: "move_column", column: column.name, index: index + 1 }) }),
    el("button", { type: "button", title: "Rename", "aria-label": `Rename ${column.name}`, text: "✎", onclick: () => {
      const input = el("input", { class: "field field--sm", value: column.name, "aria-label": "New name" });
      const done = () => {
        const to = input.value.trim().toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "");
        if (to && to !== column.name) add({ op: "rename_column", column: column.name, to });
        else onchange(staged);
      };
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") done(); if (e.key === "Escape") onchange(staged); });
      chip.replaceChildren(input, el("button", { type: "button", text: "OK", onclick: done }));
      input.focus();
      input.select();
    } }),
    columns.length > 1 ? el("button", { type: "button", title: "Put this column's text into another column", "aria-label": `Merge ${column.name} into another column`, text: "⤵", onclick: () => {
      const select = el("select", { class: "field field--sm", "aria-label": "Into which column" },
        el("option", { value: "", text: `put ${column.name} into…` }),
        columns.filter((c) => c.name !== column.name).map((c) => el("option", { value: c.name, text: c.name })));
      select.addEventListener("change", () => (select.value ? add({ op: "merge_column", column: column.name, to: select.value }) : onchange(staged)));
      chip.replaceChildren(select);
      select.focus();
    } }) : null);
  return chip;
}

function questionsCard(plan, answers, onanswer) {
  if (!plan.questions.length) return null;
  return el("section", { class: "card" },
    el("div", { class: "card__head" }, el("h2", { text: "Questions for you" }), el("p", { class: "muted", text: "Answer what you can. Unanswered questions keep the plan as it is." })),
    el("div", { class: "questions" }, plan.questions.map((question) => {
      const box = el("textarea", { class: "field", rows: "2", placeholder: "Your answer (optional)" });
      box.value = answers[question] || "";
      box.addEventListener("input", () => { answers[question] = box.value; onanswer(); });
      const quick = (text) => el("button", { class: "button button--sm button--outline", type: "button", text, onclick: () => { box.value = text; answers[question] = text; onanswer(); } });
      return el("div", { class: "question" }, el("p", {}, ...mixed(question)), el("div", { class: "quick" }, quick("Yes"), quick("No")), box);
    })));
}

// ---------- the whole screen ----------

/**
 * plan: the plan data from the server. options.decide(action, body) sends a decision and returns a promise.
 * options.readOnly shows a past plan without the decision bar.
 */
export function renderPlan(root, plan, { decide = null, readOnly = false } = {}) {
  const local = { staged: [], answers: {}, feedback: "", onanswer: () => {} };

  function draw() {
    const changes = plan.changes.length && !readOnly
      ? el("div", { class: "changes" }, el("b", { text: `What changed in version ${plan.version}` }), el("ul", {}, plan.changes.map((c) => el("li", {}, ...mixed(c)))))
      : null;
    const note = plan.note ? el("div", { class: "banner banner--warn", text: plan.note }) : null;
    const limits = plan.limitations.length
      ? el("section", { class: "card" }, el("div", { class: "card__head" }, el("h2", { text: "Limitations" })), el("ul", {}, plan.limitations.map((l) => el("li", {}, ...mixed(l)))))
      : null;
    const rules = el("details", { class: "more" }, el("summary", { text: "The extraction rules the Analyst wrote (for checking details)" }), el("pre", { class: "json", text: plan.rules }));

    root.replaceChildren(...[
      changes, note,
      el("div", { class: "plan-grid" },
        el("div", {}, structureCard(plan), kindsCard(plan), limits),
        el("div", {}, outputCard(plan, local.staged, (next) => { local.staged = next; draw(); }, readOnly), readOnly ? null : questionsCard(plan, local.answers, () => local.onanswer()), rules)),
      readOnly ? null : decisionBar()].filter(Boolean));
  }

  function decisionBar() {
    const error = el("span", { class: "form-error", role: "alert" });
    const text = el("textarea", { class: "field", rows: "2", placeholder: "A correction or instruction for the Analyst, in Hindi or English. For example: footnotes on the bhashya go in their own column." });
    text.value = local.feedback;
    const send = el("button", { class: "button button--outline", type: "button", text: "Send to the Analyst" });
    const apply = el("button", { class: "button button--dark", type: "button", text: `Apply ${local.staged.length} change${local.staged.length === 1 ? "" : "s"}` });
    const approve = el("button", { class: "button button--primary", type: "button", text: "Approve and run" });
    const stop = el("button", { class: "button button--ghost", type: "button", text: "Stop" });
    const answered = () => Object.values(local.answers).some((a) => a.trim());
    const refresh = () => {
      local.feedback = text.value;
      const words = text.value.trim() || answered();
      send.disabled = !words;
      apply.hidden = !local.staged.length || Boolean(words);
      approve.disabled = local.staged.length > 0 || Boolean(words);
      approve.title = approve.disabled ? "Send or apply your changes first, or clear them" : "";
    };
    text.addEventListener("input", refresh);
    local.onanswer = refresh;
    const act = async (action, body) => {
      error.textContent = "";
      [send, apply, approve, stop].forEach((b) => (b.disabled = true));
      try {
        await decide(action, body);
      } catch (e) {
        error.textContent = e.message;
        refresh();
        stop.disabled = false;
      }
    };
    send.addEventListener("click", () => act("revise", { feedback: text.value.trim(), answers: local.answers, edits: local.staged }));
    apply.addEventListener("click", () => act("edit", { edits: local.staged }));
    approve.addEventListener("click", () => act("approve", {}));
    stop.addEventListener("click", () => act("stop", {}));
    refresh();
    const staged = local.staged.length
      ? el("div", { class: "staged" }, el("span", { text: `Waiting to be applied: ${local.staged.map(describeEdit).join("; ")}.` }),
        el("button", { class: "link-button", type: "button", text: "Discard", onclick: () => { local.staged = []; draw(); } }))
      : null;
    return el("div", { class: "decision" },
      staged,
      el("div", { class: "decision__row" },
        el("div", {}, el("label", { class: "field-label", text: "Something to change?" }), text),
        el("div", { class: "decision__buttons" }, stop, apply, send, approve)),
      error);
  }

  draw();
}
