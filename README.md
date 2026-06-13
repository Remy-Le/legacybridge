# Munich old GUI

A local GUI agent that controls **Tryton** (an old ERP system) end-to-end, fully on-device.

## Goal

Drive the Tryton desktop GUI the way a human would, but with a local model in the loop:

1. **Screenshot** the Tryton window.
2. **Gemma (local)** looks at the screenshot and decides the next action.
3. **Act locally** — click / type / keypress to perform the step.
4. Repeat until the task is done.

Everything runs locally: no cloud, no API for the vision/decision step.
