"""
AutoTrader — Parser Unit Tests

Tests both the regex fallback parser and the AI parser's signal validation.
Run with: python -m pytest tests/test_parser.py -v
"""

from __future__ import annotations

import pytest
from src.regex_parser import RegexParser
from src.models import ParsedSignal, TradeAction


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def parser() -> RegexParser:
    return RegexParser()


# ── Test: Valid BUY Signals ─────────────────────────────────────────────────

class TestBuySignals:
    def test_clean_buy_signal(self, parser: RegexParser) -> None:
        text = """🟢 BUY XAUUSD @ 2345.50
        🛑 SL: 2338.00
        🎯 TP1: 2352.00
        🎯 TP2: 2360.00"""

        signal = parser.parse(text)
        assert signal is not None
        assert signal.action == TradeAction.BUY
        assert signal.entry_price == 2345.50
        assert signal.stop_loss == 2338.00
        assert 2352.00 in signal.take_profits
        assert 2360.00 in signal.take_profits

    def test_minimal_buy(self, parser: RegexParser) -> None:
        text = "BUY GOLD 2345\nSL 2338\nTP 2355"
        signal = parser.parse(text)
        assert signal is not None
        assert signal.action == TradeAction.BUY
        assert signal.stop_loss == 2338.0

    def test_buy_with_entry_range(self, parser: RegexParser) -> None:
        text = """Buy XAUUSD
        Entry Zone: 2340-2345
        Stop Loss: 2332
        Take Profit 1: 2355"""

        signal = parser.parse(text)
        assert signal is not None
        assert signal.action == TradeAction.BUY
        # Should use midpoint of range
        assert 2340 <= signal.entry_price <= 2345
        assert signal.stop_loss == 2332.0

    def test_go_long(self, parser: RegexParser) -> None:
        text = """Go Long XAUUSD
        Entry: 2342
        SL: 2335
        Target 1: 2350"""

        signal = parser.parse(text)
        assert signal is not None
        assert signal.action == TradeAction.BUY
        assert signal.entry_price == 2342.0

    def test_buy_without_decimal(self, parser: RegexParser) -> None:
        text = "BUY GOLD @ 2345\nSL 2338\nTP 2355"
        signal = parser.parse(text)
        assert signal is not None
        assert signal.entry_price == 2345.0


# ── Test: Valid SELL Signals ────────────────────────────────────────────────

class TestSellSignals:
    def test_clean_sell_signal(self, parser: RegexParser) -> None:
        text = """SELL XAUUSD
        Entry: 2390.50
        SL: 2398.00
        TP: 2382.00"""

        signal = parser.parse(text)
        assert signal is not None
        assert signal.action == TradeAction.SELL
        assert signal.entry_price == 2390.50
        assert signal.stop_loss == 2398.00
        assert 2382.00 in signal.take_profits

    def test_sell_with_lot_size(self, parser: RegexParser) -> None:
        text = """SELL XAUUSD
        Entry: 2390.50
        SL: 2398.00
        TP: 2382.00
        Lot: 0.05"""

        signal = parser.parse(text)
        assert signal is not None
        assert signal.lot_size == 0.05

    def test_sell_with_comma_prices(self, parser: RegexParser) -> None:
        text = """SELL XAUUSD at 2,385.50
        SL: 2,393.00
        TP1: 2,378.00"""

        signal = parser.parse(text)
        assert signal is not None
        assert signal.entry_price == 2385.50
        assert signal.stop_loss == 2393.00

    def test_multiple_take_profits(self, parser: RegexParser) -> None:
        text = """SELL XAUUSD
        Entry Price: 2401.50
        Stop Loss: 2410.00
        TP1: 2393.00
        TP2: 2385.00
        TP3: 2375.00"""

        signal = parser.parse(text)
        assert signal is not None
        assert len(signal.take_profits) == 3
        assert signal.take_profits == sorted(signal.take_profits)


# ── Test: Non-Signal Messages ───────────────────────────────────────────────

class TestNonSignals:
    def test_market_commentary(self, parser: RegexParser) -> None:
        text = """Market update: Gold is showing bullish momentum today after the Fed
        announcement. XAUUSD is currently trading at 2355. We expect a
        pullback to 2340 before the next move up."""

        signal = parser.parse(text)
        assert signal is None

    def test_greeting(self, parser: RegexParser) -> None:
        text = "Happy New Year everyone! 🎉 Wishing you a profitable year!"
        signal = parser.parse(text)
        assert signal is None

    def test_empty_text(self, parser: RegexParser) -> None:
        signal = parser.parse("")
        assert signal is None

    def test_very_short_text(self, parser: RegexParser) -> None:
        signal = parser.parse("hi")
        assert signal is None

    def test_none_text(self, parser: RegexParser) -> None:
        signal = parser.parse(None)  # type: ignore
        assert signal is None

    def test_update_signal(self, parser: RegexParser) -> None:
        text = "⚠️ UPDATE: Move SL to breakeven on the XAUUSD BUY from earlier."
        signal = parser.parse(text)
        # Should return None because there's no entry/SL/TP structure
        assert signal is None


# ── Test: Signal Validation ─────────────────────────────────────────────────

class TestSignalValidation:
    def test_buy_sl_above_entry_rejected(self) -> None:
        """BUY signal with SL above entry should be rejected by model."""
        with pytest.raises(ValueError, match="SL.*must be below"):
            ParsedSignal(
                action=TradeAction.BUY,
                entry_price=2345.0,
                stop_loss=2350.0,  # Wrong: above entry
                take_profits=[2355.0],
                raw_text="test",
            )

    def test_sell_sl_below_entry_rejected(self) -> None:
        """SELL signal with SL below entry should be rejected."""
        with pytest.raises(ValueError, match="SL.*must be above"):
            ParsedSignal(
                action=TradeAction.SELL,
                entry_price=2345.0,
                stop_loss=2340.0,  # Wrong: below entry
                take_profits=[2335.0],
                raw_text="test",
            )

    def test_buy_tp_below_entry_rejected(self) -> None:
        """BUY signal with TP below entry should be rejected."""
        with pytest.raises(ValueError, match="TP1.*must be above"):
            ParsedSignal(
                action=TradeAction.BUY,
                entry_price=2345.0,
                stop_loss=2340.0,
                take_profits=[2335.0],  # Wrong: below entry for BUY
                raw_text="test",
            )

    def test_dedup_hash_consistency(self) -> None:
        """Same signal parameters should produce the same hash."""
        s1 = ParsedSignal(
            action=TradeAction.BUY,
            entry_price=2345.0,
            stop_loss=2340.0,
            take_profits=[2355.0],
            raw_text="text1",
        )
        s2 = ParsedSignal(
            action=TradeAction.BUY,
            entry_price=2345.0,
            stop_loss=2340.0,
            take_profits=[2355.0],
            raw_text="different text",
        )
        assert s1.dedup_hash() == s2.dedup_hash()

    def test_dedup_hash_different_for_different_signals(self) -> None:
        """Different signals should have different hashes."""
        s1 = ParsedSignal(
            action=TradeAction.BUY,
            entry_price=2345.0,
            stop_loss=2340.0,
            take_profits=[2355.0],
            raw_text="test",
        )
        s2 = ParsedSignal(
            action=TradeAction.SELL,
            entry_price=2345.0,
            stop_loss=2350.0,
            take_profits=[2335.0],
            raw_text="test",
        )
        assert s1.dedup_hash() != s2.dedup_hash()

    def test_take_profits_sorted(self) -> None:
        """Take profits should be auto-sorted ascending."""
        signal = ParsedSignal(
            action=TradeAction.BUY,
            entry_price=2345.0,
            stop_loss=2340.0,
            take_profits=[2370.0, 2355.0, 2360.0],  # Unsorted
            raw_text="test",
        )
        assert signal.take_profits == [2355.0, 2360.0, 2370.0]


# ── Test: Regex Parser Confidence ───────────────────────────────────────────

class TestParserConfidence:
    def test_regex_confidence_is_lower(self, parser: RegexParser) -> None:
        """Regex parser should set a lower confidence than AI parsers."""
        text = "BUY XAUUSD @ 2345\nSL: 2338\nTP: 2352"
        signal = parser.parse(text)
        assert signal is not None
        assert signal.confidence < 1.0
        assert signal.confidence == 0.6
        assert signal.parser_source == "regex"
