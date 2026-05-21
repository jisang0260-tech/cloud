import json
import os
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import boto3
from botocore.exceptions import ClientError


BEDROCK_REGION = os.getenv("BEDROCK_REGION", "ap-northeast-2")
BEDROCK_MODEL_ID = os.getenv(
    "BEDROCK_MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0"
)
COMPREHEND_REGION = os.getenv("COMPREHEND_REGION", "ap-northeast-2")
SNS_REGION = os.getenv("SNS_REGION", "ap-northeast-2")
SNS_TOPIC_ARN = os.getenv("SNS_TOPIC_ARN", "")
MAX_ANSWERS = int(os.getenv("MAX_ANSWERS", "8"))
DATA_REGION = os.getenv("DATA_REGION", "us-east-1")
CALL_HISTORY_TABLE = os.getenv("CALL_HISTORY_TABLE", "")
USERS_TABLE = os.getenv("USERS_TABLE") or os.getenv("TARGETS_TABLE", "")
recipientId_ATTR = os.getenv("recipientId_ATTR", "recipientId")
NEXT_OPENING_QUESTION_ATTR = os.getenv(
    "NEXT_OPENING_QUESTION_ATTR",
    "next_opening_question",
)
LEGACY_NEXT_OPENING_PROMPT_ATTR = os.getenv(
    "NEXT_OPENING_PROMPT_ATTR",
    "next_opening_prompt",
)
NEXT_OPENING_MAX_CHARS = int(os.getenv("NEXT_OPENING_MAX_CHARS", "90"))
UPDATE_CALL_HISTORY = os.getenv("UPDATE_CALL_HISTORY", "true").lower() == "true"
UPDATE_USER_NEXT_OPENING = os.getenv("UPDATE_USER_NEXT_OPENING", "true").lower() == "true"
STRICT_HISTORY_UPDATE = os.getenv("STRICT_HISTORY_UPDATE", "false").lower() == "true"
DEFAULT_OPENING_QUESTION = os.getenv(
    "DEFAULT_OPENING_QUESTION",
    os.getenv(
        "OPENING_PROMPT",
        "안녕하세요 경희대 복지센터입니다. 오늘 식사는 하셨나요?",
    ),
)

RISK_LEVELS = {"normal", "caution", "danger"}

CLOSING_TEXT_BY_RISK = {
    "normal": "오늘 말씀 감사합니다. 건강한 하루 보내세요.",
    "caution": "말씀 감사합니다. 담당자가 확인 후 연락드릴게요.",
    "danger": "상황을 확인했습니다. 담당자에게 바로 전달하겠습니다.",
}

bedrock = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
comprehend = boto3.client("comprehend", region_name=COMPREHEND_REGION)
sns = boto3.client("sns", region_name=SNS_REGION)
dynamodb = boto3.resource("dynamodb", region_name=DATA_REGION)
call_history_table = dynamodb.Table(CALL_HISTORY_TABLE) if CALL_HISTORY_TABLE else None
users_table = dynamodb.Table(USERS_TABLE) if USERS_TABLE else None

KST = ZoneInfo("Asia/Seoul")


def lambda_handler(event, context):
    print("riskjudge event:", json.dumps(event, ensure_ascii=False))

    contact_data = event.get("Details", {}).get("ContactData", {})
    attrs = contact_data.get("Attributes", {})
    answers = collect_answers(attrs)

    if not answers:
        raise ValueError("No answer_n attributes were provided.")

    decision = judge_risk(answers, attrs)
    closing_text = CLOSING_TEXT_BY_RISK[decision["risk_level"]]
    sentiment_result = detect_sentiment_from_answers(answers)

    try:
        update_call_history(contact_data, attrs, answers, decision, closing_text, sentiment_result)
        update_user_next_opening_question(attrs, decision)
        publish_danger_alert(contact_data, attrs, answers, decision)

    except Exception as error:
        print("CallHistory/User update failed:", str(error))
        if STRICT_HISTORY_UPDATE:
            raise

    return {
        "risk_level": decision["risk_level"],
        "risk_score": str(decision["risk_score"]),
        "risk_reason": decision["risk_reason"],
        "analysis_summary": decision["analysis_summary"],
        "next_opening_question": decision["next_opening_question"],
        "sentiment": sentiment_result["sentiment"],
        "sentimentScore": sentiment_result["sentimentScore"],
        "closing_text": closing_text,
    }


def judge_risk(answers, attrs):
    response = bedrock.converse(
        modelId=BEDROCK_MODEL_ID,
        system=[
            {
                "text": (
                    "You are a Korean elderly care-call risk judge. "
                    "Your job is to classify risk from the collected answers only. "
                    "Return JSON only. Do not use markdown. "
                    "risk_level must be exactly one of: normal, caution, danger. "
                    "Use normal when the person appears okay, has eaten or gives no concerning signs, "
                    "and does not request help. "
                    "Use caution when there are mild or ambiguous concerns that should be checked by a caregiver, "
                    "such as skipped meals without urgent symptoms, mild pain, dizziness, loneliness, "
                    "unclear answers, or possible need for follow-up. "
                    "Use danger when urgent human attention may be needed, such as inability to eat due to pain, "
                    "severe or persistent symptoms, breathing difficulty, fall risk, inability to move, "
                    "request for immediate help, being alone with concerning symptoms, or any unsafe situation. "
                    "risk_score must be an integer from 0 to 100. "
                    "risk_reason and analysis_summary must be Korean and concise. "
                    "next_opening_question must be a Korean phone-call opening for the next call. "
                    "If recipientName is provided, next_opening_question must include that name with 님. "
                    "It must include a brief greeting and exactly one question based on analysis_summary. "
                    "If there is no specific concern, ask a general meal or health check question. "
                    f"next_opening_question must be under {NEXT_OPENING_MAX_CHARS} Korean characters."
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
                                "task": "Judge final risk level and prepare the next call opening question.",
                                "session_id": attrs.get("session_id", ""),
                                "recipientId": attrs.get("recipientId", ""),
                                "recipientName": get_recipient_name(attrs),
                                "current_opening_question": get_current_opening_question(attrs),
                                "conversation": build_conversation_context(attrs, answers),
                                "answers": answers,
                                "output_schema": {
                                    "risk_level": "normal | caution | danger",
                                    "risk_score": "integer 0-100",
                                    "risk_reason": "short Korean reason",
                                    "analysis_summary": "one Korean sentence for dashboard",
                                    "next_opening_question": "Korean greeting plus one follow-up question for next call",
                                },
                            },
                            ensure_ascii=False,
                        )
                    }
                ],
            }
        ],
        inferenceConfig={
            "maxTokens": 320,
            "temperature": 0.1,
        },
    )

    text = extract_converse_text(response)
    return normalize_decision(parse_json_object(text), get_recipient_name(attrs))


def collect_answers(attrs):
    answers = []

    for index in range(1, MAX_ANSWERS + 1):
        key = f"answer_{index}"
        value = attrs.get(key)

        if value and str(value).strip():
            answers.append(
                {
                    "key": key,
                    "text": str(value).strip(),
                }
            )

    return answers


def detect_sentiment_from_answers(answers):
    text = " ".join(
        str(answer.get("text") or "").strip()
        for answer in answers
        if str(answer.get("text") or "").strip()
    ).strip()

    if not text:
        return {"sentiment": "NEUTRAL", "sentimentScore": 0}

    try:
        response = comprehend.detect_sentiment(
            Text=trim_utf8_text(text, 4500),
            LanguageCode="ko",
        )
        sentiment = response.get("Sentiment") or "NEUTRAL"
        scores = response.get("SentimentScore") or {}
        sentiment_score = scores.get(sentiment.title(), 0)

        return {
            "sentiment": sentiment,
            "sentimentScore": int(round(sentiment_score * 100)),
        }
    except Exception as error:
        print("Comprehend sentiment failed:", str(error))
        return {"sentiment": "NEUTRAL", "sentimentScore": 0}


def trim_utf8_text(text, max_bytes):
    encoded = str(text).encode("utf-8")
    if len(encoded) <= max_bytes:
        return str(text)
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def get_current_opening_question(attrs):
    return str(
        attrs.get(NEXT_OPENING_QUESTION_ATTR)
        or attrs.get("next_opening_question")
        or attrs.get(LEGACY_NEXT_OPENING_PROMPT_ATTR)
        or attrs.get("next_opening_prompt")
        or DEFAULT_OPENING_QUESTION
    ).strip()


def get_recipient_name(attrs):
    return str(
        attrs.get("recipientName")
        or attrs.get("recipient_name")
        or attrs.get("name")
        or ""
    ).strip()


def personalize_opening_question(question, recipientName):
    question = str(question or "").strip()
    recipientName = str(recipientName or "").strip()

    if not question or not recipientName:
        return question
    if recipientName in question or f"{recipientName}님" in question:
        return question

    marker = "경희대 복지센터입니다."
    if marker in question:
        return question.replace(marker, f"{marker} {recipientName}님", 1)

    return f"{recipientName}님 {question}"


def build_conversation_context(attrs, answers):
    answer_by_index = {
        int(answer["key"].split("_")[1]): answer["text"]
        for answer in answers
        if answer.get("key") and answer.get("text")
    }
    context = [{"speaker": "AI", "text": get_current_opening_question(attrs)}]

    for index in sorted(answer_by_index):
        if index > 1:
            question = str(attrs.get(f"question_{index}") or "").strip()
            if question:
                context.append({"speaker": "AI", "text": question})

        answer = str(answer_by_index.get(index) or "").strip()
        if answer:
            context.append({"speaker": "대상자", "text": answer})

    return context


def update_call_history(contact_data, attrs, answers, decision, closing_text, sentiment_result):
    if not UPDATE_CALL_HISTORY:
        return

    if not call_history_table:
        print("CallHistory update skipped: CALL_HISTORY_TABLE is not configured.")
        return

    session_id = str(attrs.get("session_id") or "").strip()
    if not session_id:
        raise ValueError("Missing required contact attribute: session_id")

    contact_id = str(contact_data.get("ContactId") or attrs.get("contact_id") or "").strip()
    recipientId = str(attrs.get("recipientId") or "").strip()
    now = datetime.now(KST).isoformat()
    current_record = get_call_history_item(session_id)
    duration = calculate_duration_seconds(current_record)

    answer_updates = {
        answer["key"]: answer["text"]
        for answer in answers
        if answer.get("key") and answer.get("text")
    }

    expression_names = {
        "#status": "status",
        "#riskLevel": "riskLevel",
        "#riskScore": "riskScore",
        "#riskReason": "riskReason",
        "#analysis_summary": "analysis_summary",
        "#sentiment": "sentiment",
        "#sentimentScore": "sentimentScore",
        "#duration": "duration",
        "#conversation": "conversation",
        "#updated_at": "updated_at",
        "#judged_at": "judged_at",
    }
    expression_values = {
        ":status": "응답",
        ":riskLevel": decision["risk_level"],
        ":riskScore": decision["risk_score"],
        ":riskReason": decision["risk_reason"],
        ":analysis_summary": decision["analysis_summary"],
        ":sentiment": sentiment_result["sentiment"],
        ":sentimentScore": sentiment_result["sentimentScore"],
        ":duration": duration,
        ":conversation": build_conversation(attrs, answers, closing_text),
        ":updated_at": now,
        ":judged_at": now,
    }
    set_parts = [
        "#status = :status",
        "#riskLevel = :riskLevel",
        "#riskScore = :riskScore",
        "#riskReason = :riskReason",
        "#analysis_summary = :analysis_summary",
        "#sentiment = :sentiment",
        "#sentimentScore = :sentimentScore",
        "#duration = :duration",
        "#conversation = :conversation",
        "#updated_at = :updated_at",
        "#judged_at = :judged_at",
    ]

    if contact_id:
        expression_names["#contact_id"] = "contact_id"
        expression_values[":contact_id"] = contact_id
        set_parts.append("#contact_id = if_not_exists(#contact_id, :contact_id)")

    if recipientId:
        expression_names["#recipientId"] = "recipientId"
        expression_values[":recipientId"] = recipientId
        set_parts.append("#recipientId = if_not_exists(#recipientId, :recipientId)")



    call_history_table.update_item(
        Key={"session_id": session_id},
        UpdateExpression="SET " + ", ".join(set_parts),
        ExpressionAttributeNames=expression_names,
        ExpressionAttributeValues=expression_values,
    )


def get_call_history_item(session_id):
    response = call_history_table.get_item(Key={"session_id": session_id})
    return response.get("Item") or {}


def calculate_duration_seconds(record):
    started_at = (
        record.get("callTime")
        or record.get("createdAt")
        or record.get("created_at")
        or record.get("started_at")
    )
    started = parse_datetime(started_at)

    if not started:
        return None

    now = datetime.now(KST)
    return max(0, int(round((now - started).total_seconds())))

def parse_datetime(value):
    if not value:
        return None

    try:
        text = str(value).strip()
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=KST)
        return parsed.astimezone(KST)
    except ValueError:
        return None


def update_user_next_opening_question(attrs, decision):
    if not UPDATE_USER_NEXT_OPENING:
        return

    if not users_table:
        print("User next opening question update skipped: USERS_TABLE is not configured.")
        return

    recipientId = str(attrs.get("recipientId") or "").strip()
    if not recipientId:
        print("User next opening question update skipped: user_id/recipientId is missing.")
        return

    now = datetime.now(timezone.utc).isoformat()

    try:
        users_table.update_item(
            Key={recipientId_ATTR: recipientId},
            UpdateExpression=(
                "SET #next_opening_question = :next_opening_question, "
                "#lastRiskLevel = :lastRiskLevel"

             
            ),
            ExpressionAttributeNames={
                "#next_opening_question": NEXT_OPENING_QUESTION_ATTR,
                "#lastRiskLevel" : "lastRiskLevel"
        
            },
            ExpressionAttributeValues={
                ":next_opening_question": decision["next_opening_question"],
                ":lastRiskLevel": decision["risk_level"]
                
            },
        )
    except ClientError as error:
        print(f"User next opening question update failed for {recipientId_ATTR}={recipientId}: {error}")
        raise

def publish_danger_alert(contact_data, attrs, answers, decision):
    if decision["risk_level"] != "danger":
        return

    if not SNS_TOPIC_ARN:
        print("SNS danger alert skipped: SNS_TOPIC_ARN is not configured.")
        return

    recipientName = get_recipient_name(attrs) or ""
    recipientId = str(attrs.get("recipientId") or "").strip() or ""
    phone = str(attrs.get("phone_e164") or "").strip() or ""
 

    message = "\n".join(
        [
            "[CareCall 위험 알림]",
            "",
            f"대상자: {recipientName}",
            f"recipientId: {recipientId}",
            f"연락처: {phone}",
            "",
            f"위험도: {decision['risk_level']}",
            f"위험 점수: {decision['risk_score']}",
            f"위험 사유: {decision['risk_reason']}",
            f"요약: {decision['analysis_summary']}",
            ""
        ]
    )

    try:
        sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=f"[CareCall 위험] {recipientName}",
            Message=message,
        )
    except Exception as error:
        print("SNS danger alert failed:", str(error))



def build_conversation(attrs, answers, closing_text):
    answer_by_index = {
        int(answer["key"].split("_")[1]): answer["text"]
        for answer in answers
        if answer.get("key") and answer.get("text")
    }

    conversation = [{"speaker": "AI", "text": get_current_opening_question(attrs)}]

    for index in sorted(answer_by_index):
        if index > 1:
            question = str(attrs.get(f"question_{index}") or "").strip()
            if question:
                conversation.append({"speaker": "AI", "text": question})

        answer = str(answer_by_index.get(index) or "").strip()
        if answer:
            conversation.append({"speaker": "대상자", "text": answer})

    conversation.append({"speaker": "AI", "text": closing_text})

    return conversation


def normalize_decision(raw_decision, recipient_name):
    risk_level = str(raw_decision.get("risk_level") or "").strip().lower()

    if risk_level not in RISK_LEVELS:
        raise ValueError(f"Invalid risk_level: {risk_level}")

    try:
        risk_score = int(raw_decision.get("risk_score"))
    except (TypeError, ValueError):
        raise ValueError(f"Invalid risk_score: {raw_decision.get('risk_score')}")

    if not 0 <= risk_score <= 100:
        raise ValueError(f"risk_score out of range: {risk_score}")

    risk_reason = str(raw_decision.get("risk_reason") or "").strip()
    analysis_summary = str(raw_decision.get("analysis_summary") or "").strip()
    next_opening_question = str(
        raw_decision.get("next_opening_question")
        or raw_decision.get("next_opening_prompt")
        or ""
    ).strip()

    if not risk_reason:
        raise ValueError("risk_reason is required.")

    if not analysis_summary:
        raise ValueError("analysis_summary is required.")

    if not next_opening_question:
        next_opening_question = "안녕하세요 경희대 복지센터입니다. 오늘 몸 상태는 어떠신가요?"

    next_opening_question = personalize_opening_question(next_opening_question, recipient_name)

    if len(next_opening_question) > NEXT_OPENING_MAX_CHARS:
        next_opening_question = next_opening_question[:NEXT_OPENING_MAX_CHARS].rstrip()

    return {
        "risk_level": risk_level,
        "risk_score": risk_score,
        "risk_reason": risk_reason,
        "analysis_summary": analysis_summary,
        "next_opening_question": next_opening_question,
    }


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
