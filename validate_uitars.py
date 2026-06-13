"""Standalone grounding check for the UI-TARS backend. NO CLICKING.

Mirrors the detail of `agent.py --once` (the Gemma/Qwen debug path) but for the
UI-TARS backend, and adds a crop so you can eyeball grounding.

Brings Tryton to the front, takes one screenshot, runs uitars.predict once, and
writes a full debug bundle to runs/:
  - uitars_validate.png        the input screenshot the model saw
  - uitars_validate_crop.png   crop around the returned point (target centered?)
  - uitars_validate.json       task, sizes, scale, model coord space, timing,
                               the exact prompt, raw output, and parsed action

The mouse is never moved and nothing is clicked, so this is safe to run while
another session owns the mouse.

Benchmark task: "open customer invoices".

Usage:
    python validate_uitars.py "open customer invoices"
    python validate_uitars.py "open customer invoices" --app tryton --radius 90
"""

import argparse
import json
import os
import subprocess
import time

import pyautogui
import yaml

from backends import uitars

# Sane defaults so this works even before config.yaml has the uitars section
# merged by the other session. Real runs pick up config.yaml if it has one.
DEFAULT_UITARS = {
    "path": "/Users/peter/models/mlx/UI-Tars-1.5-7B-4bit-mlx",
    "max_tokens": 512,
    "temperature": 0.0,
    "language": "English",
    "scroll_amount": 5,
}


def load_settings(config_path):
    settings = {}
    try:
        with open(config_path) as f:
            settings = yaml.safe_load(f) or {}
    except FileNotFoundError:
        pass
    settings.setdefault("uitars", DEFAULT_UITARS)
    return settings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("task", help="goal to ground a single next action for")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--app", default="tryton", help="macOS app to bring to front")
    ap.add_argument("--focus-delay", type=float, default=1.5,
                    help="seconds to wait after activating the app")
    ap.add_argument("--radius", type=int, default=80,
                    help="half-size (px) of the crop drawn around the point")
    args = ap.parse_args()

    settings = load_settings(args.config)
    cfg = settings["uitars"]
    os.makedirs("runs", exist_ok=True)

    print(f"Bringing '{args.app}' to front ...")
    subprocess.run(["osascript", "-e",
                    f'tell application "{args.app}" to activate'], check=True)
    time.sleep(args.focus_delay)

    print("Loading UI-TARS ...")
    handle = uitars.load(settings)

    shot = pyautogui.screenshot()
    shot_path = "runs/uitars_validate.png"
    shot.save(shot_path)
    logical_w, logical_h = pyautogui.size()
    scale = shot.width / logical_w  # logical -> screenshot px (Retina = 2x)

    # Reconstruct what the backend builds/sees, so the debug bundle is complete
    # without widening the predict() contract.
    prompt = uitars._PROMPT_TEMPLATE.format(
        language=cfg.get("language", "English"), history="(none yet)", instruction=args.task)
    resized_w, resized_h = uitars._resized_dims(handle["processor"], shot_path)

    t0 = time.time()
    action = uitars.predict(handle, args.task, [], shot_path,
                            (logical_w, logical_h), settings)
    elapsed = time.time() - t0

    debug = {
        "task": args.task,
        "app": args.app,
        "screenshot": shot_path,
        "screenshot_size_px": [shot.width, shot.height],
        "logical_size": [logical_w, logical_h],
        "scale": scale,
        "model_coord_space_px": [resized_w, resized_h],  # smart-resized image dims
        "inference_seconds": round(elapsed, 2),
        "prompt": prompt,
        "raw_output": action["raw"],
        "parsed_action": {k: v for k, v in action.items() if k != "raw"},
    }

    crop_path = None
    if "x" in action and "y" in action:
        px, py = int(action["x"] * scale), int(action["y"] * scale)
        r = args.radius
        crop_path = "runs/uitars_validate_crop.png"
        shot.crop((px - r, py - r, px + r, py + r)).save(crop_path)
        debug["coords"] = {
            "logical": [action["x"], action["y"]],
            "screenshot_px": [px, py],
            "crop": crop_path,
        }

    json_path = "runs/uitars_validate.json"
    with open(json_path, "w") as f:
        json.dump(debug, f, indent=2)

    # Console summary (full detail is in the JSON).
    print(f"\ninference: {elapsed:.1f}s   "
          f"resized space: {resized_w}x{resized_h}px   scale: {scale}")
    print("\n=== RAW ===\n" + action["raw"])
    print("\n=== PARSED ACTION ===\n" + json.dumps(debug["parsed_action"], indent=2))
    if crop_path:
        c = debug["coords"]
        print(f"\nPoint logical {tuple(c['logical'])} -> screenshot px "
              f"{tuple(c['screenshot_px'])}")
        print(f"Crop: {crop_path} (target should be centered)")
    else:
        print(f"\nAction '{action['action']}' has no coordinates — nothing to crop.")
    print(f"Debug bundle: {json_path}")


if __name__ == "__main__":
    main()
