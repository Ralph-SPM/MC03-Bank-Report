"""Focused tests for prohibited-marker remark sanitization."""

from mc03.domain.sanitizer import sanitize_remark


def test_sanitize_removes_prohibited_markers_and_collapses_result() -> None:
    remark = (
        "Reached borrower BCAL-CH123456\n"
        "L3: INB OBD PH: +639171234567 https://example.com/case "
        "[SRC:volare] today"
    )

    assert sanitize_remark(remark) == "Reached borrower today"


def test_sanitize_handles_attached_marker_forms() -> None:
    assert sanitize_remark("BCAL_CH-ABC L3 INB OBD PH123 SRC") == ""
    assert sanitize_remark("<L3> [SRC:internal]") == ""


def test_sanitize_removes_bare_and_www_com_links() -> None:
    assert sanitize_remark("See www.example.com/path and example.com.") == "See and"


def test_clean_remark_is_preserved_character_for_character() -> None:
    clean = "Clean\nremark\twith  spacing."

    assert sanitize_remark(clean) == clean


def test_sanitize_returns_empty_for_empty_or_marker_only_input() -> None:
    assert sanitize_remark("") == ""
    assert sanitize_remark(" \t\n ") == ""
    assert sanitize_remark("BCAL-CH123 L3 INB OBD PH123 SRC") == ""


def test_sanitize_is_idempotent() -> None:
    remark = "Contact BCAL CH123 and visit https://example.com SRC"
    sanitized = sanitize_remark(remark)

    assert sanitize_remark(sanitized) == sanitized
