"""WhatsUp Gold 메인 페이지 HTML 파서 (표준 라이브러리만 사용).

파싱 전략:
    1. 모든 ``<tr>`` 의 셀(텍스트 + href)을 수집한다. 중첩 테이블이 있어도
       가장 안쪽 행만 온전히 남고 바깥 행은 빈 채로 버려진다.
    2. ``Map`` / ``Items Down`` 라벨이 함께 있는 행을 헤더로 보고 열 인덱스를
       거기서 계산한다(열 순서 변경에 견딘다).
    3. 헤더 아래에서 열 수가 같고 첫 열이 ``map.asp?...`` 링크인 행만 데이터로
       취급한다.

**조용한 실패 금지**: 맵 행이 하나도 안 잡히면 "정상 0건"이 아니라
:class:`RuntimeError` 로 죽는다. 사이트 개편/인증 실패 페이지를 정상으로
오인해 장애를 놓치는 것이 가장 위험하기 때문이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple

from site_selectors import whatsup as W


@dataclass(frozen=True)
class MapStatus:
    """맵 1개의 상태 한 줄.

    Attributes:
        name: 맵 이름(첫 열 링크 텍스트, 좌우 공백 제거).
        up: Items Up 수.
        down: Items Down 수.
        services_down: Items with Services Down 수.
    """

    name: str
    up: int
    down: int
    services_down: int


@dataclass
class _Cell:
    """수집 중인 셀 하나 (텍스트 조각 + 첫 번째 href)."""

    text_parts: List[str] = field(default_factory=list)
    href: Optional[str] = None


class _RowParser(HTMLParser):
    """``<tr>`` 단위로 셀을 모으는 최소 파서."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: List[List[_Cell]] = []
        self._row: Optional[List[_Cell]] = None
        self._cell: Optional[_Cell] = None

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag == "tr":
            self._row = []
            self._cell = None
        elif tag in ("td", "th"):
            if self._row is None:
                self._row = []
            self._cell = _Cell()
        elif tag == "br":
            # "Items<br>Down" 이 "ItemsDown" 으로 붙지 않도록 공백을 끼운다.
            if self._cell is not None:
                self._cell.text_parts.append(" ")
        elif tag == "a":
            if self._cell is not None and self._cell.href is None:
                for k, v in attrs:
                    if k.lower() == "href":
                        self._cell.href = v or ""
                        break

    def handle_startendtag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        # "<br/>" 형태도 handle_starttag 와 동일하게 처리.
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th"):
            if self._cell is not None and self._row is not None:
                self._row.append(self._cell)
            self._cell = None
        elif tag == "tr":
            if self._row is not None:
                self.rows.append(self._row)
            self._row = None
            self._cell = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.text_parts.append(data)


def _norm(cell: _Cell) -> str:
    """셀 텍스트를 공백 정규화해 반환한다."""
    return " ".join("".join(cell.text_parts).split())


def _to_int(text: str, map_name: str, label: str) -> int:
    """셀 텍스트를 정수로 변환한다.

    Args:
        text: 셀의 정규화된 텍스트.
        map_name: 오류 메시지에 쓸 맵 이름.
        label: 오류 메시지에 쓸 열 이름.

    Returns:
        정수 값.

    Raises:
        RuntimeError: 정수로 파싱할 수 없는 경우.
    """
    try:
        return int(text)
    except ValueError as e:
        raise RuntimeError(
            f"숫자 파싱 실패: map='{map_name}' col='{label}' value='{text}'"
        ) from e


def parse_map_rows(html: str) -> List[MapStatus]:
    """메인 페이지 HTML 에서 맵별 Up/Down 수를 뽑는다.

    Args:
        html: 디코딩된 메인 페이지 HTML 전문.

    Returns:
        맵 상태 리스트(페이지에 나온 순서 유지).

    Raises:
        RuntimeError: 헤더 행을 못 찾았거나, 맵 데이터 행이 0건이거나,
            숫자 열을 정수로 파싱하지 못한 경우.
    """
    parser = _RowParser()
    parser.feed(html)
    parser.close()

    header_pos: Optional[int] = None
    cols: Dict[str, int] = {}

    for i, row in enumerate(parser.rows):
        texts = [_norm(c) for c in row]
        if W.LABEL_MAP in texts and W.LABEL_ITEMS_DOWN in texts:
            header_pos = i
            cols = {
                "map": texts.index(W.LABEL_MAP),
                "up": texts.index(W.LABEL_ITEMS_UP),
                "down": texts.index(W.LABEL_ITEMS_DOWN),
                "services_down": texts.index(W.LABEL_SERVICES_DOWN),
            }
            break

    if header_pos is None:
        raise RuntimeError(
            "맵 표의 헤더 행을 찾지 못했습니다"
            f" ('{W.LABEL_MAP}' + '{W.LABEL_ITEMS_DOWN}')."
            " 인증 실패 페이지이거나 사이트 구조가 바뀌었을 수 있습니다."
        )

    width = len(parser.rows[header_pos])
    results: List[MapStatus] = []

    for row in parser.rows[header_pos + 1:]:
        if len(row) != width:
            continue
        href = row[cols["map"]].href or ""
        if not href.startswith(W.MAP_LINK_PREFIX):
            continue

        name = _norm(row[cols["map"]])
        results.append(
            MapStatus(
                name=name,
                up=_to_int(_norm(row[cols["up"]]), name, W.LABEL_ITEMS_UP),
                down=_to_int(_norm(row[cols["down"]]), name, W.LABEL_ITEMS_DOWN),
                services_down=_to_int(
                    _norm(row[cols["services_down"]]), name, W.LABEL_SERVICES_DOWN
                ),
            )
        )

    if not results:
        raise RuntimeError(
            "맵 데이터 행이 0건입니다. 정상 상황이 아니므로 실패로 처리합니다."
        )

    return results
