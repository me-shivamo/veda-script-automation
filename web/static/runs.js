// The home page: every run, newest first.

import { api, el, money, plural, status, when } from "./ui.js";

export async function renderRuns(view, config) {
  const list = el("div", { class: "runlist" }, el("p", { class: "muted", text: "Loading runs…" }));
  view.replaceChildren(el("div", { class: "container" },
    el("div", { class: "page-head" },
      el("div", {}, el("p", { class: "eyebrow", text: "Your work" }), el("h1", { text: "Runs" })),
      el("div", { class: "page-head__side" }, el("a", { class: "button button--primary", href: "#/new", text: "New extraction" }))),
    config.temporary_storage ? el("div", { class: "banner banner--warn", text: "This server clears its runs whenever it restarts or goes to sleep. Download any output you want to keep." }) : null,
    list));

  let runs;
  try {
    runs = await api("/api/runs");
  } catch (error) {
    list.replaceChildren(el("div", { class: "banner banner--error", text: error.message }));
    return;
  }
  if (!runs.length) {
    list.replaceWith(welcome());
    return;
  }
  list.replaceChildren(...runs.map((run) => {
    const meta = [when(run.created), run.rows !== null && run.rows !== undefined ? plural(run.rows, "row") : null, run.cost_usd ? money(run.cost_usd) : null, String(run.model || "").replace(/^subscription\/(.*)$/, "$1 (Claude plan)")]
      .filter(Boolean).join(" · ");
    return el("a", { class: "runrow", href: `#/runs/${run.id}` },
      el("div", {}, el("div", { class: "runrow__name", text: run.name }), el("div", { class: "runrow__meta", text: meta })),
      status(run.phase),
      el("span", { class: "runrow__go", "aria-hidden": "true", text: "→" }));
  }));
}

function welcome() {
  return el("section", { class: "welcome" },
    el("div", { class: "welcome__image", role: "img", "aria-label": "The Statue of Oneness: Adi Shankaracharya as a young monk" }),
    el("div", { class: "welcome__copy" },
      el("p", { class: "eyebrow", text: "Scanned page to structured text" }),
      el("h1", { text: "Every shloka, in its place." }),
      el("p", { text: "Load the OCR text of a Sanskrit book and say what you need. The agents work out how this book is laid out and show you the plan before anything is extracted." }),
      el("ol", {},
        el("li", { text: "Give the book and your request." }),
        el("li", { text: "Check the plan: the sections found, each kind of text, and the output file as it will look." }),
        el("li", { text: "Approve it, and read or download the output." })),
      el("div", { class: "actions" }, el("a", { class: "button button--lg button--primary", href: "#/new", text: "Start your first extraction" }))));
}
