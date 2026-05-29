# 00. 개요

> 기준 시점: 현재까지 구현된 범위 (common, common.kworks, site_selectors, jobs/zenius·daily_service·server·capture).
> runner / config/jobs.yaml 은 아직 미구현 — 본 문서에서는 "예정"으로만 언급한다.

## 한 문장 요약

운영 중인 자동화 스크립트들을 하나의 monorepo로 재작성한 프로젝트.
**구조는 통합하고, 실행은 분리한다.** 한 개의 `.venv`를 공유하지만 각 job은 독립 프로세스로 실행된다.

## 디렉터리 트리 (현재 구현분)

```
automation/
├── .venv/                       # 단일 가상환경 (git ignore)
├── pyproject.toml               # 모든 deps 정의 + 패키지 명시
├── .env / .env.example          # 비밀값(.env) / 키 이름만(.env.example)
├── common/                      # 모든 job이 공유하는 토대
│   ├── config.py                # .env 로딩, 경로 상수, TelegramTarget 동적 로더
│   ├── logging.py               # get_logger (콘솔 + 5MB×10 로테이팅)
│   ├── notify.py                # Pushover 긴급 / Telegram 메시지·사진·heartbeat
│   ├── browser.py               # sync_browser 컨텍스트 매니저 + save_storage_state
│   └── kworks/                  # KWorks 전용 공통화
│       ├── client.py            # KworksClient (login/open/comment/upload/submit)
│       └── selectors.py         # KWorks DOM 셀렉터 상수
├── site_selectors/              # 사이트별(KWorks 외) 셀렉터
│   ├── zenius.py
│   └── daily_service.py
├── jobs/                        # 실행 진입점 — 각 폴더는 python -m jobs.<name>
│   ├── zenius/__main__.py
│   ├── daily_service/__main__.py
│   ├── server/__main__.py
│   └── capture/__main__.py + window_utils.py
├── docs/                        # 본 문서들
├── logs/                        # 런타임 생성 (config.LOG_DIR)
├── state/                       # 런타임 생성 (config.STATE_DIR)
└── captures/                    # 런타임 생성 (capture job 산출물)
    └── capture/
```

향후 추가 예정: `runner/` (jobs.yaml 기반 스케줄링), `config/jobs.yaml`.

## 데이터 흐름

```
              ┌────────────────────────────────────────┐
              │ runner (예정: jobs.yaml에 따라 호출)    │
              └─────────────────┬──────────────────────┘
                                │ subprocess 호출
                                ▼
              ┌────────────────────────────────────────┐
              │ python -m jobs.<name>                  │
              │  - __main__.py 가 sys.path 부트스트랩  │
              │  - argparse (필수: --dry-run 지원)     │
              │  - stage 변수로 진입 로그              │
              └─────────────────┬──────────────────────┘
                                │ import (단방향)
                                ▼
              ┌────────────────────────────────────────┐
              │ common.config / logging / notify /     │
              │ browser / kworks  +  site_selectors    │
              └────────────────────────────────────────┘
```

규칙:
- runner → job 호출은 **subprocess만** (import 금지).
- job → common 은 **import OK**, job ↔ job 은 **import 금지**.
- common 의 공개 시그니처는 변경하지 않는다 — 확장은 옵션 인자 추가로.

## 5분 안에 핵심 정리

1. 새 job은 `jobs/<name>/__main__.py` 에 추가. 부트스트랩으로 `_PROJECT_ROOT`를 sys.path에 끼우고 `common.*` 만 의존한다.
2. 모든 job은 `--dry-run` 을 받아야 한다. dry-run에서는 로그인/세션 확인까지만 하고 알림·외부 작업은 생략.
3. `stage` 변수를 단계마다 갱신하고 진입 로그를 남긴다. 실패 시 알림 메시지에 마지막 stage를 포함.
4. 비밀값(토큰·비번)은 모두 `.env`. Telegram 봇은 job마다 다르며 `TELEGRAM_BOT__<JOBKEY>` / `TELEGRAM_CHAT__<JOBKEY>__<PURPOSE>` 명명 규칙으로 동적 로딩된다.
5. 산출물 경로 규약: 상태 파일은 `config.STATE_DIR`, 로그는 `config.LOG_DIR`, 캡처류는 `config.BASE_DIR/captures/<job>/`. cwd 기준 상대경로 사용 금지.

다음 문서: [01. 아키텍처](01_architecture.md) — 패키지별 책임과 의존 방향.
