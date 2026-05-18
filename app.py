import asyncio
from pathlib import Path

import streamlit as st

from capture import (
    DEFAULT_OUTPUT_DIR,
    DEFAULT_RANGE_CODE,
    DEFAULT_STOCK_FILE,
    DEFAULT_VIEWPORT_HEIGHT,
    DEVICE_SCALE_FACTOR,
    TARGET_DPI,
    TIME_RANGES,
    VIEWPORT_WIDTH,
    build_finviz_url,
    capture_ticker_list,
    parse_tickers,
    read_tickers,
)


st.set_page_config(page_title="Stock Curve Capture", layout="wide")


def load_tickers_from_ui(uploaded_file, stock_file_path):
    """Load tickers either from an uploaded txt file or a local file path."""
    if uploaded_file is not None:
        text = uploaded_file.getvalue().decode("utf-8-sig")
        return parse_tickers(text), uploaded_file.name

    path = Path(stock_file_path)
    if not path.exists():
        return [], str(path)

    return read_tickers(path), str(path)


def run_capture(tickers, output_dir, range_code, height):
    """Run async Playwright capture from Streamlit and update UI progress."""
    progress_bar = st.progress(0)
    status_text = st.empty()
    log_box = st.empty()
    logs = []
    results = []

    def add_log(message):
        logs.append(message)
        log_box.code("\n".join(logs[-80:]))

    def on_progress(event):
        index = event["index"]
        total = event["total"]
        ticker = event["ticker"]
        progress_bar.progress(index / total)

        if event["event"] == "start":
            status_text.info(f"[{index}/{total}] Capturing {ticker}")
            add_log(f"[{index}/{total}] Capturing {ticker}: {event['url']}")
        elif event["event"] == "success":
            status_text.success(f"[{index}/{total}] {ticker} saved")
            results.append(
                {
                    "Ticker": ticker,
                    "Status": "Success",
                    "Output": event["output_path"],
                    "Error": "",
                }
            )
            add_log(f"[{ticker}] Success: {event['output_path']}")
        elif event["event"] == "failure":
            status_text.error(f"[{index}/{total}] {ticker} failed")
            results.append(
                {
                    "Ticker": ticker,
                    "Status": "Failed",
                    "Output": event.get("output_path", ""),
                    "Error": event.get("error", ""),
                }
            )
            add_log(f"[{ticker}] Failed: {event.get('error', '')}")

    failures = asyncio.run(
        capture_ticker_list(
            tickers,
            output_dir,
            range_code=range_code,
            height=height,
            progress_callback=on_progress,
        )
    )

    progress_bar.progress(1.0)
    return failures, results


st.title("Stock Curve Capture")

with st.sidebar:
    st.header("Capture Settings")

    uploaded_file = st.file_uploader("Ticker txt file", type=["txt"])
    stock_file_path = st.text_input("Local ticker file path", value=str(DEFAULT_STOCK_FILE))
    output_dir = st.text_input("Output folder", value=str(DEFAULT_OUTPUT_DIR))

    selected_range_label = st.selectbox(
        "Time interval",
        options=list(TIME_RANGES.keys()),
        index=list(TIME_RANGES.values()).index(DEFAULT_RANGE_CODE),
    )
    range_code = TIME_RANGES[selected_range_label]

    height = st.number_input(
        "Capture height (CSS px)",
        min_value=600,
        max_value=4000,
        value=DEFAULT_VIEWPORT_HEIGHT,
        step=100,
    )

    st.caption(
        f"Capture region: {VIEWPORT_WIDTH} x {height} CSS px. "
        f"{TARGET_DPI} DPI scale ({DEVICE_SCALE_FACTOR:.3f}x)."
    )

tickers, ticker_source = load_tickers_from_ui(uploaded_file, stock_file_path)

left, right = st.columns([1, 1])

with left:
    st.subheader("Tickers")
    st.write(f"Source: `{ticker_source}`")
    if tickers:
        st.write(f"Loaded `{len(tickers)}` ticker(s).")
        st.dataframe({"Ticker": tickers}, use_container_width=True, hide_index=True)
    else:
        st.warning("No ticker loaded. Upload a txt file or enter a valid local txt path.")

with right:
    st.subheader("URL Preview")
    preview_ticker = tickers[0] if tickers else "AAPL"
    st.code(build_finviz_url(preview_ticker, range_code))
    st.write(f"Selected interval: `{selected_range_label}` (`r={range_code}`)")
    st.write(f"Output folder: `{output_dir}`")

st.divider()

can_run = bool(tickers) and bool(output_dir.strip())
if st.button("Start Capture", type="primary", disabled=not can_run):
    st.subheader("Progress")
    failures, results = run_capture(tickers, output_dir.strip(), range_code, int(height))

    st.subheader("Summary")
    success_count = len(tickers) - len(failures)
    st.write(f"Success: `{success_count}`")
    st.write(f"Failed: `{len(failures)}`")

    if results:
        st.dataframe(results, use_container_width=True, hide_index=True)

    if failures:
        st.error("Some captures failed.")
    else:
        st.success("All captures completed successfully.")
