import {
  BedrockRuntimeClient,
  ConverseCommand,
} from "@aws-sdk/client-bedrock-runtime";

const BEDROCK_REGION = process.env.BEDROCK_REGION || "us-east-1";
const BEDROCK_MODEL_ID =
  process.env.BEDROCK_MODEL_ID || "amazon.nova-micro-v1:0";
const MAX_TURNS = Number(process.env.MAX_TURNS || "7");
const REQUIRED_TURNS = Number(process.env.REQUIRED_TURNS || "3");

const NEXT_ACTIONS = new Set([
  "continue_dialog",
  "repeat",
  "final_judge",
]);

const bedrock = new BedrockRuntimeClient({ region: BEDROCK_REGION });

export const handler = async (event) => {
  console.log("lex event:", JSON.stringify(event));

  const transcript = String(event.inputTranscript || "").trim();
  const previousAttributes = event.sessionState?.sessionAttributes || {};
  const currentTurn = Number(previousAttributes.turn_index || "0") + 1;

  const sessionAttributes = stringifyAttributes({
    ...previousAttributes,
    [`answer_${currentTurn}`]: transcript,
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
      dialog_reason: decision.reason,
    })
  );

  console.log("dialog decision:", JSON.stringify(sessionAttributes));

  return lexCloseResponse(event, sessionAttributes);
};

async function decideNextStep({ transcript, turnIndex, sessionAttributes }) {
  const command = new ConverseCommand({
    modelId: BEDROCK_MODEL_ID,
    system: [
      {
        text:
          "You are a Korean care-call dialog manager for an elderly care service. " +
          "The Connect flow already asked the first question: whether the person ate a meal. " +
          "The first user answer is about that meal question. " +
          "Your main goal is to guide a short phone dialog that probes possible risk signals. " +
          "Every non-final response must ask exactly one next question. " +
          "If the answer suggests risk, ask a targeted follow-up about that risk: " +
          "for example, if they did not eat, ask why; if they feel sick, ask where or since when; " +
          "if they need help or seem isolated, ask what help is needed. " +
          "Use continue_dialog when the answer is understandable and you should ask the next question. " +
          "Use repeat only when the answer is empty, unintelligible, unrelated, or not usable. " +
          "Use final_judge when enough useful answers have been collected or the dialog should end. " +
          `When turn_index is ${MAX_TURNS - 1}, ask only one last essential risk-check question if needed. ` +
          `When turn_index is ${MAX_TURNS} or higher, next_action must be final_judge. ` +
          "When next_action is final_judge, reply_text must be a closing statement, not a question. " +
          "For final_judge, thank the person briefly and say their status will be checked. " +
          "Return JSON only. Do not use markdown. " +
          "reply_text must be exactly one short Korean sentence under 45 characters. " +
          "Do not add explanations, advice, greetings, or extra empathy.",
      },
    ],
    messages: [
      {
        role: "user",
        content: [
          {
            text: JSON.stringify({
              task:
                "Choose the next short phone line and next action for this care call.",
              latest_answer: transcript,
              turn_index: turnIndex,
              required_turns: REQUIRED_TURNS,
              max_turns: MAX_TURNS,
              previous_answers: collectAnswers(sessionAttributes),
              allowed_next_actions: Array.from(NEXT_ACTIONS),
              output_schema: {
                reply_text: "Korean sentence under 45 chars",
                next_action: Array.from(NEXT_ACTIONS).join(" | "),
                reason: "short English or Korean reason",
              },
            }),
          },
        ],
      },
    ],
    inferenceConfig: {
      maxTokens: 180,
      temperature: 0.2,
    },
  });

  const response = await bedrock.send(command);
  const text = extractConverseText(response);
  return normalizeDecision(parseJsonObject(text), turnIndex);
}

function normalizeDecision(rawDecision, turnIndex) {
  const decision = {
    reply_text: String(rawDecision.reply_text || "").trim(),
    next_action: String(rawDecision.next_action || "continue_dialog").trim(),
    reason: String(rawDecision.reason || "").trim(),
  };

  if (!NEXT_ACTIONS.has(decision.next_action)) {
    throw new Error(`Invalid next_action: ${decision.next_action}`);
  }

  if (turnIndex >= MAX_TURNS) {
    decision.next_action = "final_judge";
  }

  if (!decision.reply_text || decision.reply_text.length > 45) {
    throw new Error(`Invalid reply_text: ${decision.reply_text}`);
  }

  return decision;
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

function extractConverseText(response) {
  return (response.output?.message?.content || [])
    .map((part) => part.text || "")
    .join("\n")
    .trim();
}

function parseJsonObject(text) {
  const cleaned = text
    .replace(/^```json\s*/i, "")
    .replace(/^```\s*/i, "")
    .replace(/\s*```$/i, "")
    .trim();

  try {
    return JSON.parse(cleaned);
  } catch {
    const match = cleaned.match(/\{[\s\S]*\}/);
    if (!match) {
      throw new Error(`Bedrock response was not JSON: ${text}`);
    }
    return JSON.parse(match[0]);
  }
}
