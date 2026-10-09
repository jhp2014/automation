"""Jennifer 메인 대시보드 4개 → 2×2 합성(1920×1080) 결과물 확인용 테스트.

업로드·알림 없이 "로그인 → 메인 대시보드 캡처 → 2×2 합성 → 파일 저장"만 한다.

브라우저 헤더까지 찍기 위해 화면 밖 실제 Chrome 창을 ``PrintWindow`` 로 찍는다
(:mod:`common.offscreen`). 창은 헤더 포함 960×540 이고, 2×2 로 붙이면 1920×1080.

칸 배치는 config/jennifer_sites.json 순서대로 좌상 → 우상 → 좌하 → 우하.
하나라도 실패하면 즉시 중단하고 exit 1 (테스트라 Pushover 는 보내지 않는다).

로그인/세션은 jennifer job 과 같은 :mod:`jobs.jennifer.login` 을 쓴다.

사용 예::

    .venv\\Scripts\\python.exe -m tools.jennifer_snapshot_test
    .venv\\Scripts\\python.exe -m tools.jennifer_snapshot_test --wait-sec 30
    .venv\\Scripts\\python.exe -m tools.jennifer_snapshot_test --on-screen

산출물: ``screenshots/jennifer_snapshot/test/<YYYYmmdd_HHMMSS>/``
    - ``<n>_<사이트명>.png`` : 사이트별 창 캡처(960×540)
    - ``composite.png``      : 2×2 합성(1920×1080)
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict

from PIL import Image

from common import config
from common.logging import get_logger
from common.offscreen import (
    Point,
    compose,
    headful_chrome,
    offscreen_origin,
    place_browser_window,
    print_window,
    set_dpi_aware,
)
from jobs.jennifer.login import SESSION_DIR, ensure_login, load_sites, session_path
from site_selectors import jennifer as J

from . import enable_utf8_console


# 최종 합성 크기와 칸(=창) 크기.
COMPOSITE_SIZE = (1920, 1080)
TILE_SIZE = (COMPOSITE_SIZE[0] // 2, COMPOSITE_SIZE[1] // 2)

# 메인 대시보드 캔버스 visible 대기(jennifer job 과 동일 30초).
CANVAS_VISIBLE_TIMEOUT_MS = 30000

# 창 크기/위치 변경 후 레이아웃 재배치 대기.
RESIZE_WAIT_MS = 2000

OUT_ROOT = config.BASE_DIR / "screenshots" / "jennifer_snapshot" / "test"

LOG = get_logger("tools.jennifer_snapshot_test", "jennifer_snapshot_test.log")


def _snap_one_site(
    index: int,
    site: Dict[str, str],
    wait_sec: float,
    origin: Point,
    out_dir: Path,
) -> Path:
    """사이트 하나에 로그인해 헤더 포함 창 캡처(960×540)를 저장한다."""
    name = site["name"]
    with headful_chrome(
        size=TILE_SIZE,
        origin=origin,
        storage_state=session_path(name),
        ignore_https_errors=bool(site.get("ignore_https_errors", False)),
    ) as (browser, context, page):
        place_browser_window(browser, page, origin, TILE_SIZE)

        LOG.info("[%s] 로그인", name)
        page = ensure_login(context, page, site, LOG)

        canvas = page.locator(J.CANVAS_BY_TYPE[site["login_type"]]).first
        canvas.wait_for(state="visible", timeout=CANVAS_VISIBLE_TIMEOUT_MS)

        # 로그인 폴백 중 새 창이 생겼을 수 있으므로 최종 페이지 창을 다시 잡아 배치.
        hwnd = place_browser_window(browser, page, origin, TILE_SIZE)
        page.wait_for_timeout(RESIZE_WAIT_MS)

        LOG.info("[%s] 대시보드 표시됨 -> 차트 채움 대기 %.0fs", name, wait_sec)
        page.wait_for_timeout(int(wait_sec * 1000))

        img = print_window(hwnd)
        path = out_dir / f"{index}_{name}.png"
        img.save(path, format="PNG")
        LOG.info("[%s] 캡처 %dx%d -> %s", name, img.width, img.height, path)
    return path


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Jennifer 메인 대시보드 4개 2×2 합성 테스트(업로드/알림 없음)"
    )
    parser.add_argument(
        "--wait-sec",
        type=float,
        default=10.0,
        help="대시보드 표시 후 캡처 전까지 차트 채움 대기 초(기본 10).",
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
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    out_dir = OUT_ROOT / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    origin = (0, 0) if args.on_screen else offscreen_origin(TILE_SIZE)
    LOG.info("[START] wait_sec=%.0f origin=%s out=%s", args.wait_sec, origin, out_dir)

    try:
        sites = load_sites()
        if len(sites) != 4:
            raise RuntimeError(f"2×2 합성은 사이트 4개가 필요합니다(현재 {len(sites)}개)")

        paths = [
            _snap_one_site(i + 1, site, args.wait_sec, origin, out_dir)
            for i, site in enumerate(sites)
        ]
        tw, th = TILE_SIZE
        tiles = [
            (Image.open(p), ((i % 2) * tw, (i // 2) * th), TILE_SIZE)
            for i, p in enumerate(paths)
        ]
        composite = out_dir / "composite.png"
        compose(tiles, COMPOSITE_SIZE).save(composite, format="PNG")
        LOG.info("합성 저장: %s", composite)
    except Exception as e:
        LOG.exception("[FAIL] %r", e)
        return 1

    LOG.info("[OK] 완료: %s", out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
