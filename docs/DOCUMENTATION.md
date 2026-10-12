# Veda Script Automation: documentation

This guide covers how the tool works, how to use it, how to deploy it on Render, and how the code is organised.

## Contents

1. [What this is](#1-what-this-is)
2. [How it works](#2-how-it-works)
3. [Getting started on your computer](#3-getting-started-on-your-computer)
4. [Using the web page](#4-using-the-web-page)
5. [Preparing the book and writing the request](#5-preparing-the-book-and-writing-the-request)
6. [Checking the output](#6-checking-the-output)
7. [Deploying on Render](#7-deploying-on-render)
8. [Settings](#8-settings)
9. [The command line](#9-the-command-line)
10. [Costs](#10-costs)
11. [Security and privacy](#11-security-and-privacy)
12. [Troubleshooting](#12-troubleshooting)
13. [For developers](#13-for-developers)
14. [Limits and what is not built yet](#14-limits-and-what-is-not-built-yet)

---

## 1. What this is

Printed Sanskrit books are scanned and run through OCR. The OCR text has everything mixed together: the root text, the commentary, side headings, page headers, footnotes and stray marks.

This tool turns that text into a clean table with **one row per shloka** (or mantra, or sutra). Each row holds the root text and whatever is printed with it: commentary, meaning, headings.

You tell it in plain words what you want. For example:

> One row per mantra, with the columns num, upanishad and bhashya. Put side headings at the start of the bhashya. Remove page headers and footnotes.

The output is a CSV or JSON file, plus a short list of places a person should check.

### The one rule it never breaks

**The AI never writes any of the output text.** It only decides what each line of the book is. Code then copies the original text into the right place. So the output cannot contain invented text, a translation the book does not print, or a "corrected" spelling.

The few characters the code does change, such as a misread verse number, are all listed for you.

---

## 2. How it works

```
OCR text ──► pieces ──► Analyst ──► you approve ──► Labelers ──► code builds rows ──► Reviewer ──► output
            (code)      (AI)        the plan        (AI)         and checks them      (AI)
```

### Step by step

1. **Code cuts the book into small numbered pieces.** One piece is one printed line. A line is cut again after every verse number, so two shlokas on one line become two pieces.

2. **The Analyst reads the book and writes a plan.** The plan says which columns the output has, which kinds of line this book contains, how its sections are numbered, and how to tell the kinds of line apart. The Analyst tries the plan on a few pages and shows you sample rows. Nothing else happens until you approve.

3. **Labelers mark every piece.** The book is split into parts of about six pages, and several parts are worked on at once. A Labeler gives each piece a label, such as "root text of mantra 5", "commentary on mantra 5", "side heading" or "page header". It then runs the checks on its own work, and it is not allowed to finish while a check fails.

4. **Code builds the rows.** It copies the text of the pieces into rows, in reading order. It works out the row numbers itself by counting section endings (`इति प्रथमः खण्डः`), so one wrong label cannot shift the numbering of the rest of the book.

5. **Code checks the whole book.** See [the checks](#the-checks) below.

6. **The Reviewer takes a second look.** It sees the whole book, where a Labeler saw only a few pages. It goes through everything the checks found and everything a Labeler was unsure about. It fixes the labels it can. What it cannot settle stays on your review list.

### The three agents

| Agent | Runs | Its job | Its instructions |
|---|---|---|---|
| Analyst | Once per book | Understand the layout and write the plan | [agent/prompts/analyst.md](../agent/prompts/analyst.md) |
| Labeler | Once per part of the book | Label every piece in that part | [agent/prompts/labeler.md](../agent/prompts/labeler.md) |
| Reviewer | Once per book, after the rows are built | Fix or flag whatever is still doubtful | [agent/prompts/reviewer.md](../agent/prompts/reviewer.md) |

All three also read two shared files: [domain.md](../agent/prompts/domain.md), which describes how printed Sanskrit books are laid out, and [rules.md](../agent/prompts/rules.md), which holds the rules that always apply. These are plain text files. You can read and edit them.

### The checks

After labelling, code checks that:

- every piece has exactly one label, so nothing is silently lost
- verse numbers run in order inside each section, with no gaps
- each passage ends at its own verse number, and no text after a number is given that same number
- every row has the columns the plan says it must have
- nothing that looks like book text was thrown away as a page header or footnote
- nothing that looks like a page header or footnote was kept as book text
- a line labelled as a heading does not read like a sentence or verse

A Labeler has to fix a failed check, or flag the pieces with a reason. Flagged pieces go to the Reviewer, and then to you if they are still in doubt.

---

## 3. Getting started on your computer

### What you need

- **uv**, which installs Python and the packages. Install it from [docs.astral.sh/uv](https://docs.astral.sh/uv/).
- **An OpenRouter API key.** OpenRouter is the service that gives access to the AI models. Create a key at [openrouter.ai/keys](https://openrouter.ai/keys) and add a few dollars of credit.

### Set it up

```bash
git clone https://github.com/me-shivamo/veda-script-automation.git
cd veda-script-automation
uv sync
cp .env.example .env
```

Open the new `.env` file and paste your key after `OPENROUTER_API_KEY=`. This file stays on your computer and is never uploaded to GitHub.

### Start it

```bash
uv run python -m web
```

Open **http://localhost:8000** in your browser. To stop the server, press `Ctrl+C` in the terminal.

To try it without your own book, click **Use the sample book** on the page.

---

## 4. Using the web page

### Step 1: the book

![Loading a book and writing the request](images/workspace.jpg)

Drop a `.md` or `.txt` file onto the box, click **choose a file**, or paste the text into the box. The line under the box shows how many pages, lines and characters were loaded.

### Step 2: your request

Write what you want in the **Request** box. [Section 5](#5-preparing-the-book-and-writing-the-request) explains how to write a good one.

The other fields:

- **Example rows (optional).** A few rows of the output you want, as CSV. This is the most useful thing you can give the agents.
- **Name of this run.** How the run appears in the list of past runs, and the name of the downloaded file.
- **Model.** Which AI model to use. Only GPT-5.6 Luna has been tested.
- **Show me the plan before the full run.** Leave this ticked unless you have already run the same kind of book and trust the result.

Click **Run extraction**.

### Step 3: progress and the plan

The page shows five stages:

| Stage | What is happening |
|---|---|
| Reading the book | The Analyst is working out the layout. About a minute. |
| Your approval | The plan is ready and waiting for you. |
| Labelling | Labelers are marking every line. The page shows how many parts are done. |
| Second look | The Reviewer is checking doubts. |
| Output ready | The rows are built. |

When the plan is ready, it appears on the page:

![The plan, waiting for approval](images/plan.jpg)

Read the description and look at the sample rows. Then choose one:

- **Approve and run** if the sample rows look right.
- **Send the change** after typing what should be different, for example "put the closing line of each section in its own column". The Analyst revises the plan and shows it again.
- **Stop this run** to cancel.

Rows at the edges of the sample can be incomplete, because the sample is cut from the middle of the book. That is normal.

### Step 4: the output

![The output](images/output.jpg)

The boxes at the top show the number of rows, how many items need review, how many characters the code changed, the time taken and the cost.

| Tab | What it shows |
|---|---|
| Rows | The table. Click a row to open it fully. Use the search box to find text. A gold dot marks a row with something to review. |
| To review | The places a person should check. Click a row number to jump to that row. |
| Text changes | Every character the code changed while copying. |
| Book text | The text you loaded. |
| Request | The request you wrote. |

**Download CSV** and **Download JSON** save the rows to your computer.

### Past runs

The bottom of the page lists earlier runs. Clicking one loads its book text, request and output back into the page.

To share a run with someone who can reach the same server, copy the address from the browser. It looks like `…/?run=20261012-103428-my-book`. Adding `&tab=review` opens it on the review list.

---

## 5. Preparing the book and writing the request

### The book text

- **Pages are separated by a line containing only `---`.** Page breaks matter: they are how page headers and footnotes are recognised.
- **Each shloka, mantra or sutra must end with its number**, as printed: `॥ ५ ॥`. The tool relies on these numbers.
- Leave OCR marks such as `##` and `[^1]:` as they are. The agents know what they mean.
- Do not correct spellings or tidy the text first. The agents work with the text as it is, and the output keeps it exactly.

### The request

A good request answers four questions.

1. **What is one row?** "One row per mantra."
2. **Which columns, with which names?** "Columns: num, upanishad, bhashya."
3. **Where does the extra text go?** "Side headings go at the start of the bhashya of the mantra that follows. Closing lines such as इति प्रथमः खण्डः go at the end of the bhashya before them."
4. **What should be removed?** "Remove the book title, page headers, footnotes and printer's numbers."

Also say how you want the text itself treated, if you care:

- "Keep hyphens at line ends" or "join words that were split across lines".
- "Turn double quotes into single quotes."

The request for the sample book is in [examples/mandala-brahmana/request.txt](../examples/mandala-brahmana/request.txt). Use it as a pattern.

### What you cannot ask for

The tool only copies what the book prints. If you ask for a translation and the book has none, that column stays empty and the plan tells you so.

### Example rows

If you already have a few rows of the output you want, paste them into **Example rows**. Use the same column names as in your request. Three to five rows are enough. The Analyst compares them with the book to work out details you did not think to mention.

---

## 6. Checking the output

No run should be trusted blindly. The tool tells you where to look.

### The "To review" list

| Label | What it means | What to do |
|---|---|---|
| Verse number corrected | The OCR text has a verse number that breaks the sequence, such as ९ where ५ belongs. The number in the output was corrected. | Check the printed page and confirm. |
| Flagged by an agent | An agent was unsure about a line and explains why. | Read the reason and check that row. |
| Missing part | A row lacks a column it should have, for example a mantra with no commentary. | Check whether the book really has none. |
| Possibly dropped text | A line was removed as a page header or footnote but looks like book text. | Check that nothing real was lost. |
| Possible page header in text | A line kept in a row looks like a page header or footnote. | Check that row, usually where a passage crosses a page. |
| Possible heading mistake | A line was treated as a heading but reads like a sentence. | Check the start of that row. |
| Numbering doubt, Number missing, Numbers out of order | The verse numbers do not run in order here. | Check for a verse missing from the OCR. |
| Duplicated page | The OCR contains the same page twice. | Remove one copy from the book text and run again. |
| Section does not start at 1, Text at the start, Text at the end, Line not labelled | Something unusual at the edge of a section or of the book. | Read the message and check that row. |

A run that ends as **Done** has no errors left. A run that ends as **Done, with items to review** still has unresolved errors, so check its list with extra care.

### The "Text changes" list

| Label | What changed |
|---|---|
| Verse number fixed | A misread digit in a verse number was replaced with the right one. |
| Quotes changed | Double quotes became single quotes, if you asked for that. |
| Stray mark removed | A bullet or dot the OCR added at the start of a line was removed. |
| Hyphen joined | A word split across two lines was joined, if you asked for that. |

The `##` marks that the OCR tool adds in front of headings are also removed. They are the OCR tool's own markup, not part of the book, so they are not listed.

Apart from these, every character in the output is exactly as in the text you loaded.

### How reliable is it?

The tool has been measured on one book, the Mandala Brahmana Upanishad sample (28 pages, 89 mantras), with the GPT-5.6 Luna model:

- In the most recent runs, all 50 rows that have an expected answer matched exactly, and all 89 rows came out the same from run to run.
- Earlier runs, before some of the checks existed, were wrong on up to 8 of the 89 rows.

Results vary a little between runs, and a different book may bring problems this one did not. Treat the review list as part of the work, not as an optional extra.

---

## 7. Deploying on Render

[Render](https://render.com) is a hosting service. Deploying there gives the tool a web address that you and your colleagues can open from anywhere.

### Before you start

You need:

- a GitHub account with this repository in it
- a Render account (sign up at [render.com](https://render.com); signing in with GitHub is the easiest)
- your OpenRouter API key

The repository already contains the two files Render uses:

- [`render.yaml`](../render.yaml) tells Render what to create: one web service, on the free plan, in the Singapore region, with a password.
- [`Dockerfile`](../Dockerfile) tells Render how to build and start the app.

### Deploy

1. **Push the code to GitHub.** Render builds from GitHub, so the latest code must be there.

2. **Start a Blueprint.** In the [Render dashboard](https://dashboard.render.com), click **New**, then **Blueprint**. Choose the `veda-script-automation` repository.
   - If the repository does not appear, click the link to configure GitHub access and allow Render to see it. A private repository needs this step.
   - You can also use this direct link: `https://render.com/deploy?repo=https://github.com/me-shivamo/veda-script-automation`

3. **Enter the API key.** Render reads `render.yaml` and shows what it will create. It asks for a value for `OPENROUTER_API_KEY`. Paste your OpenRouter key.

4. **Deploy.** Confirm, and wait. The first build takes a few minutes. The service is ready when its status shows **Live**.

5. **Find your password.** Open the service, go to **Environment**, and reveal the value of `APP_PASSWORD`. Render generated it.

6. **Open the app.** The address is at the top of the service page, something like `https://veda-script-automation.onrender.com`. The browser asks you to sign in:
   - Username: `veda`
   - Password: the value from step 5

### Choosing your own password

The generated password is long and hard to type. To set your own:

1. Open the service's **Environment** page.
2. Change `APP_PASSWORD` to a password of your choice. Use a long one; it protects your OpenRouter credit.
3. Save. Render restarts the service with the new password.

You can change `APP_USERNAME` the same way.

### What the free plan does and does not do

| | Free plan |
|---|---|
| Cost | Nothing. A workspace gets 750 free hours a month. |
| Sleep | The service sleeps after 15 minutes with no visitors. |
| Waking up | The first visit after a sleep takes about a minute. |
| **Saved runs** | **Lost whenever the service restarts, redeploys or sleeps.** |
| Restarts | Render may restart a free service at any time. |

In practice:

- **Download the output of every run you want to keep**, as soon as it finishes. The page shows a reminder about this.
- **Keep the page open while a run is in progress.** The open page keeps the service awake. If the service restarts in the middle of a run, that run is lost and shows as "Interrupted".
- A short book finishes in a few minutes, so sleeping is rarely a problem during a run.

### Keeping runs permanently

To keep past runs, the service needs a **persistent disk**, and disks are only available on paid instances.

Change `render.yaml` like this, then commit and push:

```yaml
services:
  - type: web
    name: veda-script-automation
    runtime: docker
    plan: starter               # a paid instance type; see Render's Blueprint reference for the current names
    region: singapore
    healthCheckPath: /healthz
    autoDeployTrigger: commit
    disk:
      name: runs
      mountPath: /var/data
      sizeGB: 1
    envVars:
      - key: OPENROUTER_API_KEY
        sync: false
      - key: APP_USERNAME
        value: veda
      - key: APP_PASSWORD
        generateValue: true
      - key: JOBS_DIR
        value: /var/data/jobs   # keep runs on the disk
```

The three changes are the `plan`, the `disk` block and the `JOBS_DIR` variable. One gigabyte is plenty: a run of the 28-page sample book takes less than one megabyte.

You can make the same changes in the dashboard instead: change the instance type under **Settings**, add a disk on the **Disks** page with mount path `/var/data`, and add `JOBS_DIR=/var/data/jobs` under **Environment**. If you do, change `render.yaml` to match, so that the file and the service agree.

Two things change once a disk is attached:

- A paid instance does not sleep, so the one-minute wait disappears.
- Each deploy causes a short outage, because Render stops the old copy before starting the new one.

Check [Render's pricing page](https://render.com/pricing) for current prices.

### Updating the deployed app

`render.yaml` sets `autoDeployTrigger: commit`. Every time you push a new commit to the deployed branch on GitHub, Render rebuilds and redeploys by itself.

On the free plan, a redeploy clears the saved runs.

### Changing the OpenRouter key

Open the service's **Environment** page, replace the value of `OPENROUTER_API_KEY`, and save. Do this whenever a key may have been seen by someone who should not have it.

### Why these choices were made

- **Docker.** The Dockerfile pins the Python version and installs the exact package versions recorded in `uv.lock`, so the deployed app is the same as the tested one.
- **Singapore.** It is the Render region nearest to India. A region cannot be changed after the service is created; to move, delete the service and create it again.
- **One instance.** The app keeps track of runs in progress in its own memory, so it must run as a single copy. Do not scale it to several instances.
- **`/healthz`.** Render calls this address to check that the app is alive. It is the only address that does not ask for the password.

---

## 8. Settings

All settings are environment variables. On your computer they go in the `.env` file. On Render they go on the service's **Environment** page.

| Variable | Default | What it does |
|---|---|---|
| `OPENROUTER_API_KEY` | none (required) | The key that pays for the AI models. Without it the page shows a warning and runs fail. |
| `APP_PASSWORD` | empty | When set, every page and every request asks for the username and password. When empty, there is no sign-in at all. |
| `APP_USERNAME` | `veda` | The username that goes with `APP_PASSWORD`. |
| `JOBS_DIR` | `./jobs` | The folder where runs are kept. |
| `MAX_ACTIVE_RUNS` | `3` | How many runs may be in progress at the same time. A further run is refused until one finishes. This limits how fast credit can be spent. |
| `HOST` | `127.0.0.1` | The network address the server listens on. `127.0.0.1` means this computer only. `0.0.0.0` means any computer that can reach it. |
| `PORT` | `8000` | The port the server listens on. Render sets this itself. |

`python -m web` also accepts `--host` and `--port` on the command line.

If the server is started so that other computers can reach it and `APP_PASSWORD` is empty, it prints a warning.

---

## 9. The command line

Everything the web page does can also be done from a terminal.

```bash
uv run python -m agent run BOOK.md --request "what you want" [options]
```

| Option | What it does |
|---|---|
| `--request` | What you want, as text or a path to a text file. Required. |
| `--examples FILE.csv` | A few rows of the output you want. |
| `--model ID` | OpenRouter model for all three agents. Default `openai/gpt-5.6-luna`. |
| `--analyst-model`, `--labeler-model`, `--reviewer-model` | A different model for one agent. |
| `--escalation-model ID` | A stronger model that retries any part the Labeler could not finish. |
| `--effort` | How hard the model thinks: `minimal`, `low`, `medium` (default), `high`, `xhigh`. |
| `--spec FILE.json` | Reuse a saved plan and skip the Analyst. Useful for several books in the same series. |
| `--pages 1-10` | Only process these pages. |
| `--chunk-pages N` | Pages per part. Default 6. |
| `--concurrency N` | Parts worked on at once. Default 6. |
| `--auto-approve` | Do not stop to ask for approval of the plan. |
| `--jobs-dir DIR` | Where to put the run folder. Default `jobs`. |

### What a run leaves behind

Each run gets its own folder under `jobs/`, named with the date, time and run name.

| File | Contents |
|---|---|
| `output.csv`, `output.json` | The rows. |
| `issues.json` | The review list. |
| `changes.json` | Every character the code changed. |
| `analyst.md` | The plan as shown to you. |
| `spec.json` | The plan as data. Pass it to `--spec` to reuse it. |
| `input.md`, `request.txt`, `examples.csv` | What you gave. |
| `pieces.json`, `labels.json` | The numbered pieces of the book and the label on each. |
| `trace.jsonl` | Every action each agent took, in order. Useful when a result is wrong and you want to see why. |
| `state.json` | The result, the time taken and the cost. |

---

## 10. Costs

There are two separate costs.

**AI models (OpenRouter).** You pay for what the models read and write. On the 28-page sample book with GPT-5.6 Luna, a run cost between 6 and 8 US cents. Cost grows roughly with the number of pages. Other models have not been tested here, and some cost many times more per run.

Every finished run shows its cost on the page and in `state.json`. Your OpenRouter dashboard shows the total.

**Hosting (Render).** The free plan costs nothing. A paid instance with a disk has a monthly price; see [Render's pricing page](https://render.com/pricing).

To keep spending under control:

- Set a credit limit on the key in your OpenRouter account.
- Keep `APP_PASSWORD` set on any copy that others can reach.
- Leave `MAX_ACTIVE_RUNS` low.

---

## 11. Security and privacy

- **One shared password.** A deployed copy has a single username and password. Everyone who knows them can see all runs and start new ones. There are no separate user accounts.
- **The password travels with every request.** This is safe on Render, which serves the app over HTTPS. Do not expose the app over plain HTTP on a network you do not trust.
- **The book text leaves your computer.** It is sent to OpenRouter, which passes it to the company that runs the model you chose. Do not load texts you are not allowed to share.
- **The API key is never in the code.** It lives in `.env` on your computer, which Git ignores, or in Render's environment settings.
- **If a key may have leaked, replace it.** Delete it in your OpenRouter account, create a new one, and update `.env` and Render.
- **Book text is shown as text.** The page never treats book content as web code, so nothing in a book file can run in the browser.

---

## 12. Troubleshooting

| What you see | Likely cause | What to do |
|---|---|---|
| "The OpenRouter API key is not set" on the page | `OPENROUTER_API_KEY` is missing | Add it to `.env` and restart, or add it on Render's **Environment** page. |
| A run fails with "OpenRouter rejected the API key" | The key is wrong or was deleted | Create a new key and update it. |
| A run fails with "out of credits" | The OpenRouter account has no credit | Add credit in your OpenRouter account. |
| The browser keeps asking for the password | Wrong username or password | The username is `veda` unless you changed `APP_USERNAME`. Copy the password from Render's **Environment** page. |
| The page takes a minute to open on Render | The free service was asleep | Wait. It only happens on the first visit after a quiet spell. |
| Past runs have disappeared on Render | The free plan clears files on every restart | See [Keeping runs permanently](#keeping-runs-permanently). |
| A run shows "Interrupted" | The server restarted while it was in progress | Start the run again. |
| "3 runs are already in progress" | The limit on runs at once was reached | Wait for one to finish, or raise `MAX_ACTIVE_RUNS`. |
| The plan looks wrong | The request was unclear, or the book is laid out in an unusual way | Type what should change and click **Send the change**. Adding example rows helps most. |
| Many rows are on the review list | The OCR text is poor, or verse numbers are missing or misread | Check the book text. Each unit must end with its number. |
| The Render build fails | Shown in the service's **Logs** | Read the last lines of the build log. Check that `uv.lock` was committed along with `pyproject.toml`. |
| Render says the health check failed | The app did not start | Read the service's **Logs**. The app must listen on the `PORT` Render gives it, which the Dockerfile already arranges. |

To see exactly what the agents did in a run, open `trace.jsonl` in its folder under `jobs/`.

---

## 13. For developers

### Layout

```
agent/
  prompts/           System instructions for the agents (markdown)
    domain.md          How printed Sanskrit books are laid out
    rules.md           Rules that always apply
    analyst.md, labeler.md, reviewer.md
  core/              No AI in here
    pieces.py          Cut text into numbered pieces
    duplicates.py      Find pages the OCR produced twice
    spec.py            The plan (the "Spec"): columns, roles, numbering, text options
    labels.py          Labels on pieces; flags
    assemble.py        Build rows from labelled pieces; all the checks
    view.py            Text views of pieces, rows and issues for the agents
    devanagari.py      Digits and verse-number markers
  agents/
    analyst.py, labeler.py, reviewer.py   Each agent and its tools
    deps.py            Shared state: pieces, labels, usage, trace
    models.py          Which model each agent uses, through OpenRouter
  orchestrator.py    Runs one book from start to finish
  jobs.py            One folder per run
  export.py          CSV and JSON writers
  __main__.py        Command line
web/
  app.py             FastAPI server
  __main__.py        python -m web
  static/            index.html, app.css, app.js, img/
eval/
  score.py           Compare an output with a book's expected rows
  bakeoff.py         Run and score one or more models, several times if asked
examples/            Test books
tests/               pytest
```

The agents are built with [Pydantic AI](https://ai.pydantic.dev). The web page is plain HTML, CSS and JavaScript with no build step.

### How the pieces fit

- `make_pieces()` turns the text into `Piece` objects with IDs like `P0042`.
- The Analyst produces a `Spec`. A Spec lists **roles** (the labels a piece can have). Each role has a kind:
  - `unit`: numbered content, such as root text or commentary. The Labeler gives its number.
  - `attach_next`: unnumbered text kept with the unit that follows, such as a side heading.
  - `attach_prev`: unnumbered text kept with the unit before it, such as a closing line.
  - `drop`: page furniture, removed from the output.
- A role can also mark where a numbering level starts or ends. Code counts these to build row IDs.
- Labelers write `Label`s into a `LabelStore`.
- `build()` in `assemble.py` walks the pieces in order and produces the rows, the issues and the change log.

### Tests

```bash
uv run pytest
```

- `test_core.py` tests the text handling with hand-written labels. One test labels the first three pages of the sample book by hand and checks the rows against the expected output.
- `test_agents_offline.py` and `test_web.py` drive the agents and the web server with a scripted stand-in for the model. They make no API calls.

### Scoring real runs

```bash
uv run python -m eval.bakeoff --book examples/mandala-brahmana --models openai/gpt-5.6-luna --repeat 3
uv run python -m eval.score jobs/<run>/output.csv examples/mandala-brahmana
```

`bakeoff` runs the whole pipeline and scores each run. With `--repeat`, it also reports how many rows came out identical every time, which covers rows that have no expected answer. These commands call the real models and cost money.

A test book is a folder containing:

| File | Purpose |
|---|---|
| `input.md` | The OCR text. |
| `request.txt` | The request. |
| `expected.csv` or `expected.partial.csv` | The correct rows, or as many as are known. |
| `example_rows.csv` | Optional. Rows given to the Analyst as examples. |
| `gold_fixes.json` | Optional. Documented corrections to the expected rows. |

### Changing how the agents behave

1. Edit the markdown files in `agent/prompts/`.
2. Run `eval.bakeoff` with `--repeat 3` before and after, and compare.
3. Keep the worked examples in the prompts from books that are *not* used for scoring. Otherwise the score flatters the change.

When a run makes a mistake, the most durable fix has been a new check in `assemble.py`, so that the agent is told about that kind of mistake and must fix it. Prompt changes come second.

### Adding a model to the page

Edit the `MODELS` list in `web/app.py`. Any OpenRouter model that supports tool calling can be listed. Test it with `eval.bakeoff` first.

### The web server's addresses

| Address | Purpose |
|---|---|
| `GET /` | The page. |
| `GET /healthz` | Health check. Never asks for the password. |
| `GET /api/config` | Models, sample books, whether the key is set. |
| `GET /api/samples/{id}` | A sample book's text, request and example rows. |
| `GET /api/runs` | The most recent runs. |
| `POST /api/runs` | Start a run. Body: `name`, `input`, `request`, `examples`, `model`, `ask_approval`. |
| `GET /api/runs/{id}` | Progress, the plan when one is waiting, any error. |
| `POST /api/runs/{id}/decision` | Answer the plan. Body: `action` (`approve`, `revise` or `stop`) and `feedback`. |
| `POST /api/runs/{id}/stop` | Stop a run in progress. |
| `GET /api/runs/{id}/input` | The book text, request and example rows of a run. |
| `GET /api/runs/{id}/result` | Rows, review list, text changes, summary. |
| `GET /api/runs/{id}/download/csv` or `/json` | The output file. |

Limits: book text up to 5,000,000 characters, request up to 20,000, example rows up to 200,000.

---

## 14. Limits and what is not built yet

**Limits today**

- **One book, one model.** All measurements are from the sample book with GPT-5.6 Luna.
- **Numbered units only.** Each shloka, mantra or sutra must end with its printed number. Books of unnumbered prose with side notes are not supported.
- **No fixing in the page.** You correct the downloaded file by hand.
- **Footnotes are all-or-nothing.** They can be kept or dropped, but are not linked to the word they annotate.
- **Duplicated pages are only reported.**
- **One shared sign-in.** No separate accounts.
- **One server copy.** Runs in progress live in the server's memory.

**Not built yet**

- Correcting a label by clicking on it in the page.
- Voice commands for corrections, in Hindi or English.
- A measured comparison of models.
- Support for unnumbered prose.
