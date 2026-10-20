// New extraction: step 1 the book, step 2 the request and the output wanted. Then the run page takes over.

import { api, el, post, rail } from "./ui.js";

// Kept while the page is open, so going back and forth between the steps loses nothing.
export const draft = {
  step: 1, name: "", input: "", request: "", examples: "", model: "", ask: true,
  output: { shape: "", id_style: "", columns: "", section_text: "" },
};

export function prefill(source) {
  Object.assign(draft, { step: 1, name: source.name || "", input: source.input || "", request: source.request || "", examples: source.examples || "" });
}

const STEPS = [["The book", "OCR text"], ["Your request", "What to extract"], ["The plan", "You check it"], ["Output", "Read and download"]];

export function renderNew(view, config) {
  const body = el("div");
  view.replaceChildren(el("div", { class: "container wizard" },
    el("p", { class: "eyebrow", text: "New extraction" }),
    el("h1", { text: draft.step === 1 ? "Load the book" : "Say what you need" }),
    rail(STEPS, draft.step - 1),
    body));
  body.append(draft.step === 1 ? bookStep(view, config) : requestStep(view, config));
}

function pagesOf(text) {
  return text.split(/^\s*-{3,}\s*$/m).filter((page) => page.trim()).length;
}

function bookStep(view, config) {
  const stats = el("p", { class: "stats" });
  const area = el("textarea", { id: "input", class: "field field--sanskrit", lang: "sa", rows: "14", spellcheck: "false", placeholder: "…or paste the OCR text here" });
  area.value = draft.input;
  const next = el("button", { class: "button button--lg button--primary", type: "button", text: "Next: your request", onclick: () => goStep(view, config, 2) });
  const update = () => {
    draft.input = area.value;
    const text = area.value;
    next.disabled = !text.trim();
    stats.textContent = text.trim()
      ? `${pagesOf(text).toLocaleString()} page(s) · ${text.split("\n").length.toLocaleString()} lines · ${text.length.toLocaleString()} characters`
      : "No text loaded.";
  };
  area.addEventListener("input", update);
  const load = (file) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      area.value = reader.result;
      if (!draft.name) draft.name = file.name.replace(/\.[^.]+$/, "");
      update();
    };
    reader.readAsText(file, "utf-8");
  };
  const fileInput = el("input", { type: "file", accept: ".md,.txt,text/plain,text/markdown", hidden: true, onchange: (e) => load(e.target.files[0]) });
  const zone = el("label", { class: "dropzone" }, fileInput,
    el("span", { "aria-hidden": "true", text: "⇪" }),
    el("span", {}, "Drop a ", el("strong", { text: ".md" }), " or ", el("strong", { text: ".txt" }), " file here, or ", el("u", { text: "choose a file" })));
  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("is-over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("is-over"));
  zone.addEventListener("drop", (e) => { e.preventDefault(); zone.classList.remove("is-over"); load(e.dataTransfer.files[0]); });

  const samples = el("div", { class: "samples" }, config.samples.map((sample) =>
    el("button", { class: "link-button", type: "button", text: `Use the sample book: ${sample.name}`, onclick: async () => {
      const data = await api(`/api/samples/${sample.id}`);
      Object.assign(draft, { input: data.input, request: data.request, examples: data.examples, name: data.name });
      area.value = data.input;
      update();
    } })));

  const card = el("section", { class: "card" },
    el("div", { class: "card__head" }, el("h2", { text: "The book" }), el("p", { class: "muted", text: "OCR text, with pages separated by lines of ---" })),
    zone, samples,
    el("label", { class: "field-label", for: "input", text: "Book text" }), area, stats,
    el("div", { class: "actions actions--end" }, next));
  update();
  return card;
}

function choice(name, value, title, detail, current, onpick) {
  return el("label", { class: "choice" },
    el("input", { type: "radio", name, value, checked: current === value, onchange: () => onpick(value) }),
    el("b", { text: title }), el("span", { text: detail }));
}

function requestStep(view, config) {
  const error = el("span", { class: "form-error", role: "alert" });
  const request = el("textarea", { id: "request", class: "field", rows: "8",
    placeholder: "One row per mantra, with the columns: num, mantra, bhashya. Put side headings at the start of the bhashya. Remove page headers and footnotes." });
  request.value = draft.request;
  request.addEventListener("input", () => (draft.request = request.value));
  const examples = el("textarea", { class: "field field--sanskrit", lang: "sa", rows: "4", spellcheck: "false", placeholder: '"num","mantra","bhashya"' });
  examples.value = draft.examples;
  examples.addEventListener("input", () => (draft.examples = examples.value));

  const out = draft.output;
  const shapes = el("div", { class: "choices", role: "radiogroup", "aria-label": "Output shape" },
    choice("shape", "", "Let the AI decide", "From your request; a table if it says nothing.", out.shape, (v) => (out.shape = v)),
    choice("shape", "table", "Table", "One row per mantra, one column per kind of text.", out.shape, (v) => (out.shape = v)),
    choice("shape", "blocks", "Blocks", "One row per piece of text: its place, its layer, the text.", out.shape, (v) => (out.shape = v)),
    choice("shape", "tree", "Tree", "Nested JSON in the book's own shape: sections inside sections.", out.shape, (v) => (out.shape = v)));
  shapes.style.gridTemplateColumns = "repeat(auto-fit, minmax(10.5rem, 1fr))";
  const idStyle = el("select", { class: "field", onchange: (e) => (out.id_style = e.target.value) },
    el("option", { value: "", text: "Let the AI decide" }),
    el("option", { value: "dotted", text: "One ID, like 1.3.5" }),
    el("option", { value: "columns", text: "One column per level (adhyaya, khanda, mantra)" }));
  idStyle.value = out.id_style;
  const sectionText = el("select", { class: "field", onchange: (e) => (out.section_text = e.target.value) },
    el("option", { value: "", text: "Let the AI decide" }),
    el("option", { value: "merge", text: "With the text next to them" }),
    el("option", { value: "rows", text: "Apart: a row for each section" }),
    el("option", { value: "columns", text: "Apart: in their own columns" }));
  sectionText.value = out.section_text;
  const columns = el("input", { class: "field", type: "text", placeholder: "for example: num, mantra, bhashya, tippani", value: out.columns,
    oninput: (e) => (out.columns = e.target.value) });

  const name = el("input", { class: "field", type: "text", maxlength: "80", placeholder: "chandogya-upanishad", value: draft.name, oninput: (e) => (draft.name = e.target.value) });
  const model = el("select", { class: "field", onchange: (e) => (draft.model = e.target.value) },
    config.models.map((m) => el("option", { value: m.id, text: `${m.label} (${m.note})` })));
  model.value = draft.model || config.default_model;
  draft.model = model.value;
  const ask = el("input", { type: "checkbox", checked: draft.ask, onchange: (e) => (draft.ask = e.target.checked) });

  const start = el("button", { class: "button button--lg button--primary", type: "button", text: draft.ask ? "Read the book and show me the plan" : "Run the extraction" });
  ask.addEventListener("change", () => (start.textContent = draft.ask ? "Read the book and show me the plan" : "Run the extraction"));
  start.addEventListener("click", async () => {
    error.textContent = "";
    if (!draft.input.trim()) return void (error.textContent = "Load or paste the book text first.");
    if (!draft.request.trim()) return void (error.textContent = "Write your request first.");
    start.disabled = true;
    try {
      const { id } = await post("/api/runs", {
        name: draft.name.trim() || "book", input: draft.input, request: draft.request.trim(), examples: draft.examples.trim(),
        model: draft.model, ask_approval: draft.ask,
        output: { shape: out.shape, id_style: out.id_style, section_text: out.section_text, columns: out.columns.split(",").map((c) => c.trim()).filter(Boolean) },
      });
      draft.step = 1;
      location.hash = `#/runs/${id}`;
    } catch (e) {
      error.textContent = e.message;
      start.disabled = false;
    }
  });

  return el("div", {},
    el("section", { class: "card" },
      el("div", { class: "card__head" }, el("h2", { text: "Your request" }), el("p", { class: "muted", text: "Say what one row is, which columns you want and what to leave out. Hindi or English." })),
      el("label", { class: "field-label", for: "request", text: "Request" }), request,
      el("details", { class: "more", open: draft.examples ? true : undefined },
        el("summary", { text: "Example rows (optional, helps a lot)" }),
        el("p", { class: "hint", text: "A few rows of the output you want, as CSV." }), examples),
      el("fieldset", { class: "fieldset" },
        el("legend", { text: "The output file (optional)" }),
        el("p", { class: "hint", text: "You can also change all of this on the plan, after the AI has read the book." }),
        el("p", { class: "field-label", text: "Shape" }), shapes,
        el("div", { class: "field-row" },
          el("div", {}, el("label", { class: "field-label", text: "Row ID" }), idStyle),
          el("div", {}, el("label", { class: "field-label", text: "Section titles and closing lines" }), sectionText)),
        el("label", { class: "field-label", text: "Columns, in order" }), columns)),
    el("section", { class: "card" },
      el("div", { class: "field-row" },
        el("div", {}, el("label", { class: "field-label", text: "Name of this run" }), name),
        el("div", {}, el("label", { class: "field-label", text: "Model" }), model)),
      el("p", { class: "hint", text: "“Your Claude plan” runs through Claude Code on this computer and counts towards your plan's usage limits. “API key” is billed per run, and the run shows its cost when it ends." }),
      el("label", { class: "check" }, ask, " Show me the plan before the full run (recommended)"),
      el("div", { class: "actions" },
        el("button", { class: "button button--lg button--outline", type: "button", text: "Back", onclick: () => goStep(view, config, 1) }),
        el("span", { class: "spacer" }), error, start)));
}

function goStep(view, config, step) {
  draft.step = step;
  renderNew(view, config);
  window.scrollTo({ top: 0 });
}
