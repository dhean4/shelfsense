import pytest

from shelfsense_api.config import Settings
from shelfsense_api.guardrails import (
    ALL_TOOLS,
    ActionKind,
    review_decision,
    tools_for_role,
)
from shelfsense_api.pii import scrub


def _settings() -> Settings:
    return Settings(review_confidence_threshold=0.7, review_cost_limit_kobo=500_000)


def test_tools_by_role() -> None:
    assert tools_for_role("owner") == ALL_TOOLS
    assert "dispatch_technician" not in tools_for_role("field_agent")
    assert "create_reorder" in tools_for_role("field_agent")
    assert tools_for_role("reviewer") == {"get_inventory", "geocode", "submit_decisions"}
    assert tools_for_role("nobody") == frozenset()


def test_low_confidence_always_needs_review() -> None:
    d = review_decision(ActionKind.notify, 0, 0.5, "owner", _settings())
    assert d.required and "confidence" in (d.reason or "")


def test_cost_above_limit_needs_review() -> None:
    d = review_decision(ActionKind.reorder, 600_000, 0.9, "manager", _settings())
    assert d.required and "₦6,000" in (d.reason or "")


def test_cheap_confident_reorder_by_manager_is_auto_approved() -> None:
    d = review_decision(ActionKind.reorder, 100_000, 0.9, "manager", _settings())
    assert not d.required and d.reason is None


def test_field_agent_cannot_auto_approve_reorders() -> None:
    d = review_decision(ActionKind.reorder, 100_000, 0.9, "field_agent", _settings())
    assert d.required and "cannot auto-approve reorder" in (d.reason or "")


def test_escalations_always_need_review() -> None:
    d = review_decision(ActionKind.escalate, 0, 0.99, "owner", _settings())
    assert d.required


def test_dispatch_by_system_needs_review() -> None:
    d = review_decision(ActionKind.dispatch, 0, 0.99, "system", _settings())
    assert d.required


@pytest.mark.parametrize(
    "text, expected",
    [
        ("call Ada on 0803 123 4567 today", "call Ada on [phone] today"),
        ("mail ada.o@example.com or +234 803 123 4567", "mail [email] or [phone]"),
        ("account 1234567890123 is overdue", "account [number] is overdue"),
        ("order 24 units at 45000 kobo", "order 24 units at 45000 kobo"),
        (
            "sku 7f2b9a5e-3c41-4d6e-9b8a-5e1c2d3f4a5b out",
            "sku 7f2b9a5e-3c41-4d6e-9b8a-5e1c2d3f4a5b out",
        ),
    ],
)
def test_scrub(text: str, expected: str) -> None:
    assert scrub(text) == expected
