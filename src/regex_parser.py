"""
AutoTrader — Regex Fallback Signal Parser

Pattern-matching parser for common trading signal formats.
Used when both AI APIs (Gemini and Groq) are unavailable.

Handles these common signal patterns:
  - "BUY XAUUSD @ 2345.50"
  - "SELL Gold 2340"
  - "SL: 2330.00" / "SL 2330" / "Stop Loss: 2330.00"
  - "TP1: 2350" / "TP: 2350, 2360" / "Take Profit: 2350.00"
  - "Entry: 2345" / "Entry Zone: 2340-2345"
"""

from __future__ import annotations

import re
from typing import Optional

from src.models import ParsedSignal, TradeAction
from src.utils import get_logger

logger = get_logger("regex_parser")


class RegexParser:
    """
    Regex-based trading signal parser.
    Designed to handle the 80% common case of well-formatted signals.
    """

    # ── Compiled Patterns ───────────────────────────────────────────────

    # Action detection
    _BUY_PATTERN = re.compile(
        r"\b(BUY|LONG|GO\s+LONG|BUY\s+LIMIT|BUY\s+STOP|BUYING)\b",
        re.IGNORECASE,
    )
    _SELL_PATTERN = re.compile(
        r"\b(SELL|SHORT|GO\s+SHORT|SELL\s+LIMIT|SELL\s+STOP|SELLING)\b",
        re.IGNORECASE,
    )

    # Price extraction (handles comma thousands separator and decimal)
    _PRICE_RE = r"(\d{1,2}[,.]?\d{3}(?:\.\d{1,2})?|\d{4,5}(?:\.\d{1,2})?)"

    # Entry price patterns
    _ENTRY_PATTERNS = [
        re.compile(rf"(?:entry|enter|@|price|at)\s*[:\-=]?\s*{_PRICE_RE}", re.IGNORECASE),
        re.compile(
            rf"(?:BUY|SELL|LONG|SHORT)\s+(?:XAUUSD|GOLD|XAU)\s*(?:@|at)?\s*{_PRICE_RE}",
            re.IGNORECASE,
        ),
        # Entry zone/range: use midpoint
        re.compile(
            rf"(?:entry|zone)\s*[:\-=]?\s*{_PRICE_RE}\s*[-–—to]+\s*{_PRICE_RE}",
            re.IGNORECASE,
        ),
    ]

    # Stop-loss patterns
    _SL_PATTERNS = [
        re.compile(rf"(?:SL|stop\s*loss|stop)\s*[:\-=]?\s*{_PRICE_RE}", re.IGNORECASE),
    ]

    # Take-profit patterns (captures multiple TPs)
    _TP_PATTERNS = [
        # TP1: 2350 TP2: 2360 TP3: 2370
        re.compile(rf"TP\s*\d?\s*[:\-=]?\s*{_PRICE_RE}", re.IGNORECASE),
        # Take Profit: 2350
        re.compile(rf"take\s*profit\s*\d?\s*[:\-=]?\s*{_PRICE_RE}", re.IGNORECASE),
        # Target: 2350
        re.compile(rf"target\s*\d?\s*[:\-=]?\s*{_PRICE_RE}", re.IGNORECASE),
    ]

    # Lot size patterns
    _LOT_PATTERN = re.compile(
        r"(?:lot|lots|volume|size)\s*[:\-=]?\s*(\d+\.?\d*)",
        re.IGNORECASE,
    )

    def parse(
        self, raw_text: str, message_id: Optional[int] = None
    ) -> Optional[ParsedSignal]:
        """
        Attempt to parse a trading signal from raw text using regex patterns.

        Args:
            raw_text: The raw signal text.
            message_id: Optional Telegram message ID.

        Returns:
            ParsedSignal if successfully parsed, None otherwise.
        """
        if not raw_text or len(raw_text.strip()) < 10:
            return None

        text = raw_text.strip()

        # Step 1: Detect action (BUY or SELL)
        action = self._detect_action(text)
        if action is None:
            logger.debug("Regex: No BUY/SELL action found in text")
            return None

        # Step 2: Extract entry price
        entry = self._extract_entry(text)
        if entry is None:
            logger.debug("Regex: No entry price found")
            return None

        # Step 3: Extract stop-loss
        sl = self._extract_sl(text)
        if sl is None:
            logger.debug("Regex: No stop-loss found")
            return None

        # Step 4: Extract take-profits
        tps = self._extract_tps(text)
        if not tps:
            # Estimate TP based on SL distance
            sl_dist = abs(entry - sl)
            if action == TradeAction.BUY:
                tps = [entry + sl_dist * 1.5]
            else:
                tps = [entry - sl_dist * 1.5]
            logger.debug("Regex: No TP found; estimated TP=%.2f", tps[0])

        # Step 5: Extract lot size (optional)
        lot_size = self._extract_lot_size(text)

        # Step 6: Validate directional consistency
        if action == TradeAction.BUY:
            if sl >= entry:
                logger.warning(
                    "Regex: BUY signal but SL(%.2f) >= Entry(%.2f) — swapping", sl, entry
                )
                # Maybe the values are swapped in the message
                if tps[0] < entry:
                    # Everything looks inverted; it might be a SELL
                    action = TradeAction.SELL
                else:
                    return None

            if tps[0] <= entry:
                logger.warning(
                    "Regex: BUY signal but TP(%.2f) <= Entry(%.2f)", tps[0], entry
                )
                return None
        else:  # SELL
            if sl <= entry:
                logger.warning(
                    "Regex: SELL signal but SL(%.2f) <= Entry(%.2f) — checking", sl, entry
                )
                if tps[0] > entry:
                    action = TradeAction.BUY
                else:
                    return None

            if tps[0] >= entry:
                logger.warning(
                    "Regex: SELL signal but TP(%.2f) >= Entry(%.2f)", tps[0], entry
                )
                return None

        try:
            signal = ParsedSignal(
                action=action,
                entry_price=entry,
                stop_loss=sl,
                take_profits=tps,
                lot_size=lot_size,
                confidence=0.6,  # Lower confidence than AI parsers
                raw_text=raw_text,
                message_id=message_id,
                parser_source="regex",
            )
            return signal
        except Exception as e:
            logger.warning("Regex: Failed to create ParsedSignal: %s", e)
            return None

    # ── Private extraction methods ──────────────────────────────────────

    def _detect_action(self, text: str) -> Optional[TradeAction]:
        """Detect BUY or SELL from text."""
        has_buy = bool(self._BUY_PATTERN.search(text))
        has_sell = bool(self._SELL_PATTERN.search(text))

        if has_buy and not has_sell:
            return TradeAction.BUY
        if has_sell and not has_buy:
            return TradeAction.SELL
        if has_buy and has_sell:
            # Both found — use the first one that appears
            buy_pos = self._BUY_PATTERN.search(text).start()  # type: ignore
            sell_pos = self._SELL_PATTERN.search(text).start()  # type: ignore
            return TradeAction.BUY if buy_pos < sell_pos else TradeAction.SELL
        return None

    def _normalize_price(self, price_str: str) -> float:
        """Convert a price string to float, handling comma separators."""
        # Remove commas used as thousands separators
        cleaned = price_str.replace(",", "")
        return float(cleaned)

    def _extract_entry(self, text: str) -> Optional[float]:
        """Extract entry price from text."""
        for pattern in self._ENTRY_PATTERNS:
            match = pattern.search(text)
            if match:
                groups = match.groups()
                if len(groups) == 2:
                    # Entry range — use midpoint
                    low = self._normalize_price(groups[0])
                    high = self._normalize_price(groups[1])
                    return (low + high) / 2.0
                else:
                    return self._normalize_price(groups[0])

        # Fallback: find any price-like number near BUY/SELL keyword
        action_match = self._BUY_PATTERN.search(text) or self._SELL_PATTERN.search(text)
        if action_match:
            after_action = text[action_match.end():]
            price_match = re.search(self._PRICE_RE, after_action)
            if price_match:
                price = self._normalize_price(price_match.group(1))
                # Sanity check for XAUUSD range
                if 1500 <= price <= 4000:
                    return price

        return None

    def _extract_sl(self, text: str) -> Optional[float]:
        """Extract stop-loss price from text."""
        for pattern in self._SL_PATTERNS:
            match = pattern.search(text)
            if match:
                price = self._normalize_price(match.group(1))
                if 1500 <= price <= 4000:
                    return price
        return None

    def _extract_tps(self, text: str) -> list[float]:
        """Extract all take-profit levels from text."""
        tps = []
        for pattern in self._TP_PATTERNS:
            for match in pattern.finditer(text):
                price = self._normalize_price(match.group(1))
                if 1500 <= price <= 4000 and price not in tps:
                    tps.append(price)

        return sorted(tps) if tps else []

    def _extract_lot_size(self, text: str) -> Optional[float]:
        """Extract lot size from text if present."""
        match = self._LOT_PATTERN.search(text)
        if match:
            lot = float(match.group(1))
            if 0.01 <= lot <= 100:  # Sanity bounds
                return lot
        return None
