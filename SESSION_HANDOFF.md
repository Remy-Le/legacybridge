# Session handoff — local GUI agent (munich-old-gui)

Read this first, then ask Peter for the workflow he wants to run.

## What this is
A fully on-device GUI agent: **screenshot → vision model (mlx-vlm) → pyautogui action → repeat.**
No screen data leaves the machine. Target app for the demo is **Tryton** (a stand-in
for a real ERP like SAP). The point is data sovereignty: a local model operating a
legacy desktop GUI by *looking* at it, not via APIs.

## Current state (working)
Qwen3.5-35B-A3B drives Tryton autonomously: it opens "Customer Invoices", verifies
success from the breadcrumb, and stops on `done`. This is committed and pushed on `main`.

## How to run
```bash
cd ~/Documents/Curavani/Hackathons/munich-old-gui

# DRY RUN — one screenshot -> model -> coords, writes runs/once_result.json, NO clicking.
# Use this to validate coordinates before trusting a real run.
.venv/bin/python agent.py "<task>" --config config.yaml --once

# REAL RUN — executes the action loop (drives the mouse/keyboard).
.venv/bin/python agent.py "<task>" --config config.yaml
```
- The agent **auto-brings Tryton to the front** (osascript) — no manual focus needed.
- Abort any real run by **slamming the mouse into a screen corner** (pyautogui FAILSAFE).
- Every step is logged to `runs/trace.log` (input image, raw model reply, parsed action,
  the concrete click) and a screenshot `runs/step_NN.png`. `runs/` is gitignored.

## Architecture
- `agent.py` — the loop, screenshotting, execute(), trace logging, app focus. Model-agnostic.
- `backends/<name>.py` — the model. Selected by `backend:` in config. A backend exposes
  `load(settings)` and `predict(handle, task, history, image_path, screen, settings) -> action dict`
  in **logical screen coordinates**, and owns its own prompt/parse/coord-conversion.
  - `backends/qwen.py` — active. JSON-emitting mlx-vlm models (Qwen3.5, Gemma).
  - `backends/uitars.py` — parked/unused (UI-TARS exploration, not wired in).
- `config.yaml` — all settings. Nothing hardcoded in Python.

## Key facts / gotchas
- **Machine**: M3 Pro, 36GB. Single screen for runs — Tryton must be on the **built-in
  display** (pyautogui only sees/clicks the primary display).
- **Speed**: Qwen3.5 is ~32s/inference. A multi-step workflow will be slow; that's expected.
- **Coordinate space**: Qwen3.5 emits *logical* screen coords (config `coord_space: logical`),
  which map 1:1 to pyautogui clicks — no scaling. Calibrated via `--once` + a crop check
  (open `runs/once.png`, crop around the returned (x,y)*2 in screenshot px, confirm it's on
  the target). Other models may differ — recalibrate with the same method.
- **Qwen format quirks**: it sometimes returns coords as strings ("101"), floats, or a
  packed array (`x:[x,y]`). `backends/qwen.py` normalizes all of these. If a new model
  breaks, harden the parser there, not in agent.py.
- **Grid overlay** exists (config `grid:`) but is OFF — Qwen3.5 doesn't need it; it hurt
  more than helped on dense menus.
- **Done-detection**: the prompt tells the model to read the breadcrumb/active tab title and
  emit `done` when the target view is already open. Works, but the model sometimes does one
  redundant click before recognizing completion.

## Known weak spots to watch on multi-step workflows
- Redundant repeated clicks before `done`.
- Multi-step state tracking across the `history` (it's a list of past action summaries).
- Typing into fields (`type` action types at current focus — must click the field first).
- Save actions (`hotkey`, e.g. cmd+s) — untested end-to-end.
- Dense lists: vertical precision can be ~1 row off; verify with `--once` first.

## Workflow Peter will give you
He will describe a **3–4 step workflow** in Tryton (e.g. open a view, create a record,
fill fields, save). Plan to:
1. Restate the workflow as a single clear task string (the agent takes one `task` arg).
2. **Dry-run each tricky step with `--once` first** to validate coordinates before any real click.
3. Then do a real run and watch `runs/trace.log` + step screenshots; fix coords/prompt/parser as needed.

## Don't
- Don't `git add -A` (a guard blocks it; stage explicit paths).
- Don't run a real (clicking) run while another session might be driving the mouse.
