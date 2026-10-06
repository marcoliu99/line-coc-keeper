"""Which printed Luck values survive PDF extraction, and why the others are dropped.

The model reports a Luck value with the page and a verbatim quote. The extractor keeps
the value only when the quote states it and belongs to that investigator; otherwise the
owning player rolls Luck (docs/specs/feature/pregen_luck_roll_design_spec.md).
"""
from __future__ import annotations

import logging
from typing import Any
from unittest.mock import Mock, patch

import pytest

from app import pregen_extractor


def _card(name: str, luck: int, page: int, excerpt: str) -> dict[str, Any]:
    return {"name": name, "luck": luck, "luck_source_page": page, "luck_source_excerpt": excerpt}


def _sheet(name: str, luck: int, page: int, excerpt: str, **stats: int) -> dict[str, Any]:
    return {**_card(name, luck, page, excerpt), **stats}


ALICE = {"str_": 50, "con": 60, "siz": 55, "dex": 70}
BOB = {"str_": 80, "con": 45, "siz": 60, "dex": 50}
ALICE_STATS = "STR 50 CON 60 SIZ 55 DEX 70"
BOB_STATS = "STR 80 CON 45 SIZ 60 DEX 50"


def _extract(text: str, pregens: list[dict[str, Any]]) -> dict[str, int | None]:
    provider = Mock(analyze_text=Mock(return_value={"pregens": pregens}))
    with patch.object(pregen_extractor, "analysis_provider", return_value=provider):
        return {p["name"]: p.get("luck") for p in pregen_extractor.extract_pregens(text)}


@pytest.mark.parametrize(("label", "text", "pregens", "expected"), [
    ("name then Luck on the same page",
     "--- 第 1 頁 ---\nName: Alice\nSTR 50 CON 50\nLUCK 55\n",
     [_card("Alice", 55, 1, "LUCK 55")], {"Alice": 55}),
    ("Chinese label",
     "--- 第 1 頁 ---\n姓名：林文\n幸運 60\n",
     [_card("林文", 60, 1, "幸運 60")], {"林文": 60}),
    ("a colon in the quote that the page does not print",
     "--- 第 1 頁 ---\nName: Alice\nLuck 55\n",
     [_card("Alice", 55, 1, "Luck: 55")], {"Alice": 55}),
    ("lower case and line breaks in the quote",
     "--- 第 1 頁 ---\nName: Alice\nLUCK\n  55\n",
     [_card("Alice", 55, 1, "luck 55")], {"Alice": 55}),
    ("two sheets on a page print the same Luck and the quote is only the label",
     "--- 第 1 頁 ---\nName: Alice\nLUCK 55\nName: Bob\nLUCK 55\n",
     [_card("Alice", 55, 1, "LUCK 55"), _card("Bob", 55, 1, "LUCK 55")], {"Alice": 55, "Bob": 55}),
    ("a sheet that runs over a page break",
     "--- 第 1 頁 ---\nName: Alice\nSTR 50\n--- 第 2 頁 ---\nLUCK 55\n",
     [_card("Alice", 55, 2, "LUCK 55")], {"Alice": 55}),
    ("Luck of the second sheet right after the first sheet's page",
     "--- 第 1 頁 ---\nName: Alice\nLUCK 40\n--- 第 2 頁 ---\nName: Bob\nLUCK 70\n",
     [_card("Alice", 40, 1, "LUCK 40"), _card("Bob", 70, 2, "LUCK 70")], {"Alice": 40, "Bob": 70}),
    ("the only investigator, Luck printed before the name",
     "--- 第 1 頁 ---\nLUCK 55\nName: Alice\n",
     [_card("Alice", 55, 1, "LUCK 55")], {"Alice": 55}),
    ("the only investigator, name spelt differently by the model",
     "--- 第 1 頁 ---\nName: Alice\nLUCK 55\n",
     [_card("艾莉絲", 55, 1, "LUCK 55")], {"艾莉絲": 55}),
    ("two sheets, each Luck printed before its name, told apart by their characteristics",
     f"--- 第 1 頁 ---\nLUCK 55\n{ALICE_STATS}\nName: Alice\n--- 第 2 頁 ---\nLUCK 40\n{BOB_STATS}\nName: Bob\n",
     [_sheet("Alice", 55, 1, "LUCK 55", **ALICE), _sheet("Bob", 40, 2, "LUCK 40", **BOB)],
     {"Alice": 55, "Bob": 40}),
    ("two sheets, names spelt differently by the model, told apart by their characteristics",
     f"--- 第 1 頁 ---\nName: Alice\n{ALICE_STATS}\nLUCK 55\n--- 第 2 頁 ---\nName: Bob\n{BOB_STATS}\nLUCK 40\n",
     [_sheet("艾莉絲", 55, 1, "LUCK 55", **ALICE), _sheet("鮑伯", 40, 2, "LUCK 40", **BOB)],
     {"艾莉絲": 55, "鮑伯": 40}),
    ("Chinese characteristic labels",
     ("--- 第 1 頁 ---\n幸運 55\n力量 50 體質 60 體型 55 敏捷 70\n姓名 艾莉絲\n"
      "--- 第 2 頁 ---\n幸運 40\n力量 80 體質 45 體型 60 敏捷 50\n姓名 鮑伯\n"),
     [_sheet("艾莉絲", 55, 1, "幸運 55", **ALICE), _sheet("鮑伯", 40, 2, "幸運 40", **BOB)],
     {"艾莉絲": 55, "鮑伯": 40}),
    ("another investigator's name sits between the name and the Luck, characteristics decide",
     f"--- 第 1 頁 ---\nName: Alice (sister of Bob)\n{ALICE_STATS}\nLUCK 55\n",
     [_sheet("Alice", 55, 1, "LUCK 55", **ALICE), _sheet("Bob", 40, 1, "x", **BOB)], {"Alice": 55, "Bob": None}),
])
def test_a_printed_luck_that_belongs_to_the_sheet_is_kept(label, text, pregens, expected):
    assert _extract(text, pregens) == expected, label


@pytest.mark.parametrize(("label", "text", "pregens", "reason"), [
    ("the model's value differs from the quote",
     "--- 第 1 頁 ---\nName: Alice\nLUCK 55\n",
     [_card("Alice", 60, 1, "LUCK 55")], "excerpt_does_not_state_the_value"),
    ("the quote is not on the cited page",
     "--- 第 1 頁 ---\nName: Alice\nLUCK 55\n--- 第 2 頁 ---\nnothing\n",
     [_card("Alice", 55, 2, "LUCK 55")], "excerpt_not_on_cited_page"),
    ("no page or quote reported",
     "--- 第 1 頁 ---\nName: Alice\nLUCK 55\n",
     [{"name": "Alice", "luck": 55}], "missing_page_or_excerpt"),
    ("two sheets, Luck printed before any name and no characteristics to tell them apart",
     "--- 第 1 頁 ---\nLUCK 55\nName: Alice\nLUCK 40\nName: Bob\n",
     [_card("Alice", 55, 1, "LUCK 55"), _card("Bob", 40, 1, "LUCK 40")], "no_occurrence_belongs_to_this_investigator"),
    ("two sheets, the model's name is not on the page and nothing else tells them apart",
     "--- 第 1 頁 ---\nName: Alice\nLUCK 55\nName: Bob\nLUCK 40\n",
     [_card("艾莉絲", 55, 1, "LUCK 55"), _card("Bob", 40, 1, "LUCK 40")], "no_occurrence_belongs_to_this_investigator"),
    ("two sheets close enough that both are in reach of the Luck are ambiguous, not guessed",
     f"--- 第 1 頁 ---\nName: Alice\n{ALICE_STATS}\nLUCK 55\nName: Bob\n{BOB_STATS}\nLUCK 40\n",
     [_sheet("艾莉絲", 55, 1, "LUCK 55", **ALICE), _sheet("鮑伯", 40, 1, "LUCK 40", **BOB)],
     "no_occurrence_belongs_to_this_investigator"),
    ("two sheets with identical characteristics cannot be told apart by them",
     f"--- 第 1 頁 ---\nLUCK 55\n{ALICE_STATS}\nName: Alice\nLUCK 40\n{ALICE_STATS}\nName: Bob\n",
     [_sheet("Alice", 55, 1, "LUCK 55", **ALICE), _sheet("Bob", 40, 1, "LUCK 40", **ALICE)],
     "no_occurrence_belongs_to_this_investigator"),
    ("the characteristics next to the Luck are another sheet's, whatever the name before it says",
     f"--- 第 1 頁 ---\nName: Alice\n{BOB_STATS}\nLUCK 55\nName: Bob\n",
     [_sheet("Alice", 55, 1, "LUCK 55", **ALICE), _sheet("Bob", 40, 1, "x", **BOB)],
     "no_occurrence_belongs_to_this_investigator"),
    ("a name early on the previous page followed by unrelated text says nothing about a Luck at the top of the next",
     "--- 第 1 頁 ---\nName: Alice\n" + "unrelated rules text. " * 40 + "\n--- 第 2 頁 ---\nLUCK 55\n--- 第 3 頁 ---\nName: Bob\n",
     [_card("Alice", 55, 2, "LUCK 55"), _card("Bob", 40, 3, "x")], "no_occurrence_belongs_to_this_investigator"),
    ("a Luck far down the page does not reach back to the name on the page before",
     "--- 第 1 頁 ---\nName: Alice\n--- 第 2 頁 ---\n" + "unrelated rules text. " * 40 + "LUCK 55\n--- 第 3 頁 ---\nName: Bob\n",
     [_card("Alice", 55, 2, "LUCK 55"), _card("Bob", 40, 3, "x")], "no_occurrence_belongs_to_this_investigator"),
    ("the same quote follows the same name twice",
     "--- 第 1 頁 ---\nName: Alice\nLUCK 55\nbackstory\nLUCK 55\n",
     [_card("Alice", 55, 1, "LUCK 55")], "several_occurrences_belong_to_this_investigator"),
])
def test_a_luck_that_cannot_be_attributed_is_dropped_so_the_player_rolls(label, text, pregens, reason, caplog):
    with caplog.at_level(logging.WARNING, logger=pregen_extractor.__name__):
        result = _extract(text, pregens)
    assert result[pregens[0]["name"]] is None, label
    assert reason in caplog.text, label


def test_a_blank_luck_stays_blank():
    assert _extract("--- 第 1 頁 ---\nName: Alice\nLUCK\n", [{"name": "Alice"}]) == {"Alice": None}


def test_the_drop_warning_shows_what_the_model_reported_and_quoted(caplog):
    card = _card("Alice", 55, 1, "STR 50 CON 60 only, no luck label here")
    with caplog.at_level(logging.WARNING, logger=pregen_extractor.__name__):
        result = _extract("--- 第 1 頁 ---\nName: Alice\nLUCK 55\n", [card])
    assert result == {"Alice": None}
    assert "excerpt_does_not_state_the_value" in caplog.text
    assert "reported 55" in caplog.text and "no luck label here" in caplog.text
