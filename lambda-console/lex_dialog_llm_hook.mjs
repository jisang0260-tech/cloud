const OPENAI_API_KEY = process.env.OPENAI_API_KEY || "";
const OPENAI_MODEL = process.env.OPENAI_MODEL || "gpt-5.4-mini";
const MAX_TURNS = Number(process.env.MAX_TURNS || "7");
const REQUIRED_TURNS = Number(process.env.REQUIRED_TURNS || "3");

const NEXT_ACTIONS = new Set([
  "ask_next",
  "ask_followup",
  "repeat",
  "final_judge",
]);

export const handler = async (event) => {
  console.log("lex event:", JSON.stringify(event));

  const transcript = String(event.inputTranscript || "").trim();
  const previousAttributes = event.sessionState?.sessionAttributes || {};
  const currentTurn = Number(previousAttributes.turn_index || "0") + 1;

  const sessionAttributes = stringifyAttributes({
    ...previousAttributes,
    [`answer_${currentTurn}`]: transcript,
    latest_transcript: transcript,
    turn_index: String(currentTurn),
  });

  const decision = await decideNextStep({
    transcript,
    turnIndex: currentTurn,
    sessionAttributes,
  });

  Object.assign(
    sessionAttributes,
    stringifyAttributes({
      reply_text: decision.reply_text,
      next_action: decision.next_action,
      risk_flag: decision.risk_flag ? "true" : "false",
      dialog_reason: decision.reason,
    })
  );

  console.log("dialog decision:", JSON.stringify(sessionAttributes));

  return lexCloseResponse(event, sessionAttributes);
};

async function decideNextStep({ transcript, turnIndex, sessionAttributes }) {
  if (!OPENAI_API_KEY) {
    return fallbackDecision({ transcript, turnIndex });
  }

  try {
    const response = await fetch("https://api.openai.com/v1/responses", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${OPENAI_API_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: OPENAI_MODEL,
        input: [
          {
            role: "system",
            content:
              "You are a Korean care-call dialog manager. Decide the next short Korean line and next action. " +
              "Ask follow-up questions only when the user's answer has risk, ambiguity, or missing required information. " +
              `After ${MAX_TURNS} turns, always return next_action final_judge. Keep reply_text natural and under 45 Korean characters.`,
          },
          {
            role: "user",
            content: JSON.stringify({
              latest_answer: transcript,
              turn_index: turnIndex,
              required_turns: REQUIRED_TURNS,
              max_turns: MAX_TURNS,
              previous_answers: collectAnswers(sessionAttributes),
              allowed_next_actions: Array.from(NEXT_ACTIONS),
            }),
          },
        ],
        text: {
          format: {
            type: "json_schema",
            name: "carecall_dialog_decision",
            strict: true,
            schema: {
              type: "object",
              additionalProperties: false,
              properties: {
                reply_text: { type: "string" },
                next_action: {
                  type: "string",
                  enum: Array.from(NEXT_ACTIONS),
                },
                risk_flag: { type: "boolean" },
                reason: { type: "string" },
              },
              required: ["reply_text", "next_action", "risk_flag", "reason"],
            },
          },
        },
        max_output_tokens: 180,
      }),
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.error?.message || `OpenAI HTTP ${response.status}`);
    }

    return normalizeDecision(JSON.parse(extractOutputText(data)), turnIndex);
  } catch (error) {
    console.error("LLM decision failed:", error);
    return fallbackDecision({ transcript, turnIndex });
  }
}

function normalizeDecision(rawDecision, turnIndex) {
  const decision = {
    reply_text: String(rawDecision.reply_text || "").trim(),
    next_action: String(rawDecision.next_action || "ask_next").trim(),
    risk_flag: Boolean(rawDecision.risk_flag),
    reason: String(rawDecision.reason || "").trim(),
  };

  if (!NEXT_ACTIONS.has(decision.next_action)) {
    decision.next_action = "ask_next";
  }

  if (turnIndex >= MAX_TURNS) {
    decision.next_action = "final_judge";
  }

  if (!decision.reply_text) {
    decision.reply_text =
      decision.next_action === "final_judge"
        ? "말씀 감사합니다. 상태를 확인해볼게요."
        : "조금만 더 자세히 말씀해주실 수 있을까요?";
  }

  return decision;
}

function fallbackDecision({ transcript, turnIndex }) {
  const risky = /아프|어지|못 먹|굶|도움|혼자|쓰러|숨|통증|응급|힘들/.test(
    transcript
  );

  if (turnIndex >= MAX_TURNS || turnIndex >= REQUIRED_TURNS) {
    return {
      reply_text: "말씀 감사합니다. 상태를 확인해볼게요.",
      next_action: "final_judge",
      risk_flag: risky,
      reason: "fallback_final_judge",
    };
  }

  return {
    reply_text: risky
      ? "언제부터 그렇게 불편하셨어요?"
      : "오늘 몸 상태는 어떠세요?",
    next_action: risky ? "ask_followup" : "ask_next",
    risk_flag: risky,
    reason: "fallback_rule",
  };
}

function lexCloseResponse(event, sessionAttributes) {
  const intentName =
    event.sessionState?.intent?.name || "CaptureSpeechIntent";

  return {
    sessionState: {
      sessionAttributes,
      dialogAction: {
        type: "Close",
      },
      intent: {
        name: intentName,
        state: "Fulfilled",
      },
    },
  };
}

function collectAnswers(sessionAttributes) {
  return Object.entries(sessionAttributes)
    .filter(([key]) => /^answer_\d+$/.test(key))
    .sort(([a], [b]) => Number(a.split("_")[1]) - Number(b.split("_")[1]))
    .map(([key, value]) => ({ key, text: String(value || "") }));
}

function stringifyAttributes(attributes) {
  return Object.fromEntries(
    Object.entries(attributes || {}).map(([key, value]) => [
      String(key),
      String(value ?? ""),
    ])
  );
}

function extractOutputText(responseJson) {
  if (typeof responseJson.output_text === "string") {
    return responseJson.output_text;
  }

  for (const item of responseJson.output || []) {
    for (const content of item.content || []) {
      if (content.type === "output_text" && typeof content.text === "string") {
        return content.text;
      }
    }
  }

  throw new Error("No output_text found in OpenAI response.");
}
