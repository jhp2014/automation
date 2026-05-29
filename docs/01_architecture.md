# 01. 아키텍처

> 기준 시점: 현재까지 구현된 범위. runner 는 미구현이며 본 문서에서는 "예정"으로만 언급한다.

## 의존 방향 (단방향 그래프)

```
        site_selectors/*       common/kworks ───┐
              ▲                       ▲         │
              │ import                │ import  │
              │                       │         │
              └────── jobs/<name> ────┘         │
                          │                     │
                          │ import              │
                          ▼                     │
              common.{config, logging,  ◀───────┘
                       notify, browser}
```

규칙:
- `jobs/*` 는 `common.*`, `common.kworks`, `site_selectors.*` 을 import 한다.
- `common.kworks` 는 `common.{config, logging}` 만 의존한다.
- `common/*` 끼리는 필요한 한 방향 의존만 (예: `notify` → `logging`/`config`).
- `jobs ↔ jobs` 는 절대 import 하지 않는다. job 간 협업은 runner 의 스케줄/순서로만.

## 패키지별 책임

### common/config.py
- `.env` 로딩 (python-dotenv). import 시 부작용 없음.
- 경로 상수: `BASE_DIR`, `LOG_DIR`, `STATE_DIR`.
- `ensure_dirs()` — 명시적으로 호출해야 디렉터리 생성.
- Pushover 상수: `PUSHOVER_TOKEN`, `PUSHOVER_USER`.
- `TelegramTarget(bot_token, chat_id)` 와 `get_telegram_target(job_key, purpose)` — Telegram 봇/채팅의 동적 조회.

### common/logging.py
- `get_logger(name, log_file=None, level="INFO")`.
- 콘솔 + 5MB 로테이팅 파일(백업 10). 핸들러 중복 추가 방지. `propagate=False`.
- 상대 경로의 `log_file` 은 `config.LOG_DIR` 기준으로 해석.

### common/notify.py
- Pushover 긴급 (priority=2) + Telegram 메시지/사진/heartbeat.
- 전송 실패는 절대 예외를 던지지 않는다 — 로거 경고만 남기고 swallow.
- 호출부는 `TelegramTarget` 을 통째로 받는 시그니처라 봇 토큰을 직접 다루지 않는다.

### common/browser.py
- `sync_browser(...)` 컨텍스트 매니저: Chromium launch + context + page 생성, with 종료 시 자동 정리.
- `--window-size` launch arg + viewport 동기화 (큰 뷰포트 캡처 패턴).
- 깨진 storage_state 자동 폴백(빈 컨텍스트 + 경고 로그).
- teardown 은 close 예외를 삼킨다. launch 실패는 전파.
- `save_storage_state(context, path)` — 상위 폴더 자동 생성.

### common/kworks/
- `selectors.py` — 모든 KWorks DOM 셀렉터 + `ALL_TASK_FILTERS` 리스트 + 파라미터형 셀렉터 헬퍼.
- `client.py` — `KworksClient` 클래스. Playwright `page` 를 주입받아 `login → open_task_detail → type_comment → upload_files → submit` 흐름을 제공. 브라우저/컨텍스트 lifecycle 은 호출부 (혹은 `common.browser`) 책임.
- KWorks 셀렉터는 본 모듈 외부 어디에도 인라인으로 두지 않는다.

### site_selectors/
- KWorks 가 아닌 사이트별 셀렉터·URL 상수.
- 현재 파일: `zenius.py`, `daily_service.py`.
- 새 job 의 셀렉터는 `site_selectors/<job>.py` 로 추가.
- 명칭 주의: 패키지명이 Python stdlib `selectors` 와 충돌하지 않도록 `site_selectors` 를 사용한다.

### jobs/
- 각 job 폴더 = `__init__.py` (빈 파일) + `__main__.py` (얇은 진입점).
- `python -m jobs.<name>` 으로 단독 실행되며, 비즈니스 로직은 전부 common 에 두고 `__main__.py` 는 조립만 한다.
- 모든 job 은 다음을 따른다:
  - `sys.path` 부트스트랩 (파일 최상단)
  - `argparse` + `--dry-run`
  - `stage` 변수 진입 로그
  - 실패 시 마지막 stage 를 알림 메시지에 포함
  - 상태 파일은 `config.STATE_DIR`, 로그는 `config.LOG_DIR`, 그 외 산출물은 `config.BASE_DIR/captures/<job>/`.

### runner/ (예정)
- `config/jobs.yaml` 의 일정 정의를 읽어 `python -m jobs.<name>` 을 subprocess 로 호출.
- runner 가 job 인자를 변환하지 않는다 — YAML 에 `args` 리스트를 그대로 둔다.
- 현재 단계에서는 본 문서에 상세 미기재.

## 새 job 의 정형 흐름

```
1. argparse 로 인자 수신 (--dry-run 포함)
2. config.ensure_dirs()
3. stage = "init" → "credentials" → "kworks_login"/"open_task_detail" → ...
4. dry-run 분기: 로그인/세션 확인까지만 수행 후 종료
5. 예외 핸들러: 마지막 stage 를 포함한 Pushover/Telegram 알림 (dry-run 에서는 알림 생략)
6. finally 에서 [END] 로그
```

다음 문서: [02. common API](02_common_api.md).
