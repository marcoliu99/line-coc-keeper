# COC Keeper Bot

A Discord bot in which an AI runs Call of Cthulhu 7e sessions for a group of players, with a human helper who can steer and correct it.

## Language

### People and roles

**Keeper**（守秘人）:
The game master of a CoC game, played by the bot: it runs every turn, adjudicates actions, calls game tools and narrates, and holds the Keeper's authority to rule on the game. On Discord it is the bot account; the server role named `keeper` is the bot's own and grants no human anything.
_Avoid_: KP (for the AI in prose), GM

**KP Assistant**（KP 助手）, short form **KP**:
The human in a group who helps the Keeper: they can steer or correct it and run group-level administration. A group has at most one, who registers, hands over, or is taken over by a server manager. "KP" always means this human, never the Keeper.
_Avoid_: GM (for the human)

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

**Pending follow-up result**（待續敘結果）:
A player's check whose dice and final Luck decision have been committed, while the Keeper's follow-up has not yet safely finished. It is distinct from an unrolled pending check and never authorizes another roll.
_Avoid_: pending check (for a settled result)
