"""Jennifer 사이트 셀렉터 상수 (login_type 별 구분).

규약 v1.1 따름. 원본 jennifer/login_strategies.py, popup_manager.py,
data_extractor.py의 셀렉터 문자열을 그대로 옮겼다(추측 금지).

login_type 은 두 가지이며, 각각 대시보드/팝업/추출 셀렉터가 다르다:

    "standard"  → Jennifer Cloud / Group / LMS
    "new"       → Jennifer Redpen

본 모듈은 셀렉터만 노출한다. URL/로그인 자격증명/임계값은 호출부
(`config/jennifer_sites.json` 및 `jobs/jennifer/__main__.py`)에 둔다.
"""

from __future__ import annotations

from typing import Dict


# --- 공통 로그인 폼 input (두 login_type 동일) ---
SEL_INPUT_ID = "input[name='id']"
SEL_INPUT_PW = "input[name='password']"


# --- login_type 별 로그인 버튼 ---
SEL_LOGIN_BTN_STANDARD = ".login-btn"
SEL_LOGIN_BTN_NEW = "a.btn-login"

LOGIN_BTN_BY_TYPE: Dict[str, str] = {
    "standard": SEL_LOGIN_BTN_STANDARD,
    "new": SEL_LOGIN_BTN_NEW,
}


# --- 로그인 후 대시보드 진입을 확인할 대기 셀렉터 ---
# 세션 유효성 5초 확인 / 로그인 후 30초 대기 두 곳에서 동일하게 쓴다.
SEL_WAIT_STANDARD = ".interaction"
SEL_WAIT_NEW = ".chartCanvas"

WAIT_SELECTOR_BY_TYPE: Dict[str, str] = {
    "standard": SEL_WAIT_STANDARD,
    "new": SEL_WAIT_NEW,
}


# --- 더블클릭으로 팝업을 여는 대시보드 캔버스 ---
SEL_CANVAS_STANDARD = "canvas.interaction"
SEL_CANVAS_NEW = "#CanvasInteraction"

CANVAS_BY_TYPE: Dict[str, str] = {
    "standard": SEL_CANVAS_STANDARD,
    "new": SEL_CANVAS_NEW,
}


# --- 팝업 내 새로고침 버튼 ---
SEL_REFRESH_STANDARD = ".third"
SEL_REFRESH_NEW = "#refreshBtn"

REFRESH_BY_TYPE: Dict[str, str] = {
    "standard": SEL_REFRESH_STANDARD,
    "new": SEL_REFRESH_NEW,
}


# --- 팝업 내 fatal / total 텍스트 ---
SEL_FATAL_STANDARD = "div.fatal"
SEL_TOTAL_STANDARD = ".total strong"

SEL_FATAL_NEW = "#count_fatal"
SEL_TOTAL_NEW = "#count_total"

FATAL_BY_TYPE: Dict[str, str] = {
    "standard": SEL_FATAL_STANDARD,
    "new": SEL_FATAL_NEW,
}
TOTAL_BY_TYPE: Dict[str, str] = {
    "standard": SEL_TOTAL_STANDARD,
    "new": SEL_TOTAL_NEW,
}
