"""DailyService(SDS) 모니터 job (규약 v1.1).

원본 daily-service-mon.py의 동작을 보존하면서 common 모듈에 의존하도록
재작성했다:
    - 세션 폴백(깨진 파일 -> 새 컨텍스트)은 common.browser가 담당.
    - 로깅/알림/텔레그램 타깃 조회도 common에 위임.

비밀값(로그인 id/pw)은 .env에서 읽는다
(DAILYSERVICE_USER_ID/DAILYSERVICE_USER_PW).
"""

from __future__ import annotations

# 프로젝트 루트를 sys.path에 추가한다.
# python -m jobs.daily_service 로 실행하든, 이 파일을 직접 실행하든
# common / site_selectors 패키지를 항상 import할 수 있게 하기 위함이다.
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]  # automation/
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import argparse
import os
from datetime import datetime
from typing import Optional

from playwright.sync_api import Page

from common import config
from common.browser import save_storage_state, sync_browser
from common.config import TelegramTarget, get_telegram_target
from common.logging import get_logger
from common.notify import send_heartbeat, send_pushover_emergency
from site_selectors import daily_service as D


# ---------------------------------------------------------------------------
# 설정 (비밀 아님)
# ---------------------------------------------------------------------------

# 세션 파일 (config.STATE_DIR 하위).
STATE_AUTH = config.STATE_DIR / "daily_service_auth.json"

LOG = get_logger("jobs.daily_service", "daily_service.log")


# ---------------------------------------------------------------------------
# 로그인
# ---------------------------------------------------------------------------

def is_login_page(page: Page) -> bool:
    """현재 페이지가 SDS 로그인 페이지인지 판정한다."""
    if "/login" in page.url:
        return True
    return (
        page.locator(D.SEL_USERNAME).count() > 0
        and page.locator(D.SEL_PASSWORD).count() > 0
    )


def do_login(page: Page, user_id: str, user_pw: str) -> None:
    """SDS 로그인 폼을 채우고 제출한다.

    Raises:
        RuntimeError: 제출 후에도 여전히 로그인 페이지에 머무는 경우.
    """
    LOG.info("[STEP] login 시도")
    page.goto(D.LOGIN_URL, wait_until="domcontentloaded")
    page.fill(D.SEL_USERNAME, user_id)
    page.fill(D.SEL_PASSWORD, user_pw)
    page.click(D.SEL_LOGIN_SUBMIT)
    page.wait_for_load_state("networkidle")

    if is_login_page(page):
        raise RuntimeError("로그인 실패: 여전히 로그인 페이지에 있음")

    LOG.info("[OK] 로그인 성공")


# ---------------------------------------------------------------------------
# 대시보드: 비정상 카운트
# ---------------------------------------------------------------------------

def check_abnormal_service_count(page: Page) -> int:
    """대시보드에서 "비정상 서비스" 카운트를 정수로 읽는다.

    Args:
        page: 대시보드까지 진입한 페이지.

    Returns:
        비정상 서비스 건수(정수).

    Raises:
        RuntimeError: 셀렉터 텍스트를 정수로 파싱할 수 없는 경우.
    """
    page.wait_for_load_state("networkidle")
    page.wait_for_selector(D.SEL_ABNORMAL_COUNT, timeout=15000)

    text = page.locator(D.SEL_ABNORMAL_COUNT).first.inner_text().strip()
    try:
        value = int(text)
    except ValueError as e:
        raise RuntimeError(f"비정상 서비스 카운트 파싱 실패: '{text}'") from e

    LOG.info("비정상 서비스 건수: %d", value)
    return value


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def _read_credentials() -> tuple[str, str]:
    """환경변수에서 SDS 로그인 자격증명을 읽는다.

    Raises:
        RuntimeError: 필수 키가 없거나 비어 있는 경우.
    """
    user_id = os.getenv("DAILYSERVICE_USER_ID", "").strip()
    user_pw = os.getenv("DAILYSERVICE_USER_PW", "").strip()
    missing = [
        k for k, v in (
            ("DAILYSERVICE_USER_ID", user_id),
            ("DAILYSERVICE_USER_PW", user_pw),
        ) if not v
    ]
    if missing:
        raise RuntimeError(f"DailyService 로그인 자격증명 누락: {', '.join(missing)}")
    return user_id, user_pw


def _safe_get_target(purpose: str) -> Optional[TelegramTarget]:
    """텔레그램 타깃 조회. 누락이면 경고만 남기고 None 반환."""
    try:
        return get_telegram_target("dailyservice", purpose)
    except KeyError as e:
        LOG.warning("Telegram 타깃 조회 실패(purpose=%s): %s", purpose, e)
        return None


def main() -> int:
    """DailyService 모니터 진입점. argparse로 ``--dry-run``을 받는다."""
    parser = argparse.ArgumentParser(description="SDS daily service monitor")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="로그인/세션 확인까지만 수행하고 카운트 파싱·알림은 생략한다.",
    )
    args = parser.parse_args()
    dry_run = bool(args.dry_run)

    config.ensure_dirs()

    stage = "init"
    start_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    LOG.info("[START] daily_service run at %s dry_run=%s", start_ts, dry_run)

    target_heartbeat = None if dry_run else _safe_get_target("heartbeat")

    # 매 실행 시작에 heartbeat 전송 (원본 정책).
    if dry_run:
        LOG.info("[DRY-RUN] heartbeat 전송 생략")
    elif target_heartbeat is not None:
        send_heartbeat(target_heartbeat, source="daily_service")

    try:
        with sync_browser(
            headless=config.get_headless("daily_service"),
            storage_state=STATE_AUTH if STATE_AUTH.exists() else None,
        ) as (_browser, context, page):

            stage = "ensure_logged_in"
            LOG.info("[STAGE] %s", stage)

            user_id, user_pw = _read_credentials()

            # 1) 세션으로 BASE_URL 접속 시도.
            page.goto(D.BASE_URL, wait_until="domcontentloaded")

            if is_login_page(page):
                # 2) 세션 무효/만료: 새 컨텍스트가 더 안전한 것 같지만,
                # 같은 컨텍스트에서 다시 로그인해도 storage_state로 덮어쓰면
                # 충분하다(원본도 새 컨텍스트로 갈아끼웠으나, common.browser는
                # 단일 컨텍스트 패턴을 권장한다 — storage_state 덮어쓰기로 동일
                # 효과). 안전을 위해 쿠키만 비우고 다시 로그인한다.
                LOG.warning("세션 무효/만료 -> 재로그인 시도")
                try:
                    context.clear_cookies()
                except Exception as e:
                    LOG.warning("쿠키 초기화 실패(무시하고 진행): %r", e)
                do_login(page, user_id, user_pw)
                page.goto(D.BASE_URL, wait_until="networkidle")
                save_storage_state(context, STATE_AUTH)
                LOG.info("세션 갱신 저장: %s", STATE_AUTH)
            else:
                LOG.info("세션 유효 (로그인 불필요)")

            if dry_run:
                LOG.info("[DRY-RUN] 로그인까지 확인 완료, 카운트 파싱/알림 생략")
                return 0

            stage = "check_abnormal"
            LOG.info("[STAGE] %s", stage)
            abnormal_count = check_abnormal_service_count(page)

            # 비정상(>0)일 때만 Pushover 긴급.
            if abnormal_count > 0:
                send_pushover_emergency(
                    title="🚨 비정상 서비스 발생",
                    message=(
                        f"비정상 서비스가 {abnormal_count}건 감지되었습니다.\n"
                        f"Time: {start_ts}"
                    ),
                )

            LOG.info("[OK] 실행 완료")
            return 0

    except Exception as e:
        # 파일 로그에 traceback까지 남긴다.
        LOG.exception("[FAIL] stage=%s err=%r", stage, e)

        if dry_run:
            LOG.info("[DRY-RUN] 실패해도 알림은 생략")
            return 1

        send_pushover_emergency(
            title="🚨 SDS 모니터링 실패",
            message=f"Stage: {stage}\nError: {e}\nTime: {start_ts}",
        )
        return 1

    finally:
        LOG.info("[END] daily_service run finished")


if __name__ == "__main__":
    raise SystemExit(main())
