# 07. runner 내부 구조

> 기준 시점: Spec G 구현 완료. 시그니처는 [runner/](../runner) 의 실제 파일에서 가져왔다.

## 모듈 책임

```
runner/
├── __main__.py    메인 루프 — 부품 조립만 한다 (얇게)
├── config.py      jobs.yaml 로딩 + pydantic 스키마 검증, SUPABASE_URL/KEY (.env)
├── state.py       runner.json + scheduler.json 두 상태 파일 관리
├── scheduler.py   interval / hourly_jitter / one_time_list 3개 모드 판정
├── executor.py    sys.executable -m <module> subprocess 실행 (블로킹)
└── heartbeat.py   데드맨 스위치 (Supabase upsert + 연속 실패 임계 경고)
```

규칙: `__main__.py` 는 로직을 보유하지 않는다 — 다른 모듈들의 함수를 순서대로 호출만 한다.

## 의존 방향

```
                runner.__main__
                       │
        ┌──────────────┼──────────────┬───────────┐
        ▼              ▼              ▼           ▼
   runner.config  runner.state  runner.scheduler  runner.executor
        │              │                              │
        │              │                              │
        └──── common.{config, logging} ───────────────┘
                                                       └─ runner.heartbeat ─ common.notify (Pushover)
```

runner 는 `common.*` 만 의존하며, 어느 `jobs.*` 도 직접 import 하지 않는다 — job 호출은 subprocess.

## 인터프리터 통일 (규약 v1.1)

`executor._build_cmd` 의 핵심 한 줄:

```python
cmd = [sys.executable, "-m", job.module, *job.args, *(entry.args if entry else [])]
```

- `sys.executable` 은 runner 자신을 띄운 인터프리터.
- 단일 venv 전제이므로 job 별 venv 탐색이나 pythonw 분기를 두지 않는다.
- job 의 호출 계약은 **`python -m <module> <args...>`** 로 고정. 운영자는 `jobs.yaml` 의 `args` 만 만지면 된다.

## 상태 파일 두 개의 스키마

### `state/runner.json` — runner 소유

```json
{
  "running_pid": {
    "zenius": 12345
  },
  "last_heartbeat_at": "2026-05-29T12:31:12.123456"
}
```

- `running_pid`: 현재 실행 중인 자식의 PID. `run_job` 시작 시 추가, 종료 시 제거.
- `last_heartbeat_at`: 마지막 Supabase 송신 시각 (ISO 로컬). `should_send` 비교에 사용.
- `cleanup_dead_pids()` 가 죽은 PID 를 제거한다 (`os.kill(pid, 0)` 로 생존 확인).

### `state/scheduler.json` — 스케줄 소유

```json
{
  "last_run": {
    "zenius": "2026-05-29T12:30:55.000000"
  },
  "hourly_plan": {
    "2026-05-29 12": "2026-05-29T12:04:33"
  },
  "one_time_done": {
    "server|2026-05-29 01:32|--folder||...||--target-title||...": true
  }
}
```

- `last_run`: 각 job 의 마지막 실행 종료 시각 (ISO 로컬). interval 모드의 판정 기준.
- `hourly_plan`: 시간 키 (`%Y-%m-%d %H`) → 계획된 실행 시각 (ISO). hourly_jitter 가 시간마다 생성.
- `one_time_done`: 1회 entry 의 완료 표시. 키는 `{job_name}|{at}|{||-join(args)}`.

분리 이유: `runner.json` 은 매 tick 변경되어 빈번한 쓰기가 발생하고, `scheduler.json` 은 의미 안정적인 정책 상태라 디버깅 시 한쪽만 보면 된다.

## 스케줄 3모드 동작

### interval

```
return (now - last_run[name]) >= interval_sec
```

`last_run[name]` 이 없으면 즉시 실행. 파싱 실패도 즉시 실행 (안전한 쪽).

### hourly_jitter

```
hour_key = "YYYY-MM-DD HH"
if hour_key not in hourly_plan:
    delay = randint(0, jitter_max_sec)
    hourly_plan[hour_key] = (시간 정각) + delay
scheduled = hourly_plan[hour_key]
이번 시간(시간 단위)에 last_run 이 이미 있으면 → 실행 안 함
otherwise:
    return scheduled <= now <= scheduled + grace_sec
```

- "이번 시간 안에 한 번만" + "계획 시각 + grace 윈도우 안일 때만".
- `grace_sec` 를 너무 짧게 두면 runner tick 으로 윈도우를 놓칠 수 있다.

### one_time_list

각 entry 별로:
- `now < target` → 대기 (스킵).
- `target ≤ now ≤ target + grace_sec` → 이 entry 반환 → 실행 → done 처리.
- `now > target + grace_sec` → 늦었음. 자동 done 처리 (catch-up 방지). 운영자가 손으로 reset 하지 않으면 다시 안 돈다.

state_key 는 `{job_name}|{at}|{args 를 '||' 로 join}`. 같은 시각·다른 args 의 entry 는 서로 다른 키이므로 중복 실행 없음.

## 실행 모델 (블로킹)

`run_job` 은 자식 종료까지 폴링 (`p.poll() + time.sleep(0.5)`). `timeout_sec` 를 초과하면 `p.kill()` → `communicate()` 로 stderr 수거. 한 tick 안에 한 job 이 끝날 때까지 다음 job 으로 안 넘어간다 — 원본 동작 보존.

이 모델의 함의:
- 동시 실행은 지원하지 않는다 (`one_time_list` 가 capture 와 시간이 겹치면 직렬 처리).
- 그러나 stderr 캡처가 모든 job 에 적용되어 조기 크래시 추적이 항상 가능.

## 안전 디코딩

`_safe_decode(bytes) → str`: cp949 → utf-8 → utf-8 replace 순. Windows 콘솔이 cp949 인 경우가 흔하므로 우선 시도하고, 다른 인코딩으로 섞여 나오는 출력에서도 깨지지 않게 보호한다 (원본 `safe_decode` 계승).

## 데드맨 스위치 (Supabase heartbeat) — 약화 금지

`heartbeat.HeartbeatSender` 의 의미:

- `runner_heartbeat` 테이블에 `{source, last_seen_at}` 을 `interval_sec` 마다 upsert.
- runner 가 멈추면 `last_seen_at` 이 갱신되지 않는다 → 외부 감시 시스템이 이를 보고 경보를 띄울 수 있다.
- 본 클래스의 메서드는 **runner 본 루프를 죽이지 않는다** — 모든 송신 실패는 내부에서 흡수하고 로그 + 카운터로만 처리.
- 그러나 **연속 실패가 `MAX_CONSECUTIVE_FAILURES = 3` 에 도달하면 Pushover 로 1회 경고**. 같은 실패 스트릭 동안 추가 경고는 보내지 않으며, 송신이 한 번이라도 성공하면 카운터·플래그가 reset 된다.
- 운영자는 외부 감시 (예: Supabase 의 `last_seen_at` 가 일정 시간 이상 갱신 안 되면 알람을 띄우는 별도 워치독) 와 함께 운용해야 데드맨 스위치가 의도대로 작동한다.

코드 안에 "약화 금지" 메모와 함께 임계값 상수를 노출한 것은 이 정책이 사람의 판단 없이 바뀌지 않게 하기 위함이다.

## 메인 루프 (요약)

```
ensure_dirs
load_runner_config        ← 실패 시 즉시 종료(2)
HeartbeatSender 생성       ← URL/KEY 누락 시 즉시 종료(3) — 약화 금지
load_runner_state, load_scheduler_state
1회 heartbeat 시도

while True:
    if run_until 도달 → kill_all_running → break
    cleanup_dead_pids
    try_send_heartbeat
    for job in cfg.jobs:
        if 이미 running → skip
        mode 별 판정 + (one_time/jitter 면 state 저장)
        if 실행 → run_job (블로킹) → update_last_run / mark_one_time_done → save
    sleep(tick_sec)

KeyboardInterrupt → kill_all_running
finally → [RUNNER END] 로그
```

## 확장 메모 (현재 미구현)

- 동시 실행: `run_job` 을 백그라운드로 만들고 `running_pid` 만 기록하면 가능. 그러나 한 job 의 실패가 다른 job 의 로그/스크린샷을 어지럽힐 수 있어 정책 합의가 먼저 필요.
- jobs.yaml 핫리로드: 현재 시작 시 1회 로드. 핫리로드는 단순한 mtime 비교로 추가 가능하나, 진행 중인 schedule_entry 의 정합성 문제를 함께 풀어야 한다.

관련 문서: [01. 아키텍처](01_architecture.md), [04. 규약 v1.1](04_conventions.md).
