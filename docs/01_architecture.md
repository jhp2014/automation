# 01. 아키텍처

> 기준 시점: 현재 코드 일치. runner 구현 완료, jennifer / daily.yaml 반영.

## 의존 방향 (단방향 그래프)

```
   site_selectors/*      common/kworks ───┐
         ▲                    ▲           │
         │ import             │ import    │
         │                    │           │
         └────── jobs/<name> ─┘           │
                     │                    │
                     │ import             │
                     ▼                    │
         common.{config, daily, logging,  ◀┘
                  notify, browser}
                     ▲
                     │ import (단방향)
                     │
                  runner/*
```

규칙:
- `jobs/*` 는 `common.*`, `common.kworks`, `site_selectors.*` 을 import 한다.
- `common.kworks` 는 `common.{config, logging}` 만 의존한다.
- `common.config` 는 import 시 `common.daily.load_daily()` 를 호출해 daily.yaml 을 읽고 모듈 상수로 노출한다 (KWORKS_*, RUN_UNTIL, SERVER_TIMES).
- `runner/*` 는 `common.*` 만 의존한다 — 어느 `jobs.*` 도 직접 import 하지 않는다 (호출은 subprocess).
- `common/*` 끼리는 필요한 한 방향 의존만.
- `jobs ↔ jobs` 는 절대 import 하지 않는다. job 간 협업은 runner 의 스케줄/순서로만.

## 패키지별 책임

### common/config.py
- `.env` 로딩 (python-dotenv) + `config/daily.yaml` 로딩 (common.daily 위임).
- import 시 위 두 파일을 1회씩 읽는다 (디렉터리 생성·네트워크는 X). 디렉터리 생성은 `ensure_dirs()` 호출 시에만.
- 경로 상수: `BASE_DIR`, `LOG_DIR`, `STATE_DIR`.
- Pushover 상수 (`.env`): `PUSHOVER_TOKEN`, `PUSHOVER_USER`.
- KWorks 상수 (`daily.yaml`): `KWORKS_USER_ID`, `KWORKS_USER_PW`, `KWORKS_TARGET_TITLE`.
- runner 상수 (`daily.yaml`): `RUN_UNTIL`, `SERVER_TIMES`.
- `TelegramTarget(bot_token, chat_id)` + `get_telegram_target(job_key, purpose)` — Telegram 봇/채팅 동적 조회 (`.env`).

### common/daily.py
- `config/daily.yaml` 로더. pydantic 으로 스키마 검증 (`DailyConfig`, `DailyKworks`, `DailyServerTime`).
- `load_daily()` 가 1회 캐시. 파일이 없으면 `None`. 파싱/검증 실패도 `None` + 경고 로그.
- 순환 의존 방지를 위해 `common.config` 를 import 하지 않고 BASE_DIR 를 자체 계산한다.

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
- 현재 파일: `zenius.py`, `daily_service.py`, `jennifer.py`.
- 새 job 의 셀렉터는 `site_selectors/<job>.py` 로 추가.
- 명칭 주의: 패키지명이 Python stdlib `selectors` 와 충돌하지 않도록 `site_selectors` 를 사용한다.

### jobs/
- 각 job 폴더 = `__init__.py` (빈 파일) + `__main__.py` (얇은 진입점).
- `python -m jobs.<name>` 으로 단독 실행되며, 비즈니스 로직은 전부 common 에 두고 `__main__.py` 는 조립만 한다.
- 현재 5개: `zenius`, `daily_service`, `jennifer`, `server`, `capture` (`window_utils.py` 동반).
- 모든 job 은 다음을 따른다:
  - `sys.path` 부트스트랩 (파일 최상단)
  - `argparse` + `--headless`/`--no-headless` (업로드형은 `--submit`/`--no-submit` 도)
  - `stage` 변수 진입 로그
  - 실패 시 마지막 stage 를 알림 메시지에 포함
  - 상태 파일은 `config.STATE_DIR`, 로그는 `config.LOG_DIR`, 그 외 산출물은 `config.BASE_DIR/captures/<job>/`.

### runner/
- `config/jobs.yaml` (구조) + `config/daily.yaml` (매일 갱신값) 두 파일을 읽어 pydantic 으로 검증.
- 각 job 을 `[sys.executable, "-m", <module>, *args]` 로 subprocess 호출 (인터프리터 통일 조항).
- 3개 스케줄 모드: `interval` / `hourly_jitter` / `one_time_list`.
- Supabase 에 데드맨 스위치 heartbeat (3회 연속 실패 시 Pushover 경고 1회).
- 상태는 두 파일로 분리: `state/runner.json` (running_pid, last_heartbeat_at) + `state/scheduler.json` (last_run, hourly_plan, one_time_done).
- 자세한 내부 구조는 [07. runner](07_runner.md) 참고.

### config/
- `jobs.yaml` — runner 일정 **구조** (모드/모듈/주기/timeout). 거의 안 바뀜. git 추적.
- `daily.yaml` — 매일 갱신값 (run_until, KWorks 자격증명·target_title, server.times). git ignore. `.env` 와 동격이라 비밀값 OK.
- `settings.yaml` — 동작 토글 (job 별 headless / submit_by_enter). git 추적, 비밀 금지. 폴백 없음(부재/스키마 위반 시 에러).
- `jennifer_sites.json` — Jennifer 사이트 목록 (id 까지만, pw 는 .env).

## 새 job 의 정형 흐름

```
1. argparse 로 인자 수신 (--headless/--no-headless 등; default=None)
2. headless = args.headless if not None else config.get_headless("<job>")  (CLI > settings, 폴백 없음)
3. config.ensure_dirs()
4. stage = "init" → "credentials" / "resolve_target_title" → "kworks_login" → ...
5. 예외 핸들러: 마지막 stage 를 포함한 Pushover/Telegram 알림 (항상 전송)
6. finally 에서 [END] 로그
```

다음 문서: [02. common API](02_common_api.md).
