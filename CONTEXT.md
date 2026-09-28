# COC Keeper Bot

A Discord bot in which an AI runs Call of Cthulhu 7e sessions for a group of players, with a human helper who can steer and correct it.

## Language

### People and roles

**Keeper**（守秘人）:
The AI game master that runs every turn: it adjudicates actions, calls game tools and narrates.
_Avoid_: KP (for the AI in prose), GM

**KP Assistant**（KP 助手）, short form **KP**:
The human in a group who helps the Keeper: they can steer or correct it and run group-level administration. "KP" always means this human, never the Keeper.
_Avoid_: GM (for the human)

**Host**（主辦人）:
A server administrator who manages the bot for a Discord server but doesn't play in its games; identified by a Discord server role. Hosts may perform group-level administration alongside the KP Assistant.
_Avoid_: Keeper, Discord Keeper

**Player**（玩家）:
A human taking part in a game through their investigator. A player owns exactly one investigator per game.
_Avoid_: user, owner (in prose)

**Investigator**（調查員）:
The character a player plays in a game.
_Avoid_: PC (in prose), role card (for the character itself)

### Checks

**Pending check**（待處理檢定）:
A check the Keeper has asked for that is waiting for its player to roll. It belongs to the player, and a player has at most one at a time.
_Avoid_: queued check

**Luck decision**（幸運值決定）:
The player's choice, after a roll, of whether to spend Luck points to lower it to a better result. While it's open, the player can't be given a new pending check.
_Avoid_: luck buy-up (in prose)

**Major wound**（重傷）:
A single hit that deals at least half of an investigator's maximum HP without dropping them to 0. It requires a CON check.
