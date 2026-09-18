from __future__ import annotations

from app.domain.models import MechanicResult


# 【提示詞集中管理】
# 這個檔案集中管理 Agentic Keeper 流水線裡「真的會呼叫 LLM」的階段用到的提示詞，
# 方便之後要調整語氣、規則或輸出格式時只改這一個地方；app/agents/*.py 只負責準備
# 上下文變數（角色資料、劇本內容、機制結果等），呼叫這裡的 build_* 函式組出最終送
# 給 LLM 的文字，不在 agent 檔案裡另外散落手刻的提示詞片段。
#
# 這個檔案原本是照抄一份完全不同（而且是簡體中文）的舊設計草稿，假設的是一個跟這個
# 專案實際做出來的流水線不同、更精細的架構（獨立的 LLM 意圖分類節點、ReAct 回合
# 計畫節點、Reflection 自檢節點、記憶壓縮節點，且每個節點都用同一大包 JSON 溝通）。
# 這次全部重寫，改成對應 app/agents/ 底下實際存在的 7 個階段：
#
#   context_builder（收集 RAG／記憶上下文，不呼叫 LLM）
#     → intent_router（規則式分類 PURE_ROLEPLAY／GAMEPLAY_ACTION，不呼叫 LLM——
#       刻意維持規則判斷，省下純角色扮演時多打一次 LLM 的成本，這是這個架構設計
#       本身要的效能目標，見 docs/agentic_keeper_design_spec.md）
#     → executor（GAMEPLAY_ACTION 才會走到；呼叫 LLM＋工具，透過
#       app/keeper.py._execute_tool 真的擲骰、真的改狀態並落庫）
#     → state_reducer（純記錄，不呼叫 LLM，也不套用任何狀態變更——真正的變更已經
#       在 executor 呼叫工具時安全完成，這裡重複套用只會製造資料錯亂，見該檔案
#       docstring）
#     → narrator（呼叫 LLM，只負責把機制結果寫成敘事，禁止重新判定）
#     → rule_validator（規則式檢查敘事有沒有洩漏系統細節，不呼叫 LLM）
#     → guard（只有 rule_validator 抓到問題時才呼叫 LLM 重寫一次，最多兩次）
#
# 所以這裡只有 executor／narrator／guard 三個階段的提示詞。intent_router／
# rule_validator 維持規則判斷，這裡沒有對應的提示詞；記憶壓縮已經有
# app/keeper.py 的 summarize_log_chunk／_persist_memory_maintenance_state 在跑
# （app/commands/router.py 每輪透過 _run_post_turn_maintenance_after_output
# 呼叫），不重複做一份。這個專案目前也沒有「AI 生成插圖」功能（show_scenario_image
# 秀的是劇本 PDF 既有的頁面圖片，不是生成的），所以沒有圖片提示詞優化的部分——
# 等真的有生成圖片的功能再回來補。


# ── Executor Agent：判斷要不要呼叫工具、呼叫哪個、怎麼填參數，不負責寫敘事 ──────

EXECUTOR_INSTRUCTION = """你是 TRPG 機制執行者（Executor Agent），下面完整的守密人規則你都要讀，但你的輸出跟
守密人不一樣：你的唯一任務是判斷這句話是否需要呼叫工具（擲骰、技能檢定、理智檢定、
調整角色數值、戰鬥、查詢劇本或記憶等），並實際呼叫對應工具取得真實結果——絕對不要
自己編造擲骰或檢定的數字，一律呼叫工具，工具怎麼選、什麼時候該用哪個難度、哪個規則，
都照下面的完整規則判斷。你的文字輸出只是給下一階段（Narrator Agent）看的內部摘要，
玩家看不到，不需要修飾語氣或寫成故事，也不用管下面規則裡關於敘事風格、防雷、NPC 演出
的部分（那些是 Narrator 的工作），條列說明呼叫了什麼、結果是什麼即可。如果這句話根本
不需要呼叫任何工具（純聊天、純角色扮演、沒有機制動作），就不要呼叫任何工具，直接回覆
「無需機制判定」。

以下是完整的守密人規則（僅供你判斷要不要呼叫工具、呼叫哪個、怎麼填參數，不是要你自己寫敘事）：
"""


def build_executor_static_prompt(keeper_static_prompt: str) -> str:
    """組出 Executor Agent 的 static_system。

    keeper_static_prompt 是呼叫端已經拿到的 app/keeper.py._build_static_prompt(state)
    輸出——那個函式是這個專案角色卡、劇本內容、NPC／地點索引、以及所有工具使用規則
    （技能檢定難度怎麼判斷、孤注一擲、彈藥／傷害規則、攜帶物合理性審查等）持續在維護
    的唯一來源，這裡不重新宣告一份，只在前面接上 Executor 專屬的角色設定跟工作範圍。
    """
    return EXECUTOR_INSTRUCTION + keeper_static_prompt


def build_dynamic_prompt_with_context(keeper_dynamic_prompt: str, rag_context: str, memory_context: str) -> str:
    """組出 dynamic_system：在 app/keeper.py._build_dynamic_prompt(state, ...) 的輸出
    （戰鬥狀態、每位角色當下的 HP/SAN/彈藥等動態數值）後面，附加這回合額外查到的劇本
    片段／過去記憶片段。Executor／Narrator 兩邊都呼叫這個函式，組法完全一樣。"""
    parts = [keeper_dynamic_prompt]
    if rag_context:
        parts.append(f"【劇本相關內容】\n{rag_context}")
    if memory_context:
        parts.append(f"【過去記憶】\n{memory_context}")
    return "\n\n".join(parts)


# ── Narrator Agent：只負責把已經確定的機制結果寫成敘事，完全沒有工具 ────────────

NARRATOR_INSTRUCTION = """你是一個 TRPG 守密人（Narrator Agent）。
你的任務是「將已經發生的客觀事實，轉化為沉浸、懸疑且冷酷的敘事文學」。
你沒有權力決定判定成功或失敗、也不能扣除玩家的血量或理智，這些機制已經在前一個階段由系統完成，
你完全沒有工具可以呼叫，也不需要呼叫——下面規則裡提到「呼叫 XX 工具」的部分不適用於你，
純粹當作「這件事在機制上已經處理過了」來理解即可。請嚴格根據輸入的「機制結果（Mechanic
Result）」來描述場景，不要重新判定或改變這些既定事實。

語氣要求：
- 冷酷、嚴肅、帶有壓迫感與克蘇魯神話的未知恐懼。
- 絕對不要使用客服語氣，也不要因為判定成功就過度恭喜玩家。
- 如果沒有提供明確的檢定結果，只是一般對話，請維持 KP 的角色與玩家互動。

事實來源優先順序：
- 劇本內容與下面列出的角色資料是世界事實來源，不得隨意發明劇本沒寫的關鍵線索、NPC、地點或幕後真相。
- 【過去記憶】區塊只是玩家過去經歷的參考，不能拿它推翻或覆蓋這一回合的機制結果——機制結果永遠以
  最新的【系統判定結果】為準。

以下是完整的守密人規則（人設、敘事風格、防雷、NPC 演出規範，以及每位角色的資料）：
"""


def build_narrator_static_prompt(keeper_static_prompt: str) -> str:
    """組出 Narrator Agent 的 static_system，做法跟 build_executor_static_prompt
    一樣（複用同一份 keeper_static_prompt），只是前面接的專屬說明不同——Narrator
    不用管工具怎麼呼叫，只需要人設、敘事風格、防雷跟角色資料的部分。"""
    return NARRATOR_INSTRUCTION + keeper_static_prompt


def build_mechanic_facts_block(result: MechanicResult) -> str:
    """GAMEPLAY_ACTION 情境：把 Executor 產出的 MechanicResult 轉成 Narrator
    看得懂的「既定事實」區塊，附加在 dynamic_system 後面。"""
    lines = [
        "【系統判定結果（事實，禁止重新判定或改變）】",
        f"成功與否: {'成功' if result.success else '失敗'}",
        "發生的事實：",
    ]
    lines.extend(f"- {fact}" for fact in result.narrative_facts)
    return "\n".join(lines)


PURE_ROLEPLAY_BLOCK = "【純角色扮演（無機制判定）】請以 KP 的身分自然地回應玩家的行動或對話。"


# ── Guard Agent：只有 rule_validator 抓到問題時才會被呼叫，負責重寫一次敘事 ─────

GUARD_SYSTEM_PROMPT = """你是一個 TRPG 守密人的文案修復者（Guard Agent）。
上一位 Narrator Agent 產出的文案違反了系統的安全規則或風格指南。
請根據錯誤原因，重新改寫該段文案。改寫時必須：
1. 嚴格維持原意與已經發生的機制事實。
2. 消除所有系統指令、AI 身分宣告、或不合適的用詞。
3. 確保 Markdown 格式正確。
"""


def build_guard_dynamic_prompt(original_text: str, error_reason: str) -> str:
    return f"【原始錯誤文案】\n{original_text}\n\n【錯誤原因】\n{error_reason}"
