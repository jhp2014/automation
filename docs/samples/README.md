# samples

파서 회귀 확인용 실제 응답 샘플. 비밀값 없음(맵 이름·집계 수치만).

| 파일 | 출처 | 내용 |
|---|---|---|
| `whatsup_main_normal.html` | `http://nms.kyowon.co.kr/` 메인 body | 맵 16개, 전부 `Items Down = 0` |
| `whatsup_maptable_down.html` | 같은 페이지의 맵 표 부분만 | 맵 16개, `교원목동 7B6` / `교원목동 7B7` 각 1대 down |

`jobs/whatsup/parser.py` 를 손봤을 때 두 파일로 확인한다:

```bat
.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); from jobs.whatsup.parser import parse_map_rows; rows=parse_map_rows(open('docs/samples/whatsup_maptable_down.html',encoding='utf-8').read()); print(len(rows), [(r.name, r.down) for r in rows if r.down])"
```

기대값: `16 [('교원목동 7B6', 1), ('교원목동 7B7', 1)]`
