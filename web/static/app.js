// The app shell: routes, the help dialog and the settings the server reports.
//   #/              your runs
//   #/new           a new extraction (the book, then the request)
//   #/runs/<id>     one run: plan, progress, output   (#/runs/<id>/review opens a tab)

import { renderNew } from "./newrun.js";
import { leaveRun, renderRun } from "./run.js";
import { renderRuns } from "./runs.js";
import { $, api, el } from "./ui.js";

let config = null;

function route() {
  const view = $("view");
  const [, page, id, tab] = (location.hash.replace(/^#/, "") || "/").split("/");
  leaveRun();
  document.querySelectorAll("[data-nav]").forEach((link) => link.classList.toggle("is-current",
    (link.dataset.nav === "runs" && !page) || (link.dataset.nav === "new" && page === "new")));
  if (page === "new") {
    document.title = "New extraction · Veda Script Automation";
    renderNew(view, config);
  } else if (page === "runs" && id) {
    document.title = "Run · Veda Script Automation";
    renderRun(view, decodeURIComponent(id), tab || "output");
  } else {
    document.title = "Veda Script Automation";
    renderRuns(view, config);
  }
  view.focus({ preventScroll: true });
  window.scrollTo({ top: 0 });
}

async function init() {
  try {
    config = await api("/api/config");
  } catch (error) {
    $("view").replaceChildren(el("div", { class: "container" }, el("div", { class: "banner banner--error", text: `The server could not be reached: ${error.message}` })));
    return;
  }
  $("keyWarning").hidden = config.key_configured;

  const help = $("help");
  $("helpButton").addEventListener("click", () => help.showModal());
  help.querySelector("[data-close]").addEventListener("click", () => help.close());
  help.addEventListener("click", (event) => { if (event.target === help) help.close(); });

  // Links from before the app had routes: ?run=<id>&tab=review
  const params = new URLSearchParams(location.search);
  if (params.get("run")) {
    history.replaceState(null, "", `${location.pathname}#/runs/${params.get("run")}${params.get("tab") ? `/${params.get("tab")}` : ""}`);
  }
  window.addEventListener("hashchange", route);
  route();
}

init();
