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

### Scenario authority

**Scenario rule（劇本規則）**:
A source-backed description of what exists or may happen in the scenario. A conditional rule does not mean its triggering event has already happened.

**Canonical event（正典事件）**:
An occurrence established during play by an authorized decision or committed game resolution.

**Canonical fact（正典事實）**:
A world fact grounded in scenario evidence, a canonical event, or an explicit authorized KP decision. Conditional scenario content becomes an established play fact only when its condition is met.

**Narrative presentation（敘事呈現）**:
What the Keeper actually described to players. It preserves conversational and sensory continuity, but cannot independently establish a consequential object, clue, location, identity, trigger, or mechanical state.
_Avoid_: Canonical fact

**Hard world fact（世界硬事實）**:
A specific consequential assertion about an entity, quantity, location, identity, resource, secret, trigger, or mechanical state that can affect later adjudication.

**Incidental prop（附帶物件）**:
An object or detail that can support ordinary narration or possession without changing scenario clues, triggers, resources, or mechanical outcomes. Its existence does not establish hidden contents or plot relevance.

**Plot relevance（劇情關聯）**:
The supported connection between an object or statement and scenario progress, clues, triggers, or adjudicated mechanics. It is distinct from the object's mere existence or possession.

**Access item（通行道具）**:
An item with a scenario-supported or reasonably adjudicated use to enter or unlock a place. Its use can follow from the surrounding scene even when a translation does not explicitly pair the item and lock. It can matter to scenario progress without being a clue: a clue reveals information, while an access item permits an action. Mere possession of an unrelated item does not give it every access capability.

**Clue（線索）**:
Information revealed through a supported scenario condition, committed event, or authorized ruling. An object can carry a clue, but being useful or having a key shape does not make it a clue by itself.

**Player claim（玩家主張）**:
A player's statement or hypothesis about the world, distinct from the action the player is attempting. It does not establish the truth of its content.
_Avoid_: Established fact

**KP canon override（KP 正典覆寫）**:
An explicit, recorded KP decision that replaces a conflicting scenario or earlier narrative fact. Ordinary KP elaboration does not silently override the scenario; committed mechanical results need their own authorized correction.

**Player correction request（玩家更正請求）**:
A player's out-of-game challenge to a previous Keeper presentation or ruling. It is a claim to be resolved, not an automatic change to world truth or mechanics.

**Narrative correction（敘事更正）**:
An acknowledged change to how a prior scene was described when no consequential world or mechanical fact is altered.

**Unverified record（待驗證紀錄）**:
A retained fact or clue without a verified source, including older records created before provenance was tracked. It may remain visible as history, but does not authorize consequential game actions until verified.

### Scenario presentation

**Automatic image presentation（主動圖片展示）**:
The Keeper's choice to show a scenario picture during narration without a player using the dedicated page-display command. It can be replaced by an authorized text description without changing what is true in the scenario.

**Requested page display（指定頁面展示）**:
A player's explicit use of the page-display command to view a scenario image. It remains subject to chapter access and recipient permissions, independently of whether automatic image presentation is enabled.
