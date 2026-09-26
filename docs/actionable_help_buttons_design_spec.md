# Actionable Discord Help Buttons — Design Spec

## Status and goal

Draft for review. Branch: `enhancement/actionable-help-buttons`. Integration branch: `main_v2`.

Today, Discord `/coc help` provides persistent root → category → detail navigation. A detail page prints the command syntax, but its buttons only navigate back. This change gives an executable action to each help entry whose inputs can be collected in Discord. The button invokes the existing command behavior using the clicking user's identity and current channel state. The text command remains available.

The first priority is:

- `/coc pc 角色名 [職業]` and `/coc create 角色名 [職業]`: pressing **建立角色** opens a form for the name and optional occupation; submission starts the same character command.
- `/coc scenario use 劇本ID`: pressing **選擇劇本** loads the current library and presents titles with IDs in a paged select; selection uses the selected library ID, not a title typed by the user.

## Existing interfaces

- `app/help_registry.py`: `HelpEntry`, `HelpPage`, `HelpAction`, visibility and path lookup.
- `app/help_registration.py`: central metadata for all help entries and their `command` token prefixes.
- `app/help_service.py`: re-renders pages from current `GroupState`.
- `app/discord_bot.py`: persistent `HelpButton` currently handles navigation only. `on_message` gives actual text commands to `command_router.handle_text_message`.
- `app/commands/router.py`: the authoritative command entry, conversation locks, check/Luck locks and handler routing. Character and scenario handlers apply their own state and permission checks.
- `scenario_library.list_scenarios()`: current library manifests, including ID and title; `scenario use` revalidates the selected ID with `load_context`.

## User flow

```text
/coc help → category → command detail → 執行
                                      ├─ no input → run command
                                      ├─ parameters → modal or short choice
                                      ├─ live object → paged select
                                      └─ consequential action → confirmation
                                                          │
                                                          ▼
                                           command_router.handle_text_message
                                                          │
                                                          ▼
                                           existing locks, guards and handlers
```

The help message stays available for everyone in the channel. Interaction forms, selections, and confirmations are scoped to the person who clicked. A successful operation sends the same public/private command output as the text path; input errors can be ephemeral. The displayed command syntax remains on the detail page.

### Create a character

1. From the `pc` or `create` detail page, click **建立角色**.
2. Open a modal with required **角色名稱** and optional **職業**. The current parser treats each as one token, so the form validates nonempty values, length, and whitespace rather than silently splitting a multiword name/occupation.
3. On submission, reload current state. If the scenario gained pregens, the player already has a character or creation session, or the clicker became KP Assistant, show the existing handler's response. The modal itself grants no exception.
4. Build the exact `/coc pc ...` or `/coc create ...` command from validated fields and dispatch through the shared command router with `interaction.user.id`.
5. The existing character handler creates the sheet/session and persists it; help never calls `generate_investigator` or `creation.start_creation` itself.

The `create` detail also offers the existing session operations `status`, `done`, and `cancel`; `alloc` uses a form for pool, skill and points.

### Select a scenario

1. From the `scenario/use` detail, click **選擇劇本**. Read `scenario_library.list_scenarios()` at that moment; show title and short ID while storing the full ID as the option value.
2. Page the list at no more than 25 options per select. Include previous/next controls and an empty-library message. Never truncate the library silently.
3. Bind the temporary select to the clicker and channel. Before executing, check the selected full ID is still present in a freshly loaded library list. Then dispatch `/coc scenario use <ID>` through the existing router.
4. The command handler remains responsible for KP Assistant authorization, pending Luck/upload guards, context loading, timeline reset, pregen installation and persistence. A title, page index or stale option is never treated as an authority to load a scenario.
5. Changing a scenario is consequential: show its title and a confirmation step before dispatch. Recheck current state and library again on confirmation.

Discord's current component docs describe select options and modal inputs; the installed library is `discord.py>=2.4.0`. The implementation should use the APIs available in the installed version and keep option paging at 25. See [discord.py UI API](https://discordpy.readthedocs.io/en/stable/interactions/api.html) and [Discord component reference](https://docs.discord.com/developers/components/reference).

## Other help entries

The registry must declare an explicit action plan for every visible entry. Do not infer an executable command by copying the first `usage` or `examples` string: those contain placeholders and sometimes several distinct operations. A missing action plan leaves the help detail readable and is caught by a registry coverage test.

| Interaction plan | Entries and behavior |
| --- | --- |
| Direct, no parameters | `sheet`, `characters`, `pregens`, `scenario list`, `status`, `where`, `combat status`, `checkpoints`, `digests`, `away`, `back`, `leavemap`, `index`, `start`, and read-only creation/digest/list operations. Each dispatches its explicit command tokens. |
| Fixed choice | `autoroll on/off`, `era 1920/modern`, `kp / kp quit`, `pdf new/fix`, `create status/done/cancel`, `luck roll/skip/target tier`. Only offer a choice that the current state makes meaningful; handlers still validate. |
| Modal for free-form or numeric input | `pc`, `create`, `alloc`, `setskill`, `setconnection`, `combat addnpc/addally/damage`, `showpage`, `enter`, `setpersona`, `checkpoint`, `roll`. Inputs are parsed to the same argument boundaries as the text command; `/roll` continues to use its own route. |
| Select from live state | `scenario use`, `pregen`, `usepregen`, `switch`, `retire`, pending `check` choice, `rollback`, `digest`, `scenario cards`, and available library/staged item choices. Rebuild options when opened; page any list above the component limit; revalidate on submit. |
| Confirm before execution | `newgame`, `end`, `combat end/next`, `rollback`, `scenario use/clean/cancel`, `scenario cards delete`, `digest clean`, `create cancel`, and other actions that discard or advance existing state. Confirmation is private to the clicker and tied to the observed state revision/timeline. |
| Needs a file or multi-step external input | PDF/role/map uploads and `scenario import/merge/reparse` require a dedicated picker or the existing upload flow. Help must expose an honest **開始上傳／選擇暫存檔** entry when supported; until then, it keeps the specific upload instruction and does not show a misleading **執行** button. `sudo` requires a target user and an allowed delegated command; use a dedicated user/command picker before enabling execution. |

For commands with multiple suboperations in one help entry, render multiple named actions rather than executing an ambiguous default. Existing check and Luck buttons already support pending decisions; the help action should hand off to the same deterministic handlers and must not create a second roll path.

## Action model and command dispatch

Add a small, validated `HelpExecution` definition keyed by canonical `HelpEntry.path`: action ID, label, input kind, explicit command tokens or a typed command builder, and confirmation policy. Registration rejects duplicate action IDs, unknown paths and empty command plans; runtime lookup rejects an action whose entry is no longer visible. Keep this metadata independent of Discord so it can be tested and documented.

`HelpButton` remains the persistent navigation entry. The detail page adds a separate persistent action button whose custom ID carries only version, channel and action ID. Dynamic callbacks resolve the current registry definition; they do not trust a command string, scenario ID, target user, permission flag or result text from the custom ID. Temporary modal/select/confirmation callbacks bind actor ID and original channel. Validate custom ID length and action identifiers.

The adapter builds validated command text only at the final step and calls `command_router.handle_text_message` with the same callbacks used by `on_message`: display name, reply, DM, image, mention formatter, Discord Keeper role and post-turn hook. It must not call handlers directly or emit a fake user message. For long operations, acknowledge the interaction before work and send follow-up output through the existing reply helpers; for modals, opening the form is the initial response. Final output and pending Check/Luck buttons must follow the same delivery and claim logic as text commands.

On final submission, reload state under the normal command lock and re-evaluate the action's visibility and any observed state revision/timeline. A stale form offers to reopen the current help page rather than applying an outdated choice. The handler still enforces its own authorization and game rules. Consequential confirmations are single-use for a given actor and observed revision; a second submit cannot silently execute the same captured action twice. No new `GroupState` fields or database schema are planned; temporary UI state lives only for the interaction lifetime, and the persistent help button can always reopen it after restart.

## Scope and non-goals

- Discord help only; other platforms keep their text commands.
- Preserve the three-level help navigation and generated `docs/player_command_reference.md`.
- Preserve command semantics, permission checks, parser rules and public/private output.
- Do not let the LLM choose commands or create executable action metadata.
- Do not add a generic arbitrary-command form; each executable help entry needs a reviewed typed action plan.
- Do not silently execute destructive operations from a single detail click.

## Verification

- Registry tests cover every help entry's action plan or explicit upload/multi-step exception, unique action IDs and bounds.
- Adapter tests cover persistent IDs after restart, channel scope, clicker binding, modal values, paged selects including an empty library and more than 25 scenarios, stale/deleted IDs, changed state/timeline, expired temporary views and duplicate confirmation.
- Integration tests compare button and text routes for character creation and scenario selection, including the same stored state and visible reply. Repeat for a no-argument command, a fixed choice, a pending check/Luck action and a KP-only denial.
- Test that a non-KP cannot select a scenario even from another user's help message, that a player with pregens cannot use custom character creation, and that clicking a command twice cannot double-apply a consequential action.
- Run the full unit suite, Ruff and mypy. Before a PR, fetch and align with latest `main_v2`.

## Decision for review

The first implementation can either cover every registry entry with a typed interaction plan, or focus on the most used actions and leave file/multi-step commands as explicit guided entries. The branch has no runtime changes yet. Confirm the coverage expectation before implementation.
