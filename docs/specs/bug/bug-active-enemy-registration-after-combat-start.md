# Active enemies omitted after combat starts

[繁體中文](bug-active-enemy-registration-after-combat-start_zh.md) | [Docs index](../../README.md)

Category: `bug`. Status: **backlog**.

## Evidence

The 16-turn Haunting combat smoke on the unchanged `main_v2` prompt called `start_combat` but never called `add_npc_to_combat`. The same focused harness initially showed the same omission on the consolidated combat-routing branch. Because the control failed too, that observation alone does not establish a routing-table regression. An earlier run of the original combat wording called `add_npc_to_combat` three times, so the behavior is intermittent and needs repeated controlled measurement.

In the later 16-turn integration smoke, both tools succeeded, but on consecutive player turns. The `start_combat` turn used the configured four-tool budget on three `search_scenario` calls and `start_combat`; the next turn called `add_npc_to_combat`. This is direct evidence that a prompt request to register enemies in the same tool sequence can exceed `MAX_TOOLS_PER_TURN=4` when retrieval consumes the budget.

## Expected behavior

When sourced enemies are already active at formal combat start, each must be registered with scenario armor, attacks, abilities, usage limits, and a distinct display name for simultaneous instances of the same type before the first combat turn is handed off. A dormant enemy must not be registered as active until its scenario trigger occurs.

## Follow-up

Reproduce across identical scenario sequences and inspect Executor tool calls, receipts, and final combat state. Determine whether retrieval budget, missing source evidence, tool scope, the model's routing decision, or a Python handoff causes the omission. A fix must preserve the turn's tool limit against autonomous overreach while allowing the atomic registration of sourced, triggered enemies. Do not register enemies whose scenario trigger has not occurred. The prompt routing reminder is a guardrail, not proof that this intermittent defect is resolved.
