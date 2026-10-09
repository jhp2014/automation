"""좌측 모니터 캡처를 화면 밖 창 방식으로 재현하는 결과물 확인용 테스트.

업로드·알림 없이 "각 사이트 접속 → 화면 밖 창 캡처 → 좌측 모니터 배치대로 합성 →
파일 저장"만 한다. 화면 밖 창 캡처는 :mod:`common.offscreen` (헤더 포함).

배치(1920×1080, 좌측 모니터 FancyZones 를 10px 단위로 맞춤)::

    +--------------+------------------------------+
    | 1 WhatsUp    | 3 Zenius                     |
    |   560×530    |   1360×700                   |
    +--------------+                              |
    | 2 ETL        +------------------------------+
    |   560×550    | 4 Dashboard 1360×380         |
    +--------------+------------------------------+

각 칸은 로그인 직후 기본 화면. 로그인은 기존 job 의 함수를 그대로 쓴다
(Zenius: jobs.zenius / Dashboard: jobs.daily_service / WhatsUp: HTTP 인증).
결과물을 한눈에 보기 위해 한 칸이 실패해도 나머지는 계속 찍고, 실패 칸은 회색으로
채운 뒤 exit 1 로 끝난다(정식 job 에서는 하나라도 실패하면 전체 실패로 바꾼다).

사용 예::

    .venv\\Scripts\\python.exe -m tools.capture_offscreen_test
    .venv\\Scripts\\python.exe -m tools.capture_offscreen_test --wait-sec 10
    .venv\\Scripts\\python.exe -m tools.capture_offscreen_test --only ETL --on-screen

산출물: ``screenshots/capture_offscreen/test/<YYYYmmdd_HHMMSS>/``
    - ``<n>_<이름>.png`` : 칸별 창 캡처
    - ``composite.png``  : 합성(1920×1080)
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from PIL import Image
from playwright.sync_api import BrowserContext, Page

from common import config
from common.browser import save_storage_state
from common.logging import get_logger
from common.offscreen import (
    Point,
    Size,
    compose,
    headful_chrome,
    offscreen_origin,
    place_browser_window,
    print_window,
    set_dpi_aware,
)
from jobs.daily_service import __main__ as daily_service
from jobs.whatsup import __main__ as whatsup
from jobs.zenius import __main__ as zenius
from site_selectors import daily_service as D
from site_selectors import etl as E
from site_selectors import whatsup as W

from . import enable_utf8_console


COMPOSITE_SIZE = (1920, 1080)

# 창 크기/위치 변경 후 레이아웃 재배치 대기.
RESIZE_WAIT_MS = 2000

OUT_ROOT = config.BASE_DIR / "screenshots" / "capture_offscreen" / "test"

LOG = get_logger("tools.capture_offscreen_test", "capture_offscreen_test.log")


# ---------------------------------------------------------------------------
# 사이트별 진입
# ---------------------------------------------------------------------------

def _open_whatsup(context: BrowserContext, page: Page) -> Page:
    # HTTP 인증은 컨텍스트의 http_credentials 로 처리된다.
    page.goto(W.BASE_URL, wait_until="load")
    return page


def _open_etl(context: BrowserContext, page: Page) -> Page:
    # 표는 페이지 로드 후 get_monitor.jsp 응답으로 채워진다.
    page.goto(E.MONITOR_URL, wait_until="networkidle")
    return page


def _open_zenius(context: BrowserContext, page: Page) -> Page:
    user_id, user_pw = zenius._read_credentials()
    zenius.ensure_logged_in_and_save(context, page, user_id, user_pw)
    return page


def _open_dashboard(context: BrowserContext, page: Page) -> Page:
    page.goto(D.BASE_URL, wait_until="domcontentloaded")
    if daily_service.is_login_page(page):
        LOG.info("[Dashboard] 세션 무효/만료 -> 재로그인")
        context.clear_cookies()
        user_id, user_pw = daily_service._read_credentials()
        daily_service.do_login(page, user_id, user_pw)
        page.goto(D.BASE_URL, wait_until="networkidle")
        save_storage_state(context, daily_service.STATE_AUTH)
    else:
        page.wait_for_load_state("networkidle")
    return page


def _whatsup_credentials() -> Dict[str, str]:
    user_id, user_pw = whatsup._read_credentials()
    return {"username": user_id, "password": user_pw}


@dataclass(frozen=True)
class Tile:
    name: str
    pos: Point
    size: Size
    open: Callable[[BrowserContext, Page], Page]
    storage_state: Optional[Path] = None
    http_credentials: Optional[Callable[[], Dict[str, str]]] = None


TILES: List[Tile] = [
    Tile("WhatsUp", (0, 0), (560, 530), _open_whatsup,
         http_credentials=_whatsup_credentials),
    Tile("ETL", (0, 530), (560, 550), _open_etl),
    Tile("Zenius", (560, 0), (1360, 700), _open_zenius,
         storage_state=zenius.STATE_AUTH),
    Tile("Dashboard", (560, 700), (1360, 380), _open_dashboard,
         storage_state=daily_service.STATE_AUTH),
]


# ---------------------------------------------------------------------------
# 캡처
# ---------------------------------------------------------------------------

def _snap(tile: Tile, wait_sec: float, on_screen: bool) -> Image.Image:
    """칸 하나를 화면 밖 창으로 띄워 접속하고 헤더 포함 캡처를 돌려준다."""
    origin = (0, 0) if on_screen else offscreen_origin(tile.size)
    with headful_chrome(
        size=tile.size,
        origin=origin,
        storage_state=tile.storage_state,
        http_credentials=tile.http_credentials() if tile.http_credentials else None,
    ) as (browser, context, page):
        place_browser_window(browser, page, origin, tile.size)
        LOG.info("[%s] 접속", tile.name)
        page = tile.open(context, page)

        hwnd = place_browser_window(browser, page, origin, tile.size)
        page.wait_for_timeout(RESIZE_WAIT_MS)
        LOG.info("[%s] 화면 표시됨 -> 대기 %.0fs", tile.name, wait_sec)
        page.wait_for_timeout(int(wait_sec * 1000))
        return print_window(hwnd)


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="좌측 모니터 캡처 화면 밖 방식 테스트(업로드/알림 없음)"
    )
    parser.add_argument(
        "--wait-sec",
        type=float,
        default=5.0,
        help="화면 표시 후 캡처 전까지 대기 초(기본 5).",
    )
    parser.add_argument(
        "--only",
        choices=[t.name for t in TILES],
        action="append",
        help="지정한 칸만 찍는다(여러 번 가능). 나머지 칸은 회색.",
    )
    parser.add_argument(
        "--on-screen",
        action="store_true",
        help="디버그용: 창을 모니터 밖이 아니라 주 모니터 좌상단(0,0)에 띄운다.",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    enable_utf8_console()
    set_dpi_aware()
    args = _parse_args(argv)

    config.ensure_dirs()
    out_dir = OUT_ROOT / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    LOG.info("[START] wait_sec=%.0f only=%s out=%s", args.wait_sec, args.only, out_dir)

    failed: List[str] = []
    tiles: List[Tuple[Image.Image, Point, Size]] = []
    for i, tile in enumerate(TILES, start=1):
        if args.only and tile.name not in args.only:
            tiles.append((Image.new("RGB", tile.size, "gray"), tile.pos, tile.size))
            continue
        try:
            img = _snap(tile, args.wait_sec, args.on_screen)
            path = out_dir / f"{i}_{tile.name}.png"
            img.save(path, format="PNG")
            LOG.info("[%s] 캡처 %dx%d -> %s", tile.name, img.width, img.height, path)
        except Exception as e:
            LOG.exception("[%s] 실패: %r", tile.name, e)
            failed.append(tile.name)
            img = Image.new("RGB", tile.size, "gray")
        tiles.append((img, tile.pos, tile.size))

    composite = out_dir / "composite.png"
    compose(tiles, COMPOSITE_SIZE).save(composite, format="PNG")
    LOG.info("합성 저장: %s", composite)

    if failed:
        LOG.error("[FAIL] 실패 칸: %s (회색으로 채움)", failed)
        return 1
    LOG.info("[OK] 완료: %s", out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
