"""Backend for JSON-emitting mlx-vlm models (Qwen3-VL, Gemma, ...).

The model is prompted to reply with a single JSON action object. Coordinates are
converted to LOGICAL screen pixels according to `model.coord_space` in config
(different model families report coordinates in different spaces):

  - "logical"    : already in logical screen points (what Gemma 4 emits) -> as-is
  - "image"      : absolute pixels of the input screenshot -> scale to logical
  - "normalized" : 0..1000 over the image -> scale to logical

The right value is found empirically with `--once` + a crop check.
"""

import json
import re

from mlx_vlm import generate
from mlx_vlm import load as mlx_load
from mlx_vlm.prompt_utils import apply_chat_template
from mlx_vlm.utils import load_config
from PIL import Image


def load(settings):
    path = settings["model"]["path"]
    model, processor = mlx_load(path)
    return {"model": model, "processor": processor, "mlx_config": load_config(path)}


def predict(handle, task, history, image_path, region, settings):
    # Plain .replace, not .format: the prompt embeds a literal JSON example whose
    # braces would otherwise be parsed as format fields.
    prompt = (
        settings["system_prompt"]
        .replace("{task}", task)
        .replace("{history}", "\n".join(history) if history else "(none yet)")
    )
    formatted = apply_chat_template(handle["processor"], handle["mlx_config"], prompt, num_images=1)
    result = generate(
        handle["model"],
        handle["processor"],
        formatted,
        [image_path],
        max_tokens=settings["model"]["max_tokens"],
        temperature=settings["model"]["temperature"],
        verbose=False,
    )
    # mlx-vlm returns a result object (newer) or a plain string (older).
    raw = result if isinstance(result, str) else getattr(result, "text", str(result))

    action = _parse(raw)
    _normalize_point(action)
    _to_logical(action, image_path, region, settings["model"].get("coord_space", "logical"))
    action["raw"] = raw
    return action


def locate(handle, target, image_path, region, settings):
    """Deterministic-runner helper: ask the model ONLY where a described target
    is, and return (x, y, raw) in LOGICAL screen coordinates. The harness owns
    the step sequence — the model never decides what to do, only where to click."""
    prompt = settings["locate_prompt"].replace("{target}", target)
    formatted = apply_chat_template(handle["processor"], handle["mlx_config"], prompt, num_images=1)
    result = generate(
        handle["model"],
        handle["processor"],
        formatted,
        [image_path],
        max_tokens=settings["model"]["max_tokens"],
        temperature=settings["model"]["temperature"],
        verbose=False,
    )
    raw = result if isinstance(result, str) else getattr(result, "text", str(result))
    try:
        action = _parse(raw)
    except ValueError:
        # locate only needs a coordinate pair; if the JSON is unparseable
        # (e.g. {"x": 317, 309} — missing the y key), take the first two ints.
        nums = re.findall(r"-?\d+", raw)
        if len(nums) < 2:
            raise
        action = {"x": int(nums[0]), "y": int(nums[1])}
    _normalize_point(action)
    _to_logical(action, image_path, region, settings["model"].get("coord_space", "logical"))
    return action["x"], action["y"], raw


def _parse(raw):
    """Pull the first JSON object out of the model's reply, tolerating the
    malformations Qwen emits: markdown fences, a second trailing object, single
    quotes, unquoted keys, trailing commas. The raw reply is always included in
    the error so a parse failure is never blind."""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # Match INNERMOST {...} blocks (no nested braces). Our action objects are
    # always flat, and Qwen sometimes wraps the real one in an extra brace
    # ( { {"x":..} } ) or appends a second object — this skips both. Return the
    # first candidate that parses and actually carries a coordinate or action.
    candidates = re.findall(r"\{[^{}]*\}", text, re.DOTALL)
    if not candidates:
        raise ValueError(f"No JSON object in model reply:\n{raw}")
    for blob in candidates:
        for candidate in (blob, _relax_json(blob)):
            try:
                obj = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if "x" in obj or "action" in obj:
                return obj
    raise ValueError(f"Could not parse JSON from model reply:\n{raw}")


def _relax_json(s):
    """Best-effort repair of the JSON-ish shapes Qwen sometimes emits."""
    s = re.sub(r",\s*([}\]])", r"\1", s)                          # trailing commas
    s = s.replace("'", '"')                                        # single -> double quotes
    s = re.sub(r"([{,]\s*)([A-Za-z_]\w*)(\s*:)", r'\1"\2"\3', s)   # quote bare keys
    return s


def _normalize_point(action):
    """Qwen is inconsistent about coordinate formatting: ints, floats, strings
    ("101"), or a packed array (x:[x,y]). Normalize to plain int x and y."""
    x = action.get("x")
    if isinstance(x, (list, tuple)) and len(x) >= 2:
        x, action["y"] = x[0], x[1]
        action["x"] = x
    if "x" in action:
        action["x"] = _to_int(action["x"])
    if "y" in action:
        action["y"] = _to_int(action["y"])


def _to_int(v):
    if isinstance(v, (int, float)):
        return int(v)
    m = re.search(r"-?\d+", str(v))
    if not m:
        raise ValueError(f"No integer in coordinate value: {v!r}")
    return int(m.group(0))


def _to_logical(action, image_path, region, space):
    """Map the model's reported coordinates into LOGICAL screen points.

    `region` = (x, y, w, h) is the logical rectangle the input image covers:
    full screen (0, 0, W, H) normally, or the Tryton window rect when the agent
    crops its screenshot to one window. The model's coords are relative to the
    (possibly cropped) image, so we scale by the region size and add its offset."""
    if "x" not in action or "y" not in action:
        return
    rx, ry, rw, rh = region
    if space == "logical":
        return  # already absolute logical points (full-screen capture only)
    if space == "image":
        img_w, img_h = Image.open(image_path).size
        action["x"] = rx + round(action["x"] / img_w * rw)
        action["y"] = ry + round(action["y"] / img_h * rh)
    elif space == "normalized":
        action["x"] = rx + round(action["x"] / 1000 * rw)
        action["y"] = ry + round(action["y"] / 1000 * rh)
    else:
        raise ValueError(f"Unknown coord_space: {space}")
