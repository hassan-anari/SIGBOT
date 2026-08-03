# Phase 5B-1 local Renderer Lab

The Renderer Lab is a developer-only Pillow calibration tool for the three initial
signal cards. It is deliberately not registered with the production rendering
dispatcher and makes no Telegram, market, database, Redis, or clock calls. Phase
5B-2 owns any future production integration.

## Local assets

Place neutral PNG templates at `.renderer_lab/templates/ourbit.png`,
`.renderer_lab/templates/toobit.png`, and `.renderer_lab/templates/lbank.png`.
Place a locally licensed TrueType/OpenType font under `.renderer_lab/fonts/` and
pass its exact path with `--font`. Outputs belong in `.renderer_lab/output/`.
The entire directory is ignored because templates, fonts, logos, and generated
previews are local assets and must never enter source control.

## Commands

```sh
python -m chainura.rendering.lab preview --exchange ourbit --template .renderer_lab/templates/ourbit.png --output .renderer_lab/output/ourbit.png --fixture composite-long --font .renderer_lab/fonts/local.ttf
python -m chainura.rendering.lab preview-all --fixture composite-short --font .renderer_lab/fonts/local.ttf
python -m chainura.rendering.lab watch --exchange toobit --template .renderer_lab/templates/toobit.png --output .renderer_lab/output/toobit.png --fixture long-symbol --font .renderer_lab/fonts/local.ttf --debug
```

`--debug` adds obvious layer boxes, measured text boxes, layer names, and fitted
font sizes. Fixed fixture names are `composite-long`, `composite-short`,
`single-long`, `single-short`, `long-symbol`, `small-price`, and `high-leverage`.
Watch mode reloads only the selected exchange's layout module when that Python
file changes. Temporary syntax or layout errors are reported once per change;
the process stays alive and renders again after the next saved correction.

## Calibration workflow

1. Place all three template PNGs locally.
2. Select and pass a local font.
3. Render fixed fixtures.
4. Enable debug overlays.
5. Adjust only `chainura/rendering/layouts/ourbit.py`, `toobit.py`, or `lbank.py`
   for the matching exchange.
6. Re-render in watch mode.
7. Verify Long, Short, long Symbol, small-price, and high-leverage fixtures.
8. Keep final coordinate changes in typed Python code.
9. Do not commit local templates, fonts, logos, or generated previews.

The exchange modules are intentionally independent. The shared Pillow package
only measures, fits, composites, validates, and writes images; it contains no
layout coordinates. Coordinates in this phase are calibration values, not a
claim that production assets or final design measurements have been approved.
