"""The check engine: one place that decides how a player-owned check resolves.

``rules`` and ``luck`` are pure; ``service`` applies them to a ``GroupState``
inside a state transaction supplied by the caller. Commands, buttons and Keeper
tools are adapters over ``service`` — none of them keeps its own copy of the
tier, Luck, pending or event logic. Nothing here imports Discord, the Keeper or
an LLM provider.
"""
