"""ETL 관제(오류테이블 조회) 상수.

규약 v1.1 따름. 관제 화면 ``monitor.jsp`` 는 EasyUI datagrid 로, 페이지가 열린 뒤
``<table id="dg" ... method="get" url="get_monitor.jsp">`` 정의대로 같은 폴더의
``get_monitor.jsp`` 를 GET 해 표를 채운다. 그 응답(JSON 배열)을 직접 읽으므로
브라우저도 로그인도 필요 없다.

응답 행 예(키는 표의 ``field`` 이름과 같다)::

    {"START_TIME": "2026-10-09 17:10:02", "END_TIME": "...", "FOLDER_NM": "...",
     "WORKFLOW_NM": "...", "SESSION_NM": "...", "IMPORTANCE_DESC": "무해",
     "READ_CNT": "0", "READ_FAIL": "0", "WRITE_CNT": "0", "WRITE_FAIL": "0",
     "STATE": "Failed", "PROG_TIME": "0시간0분5초", "ERROR_MSG": "...",
     "WORKFLOW_COMMENTS": "...", "CHARGE_PERSON": "..."}

``WORKFLOW_COMMENTS`` / ``CHARGE_PERSON`` 은 값이 없으면 키 자체가 빠진다.
"""

from __future__ import annotations


# --- URL (비밀 아님) ---
MONITOR_URL = "http://10.1.72.25:8130/monitor/monitor.jsp"
DATA_URL = "http://10.1.72.25:8130/monitor/get_monitor.jsp"

# --- 응답 디코딩 후보 (Content-Type 에 charset 이 없을 때 순서대로 시도) ---
ENCODING_CANDIDATES = ("utf-8", "euc-kr")

# --- 응답 필드 ---
F_IMPORTANCE = "IMPORTANCE_DESC"
F_START = "START_TIME"
F_END = "END_TIME"
F_FOLDER = "FOLDER_NM"
F_WORKFLOW = "WORKFLOW_NM"
F_SESSION = "SESSION_NM"
F_STATE = "STATE"
F_PROG_TIME = "PROG_TIME"
F_READ_CNT = "READ_CNT"
F_READ_FAIL = "READ_FAIL"
F_WRITE_CNT = "WRITE_CNT"
F_WRITE_FAIL = "WRITE_FAIL"
F_ERROR_MSG = "ERROR_MSG"
F_COMMENTS = "WORKFLOW_COMMENTS"
F_CHARGE = "CHARGE_PERSON"

# START_TIME 형식(보고 완료 목록 정리용).
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
