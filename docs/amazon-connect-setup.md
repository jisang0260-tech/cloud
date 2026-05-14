# Amazon Connect 세팅 체크리스트

이번 문서는 당신 담당 범위인 `Amazon Connect 설정` 자체에 집중해서 정리했습니다.  
즉, 여기서는 Connect 인스턴스와 Contact Flow, 녹음, Lambda 연동 포인트까지를 다루고, 스케줄링 실행 주체는 별도 담당으로 봅니다.

## 1. Connect 인스턴스 생성

- 리전: `ap-northeast-2`
- 관리자 계정 생성
- 아웃바운드 통화 허용 여부 확인
- 운영 시간은 MVP 기준으로 기본값 설정

산출물:

- `ConnectInstanceId`
- 관리자 콘솔 URL

## 2. 발신 번호 Claim

- Amazon Connect 에서 발신 번호를 Claim
- E.164 형식으로 정리
- 실제 수신 단말에서 발신 번호가 정상 노출되는지 확인

## 3. Queue / Routing / Security Profile 설정

- 기본 Queue 생성: `carecall-outbound`
- 관리자용 Security Profile 생성
- 향후 상담원 전환이 필요하면 Routing Profile 추가

MVP 기준:

- 현재는 자동 안내 중심이므로 최소 구성으로 시작 가능

## 4. 통화 녹음 및 로그 설정

- Contact recording 활성화
- Contact Trace Record 확인
- CloudWatch logging 활성화

권장 저장 항목:

- `session_id`
- `user_id`
- `target_name`
- `call_date`

## 5. Outbound Contact Flow 생성

필수 블록 흐름:

1. `Set logging behavior`
2. `Set contact attributes`
3. `Play prompt` 인사말
4. 질문 시나리오 진행
5. 무응답 / 실패 / 정상 종료 분기
6. 종료

상세 설계는 [docs/contact-flow-design.md](/C:/Users/USER/Documents/api/docs/contact-flow-design.md) 참고.

## 6. Lambda 연동 포인트 정의

이번 저장소에서 구현한 Lambda 는 대시보드 백엔드용입니다.

- `GET /api/v1/calls/status`
- `GET /api/v1/calls/logs/{session_id}`
- `PATCH /api/v1/targets/{user_id}/auto-call`

즉, Connect 자체가 직접 이 Lambda 들을 호출하는 구조는 아니고, 관리자 대시보드가 API Gateway 를 통해 호출하는 구조입니다.

필요하면 추후 별도 Lambda 를 추가해 Connect Contact Flow 와 직접 연결할 수 있습니다.

## 7. 테스트 시나리오

1. Connect 인스턴스 접속
2. Contact Flow 저장 및 Publish
3. 발신 번호 확인
4. 테스트 콜 수행
5. 녹음과 로그 확인
6. 대시보드에서 세션 / 분석 결과 조회

## 범위 메모

- Connect 설정과 Contact Flow 설계는 당신 범위
- API Gateway / Cognito / API Lambda 도 당신 범위
- EventBridge 스케줄러와 Scheduler Lambda 는 이번 저장소 범위에서 제외
