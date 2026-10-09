"""화면 밖(off-screen) 실제 Chrome 창 캡처 공용 헬퍼 (Windows 전용).

브라우저 헤더(탭·주소창)까지 찍기 위해 headless 가 아니라 설치된 Chrome 을 실제
창으로 띄운다. 단, 창은 모든 모니터 바깥 좌표에 두므로 화면에는 보이지 않고,
``PrintWindow`` 로 그 창만 찍는다(다른 창에 가려져 있어도 됨, 최소화는 안 됨).
Windows 로그인 세션(데스크탑)은 필요하다.

    - 창 크기: 헤더 포함 "보이는 영역" 이 정확히 지정 크기(px)가 되도록 맞춘다.
    - 렌더 배율: --force-device-scale-factor=1 로 Windows 배율(125% 등)과 무관하게
      픽셀 = CSS px 로 고정한다.
    - 가려진 창 렌더 중단(크롬 occlusion 최적화)과 자동화 안내줄을 끈다.

사용 예::

    set_dpi_aware()                      # 프로세스 시작 시 1회
    size = (960, 540)
    origin = offscreen_origin(size)
    with headful_chrome(size=size, origin=origin) as (browser, context, page):
        place_browser_window(browser, page, origin, size)
        ... 로그인/페이지 이동 ...
        hwnd = place_browser_window(browser, page, origin, size)  # 최종 창 재배치
        page.wait_for_timeout(2000)
        img = print_window(hwnd)

이 모듈은 ``win32gui``/``win32ui`` 등 pywin32 를 import 시점에 로드한다.
"""

from __future__ import annotations

import ctypes
import json
import time
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple, Union

import win32con
import win32gui
import win32process
import win32ui
from PIL import Image
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from .logging import get_logger


_logger = get_logger("common.offscreen")

Size = Tuple[int, int]
Point = Tuple[int, int]

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

# 브라우저 창 hwnd 탐색 대기.
FIND_WINDOW_TIMEOUT_SEC = 10.0

# 모든 모니터 바깥에 둘 때 가상 데스크톱 왼쪽 끝에서 더 띄울 여백(px).
OFFSCREEN_GAP_PX = 200

# PrintWindow: DWM 합성 내용까지 그리게 하는 플래그(크롬 등 GPU 렌더 창 필수).
PW_RENDERFULLCONTENT = 0x00000002
# DwmGetWindowAttribute: 보이지 않는 리사이즈 테두리를 뺀 실제 창 영역.
DWMWA_EXTENDED_FRAME_BOUNDS = 9


# ---------------------------------------------------------------------------
# Win32: DPI / 창 탐색 / 위치 / 캡처
# ---------------------------------------------------------------------------

def set_dpi_aware() -> None:
    """좌표·크기를 물리 픽셀로 다루도록 프로세스 DPI 인식을 켠다(시작 시 1회)."""
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


def offscreen_origin(size: Size) -> Point:
    """모든 모니터를 합친 가상 데스크톱의 왼쪽 바깥 좌표(창 크기만큼 비켜서)."""
    user32 = ctypes.windll.user32
    vx = user32.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
    vy = user32.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
    return (vx - size[0] - OFFSCREEN_GAP_PX, vy)


def _place_window(hwnd: int, origin: Point, size: Size) -> None:
    """보이는 창 영역이 정확히 size 가 되도록 크기·위치를 맞춘다.

    GetWindowRect 에는 보이지 않는 리사이즈 테두리가 포함되므로, DWM 실제 영역과의
    차이만큼 보정한다. 활성화·z-order 변경은 하지 않는다(포커스 도용 방지).
    """
    flags = win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER
    if win32gui.IsIconic(hwnd) or win32gui.GetWindowPlacement(hwnd)[1] == win32con.SW_SHOWMAXIMIZED:
        win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)

    tw, th = size
    x, y = origin
    win32gui.SetWindowPos(hwnd, 0, x, y, tw, th, flags)
    for _ in range(3):
        wl, wt, wr, wb = win32gui.GetWindowRect(hwnd)
        fl, ft, fr, fb = _frame_bounds(hwnd)
        if (fr - fl, fb - ft) == (tw, th):
            break
        extra_w = (wr - wl) - (fr - fl)
        extra_h = (wb - wt) - (fb - ft)
        win32gui.SetWindowPos(
            hwnd, 0, x - (fl - wl), y - (ft - wt), tw + extra_w, th + extra_h, flags
        )
        time.sleep(0.2)
    _logger.info("창 배치: hwnd=0x%08X frame=%s", hwnd, _frame_bounds(hwnd))


def place_browser_window(browser: Browser, page: Page, origin: Point, size: Size) -> int:
    """page 가 떠 있는 브라우저 창을 찾아 origin/size 로 배치하고 hwnd 를 돌려준다.

    로그인 폴백 등으로 새 창이 생길 수 있으므로 캡처 직전에 한 번 더 부른다.
    """
    pid = _browser_pid(browser)
    try:
        hint = page.title()
    except Exception:
        hint = ""
    hwnd = _find_browser_window(pid, hint)
    _place_window(hwnd, origin, size)
    return hwnd


def print_window(hwnd: int) -> Image.Image:
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

def _usable_state(path: Union[str, Path, None]) -> Optional[Path]:
    """세션 파일이 있고 JSON 으로 읽히면 경로, 아니면 None(새 컨텍스트)."""
    if path is None:
        return None
    p = Path(path)
    try:
        json.loads(p.read_text(encoding="utf-8"))
        return p
    except Exception:
        return None


@contextmanager
def headful_chrome(
    *,
    size: Size,
    origin: Point,
    storage_state: Union[str, Path, None] = None,
    ignore_https_errors: bool = False,
    http_credentials: Optional[Dict[str, str]] = None,
) -> Iterator[Tuple[Browser, BrowserContext, Page]]:
    """설치된 Chrome 을 origin 위치에 size 크기 실제 창으로 띄운다.

    viewport 에뮬레이션을 끈다(no_viewport) — 페이지 영역 = 창 크기 - 헤더.

    Args:
        size: 창 크기(헤더 포함). 띄운 뒤 :func:`place_browser_window` 로 정밀 보정.
        origin: 창 좌상단 좌표. 보통 :func:`offscreen_origin`.
        storage_state: 재사용할 세션 파일. 없거나 깨졌으면 새 컨텍스트.
        ignore_https_errors: HTTPS 인증서 오류 무시 여부.
        http_credentials: HTTP 인증 ``{"username", "password"}`` (WhatsUp 등).
    """
    state = _usable_state(storage_state)
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
                f"--window-size={size[0]},{size[1]}",
            ],
        )
        context_kwargs: Dict[str, object] = {"no_viewport": True}
        if state is not None:
            context_kwargs["storage_state"] = str(state)
        if ignore_https_errors:
            context_kwargs["ignore_https_errors"] = True
        if http_credentials is not None:
            context_kwargs["http_credentials"] = http_credentials
        context = browser.new_context(**context_kwargs)
        page = context.new_page()
        yield browser, context, page
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception as e:
                _logger.warning("browser.close() 실패(무시): %r", e)
        try:
            pw.stop()
        except Exception as e:
            _logger.warning("playwright.stop() 실패(무시): %r", e)


# ---------------------------------------------------------------------------
# 합성
# ---------------------------------------------------------------------------

def compose(
    tiles: Sequence[Tuple[Image.Image, Point, Size]],
    canvas_size: Size,
) -> Image.Image:
    """``(이미지, 좌상단, 칸 크기)`` 목록을 한 장에 붙인다.

    이미지 크기가 칸 크기와 다르면(창 보정 실패 등) 칸 크기로 리사이즈하고 경고한다.
    """
    canvas = Image.new("RGB", canvas_size, "black")
    for img, pos, size in tiles:
        tile = img.convert("RGB")
        if tile.size != tuple(size):
            _logger.warning("칸 크기 불일치 -> 리사이즈: %s -> %s", tile.size, size)
            tile = tile.resize(size, Image.Resampling.LANCZOS)
        canvas.paste(tile, pos)
    return canvas
