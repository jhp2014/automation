"""Zenius(EMS/SMS) 셀렉터 상수.

규약 v1.1 따름. 셀렉터 문자열은 원본 zenius_monitor_v2.0.py에서 그대로 옮겼다
(추측 금지). 모든 상수는 ``SEL_`` 접두사를 쓴다.
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
