# CoC7e player command reference

This reference lists all manual Discord bot commands. Visibility describes when Help buttons appear; hidden commands can still be entered when their runtime requirements are met. Command tokens and localized sample names are preserved. [繁體中文](player_command_reference_zh.md).

## Characters

### Allocate skill points

Allocate points during interactive character creation.

Usage:
- `/coc alloc occ|int 技能名 點數`

### List my characters

List owned characters and the active character.

Usage:
- `/coc characters`

### Create an investigator interactively

Roll attributes, then allocate occupation and personal-interest skill points.

Usage:
- `/coc create 角色名 [職業]`
- `/coc alloc occ|int 技能名 點數`
- `/coc create status|done|cancel`

Examples:
- `/coc create 小明 記者`

Visibility: only when the scenario has no pregenerated characters.

### Quick investigator creation

Create a custom investigator.

Usage:
- `/coc pc 角色名 [職業]`

Examples:
- `/coc pc 小明 記者`

Visibility: only when the scenario has no pregenerated characters.

### View pregen details

Inspect a pregenerated investigator's full abilities.

Usage:
- `/coc pregen 編號`

Examples:
- `/coc pregen 1`

Visibility: only when the scenario has pregenerated characters.

### List pregens

List the scenario's pregenerated investigators.

Usage:
- `/coc pregens`

Examples:
- `/coc pregens`

Visibility: only when a scenario is loaded.

### Confirm purchase

Settle payment and acquisition according to the displayed quote.

Usage:
- `/coc purchase 報價ID`

### View purchases

View cash, pending quotes and recent purchases.

Usage:
- `/coc purchases`

### Release active character

Remove the active character binding while retaining historical character data.

Usage:
- `/coc retire [角色名]`

Examples:
- `/coc retire 小明`

### Set key background connection

Set the character's most important person, place or possession.

Usage:
- `/coc setconnection 角色名 敘述`

### Edit skill

Manually correct an owned character's skill value.

Usage:
- `/coc setskill 角色名 技能名 數值`

### View character sheet

View your investigator's sheet.

Usage:
- `/coc sheet`

Examples:
- `/coc sheet`

### Switch active character

Switch between characters you own.

Usage:
- `/coc switch 角色名`

Examples:
- `/coc switch 小明`

### Claim a pregen

Select a pregenerated investigator from the scenario.

Usage:
- `/coc usepregen 編號 [自訂名稱]`

Examples:
- `/coc usepregen 1`

Visibility: only when the scenario has pregenerated characters.

## Checks

### Toggle autoroll

Off by default; every player can toggle this group setting.

Usage:
- `/coc autoroll on|off`
- `/coc autoroll`

Examples:
- `/coc autoroll off`

### Skill or sanity check

Players trigger rolls with /coc check or a button by default; Keeper/system rolls automatically only when group autoroll is enabled.

Usage:
- `/coc check [技能或選項名稱]`

Examples:
- `/coc check 閃避`

### Luck roll and outcome selection

Players roll blank pregen Luck; existing verified sheet values are retained. Eligible checks allow spending Luck for a better result.

Usage:
- `/coc luck roll`
- `/coc luck skip|regular|hard|extreme`

Examples:
- `/coc luck roll`

## Combat

### Add an allied NPC

Add an allied NPC to combat.

Usage:
- `/coc combat addally 名稱 DEX HP`

### Add an enemy

Add an NPC/enemy to the current combat.

Usage:
- `/coc combat addnpc 名稱 DEX HP`

### Adjust combat HP

Apply HP changes to a character or NPC in combat.

Usage:
- `/coc combat damage 名稱 增減量`

Examples:
- `/coc combat damage 深潛者 -4`

### End combat

End the current combat and clear combat state.

Usage:
- `/coc combat end`

### Advance turn

Advance to the next actor.

Usage:
- `/coc combat next`

### Start combat

Create DEX-based initiative order.

Usage:
- `/coc combat start`

### View combat status

Inspect round and initiative order.

Usage:
- `/coc combat status`

## Maps

### Enter map

Manually enter a floor plan on the specified page.

Usage:
- `/coc enter 頁碼`

### Leave map tracking

Leave the current map and return movement adjudication to Keeper.

Usage:
- `/coc leavemap`

### View scenario page

View an image from a scenario page.

Usage:
- `/coc showpage 頁碼`

Examples:
- `/coc showpage 16`

### View location

View the tracked room and its exits.

Usage:
- `/coc where`

## Scenarios

### Prepare English source **[KP-only]**

Export one full Markdown workbook and send it with the original PDF to external AI.
Return corrected English as downloadable Markdown; partial result imports accumulate.

Usage:
- `/coc scenario source export SCENARIO_ID`

### Import English source **[KP-only]**

Select a matching result in Help, or use the command. Complete page coverage creates
an independent English version automatically; the active game remains unchanged.

Usage:
- `/coc scenario source import SCENARIO_ID FILE.md`

### English preparation progress **[KP-only]**

Privately list complete/pending/unresolved pages, the new version and omitted-file
errors. On completion, buttons export a fresh Chinese template or select the new
English version using the existing scenario-switch confirmation.

Usage:
- `/coc scenario source status SCENARIO_ID [EXPORT_ID]`


### Mark away

Mark yourself away; combat skips your turns.

Usage:
- `/coc away`

### Return to play

Clear away status and resume participation.

Usage:
- `/coc back`

### Cancel scenario processing

Discard the pending similar-scenario PDF operation.

Usage:
- `/coc scenario cancel`

### Manage manual role cards **[KP-only]**

List or remove this group's manual cards saved for a scenario.

Usage:
- `/coc scenario cards list 劇本ID`
- `/coc scenario cards delete 劇本ID 資產ID`

Notes:
- Only the current KP Assistant or Discord Keeper may execute this.

### Clean scenario library

Delete library entries not used by any group.

Usage:
- `/coc scenario clean 劇本ID`

### End game

End the current game.

Usage:
- `/coc end`

### Set era

Select a 1920s or modern setting.

Usage:
- `/coc era 1920|modern`

### Import server PDF **[KP-only]**

Import a large PDF from IMPORT_DIR; restricted to the current KP Assistant.

Usage:
- `/coc scenario import 檔名.pdf`

### Rebuild scenario index

Rebuild NPC/creature and location indexes manually.

Usage:
- `/coc index`

Visibility: only when a scenario is loaded.

### List scenarios

View available scenarios and the current selection.

Usage:
- `/coc scenario list`

Examples:
- `/coc scenario list`

### Merge PDF parts **[KP-only]**

Merge staged Discord PDF parts in the specified order; restricted to the current KP Assistant.

Usage:
- `/coc scenario merge 暫存ID1 暫存ID2 ...`
- `/coc scenario merge list`

### Start a new game

Reset group state and begin a new game.

Usage:
- `/coc newgame`

### Resolve scenario PDF choice

Choose whether an uploaded PDF starts a new scenario or corrects the current one.

Usage:
- `/coc pdf new|fix`

### Reparse scenario

Reprocess the pending similar-scenario PDF.

Usage:
- `/coc scenario reparse`

### Set Keeper persona

Customize or reset Keeper's narrative style.

Usage:
- `/coc setpersona 文字`
- `/coc setpersona reset`

### Start the story

Produce the scenario opening when characters are ready.

Usage:
- `/coc start`

Examples:
- `/coc start`

Visibility: only when a scenario is loaded.

### View game status

View current scenario and character state.

Usage:
- `/coc status`

### Approve Chinese template **[KP-only]**

Approve a template for RAG after reviewing source and rules.

Usage:
- `/coc scenario template approve 劇本ID 模板版本`

### Export localization workbook **[KP-only]**

Export source IDs, pages and original text for external localization, without a translation API call.

Usage:
- `/coc scenario template export 劇本ID`

### Import Chinese template **[KP-only]**

Select the scenario's Markdown file and import it; the file must already be in the server import directory.

Usage:
- `/coc scenario template import 劇本ID 檔名.md`

### Preview Chinese template **[KP-only]**

Send the KP review preview privately.

Usage:
- `/coc scenario template preview 劇本ID 模板版本`

### View Chinese template status **[KP-only]**

View imported versions and outstanding review items.

Usage:
- `/coc scenario template status 劇本ID`

### Select scenario **[KP-only]**

Select a library scenario and reviewed Chinese template version.

Usage:
- `/coc scenario use 劇本ID [模板版本]`

Examples:
- `/coc scenario use abc123 zh-TW-123456789abc`

Notes:
- Omitting the version reuses this group's selection for that scenario.

## KP Assistant

### Create checkpoint **[KP-only]**

Save complete game state for a later KP restore.

Usage:
- `/coc checkpoint [名稱]`
- `/coc checkpoint clean ID`

Notes:
- Requires the current KP Assistant or Discord Keeper role.

### List checkpoints **[KP-only]**

List this group's available checkpoints.

Usage:
- `/coc checkpoints`

Notes:
- Requires the current KP Assistant or Discord Keeper role.

### View scene digest **[KP-only]**

View the current or specified scene digest.

Usage:
- `/coc digest`
- `/coc digest 摘要ID`
- `/coc digest clean 摘要ID`

Notes:
- Requires the current KP Assistant or Discord Keeper role.

### List scene digests **[KP-only]**

List this group's scene digest history.

Usage:
- `/coc digests`

Notes:
- Requires the current KP Assistant or Discord Keeper role.

### Register cash balance **[KP-only]**

Record confirmed currency and balance; never infer cash from Credit Rating.

Usage:
- `/coc funds 角色名稱 幣別 餘額`

### Register KP Assistant

Register or release the game's KP Assistant role.

Usage:
- `/coc kp`
- `/coc kp quit`

### Restore game state **[KP-only]**

Restore group state to the selected checkpoint.

Usage:
- `/coc rollback 節點ID或唯一名稱`

Notes:
- Requires the current KP Assistant or Discord Keeper role.

### Act for a player **[KP-only]**

A KP Assistant who has released their own character may execute allowed player commands for a disconnected player.

Usage:
- `/coc sudo <@玩家> <command> [參數...]`
- `/coc sudo <@玩家> away`
- `/coc sudo <@玩家> retire [角色名]`

Notes:
- KP-only: requires the current KP Assistant or Discord Keeper role; cannot roll creation LUCK, create or claim a character for the player.
- The actor must first leave their own player binding/character-creation flow.

## Other

### Report a narrative error

Report a previous Keeper narrative for KP review without initiating an in-game action.

Usage:
- `回覆 Keeper 訊息：/coc correct <疑點>`
- `/coc correct <訊息 ID／連結> <疑點>`
- `/coc correct list`
- `/coc correct withdraw <提報編號>`

Notes:
- KP adjudication uses /coc correct approve <report-id> <public-resolution> or /coc correct reject <report-id>. Use hold <id> <entity-or-aliases> to pause a scope and supersede <old-id> <approved-replacement-id> to consolidate decisions. Targets require a message receipt in this channel's current timeline.

### Roll dice directly

Roll dice without a Keeper turn.

Usage:
- `/roll 1d100`
- `/roll 3d6+2`

Examples:
- `/roll 1d100`
