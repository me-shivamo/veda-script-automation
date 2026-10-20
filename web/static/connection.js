// The Claude connection dialog: your Claude Pro or Max plan through Claude Code, or an API key.
// It can only change anything on a copy running on your own computer; the values go to the .env file there.

import { $, el, post } from "./ui.js";

let onChange = () => {};

export function setupConnection(config, changed) {
  onChange = changed;
  const dialog = $("connection");
  $("connectionButton").hidden = !config.connection.local;
  for (const opener of document.querySelectorAll("[data-open-connection]")) {
    opener.addEventListener("click", () => { render(dialog, config.connection); dialog.showModal(); });
  }
  dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
}

function render(dialog, connection) {
  const head = el("div", { class: "dialog__head" },
    el("h2", { id: "connectionTitle", text: "Claude connection" }),
    el("button", { class: "iconbutton iconbutton--light", type: "button", "aria-label": "Close", text: "×", onclick: () => dialog.close() }));
  if (!connection.local) {
    dialog.replaceChildren(head, el("p", { class: "lead", text: "This copy is reachable by other people, so it runs only with the API key in its settings (ANTHROPIC_API_KEY). A Claude plan can be used only on a copy running on your own computer." }));
    return;
  }
  dialog.replaceChildren(head,
    el("p", { class: "lead", text: "Runs can use your Claude Pro or Max plan through Claude Code on this computer, or an Anthropic API key, which is billed for each run. What you save here stays in the .env file on this computer." }),
    planSection(dialog, connection),
    apiSection(dialog, connection));
}

function planSection(dialog, connection) {
  const state = { token: "Connected with the saved token", login: "Connected through this computer's Claude Code login" }[connection.plan] || "Not connected";
  const input = el("input", { class: "field", type: "password", autocomplete: "off", spellcheck: "false", placeholder: "sk-ant-oat…" });
  const message = el("p", { class: "hint", role: "status" });
  return el("section", { class: "fieldset connection" },
    el("h3", {}, "Your Claude plan ", el("span", { class: `pill ${connection.plan ? "pill--ok" : ""}`, text: state })),
    el("ol", { class: "steps" },
      el("li", {}, "Open a terminal and run ", el("code", { text: "claude setup-token" }), "."),
      el("li", { text: "Sign in to claude.ai in the browser window it opens." }),
      el("li", { text: "Copy the token it prints (it starts with sk-ant-oat) and paste it here. It is valid for a year." })),
    el("p", { class: "hint", text: "If you already use Claude Code on this computer, you can skip this: its login is used. Runs on your plan count towards its usage limits." }),
    el("label", { class: "field-label", text: "Token" }), input,
    el("div", { class: "connection__actions" },
      el("button", { class: "button button--sm button--primary", type: "button", text: "Save",
        onclick: () => save(dialog, { plan_token: input.value }, message, "plan") }),
      el("button", { class: "button button--sm button--outline", type: "button", text: "Test the connection",
        onclick: () => test("plan", message) }),
      connection.plan === "token" ? el("button", { class: "button button--sm button--ghost", type: "button", text: "Remove the token",
        onclick: () => save(dialog, { plan_token: "" }, message) }) : null),
    message);
}

function apiSection(dialog, connection) {
  const state = connection.api_key ? `Saved, ends in …${connection.api_key_end}` : "None saved";
  const input = el("input", { class: "field", type: "password", autocomplete: "off", spellcheck: "false", placeholder: "sk-ant-api…" });
  const message = el("p", { class: "hint", role: "status" });
  return el("section", { class: "fieldset connection" },
    el("h3", {}, "API key (optional) ", el("span", { class: `pill ${connection.api_key ? "pill--ok" : ""}`, text: state })),
    el("p", { class: "hint", text: "For runs billed to an Anthropic account instead of your plan. Create a key at platform.claude.com, under Settings → API Keys." }),
    el("label", { class: "field-label", text: "API key" }), input,
    el("div", { class: "connection__actions" },
      el("button", { class: "button button--sm button--primary", type: "button", text: "Save",
        onclick: () => save(dialog, { api_key: input.value }, message, "api") }),
      el("button", { class: "button button--sm button--outline", type: "button", text: "Test the key", disabled: !connection.api_key,
        onclick: () => test("api", message) }),
      connection.api_key ? el("button", { class: "button button--sm button--ghost", type: "button", text: "Remove the key",
        onclick: () => save(dialog, { api_key: "" }, message) }) : null),
    message);
}

async function save(dialog, change, message, testAfter) {
  const value = Object.values(change)[0];
  if (testAfter && !value.trim()) {
    show(message, false, "Paste the value first.");
    return;
  }
  try {
    const connection = await post("/api/connection", change);
    await onChange();
    render(dialog, connection);
  } catch (error) {
    show(message, false, error.message);
    return;
  }
  // The dialog was drawn again; find the new message line of the same section.
  const sections = dialog.querySelectorAll(".connection");
  const fresh = sections[testAfter === "api" || "api_key" in change ? 1 : 0].querySelector("[role=status]");
  if (testAfter) await test(testAfter, fresh);
  else show(fresh, true, "Removed.");
}

async function test(via, message) {
  show(message, null, via === "plan" ? "Asking Claude through Claude Code… this takes a few seconds." : "Checking the key…");
  try {
    const result = await post("/api/connection/test", { via });
    show(message, result.ok, result.message);
  } catch (error) {
    show(message, false, error.message);
  }
}

function show(message, ok, text) {
  message.textContent = text;
  message.className = ok === null ? "hint" : ok ? "hint hint--ok" : "form-error";
}
