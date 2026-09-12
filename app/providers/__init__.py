"""LLM provider adapters.

Each module here exposes one function:

    run_conversation(static_system, dynamic_system, tools, history, new_message,
                      execute_tool, max_iterations) -> str

so app/keeper.py (system prompt building, tool execution against GroupState, the
COC7e game logic) never needs to know which SDK is actually talking to the model.
`tools` is the common {"name", "description", "input_schema"} shape; each adapter
translates that into whatever its own SDK expects.
"""
