# 04. 규약 v1.1 (전문)

> 기준 시점: 현재까지 합의된 모든 조항(원본 v1 + 보강 사항 통합본).

## 목적

운영 중인 자동화 스크립트들을 monorepo 로 재작성한다. 기존 코드는 참고자료이며, 그대로 옮기지 않고 공통 로직을 정리해 새로 짓는다.

## 1. 아키텍처 원칙

**구조는 통합하고 실행은 분리한다.** 하나의 프로젝트, 하나의 `.venv` 를 쓰되, 각 job 은 독립 프로세스로 실행한다. runner 는 각 job 을 `python -m jobs.<name>` 을 subprocess 로 호출하는 방식으로만 구동한다. job 끼리는 서로를 import 하지 않는다. 공통 코드는 오직 `common` 패키지를 통해서만 공유한다.

## 2. 디렉터리 구조

```
automation/
  .venv/
  pyproject.toml
  .env                      # 비밀값 (git ignore)
  .env.example              # 키 이름만 (git 포함)
  common/
    __init__.py
    config.py               # .env 로딩 + 공통 설정
    logging.py              # 로거 통일
    notify.py               # pushover + telegram 통합
    browser.py              # Playwright 헬퍼
    kworks/
      __init__.py
      client.py             # KWorks 조작 공통
      selectors.py          # KWorks 셀렉터 전용
  runner/                   # (예정)
  jobs/
    <name>/__main__.py
  config/
    jobs.yaml               # JOBS 일정 정의 (예정)
  site_selectors/           # job별 셀렉터 (KWorks 외)
  state/                    # 상태 파일 (job별/용도별 분리)
  logs/
  captures/<job>/           # job별 캡처 산출물
```

## 3. 코딩 규약

모든 공개 함수·메서드에는 타입 힌트를 단다. 공개 함수에는 docstring 으로 입력·출력·발생 예외를 명시한다. job 의 `__main__.py` 는 얇게 유지한다 — 비즈니스 로직은 전부 `common` 에 두고, `__main__.py` 는 common 함수를 조립·호출만 한다.

## 4. 셀렉터 규약

셀렉터는 코드에 인라인으로 쓰지 않는다. KWorks 셀렉터는 `common/kworks/selectors.py` 에, 그 외 job 별 셀렉터는 `site_selectors/<job>.py` 에 상수로 모은다. 셀렉터 상수명은 `SEL_` 접두사를 쓴다.

> 명칭 변경 메모: 원래 `selectors/` 였으나 Python stdlib 의 `selectors` 모듈과 충돌해서 `site_selectors/` 로 리네임했다.

## 5. 설정·비밀값 규약

비밀값은 코드와 **committed YAML** (jobs.yaml 등) 에 절대 평문으로 넣지 않는다. 비밀값은 두 곳에 분산해 보관하며 **둘 다 git ignore** 대상이다:

| 파일 | 빈도 | 용도 |
|------|------|------|
| `.env` | 거의 안 바뀜 | 정적 비밀(Pushover/Telegram/Supabase 토큰, Zenius·DailyService·Jennifer 자격증명) |
| `config/daily.yaml` | 매일 갱신 | run_until, KWorks 자격증명·target_title, `jobs.server.times` |

`.env.example` / `config/daily.yaml.example` 에는 키 이름과 구조만 두고 값은 비운다. `common/config.py` 가 양쪽을 로드해 모듈 상수로 노출한다 (`common/daily.py` 가 daily.yaml 의 로더).

우선순위: **CLI 인자 > daily.yaml > (해당 없음)**. `.env` 는 별도 키들이라 충돌 없음.

Telegram 봇은 job 마다 다르며 명명 규칙으로 동적 로딩한다 — 코드에 박지 않는다.

```
TELEGRAM_BOT__<JOBKEY> = <bot_token>
TELEGRAM_CHAT__<JOBKEY>__<PURPOSE> = <chat_id>
```

새 job 추가는 `.env` 두 줄 추가만으로 끝나야 한다(코드 수정 없음).

## 6. 일정(YAML) 규약

JOBS 일정은 `config/jobs.yaml` 에 **구조** 만 정의한다(모드/모듈/주기/timeout). 매일 갱신하는 값(`run_until`, `jobs.server.times`)은 `config/daily.yaml` 에서 관리하며, runner 가 로드 시 jobs.yaml 위에 덮어쓴다.

각 job 은 실행 인자를 `args` 리스트로 직접 명시한다(러너가 변환하지 않는다). YAML 은 pydantic 으로 스키마 검증되며, 오류 시 어느 job·어느 필드가 잘못됐는지 명확히 보고한다.

## 7. 로그 규약

모든 로그는 `common/logging.py` 의 단일 팩토리로 생성한다. 포맷은 `%(asctime)s | %(levelname)s | %(message)s`, 파일은 5MB 로테이팅, 백업 10개. 콘솔+파일 동시 출력. 로거는 `propagate=False`, 핸들러 중복 추가를 방지한다.

## 8. 알림 규약

알림은 `common/notify.py` 로 통합한다. 긴급 알림은 Pushover(priority=2), 상태성 메시지(heartbeat/report)는 Telegram 을 쓴다. 알림 전송 실패는 예외를 던지지 않고 경고 로그만 남긴다(알림 실패가 본 작업을 죽이면 안 된다).

## 9. 산출물 경로 규약

상태 파일은 `config.STATE_DIR`, 로그는 `config.LOG_DIR` 에 둔다. 그 외 job 이 생성하는 산출물(캡처 이미지, baseline 등)은 `config.BASE_DIR` 하위의 용도별 폴더에 둔다 — 캡처류는 `BASE_DIR/captures/<job>/`. 모든 경로는 cwd 가 아닌 config 기준 절대경로로 해석한다.

## 10. dry-run 의무 조항

모든 job 은 `--dry-run` 플래그를 지원한다. dry-run 에서는 **로그인(또는 세션 확인)까지만** 수행하고, 그 이후의 실제 작업(데이터 스캔/업로드/알림 전송)은 하지 않는다. 단, dry-run 에서도 "어디까지 도달했는지" 를 로그로 남긴다. 알림(Pushover/Telegram)은 dry-run 에서 전송하지 않는다.

## 11. stage 로깅 조항

각 job 은 주요 단계를 `stage` 변수로 표시하고 진입 시 로그를 남긴다. 예외 발생 시 로그·알림 메시지에 마지막 `stage` 를 포함한다. 이렇게 해서 실패 지점을 로그만으로 특정할 수 있게 한다.

## 12. 실행·검증 규약

각 job 은 `python -m jobs.<name>` 으로 단독 실행 가능해야 한다. 가능한 경우 로그인까지만 하고 멈추는 dry-run 모드를 제공한다. 새 버전은 기존 운영 코드를 건드리지 않고 별도 디렉터리에서 짓는다.

## 13. AI 작업 규약

명세서는 "규약 v1.1 따름" 을 전제로 한다. 명세에 없는 환경 고유값(셀렉터·URL)은 추측하지 말고 질문한다. common 모듈의 기존 함수 시그니처는 변경하지 않는다 — 필요 시 옵션 인자를 추가하는 방식으로 확장한다. 주석은 한글로 작성하되, docstring 의 `Args:`/`Returns:`/`Raises:` 헤더는 영문 유지.

다음 문서: [05. 작업명세서 템플릿](05_job_spec_template.md).
