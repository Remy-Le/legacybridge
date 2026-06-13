# Munich old GUI

A local GUI agent that controls **Tryton** (an old ERP system) end-to-end, fully on-device.

## Goal

Drive the Tryton desktop GUI the way a human would, but with a local model in the loop:

1. **Screenshot** the Tryton window.
2. **Gemma (local)** looks at the screenshot and decides the next action.
3. **Act locally** — click / type / keypress to perform the step.
4. Repeat until the task is done.

## Why local

The whole loop runs on-device — model, screenshots, and control. **No screen data ever leaves the machine.** That makes it fully independent of any cloud and safe for sensitive enterprise systems.

## Beyond Tryton

OS-level control (e.g. `pyautogui`) means the agent isn't tied to Tryton's API — it drives whatever is on screen. Tryton is the demo target; the same approach works on **SAP** and other legacy GUIs where data must stay in-house.

## Run

```bash
pip install -r requirements.txt          # mlx-vlm pulls in MLX (Apple Silicon)
python agent.py "open the customer list and create a customer named ACME"
```

- Model and all parameters live in `config.yaml` — nothing is hardcoded.
- Each step's screenshot is saved under `runs/` for debugging.
- Slam the mouse into a screen corner to abort (pyautogui failsafe).

**macOS permissions:** grant your terminal **Screen Recording** (for screenshots) and **Accessibility** (for mouse/keyboard control) under System Settings → Privacy & Security.

## How it works

`agent.py` loops: screenshot → Gemma returns one JSON action → execute via pyautogui → wait → repeat, until the model replies `done` or `max_steps` is hit. Retina pixel→point scaling is handled automatically.
