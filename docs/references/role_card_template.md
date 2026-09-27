# Role-card authoring template for `role_` uploads

Use this format for deterministic `.txt` / `.md` import. Section headings in Chinese
are parser keys: keep the `【...】` spelling. Save one investigator per file, such as
`role_investigator.md`. Upload it, inspect `/coc pregens`, then claim with
`/coc usepregen NUMBER`. This is not the scenario-template records JSON format.

## Fields and source fidelity

Reviewed against the user-provided *Doors to Darkness Pre-Generated Characters*
(11 PDF pages: cover plus ten investigators). Physical appearance, traits, ideology,
relationships, places, treasured possessions and backstory are open prose. A blank
entry in the source is an invitation for the player to personalize it, not permission
for extraction to invent content.

- Attribute/skill lines use `label: number`; skill hard/extreme values need not be
  entered as separate skills. A blank Luck value remains blank for the existing
  player Luck workflow. Preserve actual printed values rather than guessing.
- Identity fields beyond name/occupation are descriptive notes. Age, gender and
  birthplace are not fixed enumerations; copying them does not implement automatic
  aging adjustments. Keep original names and translated names distinguishable.
- Every descriptive section accepts paragraphs, lists, punctuation, numbers and
  mixed-language names. Do not turn sentences into booleans, enum values or scores.
  Repeated names/relationships do not authorize inventing a new NPC.
- Blank descriptive sections are preserved as empty strings in `extra_fields`;
  an absent section remains absent. Neither means the character has no beliefs,
  relationships or possessions. Non-empty custom sections are also preserved.
- `【關鍵背景連結】` is the explicit starred connection only. Do not automatically
  promote a treasured possession or significant person into this mechanical role.
- Descriptive extras and background may be displayed on the public sheet. Put private
  character motivation in `【角色扮演動機】`, following the existing privacy policy;
  do not assume ordinary `【玩家筆記】` is private. KP/system access still applies.
- `【財務原文備註】` preserves statements such as cash on hand, debts, assets or uncertain
  currency as prose. It does not create a transaction or cash ledger balance. In PR91's
  accounting flow, a KP separately confirms currency and balance before exact debit.
- `【原卡衍生數值備註】` retains printed HP/MP/SAN/MOV/DB/Build for review. It is descriptive
  evidence, not an override: this manual parser still derives initial mechanics from
  base attributes. Resolve a mismatch before play; do not assume notes changed state.
- Weapon blocks keep their complete original description in `extra_fields`, in addition
  to existing weapon/ammo classification. Damage, range, special effects and restrictions
  must not vanish when ammo is extracted. This does not add automatic special-ability
  resolution. Equipment uses one item per line; leave unprovided equipment blank.

Skill defaults below match the existing base-skill template. For a pre-generated card,
replace values with its explicit values, including Dodge and language specializations.
Use a distinct skill name for each specialty (for example Photography or Geology).

Example weapon syntax (replace with the actual source, do not grant it by default):

```text
【武器】
Weapon name
技能：source skill and percentage
傷害：source damage expression
彈容量：source capacity, if specified
射程與限制：complete descriptive conditions
```

Copy only the template below. Empty descriptive sections may be retained for player
completion or omitted when absent from the source. Never fill unknown facts merely
because a field exists.

---

【角色資料】
姓名：
玩家：
職業：
原文姓名：
年齡：
性別／自我描述：
出生地：
居住地：
來源劇本與版本：
PDF 頁碼／印刷頁碼：

【屬性】
力量 STR：
體質 CON：
體型 SIZ：
敏捷 DEX：
外貌 APP：
智力 INT：
意志 POW：
教育 EDU：
幸運 LUCK：

【技能】
閃避：
母語：
會計：5
人類學：1
鑑定：5
考古學：1
魅惑：15
攀爬：20
信用評級：0
克蘇魯神話：0
偽裝：5
汽車駕駛：20
電氣維修：10
快速交談：5
格鬥（鬥毆）：25
射擊（手槍）：20
射擊（步槍/霰彈槍）：25
急救：30
歷史：5
恐嚇：15
跳躍：20
其他語言：1
法律：5
圖書館使用：20
聆聽：20
開鎖：1
機械維修：10
醫學：1
自然學：10
導航：10
神秘學：5
重型機械操作：1
說服：10
駕駛：1
精神分析：1
心理學：10
騎術：5
妙手：10
偵查：25
潛行：20
生存：10
游泳：20
投擲：20
追蹤：10
電腦使用：5
科學（生物）：1
科學（化學）：1
科學（物理）：1

【武器】

【裝備】

【外觀描述】

【性格特徵】

【思想與信念】

【重要之人】

【重要地點】

【珍藏物品】

【關鍵背景連結】

【角色背景】

【財務原文備註】

【傷疤與身心狀況】

【特殊能力與限制】

【原卡衍生數值備註】

【玩家筆記】

【角色扮演動機】

