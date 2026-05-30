"""Capture job: 좌측 모니터 캡처 + 레이아웃 검증 + KWorks 업로드 (규약 v1.1).

원본의 3개 스크립트(ac_capture_left_monitor.py / ac_orchestrator.py /
ac_upload_kworks.py)를 한 job 안의 3개 stage로 합쳤다:

    stage = capture       → 좌측 모니터 판정 + 레이아웃 검증 + 캡처 + 마커 기록
    stage = safety_check  → 직전 캡처의 최신성·존재 검증(마커 + 이미지)
    stage = upload        → KworksClient로 캡처 1장 업로드 + 옵션 댓글 + submit

Pushover는 job 안에서 실패 시 1회만 보낸다.

비밀값(로그인 id/pw)은 .env에서 읽는다(KWORKS_USER_ID/KWORKS_USER_PW).
target_title은 매일 바뀌므로 CLI 인자(--target-title)로 받는다.
"""

from __future__ import annotations

# 프로젝트 루트를 sys.path에 추가한다.
# python -m jobs.capture 로 실행하든, 이 파일을 직접 실행하든
# common / site_selectors 패키지를 항상 import할 수 있게 하기 위함이다.
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]  # automation/
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import argparse
import os
import time
from datetime import datetime
from typing import Dict, Tuple

from common import config
from common.browser import sync_browser
from common.kworks import KworksClient
from common.logging import get_logger
from common.notify import send_pushover_emergency

from . import window_utils as W


# ---------------------------------------------------------------------------
# 설정 (비밀 아님)
# ---------------------------------------------------------------------------

# KWorks 진입 URL. server job과 공유한다.
KWORKS_URL = "https://kworks.kyowon.co.kr/"

# --- 캡처 대상 식별 (원본 ac_config.json 값 그대로) ---
KEYWORDS: Dict[str, str] = {
    "WhatsUp": "WhatsUp",
    "Zenius": "Zenius",
    "ETL": "ETL",
    "Dashboard": "Daily Service Inspection Dashboard",
}
TARGET_PROCESS = "chrome.exe"
REQUIRE_CLASS_NAME = "Chrome_WidgetWin_1"
REQUIRE_VISIBLE = True
MIN_WINDOW_WIDTH = 300
MIN_WINDOW_HEIGHT = 200

# --- 레이아웃 검증 임계값 (원본 값 그대로) ---
OVERLAP_OK_THRESHOLD = 0.90
SAMPLE_GRID: Tuple[int, int] = (3, 3)
SAMPLE_MARGIN_PX = 30
OCCLUSION_OK_RATIO = 1.0

# --- dHash 비교 임계값 ---
DHASH_THRESHOLD = 15

# --- 안전 검사 ---
MAX_AGE_SECONDS = 300

# --- 산출물 경로 (규약 v1.1: BASE_DIR/captures/<job>/ 하위) ---
CAPTURES_DIR = config.BASE_DIR / "captures" / "capture"
BASELINE_FILENAME = "baseline_left_monitor.png"
LATEST_FILENAME = "latest_left_monitor.png"
LATEST_PATH_MARKER = "latest_path.txt"

LOG = get_logger("jobs.capture", "capture.log")


# ---------------------------------------------------------------------------
# 자격증명
# ---------------------------------------------------------------------------

def _read_credentials() -> tuple[str, str]:
    """daily.yaml 에서 KWorks 로그인 자격증명을 읽는다.

    값은 ``common.config.KWORKS_USER_ID`` / ``KWORKS_USER_PW`` 로 노출되며,
    실제 소스는 ``config/daily.yaml`` 의 ``kworks.user_id`` / ``kworks.user_pw``.

    Raises:
        RuntimeError: 둘 중 하나라도 비어 있는 경우(어느 키가 비었는지 명시).
    """
    user_id = config.KWORKS_USER_ID.strip()
    user_pw = config.KWORKS_USER_PW.strip()
    missing = [
        k for k, v in (
            ("kworks.user_id", user_id),
            ("kworks.user_pw", user_pw),
        ) if not v
    ]
    if missing:
        raise RuntimeError(
            f"daily.yaml 의 다음 값이 비어 있습니다: {', '.join(missing)}"
        )
    return user_id, user_pw


# ---------------------------------------------------------------------------
# 캡처 stage 보조
# ---------------------------------------------------------------------------

def _validate_targets_and_layout(
    left_monitor_rect: Tuple[int, int, int, int],
) -> None:
    """대상 크롬 창들이 좌측 모니터에서 가시 + 비가림 상태인지 검증한다.

    Raises:
        RuntimeError: 대상 누락, 최소화됨, 겹침 부족, 가려짐 중 하나라도 발생.
    """
    chrome_windows = W.collect_chrome_main_windows(
        target_process=TARGET_PROCESS,
        require_class_name=REQUIRE_CLASS_NAME,
        require_visible=REQUIRE_VISIBLE,
        min_window_width=MIN_WINDOW_WIDTH,
        min_window_height=MIN_WINDOW_HEIGHT,
    )
    matched = W.match_targets(KEYWORDS, chrome_windows)

    missing = []
    chosen: Dict[str, W.WindowInfo] = {}
    for name, candidates in matched.items():
        best = W.choose_best_match_by_overlap(candidates, left_monitor_rect)
        if not best:
            missing.append(name)
        else:
            chosen[name] = best

    if missing:
        raise RuntimeError(f"대상 누락: {missing}")

    bad_overlap = []
    minimized = []
    for name, w in chosen.items():
        if w.is_minimized:
            minimized.append(name)
        ratio = W.overlap_ratio(w.rect, left_monitor_rect)
        if ratio < OVERLAP_OK_THRESHOLD:
            bad_overlap.append(f"{name}={ratio*100:.1f}% rect={w.rect}")

    if minimized:
        raise RuntimeError(f"최소화된 대상: {minimized}")
    if bad_overlap:
        raise RuntimeError("겹침 부족: " + " ; ".join(bad_overlap))

    occl_fail = []
    for name, w in chosen.items():
        ok, detail = W.occlusion_ok(
            w,
            sample_grid=SAMPLE_GRID,
            sample_margin_px=SAMPLE_MARGIN_PX,
            occlusion_ok_ratio=OCCLUSION_OK_RATIO,
        )
        if not ok:
            occl_fail.append(f"{name}: {detail}")
    if occl_fail:
        raise RuntimeError("가림 감지: " + " ; ".join(occl_fail))

    LOG.info("레이아웃 검증 OK (targets/overlap/occlusion)")


def _write_latest_marker(image_path: Path) -> Path:
    """``LATEST_PATH_MARKER`` 파일에 이미지 절대경로를 기록하고 마커 경로 반환."""
    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    marker = CAPTURES_DIR / LATEST_PATH_MARKER
    marker.write_text(str(image_path), encoding="utf-8")
    LOG.info("최신 마커 기록: %s -> %s", marker, image_path)
    return marker


def _do_capture_stage(no_compare: bool) -> Path:
    """capture stage 본문: 모니터 판정 → 검증 → 캡처 → 마커 → (옵션) dhash.

    Args:
        no_compare: True이면 baseline과의 dhash 비교를 생략한다.

    Returns:
        방금 캡처한 ``latest`` 이미지의 절대 경로.

    Raises:
        RuntimeError: 검증 실패 또는 dhash 비교 실패 시.
    """
    left_rect = W.get_leftmost_monitor_rect()
    LOG.info("좌측 모니터 rect: %s", left_rect)

    _validate_targets_and_layout(left_rect)

    latest_path = CAPTURES_DIR / LATEST_FILENAME
    baseline_path = CAPTURES_DIR / BASELINE_FILENAME

    W.capture_left_monitor(left_rect, latest_path)
    LOG.info("최신 캡처 저장: %s", latest_path)
    _write_latest_marker(latest_path)

    if no_compare:
        LOG.info("--no-compare -> dHash 비교 생략")
        return latest_path

    if not baseline_path.exists():
        raise RuntimeError(
            f"baseline 없음: {baseline_path} (먼저 --make-baseline 으로 생성)"
        )

    dist = W.hamming_distance(W.dhash(baseline_path), W.dhash(latest_path))
    LOG.info("dHash distance=%d threshold=%d", dist, DHASH_THRESHOLD)
    if dist > DHASH_THRESHOLD:
        raise RuntimeError(f"유사도 실패: dHash {dist} > {DHASH_THRESHOLD}")

    return latest_path


def _do_baseline() -> Path:
    """baseline 캡처 + 마커 기록만 수행하고 종료(검증은 그대로 수행)."""
    left_rect = W.get_leftmost_monitor_rect()
    LOG.info("좌측 모니터 rect: %s", left_rect)

    _validate_targets_and_layout(left_rect)

    baseline_path = CAPTURES_DIR / BASELINE_FILENAME
    W.capture_left_monitor(left_rect, baseline_path)
    LOG.info("baseline 생성: %s", baseline_path)
    _write_latest_marker(baseline_path)
    return baseline_path


# ---------------------------------------------------------------------------
# safety_check stage
# ---------------------------------------------------------------------------

def _file_age_seconds(path: Path) -> float:
    """파일 수정 시각 기준 경과 초. 실패 시 매우 큰 값을 돌려준다."""
    try:
        return time.time() - path.stat().st_mtime
    except Exception:
        return 1e12


def _do_safety_check_stage() -> Path:
    """마커와 캡처 이미지가 ``MAX_AGE_SECONDS`` 이내인지 검증.

    Returns:
        검증을 통과한 이미지의 절대 경로(업로드 단계에서 그대로 쓴다).

    Raises:
        RuntimeError: 마커가 없거나, 너무 오래되었거나, 가리키는 이미지가
            없거나 오래된 경우.
    """
    marker = CAPTURES_DIR / LATEST_PATH_MARKER
    if not marker.exists():
        raise RuntimeError(f"safety_check: 마커 없음: {marker}")

    marker_age = _file_age_seconds(marker)
    if marker_age > MAX_AGE_SECONDS:
        raise RuntimeError(
            f"safety_check: 마커가 너무 오래됨({marker_age:.1f}s) > {MAX_AGE_SECONDS}s: {marker}"
        )

    image_path_str = marker.read_text(encoding="utf-8").strip()
    if not image_path_str:
        raise RuntimeError(f"safety_check: 마커가 비어 있음: {marker}")

    image_path = Path(image_path_str)
    if not image_path.is_absolute():
        image_path = (config.BASE_DIR / image_path).resolve()

    if not image_path.exists():
        raise RuntimeError(f"safety_check: 이미지 없음: {image_path}")

    image_age = _file_age_seconds(image_path)
    if image_age > MAX_AGE_SECONDS:
        raise RuntimeError(
            f"safety_check: 이미지가 너무 오래됨({image_age:.1f}s) > {MAX_AGE_SECONDS}s: {image_path}"
        )

    LOG.info("safety_check OK: %s (age=%.1fs)", image_path, image_age)
    return image_path


# ---------------------------------------------------------------------------
# CLI / main
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="좌측 모니터 캡처 + 레이아웃 검증 + KWorks 업로드"
    )
    parser.add_argument(
        "--target-title",
        required=False,
        default=None,
        help=(
            "KWorks 전체 업무 목록에서 찾을 작업 제목. --make-baseline 이외 모든 경로에서 필수. "
            "생략 시 daily.yaml 의 kworks.target_title 값을 사용한다. "
            "CLI 인자가 daily.yaml 보다 우선."
        ),
    )
    parser.add_argument(
        "--make-baseline",
        action="store_true",
        help="baseline 캡처 + 마커만 만들고 종료(업로드/알림 미수행).",
    )
    parser.add_argument(
        "--no-compare",
        action="store_true",
        help="dHash 비교 생략(baseline 없이도 진행 가능).",
    )
    parser.add_argument(
        "--submit",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="업로드 후 Enter 최종등록 여부. 미지정 시 settings.yaml 을 따른다.",
    )
    parser.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="브라우저 헤드리스 여부. 미지정 시 settings.yaml 을 따른다.",
    )
    return parser.parse_args()


def main() -> int:
    """capture job 진입점."""
    args = _parse_args()
    no_compare = bool(args.no_compare)
    make_baseline = bool(args.make_baseline)

    # 우선순위: CLI 인자 > settings.yaml. 미지정(None)이면 settings.yaml 을 따른다.
    submit = (
        args.submit
        if args.submit is not None
        else config.get_submit_by_enter("capture")
    )
    headless = (
        args.headless
        if args.headless is not None
        else config.get_headless("capture")
    )

    config.ensure_dirs()
    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)

    stage = "init"
    start_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    LOG.info(
        "[START] capture job at %s headless=%s submit=%s no_compare=%s make_baseline=%s",
        start_ts,
        headless,
        submit,
        no_compare,
        make_baseline,
    )

    try:
        if make_baseline:
            # baseline 만들기는 캡처/마커만 만들고 종료(업로드/알림 미수행).
            stage = "make_baseline"
            LOG.info("[STAGE] %s", stage)
            _do_baseline()
            LOG.info("[OK] baseline 생성 완료")
            return 0

        # 그 외 경로에서는 target_title 필수.
        # 우선순위: CLI --target-title > daily.yaml 의 kworks.target_title.
        target_title: str = (
            args.target_title or config.KWORKS_TARGET_TITLE
        ).strip()
        if not target_title:
            raise RuntimeError(
                "--target-title 도 daily.yaml 의 kworks.target_title 도 비어 있습니다 "
                "(--make-baseline 만 예외)."
            )

        stage = "capture"
        LOG.info("[STAGE] %s", stage)
        latest_path = _do_capture_stage(no_compare=no_compare)

        stage = "safety_check"
        LOG.info("[STAGE] %s", stage)
        image_path = _do_safety_check_stage()

        # safety_check가 돌려준 이미지와 직전 캡처 경로가 일치해야 정상.
        if image_path != latest_path:
            LOG.warning(
                "safety_check가 가리키는 이미지와 직전 캡처 경로가 다릅니다: marker=%s captured=%s",
                image_path,
                latest_path,
            )

        stage = "credentials"
        LOG.info("[STAGE] %s", stage)
        user_id, user_pw = _read_credentials()

        with sync_browser(
            headless=headless,
        ) as (_browser, _context, page):
            stage = "kworks_login"
            LOG.info("[STAGE] %s", stage)
            client = KworksClient(page, logger=LOG)
            client.login(KWORKS_URL, user_id, user_pw)

            stage = "open_task_detail"
            LOG.info("[STAGE] %s", stage)
            form = client.open_task_detail(target_title)

            stage = "upload_files"
            LOG.info("[STAGE] %s", stage)
            client.upload_files(form, [image_path])

            if submit:
                stage = "submit"
                LOG.info("[STAGE] %s", stage)
                client.submit(form)
            else:
                LOG.info("[INFO] submit=False -> Enter 등록 생략")

        LOG.info("[OK] capture+upload 완료: %s", image_path)
        return 0

    except Exception as e:
        LOG.exception("[FAIL] stage=%s err=%r", stage, e)

        send_pushover_emergency(
            title="[AC] Capture job FAILED",
            message=f"stage={stage} | err={e}",
        )
        return 1

    finally:
        LOG.info("[END] capture run finished")


if __name__ == "__main__":
    raise SystemExit(main())
