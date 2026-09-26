"""Turn a Pydantic model into the JSON Schema dialect structured outputs accept.

The API constrains generation with a grammar and supports a subset of JSON Schema: no
numeric bounds, string lengths, patterns or array sizes. We strip those keywords for the
request and keep the full Pydantic model for client-side validation, so anything the
grammar could not enforce is caught by the repair loop instead of being silently ignored.
"""

from typing import Any

from pydantic import BaseModel

UNSUPPORTED_KEYWORDS = frozenset(
    {
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
        "minItems",
        "maxItems",
        "uniqueItems",
        "default",
        "title",
        "examples",
    }
)

# ``uuid`` is not a format the grammar knows; the model still produces UUID strings
# because the field description says so and the repair loop catches misses.
UNSUPPORTED_FORMATS = frozenset({"uuid", "uuid4"})


def _clean(node: Any) -> Any:
    if isinstance(node, dict):
        cleaned: dict[str, Any] = {}
        for key, value in node.items():
            if key in UNSUPPORTED_KEYWORDS:
                continue
            if key == "format" and value in UNSUPPORTED_FORMATS:
                continue
            cleaned[key] = _clean(value)
        if cleaned.get("type") == "object" and "properties" in cleaned:
            cleaned["additionalProperties"] = False
            cleaned["required"] = sorted(cleaned["properties"])
        return cleaned
    if isinstance(node, list):
        return [_clean(item) for item in node]
    return node


def api_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Schema for ``output_schema``: every object closed, every property required."""
    result: dict[str, Any] = _clean(model.model_json_schema())
    return result
