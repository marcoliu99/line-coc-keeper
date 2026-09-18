import tempfile
from pathlib import Path
import sys
import types
import unittest

sys.modules.setdefault("yaml", types.SimpleNamespace(YAMLError=Exception, safe_load=lambda data: {}))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda: None))
sys.modules.setdefault(
    "app.pdf_loader",
    types.SimpleNamespace(
        extract_text=lambda pdf_bytes: ("", [], False, {}, {}),
        guess_title=lambda text, file_name="": file_name or "Untitled",
        extract_preview=lambda pdf_bytes: "",
    ),
)

from app import combat, legacy_commands as commands, keeper
from app.commands import router
from app.models import Character, GroupState


def clone_state(state: GroupState) -> GroupState:
    return GroupState.from_dict(state.to_dict())


class ReplyCollector:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def __call__(self, text: str) -> None:
        self.messages.append(text)


class StateStorePatch:
    def __init__(self, *modules) -> None:
        self.modules = modules
        self.store: dict[str, GroupState] = {}
        self.originals = []

    def __enter__(self):
        def load_state(group_id: str) -> GroupState:
            return clone_state(self.store.get(group_id, GroupState(group_id=group_id)))

        def save_state(state: GroupState) -> None:
            self.store[state.group_id] = clone_state(state)

        for module in self.modules:
            self.originals.append((module, module.load_state, module.save_state))
            module.load_state = load_state
            module.save_state = save_state
        return self

    def __exit__(self, exc_type, exc, tb):
        for module, load_state, save_state in reversed(self.originals):
            module.load_state = load_state
            module.save_state = save_state

    def put(self, state: GroupState) -> None:
        self.store[state.group_id] = clone_state(state)

    def get(self, group_id: str) -> GroupState:
        return clone_state(self.store[group_id])


class FakeProvider:
    def __init__(
        self,
        final_text: str = "AI OOC response",
        tool_calls: list[tuple[str, dict]] | None = None,
        response_id: str = "fake-response-id",
    ) -> None:
        self.final_text = final_text
        self.tool_calls = tool_calls or []
        self.response_id = response_id
        self.calls = []
        self.tool_results = []

    def run_conversation(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        tool_callback = args[5]
        for name, tool_input in self.tool_calls:
            self.tool_results.append(tool_callback(name, tool_input))
        on_response_id = kwargs.get("on_response_id")
        if on_response_id:
            on_response_id(self.response_id)
        return self.final_text


class GateSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    def __call__(self, conversation_id: str, *, is_kp: bool):
        self.calls.append((conversation_id, is_kp))

        class _GateContext:
            async def __aenter__(self_inner):
                return None

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        return _GateContext()


def tool_by_name(tools: list[dict], name: str) -> dict:
    return next(tool for tool in tools if tool["name"] == name)


class KPAssistantV2Tests(unittest.IsolatedAsyncioTestCase):
    def test_group_state_kp_ooc_log_serialization(self):
        state = GroupState(group_id="g")
        self.assertEqual(state.kp_ooc_log, [])

        state.kp_ooc_log = [
            {"role": "kp_assistant", "content": "human note"},
            {"role": "assistant", "content": "ai note"},
        ]
        round_tripped = GroupState.from_dict(state.to_dict())
        self.assertEqual(round_tripped.kp_ooc_log, state.kp_ooc_log)

        legacy_data = state.to_dict()
        del legacy_data["kp_ooc_log"]
        legacy_loaded = GroupState.from_dict(legacy_data)
        self.assertEqual(legacy_loaded.kp_ooc_log, [])

    def test_kp_ooc_dynamic_prompt_is_kp_only_and_marks_authority(self):
        state = GroupState(group_id="g")
        state.kp_ooc_log = [
            {"role": "kp_assistant", "content": "開場看到屍體要做 SAN，成功 1、失敗 1D4。"},
            {"role": "assistant", "content": "我會安排成屍體首次揭露時觸發。"},
        ]

        kp_prompt = keeper._build_dynamic_prompt(state, "kp", speaker_role="kp_assistant")
        self.assertIn("kp_assistant: 開場看到屍體要做 SAN", kp_prompt)
        self.assertIn("assistant: 我會安排成屍體首次揭露時觸發。", kp_prompt)
        self.assertIn("過去 AI Keeper 在這段 OOC history 裡的回答只用於維持討論脈絡，不是 authoritative fact", kp_prompt)
        self.assertIn("不可以只因為自己前一輪曾經說過某件事", kp_prompt)

        player_prompt = keeper._build_dynamic_prompt(state, "player", speaker_role="player")
        self.assertNotIn("開場看到屍體要做 SAN", player_prompt)
        self.assertNotIn("我會安排成屍體首次揭露時觸發", player_prompt)

    def test_kp_ooc_turn_persists_ooc_only_caps_and_preserves_openai_chain(self):
        state = GroupState(group_id="g", openai_previous_response_id="formal-chain")
        state.kp_ooc_log = [
            {"role": "kp_assistant" if i % 2 == 0 else "assistant", "content": f"old-{i}"}
            for i in range(20)
        ]
        fake_provider = FakeProvider("這是新的幕後回答", response_id="ooc-response")
        original_provider = keeper._PROVIDERS.get("openai")
        original_llm_provider = keeper.LLM_PROVIDER

        with StateStorePatch(keeper) as store:
            store.put(state)
            keeper._PROVIDERS["openai"] = fake_provider
            keeper.LLM_PROVIDER = "openai"
            try:
                final_text, private_messages, image_requests = keeper.run_turn(
                    state,
                    user_id="kp",
                    speaker_name="KP",
                    message_text="請記住這個幕後判斷",
                    speaker_role="kp_assistant",
                )
            finally:
                keeper.LLM_PROVIDER = original_llm_provider
                if original_provider is None:
                    del keeper._PROVIDERS["openai"]
                else:
                    keeper._PROVIDERS["openai"] = original_provider

            saved = store.get("g")

        self.assertEqual(final_text, "這是新的幕後回答")
        self.assertEqual(private_messages, [])
        self.assertEqual(image_requests, [])
        self.assertEqual(saved.log, [])
        self.assertEqual(saved.openai_previous_response_id, "formal-chain")
        self.assertEqual(len(saved.kp_ooc_log), 20)
        self.assertEqual(saved.kp_ooc_log[-2], {"role": "kp_assistant", "content": "請記住這個幕後判斷"})
        self.assertEqual(saved.kp_ooc_log[-1], {"role": "assistant", "content": "這是新的幕後回答"})
        self.assertNotIn("old-0", [entry["content"] for entry in saved.kp_ooc_log])
        self.assertEqual(fake_provider.calls[0][1]["previous_response_id"], "formal-chain")

    def test_kp_sanity_check_creates_canonical_log_instead_of_ooc_log(self):
        state = GroupState(group_id="g", openai_previous_response_id="formal-chain")
        state.characters["p1"] = Character(name="Marco", owner_id="p1")
        state.kp_ooc_log = [{"role": "kp_assistant", "content": "old ooc"}]
        message_text = "Marco 把屍體的頭扭斷，血噴了一臉，做 SAN 0/1d4。"
        fake_provider = FakeProvider(
            "Marco 滿臉是血，需要做理智檢定。",
            tool_calls=[
                ("sanity_check", {"investigator": "Marco", "loss_success": "0", "loss_failure": "1d4"})
            ],
            response_id="canonical-response",
        )
        original_provider = keeper._PROVIDERS.get("openai")
        original_llm_provider = keeper.LLM_PROVIDER

        with StateStorePatch(keeper) as store:
            store.put(state)
            keeper._PROVIDERS["openai"] = fake_provider
            keeper.LLM_PROVIDER = "openai"
            try:
                final_text, private_messages, image_requests = keeper.run_turn(
                    state,
                    user_id="kp",
                    speaker_name="KP",
                    message_text=message_text,
                    speaker_role="kp_assistant",
                )
            finally:
                keeper.LLM_PROVIDER = original_llm_provider
                if original_provider is None:
                    del keeper._PROVIDERS["openai"]
                else:
                    keeper._PROVIDERS["openai"] = original_provider

            saved = store.get("g")

        self.assertEqual(final_text, "Marco 滿臉是血，需要做理智檢定。")
        self.assertEqual(private_messages, [])
        self.assertEqual(image_requests, [])
        self.assertEqual(
            saved.pending_checks["p1"],
            {"type": "sanity", "loss_success": "0", "loss_failure": "1d4"},
        )
        self.assertEqual(len(saved.log), 2)
        self.assertEqual(saved.log[0]["role"], "user")
        self.assertEqual(saved.log[1], {"role": "assistant", "content": "Marco 滿臉是血，需要做理智檢定。"})
        canonical_message = saved.log[0]["content"]
        self.assertIn("[KP ASSISTANT / CANONICAL GAME EVENT]", canonical_message)
        self.assertNotIn("[KP ASSISTANT / OOC HOST INSTRUCTION]", canonical_message)
        self.assertIn(message_text, canonical_message)
        self.assertIn("sanity_check", canonical_message)
        self.assertIn('"investigator": "Marco"', canonical_message)
        self.assertIn('"loss_failure": "1d4"', canonical_message)
        self.assertIn('"loss_success": "0"', canonical_message)
        self.assertIn('"ok": true', canonical_message)
        self.assertIn('"pending": true', canonical_message)
        self.assertEqual(saved.kp_ooc_log, [{"role": "kp_assistant", "content": "old ooc"}])
        self.assertEqual(saved.openai_previous_response_id, "canonical-response")
        self.assertEqual(fake_provider.calls[0][1]["previous_response_id"], "formal-chain")

    async def test_kp_ooc_lifecycle_cleanup_commands(self):
        async def noop_dm(*args):
            raise AssertionError("DM should not be called")

        async def noop_image(*args):
            raise AssertionError("image should not be called")

        with StateStorePatch(commands) as store:
            state = GroupState(group_id="g", kp_assistant_user_id="kp")
            state.kp_ooc_log = [{"role": "kp_assistant", "content": "old kp"}]
            store.put(state)
            reply = ReplyCollector()
            await commands._handle_coc_command("g", "kp", reply, noop_dm, noop_image, noop_image, "/coc kp quit")
            saved = store.get("g")
            self.assertEqual(saved.kp_assistant_user_id, "")
            self.assertEqual(saved.kp_ooc_log, [])

            state = GroupState(group_id="g", active=True, kp_assistant_user_id="kp")
            state.kp_ooc_log = [{"role": "assistant", "content": "old answer"}]
            state.log = [{"role": "user", "content": "formal log stays"}]
            store.put(state)
            reply = ReplyCollector()
            await commands._handle_coc_command("g", "someone", reply, noop_dm, noop_image, noop_image, "/coc end")
            saved = store.get("g")
            self.assertFalse(saved.active)
            self.assertEqual(saved.kp_assistant_user_id, "")
            self.assertEqual(saved.kp_ooc_log, [])
            self.assertEqual(saved.log, [{"role": "user", "content": "formal log stays"}])

            state = GroupState(group_id="g")
            state.kp_ooc_log = [{"role": "assistant", "content": "residue"}]
            store.put(state)
            reply = ReplyCollector()
            await commands._handle_coc_command("g", "new-kp", reply, noop_dm, noop_image, noop_image, "/coc kp")
            saved = store.get("g")
            self.assertEqual(saved.kp_assistant_user_id, "new-kp")
            self.assertEqual(saved.kp_ooc_log, [])

            state = GroupState(group_id="g")
            state.kp_ooc_log = [{"role": "kp_assistant", "content": "to reset"}]
            store.put(state)
            reply = ReplyCollector()
            await commands._handle_coc_command("g", "anyone", reply, noop_dm, noop_image, noop_image, "/coc newgame")
            self.assertEqual(store.get("g").kp_ooc_log, [])

    async def test_pdf_success_clears_kp_ooc_log_but_parse_failure_does_not(self):
        temp_library = tempfile.TemporaryDirectory()
        original_library_dir = commands.scenario_library.SCENARIO_LIBRARY_DIR
        commands.scenario_library.SCENARIO_LIBRARY_DIR = Path(temp_library.name)
        original_extract = commands.pdf_loader.extract_text
        original_guess_title = commands.pdf_loader.guess_title
        original_extract_preview = commands.pdf_loader.extract_preview
        original_extract_index = commands.scenario_index.extract_scenario_index
        original_clear_images = commands.clear_page_images
        original_save_image = commands.save_page_image

        with StateStorePatch(commands) as store:
            state = GroupState(group_id="g")
            state.kp_ooc_log = [{"role": "kp_assistant", "content": "old scenario note"}]
            store.put(state)
            commands.pdf_loader.extract_text = lambda pdf_bytes: ("new scenario text", [], False, {}, {})
            commands.pdf_loader.guess_title = lambda text, file_name="": "New Scenario"
            commands.pdf_loader.extract_preview = lambda pdf_bytes: "preview"
            commands.scenario_index.extract_scenario_index = lambda text: {"npcs": [], "locations": []}
            commands.clear_page_images = lambda conversation_id: None
            commands.save_page_image = lambda conversation_id, page_number, png_bytes: None
            try:
                reply = ReplyCollector()
                push = ReplyCollector()
                await commands.handle_pdf_upload("g", reply, push, b"%PDF", "scenario.pdf", skip_similarity=True)
                saved = store.get("g")
                self.assertEqual(saved.scenario_text, "new scenario text")
                self.assertEqual(saved.kp_ooc_log, [])

                saved.kp_ooc_log = [{"role": "kp_assistant", "content": "must survive failed parse"}]
                store.put(saved)

                def fail_extract(pdf_bytes):
                    raise ValueError("bad pdf")

                commands.pdf_loader.extract_text = fail_extract
                reply = ReplyCollector()
                push = ReplyCollector()
                await commands.handle_pdf_upload("g", reply, push, b"bad", "broken.pdf", skip_similarity=True)
                self.assertEqual(
                    store.get("g").kp_ooc_log,
                    [{"role": "kp_assistant", "content": "must survive failed parse"}],
                )
            finally:
                commands.pdf_loader.extract_text = original_extract
                commands.pdf_loader.guess_title = original_guess_title
                commands.pdf_loader.extract_preview = original_extract_preview
                commands.scenario_index.extract_scenario_index = original_extract_index
                commands.clear_page_images = original_clear_images
                commands.save_page_image = original_save_image
                commands.scenario_library.SCENARIO_LIBRARY_DIR = original_library_dir
                temp_library.cleanup()

    async def test_similar_pdf_upload_reloads_state_before_saving_pending_scenario_upload(self):
        """Regression test: extract_preview/find_similar run unlocked (via
        asyncio.to_thread) before the pending_scenario_upload save. A turn
        landing on this conversation in that window must not be silently
        reverted by that save — see the review finding this guards against."""
        with StateStorePatch(commands) as store:
            state = GroupState(group_id="g")
            state.log = [{"role": "user", "content": "original"}]
            store.put(state)

            original_extract_preview = commands.pdf_loader.extract_preview
            original_find_similar = commands.scenario_library.find_similar
            original_stage_upload = commands.scenario_library.stage_upload

            def concurrent_extract_preview(pdf_bytes):
                # Simulate another turn saving fresh state while this
                # (real, asyncio.to_thread-dispatched) extraction runs.
                concurrent = store.get("g")
                concurrent.log = concurrent.log + [{"role": "assistant", "content": "concurrent turn happened"}]
                store.put(concurrent)
                return "preview text"

            commands.pdf_loader.extract_preview = concurrent_extract_preview
            commands.scenario_library.find_similar = lambda title, preview: [
                {"id": "existing-scenario", "title": "Existing", "score": 0.9}
            ]
            commands.scenario_library.stage_upload = lambda pdf_bytes: "staged-key"
            try:
                reply = ReplyCollector()
                push = ReplyCollector()
                await commands.handle_pdf_upload("g", reply, push, b"%PDF", "scenario.pdf")

                saved = store.get("g")
                self.assertEqual(len(saved.log), 2, "the concurrent turn's log entry must survive")
                self.assertIsNotNone(saved.pending_scenario_upload)
                self.assertEqual(saved.pending_scenario_upload["key"], "staged-key")
            finally:
                commands.pdf_loader.extract_preview = original_extract_preview
                commands.scenario_library.find_similar = original_find_similar
                commands.scenario_library.stage_upload = original_stage_upload

    def test_show_scenario_image_and_search_reject_kp_only_assets_for_players(self):
        """Regression test: scenario_library previously hardcoded every image
        asset's visibility to "public" and keeper._execute_tool never checked
        the field at all, so a character_sheet page (potentially an NPC/
        villain stat block or a pregen revealing a spoiler) was exactly as
        visible to an ordinary player as a map page."""
        with tempfile.TemporaryDirectory() as temp:
            original_library_dir = keeper.scenario_library.SCENARIO_LIBRARY_DIR
            keeper.scenario_library.SCENARIO_LIBRARY_DIR = Path(temp)
            try:
                text = (
                    "--- 第 1 頁 ---\n調查員：陳墨\nSTR 65 DEX 75 SAN 55\n"
                    "--- 第 2 頁 ---\n[圖片內容描述：一張地圖]\n"
                )
                scenario_id = keeper.scenario_library.save_scenario(
                    b"%PDF-1.4 fake", title="Visibility Test", filename="t.pdf", preview=text[:200],
                    text=text, indexes={}, pregens=[], page_maps={2: {"id": "map-2"}},
                    page_images={1: b"page-1", 2: b"page-2"},
                )
                state = GroupState(group_id="g")
                state.scenario_library_id = scenario_id
                state.context_chapter_ids = ["chapter-01"]

                with StateStorePatch(keeper) as store:
                    store.put(state)

                    player_search = keeper._execute_tool(state, "search_scenario_images", {}, [], [], speaker_role="player")
                    self.assertEqual([a["type"] for a in player_search["assets"]], ["map"])

                    kp_search = keeper._execute_tool(state, "search_scenario_images", {}, [], [], speaker_role="kp_assistant")
                    self.assertEqual(sorted(a["type"] for a in kp_search["assets"]), ["character_sheet", "map"])

                    player_show_sheet = keeper._execute_tool(
                        state, "show_scenario_image", {"page_number": 1}, [], [], speaker_role="player"
                    )
                    self.assertFalse(player_show_sheet["ok"])

                    kp_show_sheet = keeper._execute_tool(
                        state, "show_scenario_image", {"page_number": 1}, [], [], speaker_role="kp_assistant"
                    )
                    self.assertTrue(kp_show_sheet["ok"])

                    player_show_map = keeper._execute_tool(
                        state, "show_scenario_image", {"page_number": 2}, [], [], speaker_role="player"
                    )
                    self.assertTrue(player_show_map["ok"])
            finally:
                keeper.scenario_library.SCENARIO_LIBRARY_DIR = original_library_dir

    def test_kp_assistant_tool_allowlist_and_runtime_guard(self):
        original_rag_enabled = keeper.SCENARIO_RAG_ENABLED
        keeper.SCENARIO_RAG_ENABLED = True
        try:
            tool_names = {tool["name"] for tool in keeper._tools_for_speaker_role("kp_assistant")}
        finally:
            keeper.SCENARIO_RAG_ENABLED = original_rag_enabled

        expected_allowed = {
            "get_character_sheet",
            "get_combat_status",
            "search_memory",
            "search_scenario",
            "roll_dice",
            "skill_check",
            "sanity_check",
            "offer_check_choice",
            "npc_skill_check",
            "roll_weapon_damage",
            "roll_impaling_damage",
            "apply_combat_damage",
            "add_combat_effect",
        }
        self.assertTrue(expected_allowed.issubset(tool_names))
        self.assertFalse({"adjust_character", "adjust_ammo", "set_skill", "damage_combatant", "start_combat"} & tool_names)

        state = GroupState(group_id="g")
        state.characters["p1"] = Character(name="The Tough Guy/Dame", owner_id="p1", occupation="Dame")
        with StateStorePatch(keeper) as store:
            store.put(state)
            result = keeper._execute_tool(
                state,
                "sanity_check",
                {"investigator": "The Tough Guy/Dame", "loss_success": "1", "loss_failure": "1d4"},
                [],
                [],
                speaker_role="kp_assistant",
            )
            self.assertTrue(result["ok"])
            saved = store.get("g")
            self.assertEqual(
                saved.pending_checks["p1"],
                {"type": "sanity", "loss_success": "1", "loss_failure": "1d4"},
            )

            rejected = keeper._execute_tool(
                state,
                "adjust_character",
                {"investigator": "The Tough Guy/Dame", "field": "san", "delta": -10},
                [],
                [],
                speaker_role="kp_assistant",
            )
            self.assertFalse(rejected["ok"])
            self.assertIn("已允許的查詢與主持流程工具", rejected["error"])

    def test_kp_assistant_fixed_damage_tools_are_allowed_and_canonical(self):
        state = GroupState(group_id="g")
        char = Character(name="Marco", owner_id="p1", character_id="char-marco", dex=50, hp=12, hp_max=12)
        state.characters["p1"] = char
        state.characters_by_id["char-marco"] = char
        state.active_character_id_by_user["p1"] = "char-marco"
        combat.start_combat(state)

        with StateStorePatch(keeper) as store:
            store.put(state)
            effect_result = keeper._execute_tool(
                state,
                "add_combat_effect",
                {
                    "target": "Marco",
                    "label": "Burning Curtain",
                    "timing": "turn_start",
                    "damage": "1",
                    "damage_type": "fire",
                    "remaining_rounds": 1,
                    "tags": ["fire"],
                },
                [],
                [],
                speaker_role="kp_assistant",
            )
            damage_result = keeper._execute_tool(
                state,
                "apply_combat_damage",
                {"target": "Marco", "raw_damage": 1, "damage_type": "physical", "source_id": "glass"},
                [],
                [],
                speaker_role="kp_assistant",
            )
            blocked_result = keeper._execute_tool(
                state,
                "damage_combatant",
                {"name": "Marco", "delta": -1},
                [],
                [],
                speaker_role="kp_assistant",
            )

        self.assertTrue(effect_result["ok"])
        self.assertEqual(effect_result["damage"], "1")
        self.assertEqual(effect_result["damage_type"], "fire")
        self.assertTrue(damage_result["ok"])
        self.assertFalse(blocked_result["ok"])
        self.assertTrue(keeper._kp_tool_result_creates_canon("add_combat_effect", {}, effect_result))
        self.assertTrue(keeper._kp_tool_result_creates_canon("apply_combat_damage", {}, damage_result))
        self.assertEqual(store.get("g").characters_by_id["char-marco"].hp, 11)

    def test_roll_dice_creates_canon_only_for_game_resolution_context(self):
        self.assertTrue(keeper._kp_tool_result_creates_canon(
            "roll_dice",
            {
                "expression": "1d3",
                "purpose": "碎玻璃傷害",
                "roll_context": "game_resolution",
            },
            {"ok": True},
        ))
        self.assertFalse(keeper._kp_tool_result_creates_canon(
            "roll_dice",
            {
                "expression": "1d6",
                "purpose": "決定下一幕使用方案 A 還是 B",
                "roll_context": "ooc_randomizer",
            },
            {"ok": True},
        ))
        self.assertFalse(keeper._kp_tool_result_creates_canon(
            "roll_dice",
            {"expression": "1d3", "purpose": "碎玻璃傷害"},
            {"ok": True},
        ))
        self.assertFalse(keeper._kp_tool_result_creates_canon(
            "roll_dice",
            {
                "expression": "1d3",
                "purpose": "碎玻璃傷害",
                "roll_context": "game_resolution",
            },
            {"ok": False},
        ))

    def test_global_roll_dice_schema_does_not_expose_roll_context(self):
        roll_dice_tool = tool_by_name(keeper.TOOLS, "roll_dice")
        properties = roll_dice_tool["input_schema"]["properties"]
        self.assertNotIn("roll_context", properties)
        self.assertIn("purpose", properties)
        self.assertEqual(roll_dice_tool["input_schema"]["required"], ["expression"])

    def test_ordinary_speaker_roll_dice_schema_does_not_expose_roll_context(self):
        roll_dice_tool = tool_by_name(keeper._tools_for_speaker_role("player"), "roll_dice")
        self.assertNotIn("roll_context", roll_dice_tool["input_schema"]["properties"])

    def test_kp_roll_dice_tool_definition_adds_context_without_global_mutation(self):
        global_roll_dice_tool = tool_by_name(keeper.TOOLS, "roll_dice")
        kp_roll_dice_tool = keeper._tool_definition_for_kp_assistant(global_roll_dice_tool)

        roll_context = kp_roll_dice_tool["input_schema"]["properties"]["roll_context"]
        self.assertEqual(roll_context["enum"], ["game_resolution", "ooc_randomizer"])
        self.assertEqual(kp_roll_dice_tool["input_schema"]["required"], ["expression", "roll_context"])
        self.assertNotIn("roll_context", global_roll_dice_tool["input_schema"]["properties"])
        self.assertEqual(global_roll_dice_tool["input_schema"]["required"], ["expression"])

    def test_kp_assistant_roll_dice_schema_requires_context(self):
        roll_dice_tool = tool_by_name(keeper._tools_for_speaker_role("kp_assistant"), "roll_dice")
        properties = roll_dice_tool["input_schema"]["properties"]
        self.assertIn("roll_context", properties)
        self.assertEqual(properties["roll_context"]["enum"], ["game_resolution", "ooc_randomizer"])
        self.assertEqual(roll_dice_tool["input_schema"]["required"], ["expression", "roll_context"])

    def test_kp_roll_dice_context_validation(self):
        state = GroupState(group_id="g")
        missing_context = keeper._execute_tool(
            state,
            "roll_dice",
            {"expression": "1d6"},
            [],
            [],
            speaker_role="kp_assistant",
        )
        self.assertFalse(missing_context["ok"])
        self.assertIn("roll_context", missing_context["error"])
        self.assertIn("game_resolution", missing_context["error"])
        self.assertIn("ooc_randomizer", missing_context["error"])

        invalid_context = keeper._execute_tool(
            state,
            "roll_dice",
            {"expression": "1d6", "roll_context": "damage"},
            [],
            [],
            speaker_role="kp_assistant",
        )
        self.assertFalse(invalid_context["ok"])
        self.assertIn("roll_context", invalid_context["error"])
        self.assertIn("game_resolution", invalid_context["error"])
        self.assertIn("ooc_randomizer", invalid_context["error"])

        valid_game_resolution = keeper._execute_tool(
            state,
            "roll_dice",
            {"expression": "1d6", "roll_context": "game_resolution"},
            [],
            [],
            speaker_role="kp_assistant",
        )
        self.assertTrue(valid_game_resolution["ok"])

        valid_ooc_randomizer = keeper._execute_tool(
            state,
            "roll_dice",
            {"expression": "1d6", "roll_context": "ooc_randomizer"},
            [],
            [],
            speaker_role="kp_assistant",
        )
        self.assertTrue(valid_ooc_randomizer["ok"])

    def test_player_roll_dice_does_not_require_roll_context(self):
        result = keeper._execute_tool(
            GroupState(group_id="g"),
            "roll_dice",
            {"expression": "1d6"},
            [],
            [],
            speaker_role="player",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["expression"], "1d6")
        self.assertIn("rolls", result)
        self.assertIn("modifier", result)
        self.assertIn("total", result)

    def test_kp_roll_dice_game_resolution_creates_canonical_log(self):
        state = GroupState(group_id="g", openai_previous_response_id="formal-chain")
        message_text = "碎玻璃割傷 Marco，骰 1d3 傷害。"
        fake_provider = FakeProvider(
            "碎玻璃造成的傷害已確定。",
            tool_calls=[(
                "roll_dice",
                {
                    "expression": "1d3",
                    "purpose": "碎玻璃割傷 Marco 的傷害",
                    "roll_context": "game_resolution",
                },
            )],
            response_id="game-resolution-roll-response",
        )
        original_provider = keeper._PROVIDERS.get("openai")
        original_llm_provider = keeper.LLM_PROVIDER

        with StateStorePatch(keeper) as store:
            store.put(state)
            keeper._PROVIDERS["openai"] = fake_provider
            keeper.LLM_PROVIDER = "openai"
            try:
                keeper.run_turn(
                    state,
                    user_id="kp",
                    speaker_name="KP",
                    message_text=message_text,
                    speaker_role="kp_assistant",
                )
            finally:
                keeper.LLM_PROVIDER = original_llm_provider
                if original_provider is None:
                    del keeper._PROVIDERS["openai"]
                else:
                    keeper._PROVIDERS["openai"] = original_provider

            saved = store.get("g")

        self.assertTrue(fake_provider.tool_results[0]["ok"])
        self.assertEqual(fake_provider.tool_results[0]["expression"], "1d3")
        self.assertIn("rolls", fake_provider.tool_results[0])
        self.assertIn("modifier", fake_provider.tool_results[0])
        self.assertIn("total", fake_provider.tool_results[0])
        self.assertEqual(len(saved.log), 2)
        self.assertEqual(saved.kp_ooc_log, [])
        canonical_message = saved.log[0]["content"]
        self.assertIn(message_text, canonical_message)
        self.assertIn("roll_dice", canonical_message)
        self.assertIn('"expression": "1d3"', canonical_message)
        self.assertIn('"purpose": "碎玻璃割傷 Marco 的傷害"', canonical_message)
        self.assertIn('"roll_context": "game_resolution"', canonical_message)
        self.assertIn('"rolls"', canonical_message)
        self.assertIn('"total"', canonical_message)
        self.assertEqual(saved.openai_previous_response_id, "game-resolution-roll-response")
        self.assertEqual(fake_provider.calls[0][1]["previous_response_id"], "formal-chain")

    def test_kp_roll_dice_ooc_randomizer_stays_in_ooc_log(self):
        state = GroupState(group_id="g", openai_previous_response_id="formal-chain")
        message_text = "我幕後骰 1d6，決定下一幕用 NPC A 還是 NPC B。"
        fake_provider = FakeProvider(
            "幕後隨機結果已決定。",
            tool_calls=[(
                "roll_dice",
                {
                    "expression": "1d6",
                    "purpose": "幕後決定下一幕使用哪個 NPC",
                    "roll_context": "ooc_randomizer",
                },
            )],
            response_id="ooc-randomizer-roll-response",
        )
        original_provider = keeper._PROVIDERS.get("openai")
        original_llm_provider = keeper.LLM_PROVIDER

        with StateStorePatch(keeper) as store:
            store.put(state)
            keeper._PROVIDERS["openai"] = fake_provider
            keeper.LLM_PROVIDER = "openai"
            try:
                keeper.run_turn(
                    state,
                    user_id="kp",
                    speaker_name="KP",
                    message_text=message_text,
                    speaker_role="kp_assistant",
                )
            finally:
                keeper.LLM_PROVIDER = original_llm_provider
                if original_provider is None:
                    del keeper._PROVIDERS["openai"]
                else:
                    keeper._PROVIDERS["openai"] = original_provider

            saved = store.get("g")

        self.assertTrue(fake_provider.tool_results[0]["ok"])
        self.assertEqual(fake_provider.tool_results[0]["expression"], "1d6")
        self.assertIn("rolls", fake_provider.tool_results[0])
        self.assertIn("total", fake_provider.tool_results[0])
        self.assertEqual(saved.log, [])
        self.assertEqual(saved.kp_ooc_log[-2], {"role": "kp_assistant", "content": message_text})
        self.assertEqual(saved.kp_ooc_log[-1], {"role": "assistant", "content": "幕後隨機結果已決定。"})
        self.assertEqual(saved.openai_previous_response_id, "formal-chain")
        self.assertEqual(fake_provider.calls[0][1]["previous_response_id"], "formal-chain")

    def test_kp_roll_weapon_damage_creates_canonical_log(self):
        state = GroupState(group_id="g", openai_previous_response_id="formal-chain")
        state.characters["p1"] = Character(name="Marco", owner_id="p1", damage_bonus="+1d4")
        message_text = "Marco 的攻擊命中，骰他的 1d8 武器傷害。"
        fake_provider = FakeProvider(
            "Marco 的武器傷害已確定。",
            tool_calls=[("roll_weapon_damage", {"investigator": "Marco", "weapon_damage": "1d8"})],
            response_id="weapon-damage-response",
        )
        original_provider = keeper._PROVIDERS.get("openai")
        original_llm_provider = keeper.LLM_PROVIDER

        with StateStorePatch(keeper) as store:
            store.put(state)
            keeper._PROVIDERS["openai"] = fake_provider
            keeper.LLM_PROVIDER = "openai"
            try:
                keeper.run_turn(
                    state,
                    user_id="kp",
                    speaker_name="KP",
                    message_text=message_text,
                    speaker_role="kp_assistant",
                )
            finally:
                keeper.LLM_PROVIDER = original_llm_provider
                if original_provider is None:
                    del keeper._PROVIDERS["openai"]
                else:
                    keeper._PROVIDERS["openai"] = original_provider

            saved = store.get("g")

        self.assertTrue(fake_provider.tool_results[0]["ok"])
        self.assertEqual(fake_provider.tool_results[0]["investigator"], "Marco")
        self.assertEqual(fake_provider.tool_results[0]["damage_bonus"], "+1d4")
        self.assertIn("weapon_damage_roll", fake_provider.tool_results[0])
        self.assertIn("damage_bonus_roll", fake_provider.tool_results[0])
        self.assertIn("total", fake_provider.tool_results[0])
        self.assertEqual(len(saved.log), 2)
        self.assertEqual(saved.kp_ooc_log, [])
        canonical_message = saved.log[0]["content"]
        self.assertIn(message_text, canonical_message)
        self.assertIn("roll_weapon_damage", canonical_message)
        self.assertIn('"investigator": "Marco"', canonical_message)
        self.assertIn('"weapon_damage": "1d8"', canonical_message)
        self.assertIn('"weapon_damage_roll"', canonical_message)
        self.assertIn('"damage_bonus": "+1d4"', canonical_message)
        self.assertIn('"total"', canonical_message)
        self.assertEqual(saved.openai_previous_response_id, "weapon-damage-response")

    def test_kp_roll_impaling_damage_creates_canonical_log(self):
        state = GroupState(group_id="g", openai_previous_response_id="formal-chain")
        message_text = "這次攻擊是極限成功，計算 1d8 穿刺傷害，加值 1d4。"
        fake_provider = FakeProvider(
            "極限成功傷害已確定。",
            tool_calls=[(
                "roll_impaling_damage",
                {"weapon_damage": "1d8", "damage_bonus": "1d4", "impaling": True},
            )],
            response_id="impaling-damage-response",
        )
        original_provider = keeper._PROVIDERS.get("openai")
        original_llm_provider = keeper.LLM_PROVIDER

        with StateStorePatch(keeper) as store:
            store.put(state)
            keeper._PROVIDERS["openai"] = fake_provider
            keeper.LLM_PROVIDER = "openai"
            try:
                keeper.run_turn(
                    state,
                    user_id="kp",
                    speaker_name="KP",
                    message_text=message_text,
                    speaker_role="kp_assistant",
                )
            finally:
                keeper.LLM_PROVIDER = original_llm_provider
                if original_provider is None:
                    del keeper._PROVIDERS["openai"]
                else:
                    keeper._PROVIDERS["openai"] = original_provider

            saved = store.get("g")

        self.assertTrue(fake_provider.tool_results[0]["ok"])
        self.assertTrue(fake_provider.tool_results[0]["impaling"])
        self.assertIn("max_weapon_damage", fake_provider.tool_results[0])
        self.assertIn("max_damage_bonus", fake_provider.tool_results[0])
        self.assertIn("reroll_total", fake_provider.tool_results[0])
        self.assertIn("total", fake_provider.tool_results[0])
        self.assertEqual(len(saved.log), 2)
        self.assertEqual(saved.kp_ooc_log, [])
        canonical_message = saved.log[0]["content"]
        self.assertIn(message_text, canonical_message)
        self.assertIn("roll_impaling_damage", canonical_message)
        self.assertIn('"weapon_damage": "1d8"', canonical_message)
        self.assertIn('"damage_bonus": "1d4"', canonical_message)
        self.assertIn('"impaling": true', canonical_message)
        self.assertIn('"max_weapon_damage"', canonical_message)
        self.assertIn('"reroll_total"', canonical_message)
        self.assertIn('"total"', canonical_message)
        self.assertEqual(saved.openai_previous_response_id, "impaling-damage-response")

    def test_failed_kp_damage_tool_does_not_create_canon(self):
        state = GroupState(group_id="g", openai_previous_response_id="formal-chain")
        message_text = "不存在的角色攻擊命中，骰 1d8 傷害。"
        fake_provider = FakeProvider(
            "找不到角色，無法建立正式傷害結果。",
            tool_calls=[("roll_weapon_damage", {"investigator": "不存在的角色", "weapon_damage": "1d8"})],
            response_id="failed-damage-response",
        )
        original_provider = keeper._PROVIDERS.get("openai")
        original_llm_provider = keeper.LLM_PROVIDER

        with StateStorePatch(keeper) as store:
            store.put(state)
            keeper._PROVIDERS["openai"] = fake_provider
            keeper.LLM_PROVIDER = "openai"
            try:
                keeper.run_turn(
                    state,
                    user_id="kp",
                    speaker_name="KP",
                    message_text=message_text,
                    speaker_role="kp_assistant",
                )
            finally:
                keeper.LLM_PROVIDER = original_llm_provider
                if original_provider is None:
                    del keeper._PROVIDERS["openai"]
                else:
                    keeper._PROVIDERS["openai"] = original_provider

            saved = store.get("g")

        self.assertFalse(fake_provider.tool_results[0]["ok"])
        self.assertEqual(saved.log, [])
        self.assertEqual(saved.kp_ooc_log[-2], {"role": "kp_assistant", "content": message_text})
        self.assertEqual(saved.kp_ooc_log[-1], {"role": "assistant", "content": "找不到角色，無法建立正式傷害結果。"})
        self.assertEqual(saved.openai_previous_response_id, "formal-chain")

    def test_san_reproduction_case_uses_kp_context_and_creates_pending_check(self):
        state = GroupState(group_id="g")
        state.characters["p1"] = Character(name="The Tough Guy/Dame", owner_id="p1", occupation="Dame")
        state.kp_ooc_log = [
            {"role": "kp_assistant", "content": "開場看到屍體要做 SAN，成功 1、失敗 1D4。"},
            {"role": "assistant", "content": "了解，等屍體揭露時建立 SAN 流程。"},
        ]
        prompt = keeper._build_dynamic_prompt(state, "kp", speaker_role="kp_assistant")
        self.assertIn("開場看到屍體要做 SAN，成功 1、失敗 1D4。", prompt)

        with StateStorePatch(keeper) as store:
            store.put(state)
            result = keeper._execute_tool(
                state,
                "sanity_check",
                {"investigator": "The Tough Guy/Dame", "loss_success": "1", "loss_failure": "1d4"},
                [],
                [],
                speaker_role="kp_assistant",
            )
            self.assertTrue(result["ok"])
            self.assertTrue(result["pending"])
            self.assertEqual(store.get("g").pending_checks["p1"]["type"], "sanity")
            self.assertEqual(store.get("g").pending_checks["p1"]["loss_success"], "1")
            self.assertEqual(store.get("g").pending_checks["p1"]["loss_failure"], "1d4")

    async def test_ordinary_message_without_kp_bypasses_priority_gate(self):
        async def noop_dm(*args):
            raise AssertionError("DM should not be called")

        async def noop_image(*args):
            raise AssertionError("image should not be called")

        state = GroupState(group_id="g", active=True, game_started=True)
        state.characters["p1"] = Character(name="Investigator", owner_id="p1")
        calls = []

        async def fake_run_turn(**kwargs):
            calls.append(kwargs)
            return "keeper reply", [], []

        def forbidden_gate(*args, **kwargs):
            raise AssertionError("priority gate must be bypassed when no KP Assistant exists")

        original_gate = commands.locks.get_keeper_priority_gate
        original_run_turn = router.supervisor.run_turn
        original_resolve = router._resolve_map_action_transaction
        original_spawn = commands._spawn_post_turn_maintenance
        original_router_load_state = router.load_state
        with StateStorePatch(commands) as store:
            store.put(state)
            router.load_state = commands.load_state
            commands.locks.get_keeper_priority_gate = forbidden_gate
            router.supervisor.run_turn = fake_run_turn
            router._resolve_map_action_transaction = lambda *args: None
            commands._spawn_post_turn_maintenance = lambda conversation_id: None
            try:
                reply = ReplyCollector()
                await router.handle_text_message(
                    "g", "p1", lambda: None, reply, noop_dm, noop_image, noop_image, "look around"
                )
            finally:
                commands.locks.get_keeper_priority_gate = original_gate
                router.supervisor.run_turn = original_run_turn
                router._resolve_map_action_transaction = original_resolve
                commands._spawn_post_turn_maintenance = original_spawn
                router.load_state = original_router_load_state

        self.assertEqual(reply.messages, ["keeper reply"])
        self.assertEqual(calls[0]["speaker_role"], "player")

    async def test_ordinary_kp_message_uses_kp_priority_when_kp_exists(self):
        async def noop_dm(*args):
            raise AssertionError("DM should not be called")

        async def noop_image(*args):
            raise AssertionError("image should not be called")

        state = GroupState(group_id="g", active=True, game_started=True, kp_assistant_user_id="kp-user")
        gate = GateSpy()
        calls = []

        async def display_name():
            return "KP Name"

        async def fake_run_turn(**kwargs):
            calls.append(kwargs)
            return "kp reply", [], []

        original_gate = commands.locks.get_keeper_priority_gate
        original_run_turn = router.supervisor.run_turn
        original_spawn = commands._spawn_post_turn_maintenance
        original_router_load_state = router.load_state
        with StateStorePatch(commands) as store:
            store.put(state)
            router.load_state = commands.load_state
            commands.locks.get_keeper_priority_gate = gate
            router.supervisor.run_turn = fake_run_turn
            commands._spawn_post_turn_maintenance = lambda conversation_id: None
            try:
                reply = ReplyCollector()
                await router.handle_text_message(
                    "g", "kp-user", display_name, reply, noop_dm, noop_image, noop_image, "幕後提醒"
                )
            finally:
                commands.locks.get_keeper_priority_gate = original_gate
                router.supervisor.run_turn = original_run_turn
                commands._spawn_post_turn_maintenance = original_spawn
                router.load_state = original_router_load_state

        self.assertEqual(gate.calls, [("g", True)])
        self.assertEqual(calls[0]["speaker_role"], "kp_assistant")
        self.assertEqual(reply.messages, ["kp reply"])

    async def test_ordinary_player_message_uses_player_priority_when_kp_exists(self):
        async def noop_dm(*args):
            raise AssertionError("DM should not be called")

        async def noop_image(*args):
            raise AssertionError("image should not be called")

        state = GroupState(group_id="g", active=True, game_started=True, kp_assistant_user_id="kp-user")
        state.characters["p1"] = Character(name="Investigator", owner_id="p1")
        gate = GateSpy()
        calls = []

        async def fake_run_turn(**kwargs):
            calls.append(kwargs)
            return "player reply", [], []

        original_gate = commands.locks.get_keeper_priority_gate
        original_run_turn = router.supervisor.run_turn
        original_resolve = router._resolve_map_action_transaction
        original_spawn = commands._spawn_post_turn_maintenance
        original_router_load_state = router.load_state
        with StateStorePatch(commands) as store:
            store.put(state)
            router.load_state = commands.load_state
            commands.locks.get_keeper_priority_gate = gate
            router.supervisor.run_turn = fake_run_turn
            router._resolve_map_action_transaction = lambda *args: None
            commands._spawn_post_turn_maintenance = lambda conversation_id: None
            try:
                reply = ReplyCollector()
                await router.handle_text_message(
                    "g", "p1", lambda: None, reply, noop_dm, noop_image, noop_image, "look around"
                )
            finally:
                commands.locks.get_keeper_priority_gate = original_gate
                router.supervisor.run_turn = original_run_turn
                router._resolve_map_action_transaction = original_resolve
                commands._spawn_post_turn_maintenance = original_spawn
                router.load_state = original_router_load_state

        self.assertEqual(gate.calls, [("g", False)])
        self.assertEqual(calls[0]["speaker_role"], "player")
        self.assertEqual(reply.messages, ["player reply"])

    async def test_scheduling_snapshot_does_not_become_authoritative_state(self):
        async def noop_dm(*args):
            raise AssertionError("DM should not be called")

        async def noop_image(*args):
            raise AssertionError("image should not be called")

        state = GroupState(group_id="g", active=True, game_started=True, kp_assistant_user_id="kp-user")
        calls = []

        async def fake_run_turn(**kwargs):
            calls.append(kwargs)
            return "latest-state reply", [], []

        class MutatingGateSpy(GateSpy):
            def __init__(self, store: StateStorePatch) -> None:
                super().__init__()
                self.store = store

            def __call__(self, conversation_id: str, *, is_kp: bool):
                self.calls.append((conversation_id, is_kp))
                store = self.store

                class _GateContext:
                    async def __aenter__(self_inner):
                        latest = store.get(conversation_id)
                        latest.kp_assistant_user_id = ""
                        latest.characters["kp-user"] = Character(name="Former KP Now Player", owner_id="kp-user")
                        store.put(latest)
                        return None

                    async def __aexit__(self_inner, exc_type, exc, tb):
                        return False

                return _GateContext()

        original_gate = commands.locks.get_keeper_priority_gate
        original_run_turn = router.supervisor.run_turn
        original_resolve = router._resolve_map_action_transaction
        original_spawn = commands._spawn_post_turn_maintenance
        original_router_load_state = router.load_state
        with StateStorePatch(commands) as store:
            store.put(state)
            router.load_state = commands.load_state
            gate = MutatingGateSpy(store)
            commands.locks.get_keeper_priority_gate = gate
            router.supervisor.run_turn = fake_run_turn
            router._resolve_map_action_transaction = lambda *args: None
            commands._spawn_post_turn_maintenance = lambda conversation_id: None
            try:
                reply = ReplyCollector()
                await router.handle_text_message(
                    "g", "kp-user", lambda: None, reply, noop_dm, noop_image, noop_image, "now IC"
                )
            finally:
                commands.locks.get_keeper_priority_gate = original_gate
                router.supervisor.run_turn = original_run_turn
                router._resolve_map_action_transaction = original_resolve
                commands._spawn_post_turn_maintenance = original_spawn
                router.load_state = original_router_load_state

        self.assertEqual(gate.calls, [("g", True)])
        self.assertEqual(calls[0]["display_name"], "Former KP Now Player")
        self.assertEqual(calls[0]["speaker_role"], "player")
        self.assertEqual(calls[0]["state"].kp_assistant_user_id, "")
        self.assertEqual(reply.messages, ["latest-state reply"])


if __name__ == "__main__":
    unittest.main()
