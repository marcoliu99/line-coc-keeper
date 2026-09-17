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
    ),
)

from app import commands, keeper
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
    def __init__(self, final_text: str = "AI OOC response") -> None:
        self.final_text = final_text
        self.calls = []

    def run_conversation(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        on_response_id = kwargs.get("on_response_id")
        if on_response_id:
            on_response_id("fake-response-id")
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
        fake_provider = FakeProvider("這是新的幕後回答")
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
        original_extract = commands.pdf_loader.extract_text
        original_guess_title = commands.pdf_loader.guess_title
        original_extract_index = commands.scenario_index.extract_scenario_index
        original_clear_images = commands.clear_page_images
        original_save_image = commands.save_page_image

        with StateStorePatch(commands) as store:
            state = GroupState(group_id="g")
            state.kp_ooc_log = [{"role": "kp_assistant", "content": "old scenario note"}]
            store.put(state)
            commands.pdf_loader.extract_text = lambda pdf_bytes: ("new scenario text", [], False, {}, {})
            commands.pdf_loader.guess_title = lambda text, file_name="": "New Scenario"
            commands.scenario_index.extract_scenario_index = lambda text: {"npcs": [], "locations": []}
            commands.clear_page_images = lambda conversation_id: None
            commands.save_page_image = lambda conversation_id, page_number, png_bytes: None
            try:
                reply = ReplyCollector()
                push = ReplyCollector()
                await commands.handle_pdf_upload("g", reply, push, b"%PDF", "scenario.pdf")
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
                await commands.handle_pdf_upload("g", reply, push, b"bad", "broken.pdf")
                self.assertEqual(
                    store.get("g").kp_ooc_log,
                    [{"role": "kp_assistant", "content": "must survive failed parse"}],
                )
            finally:
                commands.pdf_loader.extract_text = original_extract
                commands.pdf_loader.guess_title = original_guess_title
                commands.scenario_index.extract_scenario_index = original_extract_index
                commands.clear_page_images = original_clear_images
                commands.save_page_image = original_save_image

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
            "skill_check",
            "sanity_check",
            "offer_check_choice",
            "npc_skill_check",
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

        def fake_run_turn(state_arg, user_id, display_name, text, resolved_location, speaker_role):
            calls.append((state_arg, user_id, display_name, text, resolved_location, speaker_role))
            return "keeper reply", [], []

        def forbidden_gate(*args, **kwargs):
            raise AssertionError("priority gate must be bypassed when no KP Assistant exists")

        original_gate = commands.locks.get_keeper_priority_gate
        original_run_turn = commands.keeper.run_turn
        original_resolve = commands._resolve_map_action_transaction
        original_spawn = commands._spawn_post_turn_maintenance
        with StateStorePatch(commands) as store:
            store.put(state)
            commands.locks.get_keeper_priority_gate = forbidden_gate
            commands.keeper.run_turn = fake_run_turn
            commands._resolve_map_action_transaction = lambda *args: None
            commands._spawn_post_turn_maintenance = lambda conversation_id: None
            try:
                reply = ReplyCollector()
                await commands.handle_text_message(
                    "g", "p1", lambda: None, reply, noop_dm, noop_image, noop_image, "look around"
                )
            finally:
                commands.locks.get_keeper_priority_gate = original_gate
                commands.keeper.run_turn = original_run_turn
                commands._resolve_map_action_transaction = original_resolve
                commands._spawn_post_turn_maintenance = original_spawn

        self.assertEqual(reply.messages, ["keeper reply"])
        self.assertEqual(calls[0][5], "player")

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

        def fake_run_turn(state_arg, user_id, display_name_arg, text, resolved_location, speaker_role):
            calls.append((state_arg, user_id, display_name_arg, text, resolved_location, speaker_role))
            return "kp reply", [], []

        original_gate = commands.locks.get_keeper_priority_gate
        original_run_turn = commands.keeper.run_turn
        original_spawn = commands._spawn_post_turn_maintenance
        with StateStorePatch(commands) as store:
            store.put(state)
            commands.locks.get_keeper_priority_gate = gate
            commands.keeper.run_turn = fake_run_turn
            commands._spawn_post_turn_maintenance = lambda conversation_id: None
            try:
                reply = ReplyCollector()
                await commands.handle_text_message(
                    "g", "kp-user", display_name, reply, noop_dm, noop_image, noop_image, "幕後提醒"
                )
            finally:
                commands.locks.get_keeper_priority_gate = original_gate
                commands.keeper.run_turn = original_run_turn
                commands._spawn_post_turn_maintenance = original_spawn

        self.assertEqual(gate.calls, [("g", True)])
        self.assertEqual(calls[0][5], "kp_assistant")
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

        def fake_run_turn(state_arg, user_id, display_name, text, resolved_location, speaker_role):
            calls.append((state_arg, user_id, display_name, text, resolved_location, speaker_role))
            return "player reply", [], []

        original_gate = commands.locks.get_keeper_priority_gate
        original_run_turn = commands.keeper.run_turn
        original_resolve = commands._resolve_map_action_transaction
        original_spawn = commands._spawn_post_turn_maintenance
        with StateStorePatch(commands) as store:
            store.put(state)
            commands.locks.get_keeper_priority_gate = gate
            commands.keeper.run_turn = fake_run_turn
            commands._resolve_map_action_transaction = lambda *args: None
            commands._spawn_post_turn_maintenance = lambda conversation_id: None
            try:
                reply = ReplyCollector()
                await commands.handle_text_message(
                    "g", "p1", lambda: None, reply, noop_dm, noop_image, noop_image, "look around"
                )
            finally:
                commands.locks.get_keeper_priority_gate = original_gate
                commands.keeper.run_turn = original_run_turn
                commands._resolve_map_action_transaction = original_resolve
                commands._spawn_post_turn_maintenance = original_spawn

        self.assertEqual(gate.calls, [("g", False)])
        self.assertEqual(calls[0][5], "player")
        self.assertEqual(reply.messages, ["player reply"])

    async def test_scheduling_snapshot_does_not_become_authoritative_state(self):
        async def noop_dm(*args):
            raise AssertionError("DM should not be called")

        async def noop_image(*args):
            raise AssertionError("image should not be called")

        state = GroupState(group_id="g", active=True, game_started=True, kp_assistant_user_id="kp-user")
        calls = []

        def fake_run_turn(state_arg, user_id, display_name, text, resolved_location, speaker_role):
            calls.append((state_arg, user_id, display_name, text, resolved_location, speaker_role))
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
        original_run_turn = commands.keeper.run_turn
        original_resolve = commands._resolve_map_action_transaction
        original_spawn = commands._spawn_post_turn_maintenance
        with StateStorePatch(commands) as store:
            store.put(state)
            gate = MutatingGateSpy(store)
            commands.locks.get_keeper_priority_gate = gate
            commands.keeper.run_turn = fake_run_turn
            commands._resolve_map_action_transaction = lambda *args: None
            commands._spawn_post_turn_maintenance = lambda conversation_id: None
            try:
                reply = ReplyCollector()
                await commands.handle_text_message(
                    "g", "kp-user", lambda: None, reply, noop_dm, noop_image, noop_image, "now IC"
                )
            finally:
                commands.locks.get_keeper_priority_gate = original_gate
                commands.keeper.run_turn = original_run_turn
                commands._resolve_map_action_transaction = original_resolve
                commands._spawn_post_turn_maintenance = original_spawn

        self.assertEqual(gate.calls, [("g", True)])
        self.assertEqual(calls[0][2], "Former KP Now Player")
        self.assertEqual(calls[0][5], "player")
        self.assertEqual(calls[0][0].kp_assistant_user_id, "")
        self.assertEqual(reply.messages, ["latest-state reply"])


if __name__ == "__main__":
    unittest.main()
