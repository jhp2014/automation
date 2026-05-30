"""Jennifer 통합 점검 job (규약 v1.1, 동기 포팅).

원본 jennifer/ 묶음(main/login_strategies/popup_manager/data_extractor/
alert_manager/config_loader/sites_config)을 한 job 으로 통합 + async→sync
포팅했다. 동작 정책은 보존한다 — Fail-Fast: 어느 사이트라도 시스템 오류 또는
fatal 임계 초과면 즉시 예외, Pushover 1회 후 종료(이후 사이트 중단).

stage 명은 ``[<사이트명>] <단계>`` 형식으로 표시해 실패 지점을 식별 가능하게 한다.

사이트 목록: config/jennifer_sites.json (id 까지만 평문, pw 는 .env).
비밀번호 env 키 규칙: ``JENNIFER_PW__<NAME_UPPER>`` — 사이트명 공백/하이픈을
언더스코어로 치환 후 대문자화. 예: ``Jennifer_cloud`` → ``JENNIFER_PW__JENNIFER_CLOUD``.
"""

from __future__ import annotations

# 프로젝트 루트를 sys.path에 추가한다.
# python -m jobs.jennifer 로 실행하든, 이 파일을 직접 실행하든
# common / site_selectors 패키지를 항상 import할 수 있게 하기 위함이다.
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]  # automation/
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import argparse
import json
import os
import re
from datetime import datetime
from typing import Dict, List, Optional

from playwright.sync_api import (
    BrowserContext,
    Error as PWError,
    Page,
    TimeoutError as PWTimeoutError,
)

from common import config
from common.browser import save_storage_state, sync_browser
from common.logging import get_logger
from common.notify import send_pushover_emergency
from site_selectors import jennifer as J


# ---------------------------------------------------------------------------
# 설정 (비밀 아님)
# ---------------------------------------------------------------------------

# 좌표 더블클릭이 헤드리스에서 불안정하면 config/settings.yaml 의 jobs.jennifer.headless
# 를 false 로 바꾼다(코드 흐름은 동일).

# fatal 임계값(원본 그대로). 초과 시 즉시 Fail-Fast.
FATAL_THRESHOLD = 50

# 차트 데이터 렌더 대기(원본 5초).
CHART_RENDER_WAIT_MS = 5000

# 새로고침 후 데이터 갱신 대기(원본 1초).
REFRESH_WAIT_MS = 1000

# 팝업 내 지표(fatal/total) visible 대기. 원본은 timeout 미지정(기본 30s)이라
# 드라이버/팝업이 끊긴 경우를 늦게 감지했다. 명시 타임아웃으로 빨리 끊는다.
METRIC_VISIBLE_TIMEOUT_MS = 10000

# 새로고침 버튼 visible 대기.
REFRESH_VISIBLE_TIMEOUT_MS = 10000

# 세션 유효성 확인 타임아웃(원본 5초).
SESSION_CHECK_TIMEOUT_MS = 5000

# 로그인 후 대시보드 진입 타임아웃(원본 30초).
LOGIN_WAIT_TIMEOUT_MS = 30000

# Redpen 로그인 후 깨진 HTTP 리다이렉트/Chrome error를 빠르게 감지할 대기.
REDPEN_REDIRECT_CHECK_MS = 3000

# 페이지 진입(goto) 타임아웃(원본 60초).
GOTO_TIMEOUT_MS = 60000

# 캔버스 visible 대기(원본 30초).
CANVAS_VISIBLE_TIMEOUT_MS = 30000

# 대시보드 더블클릭 후 팝업 생성/load 대기. 실패 시 빠르게 다음 주기로 넘긴다.
POPUP_OPEN_ATTEMPTS = 2
POPUP_WAIT_TIMEOUT_MS = 10000
POPUP_LOAD_TIMEOUT_MS = 10000

# 사이트 설정 파일(비밀번호 제외).
SITES_CONFIG_PATH = config.BASE_DIR / "config" / "jennifer_sites.json"

# 세션 저장 폴더(원본의 sessions/ 와 동등, 규약 9에 따라 STATE_DIR 하위로).
SESSION_DIR = config.STATE_DIR / "jennifer"

LOG = get_logger("jobs.jennifer", "jennifer.log")


# ---------------------------------------------------------------------------
# 사이트 / 자격증명 로딩
# ---------------------------------------------------------------------------

def _load_sites() -> List[Dict[str, str]]:
    """``config/jennifer_sites.json`` 에서 사이트 목록을 읽는다.

    Returns:
        각 항목은 ``{name, url, id, login_type}`` 키를 가진 dict 리스트.
        선택 키 ``ignore_https_errors`` (bool) 가 있으면 해당 사이트는 HTTPS
        인증서 오류를 무시하고 접속한다(기본 False).

    Raises:
        FileNotFoundError: 설정 파일이 없는 경우.
        ValueError: 파일이 JSON 배열이 아니거나 필수 키가 빠진 경우, 또는
            ``ignore_https_errors`` 가 bool 이 아닌 경우.
    """
    if not SITES_CONFIG_PATH.exists():
        raise FileNotFoundError(f"사이트 설정 없음: {SITES_CONFIG_PATH}")

    data = json.loads(SITES_CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(
            f"사이트 설정은 JSON 배열이어야 합니다: {SITES_CONFIG_PATH}"
        )

    required = {"name", "url", "id", "login_type"}
    for i, site in enumerate(data):
        if not isinstance(site, dict):
            raise ValueError(f"사이트[{i}] 형식 오류: dict 아님")
        missing = required - set(site.keys())
        if missing:
            raise ValueError(f"사이트[{i}] 키 누락: {missing}")
        if site["login_type"] not in ("standard", "new"):
            raise ValueError(
                f"사이트[{i}] login_type 은 'standard' 또는 'new' 만 허용: "
                f"{site['login_type']}"
            )
        if "ignore_https_errors" in site and not isinstance(
            site["ignore_https_errors"], bool
        ):
            raise ValueError(
                f"사이트[{i}] ignore_https_errors 는 true/false 만 허용: "
                f"{site['ignore_https_errors']}"
            )
    return data


def _env_key_for_password(site_name: str) -> str:
    """사이트명에서 비밀번호 env 키를 만든다.

    규칙: 공백·하이픈을 언더스코어로 치환한 뒤 대문자화하여
    ``JENNIFER_PW__<NAME_UPPER>`` 로 합성한다.

    Args:
        site_name: 사이트명(예: ``"Jennifer_cloud"``).

    Returns:
        예: ``"JENNIFER_PW__JENNIFER_CLOUD"``.
    """
    norm = re.sub(r"[\s\-]+", "_", site_name).upper()
    return f"JENNIFER_PW__{norm}"


def _read_password(site_name: str) -> str:
    """사이트별 비밀번호를 환경변수에서 읽는다.

    Raises:
        RuntimeError: 환경변수가 없거나 비어 있는 경우(어떤 키가 비었는지 명시).
    """
    key = _env_key_for_password(site_name)
    pw = os.getenv(key, "").strip()
    if not pw:
        raise RuntimeError(f"비밀번호 누락(env 키): {key}")
    return pw


def _session_path(site_name: str) -> Path:
    """사이트별 storage_state 파일 절대 경로."""
    return SESSION_DIR / f"{site_name}_session.json"


# ---------------------------------------------------------------------------
# 로그인 / 세션
# ---------------------------------------------------------------------------

def _do_login(
    context: BrowserContext,
    page: Page,
    login_type: str,
    site_id: str,
    site_pw: str,
    dashboard_url: str,
) -> Page:
    """login_type 에 맞춰 로그인 폼을 채워 제출한다.

    Args:
        context: 현재 BrowserContext.
        page: 로그인 페이지가 떠 있는 Page.
        login_type: ``"standard"`` 또는 ``"new"``.
        site_id: 로그인 id.
        site_pw: 로그인 pw.
        dashboard_url: 로그인 성공 후 진입해야 할 HTTPS 대시보드 URL.

    Returns:
        대시보드 진입이 끝난 Page. 로그인 후 HTTP 리다이렉트가 깨진 경우 새 Page를
        만들어 반환할 수 있다.

    Raises:
        ValueError: 알 수 없는 login_type.
        playwright.sync_api.TimeoutError: 로그인 후 대시보드 대기 실패.
    """
    btn = J.LOGIN_BTN_BY_TYPE.get(login_type)
    wait_sel = J.WAIT_SELECTOR_BY_TYPE.get(login_type)
    if not btn or not wait_sel:
        raise ValueError(f"알 수 없는 login_type: {login_type}")

    page.fill(J.SEL_INPUT_ID, site_id)
    page.fill(J.SEL_INPUT_PW, site_pw)
    page.click(btn)

    if login_type == "new":
        for _ in range(max(1, REDPEN_REDIRECT_CHECK_MS // 250)):
            current_url = page.url
            if (
                current_url.startswith("chrome-error://")
                or current_url.startswith("http://")
                or current_url.startswith(dashboard_url)
            ):
                break
            page.wait_for_timeout(250)

        current_url = page.url
        if current_url.startswith("chrome-error://") or current_url.startswith("http://"):
            LOG.warning(
                "Redpen 로그인 후 깨진 리다이렉트 감지 -> HTTPS 대시보드 직접 진입: "
                "current_url=%s dashboard_url=%s",
                current_url,
                dashboard_url,
            )
            page.goto(dashboard_url, timeout=GOTO_TIMEOUT_MS)

    try:
        page.wait_for_selector(wait_sel, timeout=LOGIN_WAIT_TIMEOUT_MS)
        return page
    except PWTimeoutError:
        if login_type != "new":
            raise

        LOG.warning(
            "로그인 후 대시보드 대기 실패 -> 새 페이지로 HTTPS 대시보드 직접 진입: "
            "current_url=%s dashboard_url=%s",
            page.url,
            dashboard_url,
        )
        old_page = page
        page = context.new_page()
        page.goto(dashboard_url, timeout=GOTO_TIMEOUT_MS)
        try:
            old_page.close()
        except Exception as close_err:
            LOG.warning("로그인 후 실패한 페이지 close 무시: err=%r", close_err)
        page.wait_for_selector(wait_sel, timeout=LOGIN_WAIT_TIMEOUT_MS)
        return page


def _ensure_login(
    context: BrowserContext,
    page: Page,
    site: Dict[str, str],
) -> Page:
    """대시보드 진입을 보장한다. 세션이 살아있으면 그대로, 아니면 재로그인.

    재로그인 후 ``save_storage_state`` 로 세션 파일을 갱신한다.

    Args:
        context: 현재 BrowserContext.
        page: 현재 Page(아직 ``goto`` 전이어야 함).
        site: 사이트 dict (``name/url/id/login_type``). 선택 키 ``login_url`` 이
            있으면 대시보드 진입 실패 시 새 Page로 로그인 페이지에 직접 폴백한다.

    Returns:
        대시보드 진입이 끝난 Page. fallback 중 새 Page를 만들 수 있으므로 호출부는
        반환값을 이후 단계에 사용해야 한다.

    Raises:
        playwright.sync_api.TimeoutError: 페이지 진입(goto) 자체가 실패한 경우.
        RuntimeError: 비밀번호 env 누락.
    """
    name = site["name"]
    url = site["url"]
    login_type = site["login_type"]
    login_url = site.get("login_url")
    wait_sel = J.WAIT_SELECTOR_BY_TYPE[login_type]

    try:
        page.goto(url, timeout=GOTO_TIMEOUT_MS)
    except PWError as e:
        if not login_url:
            raise
        LOG.warning(
            "[%s] 대시보드 진입 실패 -> 새 페이지로 로그인 URL 직접 진입: url=%s login_url=%s err=%r",
            name,
            url,
            login_url,
            e,
        )
        old_page = page
        page = context.new_page()
        page.goto(login_url, timeout=GOTO_TIMEOUT_MS)
        try:
            old_page.close()
        except Exception as close_err:
            LOG.warning("[%s] 실패한 페이지 close 무시: err=%r", name, close_err)

    # 세션 유효성: 대시보드 대기 셀렉터가 5초 안에 뜨면 유효.
    try:
        page.wait_for_selector(wait_sel, timeout=SESSION_CHECK_TIMEOUT_MS)
        LOG.info("[%s] 기존 세션 유효", name)
        return page
    except PWTimeoutError:
        LOG.warning("[%s] 세션 만료/로그인 필요 -> 재로그인 진행", name)
        if login_url and page.locator(J.SEL_INPUT_ID).count() == 0:
            LOG.warning(
                "[%s] 로그인 폼 미감지 -> 로그인 URL 직접 재진입: login_url=%s",
                name,
                login_url,
            )
            page.goto(login_url, timeout=GOTO_TIMEOUT_MS)

    pw = _read_password(name)
    page = _do_login(context, page, login_type, site["id"], pw, url)

    save_storage_state(context, _session_path(name))
    LOG.info("[%s] 세션 저장: %s", name, _session_path(name))
    return page


# ---------------------------------------------------------------------------
# 팝업 오픈
# ---------------------------------------------------------------------------

def _open_dashboard_popup(page: Page, login_type: str) -> Page:
    """대시보드 캔버스 중앙을 더블클릭해 팝업을 가로챈다.

    원본 popup_manager.py 와 동일한 흐름이며, 진단을 위해 캔버스의 bounding_box
    (width/height/중앙좌표)를 INFO 로그로 남긴다.

    Args:
        page: 대시보드 진입이 끝난 Page.
        login_type: ``"standard"`` 또는 ``"new"``.

    Returns:
        가로챈 팝업 Page (``load`` 상태까지 대기 완료).

    Raises:
        ValueError: 알 수 없는 login_type.
        RuntimeError: 캔버스 bounding_box 가 None 이거나 0인 경우, 또는
            더블클릭 후 ``expect_popup`` 이 타임아웃된 경우(좌표 빗나감과
            팝업 미생성을 구분할 수 있게 메시지에 단서를 남긴다).
    """
    selector = J.CANVAS_BY_TYPE.get(login_type)
    if not selector:
        raise ValueError(f"알 수 없는 login_type: {login_type}")

    canvas = page.locator(selector).first
    canvas.wait_for(state="visible", timeout=CANVAS_VISIBLE_TIMEOUT_MS)

    # 차트 내부 데이터가 그려지는 데 시간이 필요(원본 5초). 헤드리스 환경에서
    # 더 안정적인 좌표 클릭을 위해 그대로 둔다.
    LOG.info("차트 데이터 렌더 대기 %dms ...", CHART_RENDER_WAIT_MS)
    page.wait_for_timeout(CHART_RENDER_WAIT_MS)

    box = canvas.bounding_box()
    if box is None:
        raise RuntimeError(
            f"캔버스 bounding_box 가 None ({selector}). "
            "캔버스 미렌더/숨김 의심."
        )

    width = box.get("width", 0)
    height = box.get("height", 0)
    center_x = width / 2
    center_y = height / 2
    LOG.info(
        "캔버스 box: width=%.1f height=%.1f center=(%.1f, %.1f) selector=%s",
        width,
        height,
        center_x,
        center_y,
        selector,
    )
    if width == 0 or height == 0:
        raise RuntimeError(
            f"캔버스 크기가 0 (width={width}, height={height}). "
            "차트 렌더 실패 의심."
        )

    last_timeout: Optional[PWTimeoutError] = None
    for attempt in range(1, POPUP_OPEN_ATTEMPTS + 1):
        LOG.info(
            "캔버스 중앙 더블클릭 시도 %d/%d (%.1f, %.1f)",
            attempt,
            POPUP_OPEN_ATTEMPTS,
            center_x,
            center_y,
        )
        try:
            with page.expect_popup(timeout=POPUP_WAIT_TIMEOUT_MS) as popup_info:
                canvas.dblclick(position={"x": center_x, "y": center_y})
                canvas.hover(position={"x": center_x, "y": center_y})
            popup = popup_info.value
            LOG.info("팝업 이벤트 감지: url=%s", popup.url)
            try:
                popup.wait_for_load_state(
                    "domcontentloaded",
                    timeout=POPUP_LOAD_TIMEOUT_MS,
                )
            except PWTimeoutError as e:
                raise RuntimeError(
                    "팝업은 열렸으나 domcontentloaded 대기 타임아웃"
                    f"(timeout={POPUP_LOAD_TIMEOUT_MS}ms, url={popup.url})"
                ) from e
            break
        except PWTimeoutError as e:
            last_timeout = e
            LOG.warning(
                "팝업 생성 대기 타임아웃 attempt=%d/%d timeout=%dms box=%s",
                attempt,
                POPUP_OPEN_ATTEMPTS,
                POPUP_WAIT_TIMEOUT_MS,
                box,
            )
            if attempt < POPUP_OPEN_ATTEMPTS:
                page.wait_for_timeout(1000)
    else:
        raise RuntimeError(
            "더블클릭은 수행했으나 팝업 미발생/로드 지연"
            f"(timeout={POPUP_WAIT_TIMEOUT_MS}ms, attempts={POPUP_OPEN_ATTEMPTS}, box={box})"
        ) from last_timeout

    LOG.info("팝업 오픈 + 제어권 획득")
    return popup


# ---------------------------------------------------------------------------
# 수치 추출
# ---------------------------------------------------------------------------

def _digits_only(s: str) -> int:
    """문자열에서 숫자만 남겨 int 로(빈 문자열이면 0)."""
    if not s:
        return 0
    digits = re.sub(r"[^0-9]", "", s)
    return int(digits) if digits else 0


def _popup_state(popup_page: Page) -> str:
    """팝업/연결 상태를 예외 없이 한 줄 문자열로 만든다(진단용).

    드라이버/브라우저 연결이 끊긴 상황에서도 ``.url`` / ``.is_closed()`` 호출이
    예외를 던질 수 있으므로 각각 방어적으로 감싼다. 이 문자열로 "팝업이 닫힌
    건지", "연결이 끊긴 건지"를 사후에 구분할 단서를 남긴다.
    """
    try:
        closed = popup_page.is_closed()
    except Exception as e:
        closed = f"<is_closed 실패: {e!r}>"
    try:
        url = popup_page.url
    except Exception as e:
        url = f"<url 실패: {e!r}>"
    return f"is_closed={closed} url={url}"


def _get_metrics_with_refresh(popup_page: Page, login_type: str) -> Dict[str, int]:
    """팝업에서 새로고침 후 fatal/total 텍스트를 정수로 추출한다.

    Args:
        popup_page: 팝업 Page.
        login_type: ``"standard"`` 또는 ``"new"``.

    Returns:
        ``{"fatal": int, "total": int}``.

    Raises:
        RuntimeError: 새로고침 버튼/지표 셀렉터가 시간 안에 나타나지 않는 등
            추출 자체가 실패한 경우.
    """
    refresh_sel = J.REFRESH_BY_TYPE[login_type]
    fatal_sel = J.FATAL_BY_TYPE[login_type]
    total_sel = J.TOTAL_BY_TYPE[login_type]

    try:
        LOG.info(
            "[metrics] 진입: %s | refresh_sel=%s fatal_sel=%s total_sel=%s",
            _popup_state(popup_page),
            refresh_sel,
            fatal_sel,
            total_sel,
        )

        refresh_btn = popup_page.locator(refresh_sel)
        LOG.info("[metrics] 새로고침 버튼 visible 대기 (timeout=%dms)", REFRESH_VISIBLE_TIMEOUT_MS)
        refresh_btn.wait_for(state="visible", timeout=REFRESH_VISIBLE_TIMEOUT_MS)
        LOG.info("[metrics] 새로고침 버튼 visible 확인 -> 클릭")
        refresh_btn.click()
        LOG.info("새로고침 클릭 -> 데이터 갱신 대기 %dms", REFRESH_WAIT_MS)
        popup_page.wait_for_timeout(REFRESH_WAIT_MS)

        fatal_locator = popup_page.locator(fatal_sel).first
        total_locator = popup_page.locator(total_sel).first

        LOG.info("[metrics] fatal 지표 visible 대기 (timeout=%dms)", METRIC_VISIBLE_TIMEOUT_MS)
        fatal_locator.wait_for(state="visible", timeout=METRIC_VISIBLE_TIMEOUT_MS)
        LOG.info("[metrics] fatal 지표 visible 확인 -> 텍스트 추출")
        fatal_text = fatal_locator.inner_text()
        LOG.info("[metrics] total 지표 visible 대기 (timeout=%dms)", METRIC_VISIBLE_TIMEOUT_MS)
        total_locator.wait_for(state="visible", timeout=METRIC_VISIBLE_TIMEOUT_MS)
        LOG.info("[metrics] total 지표 visible 확인 -> 텍스트 추출")
        total_text = total_locator.inner_text()

        metrics = {
            "fatal": _digits_only(fatal_text),
            "total": _digits_only(total_text),
        }
        LOG.info(
            "수치 추출: fatal=%d total=%d (raw fatal=%r total=%r)",
            metrics["fatal"],
            metrics["total"],
            fatal_text,
            total_text,
        )
        return metrics

    except PWTimeoutError as e:
        LOG.warning("[metrics] 추출 타임아웃: %s | %s", e, _popup_state(popup_page))
        raise RuntimeError(f"수치 추출 타임아웃: {e}") from e
    except PWError as e:
        # TargetClosedError 등 드라이버/팝업/브라우저 연결 단절 계열.
        # EPIPE 로 Node 드라이버가 죽은 경우가 여기로 들어온다.
        state = _popup_state(popup_page)
        LOG.warning("[metrics] Playwright 연결/타깃 오류(단절 의심): %r | %s", e, state)
        raise RuntimeError(
            f"수치 추출 중 Playwright 오류(연결/타깃 단절 의심): {e!r} | {state}"
        ) from e


# ---------------------------------------------------------------------------
# 사이트 1회 점검
# ---------------------------------------------------------------------------

def _check_one_site(
    site: Dict[str, str],
    headless: bool,
    stage_holder: List[str],
) -> None:
    """사이트 하나를 점검한다.

    브라우저는 사이트마다 새로 띄우고 닫는다(원본 흐름 보존 + 격리 확실).

    Args:
        site: 사이트 dict (``name/url/id/login_type``).
        headless: 브라우저 헤드리스 여부(호출부에서 해석된 값).
        stage_holder: 호출부 main 의 ``stage`` 변수를 공유하기 위한 1-원소 리스트
            (예외 핸들러에서 최종 stage 를 출력해야 하므로).

    Raises:
        Exception: 시스템 오류 또는 fatal 임계 초과(Fail-Fast).
    """
    name = site["name"]
    login_type = site["login_type"]
    # 사이트별 선택 키. 사내/HTTPS 인증서 오류가 있는 사이트만 True (예: Redpen).
    ignore_https_errors = bool(site.get("ignore_https_errors", False))

    stage_holder[0] = f"[{name}] launch_browser"
    LOG.info("[STAGE] %s", stage_holder[0])

    state_path = _session_path(name)
    storage_state_arg = state_path if state_path.exists() else None

    with sync_browser(
        headless=headless,
        storage_state=storage_state_arg,
        ignore_https_errors=ignore_https_errors,
    ) as (_browser, context, page):

        stage_holder[0] = f"[{name}] ensure_login"
        LOG.info("[STAGE] %s", stage_holder[0])
        page = _ensure_login(context, page, site)

        stage_holder[0] = f"[{name}] open_dashboard_popup"
        LOG.info("[STAGE] %s", stage_holder[0])
        popup = _open_dashboard_popup(page, login_type)

        stage_holder[0] = f"[{name}] extract_metrics"
        LOG.info("[STAGE] %s", stage_holder[0])
        metrics = _get_metrics_with_refresh(popup, login_type)

        stage_holder[0] = f"[{name}] check_threshold"
        LOG.info("[STAGE] %s", stage_holder[0])
        if metrics["fatal"] > FATAL_THRESHOLD:
            raise RuntimeError(
                f"[{name}] Fatal 수치 감지: {metrics['fatal']}건 "
                f"(임계 {FATAL_THRESHOLD})"
            )

        LOG.info("[%s] 정상 (fatal=%d total=%d)", name, metrics["fatal"], metrics["total"])


# ---------------------------------------------------------------------------
# CLI / main
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Jennifer 통합 점검 (Fail-Fast)")
    parser.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="브라우저 헤드리스 여부. 미지정 시 settings.yaml 을 따른다.",
    )
    return parser.parse_args()


def main() -> int:
    """Jennifer job 진입점."""
    args = _parse_args()

    # CLI 인자 > settings.yaml. 둘 다 없으면 get_headless 가 에러로 죽인다.
    headless = (
        args.headless
        if args.headless is not None
        else config.get_headless("jennifer")
    )

    config.ensure_dirs()
    SESSION_DIR.mkdir(parents=True, exist_ok=True)

    start_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    LOG.info("[START] jennifer 통합 점검 at %s headless=%s", start_ts, headless)

    # 예외 핸들러에서 마지막 stage 를 보기 위한 1-원소 holder.
    stage_holder: List[str] = ["init"]

    try:
        stage_holder[0] = "load_sites"
        LOG.info("[STAGE] %s", stage_holder[0])
        sites = _load_sites()
        LOG.info("점검 대상 %d개 사이트 로드: %s",
                 len(sites), [s["name"] for s in sites])

        for site in sites:
            _check_one_site(site, headless, stage_holder)

        LOG.info("[END] 모든 사이트 정상 완료")
        return 0

    except Exception as e:
        last_stage = stage_holder[0]
        LOG.exception("[FAIL] stage=%s err=%r", last_stage, e)

        # Fail-Fast: 한 번만 Pushover.
        send_pushover_emergency(
            title="Jennifer 장애 발생",
            message=f"stage={last_stage} | err={e}",
        )
        return 1

    finally:
        LOG.info("[END] jennifer run finished")


if __name__ == "__main__":
    raise SystemExit(main())
