# Reading boolean settings one way

[繁體中文](boolean_settings_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `70aea21`.

## Problem

`app/config.py` read most boolean settings with `_env_bool` (accepts `1/true/yes/on` and `0/false/no/off`; anything else keeps the default and is reported at startup), but six were parsed inline as `os.environ.get(name, default).lower() in ("1", "true", "yes")`: `SCENARIO_RAG_ENABLED`, `SCENARIO_LIFECYCLE_KP_ONLY`, `OPENAI_OMIT_TEMPERATURE`, `DEBUG_SHOW_INTERNAL_IDS`, `TURN_FALLBACK_RECOVERY_ENABLED` and `RETRIEVAL_REUSE_FOR_FOLLOWUPS` (architecture review F8). The two readers accepted different spellings, and for a flag that is on by default a typo silently turned it off.

## Change

The six flags use `_env_bool` with their existing defaults. `tests/test_config_flags.py` pins the accepted spellings, the default-and-report behaviour for an unrecognised value, the default of each of the six, and fails if `config.py` parses a boolean inline again.

## Behaviour change

| Value | Flag default | Before | After |
| --- | --- | --- | --- |
| `1`, `true`, `yes` (any case, surrounding spaces) | either | on | on |
| `0`, `false`, `no` | either | off | off |
| `on` | either | **off** | on |
| `off` | either | off | off |
| an unrecognised value (`ture`) | `false` | off | off, and reported at startup |
| an unrecognised value (`ture`) | `true` (`TURN_FALLBACK_RECOVERY_ENABLED`, `RETRIEVAL_REUSE_FOR_FOLLOWUPS`) | **off, silently** | **on (the default), and reported at startup** |

The two rows in bold are the whole change. `on` was already accepted by the other flags, and a typo no longer switches a default-on feature off without a word. A deployment that relied on a misspelling to disable one of those two features must now write `false`.

## Not in scope

Running the test suite with non-default flags in CI: most tests assume the defaults, so that needs a decision about which tests are flag-neutral first. See `docs/guides/configuration_profiles.md` for what is and is not covered.
