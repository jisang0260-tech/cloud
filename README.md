# CareCall Cloud

CareCall AWS backend repository. The current runtime source of truth is the
Lambda-console implementation in `lambda-console/`. The SAM files under `src/`
and `template.yaml` remain as an earlier scaffold/reference unless we decide to
revive SAM deployment explicitly.

## Repository Layout

```text
.
|-- lambda-console/   # Current AWS Lambda handler sources
|-- openapi/          # Swagger/OpenAPI specs shared with frontend
|-- events/           # Local/SAM sample events for scaffold handlers
|-- docs/             # Amazon Connect setup and contact-flow notes
|-- src/              # Earlier SAM scaffold handlers
`-- template.yaml     # Earlier SAM template
```

## Runtime Model

- Runtime: Python 3.12 for Lambda functions.
- Deployment style: Lambda console copy/upload is the current operational path.
- API Gateway: REST API with Lambda proxy integration.
- Frontend origin: `https://d29gc62aprgiim.cloudfront.net`.
- CORS: Lambda responses should include CORS headers, and API Gateway should
  also expose OPTIONS plus gateway responses for auth/4XX/5XX failures.

## Main Call Flow
- Amazon Connect 세팅 체크리스트: [docs/amazon-connect-setup.md](docs/amazon-connect-setup.md)
- Contact Flow 설계안: [docs/contact-flow-design.md](docs/contact-flow-design.md)
  
1. `start_outbound_call_lambda.py` is invoked manually or by EventBridge
   Scheduler.
2. The scheduler checks enabled recipients, matches `autoCallTime` against the
   current KST time, creates a pending call-history item, and starts an Amazon
   Connect outbound call.
3. Amazon Connect routes user speech into Lex. `lex_dialog_bedrock_hook.py`
   receives `inputTranscript`, calls Bedrock, and returns the next response or
   early-closing action.
4. At the end of the call, `riskjudge_bedrock_lambda.py` judges risk with
   Bedrock, writes summary/risk metadata to DynamoDB, updates user-level
   `lastRiskLevel` and `next_opening_question`, and sends SNS for danger cases.
5. Dashboard APIs read recipients, today's status, call history, corrections,
   and recording playback URLs.

## Lambda Console Functions

| File | Purpose |
| --- | --- |
| `fetch_recipients_lambda.py` | `GET /api/recipients` |
| `post_recipient_lambda.py` | `POST /api/recipients` |
| `update_recipient_lambda.py` | `PUT /api/recipients/{recipientId}` and `DELETE /api/recipients/{recipientId}` |
| `fetch_today_call_status_lambda.py` | `GET /api/calls/today?date=YYYY-MM-DD` |
| `fetch_call_history_lambda.py` | `GET /api/calls/history/{recipientName}` |
| `post_call_correction_lambda.py` | `POST /api/calls/{contactId}/correction` |
| `fetch_correction_stats_lambda.py` | `GET /api/calls/corrections/stats?day=YYYY-MM-DD` |
| `get_recording_presigned_url_lambda.py` | `GET /api/recordings/url?bucket=...&key=...&download=true` |
| `start_outbound_call_lambda.py` | EventBridge/manual outbound-call scheduler |
| `lex_dialog_bedrock_hook.py` | Lex dialog code hook backed by Bedrock |
| `riskjudge_bedrock_lambda.py` | Final risk judge, DynamoDB update, SNS alert |
| `transcribe_s3_recording_lambda.py` | S3 recording event to Transcribe job |

## Data Model

### Users Table

Typical table: `carecall-users-dev`.

Important fields:

- `recipientId`
- `recipientName`
- `phoneNumber`
- `address`
- `memo`
- `autoCallTime`
- `autoCallEnabled`
- `lastRiskLevel`
- `next_opening_question`

### Call History Table

Typical table: `carecall-call-history-dev`.

Important fields:

- `session_id`
- `contactId`
- `recipientId`
- `recipientName`
- `phoneNumber`
- `callTime`
- `createdAt`
- `status`
- `conversation`
- `summary`
- `riskLevel`
- `riskReason`
- `riskScore`
- `sentiment`
- `sentimentScore`
- `recording_s3_bucket`
- `recording_s3_key`
- `transcript_s3_bucket`
- `transcript_s3_key`

Dashboard responses expose recording metadata as `recordingS3Bucket` and
`recordingS3Key`.

### Corrections Table

Typical table: `carecall-call-corrections-dev`.

Important fields:

- `correctionId`
- `contactId`
- `originalRiskLevel`
- `correctedRiskLevel`
- `reason`
- `correctedAt`
- `correctedDate`

Correction creation stores a correction item and also synchronizes the related
call-history risk fields and the user's `lastRiskLevel`.

## DynamoDB Indexes

Recommended indexes used by the Lambda handlers:

| Table | Index | Key intent |
| --- | --- | --- |
| Users | recipient/status index if configured | Faster scheduler recipient lookup |
| Call History | `ByDateIndex` | Today's call-status lookup by `createdAt` |
| Call History | `RecipientNameIndex` | Recipient history lookup by `recipientName` |
| Call History | `RecipientIdIndex` | Rename synchronization by `recipientId` |
| Call History | `ContactIdIndex` | Correction lookup by `contactId` |
| Corrections | `CorrectionsByDateIndex` | Correction stats lookup by `correctedDate` |

Several handlers intentionally fall back to scan when an expected index is not
available, but production tables should use the indexes above for predictable
latency.

## Recording Download URL

The recording URL flow is key-based:

1. `fetch_today_call_status_lambda.py` and `fetch_call_history_lambda.py` return
   `recordingS3Bucket` and `recordingS3Key`.
2. Frontend calls
   `/api/recordings/url?bucket=<bucket>&key=<recording-key>&download=true`.
3. `get_recording_presigned_url_lambda.py` validates the bucket/key and returns
   a short-lived S3 presigned URL.

Required Lambda/IAM permission:

```text
s3:GetObject on the recording bucket/key prefix
```

Security-related environment variables:

- `RECORDINGS_BUCKET`
- `ALLOWED_BUCKETS`
- `RECORDING_KEY_PREFIX`
- `PRESIGNED_URL_EXPIRES`

## CORS Notes

Each Lambda proxy response should include:

```text
Access-Control-Allow-Origin: https://d29gc62aprgiim.cloudfront.net
Access-Control-Allow-Headers: Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token
Access-Control-Allow-Methods: GET,POST,PATCH,PUT,DELETE,OPTIONS
```

For API Gateway REST API, Lambda headers alone are not enough for every failure
path. Configure:

- OPTIONS method for each browser-facing route.
- Gateway Responses such as `DEFAULT_4XX`, `DEFAULT_5XX`, `UNAUTHORIZED`, and
  `ACCESS_DENIED` with matching CORS headers.
- Lambda invoke permission for every connected method.

## OpenAPI Specs

- `openapi/carecall-api-recipient-recording.yaml`: current recipient delete and
  recording URL spec plus recording metadata additions for today/history APIs.
- `openapi/carecall-api-3.yaml`: correction and statistics related spec.
- `openapi/carecall-api.yaml`: older broad API reference.

When an API shape changes, update the matching OpenAPI file in the same feature
commit.

## SnapStart Guidance

SnapStart is available for supported managed runtimes including Python 3.12+.
The best candidates in this repository are the Bedrock-heavy handlers:

- `lex_dialog_bedrock_hook.py`
- `riskjudge_bedrock_lambda.py`

Apply SnapStart to published versions/aliases and connect Amazon Connect or Lex
integrations to the alias ARN, not `$LATEST`. After enabling it, run Lambda
console tests and one real Connect/Lex flow because snapshot/restore can expose
init-time assumptions in SDK clients, secrets, timestamps, or network state.

## Local Validation

Basic syntax checks:

```powershell
python -m py_compile lambda-console/fetch_recipients_lambda.py
python -m py_compile lambda-console/fetch_today_call_status_lambda.py
python -m py_compile lambda-console/fetch_call_history_lambda.py
python -m py_compile lambda-console/post_call_correction_lambda.py
python -m py_compile lambda-console/get_recording_presigned_url_lambda.py
```

For AWS behavior, prefer Lambda console test events plus CloudWatch logs because
the deployed path is currently Lambda console/API Gateway rather than local SAM.

## Operational Notes

- `autoCallEnabled` is stored as boolean `true` or `false`.
- Recipient rename synchronization is handled in `update_recipient_lambda.py`
  when `recipientName` changes.
- Recipient delete removes only the user-table item; call-history records are
  intentionally preserved.
- Correction stats count correction-table records for the requested date.
- SNS danger alerts are sent by `riskjudge_bedrock_lambda.py` when the judged
  risk level reaches the configured danger condition.
