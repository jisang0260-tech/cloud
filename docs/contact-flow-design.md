# Contact Flow 설계안

이번 문서는 Amazon Connect 콘솔에서 Contact Flow 를 직접 만들 때 바로 옮겨 적을 수 있도록 정리한 설계안입니다.  
스케줄링 실행 주체는 별도 담당으로 보고, Connect 내부 대화 흐름만 정의합니다.

## 목표

- 대상자에게 자동 안내 전화를 수행
- 최소 질문으로 건강 상태와 응급 여부 확인
- 통화 녹음과 메타데이터를 후속 분석 파이프라인으로 전달
- 위험군 판단에 필요한 문맥을 확보

## MVP 설계 방향

- 1차 버전은 `정해진 질문 + 전체 통화 녹음 + 사후 STT/분석` 중심
- 실시간 자연어 분기 처리가 필요해지면 Amazon Lex V2 를 추가 연동

## 권장 Contact Attributes

- `session_id`
- `user_id`
- `target_name`
- `call_date`
- `care_manager_id`

## 메인 플로우

1. `Entry`
2. `Set contact attributes`
3. `Set logging behavior`
4. `Play prompt - Greeting`
5. `Play prompt - Question 1`
6. `Play prompt - Question 2`
7. `Play prompt - Question 3`
8. `Play prompt - Closing`
9. `Disconnect / End flow`

## 실패 / 무응답 분기

1. 연결 실패 또는 무응답
2. 상태를 `FAILED` 또는 `UNANSWERED` 로 기록
3. 후속 저장 로직에서 세션 상태 반영
4. 종료

## 긴급 대응 분기

1. 대상자가 도움 요청 키워드를 말함
2. `emergency_flag=true` 같은 속성 기록
3. 후속 분석 또는 알림 모듈이 위험 상태 반영

## 권장 프롬프트

### Greeting

```text
안녕하세요. 케어콜 자동 안부 확인 서비스입니다.
잠시 건강 상태를 확인하겠습니다. 편하게 말씀해 주세요.
```

### Question 1

```text
오늘 식사는 잘 하셨나요? 식사 여부와 입맛 상태를 말씀해 주세요.
```

### Question 2

```text
오늘 몸 상태는 어떠신가요? 아프거나 불편한 곳이 있으면 말씀해 주세요.
```

### Question 3

```text
오늘 기분은 어떠신가요? 외롭거나 힘든 점이 있으면 말씀해 주세요.
```

### Closing

```text
급하게 도움이 필요하시면 지금 말씀해 주세요.
곧 통화를 종료하겠습니다. 오늘도 건강하세요.
```

## Connect 콘솔에서 체크할 것

- Contact recording: ON
- Contact attributes 전달: ON
- CloudWatch logging: ON
- 오류 분기용 프롬프트 추가

## 대시보드 백엔드와의 연결 포인트

- 관리자 대시보드는 API Gateway 를 통해 Lambda 3개를 호출
- 통화 결과는 `SessionsTable`
- 원본 녹음은 S3 `RecordingsBucket`
- 분석 결과는 S3 `AnalysisBucket`

즉, 이 저장소의 Lambda 는 `관리자용 조회/제어 API` 역할입니다.

## 확장 포인트

### 1. Lex V2 연동

- 실시간 음성 입력 구조화
- 응답 분기와 키워드 감지 개선

### 2. Post-call 분석 파이프라인

- S3 업로드 트리거
- Transcribe
- Comprehend / Bedrock
- RiskJudge Lambda

### 3. 관리자 알림

- HIGH / CRITICAL 위험도 시 알림 전송

## 범위 메모

- 이번 문서는 Contact Flow 설계 자체에 집중
- 스케줄링을 누가 트리거하느냐는 별도 구현 영역
