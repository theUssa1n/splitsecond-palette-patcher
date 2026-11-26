# Screenshots

Capture these from inside the game (same resolution, PNG, ideally with the HUD
hidden). Place the files here with the exact names below.

| File | What it shows |
| --- | --- |
| `01-broken-10plus.png` | **Before** patches — a car given a palette beyond the stock 9; colour 10 is broken/truncated. |
| `02-exe-patched-12.png` | After `ppl.py -n 24 --apply` only — colour 12 is now correct, but the screen still shows 12 slots (GFX not patched yet). |
| `03-gfx-patched-24.png` | After both patches — sitting on slot 24, remaining slots are displayed and clickable. |

The story: the stock screen caps at 9 and breaks when a car exceeds it → the
exe patch fixes the data, but the screen still stops at 12 slots → the GFX
patch reveals all 24 slots.
