"""UI-TARS 1.5 backend for the local GUI agent.

Implements the agent's backend contract: load() once, then predict() per step,
returning a canonical action dict in LOGICAL screen coordinates.

Model: flin775/UI-Tars-1.5-7B-4bit-mlx — an MLX 4-bit port of
ByteDance-Seed/UI-TARS-1.5-7B. Run via mlx_vlm, the same library the Gemma
backend uses (see agent.py's ask_model for the call pattern we mirror here).

COORDINATE SPACE (the part that changed between UI-TARS 1.0 and 1.5):
UI-TARS 1.5-7B is a Qwen2.5-VL model. It does NOT use the old [0,1000]
normalization. It emits ABSOLUTE PIXEL coordinates in the space of the
*smart-resized* image that the Qwen2.5-VL processor actually feeds the model.
So the conversion is:

    fraction   = coord_model / resized_dim        # resized_dim from the processor
    coord_logical = round(fraction * logical_dim)  # logical_dim from screen=(w,h)

We get resized_dim straight from the processor's image_grid_thw (grid units x
patch_size), so it always matches exactly what mlx_vlm fed the vision encoder —
no need to reimplement smart_resize.

ACTION SPACE (verbatim from bytedance/UI-TARS COMPUTER_USE_DOUBAO):
    click(point='<point>x y</point>')
    left_double(point='<point>x y</point>')   -> double_click
    right_single(point='<point>x y</point>')  -> right_click
    type(content='xxx')                        -> type
    hotkey(key='ctrl c')                       -> hotkey
    scroll(point='<point>x y</point>', direction='down|up|left|right')
    wait()                                     -> wait
    finished(content='xxx')                    -> done
    drag(...)  -> not in our canonical set; we crash fast on it (see below).

UI-TARS is trained on CLEAN screenshots — keep grid.enabled: false for it.

Standalone grounding check lives in ../validate_uitars.py (brings Tryton to the
front, screenshots, predicts, crops around the point — no clicking).
"""

import re

from PIL import Image
from mlx_vlm import generate, load as mlx_load
from mlx_vlm.prompt_utils import apply_chat_template
from mlx_vlm.utils import load_config

# Verbatim COMPUTER_USE_DOUBAO template (bytedance/UI-TARS codes/ui_tars/prompt.py).
# {language} controls the language of the Thought; {instruction} is the task.
# {history} is our addition: prior step summaries as text, since our agent feeds
# one screenshot per step rather than the full multi-turn screenshot history.
_PROMPT_TEMPLATE = """You are a GUI agent. You are given a task and your action history, with screenshots. You need to perform the next action to complete the task.

## Output Format
```
Thought: ...
Action: ...
```

## Action Space

click(point='<point>x1 y1</point>')
left_double(point='<point>x1 y1</point>')
right_single(point='<point>x1 y1</point>')
drag(start_point='<point>x1 y1</point>', end_point='<point>x2 y2</point>')
hotkey(key='ctrl c') # Split keys with a space and use lowercase. Also, do not use more than 3 keys in one hotkey action.
type(content='xxx') # Use escape characters \\', \\", and \\n in content part to ensure we can parse the content in normal python string format. If you want to submit your input, use \\n at the end of content.
scroll(point='<point>x1 y1</point>', direction='down or up or right or left') # Show more information on the `direction` side.
wait() #Sleep for 5s and take a screenshot to check for any changes.
finished(content='xxx') # Use escape characters \\', \\", and \\n in content part to ensure we can parse the content in normal python string format.


## Note
- Use {language} in `Thought` part.
- Write a small plan and finally summarize your next action (with its target element) in one sentence in `Thought` part.

## Action History
{history}

## User Instruction
{instruction}"""

# UI-TARS verb -> our canonical action name.
_VERB_MAP = {
    "click": "click",
    "left_double": "double_click",
    "right_single": "right_click",
    "type": "type",
    "hotkey": "hotkey",
    "scroll": "scroll",
    "wait": "wait",
    "finished": "done",
}


def load(settings):
    """Load the MLX model once; return an opaque handle for predict()."""
    cfg = settings["uitars"]
    model, processor = mlx_load(cfg["path"])
    mlx_config = load_config(cfg["path"])
    return {"model": model, "processor": processor, "mlx_config": mlx_config}


def predict(handle, task, history, image_path, screen, settings):
    """One step: screenshot -> UI-TARS -> canonical action in LOGICAL pixels."""
    cfg = settings["uitars"]
    logical_w, logical_h = screen
    processor = handle["processor"]

    prompt = _PROMPT_TEMPLATE.format(
        language=cfg.get("language", "English"),
        history="\n".join(history) if history else "(none yet)",
        instruction=task,
    )
    formatted = apply_chat_template(processor, handle["mlx_config"], prompt, num_images=1)
    result = generate(
        handle["model"],
        processor,
        formatted,
        [image_path],
        max_tokens=cfg["max_tokens"],
        temperature=cfg["temperature"],
        verbose=False,
    )
    raw = result if isinstance(result, str) else getattr(result, "text", str(result))

    resized_w, resized_h = _resized_dims(processor, image_path)
    thought, verb, args = _parse(raw)
    return _to_canonical(thought, verb, args, raw,
                         resized_w, resized_h, logical_w, logical_h, cfg)


def _resized_dims(processor, image_path):
    """Pixel dims of the smart-resized image the model actually saw. Qwen2.5-VL
    reports image_grid_thw in PATCH units (patch_size px each), so resized px =
    grid * patch_size. Running the image_processor here mirrors exactly what
    mlx_vlm does internally, so the coordinate space lines up."""
    img = Image.open(image_path).convert("RGB")
    feat = processor.image_processor(images=img, return_tensors="np")
    _, gh, gw = feat["image_grid_thw"][0]
    ps = processor.image_processor.patch_size
    return int(gw) * ps, int(gh) * ps


def _parse(raw):
    """Split UI-TARS output into (thought, verb, args_string)."""
    thought_m = re.search(r"Thought:\s*(.*?)(?:\nAction:|\Z)", raw, re.DOTALL)
    thought = thought_m.group(1).strip() if thought_m else ""

    action_m = re.search(r"Action:\s*(.*)", raw, re.DOTALL)
    if not action_m:
        raise ValueError(f"No 'Action:' in UI-TARS output:\n{raw}")
    call = action_m.group(1).strip()

    call_m = re.match(r"([a-z_]+)\s*\((.*)\)\s*$", call, re.DOTALL)
    if not call_m:
        raise ValueError(f"Cannot parse UI-TARS action call:\n{call}")
    return thought, call_m.group(1), call_m.group(2)


def _to_canonical(thought, verb, args, raw, rw, rh, lw, lh, cfg):
    if verb == "drag":
        # Our canonical action set has no drag. Crash fast rather than fake it
        # with a click that silently does the wrong thing.
        raise ValueError(f"UI-TARS emitted 'drag', unsupported by the canonical "
                         f"action set:\n{raw}")
    if verb not in _VERB_MAP:
        raise ValueError(f"Unknown UI-TARS verb {verb!r}:\n{raw}")

    out = {"thought": thought, "action": _VERB_MAP[verb], "raw": raw}

    if verb in ("click", "left_double", "right_single"):
        out["x"], out["y"] = _xy(args, rw, rh, lw, lh)
    elif verb == "type":
        out["text"] = _content(args)
    elif verb == "hotkey":
        out["keys"] = _hotkey_keys(args)
    elif verb == "scroll":
        out["amount"] = _scroll_amount(_str_arg(args, "direction"), cfg)
    elif verb == "finished":
        out["reason"] = _content(args)
    # wait: nothing extra
    return out


def _xy(args, rw, rh, lw, lh):
    """Extract the first (x, y) from a coordinate action's args and map model
    (resized-image) pixels -> logical screen pixels.

    Format-agnostic on purpose: this 1.5 checkpoint emits Qwen2.5-VL's native
    grounding tokens, e.g. start_box='<|box_start|>(480,304)<|box_end|>', while
    the documented template shows point='<point>480 304</point>'. Both — and
    plain '(x,y)' — reduce to "the first two integers in the args", which is all
    we need for click/double/right/scroll (their only digits ARE the coordinate;
    type/hotkey/finished never reach here). Coordinates are absolute pixels in
    the smart-resized image, so we divide by the resized dims to get a fraction."""
    nums = re.findall(r"-?\d+", args)
    if len(nums) < 2:
        raise ValueError(f"Could not read x,y coordinates from: {args}")
    xm, ym = int(nums[0]), int(nums[1])
    return round(xm / rw * lw), round(ym / rh * lh)


def _content(args):
    """Extract content='...' and unescape UI-TARS's python-style escapes."""
    m = re.search(r"content\s*=\s*'(.*)'\s*$", args, re.DOTALL)
    if not m:
        raise ValueError(f"No content argument in: {args}")
    s = m.group(1)
    return (s.replace("\\n", "\n").replace("\\'", "'")
             .replace('\\"', '"').replace("\\\\", "\\"))


def _str_arg(args, name):
    m = re.search(name + r"\s*=\s*'([^']*)'", args)
    if not m:
        raise ValueError(f"No {name} argument in: {args}")
    return m.group(1)


def _hotkey_keys(args):
    """key='ctrl c' -> ['ctrl', 'c'], lowercased, with 'control' aliased."""
    raw_keys = _str_arg(args, "key").lower().split()
    return [("ctrl" if k == "control" else k) for k in raw_keys]


def _scroll_amount(direction, cfg):
    """Map UI-TARS direction to pyautogui.scroll amount. Config 'scroll_amount'
    is the magnitude in scroll clicks; negative = content scrolls down (matches
    the agent's convention). pyautogui.scroll is vertical only, so left/right are
    best-effort mapped onto the vertical axis."""
    amount = cfg.get("scroll_amount", 5)
    signs = {"down": -1, "up": 1, "right": -1, "left": 1}
    if direction not in signs:
        raise ValueError(f"Unknown scroll direction: {direction!r}")
    return signs[direction] * amount
