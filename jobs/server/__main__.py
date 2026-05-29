"""Server upload job: 폴더 단위 KWorks 업로드 (규약 v1.1).

원본 server/orchestrator.py + worker_upload.py를 한 job으로 통합했다.
주요 차이점:
    - orchestrator → worker subprocess 구조를 단일 프로세스 내 폴더-루프로 대체.
    - 비즈니스 로직은 common.kworks.KworksClient에 위임.
    - 폴더가 여러 개면 폴더 사이에서 폼을 다시 열어(open_task_detail) 폴더별
      상태가 섞이지 않게 한다(원본이 폴더당 worker를 새로 띄우던 효과와 동등).

비밀값(로그인 id/pw)은 .env에서 읽는다(KWORKS_USER_ID/KWORKS_USER_PW).
target_title은 매일 바뀌므로 CLI 인자(--target-title)로 받는다.
"""

from __future__ import annotations

# 프로젝트 루트를 sys.path에 추가한다.
# python -m jobs.server 로 실행하든, 이 파일을 직접 실행하든
# common / site_selectors 패키지를 항상 import할 수 있게 하기 위함이다.
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]  # automation/
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import argparse
import os
import re
from datetime import datetime
from typing import List

from common import config
from common.browser import sync_browser
from common.kworks import KworksClient
from common.logging import get_logger
from common.notify import send_pushover_emergency


# ---------------------------------------------------------------------------
# 설정 (비밀 아님)
# ---------------------------------------------------------------------------

# KWorks 진입 URL. capture job과 공유한다.
KWORKS_URL = "https://kworks.kyowon.co.kr/"

# 업로드 대상 이미지 확장자(원본과 동일).
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

LOG = get_logger("jobs.server", "server.log")


# ---------------------------------------------------------------------------
# 이미지 유틸 (원본 worker_upload._natural_key/list_images 동등)
# ---------------------------------------------------------------------------

def _natural_key(s: str) -> list:
    """파일명을 숫자 단위로 잘라 자연 정렬용 키로 만든다.

    예: ``img2.png`` 가 ``img10.png`` 보다 앞에 오도록 한다.
    """
    parts = re.split(r"(\d+)", s)
    out: list = []
    for p in parts:
        if p.isdigit():
            out.append(int(p))
        else:
            out.append(p.lower())
    return out


def list_images(folder: Path) -> List[Path]:
    """폴더에서 지원 확장자 이미지를 자연 정렬 순서로 돌려준다.

    Args:
        folder: 검색할 디렉터리.

    Returns:
        자연 정렬된 ``Path`` 리스트(파일만, 하위 디렉터리 무시).
    """
    imgs = [
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    ]
    imgs.sort(key=lambda p: _natural_key(p.name))
    return imgs


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
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    """CLI 인자 파서.

    Returns:
        ``argparse.Namespace``: folder(list[str]), target_title(str),
        no_submit(bool), dry_run(bool).
    """
    parser = argparse.ArgumentParser(description="KWorks server upload job")
    parser.add_argument(
        "--folder",
        action="append",
        default=[],
        required=True,
        help="업로드할 이미지 폴더. 여러 번 지정 가능. 예: --folder A --folder B",
    )
    parser.add_argument(
        "--target-title",
        required=False,
        default=None,
        help=(
            "KWorks 전체 업무 목록에서 찾을 작업 제목(매일 바뀜). "
            "생략 시 daily.yaml 의 kworks.target_title 값을 사용한다. "
            "CLI 인자가 daily.yaml 보다 우선."
        ),
    )
    parser.add_argument(
        "--no-submit",
        action="store_true",
        help="Enter 등록을 생략한다(첨부만, 등록은 사용자가 직접).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="첫 폴더에 대해 로그인+폼 확보까지만 수행하고 종료.",
    )
    return parser.parse_args()


def _resolve_folder(arg: str) -> Path:
    """CLI에서 받은 폴더 인자를 절대경로화하고 디렉터리 검증한다.

    Args:
        arg: 사용자가 ``--folder``로 넘긴 문자열.

    Returns:
        절대경로화된 ``Path``.

    Raises:
        FileNotFoundError: 경로가 존재하지 않는 경우.
        NotADirectoryError: 경로가 디렉터리가 아닌 경우.
    """
    p = Path(arg).resolve()
    if not p.exists():
        raise FileNotFoundError(f"폴더 없음: {p}")
    if not p.is_dir():
        raise NotADirectoryError(f"디렉터리가 아님: {p}")
    return p


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    """서버 업로드 job 진입점."""
    args = _parse_args()
    dry_run = bool(args.dry_run)
    submit = not bool(args.no_submit)

    # target_title 우선순위: CLI --target-title > daily.yaml 의 kworks.target_title.
    target_title: str = (
        args.target_title or config.KWORKS_TARGET_TITLE
    ).strip()
    if not target_title:
        raise SystemExit(
            "ERROR: --target-title 도 daily.yaml 의 kworks.target_title 도 비어 있습니다."
        )

    config.ensure_dirs()

    stage = "init"
    start_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    LOG.info(
        "[START] server upload at %s dry_run=%s submit=%s folders=%d target_title=%r",
        start_ts,
        dry_run,
        submit,
        len(args.folder),
        target_title,
    )

    try:
        # 폴더 인자 절대경로화·존재 검증을 먼저 끝낸다(브라우저 띄우기 전 실패하게).
        stage = "resolve_folders"
        LOG.info("[STAGE] %s", stage)
        folders = [_resolve_folder(x) for x in args.folder]

        # 각 폴더의 이미지 사전 스캔(빈 폴더는 경고 후 스킵 대상으로 표시).
        plan: list[tuple[Path, list[Path]]] = []
        for f in folders:
            imgs = list_images(f)
            if not imgs:
                LOG.warning("폴더에 이미지 없음 -> 스킵: %s", f)
                continue
            plan.append((f, imgs))

        if not plan:
            LOG.warning("업로드할 폴더가 없습니다(전부 비어 있음). 종료.")
            return 0

        stage = "credentials"
        LOG.info("[STAGE] %s", stage)
        user_id, user_pw = _read_credentials()

        with sync_browser(headless=True) as (_browser, _context, page):

            stage = "kworks_login"
            LOG.info("[STAGE] %s", stage)
            client = KworksClient(page, logger=LOG)
            client.login(KWORKS_URL, user_id, user_pw)
            LOG.info("[OK] KWorks 로그인 완료")

            for idx, (folder, images) in enumerate(plan, start=1):
                LOG.info(
                    "[FOLDER] %d/%d %s (이미지 %d장)",
                    idx,
                    len(plan),
                    folder,
                    len(images),
                )

                stage = f"open_task_detail[{idx}]"
                LOG.info("[STAGE] %s", stage)
                form = client.open_task_detail(target_title)

                if dry_run:
                    # 첫 폴더에서 폼 확보까지만 도달 후 종료한다(원본 spec).
                    LOG.info("[DRY-RUN] 로그인+폼 확보 완료, 업로드/알림 생략")
                    return 0

                stage = f"type_comment[{idx}]"
                LOG.info("[STAGE] %s", stage)
                client.type_comment(form, folder.name)

                stage = f"upload_files[{idx}]"
                LOG.info("[STAGE] %s", stage)
                client.upload_files(form, images)

                if submit:
                    stage = f"submit[{idx}]"
                    LOG.info("[STAGE] %s", stage)
                    client.submit(form)
                else:
                    LOG.info("[INFO] --no-submit -> Enter 등록 생략")

                LOG.info("[OK] 폴더 처리 완료: %s", folder)

        LOG.info("[END] 모든 폴더 처리 완료: %d개", len(plan))
        return 0

    except Exception as e:
        LOG.exception("[FAIL] stage=%s err=%r", stage, e)

        if dry_run:
            LOG.info("[DRY-RUN] 실패해도 알림은 생략")
            return 1

        send_pushover_emergency(
            title="[KWorks] 서버 업로드 실패",
            message=f"stage={stage} | err={e}",
        )
        return 1

    finally:
        LOG.info("[END] server upload run finished")


if __name__ == "__main__":
    raise SystemExit(main())
