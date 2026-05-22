import argparse
import asyncio
import datetime
import random
import sys
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright
from PIL import Image, ImageDraw, ImageFont


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_STOCK_FILE = BASE_DIR / "tw_stock.txt"
DEFAULT_OUTPUT_DIR = BASE_DIR / "stock_images"
DEFAULT_OUTPUT_PDF_DIR = BASE_DIR / "tw_stock_image_pdf"
DEFAULT_OUTPUT_PDF_PATH = DEFAULT_OUTPUT_PDF_DIR / "tw_stock_capture.pdf"
TRADINGVIEW_URL_TEMPLATE = "https://www.tradingview.com/symbols/TWSE-{ticker}/?timeframe={interval}"
# TradingView timeframe mapping (TW):
# UI label -> URL parameter
TIME_RANGES = {
    "5D": "5D",
    "1M": "1M",
    "6M": "6M",
    "12M": "12M",
    "YTD": "YTD",
    "60M": "60M",
    "120M": "120M",
    "ALL": "ALL",
}
DEFAULT_RANGE_CODE = "12M"

VIEWPORT_WIDTH = 1280
DEFAULT_VIEWPORT_HEIGHT = 1500
CSS_DPI = 96
TARGET_DPI = 300
DEVICE_SCALE_FACTOR = TARGET_DPI / CSS_DPI
MIN_CAPTURE_DELAY_SECONDS = 3
MAX_CAPTURE_DELAY_SECONDS = 8
PAGE_RENDER_WAIT_SECONDS = 5
PROGRESS_BAR_WIDTH = 24
PDF_PAGE_MARGIN = 32
PDF_TITLE_HEIGHT = 48


def read_tickers(stock_file):
    """Read one ticker per line, ignoring empty lines and duplicate symbols."""
    return parse_tickers(stock_file.read_text(encoding="utf-8-sig"))


def parse_tickers(text):
    """Parse Taiwan ticker symbols from text, one symbol per line."""
    tickers = []
    seen = set()

    for line in text.splitlines():
        ticker = line.strip().upper()
        if not ticker or ticker in seen:
            continue
        tickers.append(ticker)
        seen.add(ticker)

    return tickers


def build_tradingview_url(ticker, interval=DEFAULT_RANGE_CODE):
    """Build the TradingView TWSE symbol URL for one ticker and timeframe."""
    return TRADINGVIEW_URL_TEMPLATE.format(ticker=ticker.upper(), interval=interval)


def get_filename_from_url(url, suffix=".jpg"):
    """Build a filename from TradingView TWSE URL when available."""
    try:
        parsed_url = urlparse(url)
        params = parse_qs(parsed_url.query)
        path_parts = [part for part in parsed_url.path.split("/") if part]
        symbol_part = path_parts[1] if len(path_parts) > 1 and path_parts[0] == "symbols" else None
        ticker = symbol_part.replace("TWSE-", "").upper() if symbol_part and symbol_part.startswith("TWSE-") else None
        period = params.get("timeframe", [None])[0]

        if ticker and period:
            return f"{ticker.upper()}_{period}{suffix}"
        if ticker:
            return f"{ticker.upper()}{suffix}"
    except Exception:
        pass

    return None


def format_progress(current, total):
    """Return a compact text progress bar."""
    filled = round(PROGRESS_BAR_WIDTH * current / total)
    bar = "#" * filled + "-" * (PROGRESS_BAR_WIDTH - filled)
    percent = current * 100 / total
    return f"[{bar}] {percent:5.1f}%"


async def capture_screenshot(
    page,
    url,
    output_path,
    selector=None,
    height=DEFAULT_VIEWPORT_HEIGHT,
    width=VIEWPORT_WIDTH,
    render_wait_seconds=PAGE_RENDER_WAIT_SECONDS,
):
    """Capture a page or selector screenshot and return (success, error_message)."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Opening URL: {url}", flush=True)

    try:
        await page.set_viewport_size({"width": int(width), "height": int(height)})
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)

        # Finviz chart and quote data can render after DOMContentLoaded.
        # A fixed render wait is more predictable than networkidle for pages
        # that keep background requests open.
        print("Waiting for page content to render...", flush=True)
        await asyncio.sleep(float(render_wait_seconds))

        if selector:
            print(f"Capturing selector: {selector}", flush=True)
            element = await page.query_selector(selector)
            if not element:
                return False, f"Selector not found: {selector}"

            await element.screenshot(path=str(output_path))
        else:
            print(
                "Capturing viewport: "
                f"{int(width)}x{int(height)} CSS px, "
                f"{TARGET_DPI} DPI scale ({DEVICE_SCALE_FACTOR:.3f}x)",
                flush=True,
            )
            await page.screenshot(path=str(output_path), full_page=False, type="jpeg", quality=95)

        print(f"Saved image: {output_path}", flush=True)
        return True, None
    except Exception as exc:
        return False, str(exc)


async def capture_ticker_list(
    tickers,
    output_pdf_path,
    range_code=DEFAULT_RANGE_CODE,
    height=DEFAULT_VIEWPORT_HEIGHT,
    width=VIEWPORT_WIDTH,
    render_wait_seconds=PAGE_RENDER_WAIT_SECONDS,
    selector=None,
    progress_callback=None,
):
    """Sequentially capture TradingView TW stock images and save into one multi-page PDF."""
    output_pdf_path = Path(output_pdf_path)

    if not tickers:
        print("No tickers to capture.", flush=True)
        return {"failures": [], "pdf_path": str(output_pdf_path), "saved_pages": 0}

    output_pdf_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Output PDF: {output_pdf_path}", flush=True)
    print(
        f"Capture resolution: {TARGET_DPI} DPI scale, viewport {int(width)}x{int(height)} CSS px",
        flush=True,
    )
    print(f"TradingView timeframe: {range_code}", flush=True)
    print(f"Render wait seconds: {float(render_wait_seconds):.1f}", flush=True)

    failures = []
    captured_pages = []

    def render_pdf_page(image_bytes, ticker):
        image = Image.open(BytesIO(image_bytes)).convert("RGB")
        page_width = image.width + PDF_PAGE_MARGIN * 2
        page_height = image.height + PDF_TITLE_HEIGHT + PDF_PAGE_MARGIN * 2
        page = Image.new("RGB", (page_width, page_height), "white")
        page.paste(image, (PDF_PAGE_MARGIN, PDF_TITLE_HEIGHT + PDF_PAGE_MARGIN))

        draw = ImageDraw.Draw(page)
        font = ImageFont.load_default()
        draw.text((PDF_PAGE_MARGIN, PDF_PAGE_MARGIN), str(ticker), fill="black", font=font)
        return page

    async with async_playwright() as p:
        user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/121.0.0.0 Safari/537.36"
        )
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=user_agent,
            viewport={"width": int(width), "height": int(height)},
            device_scale_factor=DEVICE_SCALE_FACTOR,
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9,zh-TW;q=0.8,zh;q=0.7",
                "Referer": "https://www.google.com/",
            },
        )
        page = await context.new_page()

        try:
            for index, ticker in enumerate(tickers, start=1):
                url = build_tradingview_url(ticker, range_code)

                if index > 1:
                    delay = random.uniform(MIN_CAPTURE_DELAY_SECONDS, MAX_CAPTURE_DELAY_SECONDS)
                    print(f"Waiting {delay:.1f}s before next capture...", flush=True)
                    await asyncio.sleep(delay)

                progress = format_progress(index, len(tickers))
                print(f"{progress} [{index}/{len(tickers)}] Capturing {ticker}...", flush=True)
                if progress_callback:
                    progress_callback(
                        {
                            "event": "start",
                            "index": index,
                            "total": len(tickers),
                            "ticker": ticker,
                            "url": url,
                            "output_path": str(output_pdf_path),
                            "width": int(width),
                            "height": int(height),
                            "render_wait_seconds": float(render_wait_seconds),
                        }
                    )

                try:
                    await page.set_viewport_size({"width": int(width), "height": int(height)})
                    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    print("Waiting for page content to render...", flush=True)
                    await asyncio.sleep(float(render_wait_seconds))

                    if selector:
                        element = await page.query_selector(selector)
                        if not element:
                            raise RuntimeError(f"Selector not found: {selector}")
                        image_bytes = await element.screenshot(type="png")
                    else:
                        image_bytes = await page.screenshot(full_page=False, type="png")
                    pdf_page = render_pdf_page(image_bytes, ticker)
                    captured_pages.append(pdf_page)
                    success, error = True, None
                except Exception as exc:
                    success, error = False, str(exc)
                if success:
                    print(f"[{ticker}] Capture succeeded", flush=True)
                    if progress_callback:
                        progress_callback(
                            {
                                "event": "success",
                                "index": index,
                                "total": len(tickers),
                                "ticker": ticker,
                                "output_path": str(output_pdf_path),
                            }
                        )
                else:
                    print(f"[{ticker}] Capture failed: {error}", flush=True)
                    failures.append((ticker, error))
                    if progress_callback:
                        progress_callback(
                            {
                                "event": "failure",
                                "index": index,
                                "total": len(tickers),
                                "ticker": ticker,
                                "error": error,
                                "output_path": str(output_pdf_path),
                            }
                        )
        finally:
            await browser.close()

    if captured_pages:
        first_page, rest_pages = captured_pages[0], captured_pages[1:]
        first_page.save(str(output_pdf_path), save_all=True, append_images=rest_pages)
        print(f"Saved PDF: {output_pdf_path}", flush=True)

    print("", flush=True)
    print("Capture summary", flush=True)
    print(f"Success: {len(tickers) - len(failures)}", flush=True)
    print(f"Failed: {len(failures)}", flush=True)

    if failures:
        print("Failed tickers:", flush=True)
        for ticker, error in failures:
            print(f"- {ticker}: {error}", flush=True)
        return {
            "failures": failures,
            "pdf_path": str(output_pdf_path),
            "saved_pages": len(captured_pages),
        }

    print("All stock captures completed successfully.", flush=True)
    return {
        "failures": failures,
        "pdf_path": str(output_pdf_path),
        "saved_pages": len(captured_pages),
    }


async def capture_stocks(
    stock_file,
    output_pdf_path,
    range_code=DEFAULT_RANGE_CODE,
    height=DEFAULT_VIEWPORT_HEIGHT,
    width=VIEWPORT_WIDTH,
    render_wait_seconds=PAGE_RENDER_WAIT_SECONDS,
    selector=None,
):
    """Sequentially capture TradingView TW stock images listed in stock_file to one PDF."""
    stock_file = Path(stock_file)

    if not stock_file.exists():
        print(f"Stock file not found: {stock_file}", flush=True)
        return 1

    tickers = read_tickers(stock_file)
    if not tickers:
        print(f"No tickers found in: {stock_file}", flush=True)
        return 1

    print(f"Loaded {len(tickers)} ticker(s) from {stock_file}", flush=True)
    failures = await capture_ticker_list(
        tickers,
        output_pdf_path,
        range_code=range_code,
        height=height,
        width=width,
        render_wait_seconds=render_wait_seconds,
        selector=selector,
    )
    return 1 if failures["failures"] else 0


async def capture_single_url(
    url,
    output_path=None,
    selector=None,
    height=DEFAULT_VIEWPORT_HEIGHT,
    width=VIEWPORT_WIDTH,
    render_wait_seconds=PAGE_RENDER_WAIT_SECONDS,
):
    """Capture one URL for ad-hoc use."""
    if not url.startswith("http"):
        url = "https://" + url

    if output_path:
        output_path = Path(output_path)
    else:
        filename = get_filename_from_url(url)
        if not filename:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"screenshot_{timestamp}.jpg"
        output_path = BASE_DIR / filename

    async with async_playwright() as p:
        user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/121.0.0.0 Safari/537.36"
        )
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=user_agent,
            viewport={"width": int(width), "height": int(height)},
            device_scale_factor=DEVICE_SCALE_FACTOR,
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9,zh-TW;q=0.8,zh;q=0.7",
                "Referer": "https://www.google.com/",
            },
        )
        page = await context.new_page()

        try:
            success, error = await capture_screenshot(
                page,
                url,
                output_path,
                selector,
                height,
                width,
                render_wait_seconds,
            )
            if not success:
                print(f"Capture failed: {error}", flush=True)
                return 1
            return 0
        finally:
            await browser.close()


def main():
    parser = argparse.ArgumentParser(description="Capture TradingView Taiwan stock curve images.")
    parser.add_argument(
        "url",
        nargs="?",
        help="Optional single URL to capture. If omitted, stock.txt batch mode is used.",
    )
    parser.add_argument("-o", "--output", help="Output file path for single URL mode.")
    parser.add_argument("-s", "--selector", help="Optional HTML selector to capture.")
    parser.add_argument(
        "-height",
        "--height",
        type=int,
        default=DEFAULT_VIEWPORT_HEIGHT,
        help=f"Viewport height in CSS pixels. Default: {DEFAULT_VIEWPORT_HEIGHT}.",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=VIEWPORT_WIDTH,
        help=f"Viewport width in CSS pixels. Default: {VIEWPORT_WIDTH}.",
    )
    parser.add_argument(
        "--render-wait-seconds",
        type=float,
        default=PAGE_RENDER_WAIT_SECONDS,
        help=f"Render wait before screenshot. Default: {PAGE_RENDER_WAIT_SECONDS}.",
    )
    parser.add_argument(
        "--stock-file",
        default=str(DEFAULT_STOCK_FILE),
        help=f"Ticker list file for batch mode. Default: {DEFAULT_STOCK_FILE}.",
    )
    parser.add_argument(
        "--output-pdf-path",
        default=str(DEFAULT_OUTPUT_PDF_PATH),
        help=f"Output PDF path for batch mode. Default: {DEFAULT_OUTPUT_PDF_PATH}.",
    )
    parser.add_argument(
        "--range",
        choices=sorted(set(TIME_RANGES.values())),
        default=DEFAULT_RANGE_CODE,
        help=f"TradingView timeframe for batch mode. Default: {DEFAULT_RANGE_CODE}.",
    )

    args = parser.parse_args()

    if args.url:
        exit_code = asyncio.run(
            capture_single_url(
                args.url,
                args.output,
                args.selector,
                args.height,
                args.width,
                args.render_wait_seconds,
            )
        )
    else:
        exit_code = asyncio.run(
            capture_stocks(
                args.stock_file,
                args.output_pdf_path,
                args.range,
                args.height,
                args.width,
                args.render_wait_seconds,
                args.selector,
            )
        )

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
