# Changelog

## 1.0.0 — 2026-11-26

- Initial public release.
- `ppl.py`: `SplitSecond.exe` palette-limit patcher. CLI now takes the exe path
  as a positional argument and `-n` (1–255), with byte-verified patch sites and
  dry-run-by-default behaviour. Purpose-built for Cracked Retail / DC builds.
- `gfx_swatches.py`: `Fe_VehicleSelect.gfx` swatch-slot patcher. Accepts the
  GFX path as a positional argument, auto-lays-out a grid, grows the panel only
  as needed, and safely handles up to 70 slots (5 × 14) without reworking
  dimensions.
- `docs/`: full write-ups of both patches (root cause, site tables, layout and
  options).
- `screenshots/`: placeholder structure for before/after capture.
