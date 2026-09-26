"""USD cost of a call from token usage. Prices are per million tokens.

The table is a snapshot of Anthropic's first-party rates (2026-06); override it with
``SHELFSENSE_LLM_PRICING_JSON`` rather than editing code when prices move.
"""

import json
from dataclasses import dataclass

from shelfsense_api.llm.types import Usage


@dataclass(frozen=True)
class Price:
    """Per-MTok rates: input, output, cache read, cache write."""

    input: float
    output: float
    cache_read: float
    cache_write: float


DEFAULT_PRICES: dict[str, Price] = {
    "claude-fable-5-1": Price(10.0, 50.0, 0.25, 12.5),
    "claude-opus-5": Price(5.0, 25.0, 0.5, 6.25),
    "claude-opus-4-8": Price(5.0, 25.0, 0.5, 6.25),
    "claude-sonnet-5": Price(2.0, 10.0, 0.2, 2.5),
    "claude-haiku-4-5": Price(1.0, 5.0, 0.1, 1.25),
}


def load_prices(override_json: str | None) -> dict[str, Price]:
    """Built-in table merged with an optional JSON override ``{"model": [in, out, cr, cw]}``."""
    prices = dict(DEFAULT_PRICES)
    if override_json:
        for model, rates in json.loads(override_json).items():
            prices[model] = Price(*(float(r) for r in rates))
    return prices


def price_for(model: str, prices: dict[str, Price]) -> Price | None:
    """Exact match first, then the longest known prefix (dated snapshots, aliases)."""
    if model in prices:
        return prices[model]
    candidates = [known for known in prices if model.startswith(known)]
    if not candidates:
        return None
    return prices[max(candidates, key=len)]


def cost_usd(model: str, usage: Usage, prices: dict[str, Price]) -> float | None:
    """Dollar cost of ``usage`` on ``model``; ``None`` when the model is not priced."""
    price = price_for(model, prices)
    if price is None:
        return None
    per_tok = 1 / 1_000_000
    return round(
        usage.input_tokens * price.input * per_tok
        + usage.output_tokens * price.output * per_tok
        + usage.cache_read_tokens * price.cache_read * per_tok
        + usage.cache_write_tokens * price.cache_write * per_tok,
        6,
    )
