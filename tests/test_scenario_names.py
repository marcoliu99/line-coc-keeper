"""A person or place reads the same on every turn: the index's Chinese name, never its English original or a fresh
translation (2026-10-12: names drifted between turns of one run)."""
from __future__ import annotations

from app import presentation
from app.agents import reply_pipeline
from app.models import GroupState
from app.prompt_builder import build_static_prompt

CORBITT = {"name": "柯比特", "aliases": ["Walter Corbitt", "Corbitt", "華特"]}
HOUSE = {"name": "柯比特宅", "aliases": ["Corbitt House"]}


def test_an_english_original_is_written_as_the_index_name():
    text = "Walter Corbitt 的身影從 Corbitt House 的地窖升起，corbitt's 笑聲迴盪。"
    assert presentation.use_scenario_names(text, [CORBITT, HOUSE]) == "柯比特 的身影從 柯比特宅 的地窖升起，柯比特的 笑聲迴盪。"


def test_only_what_the_index_lists_and_names_once_changes():
    text = "Corbittson 和 Al 以及 Deep One 都在。"
    entries = [CORBITT, {"name": "艾爾", "aliases": ["Al"]},  # too short to be a name reliably
               {"name": "深潛者", "aliases": ["Deep One"]}, {"name": "深潛者（幼體）", "aliases": ["Deep One"]}]
    assert presentation.use_scenario_names(text, entries) == text  # a longer word, a short alias, two candidates


def test_an_investigator_s_own_name_is_never_rewritten():
    entry = {"name": "卡特", "aliases": ["Carter"]}
    text = "Malcolm Carter 推開門，Carter 先生點頭。"
    assert presentation.use_scenario_names(text, [entry], keep=["Malcolm Carter"]) == text


def test_the_prompt_asks_for_the_index_names():
    state = GroupState(group_id="g", scenario_npc_index=[CORBITT], scenario_location_index=[HOUSE])
    prompt = build_static_prompt(state)
    assert "## 譯名" in prompt and "每次都一樣" in prompt
    assert "## 譯名" not in build_static_prompt(GroupState(group_id="g"))


def test_names_cover_what_delivery_rendered():
    names = [step.name for step in reply_pipeline.STEPS]
    assert names.index("finalize") < names.index("scenario_names") < names.index("player_text")
    assert next(step for step in reply_pipeline.STEPS if step.name == "scenario_names").display_only


def test_a_name_inside_quoted_english_is_left_as_written():
    will = {"name": "威爾", "aliases": ["Will"]}
    assert presentation.use_scenario_names("紙條寫著：「Will he return?」", [will]) == "紙條寫著：「Will he return?」"
    assert presentation.use_scenario_names("Will 推開了門。", [will]) == "威爾 推開了門。"


def _told(state: GroupState, text: str) -> GroupState:
    state.log.append({"role": "assistant", "content": text, "audience": "public", "timeline_id": state.timeline_id or "",
                      "record_kind": "narrative"})
    return state


def test_a_private_message_reads_the_same_names():
    from unittest.mock import MagicMock
    state = _told(GroupState(group_id="g", scenario_npc_index=[CORBITT]), "柯比特出現了。")
    draft = MagicMock(text="Corbitt 在看你。\n線索：Corbitt 的日記。", private_messages=[("p1", "只有你注意到 Corbitt 的手在動。")],
                      private_controls=[("p1", "Corbitt 的檢定")])
    ctx = MagicMock(state=state)
    ctx.message.payload = {"state": state}
    reply_pipeline._scenario_names(ctx, draft)
    assert draft.text == "柯比特 在看你。\n線索：柯比特 的日記。"
    assert draft.private_messages == [("p1", "只有你注意到 柯比特 的手在動。")]
    assert draft.private_controls == [("p1", "柯比特 的檢定")]


def test_an_alias_with_digits_or_a_comma_is_used_too():
    entries = [{"name": "三號房", "aliases": ["Room 3"]}, {"name": "幼體（深海夢魘）", "aliases": ["Youngling, deep-sea nightmare"]}]
    assert presentation.use_scenario_names("他走進 Room 3。", entries) == "他走進 三號房。"
    assert presentation.use_scenario_names("那是 Youngling, deep-sea nightmare。", entries) == "那是 幼體（深海夢魘）。"


def test_a_numbered_alias_does_not_match_a_longer_number():
    room = {"name": "三號房", "aliases": ["Room 3"]}
    assert presentation.use_scenario_names("他走進 Room 30。", [room]) == "他走進 Room 30。"


def test_an_older_index_with_the_english_heading_as_its_name_still_works():
    entry = {"name": "Walter Corbitt", "aliases": ["柯比特", "Corbitt"]}
    assert presentation.use_scenario_names("Walter Corbitt 與 Corbitt 都是他。", [entry]) == "柯比特 與 柯比特 都是他。"


def test_a_name_that_would_reveal_a_secret_is_left_in_english():
    from unittest.mock import MagicMock, patch

    from app import spoiler_policy
    state = _told(GroupState(group_id="g", scenario_npc_index=[CORBITT]), "柯比特出現了。")
    ctx = MagicMock(state=state)
    ctx.message.payload = {"state": state}
    draft = MagicMock(text="你發現Corbitt。", private_messages=[("p1", "Corbitt 看著你。")], private_controls=[])
    with patch.object(spoiler_policy, "collect_protected_terms", return_value=["發現柯比特"]):
        reply_pipeline._scenario_names(ctx, draft)
    assert draft.text == "你發現Corbitt。" and draft.private_messages == [("p1", "Corbitt 看著你。")]


def test_an_older_english_headed_entry_is_listed_by_its_chinese_name():
    state = GroupState(group_id="g", scenario_npc_index=[{"name": "Walter Corbitt", "aliases": ["柯比特", "Corbitt"]}])
    assert "・柯比特（別名：Walter Corbitt、Corbitt）" in build_static_prompt(state)


def test_an_alias_that_is_also_game_text_is_never_used():
    entry = {"name": "德克斯", "aliases": ["Dex", "Luck"]}
    assert presentation.use_scenario_names("請做 DEX 檢定，或按 Luck 按鈕。", [entry]) == "請做 DEX 檢定，或按 Luck 按鈕。"


def test_a_disguise_is_never_rewritten_into_the_identity_it_hides():
    from unittest.mock import MagicMock
    culprit = {"name": "兇手約翰", "aliases": ["Masked Man"]}
    state = GroupState(group_id="g", scenario_npc_index=[culprit])
    ctx = MagicMock(state=state)
    ctx.message.payload = {"state": state}
    draft = MagicMock(text="走廊上站著 Masked Man。", private_messages=[], private_controls=[])
    reply_pipeline._scenario_names(ctx, draft)
    assert draft.text == "走廊上站著 Masked Man。"  # never told: the name stays hidden
    _told(state, "原來他就是兇手約翰。")
    reply_pipeline._scenario_names(ctx, draft)
    assert draft.text == "走廊上站著 兇手約翰。"  # once revealed, it is the name every time
    assert "身分還沒揭露的人物" in build_static_prompt(state)


def test_an_accented_alias_is_used_too():
    entries = [{"name": "何內", "aliases": ["René"]}, {"name": "慕尼黑", "aliases": ["München"]}]
    assert presentation.use_scenario_names("René 來自 München。", entries) == "何內 來自 慕尼黑。"


def test_a_name_inside_a_longer_index_name_is_not_a_disclosure():
    from unittest.mock import MagicMock
    culprit = {"name": "約翰", "aliases": ["Masked Man"]}
    other = {"name": "約翰遜", "aliases": ["Johnson"]}
    state = _told(GroupState(group_id="g", scenario_npc_index=[culprit, other]), "約翰遜先生向你點頭。")
    ctx = MagicMock(state=state)
    ctx.message.payload = {"state": state}
    draft = MagicMock(text="Masked Man 站在門口。", private_messages=[("p1", "只有你知道 約翰 是誰；Masked Man 在看你。")],
                      private_controls=[])
    reply_pipeline._scenario_names(ctx, draft)
    assert draft.text == "Masked Man 站在門口。"
    assert draft.private_messages == [("p1", "只有你知道 約翰 是誰；約翰 在看你。")]  # told to that reader only


def test_an_alias_two_entries_share_stays_ambiguous_when_only_one_is_told():
    first, second = {"name": "守衛甲", "aliases": ["Guard"]}, {"name": "守衛乙", "aliases": ["Guard"]}
    assert presentation.use_scenario_names("Guard 走過來。", [first, second], told={"守衛甲"}) == "Guard 走過來。"


def test_a_reader_told_a_name_in_one_private_line_reads_it_in_the_next():
    from unittest.mock import MagicMock
    culprit = {"name": "約翰", "aliases": ["Masked Man"]}
    state = GroupState(group_id="g", scenario_npc_index=[culprit])
    ctx = MagicMock(state=state)
    ctx.message.payload = {"state": state}
    draft = MagicMock(text="門開了。", private_messages=[("p1", "約翰是兇手。"), ("p1", "Masked Man 離開了。"),
                                                         ("p2", "Masked Man 在看你。")], private_controls=[])
    reply_pipeline._scenario_names(ctx, draft)
    assert draft.private_messages == [("p1", "約翰是兇手。"), ("p1", "約翰 離開了。"), ("p2", "Masked Man 在看你。")]


def test_an_index_name_an_investigator_shares_is_never_written_in():
    culprit = {"name": "約翰", "aliases": ["Masked Man"]}
    assert presentation.use_scenario_names("Masked Man 站在門口。", [culprit], keep=["約翰"], told={"約翰"}) == \
        "Masked Man 站在門口。"


def test_an_investigator_whose_name_contains_an_npc_name_does_not_tell_it():
    from unittest.mock import MagicMock
    culprit = {"name": "約翰", "aliases": ["Masked Man"]}
    state = _told(GroupState(group_id="g", scenario_npc_index=[culprit]), "約翰·史密斯推開了門。")
    state.pregens = [{"name": "約翰·史密斯"}]
    ctx = MagicMock(state=state)
    ctx.message.payload = {"state": state}
    draft = MagicMock(text="Masked Man 站在門口。", private_messages=[], private_controls=[])
    reply_pipeline._scenario_names(ctx, draft)
    assert draft.text == "Masked Man 站在門口。"


def test_a_one_character_name_is_never_counted_as_told():
    from app.services import turn_fallback
    king = {"name": "王", "aliases": ["Masked Man"]}
    state = _told(GroupState(group_id="g", scenario_npc_index=[king]), "國王的畫像掛在牆上。")
    assert turn_fallback.told_names(state, [king]) == set()


def test_an_alias_that_is_a_skill_name_is_never_used():
    dodge = {"name": "道奇", "aliases": ["Dodge", "Mr Dodge"]}
    assert presentation.use_scenario_names("請做 Dodge 檢定。", [dodge]) == "請做 Dodge 檢定。"
    assert presentation.use_scenario_names("Mr Dodge 走進來。", [dodge]) == "道奇 走進來。"


def test_an_uppercase_accented_alias_never_breaks_the_reply():
    entries = [{"name": "伊席克", "aliases": ["Işık"]}]
    assert presentation.use_scenario_names("IŞIK 走進來。", entries) == "伊席克 走進來。"


def test_an_apostrophe_only_possessive_reads_as_possessive():
    entries = [{"name": "詹姆斯", "aliases": ["James"]}]
    assert presentation.use_scenario_names("James’ 外套掛在門邊。", entries) == "詹姆斯的 外套掛在門邊。"


def test_a_private_line_before_the_reveal_keeps_the_disguise():
    from unittest.mock import MagicMock
    culprit = {"name": "約翰", "aliases": ["Masked Man"]}
    state = GroupState(group_id="g", scenario_npc_index=[culprit])
    ctx = MagicMock(state=state)
    ctx.message.payload = {"state": state}
    draft = MagicMock(text="門開了。", private_messages=[("p1", "Masked Man 靠近。"), ("p1", "你認出他是約翰。")],
                      private_controls=[])
    reply_pipeline._scenario_names(ctx, draft)
    assert draft.private_messages == [("p1", "Masked Man 靠近。"), ("p1", "你認出他是約翰。")]


def test_a_reveal_late_in_the_reply_does_not_rewrite_what_came_before():
    from unittest.mock import MagicMock
    culprit = {"name": "約翰", "aliases": ["Masked Man"]}
    state = GroupState(group_id="g", scenario_npc_index=[culprit])
    ctx = MagicMock(state=state)
    ctx.message.payload = {"state": state}
    draft = MagicMock(text="Masked Man 靠近。你認出他是約翰。Masked Man 笑了。", private_messages=[], private_controls=[])
    reply_pipeline._scenario_names(ctx, draft)
    assert draft.text == "Masked Man 靠近。你認出他是約翰。約翰 笑了。"


def test_two_entries_with_one_chinese_name_are_never_written_in():
    told_one = {"name": "約翰", "aliases": ["John Carter"]}
    hidden = {"name": "約翰", "aliases": ["Masked Man"]}
    assert presentation.use_scenario_names("Masked Man 站在門口。", [told_one, hidden], told={"約翰"}) == "Masked Man 站在門口。"
