# Munich old GUI

A local GUI agent that controls **Tryton** (an old ERP system) end-to-end, fully on-device.

## Goal

Drive the Tryton desktop GUI the way a human would, but with a local model in the loop:

1. **Screenshot** the Tryton window.
2. **A local vision model (Qwen3.5)** looks at the screenshot and decides the next action.
3. **Act locally** — click / type / keypress to perform the step.
4. Repeat until the task is done.

## Why local

The whole loop runs on-device — model, screenshots, and control. **No screen data ever leaves the machine.** That makes it fully independent of any cloud and safe for sensitive enterprise systems.

## Beyond Tryton

OS-level control (`pyautogui`) means the agent isn't tied to Tryton's API — it drives whatever is on screen. Tryton is the demo target; the same approach works on **SAP** and other legacy GUIs where data must stay in-house.

## Setup

Apple Silicon Mac required (the model runs via MLX). Developed on an M3 Pro, 36 GB.

```bash
cd ~/Documents/Curavani/Hackathons/munich-old-gui

# 1. Python env (3.13, pinned in .python-version)
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # mlx-vlm pulls in MLX

# 2. Download the model (~19 GB) into the path config.yaml points at
.venv/bin/hf download mlx-community/Qwen3.5-35B-A3B-4bit \
  --local-dir /Users/peter/models/mlx/Qwen3.5-35B-A3B-4bit
```

The model is **`mlx-community/Qwen3.5-35B-A3B-4bit`** — a 4-bit MLX port of Qwen3.5-35B-A3B (a Mixture-of-Experts vision-language model, ~3B active params, Apache-2.0). `config.yaml` references it by local path, so the `--local-dir` above must match `model.path` in the config.

**macOS permissions:** grant your terminal **Screen Recording** (for screenshots) and **Accessibility** (for mouse/keyboard control) under System Settings → Privacy & Security.

## Run

```bash
# DRY RUN — one screenshot -> model -> coords, writes runs/once_result.json, NO clicking.
# Use this to validate coordinates before trusting a real run.
.venv/bin/python agent.py "<task>" --config config.yaml --once

# REAL RUN, model-driven — the model decides every action in a screenshot loop.
.venv/bin/python agent.py "open customer invoices" --config config.yaml

# REAL RUN, deterministic workflow — the SHIPPED path. A fixed YAML step list;
# the model is asked ONLY to locate described targets, never to plan. See WORKFLOW.md.
.venv/bin/python agent.py --workflow workflows/invoice_angela.yaml --config config.yaml
```

- The agent **auto-brings Tryton to the front** (osascript) — no manual focus needed.
- Abort any real run by **slamming the mouse into a screen corner** (pyautogui failsafe).
- Every step is logged: `runs/trace.log` (input image, raw model reply, parsed action, the concrete click) and a screenshot `runs/step_NN.png`. `runs/` is gitignored.
- Model and all parameters live in `config.yaml` — nothing is hardcoded.

## How it works

Two execution modes share one harness (full architecture in **`WORKFLOW.md`**):

- **Model-driven loop** (`agent.py "<task>"`): screenshot → backend returns one action → execute via pyautogui → wait → repeat, until the model replies `done` or `max_steps` is hit. The model is the planner. Brittle past a few steps — it can't reliably self-sequence a long flow (it loops/repeats).
- **Deterministic workflow runner** (`--workflow <file>.yaml`): the **shipped** path. A fixed, ordered step list the harness executes one step per iteration, never re-judging progress. Keyboard steps run with no model call; click steps ask the backend only *where* a described target is. This is how the end-to-end Tryton invoice demo runs reliably — see `WORKFLOW.md`.

- **`agent.py`** — the loop, screenshotting, `execute()`, trace logging, app focus. Model-agnostic.
- **`backends/<name>.py`** — the model, selected by `backend:` in config. A backend exposes
  `load(settings)`, `predict(...)` (model-driven mode), and `locate(...)` (workflow mode) — all
  returning **logical screen coordinates**, and owns its own prompt / parsing / coordinate conversion.
  - `backends/qwen.py` — **active**. Any JSON-emitting mlx-vlm model (Qwen3.5, Gemma).
  - `archive/uitars/` — a parked UI-TARS backend; see `archive/uitars/WHY_WE_DROPPED_UITARS.md`.
- **`config.yaml`** — all settings (model path, prompt, loop limits, coord space). Nothing hardcoded in Python.

## Gotchas

- **Single primary display.** pyautogui only sees and clicks the **built-in display** — Tryton must run there during a real run.
- **Speed.** Qwen3.5 is ~32 s per inference; a multi-step workflow is slow by design.
- **Coordinate space.** Qwen3.5 emits coordinates *normalized* to 0..1000 over the image (`coord_space: normalized`); `backends/qwen.py` scales them to logical screen points before clicking. Treating them as `logical` made every narrow target miss by ~0.66x (full story in `WORKFLOW.md` §4.1). Calibrate any new model with `--once` + a crop check (open `runs/once.png`, crop around the returned (x,y)×2 in screenshot px, confirm it's on target). Other coord spaces (`logical`, `image`) are supported in `backends/qwen.py`.
- **Model format quirks.** Qwen sometimes returns coords as strings (`"101"`), floats, or a packed array (`x:[x,y]`). `backends/qwen.py` normalizes these — harden the parser there, not in `agent.py`.
- **Grid overlay** (`grid:` in config) exists but is **off** — it hurt more than helped on dense menus.
- **Done-detection.** The prompt tells the model to read the breadcrumb / active tab title and emit `done` when the target view is already open. It occasionally does one redundant click before recognizing completion.

## Known weak spots of the model-driven loop

These are why the deterministic `--workflow` runner exists — it sidesteps all of them by owning the sequence itself and using the model only to locate targets.

- Redundant repeated clicks before `done`.
- Multi-step state tracking across `history` (a list of past action summaries).
- Typing into fields (`type` types at current focus — must click the field first).
- Save actions (`hotkey`, e.g. cmd+s) — untested end-to-end.
- Dense lists: vertical precision can be ~1 row off; verify with `--once` first.

## Don't

- Don't `git add -A` (a guard blocks it; stage explicit paths).
- Don't run a real (clicking) run while another session might be driving the mouse.
