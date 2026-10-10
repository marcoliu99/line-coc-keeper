from __future__ import annotations

import json
import re

from app import presentation
from app.domain.models import MechanicResult
from app.models import GroupState
from app.services import opposed_checks, turn_delivery, turn_fallback

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
#       本身要的效能目標，見 docs/specs/refactor/agentic_keeper_design_spec.md）
#     → executor（GAMEPLAY_ACTION 才會走到；呼叫 LLM＋工具，透過
#       app/tool_dispatch.py 與 app/keeper_tools/ 的 handler 真的擲骰、真的改狀態並落庫）
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
# （app/commands/router.py 每輪透過 app/services/post_turn.py 的 run_post_turn_maintenance_after_output
# 呼叫），不重複做一份。這個專案目前也沒有「AI 生成插圖」功能（show_scenario_image
# 秀的是劇本 PDF 既有的頁面圖片，不是生成的），所以沒有圖片提示詞優化的部分——
# 等真的有生成圖片的功能再回來補。


# ── Executor Agent：判斷要不要呼叫工具、呼叫哪個、怎麼填參數，不負責寫敘事 ──────

EXECUTOR_INSTRUCTION = """你是 TRPG 機制執行者（Executor Agent），下面完整的守密人規則你都要讀，但你的輸出跟
守密人不一樣：你的唯一任務是判斷這句話是否需要呼叫工具（擲骰、技能檢定、理智檢定、
調整角色數值、戰鬥、查詢劇本或記憶等），並實際呼叫對應工具取得真實結果——絕對不要
自己編造擲骰或檢定的數字，一律呼叫工具，工具怎麼選、什麼時候該用哪個難度、哪個規則，
都照下面的完整規則判斷。完成工具操作後，利用本次原本的最後回應交接裁決，只回傳一個 JSON
物件（不使用 Markdown、不再呼叫另一個裁決工具）：
{"disposition":"await_check","actor_character_id":"發話者的 character_id",
 "waiting_for":"等待處理者的 character_id，沒有則空字串","check_id":"相關 check_id 或 Luck decision_id",
 "reason":"簡短理由，不建立新事實","evidence_refs":["state","tool:1"]}
可用 disposition：no_mechanics（本次不需新增機制，可含成功的唯讀查詢；不等於既有檢定消失）、await_check、await_luck、
deferred（尚未輪到／等待別人，動作尚未執行，沒有自動排隊）、resolved（已擲骰結算或有可核對的工具變更）、
resolved_without_check（有劇本或真實工具依據的免檢定完成）、cancelled、blocked、incomplete。
actor_character_id 必須是發話者；await_check/Luck 的 waiting_for 可指其他真正持有待處理項目的角色。
依據只能引用目前權威 state、已提供的 scenario_context 或工具結果附帶的 evidence_ref。
失敗工具的 evidence_ref 不能作為完成依據；未完成裁決引用 state，reason 說明工具拒絕原因。
工具回傳 current_turn_state 是更新後的權威資料；以最新一份為準。查詢不到依據就保留未知／補查。
建立檢定前依序核對「玩家實際宣告→目前物件狀態→最具體的劇本觸發條件→適用規則」。
拾起靜止物件、維持已握住物件、抓取飛行物件是不同動作，不能互換；不得先把物件改成另一狀態，再代替玩家選擇戰技或防禦。
skill_check 的 action_basis 記錄目前狀態、規則引用與轉變；player_declaration 由程式保存，不能用 action_context 的模型解讀覆蓋。
劇本要求雙方對抗時，使用 skill_check.opposed，提供對手能力、規則來源、平手勝方與勝敗後果，由程式擲對手並保存；
不能用普通 skill_check 加另一次 npc_skill_check 讓 Narrator 臨場比較。不可把對抗改成固定難度，也不可重擲既有對手骰果。
已知具名跨頁引用優先依必要關聯取齊；只有缺少另一項實際裁定事實才補查，不要反覆用同義詞尋找已取得的規則。
交接／製作物品、結束戰鬥等不用擲骰的工具完成，使用 resolved_without_check，引用所有相關變更工具。
既有其他行動的檢定不因物品交接而取消；交接完成與仍待擲的舊檢定要分開敘述。
本次新建／更換的檢定仍須等待，不能以查詢成功或任意工具成功宣稱整個行動完成。
先判斷更正是否真的撤回原 action_context；接受取消時必須 clear_pending_check，不能只回 cancelled。
搜尋完整性只表示已選紀錄及其必要關聯已齊，不保證已涵蓋整個行動；仍須補查缺少的裁決事實。
中文續取使用原 query、source=auto 與 continuation；改查 source=original 時必須清空 continuation。
blocked 表示行動未完成，不得交接成已移動、已取得或已購買。
await_check 必須引用真實 check_id；await_luck 用 decision_id，不重擲。未完成工具、缺資料、額度用完
就用 incomplete，不假裝成功或「無需機制」。沒有工具也必須交代裁決；原始文字不是玩家敘事。

以下是完整的守密人規則（僅供你判斷要不要呼叫工具、呼叫哪個、怎麼填參數，不是要你自己寫敘事）：
"""


def build_executor_static_prompt(keeper_static_prompt: str) -> str:
    """組出 Executor Agent 的 static_system。

    keeper_static_prompt 是呼叫端已經拿到的 app/prompt_builder.py.build_static_prompt(state)
    輸出——那個函式是這個專案角色卡、劇本內容、NPC／地點索引、以及所有工具使用規則
    （技能檢定難度怎麼判斷、孤注一擲、彈藥／傷害規則、攜帶物合理性審查等）持續在維護
    的唯一來源，這裡不重新宣告一份，只在前面接上 Executor 專屬的角色設定跟工作範圍。
    """
    return EXECUTOR_INSTRUCTION + keeper_static_prompt


def build_dynamic_prompt_with_context(keeper_dynamic_prompt: str, rag_context: str, memory_context: str) -> str:
    """組出 dynamic_system：在 app/prompt_builder.py.build_dynamic_prompt(state, ...) 的輸出
    （戰鬥狀態、每位角色當下的 HP/SAN/彈藥等動態數值）後面，附加這回合額外查到的劇本
    片段／過去記憶片段。Executor／Narrator 兩邊都呼叫這個函式，組法完全一樣。"""
    parts = [keeper_dynamic_prompt]
    if rag_context:
        parts.append(f"【劇本相關內容】\n{rag_context}")
    if memory_context:
        parts.append(f"【過去記憶】\n{memory_context}")
    return "\n\n".join(parts)


EXECUTOR_SCENARIO_RAG_POLICY = """【Executor 劇本檢索規則】
新版中文檢索的 complete_for_action=false 表示已知必要依據未齊；不得裁決依賴它的機制。
可用同一 query 與 continuation 續取，或 source=original 補查；原文有命中也不自動解除已知缺漏。
若最低上下文仍容不下完整規則，須暫緩並請玩家聚焦行動，不能憑摘要或跨輪記憶補齊。
complete_for_action=true 只代表已知依賴已帶入，仍須檢查未知的護甲、能力、條件與次數限制。

先檢查本回合提供的【劇本相關內容】是否已回答目前行動所需的具體劇本事實。內容已明確涵蓋的事實直接重用，不要為了確認或改寫查詢而再次呼叫 search_scenario。
中文有命中不代表依據完整。加入敵人前須核對攻擊、護甲、特殊能力、觸發條件、代價、每輪/每戰使用限制；缺少裁決必要依據時，使用 search_scenario 的 source="original"，以原文名稱/別名和缺少的規則合併補查原稿。未查到不等於沒有護甲或能力，不得自行填零或省略；仍無法確認時暫緩受影響的裁決，保留已結算骰子與狀態。
只有在缺少一項會影響本次判定或眼前後果的具體事實時，才呼叫 search_scenario 補查。工具回傳已回答問題後，採用該結果繼續處理；只有另一項不同且會影響本次判定的事實仍未解答時，才再查一次。
若本回合沒有可用的【劇本相關內容】，遇到必須依劇本決定的事實時仍可照常搜尋。若上下文與搜尋結果都沒有說明該事實，保留未知，不要自行補造。
玩家的行動若在劇本依據裡已有明寫的直接後果（按鈴、開門、移動物件、進入房間、說出觸發語、觸碰或揭露物件、跨越場景邊界），照該後果處理；劇本沒有要求檢定時，不得改以偵查、聆聽、幸運等臨時檢定取代。依據只寫了觸發物件、沒寫後果時，用「目前場景＋玩家動作＋被互動的物件或 NPC」做一次聚焦的 search_scenario；不要問「接下來會發生什麼」這類寬泛問題。
這些規則只決定如何重用劇本資訊，不會自行建立檢定、擲骰、改變角色狀態或推進場景；仍須依玩家實際行動與完整規則決定必要機制。"""


def build_executor_dynamic_prompt_with_context(
    keeper_dynamic_prompt: str,
    rag_context: str,
    memory_context: str,
) -> str:
    """Build Executor context with a turn-scoped policy for reusing proactive
    scenario retrieval while preserving follow-up searches for missing facts.

    The Narrator continues to use ``build_dynamic_prompt_with_context`` and
    does not receive this retrieval policy.
    """
    dynamic_prompt = build_dynamic_prompt_with_context(
        keeper_dynamic_prompt, rag_context, memory_context
    )
    return f"{dynamic_prompt}\n\n{EXECUTOR_SCENARIO_RAG_POLICY}"


def build_resolved_check_history_block(
    events: list[dict], character_values: dict | None, *, provisional: bool = False
) -> str:
    """Show recent finalized outcomes separately from this turn's mechanics."""
    if not character_values:
        return ""
    lines = [
        ("【目前角色數值（戰鬥暫定；尚未結算）】" if provisional else "【目前角色數值（權威存檔）】")
        + "、".join(f"{name} {value}" for name, value in character_values.items()),
        "【近期已結算檢定（歷史事件，不代表本回合檢定）】",
    ]
    if not events:
        lines.append("沒有可用的近期檢定事件紀錄。這不代表角色目前數值沒有變化；目前數值以上方存檔為準。")
        return "\n".join(lines)
    for event in events[-5:]:
        lines.append(
            f"- {event.get('investigator', '調查員')}：{event.get('skill', '檢定')} "
            f"{event.get('skill_value', '?')}%，擲出 {event.get('roll', '?')}，"
            f"難度 {presentation.difficulty_label(event.get('difficulty', 'regular'))}，結果 {presentation.outcome_label(str(event.get('outcome', '未知')))}。"
        )
        if event.get('provisional'):
            lines.append('  此戰鬥紀錄是暫定機械結果；擲骰保留，但不能獨立建立已提交的世界後果。')
        effects = event.get("state_effects", [])
        if effects:
            for effect in effects:
                lines.append(
                    f"  已提交變化：{effect.get('field')} {effect.get('before')} → "
                    f"{effect.get('after')}（{effect.get('delta'):+}）。"
                )
        else:
            lines.append("  此檢定事件沒有記錄到角色數值變化。")
    lines.append("歷史事件與本回合工具結果分開判讀；不得以本回合沒有機制操作否定歷史事件。")
    return "\n".join(lines)


# ── Narrator Agent：只負責把已經確定的機制結果寫成敘事，完全沒有工具 ────────────

# The Haunting runs (2026-10-09) told players "現有紀錄沒有驗證他已起身" and "沒有可核實的戰鬥收據": the guard
# rules below, written in the engine's words, leaked into the story. Every narrator prompt carries this.
PLAYER_VOICE_RULES = """給玩家看的文字一律是故事裡的聲音（繁體中文）：
- 不要出現系統、規則或後台用語，例如「紀錄」「收據」「權威」「狀態」「已提交」「驗證」「核實」「依據」「工具」「程式」「機制」「流程」「建立檢定」，也不要解釋為什麼某件事不能寫或還不能確認。
- 還沒發生或無法確認的事就不要寫成已發生；改用故事把懸念交代清楚，再把行動交回玩家，例如「Corbitt 的爪子已逼到你面前——你要閃避，還是反擊？」
- 玩家想做的事被更急的事擋住時（例如攻擊正撲向他），用一兩句故事交代眼前的危機與該擲的檢定，不要長篇說明為何行動還沒處理。
"""


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
- 【目前角色數值】是已存檔的權威值；【近期已結算檢定】是先前回合的機制紀錄，與本回合結果分開。不得因
  本回合沒有機制操作，就否認歷史檢定或它明確記錄的數值變化；回答角色數值時採用目前角色數值。

""" + PLAYER_VOICE_RULES + """
以下是完整的守密人規則（人設、敘事風格、防雷、NPC 演出規範，以及每位角色的資料）：
"""


def build_narrator_static_prompt(keeper_static_prompt: str) -> str:
    """組出 Narrator Agent 的 static_system，做法跟 build_executor_static_prompt
    一樣（複用同一份 keeper_static_prompt），只是前面接的專屬說明不同——Narrator
    不用管工具怎麼呼叫，只需要人設、敘事風格、防雷跟角色資料的部分。"""
    return NARRATOR_INSTRUCTION + keeper_static_prompt


def build_tool_enabled_narrator_static_prompt(keeper_static_prompt: str, turn_kind: str) -> str:
    """One narrative conversation with only the tools valid for this entry."""
    if turn_kind == "resolved_check_followup":
        instruction = (
            "你是守密人。玩家檢定已由程式結算；先依權威結果處理必要的劇本或戰鬥後果，"
            "再向玩家敘事。不可重建、重擲或改判原檢定，也不可重扣已提交的數值。"
            "若原檢定已預先附上劇本引文與後果授權，傷害須用 apply_resolved_check_damage 真正提交；"
            "獨立的後續檢定須用 create_triggered_check 建立新 pending，不能替玩家擲骰。"
            "檢定結果讓調查員取得或失去物品時，用 add_carried_item／remove_carried_item 登記，不要只在敘事裡提到。"
            "急救成功讓昏迷的調查員醒來時，用 remove_status_tag 拿掉「昏迷」（「倒地」也一併拿掉），不要只在敘事裡說醒了。"
            "檢定結果觸發劇本寫明的攻擊（例如找到的匕首浮起刺來）而戰鬥還沒開始時，用 get_enemy_stat_block 取得數值表，再用 initialize_combat 登記劇本數值表上的敵人"
            "並附上攻擊（被操縱的物品登記操縱它的存在，攻擊用劇本指定要擲的數值與傷害，例如以 Corbitt 的 POW 90 擲浮空匕首、傷害 1D4+2，極難成功穿刺所以加 tags: [\"impale\"]），攻擊由戰鬥流程接手，這裡不要自己擲攻擊或寫出傷害結果；依敏捷順序輪到敵人時它才出手，比它敏捷高的調查員先行動，敘事寫到攻擊逼近為止，並告訴玩家現在輪到誰。"
            "戰鬥中依【已結算檢定】區塊裡的【戰鬥下一步】處理回合：它說引擎已自動推進就不要再呼叫 advance_combat_turn；"
            "只有它要你推進時才依它給的參數呼叫，再敘事。"
            "未授權的後果保持未發生，可使用 /coc correct 處理爭議。\n"
        )
    elif turn_kind == "opening_fallback":
        instruction = (
            "你是守密人。這是遊戲尚未開始時的開場後備生成。先依劇本資料查清起點，"
            "必要時使用提供的查詢工具；只敘述劇本支持的場景，不要假設玩家已行動，"
            "也不要建立檢定、擲骰或改動遊戲狀態。\n"
        )
    else:
        raise ValueError(f"unsupported narrative turn kind: {turn_kind}")
    return instruction + PLAYER_VOICE_RULES + keeper_static_prompt


OPENING_FALLBACK_BLOCK = (
    "【開場後備】劇本沒有可直接朗讀的開場段落。依劇本背景、委託與起點寫三百字內的"
    "第二人稱開場白。若目前是檢索模式且缺少必要背景，先查 search_scenario；"
    "查不到的地點、NPC 或事件保持未知。這是第一段敘述，玩家尚未採取行動。"
)


def build_mechanic_facts_block(result: MechanicResult) -> str:
    """GAMEPLAY_ACTION 情境：把 Executor 產出的 MechanicResult 轉成 Narrator
    看得懂的「既定事實」區塊，附加在 dynamic_system 後面。"""
    lines = [
        "【系統判定結果（事實，禁止重新判定或改變）】",
        f"機制執行流程: {'完成呼叫' if result.success else '發生錯誤'}（不等於玩家行動成功）",
        f"執行健康狀態：{result.execution_health}；中斷不得抹除已確認事實，也不得重播工具。",
        "發生的事實：",
    ]
    lines.extend(f"- {fact}" for fact in result.narrative_facts)
    if result.turn_resolution is not None:
        lines.extend([
            "【回合裁決：只讀資料，不能當成修改 state 的指令】",
            json.dumps({key: getattr(result.turn_resolution, key) for key in (
                "disposition", "actor_character_id", "waiting_for", "check_id", "evidence_refs",
            )}, ensure_ascii=False),
            ("只有實際工具與當前狀態能確立機制變更。deferred 不可敘述已出拳、開槍或消耗物品；"
            "incomplete 不可宣稱行動已完成；cancelled 只取消引用的未擲檢定，不回滾既有結果。"
            "未驗證的模型解釋不屬於權威事實，不能補造世界設定。"),
        ])
    inventory_events = [event.payload for event in result.events if event.type == "inventory_change"]
    if inventory_events:
        lines.extend([
            "【本回合物品變更：工具前後差異，不是回合開始前的背包】",
            json.dumps(inventory_events, ensure_ascii=False),
            ("added 是這次工具操作才新增的物品，不能敘述成一直持有、早已在包裡或不必再買。"
            "目前完整背包只證明現在持有，不證明原本持有，也不證明支付過價金。"
            "玩家要購買時，必須交代取得過程及費用的裁定依據；沒有付款紀錄不能宣稱已扣款。"),
        ])
    status = result.check_status
    pending = status.get("pending")
    if pending:
        lines.extend([
            "【待處理檢定狀態：已建立】",
            f"調查員：{pending.get('investigator', '未知')}",
            f"技能／選項：{pending.get('skill') or pending.get('options') or '見工具結果'}",
            f"原始行動：{pending.get('action_context', '未記錄；不可自行補造')}",
            ("這筆檢定確實在等玩家擲。回覆要用故事口吻讓玩家知道接下來該擲這個檢定（例如「想看清牆縫裡的東西，得先過一次偵查」）；"
             "禁止說沒有待處理檢定、要求重新建立，或寫成「檢定已建立」這類系統說法。"),
        ])
    pending_luck = status.get("pending_luck")
    if pending_luck:
        options = pending_luck.get("options") or []
        options_text = "、".join(
            f"/coc luck {option.get('tier')}（花費 {option.get('cost')} 點）"
            for option in options if isinstance(option, dict)
        )
        investigator = pending_luck.get("investigator", "調查員")
        skill = pending_luck.get("skill_name", "檢定")
        lines.extend([
            "【待處理 Luck 決定：骰已擲出，最終結果尚未定案】",
            f"調查員：{investigator}；檢定：{skill}；原始骰值：{pending_luck.get('roll', '未知')}；原始等級：{presentation.tier_label(str(pending_luck.get('original_tier', '未知')))}。",
            f"可用選項：{options_text or '依待處理 Luck 按鈕選擇'}；輸入 /coc luck skip 可保留原骰結果。",
            "必須請玩家完成這筆既有 Luck 決定；禁止要求重新擲骰、建立另一筆檢定，或把骰值說成已定案的成敗。暫停同一行動的後續結果敘述。",
        ])
    resolved = status.get("resolved")
    if resolved:
        outcome = "成功" if resolved.get("success") else "失敗"
        opposed_winner = resolved.get('opposed_winner')
        lines.extend([
            "【已結算檢定：結果權威且不得重擲】",
            f"{resolved.get('investigator', '調查員')} 的 {resolved.get('skill', '檢定')}：技能值 {resolved.get('skill_value', '未知')}，擲出 {resolved.get('roll', '未知')}，難度 {presentation.difficulty_label(resolved.get('difficulty', 'regular'))}，等級 {presentation.tier_label(str(resolved.get('tier', '未知')))}，結果 {outcome}。",
            "這筆檢定已結算。不得改成尚未結算、因先攻延後同一擲骰結果、要求再擲一次，或從檢定結果自行推導未提供的傷害、破壞或戰鬥。",
        ])
        if opposed_winner:
            lines.append(f"劇本對抗勝方：{opposed_winner}；此結果優先於單方技能等級。")
    if not status.get("pending") and not pending_luck and not resolved:
        lines.append(
            "【檢定狀態：沒有待處理／新建立檢定，也沒有本回合已結算結果】不得指示玩家擲骰、按檢定按鈕或輸入 /coc check；"
            "可以描述尚待處理的行動，但不可暗示已有檢定等待玩家。"
        )
    return "\n".join(lines)


def build_resolved_check_outcome_block(result: dict) -> str:
    """Build a bounded, structured authority block for post-roll narration."""
    outcome = presentation.outcome_label(str(result.get("outcome", "結果未知")))
    skill = result.get("skill", "檢定")
    consequence_plans = result.get("consequences") or []
    consequence_note = (
        f"\n已授權後果（僅符合最終成敗條件者可執行）：{json.dumps(consequence_plans, ensure_ascii=False)}\n"
        f"來源 check_id={result.get('check_id', '')}；event_id={result.get('event_id', '')}。"
        if consequence_plans else "\n沒有預先授權的檢定後果，不可補造傷害或新檢定。"
    )
    return (
        "【已結算檢定：權威機制結果】\n"
        f"調查員：{result.get('investigator', '未知')}；檢定：{skill}；"
        + ("未擲骰（玩家的選擇本身結算了攻擊）；" if result.get("no_roll") else
           f"技能值：{result.get('skill_value', '未知')}；擲出 {result.get('roll', '未知')}；"
           f"難度：{presentation.difficulty_label(result.get('difficulty', 'regular'))}；")
        + f"最終結果：{outcome}。\n"
        f"行動情境：{str(result.get('action_context', '')).strip() or '未提供'}\n"
        '【行動及對抗交接；來源與對手數值不得公開】\n'
        f"{json.dumps({'player_declaration': result.get('player_declaration'), 'opposed_outcome': opposed_checks.public_outcome(result.get('opposed_outcome'))}, ensure_ascii=False)}\n"
        'player_declaration 是原始宣告，不會自行建立新事實。'
        'opposed_outcome.winner 是程式已比較的最終勝方，優先於單方技能成功；不得重新比較或重擲。'
        'applicable_consequence 只是後果分支，傷害、物品與資源尚須對應工具才能生效。\n'
        "這次檢定已由系統擲骰並定案。只敘述這個結果允許的後果；不得重擲或改判、"
        "因戰鬥先攻把這次檢定說成尚未結算，或從骰值自行推導傷害、破壞、敵人現身或戰鬥。"
        "原檢定不可重建；若有來源授權，可用專用工具提交非戰鬥傷害或建立獨立的後續檢定。"
        "若劇本與已結算結果要求戰鬥傷害或回合推進，可使用提供的後續工具。"
        + consequence_note + _combat_next_step(result)
    )


def _combat_next_step(result: dict) -> str:
    """What the Keeper must do with the battle after this roll, when the engine already knows."""
    receipt = result.get("combat_receipt") or {}
    if not receipt.get("combat_id"):
        return ""
    blow = (f"\n【這一擊的結果】{receipt['blow']}照這個寫：閃避或反擊擲出成功，不代表躲開；命中就寫中招受傷，"
            "沒命中才寫躲開或落空。" if receipt.get("blow") else "")
    follow_up = f"\n【武器後續】這次命中依武器表還有後續，引擎沒有擲：{receipt['follow_up']}" if receipt.get("follow_up") else ""
    return blow + follow_up + _combat_turn_step(receipt)


def _combat_turn_step(receipt: dict) -> str:
    if receipt.get("scenario_ended"):
        return ("\n【戰鬥下一步】所有調查員都已倒下，戰鬥已自動結算，劇本到此結束。只描寫這一擊的結果與結局，"
                "告訴玩家可以用 /coc newgame 開新的一局；不要再推進劇情、建立檢定或呼叫任何戰鬥工具。")
    if receipt.get("settlement_ready"):
        return ("\n【戰鬥下一步】這個行動結束後有一方已全數倒下，戰鬥可以結算：不要呼叫 advance_combat_turn（那會跳過倒下的人再開一輪）。"
                "只敘事這一擊的結果與戰鬥結束的情景；結算由下一次守密人回合依戰鬥狀態取得預覽並確認，這裡不要結算。")
    advanced = receipt.get("auto_advanced")
    if isinstance(advanced, dict):
        waiting = ("" if advanced.get("phase") not in {"PLAYER_CHOICE", "PLAYER_ROLL", "LUCK_DECISION", "INJURY_CHECK"}
                   else "，正在等玩家的選擇或擲骰")
        enemy = advanced.get("enemy_turn") or {}
        stuck = (f"；下一位敵人的回合卡住（{enemy.get('error')}）：暫停中的敵方行動用 resolve_combat_ruling 恢復"
                 "（給武器或距離）或取消；沒有行動可結算的敵人才用 advance_combat_turn skip 跳過"
                 if enemy and enemy.get("ok") is False else "")
        names = "、".join(str(g.get("name")) for g in advanced.get("skipped_enemy_turns") or ())
        given_up = (f"；{names} 沒有可用的攻擊，引擎已讓出它的回合（不要敘事它出手）：劇本若寫了它的攻擊，"
                    "用 initialize_combat 以同一個名字附上攻擊再登記一次，它下一輪就會出手" if names else "")
        return (f"\n【戰鬥下一步】這個行動結束後引擎已自動推進：現在輪到 {advanced.get('next_actor', '下一位')}"
                f"（第 {advanced.get('round_now')} 輪）{waiting}{stuck}{given_up}。剛結束的行動不要再呼叫 advance_combat_turn；"
                "敘事要包含剛結算的結果，以及（若有）敵人接著的攻擊。")
    if receipt.get("auto_advance_error"):
        return (f"\n【戰鬥下一步】這個行動已結束，但引擎無法自動推進（{receipt['auto_advance_error']}）："
                "先處理它說的事，再呼叫 advance_combat_turn。")
    if receipt.get("completed"):
        return ("\n【戰鬥下一步】這個行動已經結束，但回合仍停在原行動者：先呼叫 advance_combat_turn"
                "（actor_id 填目前行動者的名字或 ID，event_id 可省略），讓下一位行動，再敘事。")
    phase = receipt.get("phase")
    if phase in {"PLAYER_CHOICE", "PLAYER_ROLL", "LUCK_DECISION", "INJURY_CHECK"}:
        return "\n【戰鬥下一步】這個行動還在等另一位玩家的選擇或擲骰：不要推進回合，敘事到這裡為止。"
    if phase == "NEEDS_RULING":
        return "\n【戰鬥下一步】這個行動暫停等待裁定：用 resolve_combat_ruling 解決或取消它，再推進。"
    return ""


def enforce_resolved_check_consistency(
    text: str, result: dict, *, new_pending_check: bool = False,
) -> str:
    """Fail closed when post-roll narration says the authoritative roll is unresolved."""
    contradictions = ["行動尚未結算", "還沒輪到", "等輪到", "請再擲", "重新擲", "重新建立檢定"]
    if not new_pending_check:
        contradictions.extend(("結果尚未結算", "檢定尚未結算"))
    if not any(phrase in text for phrase in contradictions):
        return text
    outcome = presentation.outcome_label(str(result.get("outcome", "結果未知")))
    return (
        f"{result.get('investigator', '調查員')} 的 {result.get('skill', '檢定')} 已結算："
        f"擲出 {result.get('roll', '未知')}，難度 {presentation.difficulty_label(result.get('difficulty', 'regular'))}，"
        f"結果為「{outcome}」。這次結果不得重擲或改判；未由機制結果確認的額外後果尚未發生。"
    )



_SETTLING = frozenset({"preview_combat_settlement", "confirm_combat_settlement", "get_combat_status"})
_OPENS_A_FIGHT = frozenset({"initialize_combat", "start_combat"})


def enforce_mechanic_check_consistency(text: str, result: MechanicResult, *, state: GroupState | None = None) -> str:
    """Enforce check, Luck, and resolved-result state after model narration.

    ``state`` lets a turn that could not finish name what the table has already been shown (``turn_fallback.scene_hints``).
    """
    status = result.check_status
    resolution = result.turn_resolution
    if resolution is not None:
        if resolution.disposition in {"incomplete", "blocked"}:
            warning = "這次行動目前無法繼續。" if resolution.disposition == "blocked" else "這次行動尚未完整處理。"
            confirmed = turn_delivery.distinct_lines(
                [o.public_text for o in result.observed_outcomes if o.audience == "public" and o.public_text])
            if (not status.get("pending") and not status.get("pending_luck") and not status.get("state_changed")
                    and any(not o.success and o.audience == "public" and o.public_text for o in result.observed_outcomes)):
                # A refusal that says why and what to do (an empty gun) is the whole answer, not a tool failure.
                return "\n".join(confirmed)
            if confirmed:
                warning = "\n".join(confirmed) + "\n\n" + warning
            if text.strip() and any(name in _OPENS_A_FIGHT and ok for name, ok in result.tool_calls):
                # The fight did start: the enemy rising is told before whatever is still owed, as a deferral keeps it
                # (Haunting rerun1 turn 28, where a Sanity check left the turn incomplete).
                warning = f"{text.rstrip()}\n\n{warning}"
            if status.get("state_changed"):
                warning += "已記錄的變更會保留，請勿重做已完成的部分。"
            if status.get("dice_rolled") or status.get("resolved") or status.get("pending_luck"):
                warning += "不要重擲已結算的骰。"
            held_luck = status.get("pending_luck")
            if held_luck:
                return f"{warning}\n\n{_pending_luck_fallback(held_luck)}"
            pending = status.get("pending")
            if pending:
                investigator = pending.get("investigator", "調查員")
                skill = pending.get("skill") or "檢定／選擇"
                return f"{warning}\n\n請按檢定按鈕或輸入 /coc check，擲 {investigator} 的{skill}。"
            if (resolution.disposition == "blocked" and state is not None and not state.combat.active
                    and ("confirm_combat_settlement", True) in result.tool_calls
                    and all(ok and name in _SETTLING for name, ok in result.tool_calls)
                    and any(c.hp > 0 for c in state.active_characters())):
                # The line attacked an enemy already down: the Keeper closed the fight instead, which is the answer,
                # not 「這個行動無法進行；請改試別的做法」 (rerun8 turn 56). Only when settling was all the turn did:
                # anything else it ran or failed keeps the ordinary warning.
                return "戰鬥已經結束，這一擊不必再出手了。接下來想做什麼？"
            if (state is not None and not status.get("scenario_evidence_blocked")
                    and (in_battle := turn_fallback.combat_guidance(
                        state, result.fallback_reason, resolution.actor_character_id))):
                return f"{warning}{in_battle}"
            hints = turn_fallback.scene_hints(state) if state is not None else ""
            if status.get("scenario_evidence_blocked"):
                blocked = f"{warning}目前未取得足夠的劇本依據，系統已暫停相關操作；待依據補齊後再繼續。"
                return f"{blocked}\n{hints}" if hints else blocked
            return f"{warning}{turn_fallback.guidance(result.fallback_reason, hints)}"
        if resolution.disposition == "deferred":
            waiting_name = status.get("waiting_for_name", "目前行動者")
            waiting = f"你的這次行動尚未執行，請先等待{waiting_name}完成目前的行動；輪到你時再宣告。"
            # A deferral may only change state by setting up the fight (turn_resolution._setup_only): the line that
            # started it keeps its scene, the enemy rising and who goes first, rather than only the wait (rerun8 turn 33).
            if status.get("state_changed") and text.strip():
                return f"{text.rstrip()}\n\n{waiting}"
            return waiting
        if resolution.disposition == "cancelled":
            return "已取消這筆尚未擲骰的檢定；已結算的結果與其他人的待處理項目保持不變。"
    pending = status.get("pending")
    if pending:
        denial_phrases = ("尚未建立", "沒有建立", "還沒建立", "沒有待處理", "尚未有待處理")
        if any(phrase in text for phrase in denial_phrases):
            investigator = pending.get("investigator", "調查員")
            skill = pending.get("skill")
            detail = f"「{skill}」" if skill else "這次"
            return f"{investigator} 還要擲{detail}檢定：請按檢定按鈕或輸入 /coc check 擲骰或選擇。"
        has_check_instruction = "/coc check" in text or "檢定按鈕" in text
        if not has_check_instruction:
            investigator = pending.get("investigator", "調查員")
            skill = pending.get("skill")
            detail = f"{skill} 檢定" if skill else "檢定／選擇"
            return f"{text.rstrip()}\n\n請按檢定按鈕或輸入 /coc check，擲 {investigator} 的{detail}。"
        return text

    pending_luck = status.get("pending_luck")
    if pending_luck:
        if any(phrase in text for phrase in (
            "重新擲", "再擲一次", "重新建立檢定", "結果已定案", "檢定已成功", "檢定失敗",
            "/coc check", "尚未建立", "沒有建立", "沒有待處理",
        )):
            return _pending_luck_fallback(pending_luck)
        if "/coc luck" not in text and "Luck 按鈕" not in text and "幸運按鈕" not in text:
            return f"{text.rstrip()}\n\n{_pending_luck_instruction(pending_luck)}"
        return text

    resolved = status.get("resolved")
    if resolved and any(phrase in text for phrase in ("行動尚未結算", "結果尚未結算", "還沒輪到", "等輪到", "請再擲", "重新擲")):
        investigator = resolved.get("investigator", "調查員")
        skill = resolved.get("skill", "檢定")
        outcome = "成功" if resolved.get("success") else "失敗"
        return (
            f"{investigator} 的 {skill} 檢定已結算：擲出 {resolved.get('roll', '未知')}，"
            f"難度 {presentation.difficulty_label(resolved.get('difficulty', 'regular'))}，結果為{outcome}。"
            "此結果不會重擲或改判；尚未由機制結果確認的額外後果仍未發生。"
        )

    lower_text = text.lower()
    check_command_index = lower_text.find("/coc check")
    negation_pattern = r"(?:不要|勿|不必|不需要|不用|無需|不需)[^，。；！？,;!?\n]{0,5}$"
    command_context = (
        re.split(r"[，。；！？,;!?\n]", text[:check_command_index])[-1]
        if check_command_index >= 0 else ""
    )
    command_is_negated = re.search(negation_pattern, command_context) is not None
    roll_phrases = ("擲骰", "投骰", "擲出結果")
    roll_indices = [index for phrase in roll_phrases for index in [text.find(phrase)] if index >= 0]
    roll_is_negated = any(
        re.search(negation_pattern, re.split(r"[，。；！？,;!?\n]", text[:index])[-1]) is not None
        for index in roll_indices
    )
    completion_instruction = re.search(r"(?:請)?完成[^。！？\n]{0,24}檢定(?:後|以後|之後|，|才能)", text)
    completion_is_negated = bool(completion_instruction and re.search(
        negation_pattern, re.split(r"[，。；！？,;!?\n]", text[:completion_instruction.start()])[-1]
    ))
    asks_for_check = (
        (completion_instruction is not None and not completion_is_negated and not resolved)
        or         (check_command_index >= 0 and not command_is_negated)
        or ("請按檢定按鈕" in text and not re.search(negation_pattern, text[:text.find("請按檢定按鈕")]))
        or (
            bool(roll_indices)
            and any(word in text for word in ("請", "需要", "可以", "使用", "按鈕"))
            and not roll_is_negated
        )
    )
    if asks_for_check:
        return "這回合沒有建立待處理檢定，目前不需要擲骰或使用 /coc check。請描述你接下來採取的行動。"
    return text


def _pending_luck_instruction(pending_luck: dict) -> str:
    options = pending_luck.get("options") or []
    choices = "、".join(
        f"/coc luck {option.get('tier')}（{option.get('cost')} 點）"
        for option in options if isinstance(option, dict)
    )
    investigator = pending_luck.get("investigator", "調查員")
    skill = pending_luck.get("skill_name", "檢定")
    return (
        f"{investigator} 的 {skill} 已擲出 {pending_luck.get('roll', '未知')}，目前仍等待 Luck 決定；"
        f"請使用 Luck 按鈕或輸入 /coc luck skip 保留原結果{f'，或 {choices}' if choices else ''}。"
    )


def _pending_luck_fallback(pending_luck: dict) -> str:
    return _pending_luck_instruction(pending_luck) + " 最終成敗尚未定案，請先處理這筆決定。"


def pending_luck_reply(pending_luck: dict, investigator: str = "") -> str:
    """Answer an action held behind an unresolved Luck decision.

    The same text this module already substitutes after a turn has run. A
    caller that knows the decision is outstanding can produce it before the
    turn starts, which is the whole point: the dice are already rolled, so
    nothing the model could add is still undetermined.
    """
    record = dict(pending_luck)
    if investigator:
        record.setdefault("investigator", investigator)
    return _pending_luck_fallback(record)


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
