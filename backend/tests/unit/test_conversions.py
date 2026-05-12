"""Unit tests for :mod:`library.services.conversions`.

These cover the pure-function helpers shared across service modules — no
database, no proto wiring, no async. Each function is independently
testable, so a regression here will surface long before a flaky integration
test does.
"""

from __future__ import annotations

import pytest

from library.errors import InvalidArgument
from library.services.conversions import (
    DEFAULT_PAGE_SIZE,
    MAX_EMAIL_LENGTH,
    MAX_PAGE_SIZE,
    clamp_pagination,
    normalize_search,
    validate_email,
)


class TestValidateEmail:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("jai@example.com", "jai@example.com"),
            ("  jai@example.com  ", "jai@example.com"),
            ("Jai@Example.COM", "Jai@example.com"),  # domain lowercased, local preserved
            ("first.last+tag@sub.example.co", "first.last+tag@sub.example.co"),
            ("a_b-c@x-y.io", "a_b-c@x-y.io"),
        ],
    )
    def test_accepts_and_normalizes(self, raw: str, expected: str) -> None:
        assert validate_email(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            "hello",            # no @
            "a@",               # no domain
            "@b.com",           # no local
            "a@b",              # no TLD
            "a@b.",             # empty TLD
            "a@.com",           # empty domain label
            "a b@example.com",  # whitespace in local
            "a@exa mple.com",   # whitespace in domain
            "a@@b.com",         # double @
        ],
    )
    def test_rejects_invalid_shapes(self, raw: str) -> None:
        with pytest.raises(InvalidArgument):
            validate_email(raw)

    def test_rejects_too_long(self) -> None:
        # Build a syntactically valid but over-length address.
        local = "a" * (MAX_EMAIL_LENGTH - len("@example.com") + 1)
        raw = f"{local}@example.com"
        assert len(raw) > MAX_EMAIL_LENGTH
        with pytest.raises(InvalidArgument):
            validate_email(raw)


class TestClampPagination:
    def test_defaults_when_page_size_zero(self) -> None:
        assert clamp_pagination(page_size=0, offset=0) == (DEFAULT_PAGE_SIZE, 0)

    def test_clamps_to_max(self) -> None:
        assert clamp_pagination(page_size=MAX_PAGE_SIZE + 50, offset=10) == (
            MAX_PAGE_SIZE,
            10,
        )

    def test_rejects_negative_offset(self) -> None:
        with pytest.raises(InvalidArgument):
            clamp_pagination(page_size=10, offset=-1)

    def test_rejects_negative_page_size(self) -> None:
        with pytest.raises(InvalidArgument):
            clamp_pagination(page_size=-1, offset=0)


class TestNormalizeSearch:
    @pytest.mark.parametrize("raw", ["", "   ", "\t\n"])
    def test_empty_becomes_none(self, raw: str) -> None:
        assert normalize_search(raw) is None

    def test_strips_whitespace(self) -> None:
        assert normalize_search("  hobbit  ") == "hobbit"
