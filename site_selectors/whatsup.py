"""WhatsUp Gold(NMS) 파싱 상수.

규약 v1.1 따름. 본 사이트는 JS 없는 서버렌더 HTML(WhatsUp Gold 8.0 classic)이라
CSS 셀렉터 대신 **표 헤더 라벨**로 열을 찾는다. 열 순서가 바뀌어도 헤더에서
인덱스를 다시 계산하므로 파서가 깨지지 않는다.

로그인은 HTML 폼이 아니라 HTTP 인증(브라우저 네이티브 401 프롬프트)이다.
따라서 로그인 셀렉터는 존재하지 않으며, 자격증명은 요청 헤더로 넘긴다
(:mod:`jobs.whatsup.__main__` 의 ``fetch_main_html``).
"""

from __future__ import annotations


# --- URL (비밀 아님) ---
BASE_URL = "http://nms.kyowon.co.kr/"


# --- 응답 디코딩 후보 (Content-Type 에 charset 이 없을 때 순서대로 시도) ---
# WhatsUp Gold 8.0 classic 는 charset 을 안 실어 보내는 경우가 있고, 그때
# requests 기본 추정(ISO-8859-1)으로 가면 한글 맵 이름이 깨진다.
ENCODING_CANDIDATES = ("euc-kr", "utf-8")


# --- 표 헤더 라벨 (<br> 은 공백으로 정규화한 뒤 비교) ---
LABEL_MAP = "Map"
LABEL_ITEMS_UP = "Items Up"
LABEL_ITEMS_DOWN = "Items Down"
LABEL_SERVICES_DOWN = "Items with Services Down"


# --- 데이터 행 판별: 첫 열 링크가 맵 상세 페이지를 가리키는 행만 취급 ---
MAP_LINK_PREFIX = "map.asp?"
