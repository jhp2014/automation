# 03. 새 job 작성 가이드

> 기준 시점: 현재 코드와 동기화. 실제 예시는 [jobs/zenius/__main__.py](../jobs/zenius/__main__.py), [jobs/daily_service/__main__.py](../jobs/daily_service/__main__.py), [jobs/server/__main__.py](../jobs/server/__main__.py), [jobs/capture/__main__.py](../jobs/capture/__main__.py) 참고.

## 1) 폴더 / 파일 구조

새 job 이름을 `<name>` 이라 하자.

```
jobs/<name>/
├── __init__.py           # 빈 파일
└── __main__.py           # 진입점 (얇게 유지)
site_selectors/<name>.py  # (필요 시) 사이트별 셀렉터·URL
```

상태 파일/로그/캡처는 코드 안에서 만들지 않고 `common.config` 의 경로 상수에 합성한다 — 이 문서 4번 항목 참고.

## 2) `__main__.py` 표준 골격

```python
"""<name> job 한 줄 설명 (규약 v1.1).
"""

from __future__ import annotations

# 프로젝트 루트를 sys.path에 추가한다.
# python -m jobs.<name> 로 실행하든, 이 파일을 직접 실행하든
# common / site_selectors 패키지를 항상 import할 수 있게 하기 위함이다.
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]  # automation/
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import argparse
import os
from datetime import datetime

from common import config
from common.browser import sync_browser
from common.logging import get_logger
from common.notify import send_pushover_emergency
# from common.kworks import KworksClient            # KWorks 쓰는 경우만
# from site_selectors import <name> as S            # 필요 시
# from common.config import get_telegram_target    # Telegram 쓰는 경우만

LOG = get_logger("jobs.<name>", "<name>.log")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="<name> job")
    parser.add_argument("--dry-run", action="store_true",
                        help="로그인까지만 수행하고 알림/외부 작업 생략")
    # ... job별 인자
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    dry_run = bool(args.dry_run)

    config.ensure_dirs()

    stage = "init"
    LOG.info("[START] <name> at %s dry_run=%s",
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"), dry_run)

    try:
        stage = "credentials"
        LOG.info("[STAGE] %s", stage)
        # user_id = os.getenv("<NAME>_USER_ID", "")
        # ...

        with sync_browser(headless=True) as (_browser, context, page):
            stage = "login"
            LOG.info("[STAGE] %s", stage)
            # ... 로그인/세션 확인

            if dry_run:
                LOG.info("[DRY-RUN] 로그인까지 확인 완료, 이후 단계 생략")
                return 0

            stage = "main_work"
            LOG.info("[STAGE] %s", stage)
            # ... 본 작업

        return 0

    except Exception as e:
        LOG.exception("[FAIL] stage=%s err=%r", stage, e)
        if dry_run:
            LOG.info("[DRY-RUN] 실패해도 알림은 생략")
            return 1
        send_pushover_emergency(
            title="[<Name>] 실패",
            message=f"stage={stage} | err={e}",
        )
        return 1

    finally:
        LOG.info("[END] <name> run finished")


if __name__ == "__main__":
    raise SystemExit(main())
```

## 3) 부트스트랩 위치 규칙

`from __future__ import annotations` 는 Python 문법상 모듈 docstring 직후·다른 import 보다 먼저여야 한다. 부트스트랩은 그 다음에 둔다. 순서:

1. 모듈 docstring
2. `from __future__ import annotations`
3. sys.path 부트스트랩 (`import sys` / `from pathlib import Path` / `_PROJECT_ROOT = ...`)
4. 나머지 import (`argparse`, `common.*`, …)

## 4) 경로 규칙 (cwd 사용 금지)

| 산출물 | 위치 |
|--------|------|
| 상태 파일 (`*.json`) | `config.STATE_DIR / "<job>_<용도>.json"` |
| 로그 | `get_logger("jobs.<name>", "<name>.log")` — 파일명만 넘기면 `config.LOG_DIR` 기준 |
| 캡처/baseline 등 산출물 | `config.BASE_DIR / "captures" / "<job>" / ...` |
| `.env` | `common.config` 가 `BASE_DIR/.env` 에서 자동 로딩 — job은 신경 안 씀 |

상대경로 사용 금지. `Path(".")` 또는 `os.getcwd()` 기반 합성 금지.

## 4-A) 동작 토글은 `settings.yaml` 에서 읽기

`headless`, `submit_by_enter` 같이 거의 안 바뀌는 동작 스위치는 코드 상수가 아니라
`config/settings.yaml` 에서 `config.get_headless("<job>")` / `config.get_submit_by_enter("<job>")`
로 조회한다. 우선순위는 `CLI 인자 > settings.yaml > 코드 기본값`.

## 5) `--dry-run` 규약

- 모든 job은 `--dry-run` 플래그를 받아야 한다.
- dry-run 에서는 **로그인/세션 확인까지만** 수행하고 그 이후 단계(데이터 스캔/업로드/외부 호출)는 생략.
- dry-run 에서는 **알림 전송 금지**: Pushover, Telegram(heartbeat/report) 모두 보내지 않는다.
- dry-run 도 "어디까지 도달했는지" 는 로그로 남긴다. 표준 메시지: `[DRY-RUN] 로그인까지 확인 완료, 이후 단계 생략`.

## 6) `stage` 로깅 규약

- 주요 단계마다 `stage = "<단계명>"` 으로 갱신 → `LOG.info("[STAGE] %s", stage)` 로 진입 로그.
- 예외 핸들러에서 `LOG.exception("[FAIL] stage=%s err=%r", stage, e)` 형태로 마지막 stage 를 남기고, 알림 메시지(`title`/`message`)에도 `stage=...` 를 포함.
- stage 명은 단계 의미를 살리되 짧게: `init`, `credentials`, `kworks_login`, `open_task_detail`, `upload_files`, `submit` 등.

## 7) `.env` 키 추가법

job 이 새 토큰/자격증명을 쓰면:

1. `.env.example` 에 키 이름만 추가하고 한 줄 주석으로 용도 명시.
2. 실제 값은 `.env` 에 (git 추적 안 됨).
3. Telegram 추가 시 코드 수정 없이 `.env` 두 줄만:
   ```
   TELEGRAM_BOT__<JOBKEY>=<bot_token>
   TELEGRAM_CHAT__<JOBKEY>__<PURPOSE>=<chat_id>
   ```
   사용: `get_telegram_target("<jobkey>", "<purpose>")`.

## 8) 셀렉터 분리 규칙

| 셀렉터 종류 | 위치 |
|-------------|------|
| KWorks (login/EMS/form/upload …) | **건드리지 말 것** — 이미 `common/kworks/selectors.py` 에 모여 있음 |
| 기타 사이트 (zenius / daily_service / 신규) | `site_selectors/<job>.py` |

규칙:
- 상수명은 `SEL_` 접두사.
- 셀렉터 문자열은 코드에 인라인 금지. 본문에서 `from site_selectors import <name> as S` / `from common.kworks import selectors as KS` 로 import.
- 추측 금지 — 실제 운영 사이트의 DOM에서 확인된 문자열만 쓴다.

## 9) 검증 체크리스트

새 job 을 작성하고 PR 올리기 전:

- [ ] `python -m jobs.<name> --help` 가 정상 출력
- [ ] `python -m jobs.<name> --dry-run` 가 로그인까지만 도달 후 종료, 알림 미전송
- [ ] 모든 공개 함수에 타입 힌트 + docstring (Args/Returns/Raises)
- [ ] 상태/로그/캡처 경로가 모두 `config.*` 기준
- [ ] `stage` 로깅이 단계별로 들어가 있음
- [ ] 실패 시 알림 메시지에 마지막 stage 포함
- [ ] 셀렉터가 코드 본문에 인라인되어 있지 않음
- [ ] `.env.example` 가 새 키를 반영

다음 문서: [04. 규약 v1.1](04_conventions.md).
