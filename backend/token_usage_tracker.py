#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Token usage tracking helpers for live model-cost visibility."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass, field
from typing import Any


_ASCII_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")


def estimate_text_tokens(text: str) -> int:
    """Return a stable approximation when provider usage metadata is absent.

    Chinese characters are roughly one token each for common local chat models.
    Non-CJK spans are approximated by length/4, which is the usual OpenAI-style
    fallback. This is intentionally conservative and deterministic.
    """
    if not text:
        return 0

    cjk_count = len(_CJK_RE.findall(text))
    ascii_token_estimate = 0
    for match in _ASCII_WORD_RE.finditer(text):
        ascii_token_estimate += max(1, math.ceil(len(match.group(0)) / 4))

    punctuation_and_space = len(_ASCII_WORD_RE.sub("", _CJK_RE.sub("", text)).strip())
    punctuation_estimate = math.ceil(punctuation_and_space / 4) if punctuation_and_space else 0
    return cjk_count + ascii_token_estimate + punctuation_estimate


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _pick_number(data: dict[str, Any], names: tuple[str, ...]) -> int | None:
    for name in names:
        value = _to_int(data.get(name))
        if value is not None:
            return value
    return None


def _merge_usage_dicts(*items: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for item in items:
        if isinstance(item, dict):
            merged.update(item)
            for nested_name in ("token_usage", "usage", "usage_metadata"):
                nested = item.get(nested_name)
                if isinstance(nested, dict):
                    merged.update(nested)
    return merged


def extract_provider_usage(model_message: Any) -> dict[str, int | None]:
    """Normalize LangChain/OpenAI-compatible usage fields from a model result."""
    usage = _merge_usage_dicts(
        getattr(model_message, "usage_metadata", None),
        getattr(model_message, "response_metadata", None),
    )
    if not usage and isinstance(model_message, dict):
        usage = _merge_usage_dicts(model_message)

    input_tokens = _pick_number(usage, ("input_tokens", "prompt_tokens", "prompt"))
    output_tokens = _pick_number(
        usage,
        ("output_tokens", "completion_tokens", "generated_tokens", "completion"),
    )
    total_tokens = _pick_number(usage, ("total_tokens", "total"))
    if total_tokens is None and (input_tokens is not None or output_tokens is not None):
        total_tokens = (input_tokens or 0) + (output_tokens or 0)

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


@dataclass
class TokenUsageTracker:
    completion_tokens_estimated: int = 0
    completion_chars: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    source: str = "estimated"
    model_rounds: int = 0
    _seen_provider_usage: bool = False
    _text_fragments: list[str] = field(default_factory=list)

    def add_generated_text(self, text: str) -> None:
        if not text:
            return
        self._text_fragments.append(text)
        self.completion_chars += len(text)
        self.completion_tokens_estimated += estimate_text_tokens(text)
        if self._seen_provider_usage:
            self.source = "mixed"

    def add_model_usage(self, model_message: Any) -> None:
        usage = extract_provider_usage(model_message)
        if not any(value is not None for value in usage.values()):
            return

        self.model_rounds += 1
        self._seen_provider_usage = True
        self.input_tokens = (self.input_tokens or 0) + (usage["input_tokens"] or 0)
        self.output_tokens = (self.output_tokens or 0) + (usage["output_tokens"] or 0)
        self.total_tokens = (self.total_tokens or 0) + (usage["total_tokens"] or 0)
        self.source = "mixed" if self.completion_tokens_estimated else "provider"

    def snapshot(self, *, final: bool = False) -> dict[str, Any]:
        return {
            "completion_tokens_estimated": self.completion_tokens_estimated,
            "completion_chars": self.completion_chars,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "source": self.source,
            "final": final,
            "model_rounds": self.model_rounds,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Estimate generated token count for text.")
    parser.add_argument("--text", help="Text to estimate. Reads stdin when omitted.")
    args = parser.parse_args()
    text = args.text if args.text is not None else sys.stdin.read()
    print(json.dumps({"tokens_estimated": estimate_text_tokens(text), "chars": len(text)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
