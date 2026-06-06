"""좌측 모니터 캡처 + 윈도우 열거·레이아웃 검증 유틸 (Windows 전용).

원본 ac_capture_left_monitor.py에서 pywin32/mss/Pillow를 쓰는 부분만
모듈 단위로 옮겼다. 셀렉터·임계값·키워드는 추측 금지 — 호출부(__main__)
상수로 두고 이쪽에는 박지 않는다.

이 모듈은 ``win32gui``/``win32process``/``win32api``/``mss``/``PIL.Image``를
import 시점에 로드한다. 그러므로 Windows + pywin32 설치 환경에서만 동작한다.
다른 플랫폼에서 import하면 ModuleNotFoundError 가 발생한다.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from mss import mss
from PIL import Image

import win32api
import win32con
import win32gui
import win32process


# ---------------------------------------------------------------------------
# 데이터 모델
# ---------------------------------------------------------------------------

@dataclass
class WindowInfo:
    """열거된 최상위 윈도우 한 개의 메타데이터."""

    hwnd: int
    title: str
    class_name: str
    rect: Tuple[int, int, int, int]
    width: int
    height: int
    is_visible: bool
    is_minimized: bool
    process_name: str


# ---------------------------------------------------------------------------
# win32 안전 접근 헬퍼 (예외는 모두 기본값으로 흡수)
# ---------------------------------------------------------------------------

def _safe_get_title(hwnd: int) -> str:
    try:
        return win32gui.GetWindowText(hwnd) or ""
    except Exception:
        return ""


def _safe_get_class(hwnd: int) -> str:
    try:
        return win32gui.GetClassName(hwnd) or ""
    except Exception:
        return ""


def _safe_get_rect(hwnd: int) -> Tuple[int, int, int, int]:
    try:
        return win32gui.GetWindowRect(hwnd)
    except Exception:
        return (0, 0, 0, 0)


def _safe_is_visible(hwnd: int) -> bool:
    try:
        return bool(win32gui.IsWindowVisible(hwnd))
    except Exception:
        return False


def _safe_is_minimized(hwnd: int) -> bool:
    try:
        return bool(win32gui.IsIconic(hwnd))
    except Exception:
        return False


def _get_process_name(hwnd: int) -> str:
    """hwnd 소속 프로세스의 실행파일명(소문자, 확장자 포함)을 돌려준다."""
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if not pid:
            return ""
        hproc = win32api.OpenProcess(
            win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ,
            False,
            pid,
        )
        try:
            exe_path = win32process.GetModuleFileNameEx(hproc, 0)
            return os.path.basename(exe_path).lower()
        finally:
            win32api.CloseHandle(hproc)
    except Exception:
        return ""


def enumerate_top_level_windows() -> List[int]:
    """현재 데스크톱의 모든 최상위 hwnd 목록을 반환한다."""
    hwnds: List[int] = []

    def cb(hwnd, _):
        hwnds.append(hwnd)
        return True

    win32gui.EnumWindows(cb, None)
    return hwnds


# ---------------------------------------------------------------------------
# 모니터 좌표
# ---------------------------------------------------------------------------

def _get_mss_monitors() -> List[Dict[str, int]]:
    """mss가 보고하는 모든 모니터 목록(첫 원소는 합집합 가상 데스크톱)."""
    with mss() as sct:
        return [
            {
                "left": int(m["left"]),
                "top": int(m["top"]),
                "width": int(m["width"]),
                "height": int(m["height"]),
            }
            for m in sct.monitors
        ]


def get_leftmost_monitor_rect() -> Tuple[int, int, int, int]:
    """좌측 끝 물리 모니터의 사각형(left, top, right, bottom)을 반환한다.

    Raises:
        RuntimeError: mss가 물리 모니터를 하나도 인식하지 못한 경우.
    """
    monitors = _get_mss_monitors()
    # 0번 인덱스는 모든 모니터 합집합이므로 제외하고 물리 모니터만 본다.
    physical = monitors[1:]
    if len(physical) < 1:
        raise RuntimeError("MSS가 물리 모니터를 인식하지 못함")

    leftmost = min(physical, key=lambda m: m["left"])
    left = leftmost["left"]
    top = leftmost["top"]
    right = left + leftmost["width"]
    bottom = top + leftmost["height"]
    return (left, top, right, bottom)


# ---------------------------------------------------------------------------
# 사각형 계산
# ---------------------------------------------------------------------------

def _rect_area(rect: Tuple[int, int, int, int]) -> int:
    l, t, r, b = rect
    return max(0, r - l) * max(0, b - t)


def _rect_intersection(
    a: Tuple[int, int, int, int],
    b: Tuple[int, int, int, int],
) -> Tuple[int, int, int, int]:
    al, at, ar, ab = a
    bl, bt, br, bb = b
    l = max(al, bl)
    t = max(at, bt)
    r = min(ar, br)
    btm = min(ab, bb)
    if r <= l or btm <= t:
        return (0, 0, 0, 0)
    return (l, t, r, btm)


def overlap_ratio(
    window_rect: Tuple[int, int, int, int],
    monitor_rect: Tuple[int, int, int, int],
) -> float:
    """창이 지정 모니터 사각형과 얼마나 겹치는지(0.0~1.0)."""
    w_area = _rect_area(window_rect)
    if w_area == 0:
        return 0.0
    inter = _rect_intersection(window_rect, monitor_rect)
    return _rect_area(inter) / w_area


# ---------------------------------------------------------------------------
# 크롬 창 수집·매칭
# ---------------------------------------------------------------------------

def collect_chrome_main_windows(
    *,
    target_process: str,
    require_class_name: str,
    require_visible: bool,
    min_window_width: int,
    min_window_height: int,
) -> List[WindowInfo]:
    """대상 프로세스 + 클래스 + 가시성 조건을 만족하는 최상위 창 목록.

    Args:
        target_process: 예 ``"chrome.exe"`` (소문자 비교).
        require_class_name: 빈 문자열이면 클래스명 검사 생략.
        require_visible: True이면 IsWindowVisible=True 만 허용.
        min_window_width: 픽셀 단위 최소 너비.
        min_window_height: 픽셀 단위 최소 높이.

    Returns:
        조건을 통과한 :class:`WindowInfo` 리스트.
    """
    target_proc = target_process.lower()
    require_class = (require_class_name or "").strip()

    result: List[WindowInfo] = []
    for hwnd in enumerate_top_level_windows():
        title = _safe_get_title(hwnd)
        if not title.strip():
            continue

        proc = _get_process_name(hwnd)
        if proc != target_proc:
            continue

        class_name = _safe_get_class(hwnd)
        if require_class and class_name != require_class:
            continue

        is_visible = _safe_is_visible(hwnd)
        if require_visible and not is_visible:
            continue

        is_min = _safe_is_minimized(hwnd)
        rect = _safe_get_rect(hwnd)
        l, t, r, b = rect
        w = max(0, r - l)
        h = max(0, b - t)
        if w < min_window_width or h < min_window_height:
            continue

        result.append(
            WindowInfo(
                hwnd=hwnd,
                title=title,
                class_name=class_name,
                rect=rect,
                width=w,
                height=h,
                is_visible=is_visible,
                is_minimized=is_min,
                process_name=proc,
            )
        )
    return result


def match_targets(
    keywords: Dict[str, str],
    chrome_windows: List[WindowInfo],
) -> Dict[str, List[WindowInfo]]:
    """키워드별로 제목에 substring 매칭되는 창들을 모은다.

    Args:
        keywords: ``{"키이름": "검색 substring"}``. 비교는 lower-case로.
        chrome_windows: :func:`collect_chrome_main_windows`의 결과.

    Returns:
        ``{키이름: [매칭 창 ...]}``. 매칭이 없으면 빈 리스트.
    """
    matched: Dict[str, List[WindowInfo]] = {k: [] for k in keywords.keys()}
    for w in chrome_windows:
        tl = w.title.lower()
        for name, kw in keywords.items():
            if str(kw).lower() in tl:
                matched[name].append(w)
    return matched


def choose_best_match_by_overlap(
    candidates: List[WindowInfo],
    monitor_rect: Tuple[int, int, int, int],
) -> Optional[WindowInfo]:
    """후보 중 좌측 모니터와 가장 많이 겹치는 창을 고른다."""
    if not candidates:
        return None
    scored = [(overlap_ratio(c.rect, monitor_rect), c) for c in candidates]
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


# ---------------------------------------------------------------------------
# 윈도우 활성화 + 브라우저 새로고침
# ---------------------------------------------------------------------------

def _force_foreground(hwnd: int) -> bool:
    """SetForegroundWindow를 여러 우회 기법으로 시도한다.

    Windows는 포커스 도용 방지(foreground lock) 때문에 백그라운드 프로세스가
    SetForegroundWindow를 호출하면 반환값 0(실패)으로 막는다. pywin32는 이를
    ``error(0, 'SetForegroundWindow', ...)``로 던진다. 아래 기법을 순서대로
    시도하고, 실제로 foreground가 됐는지 GetForegroundWindow로 검증한다.

    성공하면 True, 끝까지 실패하면 False를 반환한다(예외를 던지지 않는다).
    """
    # 1) 포커스 잠금 타임아웃을 0으로 (호출 프로세스 권한 내에서)
    try:
        win32gui.SystemParametersInfo(
            win32con.SPI_SETFOREGROUNDLOCKTIMEOUT, 0, win32con.SPIF_SENDCHANGE
        )
    except Exception:
        pass

    # 2) Alt 키를 한 번 까딱여 포커스 잠금을 해제 (잘 알려진 트릭)
    try:
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    except Exception:
        pass

    # 3) AttachThreadInput으로 현재 foreground 스레드에 붙어 제약을 우회
    fg = win32gui.GetForegroundWindow()
    cur_thread = win32api.GetCurrentThreadId()
    fg_thread = 0
    target_thread = 0
    attached_fg = False
    attached_target = False
    try:
        if fg:
            fg_thread, _ = win32process.GetWindowThreadProcessId(fg)
        target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)
        if fg_thread and fg_thread != cur_thread:
            attached_fg = win32process.AttachThreadInput(cur_thread, fg_thread, True)
        if target_thread and target_thread not in (cur_thread, fg_thread):
            attached_target = win32process.AttachThreadInput(
                cur_thread, target_thread, True
            )

        win32gui.BringWindowToTop(hwnd)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        try:
            win32gui.SetActiveWindow(hwnd)
        except Exception:
            pass
    finally:
        if attached_fg:
            try:
                win32process.AttachThreadInput(cur_thread, fg_thread, False)
            except Exception:
                pass
        if attached_target:
            try:
                win32process.AttachThreadInput(cur_thread, target_thread, False)
            except Exception:
                pass

    return win32gui.GetForegroundWindow() == hwnd


def activate_window(hwnd: int, attempts: int = 3) -> bool:
    """대상 윈도우를 foreground로 올린다.

    실패해도 예외를 던지지 않고 False를 반환한다(전체 캡처 잡이 죽지 않도록).
    재시도/우회에도 못 올리면 마지막에 minimize→restore 토글까지 시도한다.
    """
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    else:
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

    for i in range(attempts):
        if _force_foreground(hwnd):
            time.sleep(0.2)
            return True
        # 마지막 직전 시도에서 minimize→restore 토글로 강제 환기
        if i == attempts - 2:
            try:
                win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
                time.sleep(0.1)
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            except Exception:
                pass
        time.sleep(0.3)

    time.sleep(0.2)
    return win32gui.GetForegroundWindow() == hwnd


def refresh_window(hwnd: int) -> bool:
    """대상 윈도우를 활성화한 뒤 F5를 한 번 보낸다.

    포커스 확보에 실패하면(다른 창이 foreground) 엉뚱한 창에 F5가 가지 않도록
    키 입력을 건너뛰고 False를 반환한다. 성공하면 True.
    """
    if not activate_window(hwnd):
        return False
    win32api.keybd_event(win32con.VK_F5, 0, 0, 0)
    win32api.keybd_event(win32con.VK_F5, 0, win32con.KEYEVENTF_KEYUP, 0)
    return True


# ---------------------------------------------------------------------------
# 가림(occlusion) 검사
# ---------------------------------------------------------------------------

def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def _build_sample_points(
    rect: Tuple[int, int, int, int],
    grid: Tuple[int, int],
    margin: int,
) -> List[Tuple[int, int]]:
    """창 내부에 grid 모양으로 샘플 좌표를 생성한다."""
    l, t, r, b = rect
    w = r - l
    h = b - t

    inner_l = l + _clamp(margin, 0, max(0, w // 3))
    inner_t = t + _clamp(margin, 0, max(0, h // 3))
    inner_r = r - _clamp(margin, 0, max(0, w // 3))
    inner_b = b - _clamp(margin, 0, max(0, h // 3))

    if inner_r <= inner_l or inner_b <= inner_t:
        return [((l + r) // 2, (t + b) // 2)]

    rows, cols = grid
    pts: List[Tuple[int, int]] = []
    for ri in range(rows):
        y = inner_t if rows == 1 else inner_t + (inner_b - inner_t) * ri // (rows - 1)
        for ci in range(cols):
            x = inner_l if cols == 1 else inner_l + (inner_r - inner_l) * ci // (cols - 1)
            pts.append((int(x), int(y)))
    return pts


def _top_root_window_at_point(x: int, y: int) -> int:
    """좌표 (x, y)의 z-order 최상위(루트) 윈도우 hwnd."""
    hwnd = win32gui.WindowFromPoint((x, y))
    if not hwnd:
        return 0
    root = win32gui.GetAncestor(hwnd, win32con.GA_ROOT)
    return root if root else hwnd


def occlusion_ok(
    w: WindowInfo,
    *,
    sample_grid: Tuple[int, int],
    sample_margin_px: int,
    occlusion_ok_ratio: float,
) -> Tuple[bool, str]:
    """창이 화면 위에서 가려지지 않았는지 점격자(grid) 샘플링으로 검사.

    Args:
        w: 검사 대상 창.
        sample_grid: ``(rows, cols)`` 격자 크기.
        sample_margin_px: 창 경계로부터 안쪽으로 둘 여백(px).
        occlusion_ok_ratio: 통과 임계값(예 1.0이면 전부 자기 자신이어야 함).

    Returns:
        ``(통과여부, 디버그 메시지)``. 실패 시 처음 5개 위치의 가린 창
        정보를 포함한다.
    """
    pts = _build_sample_points(w.rect, sample_grid, sample_margin_px)
    ok = 0
    bad = []
    for (x, y) in pts:
        top_root = _top_root_window_at_point(x, y)
        if top_root == w.hwnd:
            ok += 1
        else:
            bad.append(f"({x},{y}) top=0x{top_root:08X} '{_safe_get_title(top_root)}'")

    total = len(pts)
    ratio = ok / total if total else 0.0
    if ratio >= occlusion_ok_ratio:
        return True, f"ok {ok}/{total}"
    return False, f"obscured {ok}/{total} | " + " | ".join(bad[:5])


# ---------------------------------------------------------------------------
# 캡처 + dhash
# ---------------------------------------------------------------------------

def capture_left_monitor(
    monitor_rect: Tuple[int, int, int, int],
    out_path: Path,
) -> None:
    """좌측 모니터 영역을 PNG로 저장한다(상위 폴더 자동 생성)."""
    l, t, r, b = monitor_rect
    region = {"left": l, "top": t, "width": r - l, "height": b - t}
    with mss() as sct:
        shot = sct.grab(region)
        img = Image.frombytes("RGB", shot.size, shot.rgb)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path, format="PNG")


def dhash(image_path: Path, hash_size: int = 8) -> int:
    """이미지의 difference hash(정수)."""
    img = Image.open(image_path).convert("L")
    img = img.resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
    pixels = list(img.getdata())

    def p(x, y):
        return pixels[y * (hash_size + 1) + x]

    bits = 0
    for y in range(hash_size):
        for x in range(hash_size):
            bits <<= 1
            bits |= 1 if p(x, y) > p(x + 1, y) else 0
    return bits


def hamming_distance(a: int, b: int) -> int:
    """두 dHash 정수의 해밍 거리(다른 비트 수)."""
    return (a ^ b).bit_count()
