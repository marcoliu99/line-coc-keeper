"""A retrieved scenario chunk brings along the neighbour it visibly continues into, and nothing more."""
from __future__ import annotations

import pytest

from app import scenario_adjacency as adjacency
from app import scenario_rag
from app.scenario_rag import ScenarioIndex, _Chunk


def _pad(sentence: str, filler: str) -> str:
    """A paragraph near the chunk size, so each paragraph becomes a chunk of its own."""
    return sentence + filler * max(0, (330 - len(sentence)) // len(filler))


def _index(*pages: list[str]) -> ScenarioIndex:
    text = "\n\n".join(f"--- 第 {number} 頁 ---\n" + "\n\n".join(paragraphs)
                       for number, paragraphs in enumerate(pages, 1))
    return scenario_rag.build_index(text)


@pytest.fixture(autouse=True)
def lexical_only(monkeypatch):
    monkeypatch.setattr(scenario_rag, "_embed_texts", lambda *args, **kwargs: None)


RECEPTION = [
    _pad("接待處的櫃檯上放著一個黃銅服務鈴。", "木地板發出輕微的聲響。"),
    _pad("若有人敲擊桌上的金屬器物，經理哈羅德隨即從後間現身，詢問是否登記住宿。", "他的圍裙沾著麵粉。"),
    _pad("如果調查員下到地下室，會發現一扇上鎖的鐵門。", "潮濕的牆面長著青苔。"),
]


def test_a_hit_is_joined_by_the_consequence_that_opens_the_next_chunk() -> None:
    index = _index(RECEPTION)
    chunks = [c.text for c in index.chunks]
    assert len(chunks) == 3  # the fixture really is three chunks

    rows = scenario_rag.search(index, "我按下服務鈴", top_k=1)

    assert len(rows) == 1 and "哈羅德" in rows[0]["text"]
    assert rows[0]["adjacent_chunks"] == [{"side": "next", "page": 1, "reason": "next_opens_with_consequence"}]


def test_the_chunk_after_the_consequence_is_not_exposed() -> None:
    """Chunk N+2 opens with a condition as well, but only the first neighbour travels with the hit."""
    rows = scenario_rag.search(_index(RECEPTION), "我按下服務鈴", top_k=1)
    assert "地下室" not in rows[0]["text"] and "鐵門" not in rows[0]["text"]


def test_a_hit_that_opens_with_a_condition_brings_the_chunk_that_names_the_trigger() -> None:
    rows = scenario_rag.search(_index(RECEPTION), "經理哈羅德 詢問 登記住宿", top_k=1)
    assert rows[0]["adjacent_chunks"] == [{"side": "previous", "page": 1, "reason": "hit_opens_with_consequence"}]
    assert rows[0]["text"].index("服務鈴") < rows[0]["text"].index("哈羅德")
    assert "地下室" not in rows[0]["text"]


def test_a_self_contained_hit_is_not_expanded_because_a_neighbour_exists() -> None:
    page = [
        _pad("接待處的櫃檯上放著一個黃銅服務鈴。", "木地板發出輕微的聲響。"),
        _pad("牆上掛著一幅褪色的風景畫。", "畫框的角落積了灰塵。"),
    ]
    rows = scenario_rag.search(_index(page), "我按下服務鈴", top_k=1)
    assert "風景畫" not in rows[0]["text"] and "adjacent_chunks" not in rows[0]


def test_a_chunk_cut_in_the_middle_of_a_sentence_is_completed() -> None:
    page = [
        _pad("接待處的櫃檯上放著一個黃銅服務鈴，鈴旁貼著一張字條，字條上寫著", "木地板發出輕微的聲響"),
        _pad("請按鈴等候，經理哈羅德會立刻出來接待。", "他的圍裙沾著麵粉。"),
    ]
    rows = scenario_rag.search(_index(page), "我按下服務鈴", top_k=1)
    assert "哈羅德" in rows[0]["text"] and rows[0]["adjacent_chunks"][0]["reason"] == "hit_ends_mid_sentence"


def test_the_mechanism_is_generic_and_works_in_another_scenario_and_language() -> None:
    page = [
        _pad("A rusted bell hangs beside the lighthouse door.", " Gulls cry over the grey water."),
        _pad("If the bell is rung, the keeper opens the hatch and asks what the visitors want.", " His lamp swings low."),
        _pad("When the tide turns, the cellar floods to the third step.", " Salt crusts the stones."),
    ]
    rows = scenario_rag.search(_index(page), "I ring the bell", top_k=1)
    assert "keeper opens the hatch" in rows[0]["text"] and "cellar floods" not in rows[0]["text"]


def test_a_neighbour_two_pages_away_is_not_attached() -> None:
    first = [_pad("The desk has a brass bell and a ledger that is", " Dust lies on the shelf.")]
    last = [_pad("If someone rings it the clerk comes out.", " The clock ticks.")]
    index = _index(first, [_pad("A different page about gardens and sheds.", " Roses grow wild.")], last)
    rows = scenario_rag.search(index, "ring brass bell", top_k=1)
    assert "clerk comes out" not in rows[0]["text"]


def test_a_neighbour_the_reader_may_not_see_is_never_attached() -> None:
    hit = _Chunk(page=1, text=_pad("The desk has a brass bell.", " Dust."), visibility="public")
    secret = _Chunk(page=1, text=_pad("If the bell is rung the hidden cult arrives.", " Dust."), visibility="kp_only")
    index = ScenarioIndex(chunks=[hit, secret], doc_freq={}, avg_length=1.0, text_hash="x")
    scenario_rag._compute_bm25_stats(index.chunks)
    rows = scenario_rag._result_rows([(1.0, hit)], 1, [hit], index=index, query_tokens=["bell", "ring"])
    assert "cult" not in rows[0]["text"]


def test_the_expansion_can_be_switched_off(monkeypatch) -> None:
    monkeypatch.setattr(scenario_rag, "SCENARIO_RAG_ADJACENT_CHUNKS", 0)
    rows = scenario_rag.search(_index(RECEPTION), "我按下服務鈴", top_k=1)
    assert "哈羅德" not in rows[0]["text"]


def test_a_neighbour_that_is_itself_a_hit_is_not_listed_twice() -> None:
    rows = scenario_rag.search(_index(RECEPTION), "服務鈴 哈羅德", top_k=3)
    joined = "\n".join(row["text"] for row in rows)
    assert joined.count("哈羅德隨即從後間現身") == 1


def test_the_search_reports_how_many_neighbours_it_attached() -> None:
    metrics: dict[str, object] = {}
    scenario_rag.search(_index(RECEPTION), "我按下服務鈴", top_k=1, metrics=metrics)
    assert metrics["adjacent_chunk_count"] == 1


@pytest.mark.parametrize(("text", "expected"), [
    ("若有人敲擊桌上的金屬器物", True), ("If the bell is rung", True), ("When the tide turns", True),
    ("「如果調查員下到地下室」", True), ("牆上掛著一幅畫。", False), ("The desk is oak.", False),
])
def test_what_counts_as_a_consequence(text: str, expected: bool) -> None:
    assert adjacency.starts_with_consequence(text) is expected


def test_the_overlap_the_chunker_repeats_is_not_duplicated() -> None:
    previous = "一二三四五六七八九十。"
    following = "六七八九十。\n\n如果有人按鈴。"
    assert adjacency.new_text(previous, following) == "如果有人按鈴。"


def test_the_executor_is_told_to_use_a_scripted_consequence_before_inventing_a_check() -> None:
    from app.services import prompt_config

    policy = prompt_config.EXECUTOR_SCENARIO_RAG_POLICY
    assert "直接後果" in policy and "不得改以偵查、聆聽、幸運等臨時檢定取代" in policy
    assert "不要問「接下來會發生什麼」" in policy


def test_no_runtime_module_hard_codes_a_scenario_or_one_of_its_triggers() -> None:
    from pathlib import Path

    banned = ("camp_sunny", "camp sunny", "harold", "哈羅德", "reception bell", "red bucket", "severed hand", "紅色水桶")
    offenders = {
        str(path): word for path in (Path(__file__).resolve().parents[1] / "app").rglob("*.py")
        for word in banned if word in path.read_text(encoding="utf-8").lower()
    }
    assert offenders == {}


def test_a_neighbour_already_emitted_as_a_hit_is_not_attached_to_another_hit() -> None:
    """The following chunk outranks the open-ended one, so it is emitted first; the open-ended hit must not repeat it."""
    page = [
        _pad("The inscription says", " wind moves the lamp"),  # no full stop: the hit ends mid-sentence
        _pad("DANGER inscription: the hatch is trapped and the inscription glows.", " Chains rattle."),
    ]
    index = _index(page)
    first, second = index.chunks
    rows = scenario_rag._result_rows([(0.9, second), (0.5, first)], 5, index.chunks, index=index,
                                     query_tokens=["inscription"])
    assert [row["page"] for row in rows] == [1, 1] and "adjacent_chunks" not in rows[0]
    assert sum("hatch is trapped" in row["text"] for row in rows) == 1


def test_an_overlap_that_crosses_a_paragraph_break_is_still_removed() -> None:
    previous = "第一段結尾。\n\n第二段很短。"
    following = "第一段結尾。\n\n第二段很短。\n\n如果有人按鈴，經理出現。"
    assert adjacency.new_text(previous, following) == "如果有人按鈴，經理出現。"
    assert adjacency.new_text("沒有重疊。", "完全不同的新段落。") == "完全不同的新段落。"
