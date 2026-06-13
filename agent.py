"""Local GUI agent: screenshot -> Gemma (mlx-vlm) -> pyautogui action -> repeat.

Fully on-device. No screen data leaves the machine.

Usage:
    python agent.py "open the customer list and create a new customer named ACME"
"""

import argparse
import json
import os
import re
import sys
import time

import pyautogui
import yaml
from mlx_vlm import generate, load
from mlx_vlm.prompt_utils import apply_chat_template
from mlx_vlm.utils import load_config

pyautogui.FAILSAFE = True  # slam the mouse into a corner to abort


def load_settings(path):
    with open(path) as f:
        return yaml.safe_load(f)


def grab_screenshot(out_path):
    """Save a screenshot and return (path, scale) where scale maps screenshot
    pixels -> logical points (Retina screens report 2x pixels vs click coords)."""
    shot = pyautogui.screenshot()
    shot.save(out_path)
    logical_w, _ = pyautogui.size()
    scale = shot.width / logical_w
    return out_path, scale


def ask_model(model, processor, mlx_config, prompt, image_path, settings):
    formatted = apply_chat_template(processor, mlx_config, prompt, num_images=1)
    result = generate(
        model,
        processor,
        formatted,
        [image_path],
        max_tokens=settings["model"]["max_tokens"],
        temperature=settings["model"]["temperature"],
        verbose=False,
    )
    # mlx-vlm returns a result object (newer) or a plain string (older).
    return result if isinstance(result, str) else getattr(result, "text", str(result))


def parse_action(raw):
    """Pull the first JSON object out of the model's reply."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object in model reply:\n{raw}")
    return json.loads(match.group(0))


def execute(action, scale):
    kind = action["action"]

    def pt(coord):  # screenshot pixels -> logical click points
        return coord / scale

    if kind in ("click", "double_click", "right_click"):
        x, y = pt(action["x"]), pt(action["y"])
        if kind == "click":
            pyautogui.click(x, y)
        elif kind == "double_click":
            pyautogui.doubleClick(x, y)
        else:
            pyautogui.rightClick(x, y)
    elif kind == "type":
        pyautogui.typewrite(action["text"], interval=0.02)
    elif kind == "hotkey":
        pyautogui.hotkey(*action["keys"])
    elif kind == "scroll":
        pyautogui.scroll(action["amount"])
    elif kind == "wait":
        pass
    else:
        raise ValueError(f"Unknown action: {kind}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("task", help="what the agent should accomplish")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    settings = load_settings(args.config)
    os.makedirs(settings["screenshots"]["dir"], exist_ok=True)

    print(f"Loading model: {settings['model']['path']} ...", file=sys.stderr)
    model, processor = load(settings["model"]["path"])
    mlx_config = load_config(settings["model"]["path"])

    history = []
    for step in range(1, settings["loop"]["max_steps"] + 1):
        shot_path = os.path.join(settings["screenshots"]["dir"], f"step_{step:02d}.png")
        shot_path, scale = grab_screenshot(shot_path)

        prompt = settings["system_prompt"].format(
            task=args.task,
            history="\n".join(history) if history else "(none yet)",
        )
        raw = ask_model(model, processor, mlx_config, prompt, shot_path, settings)
        action = parse_action(raw)

        line = f"{step}. {action['action']} {action.get('thought', '')}".strip()
        print(line)
        history.append(line)

        if action["action"] == "done":
            print(f"DONE: {action.get('reason', '')}")
            return

        execute(action, scale)
        time.sleep(settings["loop"]["delay_after_action"])

    print("Reached max_steps without finishing.", file=sys.stderr)


if __name__ == "__main__":
    main()
