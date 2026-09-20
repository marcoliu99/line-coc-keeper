from __future__ import annotations

import asyncio

from app import config, keeper, observability
from app.providers import anthropic_provider, gemini_provider, openai_provider

_PROVIDERS = {"anthropic": anthropic_provider, "gemini": gemini_provider, "openai": openai_provider}
from app.domain.models import AgentMessage


async def run_assistant(message: AgentMessage) -> tuple[str, list[tuple[str, str]], list[tuple[str | None, int]]]:
    """OOC Assistant Path（Phase 10）：KP 助手的場外討論走這一條，完全繞開
    Executor／State Reducer／Narrator／Rule Validator／Guard 那條「機制判定與
    故事生成」流水線。

    這個階段刻意薄到只剩一行實際呼叫——直接把整個回合委派給
    `keeper.run_turn(..., speaker_role="kp_assistant")`，不在這裡另外組
    static/dynamic prompt、不另外過濾工具、不另外決定要落庫到 `state.log`
    還是 `state.kp_ooc_log`。原因：這個函式原本（在拆出 Phase 10 這條路徑
    之前）自己重新組過一次這些邏輯，結果就是 main 之後幫 KP Assistant 加的
    「Dice Creates Canon」功能（KP 成功觸發正式擲骰／檢定時，這輪對話要從
    `kp_ooc_log` 升格寫進正式 `state.log`，並正確銜接 OpenAI 的
    `previous_response_id` 對話鏈——見 `app/keeper.py` 的
    `_kp_tool_result_creates_canon`／`_format_kp_canonical_history_message`）
    完全沒有反映到這裡，因為這裡是另一份平行的複製品，`keeper.run_turn` 加了
    新規則，這裡不會自動跟著變。`keeper.run_turn` 才是唯一持續在維護、對
    `speaker_role="kp_assistant"` 的完整行為（static／dynamic prompt、工具
    白名單、要不要走 canonical 升格、OpenAI response id 鏈要不要延續、要落庫
    到哪個 log）負責的地方，這裡只負責從 AgentMessage 轉接參數過去，任何一種
    「只搬一部分邏輯過來」都有漏掉東西的風險，見這次 rebase 到 main 之後才
    發現的落差。

    資料隔離（不觸發這輪的 post-turn maintenance，因為 OOC 討論不是真的劇情
    回合，沒有東西需要背景壓縮）由呼叫端 app/commands/router.py 已經有的
    `run_maintenance=not is_kp_assistant` 負責，這裡不用重複處理。
    """
    state = message.payload["state"]
    user_id = message.payload["user_id"]
    display_name = message.payload["display_name"]
    text = message.payload["text"]
    resolved_location = message.payload.get("resolved_location")

    # keeper.run_turn is synchronous — dispatched via asyncio.to_thread like
    # every other call site in this codebase (see app/legacy_commands.py's
    # own handle_text_message), not awaited directly.
    provider = _PROVIDERS[config.LLM_PROVIDER]
    model = getattr(provider, {"anthropic": "ANTHROPIC_MODEL", "gemini": "GEMINI_MODEL", "openai": "OPENAI_MODEL"}[config.LLM_PROVIDER])
    metrics: dict[str, int] = {}
    with observability.metrics_context(metrics):
        with observability.span("llm.turn", provider=config.LLM_PROVIDER, model=model,
                                agent="kp_assistant", metrics=metrics):
            final_text, private_messages, image_requests = await asyncio.to_thread(
                keeper.run_turn, state, user_id, display_name, text, resolved_location, "kp_assistant"
            )
    return final_text, private_messages, image_requests
