// One run's page. It follows the run as it moves on: reading the book, the plan, labelling, the output.

import { prefill } from "./newrun.js";
import { renderOutput } from "./output.js";
import { renderPlan } from "./plan.js";
import { api, duration, el, post, rail, status } from "./ui.js";

const STEPS = [
  ["Book and request", "Given"],
  ["The plan", "The Analyst reads the book"],
  ["Labelling", "Every line is marked"],
  ["Second look", "Doubts are checked"],
  ["Output", "Ready to read"],
];
const STEP_OF = { starting: 1, analysing: 1, awaiting_approval: 1, labelling: 2, reviewing: 3, assembling: 3, done: 4, needs_review: 4 };
const ENDED = new Set(["stopped", "failed", "interrupted"]);

let current = null; // { id, timer, mode, version, source }
const sent = {}; // the last correction sent per run, shown back while the Analyst works on it

export function leaveRun() {
  if (current) clearTimeout(current.timer);
  current = null;
}

export async function renderRun(view, id, tab) {
  leaveRun();
  const me = { id, timer: null, mode: "", version: 0, source: null, tab };
  current = me;
  const head = el("div", { class: "page-head" });
  const railBox = el("div");
  const body = el("div");
  view.replaceChildren(el("div", { class: "container" }, head, railBox, body));
  try {
    me.source = await api(`/api/runs/${id}/input`);
  } catch (error) {
    body.replaceChildren(el("div", { class: "banner banner--error", text: error.message }), el("div", { class: "actions" }, el("a", { class: "button button--outline", href: "#/", text: "Back to your runs" })));
    return;
  }
  const poll = async () => {
    if (current !== me) return;
    let s;
    try {
      s = await api(`/api/runs/${id}`);
    } catch {
      me.timer = setTimeout(poll, 4000);
      return;
    }
    if (current !== me) return;
    draw(s);
    if (s.active) me.timer = setTimeout(poll, s.phase === "awaiting_approval" ? 2500 : 1500);
  };

  function draw(s) {
    const step = STEP_OF[s.phase] ?? 1;
    head.replaceChildren(
      el("div", {}, el("p", { class: "eyebrow", text: "Run" }), el("h1", { text: s.name })),
      el("div", { class: "page-head__side" },
        status(s.phase),
        el("span", { class: "muted", text: [s.model, duration(s.elapsed)].filter(Boolean).join(" · ") }),
        s.active ? el("button", { class: "button button--sm button--outline", type: "button", text: "Stop run",
          onclick: () => post(`/api/runs/${id}/stop`).then(poll, poll) }) : null));
    railBox.replaceChildren(rail(STEPS, step, { busy: s.active && s.phase !== "awaiting_approval", done: s.has_output ? 4 : step - 1 }));

    let mode = "working";
    if (s.phase === "awaiting_approval" && s.proposal) mode = "plan";
    else if (s.has_output && !s.active) mode = "output";
    else if (ENDED.has(s.phase)) mode = "ended";

    if (mode === "plan") {
      if (me.mode !== "plan" || me.version !== s.proposal.version) {
        me.version = s.proposal.version;
        const planBox = el("div");
        body.replaceChildren(planBox, s.proposal.version > 1 ? earlier(s.proposal.version) : null);
        renderPlan(planBox, s.proposal, { decide: (action, payload) => decide(action, payload) });
        if (s.proposal.version > 1) window.scrollTo({ top: 0 });
      }
    } else if (mode === "output") {
      if (me.mode !== "output") {
        body.replaceChildren(el("p", { class: "muted", text: "Loading the output…" }));
        renderOutput(body, id, { tab: me.tab, source: me.source }).catch((error) => body.replaceChildren(el("div", { class: "banner banner--error", text: error.message })));
      }
    } else if (mode === "ended") {
      body.replaceChildren(ended(s));
    } else {
      body.replaceChildren(working(s));
    }
    me.mode = mode;
  }

  async function decide(action, payload) {
    await post(`/api/runs/${id}/decision`, { action, ...payload });
    if (action === "revise") sent[id] = payload;
    me.mode = "";
    me.version = 0;
    clearTimeout(me.timer);
    poll();
  }

  // Earlier versions of the plan, loaded when opened.
  function earlier(version) {
    const box = el("div");
    const details = el("details", { class: "more" }, el("summary", { text: `Earlier versions of this plan (${version - 1})` }), box);
    details.addEventListener("toggle", async () => {
      if (!details.open || box.childElementCount) return;
      const plans = (await api(`/api/runs/${id}/plans`)).filter((p) => p.version < version).reverse();
      for (const plan of plans) {
        const holder = el("div");
        box.append(el("details", { class: "more" }, el("summary", { text: `Version ${plan.version}` }), holder));
        renderPlan(holder, plan, { readOnly: true });
      }
    });
    return el("section", { class: "card" }, details);
  }

  function working(s) {
    const lines = {
      starting: ["Starting", "The run is being set up."],
      analysing: s.log.some((l) => l.startsWith("Analyst is revising"))
        ? ["The Analyst is revising the plan", "It reads your correction, changes the plan and tries it on a few pages again."]
        : ["The Analyst is reading the book", "It works out the sections, the kinds of text and the numbering, writes a plan and tries it on a few pages. This usually takes one to three minutes."],
      awaiting_approval: ["Updating the plan", "Your changes to the structure are being applied."],
      labelling: ["Labelling every line", "Labelers mark each line of the book, a few pages at a time."],
      reviewing: ["Taking a second look", "The Reviewer checks what the labelers and the checks were unsure of."],
      assembling: ["Building the output", "Code copies the text into rows and checks it is exactly the book's."],
    }[s.phase] || ["Working", ""];
    const progress = s.phase === "labelling" && s.chunks_total
      ? el("div", {}, el("div", { class: "progress", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": String(s.chunks_total), "aria-valuenow": String(s.chunks_done) },
          el("div", { style: `width:${Math.round((100 * s.chunks_done) / s.chunks_total)}%` })),
        el("p", { class: "hint", text: `${s.chunks_done} of ${s.chunks_total} parts done` }))
      : null;
    const last = sent[id];
    const echo = s.phase === "analysing" && last
      ? el("div", { class: "echo" }, el("b", { text: "You asked: " }),
        [last.feedback, ...Object.entries(last.answers || {}).filter(([, a]) => a.trim()).map(([q, a]) => `${q} → ${a}`)].filter(Boolean).join("\n"))
      : null;
    return el("section", { class: "card" },
      el("div", { class: "working" }, el("span", { class: "spinner", "aria-hidden": "true" }),
        el("div", {}, el("h2", { text: lines[0] }), el("p", { class: "muted", text: lines[1] }), progress, echo)),
      log(s));
  }

  function ended(s) {
    const message = s.error
      || (s.phase === "interrupted" ? "This run was cut off before it finished, probably because the server restarted." : "This run was stopped before it finished.");
    return el("section", { class: "card" },
      el("div", { class: `banner ${s.phase === "failed" ? "banner--error" : "banner--warn"}`, text: message }),
      el("div", { class: "actions" },
        el("button", { class: "button button--primary", type: "button", text: "Start again with this book", onclick: () => { prefill(me.source); location.hash = "#/new"; } }),
        el("a", { class: "button button--outline", href: "#/", text: "Back to your runs" })),
      log(s));
  }

  await poll();
}

const logOpen = { value: false };

function log(s) {
  if (!s.log || !s.log.length) return null;
  const box = el("details", { class: "more log", open: logOpen.value || undefined, ontoggle: (e) => (logOpen.value = e.currentTarget.open) },
    el("summary", { text: "Activity log" }), el("pre", { text: s.log.join("\n") }));
  return box;
}
