# WORKFLOW.md — Driving Tryton end-to-end with a local vision model

How the `legacybridge` agent creates and posts a complete customer invoice in
the **Tryton** ERP, fully on-device, by looking at the screen and acting like a
human would. This is the deep-dive companion to `README.md`: it documents the
*deterministic workflow runner*, the `invoice_angela.yaml` flow step by step, and
— most importantly — **why each design decision is what it is**, including the
root causes we found the hard way.

## Table of contents

1. [What this is](#1-what-this-is)
2. [Architecture](#2-architecture)
3. [The `invoice_angela.yaml` workflow, step by step](#3-the-invoice_angelayaml-workflow-step-by-step)
4. [Key decisions & root causes](#4-key-decisions--root-causes)
5. [How to run](#5-how-to-run)
6. [The JSON input contract](#6-the-json-input-contract)
7. [Honest capability split](#7-honest-capability-split)

---

## 1. What this is

A local GUI agent that drives an old desktop ERP (**Tryton**) the way a human
would — no API, no integration, no cloud. The loop is:

1. **Screenshot** the built-in display (`pyautogui`).
2. **A local vision model — Qwen3.5** (`Qwen3.5-35B-A3B-4bit`, a 4-bit MLX port
   of a Mixture-of-Experts VLM, ~3B active params, Apache-2.0) — looks at that
   screenshot and returns where to act.
3. **Act locally** via `pyautogui` — click / type / keypress.
4. Repeat.

**Everything runs on-device — model, screenshots, and control. No screen data
ever leaves the machine.** That is the whole point: it works on sensitive,
legacy enterprise systems (Tryton here, but the same OS-level approach drives
SAP and any other GUI) where the data must stay in-house and there is no usable
API to integrate against.

The demo task: **create a customer invoice for Angela Martin with one line of A4
Paper 500 (qty 1, unit price 50) and post it** — driven entirely from a clean
Tryton window with only the left navigation tree visible.

---

## 2. Architecture

### Two layers, cleanly split

- **`agent.py`** — model-agnostic harness. Owns the screenshot loop,
  `execute()` (the concrete `pyautogui` calls), trace logging, and app focus. It
  knows nothing about *which* model is used.
- **`backends/<name>.py`** — the model. Selected by `backend:` in config. A
  backend exposes `load(settings)`, `predict(...)` (model-driven mode) and
  `locate(...)` (workflow mode), and owns its own prompt, JSON parsing, and
  coordinate conversion. `backends/qwen.py` is active and covers any
  JSON-emitting mlx-vlm model (Qwen3.5, Gemma).
- **`config.yaml`** — every parameter (model path, prompts, loop limits,
  `coord_space`). **Nothing is hardcoded in Python.**

### Two execution modes

The harness has **two ways** to drive the GUI, and the switch between them is the
central design decision of this project.

**(a) Model-driven loop (`agent.py main()` / `run_once`) — the original.**
Every step: screenshot → the model reads the full task + history → it decides the
*single next action* (what to do AND where) → execute → repeat, until the model
emits `done` or hits `max_steps`. The model is the planner.

**(b) Deterministic workflow runner (`run_workflow`, `--workflow`) — what we
ship.** A YAML file holds a **fixed, ordered list of steps**. The harness
executes exactly one per iteration, in order, and **never re-judges progress**.
Keyboard steps (`hotkey` / `type` / `scroll` / `wait`) run directly with **no
model call at all**. Click steps call the backend's `locate()` — which asks the
model **only** "where is this described element?" and returns coordinates. The
model never decides *what* to do, only *where* a named target sits.

### Why we switched

**The model couldn't reliably self-sequence a 17-step workflow — it looped.**
In model-driven mode, multi-step state tracking across the `history` list broke
down: the model emitted redundant repeated clicks, lost track of which sub-steps
were already done, and on a long flow like "new record → search party → confirm →
add line → search product → set qty/price → save → post → confirm" it would
re-do steps or stall before ever reaching `done`. Asking one model to be both the
planner and the localizer, at temperature 0, over 17 dependent steps, is brittle.

The fix is a **separation of concerns**: the *human author* owns the sequence
(encoded once in the YAML, deterministically), and the *model* is demoted to the
one thing it is genuinely good at — **looking at a screenshot and pointing at a
named element**. Determinism where we can be deterministic (the order of steps,
the keyboard shortcuts, the static chrome buttons); the model only where we
genuinely need *understanding of the live screen* (find this record, find this
field). This is reliable; the self-sequencing loop was not.

### The trace

Every step writes a screenshot `runs/step_NN.png` and a block to
`runs/trace.log` (input image, the target description, the located coordinates,
the raw model reply, and the concrete `pyautogui` call). The header is written
*before* `locate()` runs, so even a crash inside localization leaves a trace
pointing at the exact step and target. A failure is always pinpointed to a single
step. `runs/` is gitignored.

---

## 3. The `invoice_angela.yaml` workflow, step by step

17 steps. Each is labelled **[Qwen-vision]** (the model locates a target on the
live screen), **[keyboard]** (a fixed key action, no model), or
**[fixed-fraction]** (a hardcoded screen-fraction click for a button the model
deterministically mislocates).

| # | Action | Type | What it does |
|---|--------|------|--------------|
| 1 | `double_click` "Customer Invoices" tree item | **Qwen-vision** | Open the Customer Invoices list from the left nav tree |
| 2 | `hotkey ctrl+n` | keyboard | Create a new invoice record (Party field becomes focused) |
| 3 | `hotkey f2` | keyboard | Open the "search relation" dialog for the focused Party field |
| 4 | `double_click` "Angela Martin" row | **Qwen-vision** | Pick the party in the search dialog |
| 5 | `hotkey enter` | keyboard | Confirm + close the party search dialog |
| 6 | `click at_frac [0.893, 0.430]` | **fixed-fraction** | The Lines **"+"** icon — add an invoice line |
| 7 | `click` Product field | **Qwen-vision** | Focus the Product field in the line editor |
| 8 | `hotkey f2` | keyboard | Open the Product search dialog |
| 9 | `double_click` "A4 Paper 500" row | **Qwen-vision** | Pick the product |
| 10 | `hotkey enter` | keyboard | Confirm + close the product search dialog |
| 11 | `click` Quantity field | **Qwen-vision** | Focus the Quantity field |
| 12 | `type "1"` | keyboard | Type the quantity |
| 13 | `click` Unit Price field | **Qwen-vision** | Focus the Unit Price field |
| 14 | `type "50"` | keyboard | Type the unit price |
| 15 | `click at_frac [0.919, 0.842]` | **fixed-fraction** | The **"✓ Add"** button — save the line |
| 16 | `click at_frac [0.900, 0.910]` | **fixed-fraction** | The **"✓ Post"** button — post the invoice |
| 17 | `click at_frac [0.560, 0.509]` | **fixed-fraction** | The **"Yes"** button on the post-confirmation modal |

**Why each is what it is:**

- **Steps 1, 4, 9 (vision double-clicks on records/menu):** these targets are
  *data-dependent* — a menu row, a party name, a product name. Their position is
  not fixed (lists scroll, items differ) and they carry **text labels** the model
  reads reliably. This is exactly the model's strength.
- **Steps 7, 11, 13 (vision clicks on form fields):** the Product / Quantity /
  Unit Price fields in the line editor are labelled and the model localizes them
  well. We must click a field before typing into it because `type` types at the
  *current focus* — there is no field targeting in the type action itself.
- **Steps 2, 3, 5, 8, 10 (keyboard navigation):** `Ctrl+N` (new record), `F2`
  (open/search relation on the focused field), and `Enter` (confirm the dialog
  selection) are **Tryton keyboard shortcuts**. They are deterministic and need
  no screen reading at all, so we never ask the model to find a toolbar icon when
  a keypress does the same job flawlessly. Faster (no ~32 s inference) and more
  robust.
- **Steps 12, 14 (typing):** the only two fields that take typed values.
- **Steps 6, 15, 16, 17 (fixed-fraction):** four static chrome buttons the model
  deterministically mislocates at temperature 0. See §4.

---

## 4. Key decisions & root causes

These are the real stories — the bugs and the fixes — not a sanitized summary.

### 4.1 `coord_space`: normalized, not logical

**Symptom.** Early on, narrow targets (toolbar icons, small fields) were missed
by roughly a constant factor, while wide targets (full-width tree rows, dialog
rows) got hit anyway "by luck".

**Root cause.** Qwen3.5 emits coordinates **normalized to 0..1000 over the input
image**, not in logical screen points. We were treating the output as `logical`
(1:1 with `pyautogui` click space). On this display logical width is ~1512, so a
raw value of ~1000 max maps to only ~0.66× of the screen — every click landed at
about two-thirds of where it should, horizontally and vertically. Wide rows are
forgiving enough that a 0.66× hit still lands *somewhere on the row*, which
masked the bug; narrow targets exposed it.

**Fix.** Set `model.coord_space: "normalized"` in `config.yaml`.
`backends/qwen.py::_to_logical()` then scales `raw / 1000 * logical_w` (and `_h`).
Verified against two toolbar icons: 328 → 496 (actual 493), 949 → 1435 (actual
1430). (`README.md` still says `logical` in one spot — that is stale; the
committed `config.yaml` and this calibration are the truth.)

The backend supports three spaces — `logical` (Gemma emits this), `image`
(absolute screenshot pixels), `normalized` (Qwen) — so a new model is calibrated
by flipping one config value plus a `--once` crop check, never by editing Python.

### 4.2 Keyboard shortcuts over fragile icon clicks

Wherever Tryton has a keyboard shortcut, we use it instead of asking the model to
find and click a toolbar icon:

- **`Ctrl+N`** — new record (no "new" toolbar icon hunt).
- **`F2`** — open/search the relation on the currently focused field (party,
  product). This is how we open the search dialogs.
- **`F3`** — would add a line, but see §4.3: it does not work from an empty Lines
  list, so we fall back to a click there.
- **`Enter`** — confirm the highlighted selection in a search dialog and close
  it. (We `double_click` the row first to select it; `double_click` alone leaves
  the dialog open, so `Enter` does the confirm+close.)

Keyboard steps are deterministic, instant (no ~32 s inference), and immune to
icon-localization error. Every shortcut we can use is one less thing the model
can get wrong.

### 4.3 The four fixed-fraction exceptions

Four clicks are stored as **screen fractions** `[fx, fy]` and resolved at runtime
against the live `pyautogui.size()` (`x = round(fx * screen_w)`). Fractions —
not absolute pixels — so they **survive resolution and scaling changes**, as long
as the Tryton window stays maximized (layout fraction-stable). Each exists
because the model *deterministically* mislocates that specific button at
temperature 0, and no amount of re-describing it moved the prediction:

1. **Lines "+" icon (step 6, `[0.893, 0.430]`).** A tiny **label-less** icon.
   `F3` doesn't work here (the empty Lines list won't take keyboard focus from a
   click), and the vision model points at the Reference field instead of the "+".
   No text to read → the model can't localize it.
2. **"✓ Add" button (step 15, `[0.919, 0.842]`).** There are **two adjacent,
   nearly identical "Add" buttons** ("Add" and "Add and New"). At temperature 0
   the model returns the *same* point regardless of phrasing — three different
   descriptions all landed on "Add and New". It cannot separate the two.
3. **"✓ Post" button (step 16, `[0.900, 0.910]`).** The model deterministically
   clicks the **bottom edge** of the Post button (y ≈ 918) and misses just below
   it; three precise descriptions didn't move it. Post is a static button, so a
   fixed center (logical ~1360,894) is reliable.
4. **"Yes" confirmation button (step 17, `[0.560, 0.509]`).** The post-confirm
   modal's "Yes". A static dialog button where a *wrong* hit ("No") is the
   worst-case outcome, so we don't gamble on the model. Center measured at logical
   (847,500).

The common thread: all four are **static chrome** (their position is fixed by the
app layout, not by data), and either label-less or ambiguous against a neighbor.
Pinning them as fractions costs nothing — they never move relative to the
maximized window — and removes the only four points where the model was
unreliable. The fractions were measured on 1512×982; re-measure only if the
*form layout* changes, not on resolution/scaling changes (those are handled
automatically).

### 4.4 Parser hardening for Qwen's malformed JSON

Qwen does not reliably emit clean JSON. `backends/qwen.py::_parse()` +
`_relax_json()` + `_normalize_point()` tolerate, without ever editing `agent.py`:

- markdown code fences around the object,
- a second trailing object, or the real object wrapped in an extra brace
  (`{ {"x":..} }`) — it matches innermost `{...}` blocks and takes the first that
  carries a coordinate or action,
- single quotes, unquoted keys, trailing commas (repaired in `_relax_json`),
- coordinates as strings (`"101"`), floats, or a packed array (`x:[x,y]`)
  (flattened in `_normalize_point`),
- and in `locate()` specifically, a totally broken pair like `{"x": 317, 309}`
  (missing the `y` key) falls back to "take the first two integers in the reply",
  because for a locate we only need a coordinate pair.

The raw reply is always included in any parse error, so a failure is never blind.
All model-quirk handling lives in the backend — harden it there.

---

## 5. How to run

From `~/Documents/Curavani/Hackathons/legacybridge`:

```bash
# REAL RUN — the deterministic 17-step invoice workflow (drives mouse/keyboard).
.venv/bin/python agent.py --workflow workflows/invoice_angela.yaml --config config.yaml

# DRY RUN — one screenshot -> model -> coords, writes runs/once_result.json, NO clicking.
# Validate a single target's coordinates before trusting a real run.
.venv/bin/python agent.py "<target description>" --config config.yaml --once

# RESUME — restart a workflow partway through. Tryton must ALREADY be in the
# state step N expects (the harness does not replay earlier steps).
.venv/bin/python agent.py --workflow workflows/invoice_angela.yaml --config config.yaml --start-step 6
```

- The agent **auto-brings Tryton to the front** (osascript) — no manual focus
  needed.
- **Abort** any real run by **slamming the mouse into a screen corner**
  (`pyautogui` failsafe).
- **Traces & screenshots** live in `runs/` (`trace.log` + `step_NN.png` per
  step, `once.png` / `once_result.json` for dry runs). `runs/` is gitignored.

**Gotchas:**

- **Built-in display only.** `pyautogui` sees and clicks the *built-in* display —
  Tryton must run there during a real run.
- **Clean start state required.** The workflow starts from a clean Tryton: no
  tabs open, just the left navigation tree visible. Step 1 opens Customer
  Invoices from that tree.
- **Window stays maximized.** The four fixed-fraction buttons assume the Tryton
  window is maximized (so the fractions hold).
- **Speed.** Qwen3.5 is ~32 s per inference; the 6 vision clicks dominate the
  runtime (~9 min total). Keyboard and fixed-fraction steps are instant.

---

## 6. The JSON input contract

`invoice_request.json` is the **shareable external input** — the clean,
machine-readable description of *what invoice to create*, decoupled from *how* the
agent drives the GUI:

```json
{
  "action": "create_and_post_customer_invoice",
  "erp": "tryton",
  "party": "Angela Martin",
  "lines": [
    { "product": "A4 Paper 500", "quantity": 1, "unit_price": 50 }
  ]
}
```

Today the workflow YAML has these values **baked in** (the party name, the
product name, the qty/price live inside `invoice_angela.yaml`). The contract
above is the input a real caller (another system, a queue, a person) would hand
us.

**Planned `--input` wiring.** Add `--input invoice_request.json` to `agent.py`.
The runner reads the JSON and **substitutes its values into the workflow's Qwen
targets and typed fields** before execution:

- `party` → the target string of step 4
  (`"the row labeled '<party>' in the Search Party dialog list"`),
- `lines[0].product` → the target of step 9,
- `lines[0].quantity` → the `text` of step 12,
- `lines[0].unit_price` → the `text` of step 14.

The YAML becomes a **template** (the fixed sequence + the structural targets),
and the JSON supplies the **content**. That keeps the deterministic skeleton
reusable across invoices while the only thing that changes per request is the
data — which is exactly the data the model is already good at finding by its text
label on screen. (Multi-line invoices then become a loop over `lines`, repeating
steps 6–15 per line.)

---

## 7. Honest capability split

What the **model** actually does vs. what is **pinned**:

- **Qwen3.5 does all 6 "understanding" clicks** — and only those. It reads the
  live ERP screen and finds *named, data-dependent* targets: the Customer
  Invoices menu item (step 1), the Angela Martin party row (4), the Product /
  Quantity / Unit Price fields (7, 11, 13), and the A4 Paper 500 product row (9).
  These are the steps that genuinely require *looking at the screen and
  understanding it* — a record that might be anywhere in a list, a field
  identified by its label. That is the model's real contribution.
- **Only static chrome is pinned.** The 4 fixed-fraction clicks (Lines "+",
  "Add", "Post", "Yes") are buttons whose position is fixed by the app layout,
  not by data — and which the model mislocates deterministically (label-less, or
  ambiguous against a twin, or a bottom-edge miss). Pinning them as
  resolution-independent screen fractions is the honest, robust choice; pretending
  the model handles them would just make the demo flaky.
- **The sequence is human-authored, not model-planned.** The order of the 17
  steps lives in the YAML. The model is a localizer, not a planner — because, as
  §2 explains, it could not reliably self-sequence the flow.

So the split is clean and defensible: **the model does the understanding (read
the ERP, find the named field/record); the harness owns the sequence; and the
handful of static, label-less, or ambiguous buttons are pinned.** Nothing is
oversold.
