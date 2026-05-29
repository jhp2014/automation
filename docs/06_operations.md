# 06. 운영 가이드

> 기준 시점: runner 구현 완료 (Spec G). 본 문서는 일상 운영자 관점에서 어떻게 기동·중지·디버깅하는지를 다룬다.

## 0) 기동 한 줄

```powershell
cd C:\Users\whdgn\Dev\kwop\automation
.\.venv\Scripts\python.exe -m runner
```

종료: `Ctrl+C` (자식 프로세스까지 정리). 자동 종료는 `config/jobs.yaml` 의 `run_until` (예: `"2026-05-29 08:40"`) 로 지정. 빈 문자열 또는 키 자체 생략 시 무기한.

## 1) `.env` 준비 (모든 키 한눈에)

`.env.example` 을 복사해 `.env` 생성 후 채운다. 누락된 키는 해당 기능을 쓰는 시점에 명확한 에러로 알려준다.

```ini
# Pushover (긴급 알림 단일 계정)
PUSHOVER_TOKEN=
PUSHOVER_USER=

# Telegram (job 단위 분리, 동적 로딩)
TELEGRAM_BOT__ZENIUS=
TELEGRAM_CHAT__ZENIUS__REPORT=
TELEGRAM_CHAT__ZENIUS__HEARTBEAT=
TELEGRAM_BOT__DAILYSERVICE=
TELEGRAM_CHAT__DAILYSERVICE__HEARTBEAT=

# 사이트별 로그인
ZENIUS_USER_ID=
ZENIUS_USER_PW=
DAILYSERVICE_USER_ID=
DAILYSERVICE_USER_PW=
KWORKS_USER_ID=          # jobs.server + jobs.capture 공유
KWORKS_USER_PW=

# Jennifer 사이트별 비밀번호 (JSON 에는 id 까지만, pw 는 여기)
JENNIFER_PW__JENNIFER_CLOUD=
JENNIFER_PW__JENNIFER_GROUP_SITE=
JENNIFER_PW__JENNIFER_LMS=
JENNIFER_PW__JENNIFER_REDPEN=

# Supabase (runner 데드맨 스위치 — 비우면 runner 가 기동되지 않는다)
SUPABASE_URL=
SUPABASE_KEY=
```

## 2) 개별 job 수동 실행 / dry-run

각 job 은 단독 실행 가능. dry-run 은 로그인까지만 도달하고 알림/외부 작업을 모두 생략한다.

```powershell
# 셰이크다운: 자격증명·세션·페이지 진입까지만 검증
.\.venv\Scripts\python.exe -m jobs.zenius --dry-run
.\.venv\Scripts\python.exe -m jobs.daily_service --dry-run
.\.venv\Scripts\python.exe -m jobs.jennifer --dry-run

# server upload (folder/target-title 필수)
.\.venv\Scripts\python.exe -m jobs.server `
  --folder "C:\images\8 전면" --folder "C:\images\8 후면" `
  --target-title "2026.05.29(금) 야간 OP관제 일일보고" --dry-run

# capture baseline (운영 사이트 4개가 좌측 모니터에 떠 있을 때 1회)
.\.venv\Scripts\python.exe -m jobs.capture --make-baseline

# capture full path (--target-title 필요)
.\.venv\Scripts\python.exe -m jobs.capture --target-title "..." --dry-run
```

각 job 의 인자 전체는 `--help` 로 확인.

## 3) `jobs.yaml` 편집

규약: 모든 일정·인자는 [`config/jobs.yaml`](../config/jobs.yaml) 에만 둔다. 코드 수정 불필요.

### 주기 변경 (interval)

```yaml
- name: zenius
  module: jobs.zenius
  mode: interval
  interval_sec: 180     # 여기 숫자만 바꾸면 됨
  timeout_sec: 600
```

### hourly_jitter 의 jitter 범위·grace 조정

```yaml
- name: capture
  module: jobs.capture
  mode: hourly_jitter
  jitter_max_sec: 600   # 매 시간 0..600s 안에 1회
  grace_sec: 60         # 계획 시각으로부터 60s 안에만 실행 (놓치면 다음 시간으로)
  timeout_sec: 900
```

### one_time_list 의 at / args 갱신

```yaml
- name: server
  module: jobs.server
  mode: one_time_list
  grace_sec: 300
  timeout_sec: 3600
  times:
    - at: "2026-05-29 01:32"
      args:
        - "--folder"
        - "C:/automation/server-helper/images/8 전면"
        - "--folder"
        - "C:/automation/server-helper/images/8 후면"
        - "--target-title"
        - "2026.05.29(금) 야간 OP관제 일일보고"
```

- `at` 는 반드시 `YYYY-MM-DD HH:MM`. 다른 형식은 스키마 검증에서 실패한다.
- `args` 는 job 의 CLI 그대로. runner 가 변환하지 않는다 (인자 변환 책임은 운영자에게).
- 같은 entry 는 정확히 1회 실행 (`state/scheduler.json` 의 `one_time_done` 에 기록).
- `at + grace_sec` 가 지난 entry 는 자동으로 done 처리 (catch-up 방지).

### 새 job 추가

새 job 모듈은 [03_job_authoring](03_job_authoring.md) 참고. `jobs.yaml` 에는 항목만 추가하면 끝:

```yaml
  - name: my_new_job
    module: jobs.my_new_job
    mode: interval
    interval_sec: 600
    timeout_sec: 300
```

스키마 검증이 어느 필드가 빠졌는지 즉시 잡아준다.

## 4) 로그·상태 파일 위치

| 종류 | 경로 |
|------|------|
| runner 로그 | `logs/runner.log` |
| job 로그 | `logs/<job>.log` (예 `logs/zenius.log`) |
| runner 상태 | `state/runner.json` (running_pid, last_heartbeat_at) |
| 스케줄 상태 | `state/scheduler.json` (last_run, hourly_plan, one_time_done) |
| Jennifer 세션 | `state/jennifer/<name>_session.json` |
| capture 산출물 | `captures/capture/` (baseline/latest/marker) |

상태 파일을 손으로 지우면 다음 tick 에 기본값으로 재생성된다.

## 5) 트러블슈팅

### "runner alive but heartbeat failing" Pushover 가 왔을 때

- `runner` 자체는 멀쩡한데 Supabase 송신만 실패하는 상태.
- 확인 순서:
  1. `SUPABASE_URL` / `SUPABASE_KEY` 가 만료되거나 잘못 갱신되지 않았는지 (`.env`).
  2. 호스트의 외부 통신이 막혔는지 (`curl https://<your-project>.supabase.co/rest/v1/`).
  3. `logs/runner.log` 의 `[HEARTBEAT] 송신 실패 #N` 로그에서 마지막 에러 객체 확인.
- 임계(3회 연속)에 도달했을 때 1회만 보낸다. 복구되면 카운터·경고 플래그가 reset.

### job 이 안 도는 것 같을 때

1. `logs/runner.log` 에 `[<job명>] START` 라인이 찍히는지.
2. 안 찍히면 `state/scheduler.json` 의 `last_run` / `hourly_plan` / `one_time_done` 확인.
   - interval: `last_run` 시각 + `interval_sec` 이 지났는가?
   - hourly_jitter: `hourly_plan["YYYY-MM-DD HH"]` 의 계획 시각 + `grace_sec` 윈도우를 이미 놓쳤는지.
   - one_time_list: 해당 entry 가 이미 done 으로 찍혀 있는지 (at + grace 지나서 자동 done 됐을 수도).
3. `running_pid` 에 좀비 PID 가 남아 있는지 (`state/runner.json`). runner 가 자동 정리하지만, 외부에서 강제 종료된 경우 즉시 안 보일 수 있음.

### dry-run 으로 좁히기

job 자체에 문제가 의심되면 runner 를 멈추고 해당 job 만 dry-run 해본다.

```powershell
.\.venv\Scripts\python.exe -m jobs.<name> --dry-run
```

로그인까지 도달하면 자격증명·셀렉터는 일단 OK. 그 다음 정상 모드로 1회 수동 실행해 어디서 막히는지 본다 (`stage=` 로그 + 알림 메시지의 `stage=...`).

### `jobs.yaml` 검증 에러 메시지

`jobs.0.interval_sec` 처럼 어느 job·어느 필드인지 점 표기로 표시된다. 메시지 본문에는 `job '<name>': mode=interval 인데 interval_sec 누락` 식으로 사람 친화 설명도 함께. 두 줄을 같이 보면 원인 즉시 식별.

## 6) 운영 체크리스트

매일:
- runner 로그에서 `[FAIL]` 또는 `STDERR` 블록 검색.
- Supabase 콘솔에서 `runner_heartbeat.last_seen_at` 최근 갱신 확인 (외부 데드맨 감시 별도 권장).

주기적으로:
- `jobs.yaml` 의 `target_title` 같은 매일 바뀌는 값 업데이트 누락 확인.
- `state/scheduler.json` 의 `one_time_done` 누적 정리(원하면 손으로 비워도 됨 — 다음 tick 에 미실행분만 다시 평가).

관련 문서: [07. runner 내부 구조](07_runner.md).
