# 02. common 공개 API 레퍼런스

> 기준 시점: 현재 코드와 동기화. 시그니처는 [common/](../common) 의 실제 파일에서 가져왔다.

## common.config

### 모듈 상수

| 이름 | 타입 | 소스 | 설명 |
|------|------|------|------|
| `BASE_DIR` | `pathlib.Path` | — | `automation/` 절대경로. |
| `LOG_DIR` | `pathlib.Path` | — | `BASE_DIR / "logs"`. |
| `STATE_DIR` | `pathlib.Path` | — | `BASE_DIR / "state"`. |
| `PUSHOVER_TOKEN` | `str` | `.env` | `PUSHOVER_TOKEN`. 없으면 `""`. |
| `PUSHOVER_USER` | `str` | `.env` | `PUSHOVER_USER`. 없으면 `""`. |
| `KWORKS_USER_ID` | `str` | `daily.yaml` | `kworks.user_id`. 없으면 `""`. |
| `KWORKS_USER_PW` | `str` | `daily.yaml` | `kworks.user_pw`. 없으면 `""`. |
| `KWORKS_TARGET_TITLE` | `str` | `daily.yaml` | `kworks.target_title`. 없으면 `""`. |
| `RUN_UNTIL` | `str` | `daily.yaml` | `run_until`. `"YYYY-MM-DD HH:MM"` 또는 `""` (무기한). |
| `SERVER_TIMES` | `list[DailyServerTime]` | `daily.yaml` | `server_times`. 없으면 `[]`. |

import 시 부작용 없음(디렉터리 생성·네트워크 X). daily.yaml 파일이 없거나 파싱/검증에 실패하면 위 KWORKS_* / RUN_UNTIL / SERVER_TIMES 는 모두 빈 값/빈 리스트가 된다 (실패는 경고 로그만).

### `ensure_dirs() -> None`
런타임 디렉터리(`LOG_DIR`, `STATE_DIR`)를 생성한다. 멱등. import 시 자동 호출되지 않는다.

### `class TelegramTarget(bot_token: str, chat_id: str)` *(frozen dataclass)*
텔레그램 대상 묶음.

### `get_telegram_target(job_key: str, purpose: str) -> TelegramTarget`
환경변수에서 봇/채팅을 동적 조회.

- 키 규칙: `TELEGRAM_BOT__<JOBKEY>` + `TELEGRAM_CHAT__<JOBKEY>__<PURPOSE>`
- `job_key`, `purpose` 는 대소문자 무관 (내부에서 UPPER 로 정규화).
- **Raises**: `KeyError` — 누락된 env 키 이름을 메시지에 명시.

```python
from common.config import get_telegram_target
hb = get_telegram_target("zenius", "heartbeat")   # TelegramTarget(...)
```

---

## common.daily

`config/daily.yaml` 로더. `common.config` 가 import 시 사용한다. 외부에서 직접 부를 일은 거의 없지만 노출되어 있다.

### `load_daily(*, force: bool = False) -> DailyConfig | None`
daily.yaml 을 1회 로드해 캐시. 파일이 없으면 `None`. 파싱/스키마 실패도 `None` + 경고 로그(파일이 운영자 수정 대상이라 거친 실패 대신 흡수).

### pydantic 모델

```python
class DailyKworks(BaseModel):
    user_id: str = ""
    user_pw: str = ""
    target_title: str = ""

class DailyServerTime(BaseModel):
    at: str               # "YYYY-MM-DD HH:MM"
    args: list[str] = []

class DailyConfig(BaseModel):
    run_until: str = ""   # "YYYY-MM-DD HH:MM" 또는 ""
    kworks: DailyKworks
    server_times: list[DailyServerTime] = []
```

---

## common.logging

### `get_logger(name: str, log_file: str | Path | None = None, level: str = "INFO") -> logging.Logger`
콘솔 + (옵션) 5MB×10 로테이팅 파일 핸들러를 부착한 로거를 반환.

- 같은 `name` 으로 재호출 시 핸들러를 중복 추가하지 않는다.
- `log_file` 이 상대경로면 `config.LOG_DIR` 기준으로 해석. 절대경로는 그대로.
- `propagate=False`, 포맷 `%(asctime)s | %(levelname)s | %(message)s` (`%Y-%m-%d %H:%M:%S`).

```python
log = get_logger("jobs.zenius", "zenius.log")
log.info("[STAGE] open_ems")
```

---

## common.notify

모든 함수의 공통 정책: **전송 실패는 예외를 던지지 않고 모듈 로거에 경고만 남기고 swallow**.

### `send_pushover_emergency(title: str, message: str) -> None`
Pushover priority=2 (retry=30, expire=900). `PUSHOVER_TOKEN` 또는 `PUSHOVER_USER` 가 비어 있으면 전송하지 않고 경고만.

### `send_telegram_message(target: TelegramTarget, text: str) -> None`
텍스트 메시지 전송 (timeout=10s).

### `send_telegram_photo(target: TelegramTarget, caption: str, image_path: str | Path) -> None`
사진 + 캡션 전송 (timeout=25s). 파일 열기 실패도 경고만 남기고 조용히 반환.

### `send_heartbeat(target: TelegramTarget, source: str) -> None`
형식: `[HB] <source> running - YYYY-MM-DD HH:MM:SS`.

```python
from common.notify import send_pushover_emergency, send_heartbeat
send_pushover_emergency("[Zenius] 로그인 실패", "stage=ensure_logged_in | err=...")
send_heartbeat(target_heartbeat, source="zenius")
```

---

## common.browser

### `sync_browser(*, headless=True, storage_state=None, viewport=None, window_size=None)`
크로미움 브라우저/컨텍스트/페이지를 생성하고 with 종료 시 정리하는 컨텍스트 매니저.

**Yields**: `tuple[Browser, BrowserContext, Page]`.

- `window_size=(W, H)` 가 주어지면 launch args 에 `--window-size=W,H` 가 들어가고 viewport 도 동일 크기로.
- `viewport` 와 `window_size` 가 동시에 있으면 `viewport` 가 우선.
- `storage_state` 파일이 존재하지 않거나 JSON 파싱 실패면 세션 없이 새 컨텍스트로 폴백 + 경고 로그.
- teardown 의 close 예외는 swallow. launch 실패는 호출부로 전파.

```python
from common.browser import sync_browser
with sync_browser(headless=True, window_size=(1920, 900)) as (br, ctx, page):
    page.goto("https://example.com")
```

### `save_storage_state(context: BrowserContext, path: str | Path) -> None`
현재 컨텍스트 세션을 지정 경로에 저장. 상위 폴더 자동 생성.

---

## common.kworks

### `from common.kworks import KworksClient`

#### `KworksClient(page, *, logger, timeout_ms=30000, settle_ms=600, banner_max_rounds=6, new_input_timeout_ms=2500, per_file_upload_timeout_ms=30000, per_file_max_retries=3, retry_sleep_ms=600, retry_backoff_ms=400)`

Playwright sync `Page` 를 주입받아 KWorks 흐름을 캡슐화한다.

- `page` 는 외부(`common.browser.sync_browser` 등)에서 만들어 주입. lifecycle 은 호출부 책임.
- `logger` 는 `common.logging.get_logger` 로 만든 job 로거를 그대로 전달.

#### `login(url: str, user_id: str, user_pw: str) -> None`
URL 로 이동 후 로그인 폼이 있으면 채워 제출. 이미 로그인 상태면 통과. `main.act` 진입까지 대기.

#### `open_task_detail(target_title: str) -> Locator`
전체 업무 진입 → 배너 닫기 → 좌측 필터 보정 → 토글 펼치기 → 제목 검색 → "자세히 보기" 클릭. 활성 댓글 폼의 Locator 를 반환.

#### `type_comment(form: Locator, text: str) -> None`
폼의 contenteditable 영역에 텍스트 입력. `text` 가 빈 문자열이면 no-op.

#### `upload_files(form: Locator, paths: list[Path], *, max_retries: int | None = None) -> None`
파일들을 순서대로 업로드. 각 파일은 `file_nm` 카운트가 1 증가하면 성공. 파일별 재시도 기본 3회(`max_retries` 로 호출 단위 오버라이드 가능).

#### `submit(form: Locator) -> None`
댓글 영역에 Enter 키를 눌러 등록.

```python
from common.browser import sync_browser
from common.kworks import KworksClient
from common.logging import get_logger

log = get_logger("jobs.server", "server.log")
with sync_browser(headless=True) as (_b, _c, page):
    client = KworksClient(page, logger=log)
    client.login("https://kworks.kyowon.co.kr/", uid, pw)
    form = client.open_task_detail("2026.05.29(금) 야간 OP관제 일일보고")
    client.type_comment(form, "야간 관제 결과")
    client.upload_files(form, [Path("img1.png"), Path("img2.png")])
    client.submit(form)
```

다음 문서: [03. job 작성 가이드](03_job_authoring.md).
