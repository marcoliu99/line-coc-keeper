"""docs/specs/bug/bug-combat-reveal-beat-skipped-before-lethal-damage.md.

Real production incident (2026-09-28, conversation ed20ad1edfb8, "The Haunting"):
an investigator drew a gun on an enemy the scenario describes as motionless
and "reluctant to move at all unless threatened." The Keeper correctly
started combat and registered the enemy on the threat, but the immediate
narration ("屍體仍伏在原處") and the later damage-resolution narration
("他倒伏下去，再沒有動靜") both described an inert body — the scenario's
own required beat (the enemy visibly rising/animating in response to the
threat, which is also what the scenario ties its Sanity-roll trigger to)
was never narrated. Two players, unable to tell anything had happened,
independently pushed back ("屍體應該要動啦" / "屍體沒動，為什麼要SAN"),
and the Keeper then re-ran the entire encounter from scratch instead of
just narrating the beat it had skipped — a second add_npc_to_combat, a
second damage roll (23, versus the original 22), and several minutes of
self-correction over a single shot.

This rule targets the root cause: when a scenario documents a dormant
enemy's activation trigger (rises, wakes, animates), that beat must be
narrated as part of the same resolution that applies damage to it —
never a silent jump from "still motionless" straight to "defeated."

The rule itself is written in English even though the rest of the
static prompt is Traditional Chinese: it is a tool-calling instruction
consumed only by the model, not player-facing narration, and an
English phrasing measured ~32% fewer tokens than an equally-tightened
Chinese version on this session's provider tokenizer — a real saving
since the static prompt is resent every turn. This is a new, previously
untuned bullet, so there is no consistency cost to weigh against that.
"""
import unittest

from app import prompt_builder
from app.models import GroupState


class CombatRevealBeatPromptTests(unittest.TestCase):
    def test_the_prompt_requires_narrating_the_activation_beat_before_lethal_resolution(self):
        prompt = prompt_builder.build_static_prompt(GroupState(group_id="combat-reveal-beat"))
        self.assertTrue(
            any(
                phrase in prompt
                for phrase in ("wakes/rises", "wake/rise", "rises/wakes")
            ),
            "missing guidance about narrating a dormant enemy's activation/rising beat",
        )
        self.assertTrue(
            "never jump straight from" in prompt,
            "missing the explicit ban on silently jumping from motionless to defeated",
        )


if __name__ == "__main__":
    unittest.main()
