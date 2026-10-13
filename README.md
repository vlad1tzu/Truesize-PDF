# TrueSize PDF

View PDFs on your screen at their **exact real-world printed size** and print any part of a drawing at any scale. Made for architecture students and CAD users.

Hold a printed sheet or a ruler up to the screen and it matches 1:1.

## Features

- **True 1:1 display.** The physical screen size is read automatically from the monitor (EDID). Windows display scaling is ignored, so 1 mm on screen is 1 mm.
- **Manual screen size.** You can enter the diagonal or the exact width × height for monitors that don't report their size.
- **Fine calibration.** Line up the screen with a real ruler or a bank card (85.60 × 53.98 mm).
- **Print & Scale:**
  - Paper sizes A5–A0, Letter, Legal, Tabloid or custom, in portrait or landscape, with a printer margin.
  - Scale in % or as an architectural scale (e.g. **1:100 → 1:50**).
  - **Choose which part gets printed.** Drag the sheet over the drawing or click a corner or side (top-left, top-right, …). Other programs only print the centre when you scale up.
  - The screen shows the printed result at real size before you print.
  - **Poster mode** tiles the whole drawing over several sheets, with overlap and glue marks.
  - Exports an exact-scale PDF, or opens it ready to print.
- Mm ruler overlay, overview mode, multi-monitor support, and paging through multi-page PDFs.

## Install & run

1. Install [Python 3](https://www.python.org/downloads/) and tick **"Add Python to PATH"**.
2. Double-click `run.bat`. It installs PyMuPDF on first run.
3. Double click `build_exe.bat`. To get a standalone `.exe`. The result is `dist\TrueSizePDF.exe`.

You can also run build_exe.bat from a command prompt:

```
pip install -r requirements.txt
python TrueSizePDF.py [file.pdf]
```

## Keys

| Key | Action |
|---|---|
| O | Open PDF |
| P | Print & Scale panel |
| Arrows (Shift = faster, Ctrl = to edge) | Move around |
| Mouse drag / wheel | Move around (in print mode, left-drag moves the sheet) |
| PgUp / PgDn, Home / End | Pages |
| Z | Overview ↔ real size |
| R / H | Ruler / info box |
| S / C | Settings / calibration |
| M | Next monitor |
| F1 | Help |
| Esc | Close panel / quit |

## Printing tip

When printing the exported PDF, always choose **"Actual size" / 100%**, never "Fit to page". The file is already scaled and positioned.

## Requirements

Windows 10/11, Python 3.8+, [PyMuPDF](https://pymupdf.readthedocs.io/). PyMuPDF is licensed under AGPL-3.0, so choose your own license with that in mind.
