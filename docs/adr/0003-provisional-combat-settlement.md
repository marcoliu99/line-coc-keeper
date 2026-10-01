# Keep the whole battle provisional until settlement

The operator explicitly selected whole-battle provisional resources instead of the current per-action persistent character updates. All battle-caused resource, injury and healing changes therefore share a durable combat working state, with per-action checkpoints and unchanged dice on recovery; character resources publish only through an atomic final settlement. This supports whole-battle rollback at the cost of effective-state reads, conflict checks, append-only corrections and ensuring every resource mutation participates in the same contract.

The decision covers provisional ownership, not an assumption about approval identity: the existing glossary distinguishes the bot Keeper from the human KP Assistant, and the interview must still clarify which approves settlement/rollback. Ending while dying or under a continuing effect also remains an explicit design frontier. No runtime migration is authorized by this document alone.
