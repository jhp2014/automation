"""Zenius 모니터 job (규약 v1.1).

원본 zenius_monitor_v2.0.py의 로직을 보존하면서 common 모듈에 의존하도록
재작성했다:
    - 브라우저/컨텍스트 lifecycle: common.browser.sync_browser
    - 로깅: common.logging.get_logger
    - 알림: common.notify (Pushover 긴급 / Telegram 보고서·heartbeat)
    - 텔레그램 타깃 조회: common.config.get_telegram_target
    - 셀렉터: site_selectors.zenius

비밀값(로그인 id/pw)은 .env에서 읽는다(ZENIUS_USER_ID/ZENIUS_USER_PW).
"""

from __future__ import annotations

# 프로젝트 루트를 sys.path에 추가한다.
# python -m jobs.zenius 로 실행하든, 이 파일을 직접 실행하든
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
from typing import Optional

from playwright.sync_api import (
    BrowserContext,
    Page,
    TimeoutError as PWTimeoutError,
)

from common import config
from common.browser import save_storage_state, sync_browser
from common.config import TelegramTarget, get_telegram_target
from common.logging import get_logger
from common.notify import (
    send_pushover_emergency,
    send_telegram_message,
    send_telegram_photo,
)
from site_selectors import zenius as Z


# ---------------------------------------------------------------------------
# 정책 / 설정 (비밀 아님)
# ---------------------------------------------------------------------------

# 위험 등급(원본 그대로): 치명/긴급/위험 + 미처리만 후보.
CRITICAL_TITLES = {"치명", "긴급", "위험", "주의", "무해"}
IGNORE_STATUSES = {"종료", "응답", "인지"}
# 지속 9분 이상이면 보고 대상.
THRESHOLD_SEC = 9 * 60
# 보고서 양식의 "전파내용" 끝에 들어가는 접촉 방식.
CONTACT_METHOD = "유선"

# 캡처가 짤리지 않도록 큰 뷰포트 고정(원본 패턴).
BROWSER_WINDOW_WIDTH = 1920
BROWSER_WINDOW_HEIGHT = 900

# 상태 파일 (모두 config.STATE_DIR 하위).
STATE_AUTH = config.STATE_DIR / "zenius_auth.json"
STATE_REPORTED_IDS = config.STATE_DIR / "zenius_reported_ids.json"

# 캡처 저장 폴더 (state와 분리해 BASE_DIR/screenshots에 둔다).
SCREEN_DIR = config.BASE_DIR / "screenshots" / "zenius"

LOG = get_logger("jobs.zenius", "zenius.log")


# ---------------------------------------------------------------------------
# reported_ids
# ---------------------------------------------------------------------------

def load_reported_ids() -> set[str]:
    """이전에 보고 완료된 이벤트 id 집합을 로드한다.

    Returns:
        파일이 없거나 깨졌으면 빈 집합. 파일이 정상이면 문자열 id 집합.
    """
    if not STATE_REPORTED_IDS.exists():
        return set()
    try:
        data = json.loads(STATE_REPORTED_IDS.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return set(str(x) for x in data)
    except Exception as e:
        LOG.warning("reported_ids 로드 실패(빈 집합으로 진행): %r", e)
    return set()


def save_reported_ids(ids_set: set[str]) -> None:
    """보고 처리 끝난 이벤트 id 집합을 저장한다.

    Args:
        ids_set: 누적된 reported id 집합.
    """
    STATE_REPORTED_IDS.parent.mkdir(parents=True, exist_ok=True)
    STATE_REPORTED_IDS.write_text(
        json.dumps(sorted(ids_set), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# 로그인 / 권한
# ---------------------------------------------------------------------------

def is_login_page(page: Page) -> bool:
    """현재 페이지가 zenius 로그인 페이지인지 판정한다."""
    if "login.zenius" in page.url:
        return True
    return page.locator(Z.SEL_ID).count() > 0


def is_403_error_page(page: Page) -> bool:
    """현재 페이지가 403 에러 페이지인지 판정한다."""
    return page.url.startswith(Z.ERROR_403_PREFIX) or "ERROR_CODE=403" in page.url


def handle_403_popup_to_login(page: Page) -> None:
    """403 팝업의 OK를 눌러 로그인 페이지로 이동시킨다.

    팝업 OK 셀렉터가 보이지 않으면 LOGIN_URL로 직접 이동한다.
    """
    try:
        page.wait_for_selector(Z.SEL_ERROR_OK, state="visible", timeout=5000)
        with page.expect_navigation(wait_until="domcontentloaded", timeout=15000):
            page.click(Z.SEL_ERROR_OK)
    except PWTimeoutError:
        page.goto(Z.LOGIN_URL, wait_until="domcontentloaded")


def do_login(page: Page, user_id: str, user_pw: str) -> None:
    """로그인 폼을 채우고 제출한다."""
    page.goto(Z.LOGIN_URL, wait_until="load")
    page.wait_for_selector(Z.SEL_ID, state="visible", timeout=15000)
    page.fill(Z.SEL_ID, user_id)
    page.fill(Z.SEL_PW, user_pw)

    try:
        with page.expect_navigation(wait_until="networkidle", timeout=20000):
            page.click(Z.SEL_LOGIN_BTN)
    except PWTimeoutError:
        # 네비게이션이 안 잡히는 케이스: 클릭 후 networkidle만 기다린다.
        page.click(Z.SEL_LOGIN_BTN)
        page.wait_for_load_state("networkidle")


def ensure_logged_in_and_save(
    context: BrowserContext,
    page: Page,
    user_id: str,
    user_pw: str,
) -> None:
    """메인까지 진입하고 세션을 storage_state로 저장한다.

    Args:
        context: 현재 BrowserContext.
        page: 현재 Page.
        user_id: zenius 로그인 id.
        user_pw: zenius 로그인 pw.

    Raises:
        RuntimeError: 로그인/권한 획득에 끝내 실패한 경우.
    """
    page.goto(Z.MAIN_URL, wait_until="domcontentloaded")
    page.wait_for_load_state("domcontentloaded")

    if is_403_error_page(page):
        handle_403_popup_to_login(page)
        page.wait_for_load_state("domcontentloaded")

    if is_login_page(page):
        do_login(page, user_id, user_pw)

    page.goto(Z.MAIN_URL, wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")

    if is_403_error_page(page) or is_login_page(page):
        raise RuntimeError(f"로그인/권한 획득 실패. 현재 URL: {page.url}")

    save_storage_state(context, STATE_AUTH)


# ---------------------------------------------------------------------------
# EMS 진입 / 정렬
# ---------------------------------------------------------------------------

def is_status_sort_asc(page: Page) -> bool:
    """처리상태 컬럼이 ASC 정렬 상태인지 판정한다."""
    asc = page.locator(f"{Z.SEL_STATUS_SORT_WRAP} span[sort='asc']").first
    desc = page.locator(f"{Z.SEL_STATUS_SORT_WRAP} span[sort='desc']").first
    asc_class = asc.get_attribute("class") or ""
    desc_class = desc.get_attribute("class") or ""
    return ("ui-state-disabled" not in asc_class) and ("ui-state-disabled" in desc_class)


def click_ems_and_ensure_status_sort(page: Page) -> None:
    """EMS 메뉴 진입 후 처리상태를 ASC로 맞춘다.

    Raises:
        RuntimeError: 최대 3회 클릭에도 ASC가 되지 않는 경우.
    """
    page.wait_for_selector(Z.SEL_EMS_MENU, state="visible", timeout=15000)
    page.click(Z.SEL_EMS_MENU)
    page.wait_for_selector(Z.SEL_STATUS_TH, state="visible", timeout=20000)

    header = page.locator(Z.SEL_STATUS_TH).first
    for _ in range(3):
        if is_status_sort_asc(page):
            return
        header.click()
        page.wait_for_timeout(300)

    raise RuntimeError("처리상태 정렬을 ASC로 맞추지 못했습니다.")


# ---------------------------------------------------------------------------
# 그리드 파싱
# ---------------------------------------------------------------------------

def parse_hhmmss_to_seconds(s: str) -> int:
    """HH:MM:SS 문자열을 초 단위로 변환한다(파싱 실패 시 0)."""
    m = re.match(r"^\s*(\d{2}):(\d{2}):(\d{2})\s*$", s or "")
    if not m:
        return 0
    hh, mm, ss = map(int, m.groups())
    return hh * 3600 + mm * 60 + ss


def _cell_text(row, selector: str) -> str:
    """행 내부 td의 title 우선, 없으면 inner_text를 가져온다."""
    td = row.locator(selector).first
    return (td.get_attribute("title") or td.inner_text() or "").strip()


def scan_critical_rows(page: Page) -> list[dict]:
    """EMS 그리드를 훑어 위험 등급 + 미처리 행만 추출한다.

    Returns:
        각 항목은 ``{id, status, severity, duration, duration_sec, title, msg,
        group, host, evttime}`` 키를 가진 dict.
    """
    rows = page.locator(Z.SEL_GRID_ROWS)
    count = rows.count()
    found: list[dict] = []

    for i in range(count):
        row = rows.nth(i)

        event_id = (row.get_attribute("id") or "").strip()
        if not event_id:
            continue

        status = _cell_text(row, Z.SEL_TD_STATUS)

        sev_span = row.locator(Z.SEL_TD_ALERT).first
        severity = (sev_span.get_attribute("title") or "").strip()

        duration = _cell_text(row, Z.SEL_TD_DUR)
        duration_sec = parse_hhmmss_to_seconds(duration)

        evt_title = _cell_text(row, Z.SEL_TD_TITLE)
        evt_msg = _cell_text(row, Z.SEL_TD_MSG)
        groupname = _cell_text(row, Z.SEL_TD_GROUP)
        hostname = _cell_text(row, Z.SEL_TD_HOST)
        evttime = _cell_text(row, Z.SEL_TD_EVTTIME)

        if severity in CRITICAL_TITLES and status not in IGNORE_STATUSES:
            found.append({
                "id": event_id,
                "status": status,
                "severity": severity,
                "duration": duration,
                "duration_sec": duration_sec,
                "title": evt_title,
                "msg": evt_msg,
                "group": groupname,
                "host": hostname,
                "evttime": evttime,
            })

    return found


# ---------------------------------------------------------------------------
# SMS 간편검색 (담당자 조회)
# ---------------------------------------------------------------------------

def wait_sms_grid_idle(page: Page, timeout_ms: int = 20000) -> None:
    """SMS jqGrid 로딩 레이어가 사라질 때까지 대기한다.

    로딩 레이어가 아예 없는 환경(렌더 없이 갱신)에서는 networkidle로 폴백한다.
    """
    loading_text = page.locator(Z.SEL_SMS_LOADING_TEXT)
    loading_mask = page.locator(Z.SEL_SMS_LOADING_MASK)

    had_any = False

    if loading_text.count() > 0:
        had_any = True
        try:
            loading_text.wait_for(state="visible", timeout=1000)
        except PWTimeoutError:
            pass
        loading_text.wait_for(state="hidden", timeout=timeout_ms)

    if loading_mask.count() > 0:
        had_any = True
        try:
            loading_mask.wait_for(state="visible", timeout=1000)
        except PWTimeoutError:
            pass
        loading_mask.wait_for(state="hidden", timeout=timeout_ms)

    if not had_any:
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(300)


def click_sms_and_wait_loaded(page: Page) -> None:
    """SMS 메뉴 진입 후 간편검색 영역이 보일 때까지 대기한다."""
    page.wait_for_selector(Z.SEL_SMS_MENU, state="visible", timeout=15000)
    page.click(Z.SEL_SMS_MENU)
    page.wait_for_selector(Z.SEL_SMS_GRID, state="attached", timeout=20000)
    page.wait_for_selector(Z.SEL_SMS_SEARCH_INPUT, state="visible", timeout=20000)
    page.wait_for_selector(Z.SEL_SMS_SEARCH_BTN, state="visible", timeout=20000)


def lookup_owner_in_sms(page: Page, host: str) -> tuple[str, str]:
    """SMS 간편검색으로 담당자(위치)와 검색 결과 캡처 경로를 얻는다.

    현장 가정: host 검색 결과는 1행만 반환된다. 다중 행이면 첫 행을 쓰고
    경고 로그를 남긴다.

    Args:
        page: SMS 메뉴까지 진입한 페이지.
        host: 검색할 호스트명.

    Returns:
        ``(owner, sms_screenshot_path)``. 결과가 없거나 실패하면 owner는 "".
        검색 자체는 했고 결과가 없을 때만 _noresult 캡처 경로를 돌려준다.
    """
    host = (host or "").strip()
    if not host:
        return "", ""

    page.locator(Z.SEL_SMS_SEARCH_INPUT).fill("")
    page.locator(Z.SEL_SMS_SEARCH_INPUT).fill(host)
    page.locator(Z.SEL_SMS_SEARCH_BTN).click()

    try:
        wait_sms_grid_idle(page, timeout_ms=20000)
    except PWTimeoutError:
        return "", ""

    rows = page.locator(Z.SEL_SMS_ROWS)
    if rows.count() == 0:
        return "", capture_sms(page, suffix=f"sms_{safe_filename(host)}_noresult")

    if rows.count() > 1:
        LOG.warning(
            "SMS 검색 결과가 2행 이상(현장 가정과 다름). host=%s rows=%d -> 첫 행 사용",
            host,
            rows.count(),
        )

    first = rows.first
    try:
        first.wait_for(state="visible", timeout=3000)
    except Exception:
        return "", ""

    owner_td = first.locator(Z.SEL_SMS_TD_OWNER).first
    owner = (owner_td.get_attribute("title") or owner_td.inner_text() or "").strip()

    sms_img = capture_sms(page, suffix=f"sms_{safe_filename(host)}")
    return owner, sms_img


# ---------------------------------------------------------------------------
# 보고 / 캡처
# ---------------------------------------------------------------------------

def build_report_message(event: dict, owner: str) -> str:
    """원본 보고서 양식을 그대로 재현한다."""
    now_hm = datetime.now().strftime("%H:%M")
    owner_text = owner if owner else "담당자 미확인"
    return (
        f"1. 발생일시 : {event.get('evttime', '')}\n"
        f"2. 서비스명 : {event.get('group', '')} {event.get('host', '')}\n"
        f"3. 이슈현상 : {event.get('title', '')} {event.get('msg', '')}\n"
        f"4. 전파내용 : {now_hm} {owner_text} 내용전파완료({CONTACT_METHOD})"
    )


def safe_filename(s: str) -> str:
    """파일명으로 쓸 수 있게 위험 문자를 _ 로 치환한다."""
    s = (s or "").strip()
    if not s:
        return "unknown"
    return re.sub(r"[^0-9A-Za-z._-]+", "_", s)[:80]


def ensure_big_viewport(page: Page) -> None:
    """캡처가 짤리지 않도록 큰 뷰포트로 다시 고정한다(이미 동일하면 무해)."""
    try:
        page.set_viewport_size({
            "width": BROWSER_WINDOW_WIDTH,
            "height": BROWSER_WINDOW_HEIGHT,
        })
    except Exception:
        # viewport=None인 컨텍스트 등에서는 무시.
        pass


def capture_ems(page: Page) -> str:
    """EMS 화면을 full_page로 캡처해 경로를 반환한다."""
    SCREEN_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = SCREEN_DIR / f"ems_{ts}.png"
    ensure_big_viewport(page)
    page.wait_for_timeout(300)
    page.screenshot(path=str(path), full_page=True)
    return str(path)


def capture_sms(page: Page, suffix: str = "sms") -> str:
    """SMS 화면을 full_page로 캡처해 경로를 반환한다."""
    SCREEN_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = SCREEN_DIR / f"{suffix}_{ts}.png"
    ensure_big_viewport(page)
    page.wait_for_timeout(200)
    page.screenshot(path=str(path), full_page=True)
    return str(path)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def _read_credentials() -> tuple[str, str]:
    """환경변수에서 zenius 로그인 자격증명을 읽는다.

    Raises:
        RuntimeError: 필수 키가 없거나 비어 있는 경우.
    """
    user_id = os.getenv("ZENIUS_USER_ID", "").strip()
    user_pw = os.getenv("ZENIUS_USER_PW", "").strip()
    missing = [k for k, v in (("ZENIUS_USER_ID", user_id), ("ZENIUS_USER_PW", user_pw)) if not v]
    if missing:
        raise RuntimeError(f"zenius 로그인 자격증명 누락: {', '.join(missing)}")
    return user_id, user_pw


def _safe_get_target(purpose: str) -> Optional[TelegramTarget]:
    """텔레그램 타깃 조회. 누락이면 경고만 남기고 None 반환."""
    try:
        return get_telegram_target("zenius", purpose)
    except KeyError as e:
        LOG.warning("Telegram 타깃 조회 실패(purpose=%s): %s", purpose, e)
        return None


def main() -> int:
    """Zenius 모니터 진입점. ``--headless/--no-headless`` 를 받는다."""
    parser = argparse.ArgumentParser(description="Zenius EMS monitor")
    parser.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="브라우저 헤드리스 여부. 미지정 시 settings.yaml 의 zenius.headless 사용.",
    )
    args = parser.parse_args()

    # 우선순위: CLI > settings.yaml(폴백 없음 — 둘 다 없으면 조회에서 raise).
    headless = (
        args.headless if args.headless is not None
        else config.get_headless("zenius")
    )

    config.ensure_dirs()

    stage = "init"
    start_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    LOG.info("[START] zenius run at %s headless=%s", start_ts, headless)

    target_report = _safe_get_target("report")
    target_heartbeat = _safe_get_target("heartbeat")

    # heartbeat: 매 실행 시작에 전송 (every_run 정책).
    if target_heartbeat is not None:
        send_telegram_message(
            target_heartbeat,
            f"[HB:start] zenius running - {start_ts}",
        )

    reported = load_reported_ids()

    try:
        with sync_browser(
            headless=headless,
            storage_state=STATE_AUTH if STATE_AUTH.exists() else None,
            window_size=(BROWSER_WINDOW_WIDTH, BROWSER_WINDOW_HEIGHT),
        ) as (_browser, context, page):

            stage = "ensure_logged_in"
            LOG.info("[STAGE] %s", stage)
            user_id, user_pw = _read_credentials()
            ensure_logged_in_and_save(context, page, user_id, user_pw)
            LOG.info("[OK] 로그인/세션 확보 완료")

            stage = "open_ems"
            LOG.info("[STAGE] %s", stage)
            click_ems_and_ensure_status_sort(page)

            stage = "scan_rows"
            LOG.info("[STAGE] %s", stage)
            crit = scan_critical_rows(page)
            LOG.info("위험 이벤트(치명/긴급/위험)+미처리 건수: %d", len(crit))

            stage = "filter_candidates"
            candidates = [
                r for r in crit
                if r["duration_sec"] >= THRESHOLD_SEC and r["id"] not in reported
            ]
            if not candidates:
                LOG.info("보고 대상 없음")
                return 0

            candidates.sort(key=lambda r: r["duration_sec"], reverse=True)

            # 한 실행당 Pushover 긴급은 1회만(원본 정책).
            send_pushover_emergency(
                title=f"[Zenius] 위험 지속(10분+) {len(candidates)}건",
                message="위험 이벤트 감지. 상세 보고/캡처는 Telegram(Report) 확인.",
            )

            stage = "capture_ems"
            LOG.info("[STAGE] %s", stage)
            ems_img = capture_ems(page)

            stage = "open_sms"
            LOG.info("[STAGE] %s", stage)
            sms_ready = True
            try:
                click_sms_and_wait_loaded(page)
            except Exception as e:
                sms_ready = False
                LOG.warning("SMS 페이지 이동 실패(담당자 미확인으로 진행): %r", e)

            stage = "per_event_report"
            LOG.info("[STAGE] %s", stage)
            for ev in candidates:
                owner = ""
                sms_img = ""
                if sms_ready:
                    try:
                        owner, sms_img = lookup_owner_in_sms(page, ev["host"])
                    except Exception as e:
                        LOG.warning("SMS 담당자 조회 실패(계속 진행): host=%s err=%r", ev["host"], e)

                report_msg = build_report_message(ev, owner)

                if target_report is not None:
                    # 사진 전송 실패는 notify 내부에서 경고만 남기고 swallow되므로
                    # 별도 fallback 분기는 불필요하지만, EMS 캡처 누락 케이스를
                    # 대비해 text도 함께 보낸다.
                    send_telegram_photo(target_report, report_msg, ems_img)
                    if sms_img:
                        send_telegram_photo(
                            target_report,
                            f"[SMS] host={ev.get('host', '')} / 담당자={owner or '미확인'}",
                            sms_img,
                        )
                else:
                    LOG.warning("Telegram(report) 타깃 없음 -> 보고 메시지 콘솔만: %s", report_msg)

                reported.add(ev["id"])

            stage = "save_reported_ids"
            LOG.info("[STAGE] %s", stage)
            save_reported_ids(reported)
            LOG.info("[OK] 이번 실행에서 보고 처리: %d건", len(candidates))
            return 0

    except Exception as e:
        # 파일 로그에 traceback까지 남긴다.
        LOG.exception("[FAIL] stage=%s err=%r", stage, e)

        # 로그인/권한 실패는 더 명확한 제목으로.
        title = (
            "[Zenius] 로그인 실패"
            if stage == "ensure_logged_in"
            else "[Zenius] 스크립트 실패"
        )
        send_pushover_emergency(title=title, message=f"stage={stage} | err={e}")

        if target_heartbeat is not None:
            send_telegram_message(
                target_heartbeat,
                f"[FAIL] zenius stage={stage} | {e}",
            )
        return 1

    finally:
        LOG.info("[END] zenius run finished")


if __name__ == "__main__":
    raise SystemExit(main())
