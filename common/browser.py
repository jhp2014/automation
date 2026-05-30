"""Playwright 브라우저/컨텍스트/페이지 공용 헬퍼.

규약 v1.1 따름. 동기 Playwright 기준이며, 브라우저 launch와 context 생성
(storage_state 로드 옵션 포함), teardown, storage_state 저장이라는 기계적
작업만 공통화한다. 사이트별 로그인 로직은 포함하지 않는다.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, Optional, Tuple, Union

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    sync_playwright,
)

from .logging import get_logger


_logger = get_logger("common.browser")


def _is_storage_state_usable(path: Path) -> bool:
    """storage_state 파일이 JSON으로 정상 파싱되는지 검사한다.

    Args:
        path: 검사 대상 storage_state 파일 경로.

    Returns:
        True이면 그대로 Playwright에 넘겨도 되고, False이면 파싱 실패 등의
        이유로 사용할 수 없다. 호출부는 False일 때 새 컨텍스트로 폴백한다.
    """
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except Exception as e:
        _logger.warning("storage_state 읽기 실패: path=%s err=%r", path, e)
        return False

    if not raw:
        _logger.warning("storage_state 비어 있음: path=%s", path)
        return False

    try:
        json.loads(raw)
    except Exception as e:
        _logger.warning("storage_state JSON 파싱 실패: path=%s err=%r", path, e)
        return False

    return True


@contextmanager
def sync_browser(
    *,
    headless: bool = True,
    storage_state: Union[str, Path, None] = None,
    viewport: Optional[Dict[str, int]] = None,
    window_size: Optional[Tuple[int, int]] = None,
    ignore_https_errors: bool = False,
) -> Iterator[Tuple[Browser, BrowserContext, Page]]:
    """크로미움 브라우저/컨텍스트/페이지를 생성하고 종료 시 정리한다.

    동작 요약:
        - ``window_size``가 주어지면 launch args에 ``--window-size=W,H``를 추가하고,
          viewport도 동일 크기로 맞춘다(zenius 캡처 짤림 방지 패턴 재현).
        - ``storage_state`` 경로가 주어졌지만 파일이 깨졌거나 비어 있으면
          (JSON 파싱 실패) 세션 없이 새 컨텍스트로 폴백하고 경고 로그를 남긴다.
        - teardown(context/browser close)은 예외를 삼키고, launch 실패는
          호출부로 그대로 전파한다.
        - ``viewport``와 ``window_size``가 모두 주어지면 ``viewport``를 우선한다
          (명시값 존중).

    Args:
        headless: True이면 헤드리스로 기동.
        storage_state: 재사용할 세션 파일 경로. None이면 새 컨텍스트.
        viewport: ``{"width": int, "height": int}`` 형태의 명시 뷰포트.
        window_size: ``(width, height)`` 튜플. 브라우저 창 크기와 (viewport가
            없을 때) 뷰포트 크기를 함께 결정한다.
        ignore_https_errors: True이면 HTTPS 인증서 오류를 무시하고 컨텍스트를
            생성한다. 사내 인증서/SSL inspection/자체서명 인증서 환경에서 HTTPS
            접속 실패를 우회할 때 사용한다.

    Yields:
        ``(Browser, BrowserContext, Page)`` 튜플. with 블록을 빠져나가면
        context/browser가 자동으로 close된다.

    Raises:
        playwright.sync_api.Error: 브라우저 launch 자체가 실패한 경우.
    """
    launch_args: list[str] = []
    if window_size is not None:
        w, h = window_size
        launch_args.append(f"--window-size={w},{h}")

    # viewport 결정: 명시값이 있으면 그것을 쓰고, 없으면 window_size에서 파생.
    if viewport is not None:
        effective_viewport: Optional[Dict[str, int]] = viewport
    elif window_size is not None:
        w, h = window_size
        effective_viewport = {"width": w, "height": h}
    else:
        effective_viewport = None

    # storage_state 폴백 판정.
    state_path: Optional[Path] = None
    if storage_state is not None:
        candidate = Path(storage_state)
        if candidate.exists() and _is_storage_state_usable(candidate):
            state_path = candidate
        else:
            if not candidate.exists():
                _logger.info("storage_state 미존재 -> 새 컨텍스트: path=%s", candidate)
            # 파싱 실패 케이스는 _is_storage_state_usable 내부에서 이미 경고 로그.

    # launch 실패는 그대로 전파 (with 진입 전이라 상위로 올라간다).
    pw = sync_playwright().start()
    browser: Optional[Browser] = None
    context: Optional[BrowserContext] = None
    try:
        browser = pw.chromium.launch(headless=headless, args=launch_args)

        context_kwargs: Dict[str, object] = {}
        if state_path is not None:
            context_kwargs["storage_state"] = str(state_path)
        if effective_viewport is not None:
            context_kwargs["viewport"] = effective_viewport
            context_kwargs["device_scale_factor"] = 1
        if ignore_https_errors:
            context_kwargs["ignore_https_errors"] = True

        context = browser.new_context(**context_kwargs)
        page = context.new_page()

        yield browser, context, page

    finally:
        # teardown은 예외를 삼킨다. 정리 단계의 실패가 본 작업의 결과를 덮지
        # 않도록 한다.
        if context is not None:
            try:
                context.close()
            except Exception as e:
                _logger.warning("context.close() 실패(무시): %r", e)
        if browser is not None:
            try:
                browser.close()
            except Exception as e:
                _logger.warning("browser.close() 실패(무시): %r", e)
        try:
            pw.stop()
        except Exception as e:
            _logger.warning("playwright.stop() 실패(무시): %r", e)


def save_storage_state(context: BrowserContext, path: Union[str, Path]) -> None:
    """현재 컨텍스트 세션을 지정 경로에 저장한다(상위 폴더 자동 생성).

    Args:
        context: 저장할 Playwright BrowserContext.
        path: 저장 경로. 상위 폴더가 없으면 자동 생성한다.

    Returns:
        None.

    Raises:
        OSError: 파일 쓰기 자체가 실패할 때(권한, 디스크 등).
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(p))
