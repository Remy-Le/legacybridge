# LegacyBridge

**The integration layer for apps that have no API.**

A huge amount of business-critical software has no usable API: old ERPs
(Tryton, SAP, in-house systems from the 2000s), desktop accounting tools, niche
industry apps. If you are building something modern and you need to read or
write into one of those systems, you hit a wall — there is no endpoint to call,
no SDK, no webhook. The vendor will not build one. So the data stays trapped,
and the integration becomes a human copy-pasting between two windows.

**LegacyBridge is the bridge across that wall.** It drives the legacy app's GUI
on-device with a local vision model — clicking, typing, and reading the screen
the way a person would — so a modern app can finally push and pull data into a
system that was never built to be integrated. No API. No cloud. No screen data
ever leaves the machine.

Think **Plaid or Twilio, but for legacy desktop software**: you build against a
clean interface, LegacyBridge handles the messy reality behind it.

## Who this is for

Developers and startups stuck integrating a legacy desktop application — an old
ERP, an SAP install, an internal Windows tool — that exposes no API you can use.
You want your modern app to create an invoice, update a record, or read a
customer in that system, and the only "integration path" anyone has offered you
is "have someone type it in by hand." That is the wall LegacyBridge removes.

## The demo story: Billflow → Tryton

The included demo makes the wall concrete.

**Billflow** is a vibecoded modern billing app. A user composes an invoice in it.
Billflow is ready to send that invoice to the customer's ERP — except the
customer runs **Tryton**, an old desktop ERP with **no usable API**. Billflow
hits the wall.

LegacyBridge unblocks it: it takes the structured invoice and **drives the
Tryton GUI** to create the line, save it, and post the invoice — end to end,
fully on-device. The legacy system gets its data; Billflow never had to know how.

The demo pipeline also strings together the hackathon partners to get from a
human request to a posted invoice:

1. **Gemini** — chat front-end where the user describes what they want.
2. **Tavily** — product research / lookup.
3. **Pioneer / Fastino** — structures the result into clean JSON (the invoice request).
4. **LegacyBridge** — pushes that JSON into the legacy ERP by driving its GUI.

## Partner technologies

LegacyBridge is built with these hackathon partner technologies:

- **Google DeepMind (Gemini)** — chat front-end where the user describes the request.
- **Tavily** — product research / lookup.
- **Pioneer** — structures the result into clean, validated JSON (the invoice request).
- **Aikido** — security scanning of the codebase (SAST + dependency scanning).
  The repository scans **clean — 0 issues**:

![Aikido scan of legacybridge: 0 issues](docs/aikido-clean-scan.png)

## Demo

There are two ways to see it run.

### Live demo prerequisites (read first)

The on-device run drives a real GUI, so the environment has to match what the
agent was calibrated on. Getting these wrong is the #1 cause of failures:

- **Display at 1512×982 (logical).** The vision model's precision on Tryton's
  dense menu tree and the four pinned-button fractions were both tuned at this
  resolution. At a larger resolution the screenshot is downscaled harder before
  the model sees it, and it starts missing rows; the pinned buttons drift too.
  Set the Mac's scaled resolution to "looks like 1512×982".
- **Both windows maximized, same Space.** Maximize Brave (Billflow) and Tryton
  in place — *not* macOS true-fullscreen (which puts each in its own Space and
  makes activation/screenshots flaky). The agent auto-switches to Tryton when the
  run starts and back to Billflow when it finishes.
- **Tryton: logged in, clean start state.** Left nav tree expanded so
  *Customer Invoices* is visible, no draft tabs or dialogs open, nothing
  overlapping the window (close any screen-recording overlays).
- **Plugged into power.** A sustained ~9-minute inference run throttles on battery.
- **Abort anytime** by slamming the mouse into a screen corner (pyautogui failsafe).

Then run the server in **real mode** (loads the ~19 GB model once and keeps it
warm) and wait for `Model warm. LegacyBridge ready.` before clicking. In Billflow:
**Generate → toggle LegacyBridge on → Push to ERP.**

### 1. The Billflow demo (the show)

A small Flask server serves the Billflow UI and orchestrates the pipeline.

```bash
python demo/server.py
# then open http://localhost:5050   (port 5000 is taken by macOS AirPlay; override with PORT=)
```

To **rehearse the UI without invoking the local model** (useful when you just
want to walk through the flow, or you do not have the ~19 GB model downloaded),
set `FAKE_RUN=1` — LegacyBridge plays back the workflow steps without doing real
inference or real clicks:

```bash
FAKE_RUN=1 python demo/server.py
```

### 2. The real workflow (the engine)

Underneath the demo, the actual on-device run is a single command. It reads the
invoice request JSON and drives a live, maximized Tryton window to create and
post the invoice:

```bash
.venv/bin/python agent.py \
  --workflow workflows/invoice_template.yaml \
  --input invoice_request.json \
  --config config.yaml
```

> Note: the shipped, hard-wired demo flow is `workflows/invoice_angela.yaml`
> (party/product/qty/price baked in). `invoice_template.yaml` is the same
> sequence with the data fields turned into `{party}/{product}/{quantity}/{unit_price}`
> tokens, so `--input invoice_request.json` fills them at runtime. The
> substitution wiring lives in `agent.py` (`load_invoice_substitutions` /
> `apply_substitutions`). See **`WORKFLOW.md` §6** for the input contract.

Proof of any run lands in `runs/`: a step-by-step `trace.log` and a screenshot
`runs/step_NN.png` per step. `runs/` is gitignored.

---

# How it actually works (the honest engineering)

Everything below is the unsanitized technical truth. The full deep-dive — the
17-step Tryton invoice flow, every design decision and its root cause — is in
**`WORKFLOW.md`**. The short version follows.

## The core insight: the model is a *localizer*, not a planner

The naïve approach is to let the vision model plan: screenshot → "what's the next
action?" → click → repeat. **That breaks past a few steps.** Asked to
self-sequence a long, dependent flow (new record → search party → confirm → add
line → search product → set qty/price → save → post → confirm), the model loops,
repeats clicks, and loses track of what it already did.

So LegacyBridge splits the responsibilities:

- **The harness owns the sequence.** A human authors the ordered step list once,
  in a YAML workflow. The harness executes exactly one step per iteration, in
  order, and never re-judges progress.
- **The model owns only localization.** On a click step, it is asked one narrow
  question — *"where is this described element on screen?"* — and returns
  coordinates. It never decides *what* to do, only *where* a named target sits.
- **Keyboard steps skip the model entirely.** Tryton shortcuts (`Ctrl+N`, `F2`,
  `Enter`) are deterministic and instant — no inference, nothing to mislocate.

This is reliable where the self-planning loop was not. It is also the right
architecture: the model does the one thing it is genuinely good at (read a live
screen, find a named field or record), and nothing else.

## Setup

Apple Silicon Mac required (the model runs via MLX). Developed on an M3 Pro, 36 GB.

```bash
cd ~/Documents/Curavani/Hackathons/legacybridge

# 1. Python env (3.13, pinned in .python-version)
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # mlx-vlm pulls in MLX

# 2. Download the model (~19 GB) into the path config.yaml points at
.venv/bin/hf download mlx-community/Qwen3.5-35B-A3B-4bit \
  --local-dir /Users/peter/models/mlx/Qwen3.5-35B-A3B-4bit
```

The model is **`mlx-community/Qwen3.5-35B-A3B-4bit`** — a 4-bit MLX port of
Qwen3.5-35B-A3B (a Mixture-of-Experts vision-language model, ~3B active params,
Apache-2.0). `config.yaml` references it by local path, so the `--local-dir`
above must match `model.path` in the config.

**macOS permissions:** grant your terminal **Screen Recording** (for
screenshots) and **Accessibility** (for mouse/keyboard control) under System
Settings → Privacy & Security.

## Run modes

```bash
# DRY RUN — one screenshot -> model -> coords, writes runs/once_result.json, NO clicking.
# Use this to validate coordinates before trusting a real run.
.venv/bin/python agent.py "<task>" --config config.yaml --once

# REAL RUN, model-driven — the model decides every action in a screenshot loop.
# Original / experimental path; brittle past a few steps (see core insight above).
.venv/bin/python agent.py "open customer invoices" --config config.yaml

# REAL RUN, deterministic workflow — the SHIPPED path. A fixed YAML step list;
# the model is asked ONLY to locate described targets, never to plan. See WORKFLOW.md.
.venv/bin/python agent.py --workflow workflows/invoice_angela.yaml --config config.yaml
```

- The agent **auto-brings Tryton to the front** (osascript) — no manual focus needed.
- Abort any real run by **slamming the mouse into a screen corner** (pyautogui failsafe).
- Every step is logged: `runs/trace.log` (input image, raw model reply, parsed
  action, the concrete click) and a screenshot `runs/step_NN.png`. `runs/` is gitignored.
- Model and all parameters live in `config.yaml` — nothing is hardcoded.

## Architecture

Two execution modes share one harness (full architecture in **`WORKFLOW.md`**):

- **Model-driven loop** (`agent.py "<task>"`): screenshot → backend returns one
  action → execute via pyautogui → wait → repeat, until the model replies `done`
  or `max_steps` is hit. The model is the planner. Brittle past a few steps — it
  can't reliably self-sequence a long flow (it loops/repeats).
- **Deterministic workflow runner** (`--workflow <file>.yaml`): the **shipped**
  path. A fixed, ordered step list the harness executes one step per iteration,
  never re-judging progress. Keyboard steps run with no model call; click steps
  ask the backend only *where* a described target is. This is how the end-to-end
  Tryton invoice demo runs reliably — see `WORKFLOW.md`.

The codebase splits cleanly:

- **`agent.py`** — the loop, screenshotting, `execute()`, trace logging, app
  focus. Model-agnostic.
- **`backends/<name>.py`** — the model, selected by `backend:` in config. A
  backend exposes `load(settings)`, `predict(...)` (model-driven mode), and
  `locate(...)` (workflow mode) — all returning **logical screen coordinates**,
  and owns its own prompt / parsing / coordinate conversion.
  - `backends/qwen.py` — **active**. Any JSON-emitting mlx-vlm model (Qwen3.5, Gemma).
  - `archive/uitars/` — a parked UI-TARS backend; see `archive/uitars/WHY_WE_DROPPED_UITARS.md`.
- **`config.yaml`** — all settings (model path, prompt, loop limits, coord space).
  Nothing hardcoded in Python.

## Gotchas

- **Single primary display.** pyautogui only sees and clicks the **built-in
  display** — Tryton must run there during a real run.
- **Speed.** Qwen3.5 is ~32 s per inference; a multi-step workflow is slow by design.
- **Coordinate space.** Qwen3.5 emits coordinates *normalized* to 0..1000 over
  the image (`coord_space: normalized`); `backends/qwen.py` scales them to logical
  screen points before clicking. Treating them as `logical` made every narrow
  target miss by ~0.66x (full story in `WORKFLOW.md` §4.1). Calibrate any new
  model with `--once` + a crop check (open `runs/once.png`, crop around the
  returned (x,y)×2 in screenshot px, confirm it's on target). Other coord spaces
  (`logical`, `image`) are supported in `backends/qwen.py`.
- **Model format quirks.** Qwen sometimes returns coords as strings (`"101"`),
  floats, or a packed array (`x:[x,y]`). `backends/qwen.py` normalizes these —
  harden the parser there, not in `agent.py`.
- **Grid overlay** (`grid:` in config) exists but is **off** — it hurt more than
  helped on dense menus.
- **Done-detection.** The prompt tells the model to read the breadcrumb / active
  tab title and emit `done` when the target view is already open. It occasionally
  does one redundant click before recognizing completion.

## Known weak spots of the model-driven loop

These are why the deterministic `--workflow` runner exists — it sidesteps all of
them by owning the sequence itself and using the model only to locate targets.

- Redundant repeated clicks before `done`.
- Multi-step state tracking across `history` (a list of past action summaries).
- Typing into fields (`type` types at current focus — must click the field first).
- Save actions (`hotkey`, e.g. cmd+s) — untested end-to-end.
- Dense lists: vertical precision can be ~1 row off; verify with `--once` first.

## Capability split — what's real vs. pinned

LegacyBridge does not pretend the model does everything. The honest split (full
detail in **`WORKFLOW.md` §7**):

- **The model does the understanding clicks** — reading the live ERP screen and
  finding *named, data-dependent* targets (the menu item, the party row, the
  product row, the labelled form fields). That is its real contribution.
- **A handful of static chrome buttons are pinned** as resolution-independent
  screen fractions — the label-less Lines "+" icon, the ambiguous "Add" twin, the
  "Post" and "Yes" buttons. These are fixed by the app layout, not by data, and
  the model mislocates them deterministically. Pinning them is the robust, honest
  choice, not a workaround we hide.
- **The sequence is human-authored**, encoded once in the workflow YAML.

Nothing is oversold. Read `WORKFLOW.md` for the per-step breakdown and the root
cause behind every decision.

## Don't

- Don't `git add -A` (a guard blocks it; stage explicit paths).
- Don't run a real (clicking) run while another session might be driving the mouse.
