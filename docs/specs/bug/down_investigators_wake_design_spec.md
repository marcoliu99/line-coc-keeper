# HP 0 is down after a fight, and an unconscious investigator is not locked out

[繁體中文](down_investigators_wake_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `b0c11f3`.

## Problem

From the Haunting soak run of 2026-10-12 at b0c11f3:

- **HP 0 played on.** Edith was at HP 0 when the fight ended, then acted for 11 turns and rolled three APP checks. In 7e, HP 0 means unconscious, or dying with a major wound. `knocked_out` required HP above 0, so she was never held.
- **Unconscious meant locked out.** James failed his major-wound CON check at HP 1. His companions were standing, so every line he sent got 「隊友可以替James急救」. Nobody gave First Aid, and his player sat out 44 turns (11 lines).

## Change

- **HP 0 is down.** `unconscious_wake.knocked_out` now also covers HP 0 and dying, outside combat. Dead is excluded.
- **A dying investigator waits.** HP 0 with a major wound, or a `dying` injury, waits for First Aid on every line, with 「…倒地瀕死…隊友要盡快替…急救」. The combat obligations decide whether they live.
- **Told once, then woken.** Any other knocked-out investigator with a companion standing gets the First Aid line once. It also tells the player that without First Aid, their next action wakes them after a time skip. The offer is remembered in the injury record (`first_aid_offered`), and the next line takes the existing time-skip wake. When nobody is left standing, the first line wakes them, as before.
- **Waking from HP 0** leaves the investigator at HP 1. The major wound stays, and the wake clears the offer.

## Verification

`tests/test_unconscious_wake.py`:
- HP 0 after a fight is down and wakes at HP 1;
- a dying investigator waits every time;
- told once, the next line wakes them, and the offer is cleared on waking;
- the waiting reply carries the wake notice and records the offer;
- a running fight or death is not this rule.
