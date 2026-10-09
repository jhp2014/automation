"""Jennifer 메인 대시보드 4개 → 2×2 합성(1920×1080) 결과물 확인용 테스트.

업로드·알림 없이 "로그인 → 메인 대시보드 캡처 → 2×2 합성 → 파일 저장"만 한다.

브라우저 헤더(탭·주소창)까지 찍기 위해 headless 가 아니라 설치된 Chrome 을 실제
창으로 띄운다. 단, 창은 모든 모니터 바깥 좌표에 두므로 화면에는 보이지 않고,
``PrintWindow`` 로 그 창만 찍는다(다른 창에 가려져 있어도 됨, 최소화는 안 됨).
Windows 로그인 세션(데스크탑)은 필요하다.

    - 창 크기: 헤더 포함 보이는 영역이 정확히 960×540 (2×2 합성 시 1920×1080).
    - 렌더 배율: --force-device-scale-factor=1 로 Windows 배율(125% 등)과 무관하게
      픽셀 = CSS px 로 고정한다.
    - 가려진 창 렌더 중단(크롬 occlusion 최적화)과 자동화 안내줄을 끈다.

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
import ctypes
import json
import time
from contextlib import contextmanager
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

import win32con
import win32gui
import win32process
import win32ui
from PIL import Image
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from common import config
from common.logging import get_logger
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

# 브라우저 창 hwnd 탐색 대기.
FIND_WINDOW_TIMEOUT_SEC = 10.0

# 모든 모니터 바깥에 둘 때 가상 데스크톱 왼쪽 끝에서 더 띄울 여백(px).
OFFSCREEN_GAP_PX = 200

# 설치된 Chrome 으로 띄운다(평소 보는 크롬 헤더와 같게).
BROWSER_CHANNEL = "chrome"

CHROME_ARGS = [
    # Windows 배율과 무관하게 1px = 1CSS px (창 960×540 이 그대로 960×540 으로 찍힘).
    "--force-device-scale-factor=1",
    # 가려지거나 화면 밖인 창도 계속 그리게 한다.
    "--disable-features=CalculateNativeWinOcclusion",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
]
# "자동화된 테스트 소프트웨어에 의해 제어되고 있습니다" 안내줄 제거.
IGNORE_DEFAULT_ARGS = ["--enable-automation"]

# PrintWindow: DWM 합성 내용까지 그리게 하는 플래그(크롬 등 GPU 렌더 창 필수).
PW_RENDERFULLCONTENT = 0x00000002
# DwmGetWindowAttribute: 보이지 않는 리사이즈 테두리를 뺀 실제 창 영역.
DWMWA_EXTENDED_FRAME_BOUNDS = 9

OUT_ROOT = config.BASE_DIR / "screenshots" / "jennifer_snapshot" / "test"

LOG = get_logger("tools.jennifer_snapshot_test", "jennifer_snapshot_test.log")


# ---------------------------------------------------------------------------
# Win32: DPI / 창 탐색 / 위치 / 캡처
# ---------------------------------------------------------------------------

def _set_dpi_aware() -> None:
    """좌표·크기를 물리 픽셀로 다루도록 프로세스 DPI 인식을 켠다."""
    try:
        # -4 = DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass


def _frame_bounds(hwnd: int) -> Tuple[int, int, int, int]:
    """DWM 기준 실제로 보이는 창 영역(left, top, right, bottom)."""
    rect = wintypes.RECT()
    hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
        wintypes.HWND(hwnd),
        ctypes.c_uint(DWMWA_EXTENDED_FRAME_BOUNDS),
        ctypes.byref(rect),
        ctypes.sizeof(rect),
    )
    if hr != 0:
        return win32gui.GetWindowRect(hwnd)
    return (rect.left, rect.top, rect.right, rect.bottom)


def _browser_pid(browser: Browser) -> int:
    """CDP 로 브라우저(메인) 프로세스 PID 를 얻는다."""
    cdp = browser.new_browser_cdp_session()
    try:
        info = cdp.send("SystemInfo.getProcessInfo")
    finally:
        cdp.detach()
    for proc in info.get("processInfo", []):
        if proc.get("type") == "browser":
            return int(proc["id"])
    raise RuntimeError(f"브라우저 PID 를 찾지 못함: {info}")


def _find_browser_window(pid: int, title_hint: str) -> int:
    """pid 소유의 크롬 최상위 창 hwnd. 여러 개면 제목에 title_hint 가 든 창."""
    deadline = time.monotonic() + FIND_WINDOW_TIMEOUT_SEC
    while True:
        found: List[Tuple[int, str]] = []

        def cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return True
            if win32gui.GetClassName(hwnd) != "Chrome_WidgetWin_1":
                return True
            _, wpid = win32process.GetWindowThreadProcessId(hwnd)
            title = win32gui.GetWindowText(hwnd)
            if wpid == pid and title.strip():
                found.append((hwnd, title))
            return True

        win32gui.EnumWindows(cb, None)
        if len(found) == 1:
            return found[0][0]
        if title_hint:
            matched = [h for h, t in found if title_hint in t]
            if len(matched) == 1:
                return matched[0]
        if time.monotonic() > deadline:
            raise RuntimeError(
                f"브라우저 창 탐색 실패(pid={pid}, hint={title_hint!r}, 후보={found})"
            )
        time.sleep(0.2)


def _offscreen_origin() -> Tuple[int, int]:
    """모든 모니터를 합친 가상 데스크톱의 왼쪽 바깥 좌표."""
    user32 = ctypes.windll.user32
    vx = user32.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
    vy = user32.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
    return (vx - TILE_SIZE[0] - OFFSCREEN_GAP_PX, vy)


def _place_window(hwnd: int, origin: Tuple[int, int]) -> None:
    """보이는 창 영역이 정확히 TILE_SIZE 가 되도록 크기·위치를 맞춘다.

    GetWindowRect 에는 보이지 않는 리사이즈 테두리가 포함되므로, DWM 실제 영역과의
    차이만큼 보정한다. 활성화·z-order 변경은 하지 않는다(포커스 도용 방지).
    """
    flags = win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER
    if win32gui.IsIconic(hwnd) or win32gui.GetWindowPlacement(hwnd)[1] == win32con.SW_SHOWMAXIMIZED:
        win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)

    tw, th = TILE_SIZE
    x, y = origin
    win32gui.SetWindowPos(hwnd, 0, x, y, tw, th, flags)
    for _ in range(3):
        wl, wt, wr, wb = win32gui.GetWindowRect(hwnd)
        fl, ft, fr, fb = _frame_bounds(hwnd)
        extra_w = (wr - wl) - (fr - fl)
        extra_h = (wb - wt) - (fb - ft)
        if (fr - fl, fb - ft) == (tw, th):
            break
        win32gui.SetWindowPos(
            hwnd, 0, x - (fl - wl), y - (ft - wt), tw + extra_w, th + extra_h, flags
        )
        time.sleep(0.2)
    LOG.info("창 배치: hwnd=0x%08X frame=%s", hwnd, _frame_bounds(hwnd))


def _print_window(hwnd: int) -> Image.Image:
    """PrintWindow 로 창 전체를 그려 받은 뒤 실제 보이는 영역만 잘라 반환한다."""
    wl, wt, wr, wb = win32gui.GetWindowRect(hwnd)
    w, h = wr - wl, wb - wt

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    src_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    mem_dc = src_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    try:
        bmp.CreateCompatibleBitmap(src_dc, w, h)
        mem_dc.SelectObject(bmp)
        ok = ctypes.windll.user32.PrintWindow(
            wintypes.HWND(hwnd), wintypes.HDC(mem_dc.GetSafeHdc()), PW_RENDERFULLCONTENT
        )
        if not ok:
            raise RuntimeError(f"PrintWindow 실패: hwnd=0x{hwnd:08X}")
        bits = bmp.GetBitmapBits(True)
        img = Image.frombuffer("RGB", (w, h), bits, "raw", "BGRX", 0, 1)
    finally:
        win32gui.DeleteObject(bmp.GetHandle())
        mem_dc.DeleteDC()
        src_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)

    fl, ft, fr, fb = _frame_bounds(hwnd)
    return img.crop((fl - wl, ft - wt, fr - wl, fb - wt))


# ---------------------------------------------------------------------------
# 브라우저
# ---------------------------------------------------------------------------

def _usable_state(path: Path) -> Optional[Path]:
    """세션 파일이 JSON 으로 읽히면 경로, 아니면 None(새 컨텍스트)."""
    try:
        json.loads(path.read_text(encoding="utf-8"))
        return path
    except Exception:
        return None


@contextmanager
def _headful_chrome(
    site: Dict[str, str],
    origin: Tuple[int, int],
) -> Iterator[Tuple[Browser, BrowserContext, Page]]:
    """설치된 Chrome 을 실제 창으로 띄운다(viewport 에뮬레이션 없음 = 창 크기 그대로)."""
    state = _usable_state(session_path(site["name"]))
    pw = sync_playwright().start()
    browser: Optional[Browser] = None
    try:
        browser = pw.chromium.launch(
            channel=BROWSER_CHANNEL,
            headless=False,
            chromium_sandbox=True,  # --no-sandbox 경고 안내줄 방지
            ignore_default_args=IGNORE_DEFAULT_ARGS,
            args=CHROME_ARGS + [
                f"--window-position={origin[0]},{origin[1]}",
                f"--window-size={TILE_SIZE[0]},{TILE_SIZE[1]}",
            ],
        )
        context = browser.new_context(
            no_viewport=True,
            storage_state=str(state) if state else None,
            ignore_https_errors=bool(site.get("ignore_https_errors", False)),
        )
        page = context.new_page()
        yield browser, context, page
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception as e:
                LOG.warning("browser.close() 실패(무시): %r", e)
        try:
            pw.stop()
        except Exception as e:
            LOG.warning("playwright.stop() 실패(무시): %r", e)


def _snap_one_site(
    index: int,
    site: Dict[str, str],
    wait_sec: float,
    origin: Tuple[int, int],
    out_dir: Path,
) -> Path:
    """사이트 하나에 로그인해 헤더 포함 창 캡처(960×540)를 저장한다."""
    name = site["name"]
    with _headful_chrome(site, origin) as (browser, context, page):
        pid = _browser_pid(browser)
        _place_window(_find_browser_window(pid, ""), origin)

        LOG.info("[%s] 로그인", name)
        page = ensure_login(context, page, site, LOG)

        canvas = page.locator(J.CANVAS_BY_TYPE[site["login_type"]]).first
        canvas.wait_for(state="visible", timeout=CANVAS_VISIBLE_TIMEOUT_MS)

        # 로그인 폴백 중 새 창이 생겼을 수 있으므로 최종 페이지 창을 다시 잡아 배치.
        hwnd = _find_browser_window(pid, page.title())
        _place_window(hwnd, origin)
        page.wait_for_timeout(RESIZE_WAIT_MS)

        LOG.info("[%s] 대시보드 표시됨 -> 차트 채움 대기 %.0fs", name, wait_sec)
        page.wait_for_timeout(int(wait_sec * 1000))

        img = _print_window(hwnd)
        path = out_dir / f"{index}_{name}.png"
        img.save(path, format="PNG")
        LOG.info("[%s] 캡처 %dx%d -> %s", name, img.width, img.height, path)
    return path


def _compose(tile_paths: List[Path], out_path: Path) -> None:
    """4장을 칸 크기로 맞춰 2×2(좌상·우상·좌하·우하)로 붙여 저장한다."""
    canvas = Image.new("RGB", COMPOSITE_SIZE, "black")
    tw, th = TILE_SIZE
    for i, p in enumerate(tile_paths):
        with Image.open(p) as img:
            tile = img.convert("RGB")
            if tile.size != TILE_SIZE:
                LOG.warning("칸 크기 불일치 -> 리사이즈: %s %s", p.name, tile.size)
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
        "--on-screen",
        action="store_true",
        help="디버그용: 창을 모니터 밖이 아니라 주 모니터 좌상단(0,0)에 띄운다.",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    enable_utf8_console()
    _set_dpi_aware()
    args = _parse_args(argv)

    config.ensure_dirs()
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    out_dir = OUT_ROOT / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    origin = (0, 0) if args.on_screen else _offscreen_origin()
    LOG.info("[START] wait_sec=%.0f origin=%s out=%s", args.wait_sec, origin, out_dir)

    try:
        sites = load_sites()
        if len(sites) != 4:
            raise RuntimeError(f"2×2 합성은 사이트 4개가 필요합니다(현재 {len(sites)}개)")

        tiles = [
            _snap_one_site(i + 1, site, args.wait_sec, origin, out_dir)
            for i, site in enumerate(sites)
        ]
        _compose(tiles, out_dir / "composite.png")
    except Exception as e:
        LOG.exception("[FAIL] %r", e)
        return 1

    LOG.info("[OK] 완료: %s", out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
