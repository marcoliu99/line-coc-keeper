# Implemented mechanics and remaining rule gaps

[繁體中文](rules_reference_zh.md)

## Authority and scope

This is an implementation reference for the repository, not a new verification of the complete CoC rulebooks. Code in `app/dice.py`, `app/combat.py`, `app/luck.py` and their regression tests defines supported mechanics; the scenario defines its own content.

## Checks and Luck

Supported: d100 tiers, bonus/penalty dice, regular/hard/extreme required difficulty, SAN loss expressions, owner-triggered pending checks and optional group autoroll. Difficulty must be met, not merely some success tier. Luck upgrades are offered whenever legal, useful and affordable; SAN is excluded. Blank pregen Luck remains an explicit owner roll.

## Combat

DEX order, distinct NPC cards, armor, attacks, abilities, turn/round effects, damage bonus and impaling calculations are supported. Dodge wins an equal successful melee tier; Fight Back needs a better one. Ranged defenses have their own rules. Raw versus final damage tools avoid double armor reduction. Major-wound checks respect pending/autoroll ownership.

## Remaining gaps

No fully general paired-player opposed-check coordinator, combined-skill single-roll tool, grapple/disarm Build engine, or complete automatic surprise/outnumbered rules engine is claimed. Difficulty selection, investigation pacing and many availability judgments still depend on Keeper interpretation. Supported special abilities do not imply every published spell is implemented.

## Possessions and acquisition

Mundane personal items may be permitted by narrative policy. Important acquisitions need scenario or established-scene evidence. Travel, availability, provenance and legality remain Keeper judgments. Credit Rating, lifestyle, price and cash do not gate acquisition; there is no active quote, debit or purchase receipt system.
