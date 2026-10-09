"""Jennifer 메인 대시보드 4개 → 2×2 합성(1920×1080) 결과물 확인용 테스트.

업로드·알림 없이 "로그인 → 메인 대시보드 캡처 → 2×2 합성 → 파일 저장"만 한다.
칸 하나(960×540)를 만드는 방식 3가지를 한 번에 만들어 비교한다:

    1920x1080 : 1920×1080 으로 렌더 후 960×540 으로 축소 (화면 구성 그대로, 글자 작음)
    1280x720  : 1280×720 으로 렌더 후 960×540 으로 축소 (절충)
    960x540   : 처음부터 960×540 으로 렌더 (글자 원래 크기, 레이아웃 재배치 가능)

사이트마다 로그인은 1회만 하고, 큰 해상도부터 viewport 를 줄여가며 찍는다.
칸 배치는 config/jennifer_sites.json 순서대로 좌상 → 우상 → 좌하 → 우하.
하나라도 실패하면 즉시 중단하고 exit 1 (테스트라 Pushover 는 보내지 않는다).

로그인/세션은 jennifer job 과 같은 :mod:`jobs.jennifer.login` 을 쓴다.

사용 예::

    python -m tools.jennifer_snapshot_test
    python -m tools.jennifer_snapshot_test --wait-sec 30
    python -m tools.jennifer_snapshot_test --no-headless

산출물: ``screenshots/jennifer_snapshot/test/<YYYYmmdd_HHMMSS>/``
    - ``<n>_<사이트명>_<W>x<H>.png`` : 사이트별 원본
    - ``composite_<W>x<H>.png``      : 렌더 해상도별 2×2 합성(모두 1920×1080)
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from PIL import Image

from common import config
from common.browser import sync_browser
from common.logging import get_logger
from jobs.jennifer.login import SESSION_DIR, ensure_login, load_sites, session_path
from site_selectors import jennifer as J

from . import enable_utf8_console


# 렌더 해상도 후보(큰 것부터 — 로그인 직후 가장 큰 화면으로 먼저 찍는다).
RENDER_SIZES: List[Tuple[int, int]] = [(1920, 1080), (1280, 720), (960, 540)]

# 최종 합성 크기와 칸 크기.
COMPOSITE_SIZE = (1920, 1080)
TILE_SIZE = (COMPOSITE_SIZE[0] // 2, COMPOSITE_SIZE[1] // 2)

# 메인 대시보드 캔버스 visible 대기(jennifer job 과 동일 30초).
CANVAS_VISIBLE_TIMEOUT_MS = 30000

# viewport 변경 후 차트 재배치/재렌더 대기.
RESIZE_WAIT_MS = 3000

OUT_ROOT = config.BASE_DIR / "screenshots" / "jennifer_snapshot" / "test"

LOG = get_logger("tools.jennifer_snapshot_test", "jennifer_snapshot_test.log")


def _snap_one_site(
    index: int,
    site: Dict[str, str],
    headless: bool,
    wait_sec: float,
    out_dir: Path,
) -> Dict[Tuple[int, int], Path]:
    """사이트 하나에 로그인해 렌더 해상도별 메인 대시보드 스크린샷을 저장한다.

    Returns:
        ``{(w, h): 저장 경로}``.
    """
    name = site["name"]
    state_path = session_path(name)
    first_w, first_h = RENDER_SIZES[0]

    shots: Dict[Tuple[int, int], Path] = {}
    with sync_browser(
        headless=headless,
        storage_state=state_path if state_path.exists() else None,
        viewport={"width": first_w, "height": first_h},
        ignore_https_errors=bool(site.get("ignore_https_errors", False)),
    ) as (_browser, context, page):
        LOG.info("[%s] 로그인", name)
        page = ensure_login(context, page, site, LOG)

        canvas = page.locator(J.CANVAS_BY_TYPE[site["login_type"]]).first
        canvas.wait_for(state="visible", timeout=CANVAS_VISIBLE_TIMEOUT_MS)
        LOG.info("[%s] 대시보드 표시됨 -> 차트 채움 대기 %.0fs", name, wait_sec)
        page.wait_for_timeout(int(wait_sec * 1000))

        for i, (w, h) in enumerate(RENDER_SIZES):
            if i > 0:
                page.set_viewport_size({"width": w, "height": h})
                page.wait_for_timeout(RESIZE_WAIT_MS)
            path = out_dir / f"{index}_{name}_{w}x{h}.png"
            page.screenshot(path=str(path))
            LOG.info("[%s] 캡처 %dx%d -> %s", name, w, h, path)
            shots[(w, h)] = path
    return shots


def _compose(tile_paths: List[Path], out_path: Path) -> None:
    """4장을 칸 크기로 맞춰 2×2(좌상·우상·좌하·우하)로 붙여 저장한다."""
    canvas = Image.new("RGB", COMPOSITE_SIZE, "black")
    tw, th = TILE_SIZE
    for i, p in enumerate(tile_paths):
        with Image.open(p) as img:
            tile = img.convert("RGB")
            if tile.size != TILE_SIZE:
                tile = tile.resize(TILE_SIZE, Image.Resampling.LANCZOS)
        canvas.paste(tile, ((i % 2) * tw, (i // 2) * th))
    canvas.save(out_path, format="PNG")
    LOG.info("합성 저장: %s", out_path)


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
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="브라우저 헤드리스 여부. 미지정 시 settings.yaml 의 jennifer 값을 따른다.",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    enable_utf8_console()
    args = _parse_args(argv)
    headless = (
        args.headless
        if args.headless is not None
        else config.get_headless("jennifer")
    )

    config.ensure_dirs()
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    out_dir = OUT_ROOT / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    LOG.info("[START] headless=%s wait_sec=%.0f out=%s", headless, args.wait_sec, out_dir)

    try:
        sites = load_sites()
        if len(sites) != 4:
            raise RuntimeError(f"2×2 합성은 사이트 4개가 필요합니다(현재 {len(sites)}개)")

        per_site = [
            _snap_one_site(i + 1, site, headless, args.wait_sec, out_dir)
            for i, site in enumerate(sites)
        ]
        for w, h in RENDER_SIZES:
            _compose(
                [shots[(w, h)] for shots in per_site],
                out_dir / f"composite_{w}x{h}.png",
            )
    except Exception as e:
        LOG.exception("[FAIL] %r", e)
        return 1

    LOG.info("[OK] 완료: %s", out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
