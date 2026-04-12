"""
AutoTrader — AI Signal Parser Module

Parses unstructured trading signal text into structured ParsedSignal objects.
Uses a failover chain: Gemini Flash → Groq LLaMA → Regex fallback.

Each AI provider receives a carefully engineered system prompt that constrains
output to a strict JSON schema.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Optional

import google.generativeai as genai
from groq import AsyncGroq

from src.models import ParsedSignal, TradeAction
from src.regex_parser import RegexParser
from src.utils import get_logger

logger = get_logger("ai_parser")


# ── System Prompt ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a trading signal parser for XAUUSD (Gold/USD). Your ONLY job is to extract structured trade data from raw signal messages.

RULES:
1. Respond with ONLY valid JSON — no markdown, no explanation, no extra text.
2. If the message is NOT a trading signal (e.g., commentary, news, greetings), respond with exactly: {"is_signal": false}
3. If it IS a trading signal, respond with this exact JSON schema:

{
  "is_signal": true,
  "action": "BUY" or "SELL",
  "entry_price": <float>,
  "stop_loss": <float>,
  "take_profits": [<float>, ...],
  "lot_size": <float or null>,
  "confidence": <float 0.0-1.0>
}

EXTRACTION RULES:
- "action": Must be exactly "BUY" or "SELL". Synonyms: "Long"/"Go long" = BUY, "Short"/"Go short" = SELL.
- "entry_price": The recommended entry price. If a range is given (e.g., "2340-2345"), use the midpoint.
- "stop_loss": The stop-loss level. Always required for a valid signal.
- "take_profits": Array of take-profit levels, sorted ascending. Minimum 1 required.
- "lot_size": Explicit lot size if mentioned. Set to null if not specified.
- "confidence": Your confidence that you correctly parsed the signal (0.0 = guessing, 1.0 = certain).

XAUUSD CONTEXT:
- Gold prices are typically in the 1800-3500 range (2024-2026 context).
- Common SL distances: 3-20 dollars from entry.
- Common TP distances: 5-50 dollars from entry.
- If SL or TP seem unreasonable for XAUUSD, reduce confidence.

CRITICAL: For BUY signals, SL must be BELOW entry, and TP must be ABOVE entry.
CRITICAL: For SELL signals, SL must be ABOVE entry, and TP must be BELOW entry.
CRITICAL: Output ONLY the JSON object. No other text."""

# ── Gemini Parser ───────────────────────────────────────────────────────────


class GeminiParser:
    """Parse signals using Google Gemini Flash API."""

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash") -> None:
        self._model_name = model
        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(
            model_name=model,
            system_instruction=SYSTEM_PROMPT,
            generation_config=genai.GenerationConfig(
                temperature=0.1,      # Very low temp for deterministic parsing
                max_output_tokens=512, # Short responses only
                response_mime_type="application/json",  # Force JSON output
            ),
        )
        logger.info("Gemini parser initialized: model=%s", model)

    async def parse(self, raw_text: str) -> Optional[dict]:
        """
        Send text to Gemini and parse the JSON response.

        Args:
            raw_text: The raw signal message text.

        Returns:
            Parsed dict with signal data, or None on failure.
        """
        try:
            # Run the synchronous API call in a thread pool to avoid blocking
            response = await asyncio.to_thread(
                self._model.generate_content,
                raw_text,
            )

            if not response.text:
                logger.warning("Gemini returned empty response")
                return None

            result = json.loads(response.text)
            logger.debug("Gemini raw response: %s", result)
            return result

        except json.JSONDecodeError as e:
            logger.warning("Gemini returned invalid JSON: %s", e)
            return None
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "quota" in error_str.lower():
                logger.warning("Gemini rate limit hit: %s", e)
            elif "403" in error_str:
                logger.error("Gemini API key invalid or quota exhausted: %s", e)
            else:
                logger.error("Gemini API error: %s", e)
            return None


# ── Groq Parser ─────────────────────────────────────────────────────────────


class GroqParser:
    """Parse signals using Groq API (LLaMA models)."""

    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile") -> None:
        self._model = model
        self._client = AsyncGroq(api_key=api_key)
        logger.info("Groq parser initialized: model=%s", model)

    async def parse(self, raw_text: str) -> Optional[dict]:
        """
        Send text to Groq and parse the JSON response.

        Args:
            raw_text: The raw signal message text.

        Returns:
            Parsed dict with signal data, or None on failure.
        """
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": raw_text},
                ],
                temperature=0.1,
                max_tokens=512,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content
            if not content:
                logger.warning("Groq returned empty response")
                return None

            result = json.loads(content)
            logger.debug("Groq raw response: %s", result)
            return result

        except json.JSONDecodeError as e:
            logger.warning("Groq returned invalid JSON: %s", e)
            return None
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "rate" in error_str.lower():
                logger.warning("Groq rate limit hit: %s", e)
            else:
                logger.error("Groq API error: %s", e)
            return None


# ── AI Signal Parser (Orchestrator) ─────────────────────────────────────────


class AISignalParser:
    """
    Orchestrates signal parsing with failover:
    Gemini Flash → Groq LLaMA → Regex fallback.
    """

    def __init__(
        self,
        gemini_api_key: str,
        gemini_model: str,
        groq_api_key: str,
        groq_model: str,
    ) -> None:
        self._gemini = GeminiParser(api_key=gemini_api_key, model=gemini_model)
        self._groq = GroqParser(api_key=groq_api_key, model=groq_model)
        self._regex = RegexParser()

        # Track provider health for logging
        self._provider_stats = {
            "gemini": {"success": 0, "fail": 0},
            "groq": {"success": 0, "fail": 0},
            "regex": {"success": 0, "fail": 0},
        }

    async def parse(self, raw_text: str, message_id: Optional[int] = None) -> Optional[ParsedSignal]:
        """
        Parse a raw signal text through the failover chain.

        Args:
            raw_text: Raw message text from Telegram.
            message_id: Optional Telegram message ID for tracking.

        Returns:
            A validated ParsedSignal, or None if the message isn't a signal.
        """
        if not raw_text or len(raw_text.strip()) < 5:
            logger.debug("Skipping empty/tiny message")
            return None

        # Try Gemini first
        signal = await self._try_provider("gemini", self._gemini, raw_text, message_id)
        if signal is not None:
            return signal

        # Failover to Groq
        logger.info("Falling back to Groq parser...")
        signal = await self._try_provider("groq", self._groq, raw_text, message_id)
        if signal is not None:
            return signal

        # Last resort: regex
        logger.info("Falling back to regex parser...")
        signal = self._try_regex(raw_text, message_id)
        if signal is not None:
            return signal

        logger.warning("All parsers failed for message: %s", raw_text[:100])
        return None

    async def _try_provider(
        self,
        name: str,
        provider,
        raw_text: str,
        message_id: Optional[int],
    ) -> Optional[ParsedSignal]:
        """Try parsing with a specific AI provider."""
        try:
            result = await provider.parse(raw_text)
            if result is None:
                self._provider_stats[name]["fail"] += 1
                return None

            # Check if it's a signal
            if not result.get("is_signal", False):
                logger.info("Message identified as non-signal by %s", name)
                return None

            # Convert to ParsedSignal
            signal = self._build_signal(result, raw_text, message_id, name)
            if signal:
                self._provider_stats[name]["success"] += 1
                logger.info(
                    "Signal parsed by %s: %s %s @ %.2f, SL=%.2f, TP=%s",
                    name, signal.action.value, "XAUUSD",
                    signal.entry_price, signal.stop_loss,
                    [f"{tp:.2f}" for tp in signal.take_profits],
                )
            return signal

        except Exception as e:
            self._provider_stats[name]["fail"] += 1
            logger.error("Provider %s error: %s", name, e)
            return None

    def _try_regex(
        self, raw_text: str, message_id: Optional[int]
    ) -> Optional[ParsedSignal]:
        """Try parsing with the regex fallback."""
        try:
            signal = self._regex.parse(raw_text, message_id)
            if signal:
                self._provider_stats["regex"]["success"] += 1
                logger.info(
                    "Signal parsed by regex: %s @ %.2f, SL=%.2f",
                    signal.action.value, signal.entry_price, signal.stop_loss,
                )
            else:
                self._provider_stats["regex"]["fail"] += 1
            return signal
        except Exception as e:
            self._provider_stats["regex"]["fail"] += 1
            logger.error("Regex parser error: %s", e)
            return None

    def _build_signal(
        self,
        data: dict,
        raw_text: str,
        message_id: Optional[int],
        source: str,
    ) -> Optional[ParsedSignal]:
        """
        Build a ParsedSignal from raw AI response dict with validation.
        Returns None if the data is invalid.
        """
        try:
            action_str = str(data.get("action", "")).upper().strip()
            if action_str not in ("BUY", "SELL"):
                logger.warning("Invalid action '%s' from %s", action_str, source)
                return None

            entry = float(data.get("entry_price", 0))
            sl = float(data.get("stop_loss", 0))
            tps_raw = data.get("take_profits", [])

            if entry <= 0 or sl <= 0:
                logger.warning("Invalid entry/SL values from %s: entry=%.2f, sl=%.2f",
                               source, entry, sl)
                return None

            # Normalize take_profits to list of floats
            if isinstance(tps_raw, (int, float)):
                tps = [float(tps_raw)]
            elif isinstance(tps_raw, list):
                tps = [float(tp) for tp in tps_raw if tp and float(tp) > 0]
            else:
                tps = []

            if not tps:
                # If no TP, estimate one based on SL distance
                sl_distance = abs(entry - sl)
                if action_str == "BUY":
                    tps = [entry + sl_distance * 1.5]
                else:
                    tps = [entry - sl_distance * 1.5]
                logger.info(
                    "No TP provided; estimated TP=%.2f (1.5x SL distance)", tps[0]
                )

            lot_size = data.get("lot_size")
            if lot_size is not None:
                try:
                    lot_size = float(lot_size)
                    if lot_size <= 0:
                        lot_size = None
                except (ValueError, TypeError):
                    lot_size = None

            confidence = float(data.get("confidence", 0.8))

            return ParsedSignal(
                action=TradeAction(action_str),
                entry_price=entry,
                stop_loss=sl,
                take_profits=tps,
                lot_size=lot_size,
                confidence=confidence,
                raw_text=raw_text,
                message_id=message_id,
                parser_source=source,
            )

        except (ValueError, TypeError) as e:
            logger.warning("Failed to build ParsedSignal from %s data: %s", source, e)
            return None

    def get_stats(self) -> dict:
        """Return parsing statistics per provider."""
        return dict(self._provider_stats)
