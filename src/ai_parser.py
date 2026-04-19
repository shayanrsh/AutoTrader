"""
AutoTrader — AI Signal Parser Module

Parses unstructured trading signal text into structured ParsedSignal objects.
Uses a failover chain: Ollama Gemma → Gemini Flash → xAI Grok → Regex fallback.

Each AI provider receives a carefully engineered system prompt that constrains
output to a strict JSON schema.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import signal
import subprocess
import sys
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import httpx
except Exception as exc:
    httpx = None  # type: ignore[assignment]
    _HTTPX_IMPORT_ERROR = exc
else:
    _HTTPX_IMPORT_ERROR = None

try:
    from google import genai as modern_genai
    from google.genai import types as modern_genai_types
except Exception as exc:
    modern_genai = None  # type: ignore[assignment]
    modern_genai_types = None  # type: ignore[assignment]
    _MODERN_GENAI_IMPORT_ERROR = exc
else:
    _MODERN_GENAI_IMPORT_ERROR = None

try:
    from src.utils import get_logger
except ModuleNotFoundError:
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from src.utils import get_logger

# Lazily imported runtime symbols so --help works even if heavy deps are missing.
ParsedSignal = None  # type: ignore[assignment]
TradeAction = None  # type: ignore[assignment]
Database = None  # type: ignore[assignment]
SignalRecord = None  # type: ignore[assignment]
RegexParser = None  # type: ignore[assignment]
get_settings = None  # type: ignore[assignment]


def _ensure_runtime_imports() -> None:
    """Import runtime modules only when actual parsing/runtime execution is requested."""
    global ParsedSignal, TradeAction, Database, SignalRecord, RegexParser, get_settings

    if all(
        symbol is not None
        for symbol in (ParsedSignal, TradeAction, Database, SignalRecord, RegexParser, get_settings)
    ):
        return

    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    try:
        from src.config import get_settings as _get_settings
        from src.database import Database as _Database
        from src.models import ParsedSignal as _ParsedSignal
        from src.models import SignalRecord as _SignalRecord
        from src.models import TradeAction as _TradeAction
        from src.regex_parser import RegexParser as _RegexParser
    except ModuleNotFoundError as exc:
        missing_name = getattr(exc, "name", "unknown dependency")
        raise RuntimeError(
            "Missing runtime dependency '"
            f"{missing_name}' for ai_parser execution. "
            "Install project dependencies and rerun, for example:\n"
            "  cd /root/Projects/AutoTrader\n"
            "  python3 -m venv venv\n"
            "  source venv/bin/activate\n"
            "  pip install -r requirements.txt\n"
            "Then run:\n"
            "  ./venv/bin/python src/ai_parser.py --mode local-only --limit 200"
        ) from exc

    ParsedSignal = _ParsedSignal
    TradeAction = _TradeAction
    Database = _Database
    SignalRecord = _SignalRecord
    RegexParser = _RegexParser
    get_settings = _get_settings

logger = get_logger("ai_parser")
GEMINI_FALLBACK_MODELS = (
    "gemma-3n-e2b-it",
    "gemma-3-4b-it",
    "gemma-4-26b-a4b-it",
)
GOOGLE_DEFAULT_MODEL = GEMINI_FALLBACK_MODELS[0]


def _extract_json_object(text: str) -> str:
    """Return the first balanced JSON object substring from text, if present."""
    start = text.find("{")
    if start < 0:
        return text

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    return text[start:]


def _parse_json_relaxed(text: str) -> dict:
    """Parse model JSON with minimal repairs for common formatting issues."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.replace("json", "", 1).strip()

    cleaned = _extract_json_object(cleaned)
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
    return json.loads(cleaned)


# ── System Prompt ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a trading signal parser for XAUUSD (Gold/USD). Your ONLY job is to extract structured trade data from raw signal messages.

RULES:
1. Respond with ONLY valid JSON — no markdown, no explanation, no extra text.
2. If the message is NOT a trading signal (e.g., commentary, news, greetings), respond with exactly: {"is_signal": false}
3. If it IS a trading signal, respond with this exact JSON schema:

{
  "is_signal": true,
  "action": "BUY" or "SELL",
  "entry_price": [<float>, ...],
  "SL": <float>,
    "tp": <float>
}

EXTRACTION RULES:
- "action": Must be exactly "BUY" or "SELL". Synonyms: "خریدن/خرید"/"بخر/همین الآن طلا رو بخر/ همین حالا طلا رو بخر" = BUY, "فروختن/فروش"/"بفروش/همین حالا طلا رو بفروش/همین الآن طلا رو بفروش" = SELL.
- "entry_price": The recommended entry price. If a range is given (e.g., "2340-2345"), write make it an array of two floats sorted ascending: [2340.0, 2345.0]. If only one price is given, make it a single-element array.
- "SL": The stop-loss level. Always required for a valid signal.
- "tp": The primary take-profit level as a single float (TP1).
- Persian TP labels are common: "تی پی 1", "تیپی1", "هدف اول", "هدف 1" all mean TP1.
- If TP1/TP2/TP3 are present, TP1 is the PRIMARY target.

XAUUSD CONTEXT:
- Gold prices are typically in the 1800-3500 range (2024-2026 context).
- Common SL distances: 3-20 dollars from entry.
- Common TP distances: 5-50 dollars from entry.
- If SL or TP seem unreasonable for XAUUSD, reduce confidence.

CRITICAL: For BUY signals, SL must be BELOW entry, and tp must be ABOVE entry.
CRITICAL: For SELL signals, SL must be ABOVE entry, and tp must be BELOW entry.
CRITICAL: Output ONLY the JSON object. No other text."""


class LocalRateLimiter:
    """Simple in-process request limiter using rolling 60s and 24h windows."""

    def __init__(self, rpm: int = 0, rpd: int = 0) -> None:
        self._rpm = max(0, int(rpm))
        self._rpd = max(0, int(rpd))
        self._minute_hits: deque[float] = deque()
        self._day_hits: deque[float] = deque()

    def allow(self) -> bool:
        now = datetime.now(timezone.utc).timestamp()
        minute_cutoff = now - 60.0
        day_cutoff = now - 86400.0

        while self._minute_hits and self._minute_hits[0] < minute_cutoff:
            self._minute_hits.popleft()
        while self._day_hits and self._day_hits[0] < day_cutoff:
            self._day_hits.popleft()

        if self._rpm > 0 and len(self._minute_hits) >= self._rpm:
            return False
        if self._rpd > 0 and len(self._day_hits) >= self._rpd:
            return False

        self._minute_hits.append(now)
        self._day_hits.append(now)
        return True


class OllamaParser:
    """Parse signals using a local Ollama model."""

    def __init__(
        self,
        base_url: str,
        model: str = "gemma3:1b-q4_K_M",
        rpm_limit: int = 0,
        rpd_limit: int = 0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._limiter = LocalRateLimiter(rpm=rpm_limit, rpd=rpd_limit)
        logger.info("Ollama parser initialized: model=%s url=%s", model, self._base_url)

    async def parse(self, raw_text: str) -> Optional[dict]:
        """Send text to local Ollama and parse JSON response."""
        if httpx is None:
            logger.error("httpx dependency missing for Ollama parser: %r", _HTTPX_IMPORT_ERROR)
            return None

        if not self._limiter.allow():
            logger.warning(
                "Ollama local rate limit reached for model=%s. Skipping request.",
                self._model,
            )
            return None

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": raw_text},
            ],
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0.1,
                "num_predict": 512,
            },
        }

        try:
            timeout = httpx.Timeout(connect=5.0, read=90.0, write=20.0, pool=10.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(f"{self._base_url}/api/chat", json=payload)

            if response.status_code >= 400:
                logger.warning(
                    "Ollama API error: status=%d body=%s",
                    response.status_code,
                    response.text[:300],
                )
                return None

            body = response.json()
            content = ((body.get("message") or {}).get("content") or body.get("response") or "")
            if not content:
                logger.warning("Ollama returned empty response")
                return None

            result = json.loads(content)

            # Some local models omit the exact schema keys; normalize common variants.
            if "is_signal" not in result:
                action = str(result.get("action", "")).upper().strip()
                if action in {"BUY", "SELL"}:
                    entry = result.get("entry_price", result.get("XAUUSD", result.get("entry")))
                    sl = result.get("SL", result.get("stop_loss", result.get("sl")))
                    tps = result.get("take_profits")
                    if tps is None:
                        tp1 = result.get(
                            "tp",
                            result.get("TP", result.get("tp1", result.get("TP1", result.get("take_profit")))),
                        )
                        tps = [tp1] if tp1 is not None else []
                    result = {
                        "is_signal": True,
                        "action": action,
                        "entry_price": entry,
                        "SL": sl,
                        "stop_loss": sl,
                        "tp": tps[0] if isinstance(tps, list) and tps else tps,
                        "take_profits": tps,
                        "confidence": float(result.get("confidence", 0.8)),
                    }
                else:
                    result = {"is_signal": False}

            logger.debug("Ollama raw response: %s", result)
            return result
        except json.JSONDecodeError as e:
            logger.warning("Ollama returned invalid JSON: %s", e)
            return None
        except Exception as e:
            logger.warning("Ollama API unavailable or failed: %r", e)
            return None

# ── Gemini Parser ───────────────────────────────────────────────────────────


class GeminiParser:
    """Parse signals using Google Gemini Flash API."""

    def __init__(
        self,
        api_key: str,
        model: str = GOOGLE_DEFAULT_MODEL,
        rpm_limit: int = 0,
        rpd_limit: int = 0,
        rate_limits_by_model: Optional[dict[str, tuple[int, int]]] = None,
    ) -> None:
        self._api_key = api_key.strip()
        self._default_limits = (max(0, int(rpm_limit)), max(0, int(rpd_limit)))
        self._rate_limits_by_model = rate_limits_by_model or {}
        preferred_model = self._normalize_model_name(model)
        self._model_candidates = self._build_model_candidates(preferred_model)
        self._limiters: dict[str, LocalRateLimiter] = {}
        self._active_model_name = self._model_candidates[0]
        self._last_status_code: Optional[int] = None

        if not self._api_key:
            logger.warning("Gemini parser initialized without API key")
        logger.info(
            "Gemini parser initialized with fallback chain: %s",
            " -> ".join(self._model_candidates),
        )

    @staticmethod
    def _normalize_model_name(model: str) -> str:
        value = str(model).strip().lower()
        if value.startswith("models/"):
            value = value.split("/", 1)[1]

        # Backward-compatible aliases for common/older naming conventions.
        aliases = {
            "gemma-3-2b-it": "gemma-3n-e2b-it",
            "gemma-3-2b": "gemma-3n-e2b-it",
            "gemma 3 2b": "gemma-3n-e2b-it",
        }
        return aliases.get(value, value)

    def _build_model_candidates(self, preferred_model: str) -> list[str]:
        candidates: list[str] = []
        for candidate in (preferred_model, *GEMINI_FALLBACK_MODELS):
            normalized = self._normalize_model_name(candidate)
            if normalized and normalized not in candidates:
                candidates.append(normalized)
        return candidates or [GOOGLE_DEFAULT_MODEL]

    def _endpoint_for_model(self, model_name: str) -> str:
        return (
            f"https://generativelanguage.googleapis.com/v1beta/"
            f"models/{model_name}:generateContent"
        )

    def _limiter_for_model(self, model_name: str) -> LocalRateLimiter:
        existing = self._limiters.get(model_name)
        if existing is not None:
            return existing

        rpm, rpd = self._rate_limits_by_model.get(model_name, self._default_limits)
        limiter = LocalRateLimiter(rpm=rpm, rpd=rpd)
        self._limiters[model_name] = limiter
        return limiter

    @staticmethod
    def _model_unavailable(status_code: int, body: str) -> bool:
        if status_code == 404:
            return True

        if status_code != 400:
            return False

        lowered = body.lower()
        markers = (
            "model not found",
            "not found",
            "unsupported model",
            "not available",
            "does not exist",
        )
        return any(marker in lowered for marker in markers)

    async def parse(self, raw_text: str) -> Optional[dict]:
        """
        Send text to Gemini and parse the JSON response.

        Args:
            raw_text: The raw signal message text.

        Returns:
            Parsed dict with signal data, or None on failure.
        """
        try:
            self._last_status_code = None
            if httpx is None:
                logger.error("httpx dependency missing for Gemini parser: %r", _HTTPX_IMPORT_ERROR)
                return None

            if not self._api_key:
                logger.error("Gemini API key is missing")
                return None

            def _build_payload(use_system_instruction: bool, use_json_mode: bool) -> dict:
                generation_config = {
                    "temperature": 0.1,
                    "maxOutputTokens": 512,
                }
                if use_json_mode:
                    generation_config["responseMimeType"] = "application/json"
                    generation_config["responseSchema"] = {
                        "type": "OBJECT",
                        "properties": {
                            "is_signal": {"type": "BOOLEAN"},
                            "action": {"type": "STRING"},
                            "entry_price": {
                                "type": "ARRAY",
                                "items": {"type": "NUMBER"},
                            },
                            "SL": {"type": "NUMBER"},
                            "tp": {"type": "NUMBER"},
                            "confidence": {"type": "NUMBER"},
                        },
                        "required": ["is_signal"],
                    }

                if use_system_instruction:
                    return {
                        "system_instruction": {
                            "parts": [{"text": SYSTEM_PROMPT}],
                        },
                        "contents": [
                            {
                                "role": "user",
                                "parts": [{"text": raw_text}],
                            }
                        ],
                        "generationConfig": generation_config,
                    }

                # Some models (e.g., gemma-3-4b-it) reject developer/system instructions.
                merged_text = (
                    f"{SYSTEM_PROMPT}\n\n"
                    "Now parse the following message and return ONLY valid JSON object (no extra text):\n"
                    f"{raw_text}"
                )
                return {
                    "contents": [
                        {
                            "role": "user",
                            "parts": [{"text": merged_text}],
                        }
                    ],
                    "generationConfig": generation_config,
                }

            use_system_instruction = True
            use_json_mode = True
            payload = _build_payload(use_system_instruction=use_system_instruction, use_json_mode=use_json_mode)

            headers = {
                "Content-Type": "application/json",
                "X-goog-api-key": self._api_key,
            }
            async with httpx.AsyncClient(timeout=45.0) as client:
                for model_name in self._model_candidates:
                    limiter = self._limiter_for_model(model_name)
                    if not limiter.allow():
                        logger.warning(
                            "Gemini local rate limit reached for model=%s. Trying next fallback model.",
                            model_name,
                        )
                        continue

                    use_system_instruction = True
                    use_json_mode = True
                    payload = _build_payload(
                        use_system_instruction=use_system_instruction,
                        use_json_mode=use_json_mode,
                    )
                    endpoint = self._endpoint_for_model(model_name)

                    response = await client.post(endpoint, headers=headers, json=payload)

                    if response.status_code == 400 and "Developer instruction is not enabled" in response.text:
                        logger.info(
                            "Gemini model=%s does not support system_instruction; retrying without it",
                            model_name,
                        )
                        use_system_instruction = False
                        payload = _build_payload(
                            use_system_instruction=use_system_instruction,
                            use_json_mode=use_json_mode,
                        )
                        response = await client.post(endpoint, headers=headers, json=payload)

                    if response.status_code == 400 and "JSON mode is not enabled" in response.text:
                        logger.info(
                            "Gemini model=%s does not support JSON mode; retrying without response schema",
                            model_name,
                        )
                        use_json_mode = False
                        payload = _build_payload(
                            use_system_instruction=use_system_instruction,
                            use_json_mode=use_json_mode,
                        )
                        response = await client.post(endpoint, headers=headers, json=payload)

                    if response.status_code >= 400:
                        self._last_status_code = int(response.status_code)
                        if self._model_unavailable(response.status_code, response.text):
                            logger.warning(
                                "Gemini model unavailable: %s (status=%d). Trying next fallback model.",
                                model_name,
                                response.status_code,
                            )
                            continue

                        if response.status_code == 429:
                            logger.warning("Gemini rate limit hit: status=429 body=%s", response.text[:300])
                        elif response.status_code in {401, 403}:
                            logger.error("Gemini API key invalid or unauthorized: status=%d", response.status_code)
                        else:
                            logger.error("Gemini API error: status=%d body=%s", response.status_code, response.text[:300])
                        return None

                    body = response.json()
                    candidates = body.get("candidates") or []
                    if not candidates:
                        logger.warning("Gemini returned no candidates for model=%s", model_name)
                        continue

                    content = (candidates[0] or {}).get("content") or {}
                    parts = content.get("parts") or []
                    text = ""
                    for part in parts:
                        part_text = (part or {}).get("text")
                        if part_text:
                            text += str(part_text)
                    text = text.strip()

                    if not text:
                        logger.warning("Gemini returned empty response for model=%s", model_name)
                        continue

                    try:
                        result = json.loads(text)
                    except json.JSONDecodeError:
                        result = _parse_json_relaxed(text)

                    if model_name != self._active_model_name:
                        logger.info(
                            "Gemini model switch successful: %s -> %s",
                            self._active_model_name,
                            model_name,
                        )
                        self._active_model_name = model_name

                    logger.debug("Gemini raw response (model=%s): %s", model_name, result)
                    return result

            logger.warning(
                "All configured Gemini models failed/unavailable: %s",
                ", ".join(self._model_candidates),
            )
            return None

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

    @property
    def last_status_code(self) -> Optional[int]:
        return self._last_status_code


# ── xAI Grok Parser ─────────────────────────────────────────────────────────


class GrokParser:
    """Parse signals using xAI Grok Chat Completions API."""

    def __init__(
        self,
        api_key: str,
        model: str = "grok-3-mini",
        rpm_limit: int = 0,
        rpd_limit: int = 0,
    ) -> None:
        self._model = model
        self._limiter = LocalRateLimiter(rpm=rpm_limit, rpd=rpd_limit)
        self._api_key = api_key.strip()
        self._base_url = "https://api.x.ai/v1"
        logger.info("Grok parser initialized: model=%s", model)

    async def parse(self, raw_text: str) -> Optional[dict]:
        """
        Send text to xAI Grok and parse the JSON response.

        Args:
            raw_text: The raw signal message text.

        Returns:
            Parsed dict with signal data, or None on failure.
        """
        try:
            if httpx is None:
                logger.error("httpx dependency missing for Grok parser: %r", _HTTPX_IMPORT_ERROR)
                return None

            if not self._limiter.allow():
                logger.warning(
                    "xAI local rate limit reached for model=%s. Skipping request.",
                    self._model,
                )
                return None

            payload = {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": raw_text},
                ],
                "temperature": 0.1,
                "max_tokens": 512,
                "response_format": {"type": "json_object"},
            }
            headers = {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            }

            async with httpx.AsyncClient(timeout=45.0) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )

            if response.status_code >= 400:
                if response.status_code in {401, 403}:
                    logger.error("xAI API key invalid or unauthorized: status=%d", response.status_code)
                elif response.status_code == 429:
                    logger.warning("xAI rate limit hit: status=429 body=%s", response.text[:300])
                else:
                    logger.error("xAI API error: status=%d body=%s", response.status_code, response.text[:300])
                return None

            body = response.json()
            choices = body.get("choices") or []
            if not choices:
                logger.warning("Grok returned no choices")
                return None

            content = ((choices[0] or {}).get("message") or {}).get("content")
            if not content:
                logger.warning("Grok returned empty response")
                return None

            result = json.loads(content)
            logger.debug("Grok raw response: %s", result)
            return result

        except json.JSONDecodeError as e:
            logger.warning("Grok returned invalid JSON: %s", e)
            return None
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "rate" in error_str.lower():
                logger.warning("Grok rate limit hit: %s", e)
            else:
                logger.error("Grok API error: %s", e)
            return None


# ── AI Signal Parser (Orchestrator) ─────────────────────────────────────────


class AISignalParser:
    """
    Orchestrates signal parsing with failover:
    Ollama Gemma → Gemini Flash → xAI Grok → Regex fallback.
    """

    def __init__(
        self,
        ollama_enabled: bool,
        ollama_base_url: str,
        ollama_model: str,
        gemini_api_key: str,
        gemini_model: str,
        xai_api_key: str,
        xai_model: str,
        ollama_rate_limits: Optional[dict[str, tuple[int, int]]] = None,
        gemini_rate_limits: Optional[dict[str, tuple[int, int]]] = None,
        xai_rate_limits: Optional[dict[str, tuple[int, int]]] = None,
        provider_mode: str = "hybrid",
        cloud_provider: str = "all",
    ) -> None:
        _ensure_runtime_imports()
        self._provider_mode = provider_mode
        self._cloud_provider = cloud_provider
        self._use_ollama = provider_mode in {"hybrid", "local-only"}
        self._use_cloud = provider_mode in {"hybrid", "cloud-only"}
        self._use_grok = self._use_cloud and cloud_provider == "all"
        # Keep regex fallback only in hybrid mode to preserve strict local/cloud-only behavior.
        self._use_regex = provider_mode == "hybrid"

        ollama_limits = (ollama_rate_limits or {}).get(ollama_model, (0, 0))
        gemini_limits = (gemini_rate_limits or {}).get(gemini_model, (0, 0))
        xai_limits = (xai_rate_limits or {}).get(xai_model, (0, 0))

        self._ollama: Optional[OllamaParser] = None
        if ollama_enabled and self._use_ollama:
            self._ollama = OllamaParser(
                base_url=ollama_base_url,
                model=ollama_model,
                rpm_limit=ollama_limits[0],
                rpd_limit=ollama_limits[1],
            )

        self._gemini: Optional[GeminiParser] = None
        if self._use_cloud:
            self._gemini = GeminiParser(
                api_key=gemini_api_key,
                model=gemini_model,
                rpm_limit=gemini_limits[0],
                rpd_limit=gemini_limits[1],
                rate_limits_by_model=gemini_rate_limits,
            )

        self._grok: Optional[GrokParser] = None
        if self._use_grok:
            self._grok = GrokParser(
                api_key=xai_api_key,
                model=xai_model,
                rpm_limit=xai_limits[0],
                rpd_limit=xai_limits[1],
            )
        self._regex = RegexParser()

        # Track provider health for logging
        self._provider_stats = {
            "ollama": {"success": 0, "fail": 0},
            "gemini": {"success": 0, "fail": 0},
            "grok": {"success": 0, "fail": 0},
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

        # Try Ollama first (if enabled)
        if self._ollama is not None:
            signal, non_signal = await self._try_provider("ollama", self._ollama, raw_text, message_id)
            if signal is not None:
                return signal
            if non_signal:
                return None

        # Failover to Gemini
        if self._gemini is not None:
            logger.info("Falling back to Gemini parser...")
            signal, non_signal = await self._try_provider("gemini", self._gemini, raw_text, message_id)
            if signal is not None:
                return signal
            if non_signal:
                return None

        # Failover to xAI Grok
        if self._grok is not None:
            logger.info("Falling back to Grok parser...")
            signal, non_signal = await self._try_provider("grok", self._grok, raw_text, message_id)
            if signal is not None:
                return signal
            if non_signal:
                return None

        # Last resort: regex
        if self._use_regex:
            logger.info("Falling back to regex parser...")
            signal = self._try_regex(raw_text, message_id)
            if signal is not None:
                return signal

        logger.warning("All parsers failed for message: %s", raw_text[:100])
        return None

    async def parse_google(self, raw_text: str, message_id: Optional[int] = None) -> Optional[ParsedSignal]:
        """Parse with Gemini only (no local/fallback providers)."""
        if not raw_text or len(raw_text.strip()) < 5:
            logger.debug("Skipping empty/tiny message")
            return None

        if self._gemini is None:
            logger.warning("Gemini parser is disabled by current provider mode")
            return None

        signal, non_signal = await self._try_provider("gemini", self._gemini, raw_text, message_id)
        if signal is not None:
            return signal
        if non_signal:
            return None

        logger.warning("Google-only parse failed for message: %s", raw_text[:100])
        return None

    async def _try_provider(
        self,
        name: str,
        provider,
        raw_text: str,
        message_id: Optional[int],
    ) -> tuple[Optional[ParsedSignal], bool]:
        """Try parsing with a specific AI provider."""
        try:
            result = await provider.parse(raw_text)
            if result is None:
                self._provider_stats[name]["fail"] += 1
                return None, False

            # Check if it's a signal
            if not result.get("is_signal", False):
                logger.info("Message identified as non-signal by %s", name)
                return None, True

            # Convert to ParsedSignal
            signal = self._build_signal(result, raw_text, message_id, name)
            if signal:
                self._provider_stats[name]["success"] += 1
                logger.info(
                    "Signal parsed by %s: %s %s @ %.2f, SL=%.2f, TP=%s",
                    name, signal.action.value, "XAUUSD",
                    signal.entry_price, signal.stop_loss,
                    f"{signal.take_profits:.2f}",
                )
            return signal, False

        except Exception as e:
            self._provider_stats[name]["fail"] += 1
            logger.error("Provider %s error: %s", name, e)
            return None, False

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
            def _extract_numbers(value: object) -> list[float]:
                numbers: list[float] = []

                if value is None:
                    return numbers
                if isinstance(value, (list, tuple)):
                    for item in value:
                        numbers.extend(_extract_numbers(item))
                    return numbers

                try:
                    numbers.append(float(value))
                except (TypeError, ValueError):
                    pass
                return numbers

            def _extract_labeled_levels(text: str) -> tuple[Optional[float], list[float]]:
                sl_patterns = [
                    r"حد\s*ضرر\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)",
                    r"استاپ\s*[\-\u200c\s]*لاس\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)",
                    r"استاپ\s*لاس\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)",
                ]
                tp_patterns = [
                    r"حد\s*سود\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)",
                    r"تیک\s*پروفیت\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)",
                    r"تی\s*پی\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)",
                    r"هدف\s*(?:اول|اولی|1)?\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)",
                ]

                sl_matches: list[str] = []
                tp_matches: list[str] = []
                for pattern in sl_patterns:
                    sl_matches.extend(re.findall(pattern, text))
                for pattern in tp_patterns:
                    tp_matches.extend(re.findall(pattern, text))

                sl_value: Optional[float] = None
                if sl_matches:
                    try:
                        sl_value = float(sl_matches[0])
                    except (TypeError, ValueError):
                        sl_value = None

                tp_values: list[float] = []
                for raw in tp_matches:
                    try:
                        tp_values.append(float(raw))
                    except (TypeError, ValueError):
                        continue

                return sl_value, tp_values

            action_str = str(data.get("action", "")).upper().strip()
            if action_str not in ("BUY", "SELL"):
                logger.warning("Invalid action '%s' from %s", action_str, source)
                return None

            entry_raw = data.get("entry_price", 0)
            entry_values = _extract_numbers(entry_raw)
            if not entry_values:
                entry = 0.0
            elif len(entry_values) == 1:
                entry = entry_values[0]
            else:
                # Direction-aware range selection requested by strategy.
                # BUY: use upper bound, SELL: use lower bound.
                entry = max(entry_values) if action_str == "BUY" else min(entry_values)

            if entry <= 0:
                logger.warning("Invalid entry from %s: entry=%.2f", source, entry)
                return None

            is_buy = action_str == "BUY"

            def _valid_sl(value: float) -> bool:
                return value < entry if is_buy else value > entry

            def _valid_tp(value: float) -> bool:
                return value > entry if is_buy else value < entry

            def _dedupe(values: list[float]) -> list[float]:
                out: list[float] = []
                for value in values:
                    if all(abs(value - existing) > 1e-9 for existing in out):
                        out.append(value)
                return out

            labeled_sl, labeled_tps = _extract_labeled_levels(raw_text)

            sl_candidates = _extract_numbers(data.get("SL", data.get("stop_loss", 0)))
            sl = sl_candidates[0] if sl_candidates else 0.0
            tps_raw = data.get("take_profits", data.get("tp", data.get("TP")))

            # Prefer explicit TP1-style keys when present.
            tp1_candidates = [
                data.get("tp1"),
                data.get("tp_1"),
                data.get("take_profit_1"),
                data.get("takeProfit1"),
                data.get("target1"),
                data.get("هدف1"),
                data.get("هدف_1"),
                data.get("هدف اول"),
            ]

            # Normalize take_profits to list of floats (TP1 priority)
            tps = [tp for tp in _extract_numbers(tps_raw) if tp > 0]

            tp1_value: Optional[float] = None
            for candidate in tp1_candidates:
                candidate_values = _extract_numbers(candidate)
                if candidate_values:
                    v = candidate_values[0]
                    if v > 0:
                        tp1_value = v
                        break

            if tp1_value is not None:
                # Force TP1 as primary even when TP2/TP3 also exist.
                tps = [tp1_value] + [tp for tp in tps if abs(tp - tp1_value) > 1e-9]

            # Prefer explicitly labeled values in Persian message text when available.
            if labeled_sl is not None and labeled_sl > 0:
                sl = labeled_sl
            if labeled_tps:
                tps = labeled_tps + tps

            # Keep only direction-valid TP levels and de-duplicate.
            tps = _dedupe([tp for tp in tps if tp > 0 and _valid_tp(tp)])

            # If SL is on the wrong side, attempt recovery from alternate keys/values.
            if sl <= 0 or not _valid_sl(sl):
                sl_recovery_pool: list[float] = []
                for key in (
                    "sl",
                    "stop",
                    "sl_price",
                    "stopLoss",
                    "حد ضرر",
                    "stop_loss",
                ):
                    sl_recovery_pool.extend(_extract_numbers(data.get(key)))

                # Some model outputs accidentally place SL among TP values.
                sl_recovery_pool.extend(_extract_numbers(tps_raw))
                sl_recovery_pool.extend(tp for tp in tps if tp > 0)

                valid_sls = [v for v in sl_recovery_pool if v > 0 and _valid_sl(v)]
                if valid_sls:
                    sl = min(valid_sls, key=lambda v: abs(v - entry))

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

            if sl <= 0 or not _valid_sl(sl):
                logger.warning(
                    "Invalid side for SL from %s: action=%s entry=%.2f sl=%.2f",
                    source,
                    action_str,
                    entry,
                    sl,
                )
                return None

            if not tps:
                logger.warning(
                    "No valid TP levels from %s: action=%s entry=%.2f",
                    source,
                    action_str,
                    entry,
                )
                return None

            # Apply execution offsets requested by strategy before persistence/execution.
            if is_buy:
                tps = [tp - 1.0 for tp in tps]
                sl -= 2.0
            else:
                tps = [tp + 1.0 for tp in tps]
                sl += 2.0

            # Keep only TP values that are still direction-valid after offsets.
            tps = _dedupe([tp for tp in tps if tp > 0 and _valid_tp(tp)])
            if not tps or sl <= 0 or not _valid_sl(sl):
                logger.warning(
                    "Adjusted signal became invalid from %s: action=%s entry=%.2f sl=%.2f tps=%s",
                    source,
                    action_str,
                    entry,
                    sl,
                    tps,
                )
                return None

            lot_size = data.get("lot_size")
            if lot_size is not None:
                try:
                    lot_values = _extract_numbers(lot_size)
                    lot_size = lot_values[0] if lot_values else None
                    if lot_size <= 0:
                        lot_size = None
                except (ValueError, TypeError):
                    lot_size = None

            confidence_values = _extract_numbers(data.get("confidence", 0.8))
            confidence = confidence_values[0] if confidence_values else 0.8

            return ParsedSignal(
                action=TradeAction(action_str),
                entry_price=entry,
                stop_loss=sl,
                take_profits=tps[0],
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
        return {
            provider: {
                "success": int(values.get("success", 0)),
                "fail": int(values.get("fail", 0)),
            }
            for provider, values in self._provider_stats.items()
        }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "AI parser utility: parse pending telegram messages from DB (default), "
            "or parse one provided text."
        ),
        epilog=(
            "Examples:\n"
            "  # DB parse with hybrid mode (local + cloud + regex fallback)\n"
            "  python src/ai_parser.py --mode hybrid --limit 200\n\n"
            "  # DB parse with local LLM only (Ollama only, no cloud, no regex)\n"
            "  python src/ai_parser.py --mode local-only --limit 200\n\n"
            "  # DB parse with cloud AI only (Gemini + xAI, no local, no regex)\n"
            "  python src/ai_parser.py --mode cloud-only --cloud-provider all --limit 200\n\n"
            "  # Cloud Gemni-only mode (backward-compatible)\n"
            "  python src/ai_parser.py --google --limit 200\n\n"
            "  # One-off text parse with local LLM only\n"
            "  python src/ai_parser.py --mode local-only --text 'BUY XAUUSD 3320 SL 3310 TP 3340'\n"
        ),
    )
    parser.add_argument(
        "--text",
        default="",
        help="Raw signal text to parse once and print result.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Max pending DB rows to parse in one run (default: 200).",
    )
    parser.add_argument(
        "--include-failed",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Include FAILED rows for retry (default: disabled; use --include-failed to enable).",
    )
    parser.add_argument(
        "--health-check",
        action="store_true",
        help="Only initialize parser and exit (no DB parsing).",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show background parser worker status and exit.",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Stop background parser worker and exit.",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Start parser worker in background and exit.",
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="Run parser worker loop in foreground.",
    )
    parser.add_argument(
        "--worker",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Worker polling interval in seconds (default: 30).",
    )
    parser.add_argument(
        "--google",
        action="store_true",
        help="Use Google Gemini only (skip Ollama, xAI, and regex).",
    )
    parser.add_argument(
        "--mode",
        choices=["hybrid", "local-only", "cloud-only"],
        default="hybrid",
        help=(
            "Provider execution mode: hybrid (default), local-only (Ollama only), "
            "cloud-only (Gemini/xAI only)."
        ),
    )
    parser.add_argument(
        "--cloud-provider",
        choices=["all", "gemini-only"],
        default="all",
        help="Cloud provider selection for cloud/hybrid modes: all (Gemini + xAI) or gemini-only.",
    )
    parser.add_argument(
        "--google-model",
        default=GOOGLE_DEFAULT_MODEL,
        help=(
            "Google model to use with --google / cloud mode "
            f"(default: {GOOGLE_DEFAULT_MODEL}). "
            "Use full form like models/gemma-3n-e2b-it or short name."
        ),
    )
    return parser.parse_args()


def _resolve_provider_args(args: argparse.Namespace) -> tuple[str, str]:
    """Resolve effective provider mode/provider with backward compatibility."""
    mode = args.mode
    cloud_provider = args.cloud_provider

    if args.google:
        mode = "cloud-only"
        cloud_provider = "gemini-only"

    return mode, cloud_provider


def _project_root_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def _pid_file_path() -> Path:
    return _project_root_dir() / "data" / "ai_parser_worker.pid"


def _is_pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_pid_file() -> Optional[int]:
    pid_file = _pid_file_path()
    if not pid_file.exists():
        return None

    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except Exception:
        return None
    return pid if pid > 0 else None


def _status_background_worker() -> str:
    pid = _read_pid_file()
    if pid is None:
        return "AI parser worker is not running (no pid file)."
    if _is_pid_running(pid):
        return f"AI parser worker is running in background (pid={pid})."

    try:
        _pid_file_path().unlink(missing_ok=True)
    except Exception:
        pass
    return "AI parser worker is not running (stale pid file cleaned)."


def _stop_background_worker() -> str:
    pid = _read_pid_file()
    if pid is None:
        return "AI parser worker is not running."

    if not _is_pid_running(pid):
        try:
            _pid_file_path().unlink(missing_ok=True)
        except Exception:
            pass
        return "AI parser worker was already stopped (stale pid file removed)."

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        return f"Failed to stop parser worker pid={pid}: {exc}"

    try:
        _pid_file_path().unlink(missing_ok=True)
    except Exception:
        pass
    return f"Stop signal sent to AI parser worker (pid={pid})."


def _launch_background_worker(args: argparse.Namespace) -> int:
    script_path = Path(__file__).resolve()
    pid_file = _pid_file_path()
    pid_file.parent.mkdir(parents=True, exist_ok=True)

    if pid_file.exists():
        try:
            existing_pid = int(pid_file.read_text(encoding="utf-8").strip())
            if existing_pid > 0 and _is_pid_running(existing_pid):
                return existing_pid
        except Exception:
            pass

    cmd = [
        sys.executable,
        str(script_path),
        "--worker",
        "--interval",
        str(max(5, int(args.interval))),
        "--limit",
        str(max(1, int(args.limit))),
    ]
    if args.include_failed:
        cmd.append("--include-failed")
    else:
        cmd.append("--no-include-failed")
    if args.google:
        cmd.append("--google")

    process = subprocess.Popen(  # noqa: S603
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(_project_root_dir()),
        preexec_fn=os.setsid,
        close_fds=True,
    )
    pid_file.write_text(str(process.pid), encoding="utf-8")
    return process.pid


def _coerce_utc_datetime(raw: object) -> datetime:
    """Convert DB timestamp to UTC datetime safely."""
    if isinstance(raw, datetime):
        return raw if raw.tzinfo is not None else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, str) and raw:
        value = raw.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(value)
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


async def _parse_pending_db_messages(
    parser: AISignalParser,
    db: Database,
    limit: int,
    include_failed: bool,
    google_only: bool = False,
) -> dict[str, int]:
    """Parse pending telegram_messages rows and persist signal records exactly once."""
    rows = await db.get_pending_telegram_events(limit=limit, include_failed=include_failed)
    stats = {
        "fetched": len(rows),
        "processed": 0,
        "signals": 0,
        "skipped": 0,
        "failed": 0,
        "already_parsed": 0,
    }

    for row in rows:
        event_id = int(row["id"])
        message_id = int(row["message_id"])
        raw_text = str(row.get("text_after") or "")
        created_at = _coerce_utc_datetime(row.get("created_at"))

        try:
            if await db.has_parsed_signal_for_message(message_id):
                await db.mark_telegram_event_parse_status(
                    event_id,
                    "PROCESSED",
                    parser_source="existing",
                    parse_error="signal already exists for message_id",
                )
                stats["already_parsed"] += 1
                stats["processed"] += 1
                continue

            await db.mark_telegram_event_parse_status(event_id, "PROCESSING")
            before_stats = parser.get_stats()
            if google_only:
                signal = await parser.parse_google(raw_text, message_id=message_id)
            else:
                signal = await parser.parse(raw_text, message_id=message_id)
            after_stats = parser.get_stats()

            ollama_fail_delta = (
                int(after_stats.get("ollama", {}).get("fail", 0))
                - int(before_stats.get("ollama", {}).get("fail", 0))
            )
            gemini_fail_delta = (
                int(after_stats.get("gemini", {}).get("fail", 0))
                - int(before_stats.get("gemini", {}).get("fail", 0))
            )
            grok_fail_delta = (
                int(after_stats.get("grok", {}).get("fail", 0))
                - int(before_stats.get("grok", {}).get("fail", 0))
            )
            ai_provider_failed = (
                (ollama_fail_delta > 0)
                or (gemini_fail_delta > 0)
                or (grok_fail_delta > 0)
            )

            if signal is None:
                if ai_provider_failed:
                    if google_only and parser._gemini.last_status_code == 429:
                        await db.mark_telegram_event_parse_status(
                            event_id,
                            "PENDING",
                            parser_source="gemini",
                            parse_error="gemini rate limited (429), deferred for retry",
                        )
                        stats["skipped"] += 1
                        stats["processed"] += 1
                        continue

                    await db.mark_telegram_event_parse_status(
                        event_id,
                        "PENDING",
                        parser_source="none",
                        parse_error="ai provider unavailable/request failed; deferred for retry",
                    )
                    stats["skipped"] += 1
                    stats["processed"] += 1
                    continue

                await db.mark_telegram_event_parse_status(
                    event_id,
                    "PROCESSED",
                    parser_source="none",
                    parse_error="non-signal or parse failed",
                )
                stats["skipped"] += 1
                stats["processed"] += 1
                continue

            signal.timestamp = created_at
            await db.insert_signal(
                SignalRecord(
                    dedup_hash=signal.dedup_hash(),
                    raw_text=raw_text[:500],
                    parsed_action=signal.action.value,
                    parsed_entry=signal.entry_price,
                    parsed_sl=signal.stop_loss,
                    parsed_tp1=signal.take_profits,
                    parser_source=signal.parser_source,
                    trade_status="PARSED",
                    message_id=message_id,
                    created_at=created_at,
                )
            )
            await db.mark_telegram_event_parse_status(
                event_id,
                "PROCESSED",
                parser_source=signal.parser_source,
            )
            stats["signals"] += 1
            stats["processed"] += 1
        except Exception as exc:
            await db.mark_telegram_event_parse_status(
                event_id,
                "FAILED",
                parser_source="none",
                parse_error=str(exc)[:400],
            )
            stats["failed"] += 1

    return stats


async def _run_worker_loop(args: argparse.Namespace) -> None:
    _ensure_runtime_imports()
    settings = get_settings()
    provider_mode, cloud_provider = _resolve_provider_args(args)
    selected_gemini_model = args.google_model if args.google else settings.gemini_model
    parser = AISignalParser(
        ollama_enabled=settings.ollama_enabled,
        ollama_base_url=settings.ollama_base_url,
        ollama_model=settings.ollama_model,
        gemini_api_key=settings.gemini_api_key,
        gemini_model=selected_gemini_model,
        xai_api_key=settings.xai_api_key,
        xai_model=settings.xai_model,
        ollama_rate_limits=settings.ollama_rate_limits_map(),
        gemini_rate_limits=settings.gemini_rate_limits_map(),
        xai_rate_limits=settings.xai_rate_limits_map(),
        provider_mode=provider_mode,
        cloud_provider=cloud_provider,
    )
    db = Database(db_path=settings.database_path)
    await db.connect()

    interval = max(5, int(args.interval))
    limit = max(1, int(args.limit))
    include_failed = bool(args.include_failed)

    if args.worker:
        _pid_file_path().write_text(str(os.getpid()), encoding="utf-8")

    print(
        "AI parser worker running "
        f"(interval={interval}s, limit={limit}, include_failed={include_failed})."
    )
    print(
        f"Provider mode: {provider_mode}"
        + (f" (cloud-provider={cloud_provider})" if provider_mode != "local-only" else "")
    )
    if provider_mode != "local-only":
        print(f"Gemini model: {selected_gemini_model}")
    print("Press Ctrl+C to stop.")

    try:
        while True:
            await db.recover_processing_telegram_events()
            stats = await _parse_pending_db_messages(
                parser=parser,
                db=db,
                limit=limit,
                include_failed=include_failed,
                google_only=args.google,
            )
            print(
                "Worker cycle: "
                f"fetched={stats['fetched']} processed={stats['processed']} "
                f"signals={stats['signals']} skipped={stats['skipped']} "
                f"already_parsed={stats['already_parsed']} failed={stats['failed']}"
            )
            await asyncio.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        await db.close()
        if args.worker:
            try:
                _pid_file_path().unlink(missing_ok=True)
            except Exception:
                pass


async def _run_cli() -> None:
    args = _parse_args()
    provider_mode, cloud_provider = _resolve_provider_args(args)

    if any(flag in sys.argv for flag in ("-h", "--help")):
        # Keep help available even when optional runtime deps are not installed.
        return

    if args.status:
        print(_status_background_worker())
        return

    if args.stop:
        print(_stop_background_worker())
        return

    if args.daemon:
        pid = _launch_background_worker(args)
        print(f"[OK] AI parser worker is running in background (pid={pid}).")
        print("Use --status to check and --stop to terminate it.")
        return

    if args.foreground or args.worker:
        await _run_worker_loop(args)
        return

    _ensure_runtime_imports()
    settings = get_settings()
    selected_gemini_model = args.google_model if args.google else settings.gemini_model
    parser = AISignalParser(
        ollama_enabled=settings.ollama_enabled,
        ollama_base_url=settings.ollama_base_url,
        ollama_model=settings.ollama_model,
        gemini_api_key=settings.gemini_api_key,
        gemini_model=selected_gemini_model,
        xai_api_key=settings.xai_api_key,
        xai_model=settings.xai_model,
        ollama_rate_limits=settings.ollama_rate_limits_map(),
        gemini_rate_limits=settings.gemini_rate_limits_map(),
        xai_rate_limits=settings.xai_rate_limits_map(),
        provider_mode=provider_mode,
        cloud_provider=cloud_provider,
    )

    print(
        f"Provider mode: {provider_mode}"
        + (f" (cloud-provider={cloud_provider})" if provider_mode != "local-only" else "")
    )
    if provider_mode != "local-only":
        print(f"Gemini model: {selected_gemini_model}")

    if args.health_check:
        print("[OK] AI parser initialized successfully.")
        print("Health check mode: no parsing executed.")
        return

    if args.text.strip():
        if args.google:
            signal = await parser.parse_google(args.text, message_id=None)
        else:
            signal = await parser.parse(args.text, message_id=None)
        if signal is None:
            print("Parse result: non-signal or parsing failed")
            return

        print("Parse result:")
        print(signal.model_dump_json(indent=2))
        return

    db = Database(db_path=settings.database_path)
    await db.connect()
    try:
        recovered = await db.recover_processing_telegram_events()
        if recovered > 0:
            print(f"Recovered {recovered} stale PROCESSING row(s) to PENDING.")
        stats = await _parse_pending_db_messages(
            parser=parser,
            db=db,
            limit=max(1, args.limit),
            include_failed=args.include_failed,
            google_only=args.google,
        )
    finally:
        await db.close()

    print("[OK] AI parser initialized successfully.")
    print(
        "DB parse run complete: "
        f"fetched={stats['fetched']} processed={stats['processed']} "
        f"signals={stats['signals']} skipped={stats['skipped']} "
        f"already_parsed={stats['already_parsed']} failed={stats['failed']}"
    )
    if stats["fetched"] == 0:
        print("No pending telegram messages found.")


def main() -> None:
    asyncio.run(_run_cli())


if __name__ == "__main__":
    main()
