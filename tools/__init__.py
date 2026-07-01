"""운영 보조 스크립트 모음 (규약 v1.1 모듈 스타일).

각 스크립트는 ``python -m tools.<name>`` 으로 실행한다. ``scripts/*.bat`` 래퍼가
venv 파이썬으로 이들을 호출한다.

    - tools.gen_daily : daily.base.yaml → config/daily.yaml 생성(근무 자동 판정).
    - tools.clean     : logs/ + state/ 정리(runner 실행 중이면 거부).
    - tools.runnerctl : runner 백그라운드 기동/상태/로그/종료.
"""

from __future__ import annotations

import sys


def enable_utf8_console() -> None:
    """stdout/stderr 를 UTF-8 로 재설정한다(한글·기호 콘솔 출력 안전).

    Windows 기본 콘솔 코드페이지(cp949)에서는 em-dash 등 일부 기호 출력이
    예외로 죽는다. ``.bat`` 래퍼가 ``chcp 65001`` 을 하지만, 직접 실행이나 다른
    환경에서도 죽지 않도록 ``errors="replace"`` 로 방어한다. 각 CLI 의 main()
    진입 시 1회 호출한다.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
