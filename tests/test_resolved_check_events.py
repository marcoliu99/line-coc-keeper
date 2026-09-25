from __future__ import annotations

import unittest
from unittest.mock import patch

from app.legacy_commands import _persist_resolved_check_event
from app.models import Character, GroupState
from app.services.prompt_config import build_resolved_check_history_block


class ResolvedCheckEventTests(unittest.TestCase):
    def test_old_state_defaults_to_empty_and_new_state_round_trips_events(self):
        old_state = GroupState.from_dict({"group_id": "g"})
        self.assertEqual(old_state.resolved_check_events, [])

        state = GroupState(
            "g",
            resolved_check_events=[
                {
                    "event_id": "check-1",
                    "timeline_id": "timeline-a",
                    "owner_id": "p1",
                    "state_effects": [],
                }
            ],
        )
        restored = GroupState.from_dict(state.to_dict())
        self.assertEqual(restored.resolved_check_events, state.resolved_check_events)

    def test_event_persists_only_the_actual_committed_hp_change(self):
        state = GroupState("g", timeline_id="timeline-a")
        char = Character(
            name="Marco", owner_id="p1", character_id="char-1", hp=7, hp_max=12, luck=35
        )
        state.characters_by_id[char.character_id] = char
        state.set_active_character("p1", char.character_id)
        before = {"hp": 12, "san": 50, "mp": 10, "luck": 35}
        seed = {
            "event_id": "check-1",
            "check_id": "check-1",
            "timeline_id": "timeline-a",
            "owner_id": "p1",
            "character_id": "char-1",
            "investigator": "Marco",
            "skill": "DEX",
            "skill_value": 70,
            "roll": 88,
            "difficulty": "hard",
            "outcome": "failure",
            "state_before": before,
        }

        with patch("app.legacy_commands.load_state", return_value=state), patch(
            "app.legacy_commands.save_state"
        ) as save:
            _persist_resolved_check_event("g", seed)

        event = state.resolved_check_events[0]
        self.assertEqual(event["state_effects"], [{
            "field": "HP", "before": 12, "after": 7, "delta": -5,
        }])
        save.assert_called_once_with(state, reason="resolved_check_event")

    def test_failure_without_committed_state_change_has_no_effect_claim(self):
        state = GroupState("g", timeline_id="timeline-a")
        char = Character(name="Marco", owner_id="p1", character_id="char-1", hp=12, luck=35)
        state.characters_by_id[char.character_id] = char
        state.set_active_character("p1", char.character_id)
        seed = {
            "event_id": "check-2", "check_id": "check-2", "timeline_id": "timeline-a",
            "owner_id": "p1", "character_id": "char-1", "investigator": "Marco",
            "skill": "DEX", "skill_value": 70, "roll": 88, "difficulty": "hard",
            "outcome": "failure", "state_before": {"hp": 12, "san": 50, "mp": 10, "luck": 35},
        }

        with patch("app.legacy_commands.load_state", return_value=state), patch(
            "app.legacy_commands.save_state"
        ):
            _persist_resolved_check_event("g", seed)

        self.assertEqual(state.resolved_check_events[0]["state_effects"], [])

    def test_history_context_separates_past_roll_from_current_values(self):
        block = build_resolved_check_history_block(
            [{
                "investigator": "Marco", "skill": "DEX", "skill_value": 70,
                "roll": 88, "difficulty": "hard", "outcome": "failure",
                "state_effects": [{"field": "HP", "before": 12, "after": 7, "delta": -5}],
            }],
            {"HP": "7/12", "Luck": 35},
        )

        self.assertIn("目前角色數值（權威存檔）", block)
        self.assertIn("HP 7/12", block)
        self.assertIn("DEX 70%", block)
        self.assertIn("88", block)
        self.assertIn("HP 12 → 7（-5）", block)
        self.assertIn("與本回合工具結果分開", block)

    def test_event_history_is_bounded_when_appending(self):
        state = GroupState("g", timeline_id="timeline-a")
        char = Character(name="Marco", owner_id="p1", character_id="char-1", hp=10, luck=35)
        state.characters_by_id[char.character_id] = char
        state.set_active_character("p1", char.character_id)
        state.resolved_check_events = [
            {"event_id": f"old-{i}", "timeline_id": "timeline-a", "owner_id": "p1"}
            for i in range(20)
        ]
        seed = {
            "event_id": "new", "check_id": "new", "timeline_id": "timeline-a",
            "owner_id": "p1", "character_id": "char-1", "investigator": "Marco",
            "skill": "DEX", "skill_value": 70, "roll": 50, "difficulty": "regular",
            "outcome": "success", "state_before": {"hp": 10, "san": 50, "mp": 10, "luck": 35},
        }
        with patch("app.legacy_commands.load_state", return_value=state), patch(
            "app.legacy_commands.save_state"
        ):
            _persist_resolved_check_event("g", seed)

        self.assertEqual(len(state.resolved_check_events), 20)
        self.assertEqual(state.resolved_check_events[-1]["event_id"], "new")
        self.assertEqual(state.resolved_check_events[0]["event_id"], "old-1")


if __name__ == "__main__":
    unittest.main()
