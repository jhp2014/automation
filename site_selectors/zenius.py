"""Zenius(EMS/SMS/NMS/FMS) 셀렉터 상수.

규약 v1.1 따름. EMS/SMS 셀렉터 문자열은 원본 zenius_monitor_v2.0.py에서 그대로
옮겼고(추측 금지), NMS/FMS 셀렉터는 저장된 실제 화면 소스
``docs/zenius/Zenius7_nms.mhtml`` / ``Zenius7_fms.mhtml`` 에서 추출했다.
모든 상수는 ``SEL_`` 접두사를 쓴다.
"""

from __future__ import annotations


# --- Zenius URL (비밀 아님) ---
LOGIN_URL = "http://ems.kyowon.co.kr/zenius7/login.zenius?_m=loginPage"
MAIN_URL = "http://ems.kyowon.co.kr/zenius7/layout.zenius?_m=main"
ERROR_403_PREFIX = (
    "http://ems.kyowon.co.kr/zenius7/error.zenius?_m=error&ERROR_CODE=403"
)


# --- 로그인 셀렉터 ---
SEL_ID = "#z_username"
SEL_PW = "#z_password"
SEL_LOGIN_BTN = "a.btn_login"
SEL_ERROR_OK = "#popup_ok"


# --- 메뉴 셀렉터 ---
SEL_EMS_MENU = "li#z_ems a"
SEL_SMS_MENU = "li#z_sms a"
SEL_NMS_MENU = "li#z_nms a"
SEL_FMS_MENU = "li#z_fms a"


# --- EMS jqGrid 셀렉터 ---
SEL_STATUS_TH = "#eventMainTable_z_status_str"
SEL_STATUS_SORT_WRAP = "#jqgh_eventMainTable_z_status_str"
SEL_GRID_ROWS = "tr.jqgrow"

# 행 안의 td (aria-describedby 기반)
SEL_TD_STATUS = "td[aria-describedby='eventMainTable_z_status_str']"
SEL_TD_ALERT = "td[aria-describedby='eventMainTable_z_alert_str'] span"
SEL_TD_DUR = "td[aria-describedby='eventMainTable_duration_str']"
SEL_TD_TITLE = "td[aria-describedby='eventMainTable_z_myname']"
SEL_TD_MSG = "td[aria-describedby='eventMainTable_z_mymsg']"
SEL_TD_GROUP = "td[aria-describedby='eventMainTable_groupname']"
SEL_TD_HOST = "td[aria-describedby='eventMainTable_z_myhost']"
SEL_TD_EVTTIME = "td[aria-describedby='eventMainTable_z_evttime_str']"
# 인프라명(NMS/SMS/FMS) — 담당자 조회 화면 라우팅에 사용.
SEL_TD_WNAME = "td[aria-describedby='eventMainTable_z_wname']"


# --- SMS 화면 셀렉터 (간편검색만 사용) ---
SEL_SMS_GRID = "#smsMonitorAgentGrid"
SEL_SMS_ROWS = "#smsMonitorAgentGrid tr.jqgrow"

SEL_SMS_SEARCH_INPUT = "#search_text"
SEL_SMS_SEARCH_BTN = "#simpleSearchIcon"

# jqGrid 로딩 레이어(있을 수도/없을 수도)
SEL_SMS_LOADING_TEXT = "#load_smsMonitorAgentGrid"
SEL_SMS_LOADING_MASK = "#lui_smsMonitorAgentGrid"

# SMS 행 안의 td
SEL_SMS_TD_HOST = "td[aria-describedby='smsMonitorAgentGrid_z_myhost']"
SEL_SMS_TD_OWNER = "td[aria-describedby='smsMonitorAgentGrid_z_mylocate']"


# --- NMS 화면 셀렉터 (간편검색만 사용) ---
SEL_NMS_GRID = "#monitorDeviceGrid"
SEL_NMS_ROWS = "#monitorDeviceGrid tr.jqgrow"

SEL_NMS_SEARCH_INPUT = "#searchText"
SEL_NMS_SEARCH_BTN = "#simpleSearchIcon"

SEL_NMS_LOADING_TEXT = "#load_monitorDeviceGrid"
SEL_NMS_LOADING_MASK = "#lui_monitorDeviceGrid"

# NMS 행 안의 td — z_contact 컬럼 헤더명은 "담당자 연락처"(쉼표 구분 복수 이름).
SEL_NMS_TD_HOST = "td[aria-describedby='monitorDeviceGrid_z_myhost']"
SEL_NMS_TD_OWNER = "td[aria-describedby='monitorDeviceGrid_z_contact']"


# --- FMS 화면 셀렉터 (간편검색만 사용) ---
SEL_FMS_GRID = "#fmsMonitorFacilityTB"
SEL_FMS_ROWS = "#fmsMonitorFacilityTB tr.jqgrow"

# 주의: FMS 검색 입력은 소문자 id(#searchtext), 버튼도 NMS와 다름(#searchBtn).
SEL_FMS_SEARCH_INPUT = "#searchtext"
SEL_FMS_SEARCH_BTN = "#searchBtn"

SEL_FMS_LOADING_TEXT = "#load_fmsMonitorFacilityTB"
SEL_FMS_LOADING_MASK = "#lui_fmsMonitorFacilityTB"

# FMS 행 안의 td — 담당자 이름은 "설명"(z_mydesc) 컬럼에 들어 있다.
SEL_FMS_TD_HOST = "td[aria-describedby='fmsMonitorFacilityTB_z_nodename']"
SEL_FMS_TD_OWNER = "td[aria-describedby='fmsMonitorFacilityTB_z_mydesc']"
