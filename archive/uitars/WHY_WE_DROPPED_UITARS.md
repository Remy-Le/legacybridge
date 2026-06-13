# Why we dropped UI-TARS-1.5-7B

**Date:** 2026-06-13
**Status:** Parked / archived. Not wired into the agent (`config.yaml` runs `backend: qwen`).

## What this folder is

The UI-TARS grounding backend and its standalone validator, moved here so the
live `backends/` directory only holds what we actually use.

- `uitars.py` — the backend (was `backends/uitars.py`). Implements the agent's
  `load()` / `predict()` contract for UI-TARS-1.5-7B via mlx-vlm.
- `validate_uitars.py` — standalone grounding check (activate Tryton →
  screenshot → predict → crop around the point, **no clicking**). Import was
  changed from `from backends import uitars` to `import uitars` since the two
  files now live side-by-side.

The model itself is **not** in the repo. It was downloaded to
`/Users/peter/models/mlx/UI-Tars-1.5-7B-4bit-mlx` (~8.4 GB). To reclaim the disk:

```bash
rm -rf /Users/peter/models/mlx/UI-Tars-1.5-7B-4bit-mlx
```

## The decision in one line

UI-TARS-1.5-7B grounds too imprecisely on Tryton's dense, small-font tree menu —
our actual benchmark — and there's no better open UI-TARS to fall back to, so we
moved on to stronger grounders (Holo1.5-7B / Qwen3-VL-30B).

## What we observed

The benchmark task is **"open customer invoices"** (an item in Tryton's left
navigation tree, under Financial → Invoices).

- **The backend and coordinate math are correct.** UI-TARS-1.5-7B is a
  Qwen2.5-VL model: it emits absolute pixels in the *smart-resized* image space
  using Qwen's native grounding tokens, e.g.
  `click(point='<|box_start|>(480,304)<|box_end|>')` — **not** the [0,1000]
  scheme of UI-TARS 1.0. We convert by deriving the resized dimensions straight
  from the processor's `image_grid_thw`, so the conversion always matches what
  the model actually saw. Proven correct: on a clear target (the *Supplier
  Invoices* tab) the predicted point landed dead-center, x **and** y exact.
- **It still missed the benchmark.** On "open customer invoices" the model
  correctly *named* "Customer Invoices" in its reasoning but pointed ~2× too low
  (into the Productions/Projects area). The x-coordinate (the nav column) was
  exact; only the row was wrong. This is a model grounding-accuracy miss on a
  dense tree, **not** a Retina/`smart_resize`/coordinate bug (those are handled
  correctly, as the Supplier Invoices hit proves).
- **It's slow.** ~49–71 s per step on this Mac (4-bit 7B via mlx-vlm).

## Why this is expected, not a fluke

Tryton's tiny tree menu is exactly the **ScreenSpot-Pro** regime (dense,
high-resolution, professional desktop UIs with tiny targets). UI-TARS-1.5-7B
scores ~**39%** there — i.e. it misses well over half of such targets. So a miss
on a small tree row is the expected behaviour, not bad luck.

## Why we didn't just use "a bigger UI-TARS"

- **1.5 has no larger open weights** — 7B is the only open UI-TARS 1.5.
- The only bigger open UI-TARS is the **older 1.0-generation 72B**, which is not
  better at grounding than 1.5-7B (older generation), is ~40 GB, and is far too
  slow on this Mac.
- The full **UI-TARS-1.5** flagship (the model with the headline ScreenSpot-Pro
  ~61.6% number) is **closed** — cloud/API only, no downloadable weights, which
  defeats the point of a local on-device agent.

## What we're using instead

From research into open-source GUI grounding (2025–2026), in priority order:

1. **Holo1.5-7B** (`mlx-community/holo1.5-7b-mlx`, Apache-2.0, Qwen2.5-VL base) —
   near drop-in for this backend, **57.9%** on ScreenSpot-Pro vs UI-TARS's 39%.
2. **Crop-and-zoom (coarse → fine)** — training-free second pass that stacks on
   any model; big gains on small dense targets.
3. **macOS Accessibility (AX) API / OCR hybrid** — for a text tree, read the row
   labels and click the matched box; most reliable, sidesteps pixel grounding
   entirely.
4. **Qwen3-VL-30B** (the current `qwen` backend) — ~60% on the leaderboard; good
   as both planner and grounder, but slower.

Gemma is poor at coordinate grounding and is not used for the clicking role.

## If we ever revisit UI-TARS

The backend here is complete and correct — re-wiring it is just moving
`uitars.py` back into `backends/` and setting `backend: uitars` in `config.yaml`
(plus a `uitars:` config section: `path`, `max_tokens`, `temperature`,
`language`, `scroll_amount`, and `grid.enabled: false` — UI-TARS needs clean
screenshots). The reason to revisit would be a newer/larger open UI-TARS
checkpoint, or pairing this backend with the crop-and-zoom refinement above.
