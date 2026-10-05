# `/coc` system subcommands as a table of handlers

[繁體中文](system_command_table_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `e4c6044`.

## Problem

`handle_system_command` in `app/commands/handlers/system.py` was 641 lines: an `if sub == ...` chain with every subcommand's body inline, and a 267-line `scenario` branch that was itself a chain on the action word. A reader looking for what `/coc autoroll` does had to scroll past `/coc scenario`. See `docs/architecture/main_v2_architecture_review.md` (F10).

## Constraint: no command changes

These are cold-path administrative commands; the aim is readability, not behaviour. Every subcommand answers the same text, takes the same locks and calls the same services as before. The mutation-admission decorator, the replacement-guard check that precedes `newgame`, `end`, `rollback`, `era` and the scenario actions that replace a scenario, and the unknown-command reply are unchanged.

## Layout

- `handle_system_command` keeps its signature and decorator, runs the replacement guard, then looks the subcommand up in `_COMMANDS` and calls the handler with a frozen `_Call` carrying its arguments. Unknown subcommands get the same "未知的系統指令" reply.
- Each former `if sub == ...` branch is a handler `async def _<name>_command(call)`, its body moved without edits apart from one line at the top that binds the arguments it uses to the local names the body already used. `checkpoint`/`checkpoints`/`rollback` share one handler, as do `digest`/`digests`, exactly as the old `sub in (...)` tests did.
- `/coc scenario` looks its action up in `_SCENARIO_ACTIONS`; each action (`source`, `template`, `cards`, `import`, `merge`, `list`, `reparse`, `cancel`, `use`, `clean`) is its own handler taking the call and the state loaded once for the command. An unknown action still gets the usage line.
- Adding a subcommand is a handler and one table row.

## Verification

`tests/test_system_command_table.py`: every subcommand the router sends here has a handler, the scenario actions the router treats as long operations have one, aliases share a handler, the entry point stays a guard plus a dispatch (under 45 lines), an unknown subcommand and an unknown scenario action reply as before, and dispatch passes the caller's arguments through. The existing command, opening, scenario and sudo tests pass unchanged. `ruff check .`, `mypy app` and the full `pytest` pass.
