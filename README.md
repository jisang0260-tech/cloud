# CareCall Backend Scaffold

`spec_report_2026_Team D.pdf` 기준으로, 당신 담당 범위인 `Amazon Connect + API Gateway + Cognito 인증 + API Lambda` 에 맞춰 정리한 백엔드 초안입니다.

## 현재 범위

- Amazon Connect 설정 체크리스트
- Contact Flow 설계 문서
- API Gateway HTTP API
- Cognito User Pool / JWT 인증
- Dashboard API Lambda 3개

이번 저장소에는 `Scheduler Lambda` 와 `EventBridge` 구현을 포함하지 않습니다.

## 구현된 API

- `GET /api/v1/calls/status?date=YYYY-MM-DD`
- `GET /api/v1/calls/logs/{session_id}`
- `PATCH /api/v1/targets/{user_id}/auto-call`

## 프로젝트 구조

```text
.
├── docs
│   ├── amazon-connect-setup.md
│   └── contact-flow-design.md
├── events
│   ├── get-call-detail.json
│   ├── get-call-status.json
│   └── patch-auto-call.json
├── src
│   ├── common
│   │   ├── auth.py
│   │   ├── config.py
│   │   ├── errors.py
│   │   ├── repositories.py
│   │   ├── responses.py
│   │   └── services.py
│   └── handlers
│       ├── get_call_log_detail
│       │   └── app.py
│       ├── get_call_status
│       │   └── app.py
│       └── update_auto_call
│           └── app.py
└── template.yaml
```

## 배포 전제

- AWS SAM CLI 설치
- AWS 자격 증명 설정
- Amazon Connect 인스턴스와 Contact Flow 준비
- 관리자 로그인용 Cognito User Pool 사용자 생성

## 배포 방법

```bash
sam build
sam deploy --guided
```

## 인증 구조

- API Gateway HTTP API 에 Cognito JWT Authorizer 를 붙였습니다.
- 유효한 JWT 가 아니면 API Gateway 단계에서 차단됩니다.
- Lambda 내부에서는 `admin` 그룹 또는 `custom:role=admin` 클레임을 한 번 더 확인합니다.

## 데이터 모델

### TargetsTable

대상자 기본 정보와 자동 발신 설정을 저장합니다.

```json
{
  "user_id": "U10293",
  "name": "김OO",
  "phone_number": "+821012341234",
  "auto_call_enabled": true,
  "auto_call_status": "ENABLED",
  "preferred_call_time": "10:00",
  "updated_at": "2026-03-24T14:45:00Z"
}
```

### SessionsTable

통화 세션, 분석 결과, 녹취 파일 키를 저장합니다.

```json
{
  "session_id": "sess_8a7b6c5d",
  "user_id": "U10293",
  "target_name": "김OO",
  "call_date": "2026-03-24",
  "start_time": "2026-03-24T10:15:30Z",
  "call_status": "COMPLETED",
  "risk_level": "HIGH",
  "analysis_summary": "우울 감정 지수 85% 감지, 식사 누락 호소",
  "call_duration_sec": 124,
  "audio_s3_key": "2026/03/24/sess_8a7b6c5d.wav",
  "analysis_s3_key": "2026/03/24/sess_8a7b6c5d_report.json",
  "transcript": [
    {
      "speaker": "AI_AGENT",
      "time_offset": "00:00",
      "text": "안녕하세요, 오늘 식사는 잘 챙겨 드셨나요?"
    }
  ]
}
```

## 로컬 테스트 예시

```bash
sam local invoke GetCallStatusFunction --event events/get-call-status.json
sam local invoke GetCallLogDetailFunction --event events/get-call-detail.json
sam local invoke UpdateAutoCallFunction --event events/patch-auto-call.json
```

## 문서

- Amazon Connect 세팅 체크리스트: [docs/amazon-connect-setup.md](/C:/Users/USER/Documents/api/docs/amazon-connect-setup.md)
- Contact Flow 설계안: [docs/contact-flow-design.md](/C:/Users/USER/Documents/api/docs/contact-flow-design.md)

## 참고

- PDF 안의 "위에서 정리한 7가지" 원문이 없어서, 실제 Connect 구축에 필요한 핵심 7개 세팅 항목으로 재구성했습니다.
- 실시간 자연어 분기는 추후 Amazon Lex V2 연동으로 확장 가능하도록 문서에 반영했습니다.
