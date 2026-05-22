from capture_tw import (
    DEFAULT_RANGE_CODE,
    TIME_RANGES,
    build_tradingview_url,
    get_filename_from_url,
    parse_tickers,
)


def test_default_timeframe_is_12m():
    assert DEFAULT_RANGE_CODE == "12M"


def test_timeframe_mapping_contains_required_items():
    expected = {
        "5D": "5D",
        "1M": "1M",
        "6M": "6M",
        "12M": "12M",
        "YTD": "YTD",
        "60M": "60M",
        "120M": "120M",
        "ALL": "ALL",
    }
    assert TIME_RANGES == expected


def test_build_tradingview_url_uses_twse_and_timeframe():
    assert (
        build_tradingview_url("2330", "12M")
        == "https://www.tradingview.com/symbols/TWSE-2330/?timeframe=12M"
    )


def test_parse_tickers_normalizes_and_deduplicates():
    text = "2330\n2317\n2330\n 0050 \n\n"
    assert parse_tickers(text) == ["2330", "2317", "0050"]


def test_get_filename_from_tradingview_url():
    url = "https://www.tradingview.com/symbols/TWSE-2330/?timeframe=YTD"
    assert get_filename_from_url(url) == "2330_YTD.jpg"
