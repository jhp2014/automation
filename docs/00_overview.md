# 00. 개요

> 기준 시점: 현재 코드 일치. common (config·daily·logging·notify·browser·kworks), site_selectors, jobs/{zenius, daily_service, jennifer, server, capture}, runner (구현 완료), config/jobs.yaml + config/daily.yaml 까지 포함.

## 한 문장 요약

운영 중인 자동화 스크립트들을 하나의 monorepo로 재작성한 프로젝트.
**구조는 통합하고, 실행은 분리한다.** 한 개의 `.venv`를 공유하지만 각 job은 독립 프로세스로 실행된다.

## 디렉터리 트리

```
automation/
├── .venv/                          # 단일 가상환경 (git ignore)
├── pyproject.toml                  # 모든 deps 정의 + 패키지 명시
├── .env / .env.example             # 거의 안 바뀌는 비밀 (git ignore)
├── common/                         # 모든 job이 공유하는 토대
│   ├── config.py                   # .env + daily.yaml 로딩, 경로 상수, TelegramTarget
│   ├── daily.py                    # config/daily.yaml 로더 (pydantic 스키마)
│   ├── logging.py                  # get_logger (콘솔 + 5MB×10 로테이팅)
│   ├── notify.py                   # Pushover 긴급 / Telegram 메시지·사진·heartbeat
│   ├── browser.py                  # sync_browser 컨텍스트 매니저 + save_storage_state
│   └── kworks/                     # KWorks 전용 공통화
│       ├── client.py               # KworksClient (login/open/comment/upload/submit)
│       └── selectors.py            # KWorks DOM 셀렉터 상수
├── site_selectors/                 # 사이트별(KWorks 외) 셀렉터
│   ├── zenius.py
│   ├── daily_service.py
│   └── jennifer.py
├── jobs/                           # 실행 진입점 — 각 폴더는 python -m jobs.<name>
│   ├── zenius/__main__.py
│   ├── daily_service/__main__.py
│   ├── jennifer/__main__.py
│   ├── server/__main__.py
│   └── capture/__main__.py + window_utils.py
├── runner/                         # python -m runner — YAML 기반 스케줄러
│   ├── __main__.py                 # 메인 루프 (얇음)
│   ├── config.py                   # jobs.yaml + daily.yaml 로딩, pydantic 검증
│   ├── state.py                    # runner.json + scheduler.json 분리 관리
│   ├── scheduler.py                # interval / hourly_jitter / one_time_list 판정
│   ├── executor.py                 # sys.executable -m subprocess
│   └── heartbeat.py                # Supabase 데드맨 스위치
├── config/                         # 구조 + 매일 갱신값
│   ├── jobs.yaml                   # 구조 (모드/모듈/주기/timeout). 거의 안 바뀜
│   ├── daily.yaml                  # 매일 갱신 (run_until, KWorks 자격증명, server times). git ignore
│   ├── daily.yaml.example          # daily.yaml 템플릿
│   └── jennifer_sites.json         # Jennifer 사이트 목록 (id 까지만, pw 는 .env)
├── scripts/                        # Windows bat 단축실행
│   ├── runner.bat
│   ├── <job>.bat / <job>-dry-run.bat
│   ├── capture-baseline.bat
│   └── dry-run-all.bat
├── docs/                           # 본 문서들
├── logs/                           # 런타임 생성 (config.LOG_DIR)
├── state/                          # 런타임 생성 (config.STATE_DIR)
└── captures/                       # 런타임 생성 (capture job 산출물)
    └── capture/
```

## 두 개의 비밀 소스 (둘 다 git ignore)

| 파일 | 빈도 | 들어갈 값 |
|------|------|----------|
| `.env` | 거의 안 바뀜 | Pushover/Telegram/Supabase 토큰, Zenius·DailyService·Jennifer 자격증명 |
| `config/daily.yaml` | 매일 갱신 | `run_until`, KWorks 자격증명·target_title, `jobs.server.times` |

## 데이터 흐름

```
┌───────────────────────────────────────────────────────────────────┐
│ runner (python -m runner)                                          │
│  - config/jobs.yaml + config/daily.yaml 로드 + pydantic 검증       │
│  - state/runner.json + state/scheduler.json 관리                   │
│  - Supabase 데드맨 스위치 (heartbeat upsert + 3회 실패 → Pushover) │
└─────────────────┬─────────────────────────────────────────────────┘
                  │ subprocess: [sys.executable, "-m", <module>, *args]
                  ▼
┌───────────────────────────────────────────────────────────────────┐
│ python -m jobs.<name>                                              │
│  - __main__.py 가 sys.path 부트스트랩                              │
│  - argparse (--dry-run 필수)                                       │
│  - stage 변수로 진입 로그 + 실패 시 알림에 마지막 stage 포함        │
└─────────────────┬─────────────────────────────────────────────────┘
                  │ import (단방향)
                  ▼
┌───────────────────────────────────────────────────────────────────┐
│ common.{config, daily, logging, notify, browser, kworks}           │
│ site_selectors.{zenius, daily_service, jennifer}                   │
└─────────────────┬─────────────────────────────────────────────────┘
                  │ 파일 I/O (import 시점 또는 호출 시)
                  ▼
        .env (정적 비밀)  +  config/daily.yaml (매일 갱신)
```

규칙:
- runner → job 호출은 **subprocess만** (import 금지).
- job → common 은 **import OK**, job ↔ job 은 **import 금지**.
- common 의 공개 시그니처는 변경하지 않는다 — 확장은 옵션 인자 추가로.

## 5분 안에 핵심 정리

1. 새 job은 `jobs/<name>/__main__.py` 에 추가. 부트스트랩으로 `_PROJECT_ROOT`를 sys.path에 끼우고 `common.*` 만 의존한다.
2. 모든 job은 `--dry-run` 을 받아야 한다. dry-run에서는 로그인/세션 확인까지만 하고 알림·외부 작업은 생략.
3. `stage` 변수를 단계마다 갱신하고 진입 로그를 남긴다. 실패 시 알림 메시지에 마지막 stage를 포함.
4. 비밀값(토큰·비번)은 `.env`. 매일 갱신값(run_until, KWorks 자격증명·target_title, server times)은 `config/daily.yaml`. Telegram 봇은 job마다 다르며 `TELEGRAM_BOT__<JOBKEY>` / `TELEGRAM_CHAT__<JOBKEY>__<PURPOSE>` 명명 규칙으로 동적 로딩된다.
5. 산출물 경로 규약: 상태 파일은 `config.STATE_DIR`, 로그는 `config.LOG_DIR`, 캡처류는 `config.BASE_DIR/captures/<job>/`. cwd 기준 상대경로 사용 금지.

다음 문서: [01. 아키텍처](01_architecture.md) — 패키지별 책임과 의존 방향.
설치는: [08. 설치](08_install.md) — 새 PC 세팅 절차.
