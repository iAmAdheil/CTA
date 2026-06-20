"""Token → dollar cost map for harness history.

Mechanical, no LLM. Prices are USD per *million* tokens, sourced from the
claude-api skill's model table (2026-06). Cache writes bill at 1.25x the input
rate (5-minute TTL — what the harness uses), cache reads at 0.1x — so we derive
both from the base input rate instead of hardcoding four numbers per model.

`cost_usd()` takes a usage dict shaped like a Claude Code transcript's
`message.usage` (input_tokens / output_tokens / cache_creation_input_tokens /
cache_read_input_tokens) plus a model id, and returns dollars. Unknown models
fall back to `None` cost (callers surface tokens-only) so a new model id never
silently mis-prices a run.
"""

from __future__ import annotations

from typing import Any

# Base per-MTok rates: (input, output). Cache write = input*1.25, read = input*0.1.
_BASE: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.0, 50.0),
    "claude-mythos-5": (10.0, 50.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-opus-4-5": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

# Short aliases the harness uses in task files / orchestrator-state (model: opus).
_ALIAS = {
    "opus": "claude-opus-4-8",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5",
    "fable": "claude-fable-5",
}

_CACHE_WRITE_MULT = 1.25
_CACHE_READ_MULT = 0.10


def canonical_model(model: str | None) -> str | None:
    """Resolve a short alias or full id to a known pricing key, else return as-is."""
    if not model:
        return None
    m = model.strip()
    if m in _BASE:
        return m
    if m in _ALIAS:
        return _ALIAS[m]
    # tolerate a date suffix (claude-opus-4-8-20260101) by prefix match
    for key in _BASE:
        if m.startswith(key):
            return key
    return m


def rates(model: str | None) -> dict[str, float] | None:
    """Per-MTok rate dict for a model, or None if unknown."""
    key = canonical_model(model)
    if key not in _BASE:
        return None
    inp, out = _BASE[key]
    return {
        "input": inp,
        "output": out,
        "cache_write": round(inp * _CACHE_WRITE_MULT, 4),
        "cache_read": round(inp * _CACHE_READ_MULT, 4),
    }


def cost_usd(usage: dict[str, Any], model: str | None) -> float | None:
    """Dollars for one usage dict + model. None if the model isn't priced."""
    r = rates(model)
    if r is None:
        return None
    inp = usage.get("input_tokens", 0) or 0
    out = usage.get("output_tokens", 0) or 0
    cw = usage.get("cache_creation_input_tokens", 0) or 0
    cr = usage.get("cache_read_input_tokens", 0) or 0
    dollars = (
        inp * r["input"]
        + out * r["output"]
        + cw * r["cache_write"]
        + cr * r["cache_read"]
    ) / 1_000_000
    return round(dollars, 6)
