import json
import os
import re

import boto3


BEDROCK_REGION = os.getenv("BEDROCK_REGION", "ap-northeast-1")
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "jp.amazon.nova-2-lite-v1:0")
MAX_TURNS = int(os.getenv("MAX_TURNS", "7"))
REQUIRED_TURNS = int(os.getenv("REQUIRED_TURNS", "3"))
NEXT_OPENING_QUESTION_ATTR = os.getenv(
    "NEXT_OPENING_QUESTION_ATTR",
    "next_opening_question",
)
NEXT_ACTIONS = {"continue_dialog", "repeat", "final_judge"}

bedrock = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)

def get_lex_session_attributes(event):
    return (
        event.get("sessionState", {}).get("sessionAttributes")
        or event.get("sessionAttributes")
        or {}
    )


def get_current_opening_question(event):
    attrs = get_lex_session_attributes(event)

    return str(
        attrs.get(NEXT_OPENING_QUESTION_ATTR)
        or attrs.get("next_opening_question")
        or attrs.get("NEXT_OPENING_QUESTION_ATTR")
        or DEFAULT_OPENING_QUESTION
    ).strip()

def lambda_handler(event, context):
    print("lex event:", json.dumps(event, ensure_ascii=False))

    transcript = str(event.get("inputTranscript") or "").strip()
    session_state = event.get("sessionState") or {}
    previous_attributes = session_state.get("sessionAttributes") or {}
    current_turn = int(previous_attributes.get("turn_index") or "0") + 1

    session_attributes = stringify_attributes(
        {
            **previous_attributes,
            f"answer_{current_turn}": transcript,
            "turn_index": str(current_turn),
        }
    )

    decision = decide_next_step(event,transcript, current_turn, session_attributes)

    session_attributes.update(
        stringify_attributes(
            {
                "reply_text": decision["reply_text"],
                "next_action": decision["next_action"],
                "dialog_reason": decision["reason"],
            }
        )
    )

    print("dialog decision:", json.dumps(session_attributes, ensure_ascii=False))

    return lex_close_response(event, session_attributes)


def decide_next_step(event,transcript, turn_index, session_attributes):

    current_opening_question = get_current_opening_question(event)
    response = bedrock.converse(
        modelId=BEDROCK_MODEL_ID,
        system=[
                {
                    "text": (
                        "You are a Korean care-call dialog manager for an elderly care service. "
                        "Your role is to have warm, natural conversations with elderly users,"
                        "Risk signals may include loneliness, illness, confusion, pain, skipped meals, medication issues, falls, emergency situations, or unusual changes in mood or behavior. "
                        "Do not force the conversation into a negative mood just to find risk signals. "
                        "If there is no clear risk signal, ask a natural general question about the user's day. "
                        "Always speak politely, warmly, and naturally in Korean, in a style familiar and appropriate for elderly users. "
                        f"The Connect flow already asked the opening question: '{current_opening_question}'. "
                        "answer_1 is the user's response to this opening question. "
                        "question_2 is stored in session attributes and is the AI question asked after answer_1. "
                        "answer_2 is the user's response to question_2. "
                        "question_3 is stored in session attributes and is the AI question asked after answer_2. "
                        "answer_3 is the user's response to question_3. "
                        "Continue this question_n → answer_n mapping for all turns. "
                        "Use the full mapped conversation flow to decide the next response. "
                        "Do not treat every answer as a response to the opening question."
                        "also you summarize user`s daily life."
                        "Every non-final response must ask exactly one next question. "
                        "If the answer suggests risk, ask a targeted follow-up about that risk. "
                        "for example, if they did not eat, ask why; if they feel sick, ask where or since when; "
                        "if they need help or seem isolated, ask what help is needed. "
                        "Use continue_dialog when the answer is understandable and you should ask the next question. "
                        "Use repeat only when the answer is empty, unintelligible. "
                        "Use final_judge when enough useful answers have been collected or the dialog should end. "
                        f"When turn_index is {MAX_TURNS - 1}, ask only one last essential risk-check question if needed. "
                        f"When turn_index is {MAX_TURNS} or higher, next_action must be final_judge. "
                         "When next_action is final_judge, reply_text must be a closing statement, not a question. "
                        "Return JSON only. Do not use markdown. "
                        "reply_text must be one or two short Korean sentence under 45 characters. "
                    )
                }
        ],
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "text": json.dumps(
                            {
                                "task": (
                                    "Choose the next short phone line and next action "
                                    "for this care call."
                                ),
                                "latest_answer": transcript,
                                "turn_index": turn_index,
                                "required_turns": REQUIRED_TURNS,
                                "max_turns": MAX_TURNS,
                                "previous_answers": collect_answers(session_attributes),
                                "allowed_next_actions": sorted(NEXT_ACTIONS),
                                "output_schema": {
                                        "reply_text": "Korean sentence under 45 chars",
                                        "next_action": "continue_dialog | repeat | final_judge",
                                        "reason": "short English or Korean reason",
                                    },
                            },
                            ensure_ascii=False,
                        )
                    }
                ],
            }
        ],
        inferenceConfig={
            "maxTokens": 180,
            "temperature": 0.2,
        },
    )

    text = extract_converse_text(response)
    return normalize_decision(parse_json_object(text), turn_index)


def normalize_decision(raw_decision, turn_index):
    decision = {
        "reply_text": str(raw_decision.get("reply_text") or "").strip(),
        "next_action": str(raw_decision.get("next_action") or "continue_dialog").strip(),
        "reason": str(raw_decision.get("reason") or "").strip(),
    }

    if decision["next_action"] not in NEXT_ACTIONS:
        raise ValueError(f"Invalid next_action: {decision['next_action']}")

    if turn_index >= MAX_TURNS:
        decision["next_action"] = "final_judge"

    if not decision["reply_text"] or len(decision["reply_text"]) > 45:
        raise ValueError(f"Invalid reply_text: {decision['reply_text']}")

    return decision


def lex_close_response(event, session_attributes):
    intent = ((event.get("sessionState") or {}).get("intent") or {})
    intent_name = intent.get("name") or "CaptureSpeechIntent"

    return {
        "sessionState": {
            "sessionAttributes": session_attributes,
            "dialogAction": {"type": "Close"},
            "intent": {
                "name": intent_name,
                "state": "Fulfilled",
            },
        }
    }


def collect_answers(session_attributes):
    answers = []

    for key, value in session_attributes.items():
        if re.fullmatch(r"answer_\d+", key):
            answers.append(
                {
                    "key": key,
                    "text": str(value or ""),
                }
            )

    return sorted(answers, key=lambda item: int(item["key"].split("_")[1]))


def stringify_attributes(attributes):
    return {str(key): str(value or "") for key, value in (attributes or {}).items()}


def extract_converse_text(response):
    content = (((response.get("output") or {}).get("message") or {}).get("content") or [])
    return "\n".join(part.get("text", "") for part in content).strip()


def parse_json_object(text):
    cleaned = re.sub(r"^```json\s*|^```\s*|\s*```$", "", text.strip(), flags=re.I)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if not match:
            raise ValueError(f"Bedrock response was not JSON: {text}")
        return json.loads(match.group(0))