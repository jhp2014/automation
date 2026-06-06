# 05. 작업명세서 템플릿

> 새 job 추가/기존 job 수정을 AI에게 의뢰할 때 쓰는 명세서 양식.
> "규약 v1.1 따름" 을 명시하면 본 문서 [04. 규약 v1.1](04_conventions.md) 의 모든 조항을 전제로 한다.

---

## 작업명세서 #<번호> — <한 줄 제목>

### 대상

작성 또는 수정할 파일 경로를 모두 나열한다. 예시:

> `jobs/<name>/{__init__,__main__}.py`, `site_selectors/<name>.py`. 규약 v1.1 따름(한글 주석, 동작 토글 `--headless`, stage 로깅, 경로 안전성, 산출물 경로 조항).

### 전제

이 명세서가 가정하는 기존 상태(어떤 파일을 변경하지 않을지, 어떤 모듈을 그대로 쓸지). 예시:

> `common.*` 의 공개 시그니처는 변경하지 않는다. 비밀값(로그인 id/pw)은 `.env` 에서 읽는다. 기존 운영 코드는 수정 금지.

### 동작 요약

job 한 회 실행이 수행할 단계를 **stage 단위로** 기술한다. KWorks/Playwright 사용 시 어떤 common 함수를 어떤 순서로 호출하는지 명시.

> 예: `init` → `credentials` → `kworks_login` → `open_task_detail` → `type_comment(폴더명)` → `upload_files(자연정렬 이미지들)` → `submit`.

원본 스크립트가 있으면 "원본 보존" 으로 따로 묶어 다음을 명시:
- 보존할 정책/임계값 (예: `THRESHOLD_SEC = 10*60`, `CRITICAL_TITLES = {"치명","긴급","위험","주의","무해"}`)
- 보존할 셀렉터 출처 (예: 원본의 `aria-describedby` 기반 td 셀렉터를 그대로 옮긴다)
- 보존할 동작 (예: 정렬 ASC 보정, 배너 닫기 best-effort)

### CLI 인자

`argparse` 인자를 표 또는 목록으로:

| 인자 | 필수 | 기본 | 설명 |
|------|------|------|------|
| `--target-title` | 필수 | — | KWorks 작업 제목 (매일 바뀜) |
| `--folder` | 1개 이상 필수 | — | 업로드할 폴더명 (append, screenshots/server 기준 상대명) |
| `--submit` / `--no-submit` | 선택 | None→settings | Enter 최종등록 여부 (업로드형 전용) |
| `--headless` / `--no-headless` | 선택 | None→settings | 헤드리스 여부 (`BooleanOptionalAction`) |

### env 키

job 이 추가로 읽을 환경변수. 비밀값과 비밀 아닌 값을 구분.

> 비밀: `KWORKS_USER_ID`, `KWORKS_USER_PW`.
> Telegram: `get_telegram_target("<jobkey>", "<purpose>")`. (`.env.example` 에도 같은 키 추가)

### 셀렉터

| 셀렉터 출처 | 위치 |
|-------------|------|
| KWorks | `common/kworks/selectors.py` (기존, 건드리지 않음) |
| 사이트별 | `site_selectors/<name>.py` 신규 |

본문에서 셀렉터를 인라인 사용 금지.

### 산출물 경로

| 산출물 | 위치 |
|--------|------|
| 상태 파일 | `config.STATE_DIR / "<job>_<용도>.json"` |
| 로그 | `get_logger("jobs.<name>", "<name>.log")` |
| 캡처/baseline | `config.BASE_DIR / "captures" / "<job>" / ...` |

### 점검 경로 정의

`--dry-run` 은 폐지됐다(실패 시 항상 알림 전송). 점검은 동작 토글로 한다:
업로드형은 `--no-submit` 으로 Enter 최종등록 직전까지(첨부 포함) 수행, 비업로드형은
`--no-headless` 로 헤드풀 실행해 로그인/세션/페이지 진입을 눈으로 확인.

### 건드리면 안 되는 것

- 기존 운영 코드 (`source/...`) 는 참고만, 수정 금지.
- 원본 URL·셀렉터·임계값·키워드 문자열은 그대로 유지(추측 금지).
- `common.*` 의 시그니처 변경 금지(필요 시 옵션 인자 추가).

### 검증 기준

명세 이행 여부를 확인할 수 있는 구체적 명령. 예:

- `python -m jobs.<name> --help` 가 인자 목록을 정상 출력(`--headless/--no-headless` 노출)
- `python -m jobs.<name> --no-headless` 헤드풀로 로그인까지 도달 확인(업로드형은 `--no-submit` 으로 Enter 직전까지)
- (KWorks 사용 시) target 제목으로 검색되어 폼이 열리는지 정상 모드 1회 확인
- 상태/로그/캡처가 모두 `config.*` 기준 경로에 생성되는지 확인

### `.env.example` 갱신

새로 추가한 키들을 `.env.example` 에 값 없이 나열하고 한 줄 주석으로 용도 명시.

---

## 사용 팁

- 명세서는 짧을수록 좋다. 동작 요약 + 보존 사항만 명확하면 나머지는 규약이 메꿔준다.
- 새 시그니처를 요청할 때는 함수 형태를 코드 블록으로 그대로 적어주면 AI 가 추측하지 않는다.
- 환경 고유값(셀렉터/URL/임계값)은 명세서에 정확히 명시하거나 "원본 그대로 유지" 를 명시할 것.
- 검증 기준은 가능하면 외부 망/실 사이트 접속 없이 검증 가능한 명령으로 시작하라 (예: `--help`).
