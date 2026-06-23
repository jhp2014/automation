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

import 시 동작: `.env` 로드 + `config/daily.yaml` 1회 읽기 (있으면). **디렉터리 생성·네트워크 호출은 없다**. daily.yaml 파일이 없거나 파싱/검증에 실패하면 위 KWORKS_* / RUN_UNTIL / SERVER_TIMES 는 모두 빈 값/빈 리스트가 된다 (실패는 경고 로그만).

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

### `get_headless(job_key: str) -> bool`
`config/settings.yaml` 에서 `<job_key>.headless` 를 조회. **폴백 없음**: 파일이
없거나(`FileNotFoundError`) 파싱/스키마가 깨졌거나 해당 job 키가 없으면
(`RuntimeError`) 예외로 죽는다. CLI 로 `--headless` / `--no-headless` 를 명시한
경우에는 호출부가 본 함수를 부르지 않으므로 settings.yaml 없이도 실행된다.

### `get_submit_by_enter(job_key: str) -> bool`
`config/settings.yaml` 에서 `<job_key>.submit_by_enter` 를 조회(server / capture
전용). 폴백 없음: 파일·job 키·필드 중 하나라도 없으면 예외로 죽는다. CLI 로
`--submit` / `--no-submit` 을 명시하면 호출부가 본 함수를 부르지 않는다.

### `get_refresh_targets(job_key: str) -> list[str]`
`config/settings.yaml` 에서 `<job_key>.refresh_targets` 를 조회(capture 전용).
캡처 직전 새로고침할 대상 이름 목록(순서 유지). daily service 먹통 등으로 특정
페이지를 새로고침에서 빼야 할 때 코드 수정 없이 yaml 로 조정한다. 폴백 없음:
파일·job 키·필드 중 하나라도 없으면 예외로 죽는다. 대상 이름이 실제 캡처 대상
(`KEYWORDS`)에 속하는지는 호출부(`jobs.capture`)가 검증한다. 빈 목록(`[]`)이면
새로고침 자체를 생략한다.

우선순위는 항상 **CLI 인자 > settings.yaml**. job 들은 다음 패턴으로 해석한다:

```python
from common import config
# args.headless 는 BooleanOptionalAction(default=None): 미지정이면 settings 를 따른다.
headless = (
    args.headless if args.headless is not None else config.get_headless("zenius")
)
with sync_browser(headless=headless) as (_b, _c, page):
    ...
submit = (
    args.submit if args.submit is not None else config.get_submit_by_enter("server")
)
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

## common.settings

`config/settings.yaml` 로더. `common.config` 가 import 시 사용한다. 외부에서 직접
부를 일은 거의 없고, 보통 `config.get_headless` / `config.get_submit_by_enter` /
`config.get_refresh_targets` 헬퍼로 조회한다.

`settings.yaml` 은 **git 추적, 비밀값 금지**. `.env` / `daily.yaml` 과 달리 동작
토글(headless / submit_by_enter / capture 의 refresh_targets)만 담는다.

### `load_settings(*, force: bool = False) -> SettingsConfig`
settings.yaml 을 1회 로드해 캐시. **폴백 없음**(`daily.py` 가 부재를 `None` 으로
흡수하는 것과 다르다):

- 파일이 없으면 `FileNotFoundError`.
- YAML 파싱 실패 / 최상위가 dict 아님 / 스키마 검증 실패면 `RuntimeError`.

### pydantic 모델 (flat 스키마, `extra="forbid"`)

```python
class JobSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    headless: bool                          # 모든 job 필수
    submit_by_enter: bool | None = None     # server / capture 만 의미
    refresh_targets: list[str] | None = None  # capture 만 의미

class SettingsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    zenius: JobSettings
    daily_service: JobSettings
    jennifer: JobSettings
    capture: JobSettings
    server: JobSettings
```

5개 job 키가 모두 필수다. 하나라도 빠지거나 오탈자 키가 있으면 검증 실패로
죽는다(`defaults` 섹션은 없다).

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

### `sync_browser(*, headless=True, storage_state=None, viewport=None, window_size=None, ignore_https_errors=False)`
크로미움 브라우저/컨텍스트/페이지를 생성하고 with 종료 시 정리하는 컨텍스트 매니저.

**Yields**: `tuple[Browser, BrowserContext, Page]`.

- `window_size=(W, H)` 가 주어지면 launch args 에 `--window-size=W,H` 가 들어가고 viewport 도 동일 크기로.
- `viewport` 와 `window_size` 가 동시에 있으면 `viewport` 가 우선.
- `storage_state` 파일이 존재하지 않거나 JSON 파싱 실패면 세션 없이 새 컨텍스트로 폴백 + 경고 로그.
- `ignore_https_errors=True` 면 `new_context()` 에 같은 옵션을 넘겨 HTTPS 인증서 오류를 무시한다(사내 인증서/SSL inspection/자체서명 환경). 기본 `False` — 기존 호출부는 영향 없음.
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
