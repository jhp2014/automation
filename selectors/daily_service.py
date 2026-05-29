"""DailyService(SDS) 셀렉터 상수.

규약 v1.1 따름. 원본 daily-service-mon.py의 셀렉터/URL을 그대로 옮겼다
(추측 금지). 셀렉터 상수는 ``SEL_`` 접두사를 쓴다.
"""

from __future__ import annotations


# --- URL (비밀 아님) ---
BASE_URL = "http://sds.aicando.co.kr:8081/"
LOGIN_URL = "http://sds.aicando.co.kr:8081/login"


# --- 로그인 폼 ---
SEL_USERNAME = "#username"
SEL_PASSWORD = "#password"
SEL_LOGIN_SUBMIT = "button[type='submit']"


# --- 대시보드: 비정상 서비스 카운트 ---
# div.error_s > span 의 inner text가 정수.
SEL_ABNORMAL_COUNT = "div.error_s span"
