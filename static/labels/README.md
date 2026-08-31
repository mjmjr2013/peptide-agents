# Sticker artwork, one file per SKU

The manifest emailed to Jason embeds these images so the crew can see the sticker
next to the row they are labelling (HANDOFF §30c).

- **Filename must be `<SKU>.png` (or .jpg)** and the SKU must be registered in
  `core.catalog.SKU_LABEL_FILE`. `catalog.label_path()` is the only lookup.
- **A SKU with no file here is NOT a blank cell.** The manifest prints
  "⚠ NO STICKER ON FILE — do not label, ask Jordan" in red on that row. That is
  deliberate: at a bench, a blank reads as "no sticker needed".
- **Never point a SKU at approximate artwork.** Strength mix-ups (10mg vs 100mg)
  are the failure this manifest exists to prevent, so a wrong-but-plausible
  picture is worse than none at all.
- `catalog.labels_missing()` lists every SKU still without one.

Source: the shared Google Photos album of current label artwork. These are
Northline's own stickers — the ones in Daniel's example workbook were a formatting
reference only and are NOT ours.
