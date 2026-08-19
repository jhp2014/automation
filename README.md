# automation

KWorks / Zenius / DailyService / Jennifer 자동화 모노레포 (규약 v1.1).

이 문서는 **스크립트 사용법**만 정리한다. 아키텍처·job 작성·설치 등 상세는
[`docs/`](docs/) 참고 (특히 [운영 가이드](docs/06_operations.md), [설치](docs/08_install.md)).

모든 스크립트는 `scripts\*.bat` 래퍼로 실행하며, 래퍼가 `.venv` 파이썬을 자동으로
찾는다. 직접 `python -m <module>` 로도 돌릴 수 있다.

---

## 하루 운영 흐름 (요약)

```bat
scripts\gen-daily.bat          REM 1) 근무 시작 무렵 — config/daily.yaml 자동 생성
scripts\runnerctl.bat start    REM 2) runner 백그라운드 기동
scripts\runnerctl.bat status   REM    상태 확인
scripts\runnerctl.bat logs -f  REM    로그 실시간 추적
scripts\runnerctl.bat stop     REM 3) 근무 종료 시 우아하게 종료
scripts\clean.bat              REM 4) (선택) 로그·상태 정리
```

> 처음 쓰기 전 `config\daily.base.yaml.example` 을 `config\daily.base.yaml` 로
> 복사해 운영자 자격증명과 근무별 시각/폴더를 채운다. (git 추적 안 함 — 비밀값 OK)

---

## 운영 스크립트

### `gen-daily` — 오늘자 `config/daily.yaml` 생성

`config/daily.base.yaml`(안 바뀌는 템플릿)에 **그날 날짜만** 입혀 `daily.yaml` 을
만든다. 실행 시각으로 근무(주간/야간)와 기준 시각을 자동 판정한다.

```bat
scripts\gen-daily.bat                REM 실행 시각으로 자동 판정
scripts\gen-daily.bat 21             REM shift 직접 지정(09 / 18 / 21)
scripts\gen-daily.bat -o hong        REM 자격증명 운영자 선택
scripts\gen-daily.bat -d 2026-07-02  REM 운영기준일 강제(YYYY-MM-DD)
scripts\gen-daily.bat --dry-run      REM 파일 안 쓰고 결과만 출력
```

| 실행 시각대 | 근무 | 기준 | run_until |
|---|---|---|---|
| 08~10시 | 주간 | 09 | **당일** 20:40 |
| 17~19시 | 야간 | 18 | **익일** 08:40 |
| 20~22시 | 야간 | 21 | **익일** 08:40 |

- **운영기준일 D**: 실행 시각이 다음날 06시 전이면 '전날' 로 본다.
- 각 값의 날짜 = `D + day`(base 의 `day` 오프셋: 0=당일, 1=다음날). 야간 새벽 캡처는
  `day:1` 이라 자동으로 D+1 에 잡힌다.
- 위 시간대 밖에서 인자 없이 돌리면 에러 → shift 를 직접 지정(`gen-daily.bat 18`).
- 기존 `daily.yaml` 은 `daily.yaml.bak` 로 백업 후 덮어쓴다. 생성물은 즉시 스키마 검증.

### `runnerctl` — runner 백그라운드 제어

```bat
scripts\runnerctl.bat start            REM 백그라운드 기동(이미 실행 중이면 거부)
scripts\runnerctl.bat status           REM runner_pid 생존 / last_tick / 실행 중 job
scripts\runnerctl.bat logs             REM runner.log 끝 40줄
scripts\runnerctl.bat logs -f          REM 실시간 추적(-n N 으로 줄 수 조정, Ctrl+C 중단)
scripts\runnerctl.bat logs --boot      REM 마지막 기동 시 설정 요약(설정 로드 OK / [DAILY] / run_until)
scripts\runnerctl.bat stop             REM 우아한 종료
scripts\runnerctl.bat stop --timeout 90
```

`stop` 은 `taskkill /F` 로 바로 죽이지 않는다. `state/stop.flag` 를 써서 runner 가
**스스로** 자식 정리(브라우저 등) 경로로 내려가게 한다 → 고아 프로세스 방지. 진행 중인
job 이 끝난 뒤 종료되며, `--timeout`(기본 60s) 안에 안 내려가면 트리째 강제 종료한다.

### `clean` — 로그·상태 정리

```bat
scripts\clean.bat              REM logs/* + state/runner.json,scheduler.json,stop.flag
scripts\clean.bat --logs-only  REM 로그만
scripts\clean.bat --sessions   REM + jennifer 세션(지우면 다음 실행 때 재로그인)
scripts\clean.bat --dry-run    REM 삭제 대상만 출력
scripts\clean.bat --force      REM runner 실행 중 거부를 무시(권장 안 함)
```

**안전장치**: `runner_pid`(또는 살아있는 자식 job)가 감지되면 삭제를 거부한다. 실행 중
삭제 시 파일 잠금 충돌·상태 꼬임이 생기기 때문. 먼저 `runnerctl stop` 으로 정지할 것.

---

## Job 스크립트 (수동 실행)

runner 없이 개별 job 을 1회 돌릴 때 사용한다. 상세·인자는
[job 작성](docs/03_job_authoring.md) / [운영 가이드](docs/06_operations.md) 참고.

| 스크립트 | 역할 |
|---|---|
| `scripts\runner.bat` | 메인 runner (포그라운드, `Ctrl+C` 종료) — 백그라운드는 `runnerctl` |
| `scripts\server.bat` | KWorks 서버 캡처 업로드 |
| `scripts\capture.bat` | 대시보드 캡처 |
| `scripts\capture-baseline.bat` | 캡처 baseline 갱신 |
| `scripts\zenius.bat` / `scripts\zenius-baseline.bat` | Zenius 수집 / baseline |
| `scripts\daily_service.bat` | DailyService 작업 |
| `scripts\whatsup.bat` | WhatsUp(NMS) 맵별 Items Down 점검 (`--dry-run` 이면 알림·상태 저장 생략) |
| `scripts\jennifer.bat` | Jennifer 수치 수집 |

---

## 설정 파일

| 파일 | git | 용도 |
|---|---|---|
| `.env` | ignore | 거의 안 바뀌는 비밀(토큰, 사이트 자격증명) |
| `config/daily.base.yaml` | ignore | `gen-daily` 템플릿 원본(운영자 풀·근무별 시각/폴더) |
| `config/daily.yaml` | ignore | `gen-daily` 산출물(매일 갱신) |
| `config/settings.yaml` | 추적 | 동작 토글(headless 등, 비밀값 금지) |
| `config/jobs.yaml` | 추적 | runner 스케줄 정의 |

`*.example` 파일을 복사해 실제 파일을 만든다. 자세한 설명은
[운영 가이드 1)](docs/06_operations.md) 참고.
