"""Local GUI agent: screenshot -> VLM backend -> pyautogui action -> repeat.

Fully on-device. No screen data leaves the machine. The model is pluggable: pick a
backend in config (`backend: qwen`); each backend lives in backends/<name>.py and
returns a canonical action dict in LOGICAL screen coordinates.

Usage:
    python agent.py "open the customer list and create a new customer named ACME"
    python agent.py "..." --once     # dry run: one screenshot -> coords, no clicking
"""

import argparse
import importlib
import json
import os
import re
import subprocess
import sys
import time

import pyautogui
import yaml
from PIL import ImageDraw, ImageFont

pyautogui.FAILSAFE = True  # slam the mouse into a corner to abort


def load_settings(path):
    with open(path) as f:
        return yaml.safe_load(f)


def load_backend(settings):
    """Import the model backend named in config (backends/<name>.py)."""
    return importlib.import_module(f"backends.{settings['backend']}")


def _load_font(size):
    """A legible TrueType font, falling back to PIL's bitmap default."""
    for path in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                 "/System/Library/Fonts/Helvetica.ttc"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_grid(img, scale, spacing, font_size):
    """Overlay a labeled coordinate grid. Labels are in LOGICAL coords (the model's
    output space); lines are drawn in image pixels (= logical * scale). This gives
    the model fixed anchors so it can read off precise click coordinates."""
    draw = ImageDraw.Draw(img)
    font = _load_font(font_size)
    w, h = img.size
    red = (255, 0, 0)
    logical_w, logical_h = int(w / scale), int(h / scale)

    for lx in range(0, logical_w + 1, spacing):
        px = int(lx * scale)
        draw.line([(px, 0), (px, h)], fill=red, width=1)
        draw.text((px + 3, 1), str(lx), fill=red, font=font)
    for ly in range(0, logical_h + 1, spacing):
        py = int(ly * scale)
        draw.line([(0, py), (w, py)], fill=red, width=1)
        draw.text((3, py + 1), str(ly), fill=red, font=font)
    return img


def get_window_rect(app_name):
    """Logical (x, y, w, h) of the front window of the named macOS app, via System
    Events. Used to scope the agent to one window (e.g. Tryton in a screen half)."""
    script = (f'tell application "System Events" to tell '
              f'(first process whose name contains "{app_name}") '
              f'to get {{position, size}} of window 1')
    out = subprocess.run(["osascript", "-e", script],
                         capture_output=True, text=True, check=True).stdout
    nums = [int(n) for n in re.findall(r"-?\d+", out)]
    return tuple(nums[:4])  # (x, y, w, h)


def get_region(settings):
    """The logical rectangle the agent looks at and clicks within. Full screen by
    default; the target app's window when `capture_window: true` (so the model
    never sees — and never mis-clicks — anything outside that window)."""
    w, h = pyautogui.size()
    if settings.get("capture_window") and settings.get("target_app"):
        return get_window_rect(settings["target_app"])
    return (0, 0, w, h)


def grab_screenshot(out_path, grid=None, region=None):
    """Save a screenshot and return (path, scale, width, height) where scale maps
    screenshot pixels -> logical points (Retina screens report 2x pixels vs click
    coords). If `region` (logical x,y,w,h) is given, the shot is cropped to it so
    the model only sees that window. If grid is enabled, it's drawn on first."""
    shot = pyautogui.screenshot()
    logical_w, _ = pyautogui.size()
    scale = shot.width / logical_w
    if region is not None:
        rx, ry, rw, rh = region
        shot = shot.crop((round(rx * scale), round(ry * scale),
                          round((rx + rw) * scale), round((ry + rh) * scale)))
    if grid and grid.get("enabled"):
        draw_grid(shot, scale, grid.get("spacing", 100), grid.get("font_size", 40))
    shot.save(out_path)
    return out_path, scale, shot.width, shot.height


def write_trace(log_path, text):
    """Append a chunk to the per-run debug trace."""
    with open(log_path, "a") as f:
        f.write(text)


def bring_to_front(app_name):
    """Activate the target macOS app so we never depend on manual window focus."""
    subprocess.run(["osascript", "-e", f'tell application "{app_name}" to activate'], check=True)


def execute(action):
    """Perform the action with pyautogui. Returns a human-readable description of
    the concrete call made for the debug trace.

    Backends return coordinates already in LOGICAL screen space (= pyautogui.size()),
    which is exactly pyautogui's click space — so we click them directly, no scaling.
    """
    kind = action["action"]

    if kind in ("click", "double_click", "right_click"):
        x, y = action["x"], action["y"]
        if kind == "click":
            pyautogui.click(x, y)
        elif kind == "double_click":
            pyautogui.doubleClick(x, y)
        else:
            pyautogui.rightClick(x, y)
        return f"{kind} click_pt=({x},{y}) [logical]"
    elif kind == "type":
        pyautogui.typewrite(action["text"], interval=0.02)
        return f"type text={action['text']!r}"
    elif kind == "hotkey":
        pyautogui.hotkey(*action["keys"])
        return f"hotkey keys={action['keys']}"
    elif kind == "scroll":
        pyautogui.scroll(action["amount"])
        return f"scroll amount={action['amount']}"
    elif kind == "wait":
        return "wait (no-op)"
    else:
        raise ValueError(f"Unknown action: {kind}")


def focus_target(settings):
    """Bring the configured app to the front and wait for it to settle."""
    app = settings.get("target_app")
    if app:
        print(f"Bringing '{app}' to front ...", file=sys.stderr)
        bring_to_front(app)
        time.sleep(settings.get("focus_delay", 1.0))


def run_once(backend, handle, settings, task):
    """Dry run: one screenshot -> backend -> coordinates, execute NOTHING.
    Writes everything (raw reply, parsed action, coords, timing) to a JSON file
    so we can validate coordinates before trusting any click."""
    focus_target(settings)

    region = get_region(settings)
    shot_path = os.path.join(settings["screenshots"]["dir"], "once.png")
    shot_path, scale, shot_w, shot_h = grab_screenshot(shot_path, settings.get("grid"), region)

    t0 = time.time()
    action = backend.predict(handle, task, [], shot_path, region, settings)
    elapsed = time.time() - t0

    result = {
        "task": task,
        "backend": settings["backend"],
        "screenshot": shot_path,
        "screenshot_size_px": [shot_w, shot_h],
        "scale": scale,
        "inference_seconds": round(elapsed, 2),
        "raw_output": action.get("raw", ""),
        "parsed_action": {k: v for k, v in action.items() if k != "raw"},
    }
    if "x" in action and "y" in action:
        result["coords"] = {"click_pt": [action["x"], action["y"]]}  # logical coords

    out_path = os.path.join(settings["screenshots"]["dir"], "once_result.json")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)

    print(json.dumps(result, indent=2))
    print(f"\nNo action executed. Result written to {out_path}", file=sys.stderr)


def load_invoice_substitutions(input_path):
    """Read an invoice-request JSON and build the {token: value} map the workflow
    template uses. Single-line invoices only: we take the first line and ignore the
    rest. All values are coerced to str so they drop straight into target/text."""
    with open(input_path) as f:
        data = json.load(f)
    line = data["lines"][0]
    return {
        "party": str(data["party"]),
        "product": str(line["product"]),
        "quantity": str(line["quantity"]),
        "unit_price": str(line["unit_price"]),
    }


def apply_substitutions(step, subs):
    """Replace the literal {party}/{product}/{quantity}/{unit_price} tokens in a
    step's `target` and `text` strings. Plain str.replace (not str.format) because
    the descriptions contain other braces we must leave alone."""
    step = dict(step)
    for field in ("target", "text"):
        if field in step:
            value = step[field]
            for token, replacement in subs.items():
                value = value.replace("{" + token + "}", replacement)
            step[field] = value
    return step


def run_workflow(backend, handle, settings, workflow_path, start_step=1, input_path=None):
    """Deterministic step-runner. The workflow YAML holds a fixed ordered list of
    steps; we execute exactly one per iteration, in order, never re-judging
    progress. Keyboard steps (hotkey/type/scroll/wait) run directly with no model
    call. Click steps ask the backend ONLY for the coordinates of a described
    target, then click once. A screenshot + trace is written for every step so a
    failure is pinpointed to a single step.

    If input_path is given, invoice values from that JSON are substituted into the
    template's tokens at runtime, so one template drives any party/product/qty/price."""
    focus_target(settings)

    with open(workflow_path) as f:
        steps = yaml.safe_load(f)["steps"]

    if input_path:
        subs = load_invoice_substitutions(input_path)
        steps = [apply_substitutions(step, subs) for step in steps]

    # The rectangle we look at and click within: full screen, or just the target
    # window when capture_window is set. Read ONCE here (the window stays put for
    # the run) so dialogs that open over it are still inside the same crop.
    region = get_region(settings)

    log_path = os.path.join(settings["screenshots"]["dir"], "trace.log")
    with open(log_path, "w") as f:  # fresh trace per run
        f.write(f"WORKFLOW: {workflow_path}\nBACKEND: {settings['backend']}\n"
                f"STEPS: {len(steps)}\nREGION: {region}\n")

    for i, step in enumerate(steps, 1):
        if i < start_step:  # resume mid-flow: Tryton must already be in step i's state
            continue
        shot_path = os.path.join(settings["screenshots"]["dir"], f"step_{i:02d}.png")
        shot_path, scale, shot_w, shot_h = grab_screenshot(shot_path, settings.get("grid"), region)

        kind = step["action"]
        # Write the header BEFORE locating, so a crash inside locate() still
        # leaves a trace pointing at the exact step and target.
        write_trace(log_path, "".join([
            f"\n===== STEP {i}/{len(steps)}: {kind} =====\n",
            f"[INPUT IMAGE]  {shot_path}  size={shot_w}x{shot_h}px  scale={scale}\n",
        ]))

        if kind in ("click", "double_click", "right_click"):
            if "at_frac" in step:
                # Fixed position as a FRACTION of the REGION, resolved at runtime —
                # so it follows the window wherever it sits (e.g. Tryton in a screen
                # half). Used only for the label-less / ambiguous chrome buttons the
                # vision model can't localize.
                fx, fy = step["at_frac"]
                x = round(region[0] + fx * region[2])
                y = round(region[1] + fy * region[3])
                write_trace(log_path, f"[FIXED-FRAC]  {step['at_frac']} -> ({x},{y}) [logical]\n")
            else:
                target = step["target"]
                write_trace(log_path, f"[TARGET]  {target}\n")
                t0 = time.time()
                x, y, raw = backend.locate(handle, target, shot_path, region, settings)
                elapsed = time.time() - t0
                write_trace(log_path, f"[LOCATE]  ({elapsed:.1f}s) -> ({x},{y}) [logical]\n[RAW]  {raw}\n")
            step = {**step, "x": x, "y": y}

        line = f"{i}. {kind} {step.get('target', step.get('keys', step.get('text', '')))}"
        print(line)

        detail = execute(step)
        write_trace(log_path, f"[EXECUTION]  {detail}\n")
        time.sleep(settings["loop"]["delay_after_action"])

    print("Workflow complete.", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("task", nargs="?", help="what the agent should accomplish (model-driven mode)")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--once", action="store_true",
                    help="dry run: one screenshot -> coords, execute nothing")
    ap.add_argument("--workflow",
                    help="run a fixed YAML workflow deterministically (harness owns the sequence)")
    ap.add_argument("--start-step", type=int, default=1,
                    help="resume a workflow from this step (Tryton must already be in that state)")
    ap.add_argument("--input",
                    help="JSON invoice request whose party/product/quantity/unit_price "
                         "are substituted into the workflow template (--workflow only)")
    args = ap.parse_args()

    settings = load_settings(args.config)
    os.makedirs(settings["screenshots"]["dir"], exist_ok=True)

    backend = load_backend(settings)
    print(f"Loading backend '{settings['backend']}': {settings['model']['path']} ...", file=sys.stderr)
    handle = backend.load(settings)

    if args.workflow:
        run_workflow(backend, handle, settings, args.workflow, args.start_step, args.input)
        return

    if args.once:
        run_once(backend, handle, settings, args.task)
        return

    focus_target(settings)

    start_delay = settings["loop"].get("start_delay", 0)
    if start_delay:
        print(f"Starting in {start_delay}s ...", file=sys.stderr)
        time.sleep(start_delay)

    # One scannable debug trace per run: input image, full raw reply, parsed
    # action, and the concrete pyautogui call — every step.
    log_path = os.path.join(settings["screenshots"]["dir"], "trace.log")
    with open(log_path, "w") as f:  # fresh trace per run
        f.write(f"TASK: {args.task}\nBACKEND: {settings['backend']}\n")

    region = get_region(settings)
    history = []
    for step in range(1, settings["loop"]["max_steps"] + 1):
        shot_path = os.path.join(settings["screenshots"]["dir"], f"step_{step:02d}.png")
        shot_path, scale, shot_w, shot_h = grab_screenshot(shot_path, settings.get("grid"), region)

        t0 = time.time()
        action = backend.predict(handle, args.task, history, shot_path, region, settings)
        elapsed = time.time() - t0

        parsed = {k: v for k, v in action.items() if k != "raw"}
        write_trace(log_path, "".join([
            f"\n===== STEP {step} =====\n",
            f"[INPUT IMAGE]  {shot_path}  size={shot_w}x{shot_h}px  scale={scale}\n",
            f"[RAW OUTPUT]  ({elapsed:.1f}s)\n{action.get('raw', '')}\n",
            f"[PARSED ACTION]  {parsed}\n",
        ]))

        line = f"{step}. {action['action']} {action.get('thought', '')}".strip()
        print(line)
        history.append(line)

        if action["action"] == "done":
            write_trace(log_path, f"[EXECUTION]  done -> {action.get('reason', '')}\n")
            print(f"DONE: {action.get('reason', '')}")
            return

        detail = execute(action)
        write_trace(log_path, f"[EXECUTION]  {detail}\n")

        time.sleep(settings["loop"]["delay_after_action"])

    print("Reached max_steps without finishing.", file=sys.stderr)


if __name__ == "__main__":
    main()
