import unittest

from capture_tw import (
    DEFAULT_RANGE_CODE,
    TIME_RANGES,
    build_tradingview_url,
    get_filename_from_url,
    parse_tickers,
)


class TestCaptureTW(unittest.TestCase):
    def test_default_timeframe_is_12m(self):
        self.assertEqual(DEFAULT_RANGE_CODE, "12M")

    def test_timeframe_mapping_contains_required_items(self):
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
        self.assertEqual(TIME_RANGES, expected)

    def test_build_tradingview_url_uses_twse_and_timeframe(self):
        self.assertEqual(
            build_tradingview_url("2330", "12M"),
            "https://www.tradingview.com/symbols/TWSE-2330/?timeframe=12M",
        )

    def test_parse_tickers_normalizes_and_deduplicates(self):
        text = "2330\n2317\n2330\n 0050 \n\n"
        self.assertEqual(parse_tickers(text), ["2330", "2317", "0050"])

    def test_get_filename_from_tradingview_url(self):
        url = "https://www.tradingview.com/symbols/TWSE-2330/?timeframe=YTD"
        self.assertEqual(get_filename_from_url(url), "2330_YTD.jpg")


if __name__ == "__main__":
    unittest.main()
