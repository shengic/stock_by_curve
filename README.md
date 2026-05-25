# Stock by Curve

Capture Finviz stock curve screenshots from a ticker list and export them into a single local PDF.

## Features

- Read ticker symbols from a text file (one ticker per line).
- Select specific tickers in the Tkinter UI.
- Capture charts sequentially from Finviz with selectable time ranges.
- Export captured charts into one multi-page PDF.
- Show progress, logs, and a scrollable progress message panel.
- Use 300 DPI capture scale with default viewport `1280 x 1500` CSS px.

## Project Files

- `capture.py`: Core Playwright capture logic and CLI entry.
- `tk_app.py`: Tkinter desktop UI.
- `app.py`: Streamlit prototype (reference).
- `stock.txt`: Default ticker list.

## Requirements

- Python 3
- `playwright`
- `tkinter`
- `streamlit` (only if using `app.py`)

## Setup

```bash
pip install playwright
python -m playwright install chromium
```

## Usage

### Desktop UI

```bash
python tk_app.py
```

- Select a ticker txt file or use default `stock.txt`.
- Select output folder or use default `stock_image_pdf`.
- Choose time interval.
- Check tickers and click `Start Capture`.

### Command Line

```bash
python -u capture.py
```

- Reads `stock.txt` from project folder by default.
- Uses default range `y1` unless `--range` is provided.
- Outputs to default PDF path unless `--output-pdf-path` is provided.

## Time Ranges

- `1 Month` (`m1`)
- `3 Months` (`m3`)
- `6 Months` (`m6`)
- `1 Year` (`y1`)
- `Year to Date` (`ytd`)
- `2 Years` (`y2`)
- `5 Years` (`y5`)

## Default Output

- Folder: `stock_image_pdf`
- Filename pattern: `stock_capture_YYYYMMDD_HHMM.pdf`

## Changelog

### v5.1

- TW capture routing is now automatic between `TWSE` and `TPEX`.
- Added exchange fallback logic for TW symbols in `capture_tw.py`.
- Added known override for `00937B` to prefer `TPEX`.
- TW capture now validates resolved TradingView symbol/exchange before screenshot.

### v3.0

- Updated Tk app version to `3.0`.
- Default paths now auto-resolve from the folder containing `tk_app.py`.
- Default PDF folder changed to `stock_image_pdf`.
- PDF filename field now shows full path and filename.
- Added scrollable progress message panel near progress bar.
- Progress panel keeps only latest 200 lines.
- On save, existing target PDF is deleted before replacing.
- If Save As is cancelled, temporary PDF is deleted automatically.
- Increased default Tk window height so bottom progress messages are visible at startup.
