"""Source-bound duplicate and optionality regressions from the PR155 review."""
from __future__ import annotations

import pytest

from app import pdf_page_criticality as criticality


def classification(kind: str, **updates: object) -> dict:
    """Construct the current classifier wire shape, not production authority."""
    return {
        "page_role": kind,
        "contains_gameplay_source": False,
        "contains_mechanics": False,
        "contains_required_clue": False,
        "asset_only": True,
        "all_source_fragments_accounted_for": True,
        "source_fragments": [],
        "optional_source_quote": "",
        "optional_source_page": 0,
        **updates,
    }


@pytest.mark.parametrize(
    ("fragment", "source"),
    [
        ("Take 1d6 damage immediately.", "Do not take 1d6 damage immediately."),
        ("Open the cellar door immediately.", "Never open the cellar door immediately."),
    ],
)
def test_reference_duplicate_must_include_polarity_scope(fragment: str, source: str) -> None:
    # A phrase embedded in its negation is not an equivalent reference rule.
    assert criticality.reference_compatible(fragment, source) is False


def test_handout_duplicate_cannot_use_positive_substring_of_negative_source() -> None:
    fragment = "Take 1d6 damage immediately."
    source = "Do not take 1d6 damage immediately."
    safe = {2: source}
    result = criticality.decide(
        classification("handout", contains_gameplay_source=True, source_fragments=[fragment]),
        "", safe, safe,
    )
    assert result["page_role"] != "DUPLICATE_SOURCE"
    assert result["duplicate_source_present"] is False


def test_bound_optional_quote_does_not_override_mandatory_selection_elsewhere() -> None:
    permission = "Each player creates an investigator."
    safe = {
        2: permission,
        3: "Players must use the pre-generated investigators.",
    }
    assert criticality._mandatory_pregens(criticality._normalize(" ".join(safe.values())))
    result = criticality.decide(
        classification("pregen", optional_source_quote=permission, optional_source_page=2),
        "", safe, {2: permission},
    )
    # Reject this unsupported optionality decision, not necessarily the whole scenario.
    assert result["page_role"] != "OPTIONAL_PREGEN"
    assert result["source_critical"] is not False


def test_bound_quote_cannot_bypass_incomplete_page_coverage() -> None:
    permission = "Each player creates an investigator."
    safe = {2: permission}
    result = criticality.decide(
        classification(
            "pregen", optional_source_quote=permission, optional_source_page=2,
            all_source_fragments_accounted_for=False,
        ),
        "", safe, safe,
    )
    assert result["page_role"] != "OPTIONAL_PREGEN"
    assert result["source_critical"] is not False


def test_exact_positive_reference_counterpart_remains_supported() -> None:
    text = "Take 1d6 damage immediately."
    assert criticality.reference_compatible(text, text) is True


def test_complete_unrestricted_pregen_permission_remains_supported() -> None:
    permission = "Each player creates an investigator."
    safe = {2: permission}
    result = criticality.decide(
        classification("pregen", optional_source_quote=permission, optional_source_page=2),
        "", safe, safe,
    )
    assert result["page_role"] == "OPTIONAL_PREGEN"
    assert result["source_critical"] is False


@pytest.mark.parametrize("source", [
    "When the lamp is lit, take 1d6 damage immediately.",
    "The monster says: take 1d6 damage immediately.",
    "Do not\n take 1d6 damage immediately.",
])
def test_counterpart_requires_complete_subject_and_condition_scope(source):
    assert not criticality.reference_compatible("Take 1d6 damage immediately.", source)


def test_aligned_multiple_sentences_preserve_counterpart():
    fragment = "Take 1d6 damage immediately. Then wait for help."
    source = "The scene begins. Take 1d6 damage immediately. Then wait for help. The scene ends."
    assert criticality.reference_compatible(fragment, source)


def test_authored_optional_section_requires_complete_coverage():
    permission = "Each player creates an investigator."
    asset = {"kind": "pregen", "start_page": 4, "end_page": 8, "title_sha256": "section"}
    output = classification("pregen", all_source_fragments_accounted_for=False)
    assert criticality.optional_section({2: permission}, asset, output) is None
