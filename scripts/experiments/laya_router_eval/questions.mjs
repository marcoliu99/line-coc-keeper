// The questions Laya answers about one player line, shared by the sidecar (live shadow mode) and the offline eval.
export const QUESTIONS = {
  route: {
    type: "choice",
    instructions: "這是 TRPG（克蘇魯的呼喚）玩家在遊戲中說的一句話。守密人要怎麼處理它？",
    criteria: {
      narrate: "純對話、角色扮演、表達情緒或姿態、單純看看四周：不需要擲骰、戰鬥或物品變動，守密人描述即可",
      check: "結果不確定、需要技能檢定或擲骰的行動，例如搜索、偷聽、撬開、說服、急救、攀爬、躲藏",
      combat: "戰鬥相關的行動：攻擊、防守、閃避、逃跑、或引發戰鬥",
      item: "取得、拿走、使用、交出或丟棄物品",
      other: "其他需要守密人處理規則或改變遊戲狀態的行動，例如前往新地點、記錄線索",
    },
  },
  needs: { type: "noul", instructions: "這句話需要擲骰、戰鬥、物品變動或其他遊戲狀態的改變。" },
};

export async function loadLaya(options = {}) {
  const { Laya } = await import("@receptron/laya");
  // The players write Traditional Chinese: the multilingual checkpoint unless a local export is given.
  return Laya.load({ subfolder: "multilingual", ...options });
}

export async function route(laya, state) {
  const started = performance.now();
  const out = await laya.systemOne(state, QUESTIONS);
  return {
    route: out.answers.route.choice, probabilities: out.answers.route.probabilities,
    needs: out.answers.needs.noul, ms: performance.now() - started, input_tokens: out.usage?.input_tokens,
  };
}
