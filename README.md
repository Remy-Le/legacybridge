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
