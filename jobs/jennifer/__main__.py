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

# 세션 유효성 확인 타임아웃(원본 5초).
SESSION_CHECK_TIMEOUT_MS = 5000

# 로그인 후 대시보드 진입 타임아웃(원본 30초).
LOGIN_WAIT_TIMEOUT_MS = 30000

# 페이지 진입(goto) 타임아웃(원본 60초).
GOTO_TIMEOUT_MS = 60000

# 캔버스 visible 대기(원본 30초).
CANVAS_VISIBLE_TIMEOUT_MS = 30000

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

def _do_login(page: Page, login_type: str, site_id: str, site_pw: str) -> None:
    """login_type 에 맞춰 로그인 폼을 채워 제출한다.

    Args:
        page: 로그인 페이지가 떠 있는 Page.
        login_type: ``"standard"`` 또는 ``"new"``.
        site_id: 로그인 id.
        site_pw: 로그인 pw.

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
    page.wait_for_selector(wait_sel, timeout=LOGIN_WAIT_TIMEOUT_MS)


def _ensure_login(
    context: BrowserContext,
    page: Page,
    site: Dict[str, str],
) -> None:
    """대시보드 진입을 보장한다. 세션이 살아있으면 그대로, 아니면 재로그인.

    재로그인 후 ``save_storage_state`` 로 세션 파일을 갱신한다.

    Args:
        context: 현재 BrowserContext.
        page: 현재 Page(아직 ``goto`` 전이어야 함).
        site: 사이트 dict (``name/url/id/login_type``).

    Raises:
        playwright.sync_api.TimeoutError: 페이지 진입(goto) 자체가 실패한 경우.
        RuntimeError: 비밀번호 env 누락.
    """
    name = site["name"]
    url = site["url"]
    login_type = site["login_type"]
    wait_sel = J.WAIT_SELECTOR_BY_TYPE[login_type]

    page.goto(url, timeout=GOTO_TIMEOUT_MS)

    # 세션 유효성: 대시보드 대기 셀렉터가 5초 안에 뜨면 유효.
    try:
        page.wait_for_selector(wait_sel, timeout=SESSION_CHECK_TIMEOUT_MS)
        LOG.info("[%s] 기존 세션 유효", name)
        return
    except PWTimeoutError:
        LOG.warning("[%s] 세션 만료/로그인 필요 -> 재로그인 진행", name)

    pw = _read_password(name)
    _do_login(page, login_type, site["id"], pw)

    save_storage_state(context, _session_path(name))
    LOG.info("[%s] 세션 저장: %s", name, _session_path(name))


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

    LOG.info("캔버스 중앙 더블클릭 (%.1f, %.1f)", center_x, center_y)
    try:
        with page.expect_popup() as popup_info:
            canvas.dblclick(position={"x": center_x, "y": center_y})
            canvas.hover(position={"x": center_x, "y": center_y})
        popup = popup_info.value
    except PWTimeoutError as e:
        # bounding_box 가 정상이었으므로 좌표는 가능. 그래도 팝업이 안 뜬 상황 →
        # 차트 렌더 후 더블클릭 이벤트가 캔버스에 전달되지 않았거나, 사이트가
        # 팝업을 만들지 않은 경우. 두 가지를 구분 가능하게 메시지에 남긴다.
        raise RuntimeError(
            "더블클릭은 수행했으나 팝업 미발생(좌표/렌더/이벤트바인딩 의심). "
            f"box={box}"
        ) from e

    popup.wait_for_load_state("load")
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
        refresh_btn = popup_page.locator(refresh_sel)
        refresh_btn.wait_for(state="visible", timeout=10000)
        refresh_btn.click()
        LOG.info("새로고침 클릭")
        popup_page.wait_for_timeout(REFRESH_WAIT_MS)

        fatal_locator = popup_page.locator(fatal_sel).first
        total_locator = popup_page.locator(total_sel).first

        fatal_locator.wait_for(state="visible")
        fatal_text = fatal_locator.inner_text()
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
        raise RuntimeError(f"수치 추출 타임아웃: {e}") from e


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
        _ensure_login(context, page, site)

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
