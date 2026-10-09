"""Jennifer 사이트 목록 로딩 + 로그인/세션 공용 모듈.

jennifer 점검 job(``jobs.jennifer``)과 대시보드 스냅샷(``tools.jennifer_snapshot_test``)
이 같은 로그인 흐름을 쓰도록 ``jobs/jennifer/__main__.py`` 에서 분리했다.
동작은 분리 전과 동일하다.

사이트 목록: config/jennifer_sites.json (id 까지만 평문, pw 는 .env).
비밀번호 env 키 규칙: ``JENNIFER_PW__<NAME_UPPER>`` — 사이트명 공백/하이픈을
언더스코어로 치환 후 대문자화. 예: ``Jennifer_cloud`` → ``JENNIFER_PW__JENNIFER_CLOUD``.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Dict, List

from playwright.sync_api import (
    BrowserContext,
    Error as PWError,
    Page,
    TimeoutError as PWTimeoutError,
)

from common import config
from common.browser import save_storage_state
from site_selectors import jennifer as J


# ---------------------------------------------------------------------------
# 설정 (비밀 아님)
# ---------------------------------------------------------------------------

# 세션 유효성 확인 타임아웃(원본 5초).
SESSION_CHECK_TIMEOUT_MS = 5000

# 로그인 후 대시보드 진입 타임아웃(원본 30초).
LOGIN_WAIT_TIMEOUT_MS = 30000

# Redpen 로그인 후 깨진 HTTP 리다이렉트/Chrome error를 빠르게 감지할 대기.
REDPEN_REDIRECT_CHECK_MS = 3000

# 페이지 진입(goto) 타임아웃(원본 60초).
GOTO_TIMEOUT_MS = 60000

# 사이트 설정 파일(비밀번호 제외).
SITES_CONFIG_PATH = config.BASE_DIR / "config" / "jennifer_sites.json"

# 세션 저장 폴더(원본의 sessions/ 와 동등, 규약 9에 따라 STATE_DIR 하위로).
SESSION_DIR = config.STATE_DIR / "jennifer"


# ---------------------------------------------------------------------------
# 사이트 / 자격증명 로딩
# ---------------------------------------------------------------------------

def load_sites() -> List[Dict[str, str]]:
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


def session_path(site_name: str) -> Path:
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
    log: logging.Logger,
) -> Page:
    """login_type 에 맞춰 로그인 폼을 채워 제출한다.

    Args:
        context: 현재 BrowserContext.
        page: 로그인 페이지가 떠 있는 Page.
        login_type: ``"standard"`` 또는 ``"new"``.
        site_id: 로그인 id.
        site_pw: 로그인 pw.
        dashboard_url: 로그인 성공 후 진입해야 할 HTTPS 대시보드 URL.
        log: 호출부 job 의 로거.

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
            log.warning(
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

        log.warning(
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
            log.warning("로그인 후 실패한 페이지 close 무시: err=%r", close_err)
        page.wait_for_selector(wait_sel, timeout=LOGIN_WAIT_TIMEOUT_MS)
        return page


def ensure_login(
    context: BrowserContext,
    page: Page,
    site: Dict[str, str],
    log: logging.Logger,
) -> Page:
    """대시보드 진입을 보장한다. 세션이 살아있으면 그대로, 아니면 재로그인.

    재로그인 후 ``save_storage_state`` 로 세션 파일을 갱신한다.

    Args:
        context: 현재 BrowserContext.
        page: 현재 Page(아직 ``goto`` 전이어야 함).
        site: 사이트 dict (``name/url/id/login_type``). 선택 키 ``login_url`` 이
            있으면 대시보드 진입 실패 시 새 Page로 로그인 페이지에 직접 폴백한다.
        log: 호출부 job 의 로거.

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
        log.warning(
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
            log.warning("[%s] 실패한 페이지 close 무시: err=%r", name, close_err)

    # 세션 유효성: 대시보드 대기 셀렉터가 5초 안에 뜨면 유효.
    try:
        page.wait_for_selector(wait_sel, timeout=SESSION_CHECK_TIMEOUT_MS)
        log.info("[%s] 기존 세션 유효", name)
        return page
    except PWTimeoutError:
        log.warning("[%s] 세션 만료/로그인 필요 -> 재로그인 진행", name)
        if login_url and page.locator(J.SEL_INPUT_ID).count() == 0:
            log.warning(
                "[%s] 로그인 폼 미감지 -> 로그인 URL 직접 재진입: login_url=%s",
                name,
                login_url,
            )
            page.goto(login_url, timeout=GOTO_TIMEOUT_MS)

    pw = _read_password(name)
    page = _do_login(context, page, login_type, site["id"], pw, url, log)

    save_storage_state(context, session_path(name))
    log.info("[%s] 세션 저장: %s", name, session_path(name))
    return page
