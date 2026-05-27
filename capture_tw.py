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
TRADINGVIEW_URL_TEMPLATE = "https://www.tradingview.com/symbols/{exchange}-{ticker}/?timeframe={interval}"
EXCHANGES = ("TWSE", "TPEX")
# Known exchange overrides when TradingView does not follow the common default.
SYMBOL_EXCHANGE_OVERRIDES = {
    "00937B": "TPEX",
}

# New localized timeframe mapping
# These keys will be used in the UI
TIME_RANGES = {
    "日線": "DAILY",
    "周線": "WEEKLY",
    "月線": "MONTHLY",
    "季線": "QUARTERLY",
    "年線": "YEARLY",
}
DEFAULT_RANGE_CODE = "YEARLY"

# Source-specific configurations
SOURCE_CONFIG = {
    "GOODINFO": {
        "height": 4500,
        "render_wait": 5,
        "intervals": {
            "DAILY": "DATE",
            "WEEKLY": "WEEK",
            "MONTHLY": "MONTH",
            "QUARTERLY": "QUAR",
            "YEARLY": "YEAR",
        }
    },
    "TRADINGVIEW": {
        "height": 1500,
        "render_wait": 5,
        "intervals": {
            "DAILY": "1D",
            "WEEKLY": "1W",
            "MONTHLY": "1M",
            "QUARTERLY": "3M",
            "YEARLY": "12M",
        }
    },
    "YAHOO": {
        "height": 1500,
        "render_wait": 5,
        "intervals": {
            "DAILY": "1d",
            "WEEKLY": "1wk",
            "MONTHLY": "1mo",
            "QUARTERLY": "3mo",
            "YEARLY": "1y",
        }
    }
}

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


def build_goodinfo_url(ticker, interval_key="DAILY"):
    """Build Goodinfo.tw URL."""
    cat = SOURCE_CONFIG["GOODINFO"]["intervals"].get(interval_key, "DATE")
    return f"https://goodinfo.tw/tw/ShowK_Chart.asp?CHT_CAT={cat}&PRICE_ADJ=F&STOCK_ID={ticker}"


def build_tradingview_urls(ticker, interval_key="DAILY"):
    """Build candidate TradingView URLs with exchange fallbacks."""
    ticker = ticker.upper()
    interval = SOURCE_CONFIG["TRADINGVIEW"]["intervals"].get(interval_key, "1D")
    preferred = SYMBOL_EXCHANGE_OVERRIDES.get(ticker, "TWSE")
    exchanges = [preferred] + [exchange for exchange in EXCHANGES if exchange != preferred]
    return [
        f"https://www.tradingview.com/symbols/{ex.upper()}-{ticker}/?timeframe={interval}"
        for ex in exchanges
    ]


def build_yahoo_urls(ticker, interval_key="DAILY"):
    """Build Yahoo Finance candidate URLs (TW for TWSE, TWO for TPEX)."""
    # In practice Yahoo often uses .TW for TWSE and .TWO for TPEX
    return [
        f"https://finance.yahoo.com/quote/{ticker}.TW/chart",
        f"https://finance.yahoo.com/quote/{ticker}.TWO/chart"
    ]


def extract_symbol_exchange(url):
    """Extract exchange and ticker from a TradingView symbol URL path."""
    try:
        parsed = urlparse(url)
        path_parts = [part for part in parsed.path.split("/") if part]
        if len(path_parts) < 2 or path_parts[0].lower() != "symbols":
            return None, None
        symbol = path_parts[1].upper()
        if "-" not in symbol:
            return None, None
        exchange, ticker = symbol.split("-", 1)
        if exchange in EXCHANGES and ticker:
            return exchange, ticker
    except Exception:
        pass
    return None, None


def get_filename_from_url(url, suffix=".jpg"):
    """Build a filename from TradingView symbol URL when available."""
    try:
        parsed_url = urlparse(url)
        params = parse_qs(parsed_url.query)
        path_parts = [part for part in parsed_url.path.split("/") if part]
        symbol_part = path_parts[1] if len(path_parts) > 1 and path_parts[0] == "symbols" else None
        ticker = None
        if symbol_part and "-" in symbol_part:
            exchange, symbol_ticker = symbol_part.split("-", 1)
            if exchange.upper() in EXCHANGES:
                ticker = symbol_ticker.upper()
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


USER_AGENTS = [
    # Chrome on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    # Chrome on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    # Safari on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2.1 Safari/605.1.15",
    # Safari on iPhone
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
    # Firefox on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    # Edge on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
]

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
    total_tickers = len(tickers)
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
    print(f"Timeframe: {range_code}", flush=True)

    failures = []
    captured_pages = []

    def render_pdf_pages(image_bytes, ticker):
        """Split a long image into multiple segments and return as a list of PDF pages."""
        image = Image.open(BytesIO(image_bytes)).convert("RGB")
        img_w, img_h = image.size
        
        # Increased segment height to 4500 for fewer partitions (usually 1 page per capture)
        max_seg_h = 4500 
        
        pages = []
        for i in range(0, img_h, max_seg_h):
            seg_h = min(max_seg_h, img_h - i)
            segment = image.crop((0, i, img_w, i + seg_h))
            
            page_w = img_w + PDF_PAGE_MARGIN * 2
            page_h = seg_h + PDF_TITLE_HEIGHT + PDF_PAGE_MARGIN * 2
            page = Image.new("RGB", (page_w, page_h), "white")
            page.paste(segment, (PDF_PAGE_MARGIN, PDF_TITLE_HEIGHT + PDF_PAGE_MARGIN))
            
            draw = ImageDraw.Draw(page)
            font = ImageFont.load_default()
            label = f"{ticker} [Part {len(pages)+1}]" if img_h > max_seg_h else ticker
            draw.text((PDF_PAGE_MARGIN, PDF_PAGE_MARGIN), str(label), fill="black", font=font)
            pages.append(page)
        return pages

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        
        try:
            for index, ticker in enumerate(tickers, start=1):
                # Randomize User-Agent and Context for each ticker to look like different users
                user_agent = random.choice(USER_AGENTS)
                context = await browser.new_context(
                    user_agent=user_agent,
                    viewport={"width": int(width), "height": int(height)},
                    device_scale_factor=DEVICE_SCALE_FACTOR,
                    extra_http_headers={
                        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
                    },
                )
                page = await context.new_page()

                # Map UI label (e.g., "年線") back to internal key (e.g., "YEARLY")
                if range_code in TIME_RANGES.values():
                    interval_key = range_code
                else:
                    interval_key = TIME_RANGES.get(range_code, "YEARLY")

                if index > 1:
                    delay = random.uniform(MIN_CAPTURE_DELAY_SECONDS, MAX_CAPTURE_DELAY_SECONDS)
                    print(f"Waiting {delay:.1f}s before next capture...", flush=True)
                    await asyncio.sleep(delay)

                progress = format_progress(index, total_tickers)
                print(f"{progress} [{index}/{total_tickers}] Capturing {ticker}...", flush=True)

                success, error = False, None
                
                # 1. TradingView (NOW FIRST)
                try:
                    await context.set_extra_http_headers({"Referer": "https://www.google.com/"})
                    tv_urls = build_tradingview_urls(ticker, interval_key)
                    tv_height = SOURCE_CONFIG["TRADINGVIEW"]["height"]
                    for tv_url in tv_urls:
                        try:
                            print(f"[{ticker}] Trying TradingView: {tv_url}", flush=True)
                            await page.set_viewport_size({"width": int(width), "height": int(tv_height)})
                            await page.goto(tv_url, wait_until="domcontentloaded", timeout=60000)
                            await asyncio.sleep(SOURCE_CONFIG["TRADINGVIEW"]["render_wait"])
                            resolved_exchange, resolved_ticker = extract_symbol_exchange(page.url)
                            if resolved_ticker == ticker:
                                image_bytes = await page.screenshot(full_page=False, type="png")
                                pdf_pages = render_pdf_pages(image_bytes, f"{ticker} (TradingView)")
                                captured_pages.extend(pdf_pages)
                                success = True
                                print(f"[{ticker}] TradingView capture succeeded", flush=True)
                                break
                        except Exception as exc:
                            error = str(exc)
                except Exception as exc:
                    error = str(exc)

                # 2. Goodinfo Fallback (NOW SECOND)
                if not success:
                    try:
                        g_url = build_goodinfo_url(ticker, interval_key)
                        g_height = SOURCE_CONFIG["GOODINFO"]["height"]
                        print(f"[{ticker}] Trying Goodinfo fallback: {g_url}", flush=True)
                        await page.set_viewport_size({"width": int(width), "height": int(g_height)})
                        await context.set_extra_http_headers({
                            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
                            "Referer": "https://goodinfo.tw/tw/index.asp",
                        })
                        await page.goto(g_url, wait_until="load", timeout=60000)
                        
                        # Trigger scrolling
                        await page.evaluate("window.scrollTo(0, 500)")
                        await asyncio.sleep(1)
                        await page.evaluate(f"window.scrollTo(0, {int(g_height) // 2})")
                        await asyncio.sleep(1)
                        await page.evaluate("window.scrollTo(0, 0)")
                        
                        try:
                            await page.wait_for_selector("#divK_Chart_Detail", timeout=5000)
                        except:
                            pass

                        await asyncio.sleep(SOURCE_CONFIG["GOODINFO"]["render_wait"])
                        content = await page.content()
                        if "查無資料" in content:
                            raise RuntimeError("Goodinfo: No data")

                        image_bytes = await page.screenshot(full_page=False, type="png")
                        pdf_pages = render_pdf_pages(image_bytes, f"{ticker} (Goodinfo)")
                        captured_pages.extend(pdf_pages)
                        success = True
                        print(f"[{ticker}] Goodinfo capture succeeded", flush=True)
                    except Exception as exc:
                        print(f"[{ticker}] Goodinfo fallback failed: {exc}", flush=True)
                        error = str(exc)

                # 3. Yahoo Fallback
                if not success:
                    y_urls = build_yahoo_urls(ticker, interval_key)
                    y_height = SOURCE_CONFIG["YAHOO"]["height"]
                    for y_url in y_urls:
                        try:
                            print(f"[{ticker}] Trying Yahoo fallback: {y_url}", flush=True)
                            await page.set_viewport_size({"width": int(width), "height": int(y_height)})
                            await page.goto(y_url, wait_until="domcontentloaded", timeout=60000)
                            await asyncio.sleep(SOURCE_CONFIG["YAHOO"]["render_wait"])
                            image_bytes = await page.screenshot(full_page=False, type="png")
                            pdf_pages = render_pdf_pages(image_bytes, f"{ticker} (Yahoo)")
                            captured_pages.extend(pdf_pages)
                            success = True
                            print(f"[{ticker}] Yahoo capture succeeded", flush=True)
                            break
                        except Exception as exc:
                            error = str(exc)

                if success:
                    if progress_callback:
                        progress_callback({"event": "success", "index": index, "total": total_tickers, "ticker": ticker, "output_path": str(output_pdf_path)})
                else:
                    failures.append((ticker, error))
                    if progress_callback:
                        progress_callback({"event": "failure", "index": index, "total": total_tickers, "ticker": ticker, "error": error, "output_path": str(output_pdf_path)})
                
                # Close context per ticker to ensure fresh identity
                await context.close()
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
