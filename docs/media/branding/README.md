# BreadBoard Branding Assets (Canonical)

This directory holds canonical branding assets for BreadBoard.

## Primary Logo (v1)

- File: `breadboard_ascii_logo_v1.svg`
- Canonical source for this version: `docs/media/branding/breadboard_ascii_logo_v1.svg`
- SHA-256: `6f09c6b9013bcf5135a8e02953f397b0682bf9d46ad3435aacf4e2f800846c85`
- README/web PNG: `breadboard_ascii_logo_v1.png`
- OpenGraph PNG: `og_banner_v1.png`
- Full asset checksums manifest: `checksums.sha256`

Notes:

- Locked v1 logo to use going forward unless explicitly superseded.
- Pixel-style main wordmark with checkerboard shading for `░` cells.
- This is the default banner/logo for docs and marketing contexts.

## Icon + Favicon Set (v1)

- `breadboard_icon_bb_v1.svg`
- `breadboard_icon_bb_v1.png` (256x256)
- `favicon.svg`
- `favicon-32.png`
- `favicon-64.png`

## Regeneration

From repo root:

```bash
python - <<'PY'
from pathlib import Path
import cairosvg
base = Path('docs/media/branding')
cairosvg.svg2png(url=str(base/'breadboard_ascii_logo_v1.svg'), write_to=str(base/'breadboard_ascii_logo_v1.png'), output_width=1100, output_height=352)
cairosvg.svg2png(url=str(base/'breadboard_ascii_logo_v1.svg'), write_to=str(base/'og_banner_v1.png'), output_width=1200, output_height=630)
cairosvg.svg2png(url=str(base/'breadboard_icon_bb_v1.svg'), write_to=str(base/'breadboard_icon_bb_v1.png'), output_width=256, output_height=256)
cairosvg.svg2png(url=str(base/'favicon.svg'), write_to=str(base/'favicon-32.png'), output_width=32, output_height=32)
cairosvg.svg2png(url=str(base/'favicon.svg'), write_to=str(base/'favicon-64.png'), output_width=64, output_height=64)
PY
```

## Versioning Policy

When replacing any asset:

1. Add a new versioned file (do not overwrite silently).
2. Update this README with fresh hashes.
3. Refresh `checksums.sha256` with current assets.
4. Document rationale in release docs/changelog.
