# 06. 운영 가이드

> 기준 시점: runner 구현 완료 (Spec G). 본 문서는 일상 운영자 관점에서 어떻게 기동·중지·디버깅하는지를 다룬다.

## 0) 기동 한 줄

가장 쉬운 방법: [`scripts/`](../scripts) 의 bat 파일을 더블클릭하거나 cmd 에서 실행.

```powershell
# runner (main loop)
scripts\runner.bat

# 개별 job
scripts\zenius.bat
scripts\daily_service.bat
scripts\jennifer.bat
scripts\capture.bat
scripts\server.bat           # 폴더 "8 전면"/"8 후면" 하드코딩 — 본 파일 편집해서 변경 가능

# capture baseline 1회 생성 (config/settings.yaml 의 required_targets 가 좌측 모니터에 떠 있어야 OK)
scripts\capture-baseline.bat
```

> 개별 `scripts\<job>.bat` 은 디버그용으로 `--no-headless`(헤드풀)를 박아둔다.
> 운영 루프(`runner.bat`)는 토글을 박지 않으므로 `config/settings.yaml` 을 따른다.
> `--dry-run` 전용 bat 은 폐지됐다(dry-run 자체가 사라짐).

bat 들은 모두 `chcp 65001` 로 UTF-8 콘솔 설정 + 본 bat 위치 기준으로 자동으로 프로젝트 루트로 cd → cwd 가 어디든 동일하게 동작. 추가 인자가 필요하면 그대로 뒤에 붙이면 된다 (예: `scripts\server.bat --submit`).

bat 없이 직접 실행:

```powershell
cd C:\Users\whdgn\Dev\kwop\automation
.\.venv\Scripts\python.exe -m runner
```

종료: `Ctrl+C` (자식 프로세스까지 정리). 자동 종료는 [`config/daily.yaml`](../config/daily.yaml.example) 의 `run_until` (예: `"2026-05-29 08:40"`) 로 지정. 빈 문자열 또는 키 자체 생략 시 무기한 (`jobs.yaml` 의 `run_until` 폴백을 본다).

## 1) 비밀/동작 파일 (`.env` + `config/daily.yaml` + `config/settings.yaml`)

운영에서 다루는 비밀/매일값은 세 파일에 나뉘어 있다.

| 파일 | git | 빈도 | 들어갈 값 |
|------|-----|------|----------|
| `.env` | ignore | 거의 안 바뀜 | Pushover, Telegram, Supabase, 그 외 사이트별(Zenius/DailyService/Jennifer) 자격증명 |
| `config/daily.yaml` | ignore | 매일 갱신 | `run_until`, KWorks 자격증명/제목, `jobs.server` 의 `times` |
| `config/settings.yaml` | **추적** (비밀 아님) | 거의 안 바뀜 | job 별 `headless`, `submit_by_enter`, capture 대상 같은 동작 토글 |

### 1-A) `.env` (거의 안 바뀌는 비밀)

`.env.example` 을 복사해 `.env` 생성 후 채운다.

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

# 사이트별 로그인 (KWorks 제외 — KWorks 는 daily.yaml 로 이동)
ZENIUS_USER_ID=
ZENIUS_USER_PW=
DAILYSERVICE_USER_ID=
DAILYSERVICE_USER_PW=

# Jennifer 사이트별 비밀번호 (JSON 에는 id 까지만, pw 는 여기)
JENNIFER_PW__JENNIFER_CLOUD=
JENNIFER_PW__JENNIFER_GROUP_SITE=
JENNIFER_PW__JENNIFER_LMS=
JENNIFER_PW__JENNIFER_REDPEN=

# Supabase (runner 데드맨 스위치 — 비우면 runner 가 기동되지 않는다)
SUPABASE_URL=
SUPABASE_KEY=
```

### 1-B) `config/daily.yaml` (매일 갱신)

`config/daily.yaml.example` 을 복사해 `config/daily.yaml` 생성 후 채운다. 매일 운영 시작 시 이 파일 하나만 열어서 갱신하면 된다.

```yaml
# runner 자동 종료 시각. 빈 문자열이면 무기한.
run_until: "2026-05-30 08:40"

# KWorks 자격증명 + 매일 바뀌는 작업 제목 (server·capture 공유)
kworks:
  user_id: "..."
  user_pw: "..."
  target_title: "2026.05.30(토) 야간 OP관제 일일보고"

# jobs.server one_time_list 의 times. 비어 있으면 jobs.yaml 의 server.times 폴백.
# --folder 는 screenshots/server/ 기준 상대 폴더명만 받는다(절대경로 거부).
server_times:
  - at: "2026-05-30 01:32"
    args:
      - "--folder"
      - "8 전면"
      - "--folder"
      - "8 후면"
  - at: "2026-05-30 04:41"
    args:
      - "--folder"
      - "9 전면"
      - "--folder"
      - "9 후면"
```

우선순위: **CLI 인자 > daily.yaml**. 즉 수동 실행 시 `--target-title "..."` 를 주면 그 값이 이긴다.

## 2) 개별 job 수동 실행 / 점검

`--dry-run` 은 폐지됐다. 점검은 동작 토글로 한다(우선순위 `CLI 인자 > settings.yaml`).
비업로드형은 `--no-headless` 로 헤드풀 실행해 눈으로 확인하고, 업로드형은
`--no-submit` 으로 Enter 최종등록 직전까지(첨부 포함) 수행한다.

```powershell
# 비업로드형: 헤드풀로 로그인/세션/페이지 진입 확인 (실패 시 평소처럼 알림 전송됨)
.\.venv\Scripts\python.exe -m jobs.zenius --no-headless
.\.venv\Scripts\python.exe -m jobs.daily_service --no-headless
.\.venv\Scripts\python.exe -m jobs.jennifer --no-headless

# server upload (folder/target-title 필수). --folder 는 screenshots/server/ 기준
# 상대 폴더명만 받는다(절대경로 거부). 아래 예는 screenshots/server/8 전면, 8 후면.
# --no-submit 으로 첨부까지만(Enter 등록은 생략), --no-headless 로 눈으로 확인.
.\.venv\Scripts\python.exe -m jobs.server `
  --folder "8 전면" --folder "8 후면" `
  --target-title "2026.05.29(금) 야간 OP관제 일일보고" --no-submit --no-headless

# capture baseline (settings.yaml 의 capture.required_targets 가 좌측 모니터에 떠 있을 때 1회)
.\.venv\Scripts\python.exe -m jobs.capture --make-baseline

# capture 디버그: 첨부까지만(Enter 등록 생략) + 헤드풀
.\.venv\Scripts\python.exe -m jobs.capture --target-title "..." --no-submit --no-headless
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

**중요**: `jobs.server` 의 실제 `times` 는 `config/daily.yaml` 의 `server_times` 에서 관리한다. `jobs.yaml` 의 `server.times` 는 daily.yaml 이 비어 있을 때의 폴백(현재는 과거 시각 dummy)이다.

`daily.yaml` 예시:

```yaml
server_times:
  - at: "2026-05-30 01:32"
    args:
      - "--folder"
      - "8 전면"         # 상대경로 → screenshots/server/8 전면
      - "--folder"
      - "8 후면"
```

- `at` 는 반드시 `YYYY-MM-DD HH:MM`. 다른 형식은 스키마 검증에서 실패한다.
- `args` 는 job 의 CLI 그대로. runner 가 변환하지 않는다 (인자 변환 책임은 운영자에게).
- **`--target-title` 은 args 에 두지 않는다** — daily.yaml 의 `kworks.target_title` 한 곳만 갱신하면 capture·server 두 job 이 모두 새 값을 본다.
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
| runner 상태 | `state/runner.json` (running_pid, last_heartbeat_at, **runner_pid**, **last_tick_at**) |
| 스케줄 상태 | `state/scheduler.json` (last_run, hourly_plan, one_time_done) |
| 종료 신호 | `state/stop.flag` (`runnerctl stop` 이 작성, runner 가 감지 후 우아하게 종료) |
| Jennifer 세션 | `state/jennifer/<name>_session.json` |
| capture 산출물 | `screenshots/capture/` (baseline/latest/marker) |

상태 파일을 손으로 지우면 다음 tick 에 기본값으로 재생성된다. `runner_pid` 는 runner
본체 PID(자식 job 의 `running_pid` 와 별개)로, `runnerctl` / `clean` 이 이 PID 의
생존으로 "실행 중" 을 정확히 판정한다.

## 4-B) 보조 스크립트 (`tools.*` + `scripts/*.bat`)

매일 운영을 단순화하는 헬퍼 3종. 모두 `scripts/<name>.bat`(venv 파이썬 자동 사용)
래퍼가 있고, 직접 `python -m tools.<name>` 로도 돌릴 수 있다.

### gen-daily — `config/daily.yaml` 자동 생성

`config/daily.base.yaml`(안 바뀌는 템플릿 원본, **git ignore**)에 그날 날짜만 입혀
`config/daily.yaml` 을 만든다. `config/daily.base.yaml.example` 을 복사해 1회 세팅.

```bat
scripts\gen-daily.bat            REM 실행 시각으로 근무 자동 판정(08~10→주간09, 17~19→야간18, 20~22→야간21)
scripts\gen-daily.bat 21         REM shift 직접 지정(윈도우 밖이거나 강제)
scripts\gen-daily.bat -o hong    REM 자격증명 운영자 선택(operators 풀에서)
scripts\gen-daily.bat -d 2026-07-02 --dry-run
```

- **운영기준일 D**: 실행 시각이 다음날 06시 전이면 '전날' 로 본다. 각 값의 날짜 =
  `D + day`(base 의 `day` 오프셋; 0=당일, 1=다음날). 야간 새벽 캡처는 `day:1` 이라
  자동으로 D+1 에 잡힌다.
- **run_until**: 주간 = 당일 20:40, 야간 = 익일 08:40 (base 의 shift 별 정의).
- 기존 `daily.yaml` 은 `daily.yaml.bak` 로 백업 후 덮어쓴다. 생성물은 즉시
  `common.daily` 스키마로 검증한다.

### runnerctl — runner 백그라운드 제어

```bat
scripts\runnerctl.bat start      REM 백그라운드로 기동(이미 실행 중이면 거부)
scripts\runnerctl.bat status     REM runner_pid 생존 / last_tick 신선도 / 실행 중 job
scripts\runnerctl.bat logs -f    REM runner.log 추적(끝 N줄: -n N)
scripts\runnerctl.bat logs --boot REM 마지막 [RUNNER START] 이후 설정 요약 블록
scripts\runnerctl.bat stop       REM stop.flag 작성 → 우아한 종료(무응답 시 taskkill /T /F 폴백)
```

`stop` 은 `taskkill /F` 로 바로 죽이지 않는다 — runner 가 stop.flag 를 감지해
**스스로** 기존 자식 정리(`kill_all_running`) 경로로 내려가야 브라우저 등 자식이
고아로 남지 않기 때문이다. 진행 중인 job 이 끝난 뒤 종료되므로 `--timeout`(기본 60s)
안에 안 내려가면 그때 트리째 강제 종료한다.

### clean — 로그·상태 정리

```bat
scripts\clean.bat                REM logs/* + state/runner.json,scheduler.json,stop.flag
scripts\clean.bat --sessions     REM + jennifer 세션(지우면 다음 실행 때 재로그인)
scripts\clean.bat --logs-only    REM 로그만
scripts\clean.bat --dry-run
```

`runner_pid`(또는 살아있는 자식 job)가 감지되면 **삭제를 거부**한다(실행 중 삭제 시
파일 잠금 충돌 + 상태 꼬임 방지). 먼저 `runnerctl stop` 으로 정지할 것. 정말 강행하려면
`--force`. jennifer 세션은 재로그인 비용이 있어 `--sessions` 없이는 건드리지 않는다.

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

### 헤드풀로 좁히기

job 자체에 문제가 의심되면 runner 를 멈추고 해당 job 만 헤드풀로 1회 돌려본다.

```powershell
.\.venv\Scripts\python.exe -m jobs.<name> --no-headless
# 업로드형이면 Enter 등록 직전까지만:
.\.venv\Scripts\python.exe -m jobs.server --folder "8 전면" --no-submit --no-headless
```

브라우저 화면 + `stage=` 로그로 어디서 막히는지 본다(실패 시 알림 메시지에도 `stage=...` 포함). 로그인까지 도달하면 자격증명·셀렉터는 일단 OK.

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
