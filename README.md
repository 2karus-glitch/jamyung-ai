# JAMYUNG.AI

실시간 평판 위기 조기경보 시스템 프로토타입입니다.

## 핵심 기능

- 기업/브랜드 키워드 기반 평판 데이터 분석
- Naver News RSS 기반 best-effort 뉴스 수집
- 시뮬레이션 모드
- 한국어 규칙 기반 감성 점수
- 부정 언급 비율
- Z-score 기반 이상징후
- 언급량 증가 속도
- 이슈 유형 분류
- 위험 키워드 TOP 10
- 종합 Risk Score
- CRISIS / WARNING / CAUTION / NORMAL 단계
- 대응 플레이북
- 원문 스트림
- CSV 다운로드

## 파일 구조

```text
JAMYUNG.AI/
├── app.py
├── requirements.txt
└── README.md
```

## 로컬 실행

Python 3.10 이상을 권장합니다.

```bash
pip install -r requirements.txt
streamlit run app.py
```

브라우저에서 Streamlit이 표시하는 로컬 주소로 접속합니다.

## GitHub + Streamlit 배포

1. GitHub에 새 repository를 만듭니다.
2. `app.py`, `requirements.txt`, `README.md`를 업로드합니다.
3. Streamlit 배포 서비스에서 GitHub repository를 연결합니다.
4. Main file은 `app.py`로 지정합니다.
5. Deploy 합니다.

## 데이터 모드

### 시뮬레이션

서비스 구조와 Risk Score를 테스트하기 위한 가상 데이터입니다.

### 실데이터

현재는 Naver News RSS를 best-effort 방식으로 조회합니다.

RSS 제공 방식이나 외부 서비스 정책이 변경될 경우 실데이터 수집이 실패할 수 있습니다. 이 경우 앱은 가상 데이터로 몰래 대체하지 않고 오류를 표시합니다.

## Risk Score

현재 프로토타입은 다음 신호를 결합합니다.

- Volume: 부정 언급 비율
- Anomaly: 이전 구간 대비 이상 증가
- Velocity: 언급량 증가 속도
- Intensity: 평균 부정 감성 강도
- Issue Concentration: 특정 이슈의 집중도

단계:

- 75 이상: CRISIS
- 55 이상: WARNING
- 30 이상: CAUTION
- 30 미만: NORMAL

## 중요한 한계

현재 버전의 감성분석은 사전 기반 규칙입니다. 따라서 실제 AI 감성모델과 동일한 의미의 자연어 이해 시스템은 아닙니다.

또한 RSS는 진정한 의미의 실시간 스트리밍이 아니므로, 제품 소개에서는 필요에 따라 "최근 수집 데이터 기반 평판 조기경보"라고 표현하는 것이 안전합니다.

## 다음 단계

실서비스 수준으로 발전시키려면 다음 순서를 권장합니다.

1. 뉴스 수집 안정화
2. Naver API 또는 추가 뉴스 소스 연동
3. 중복 기사 제거
4. 7일/30일 rolling baseline 구축
5. 키워드가 아닌 이슈 클러스터링
6. 뉴스·커뮤니티·블로그 등 source diversity 확대
7. 알림 시스템
8. DB 저장
9. 사용자별 기업 모니터링
10. AI 기반 문맥 감성/위험도 분석

JAMYUNG.AI v3.1

## v4.0 업데이트 (데모)

이전 버전 대비 다음이 반영되었습니다.

- **다중 소스 수집**: Naver News + Google News RSS를 동시에 조회하고, 제목 유사도 기반으로 중복 기사를 제거합니다. 일부 소스가 실패해도 나머지 소스로 계속 진행하며, 실패 사실은 화면에 표시됩니다.
- **AI 감성분석(선택)**: Anthropic API Key를 입력하면 사전 기반 규칙 대신 Claude(Haiku 4.5 / Sonnet 5)가 문맥을 보고 감성 점수와 이슈 유형을 매깁니다. 키가 없거나 호출이 실패하면 자동으로 규칙 기반 점수로 대체됩니다.
- **AI 이슈 클러스터링(실험적, 선택)**: scikit-learn TF-IDF + KMeans로 제목을 자동 군집화합니다. 기사 수가 너무 적으면(8건 미만) 생략됩니다.
- **롤링 베이스라인 개선**: 시계열 버킷 간격(6/12/24시간)을 선택할 수 있고, 베이스라인으로 사용된 구간 수와 기간(일)을 화면에 표시해 Z-score의 신뢰도를 더 투명하게 보여줍니다.
- **웹훅 알림(선택)**: Slack/Discord 호환 웹훅 URL을 등록하면 리스크 등급이 상승할 때(예: WARNING 이상) 자동으로 알림을 전송합니다. 세션 내 알림 로그도 확인할 수 있습니다.

이 버전은 여전히 프로토타입이며, 알림 로그와 베이스라인은 세션이 끝나면 초기화됩니다(영구 저장은 다음 단계 항목인 DB 연동에서 다룰 예정입니다).

JAMYUNG.AI v4.0
