# `gfx_swatches.py` — how the GFX patch works

## Target

`Fe_VehicleSelect.gfx` — the vehicle-select screen. In the SSDC build it lives at:

```
Deferred/UI/frontend/Flash/Fe_VehicleSelect.gfx
```

The file is an SWF-family container (signatures `GFX`, `CFX`, `FWS`, `CWS`).

## The two stock limits

Decompiled ActionScript 2 of the root timeline reveals how the swatch strip
works:

```
function AddColourSwatch(zCol) {            // called once per palette colour
    var swatch = eval("_root.blackholder.swatch" + numColourSwatches);
    swatch._visible = true;
    swatch.SetColour(zCol);
    numColourSwatches++;
}
function ClearColourSwatches() {            // hides every slot
    for (i = 0; i < 12; i++) eval("_root.blackholder.swatch" + i)._visible = false;
    numColourSwatches = 0;
}
```

So there are exactly two limits:

1. the `blackholder` clip only contains the instances `swatch0 .. swatch11`
   (character class `mcColourSwatch`); anything above is silently ignored;
2. `ClearColourSwatches()` hides slots with a hard-coded `12` loop bound.

## What the patch does

For a requested slot count `N` (1 .. N_max):

- adds `swatch12 .. swatch(N-1)` as copies of the last slot,
- lays the slots out as a grid: `--per-row` slots per row (default 14). The
  first 12 slots of the first row keep their stock positions, so a vehicle with
  12 colours or fewer looks exactly like the original screen,
- grows the black **panel** behind the swatches only as far as the grid needs:
  - width: only when the first row needs more than the stock 12 slots,
  - height: only when the rows no longer fit into the stock panel,
- changes the loop bound in `ClearColourSwatches()` from 12 to `N`,
- refuses (or warns with `--force`) when the grid would run into the
  "SELECT VEHICLE" title area below the panel.

## Auto-fitting and the 70-slot default

For the stock `Fe_VehicleSelect.gfx`, the panel leaves room for **5 rows × 14 =
70 slots** above the title area without the script having to rework the
dimensions. Running the tool prints this limit:

```
limit    : 5 rows x 14 = 70 slots fit above y = 276
```

`N ≤ 70` is the safe, "just works" range. Going higher requires `--force` (the
panel extends) and a careful check that the result still fits the screen.

## Arguments

```
python gfx_swatches.py <gfx> [-n N] [--per-row R] [--pitch P]
                       [--bottom-pad B] [--right-pad R2] [--max-bottom M]
                       [--force] [--out FILE] [--apply]
```

| Argument | Default | Meaning |
| --- | --- | --- |
| `gfx` | — | path to `Fe_VehicleSelect.gfx` (required, positional) |
| `-n, --count` | 24 | number of colour slots wanted |
| `--per-row` | 14 | slots per row (minimum 12) |
| `--pitch` | measured | distance between slots in px |
| `--bottom-pad` | 9.0 | space kept below the last row (px) |
| `--right-pad` | 16.0 | space kept right of the widest row (px) |
| `--max-bottom` | 276.0 | lowest allowed panel edge, measured from panel top |
| `--force` | off | allow a panel that passes `--max-bottom` |
| `--out` | in-place | write the patched file elsewhere |
| `--apply` | off | write the result (default is dry run) |

## Verification and backups

- The tool re-parses the patched file and asserts: top-level tag count
  unchanged, all depths unique, the loop bound patched, the panel outline
  intact, and every swatch inside the panel.
- Default mode is a dry run.
- With `--apply`, a backup is written to `<gfx>.orig` on the first patch.

## Notes / caution

- The shape encoder round-trips the untouched panel exactly; if that invariant
  fails the script aborts.
- Exceeding `N = 70` (or changing `--per-row`/padding aggressively) can push the
  grid into the title area or off-screen — test in-game after patching.
- This script only controls how many swatches the menu **shows**. It is
  independent of the executable capacity set by `ppl.py` (which may be larger).
  Pick `N` to fit the panel — the default safe range is 12–70. Colours that the
  exe stores beyond the visible slots are simply not displayed, not corrupted.
