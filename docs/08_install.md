# 08. 설치 (새 PC 세팅)

> 기준 시점: 현재 코드 일치. Windows + 단일 `.venv` 전제. KWorks/Jennifer 등 사이트들은 사설망 환경 가정.

## 0) 사전 요구

| 요소 | 요구 | 비고 |
|------|------|------|
| OS | **Windows 10/11** | capture job 이 `pywin32` + `mss` + 좌표 더블클릭 사용. WSL/원격 세션에서는 모니터 인식이 안 될 수 있음 |
| Python | **3.10 이상** | `int.bit_count()`(3.10+), `match` 문 사용 안 함이지만 typing 표기 등이 3.10 기준 |
| Git | 임의 버전 | 코드 클론용 |
| 디스플레이 | **실제 모니터 1대 이상** | capture 가 좌측 모니터 선택 → 캡처. 헤드리스/원격 데스크톱 환경은 부분 동작 |

설치된 Python 확인:
```powershell
py -3 --version           # 또는 python --version
```
3.10 미만이면 [python.org](https://www.python.org/downloads/windows/) 에서 3.10+ 설치.

## 1) 소스 가져오기

원하는 위치로 클론:
```powershell
cd C:\Users\<you>\Dev
git clone <repo-url> kwop
cd kwop\automation
```

이 폴더(`automation\`)가 모든 경로의 기준(`config.BASE_DIR`)이 된다.

## 2) `.venv` 생성

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python --version          # 3.10+ 인지 재확인
```

> PowerShell 실행 정책 오류가 나면: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

## 3) 의존성 설치

본 프로젝트는 `pyproject.toml` 로 모든 의존성을 선언한다. editable install 한 번이면 끝.

```powershell
python -m pip install --upgrade pip
python -m pip install -e .
```

설치되는 패키지 (코드 import 기준):

| 패키지 | 용도 |
|--------|------|
| `playwright` | 모든 브라우저 job (zenius, daily_service, jennifer, server, capture) |
| `python-dotenv` | `.env` 로딩 (`common.config`) |
| `requests` | Pushover/Telegram HTTP (`common.notify`) |
| `pydantic>=2` | `jobs.yaml`, `daily.yaml`, `jennifer_sites.json` 스키마 검증 |
| `pyyaml` | YAML 파싱 |
| `supabase` | runner 데드맨 스위치 heartbeat |
| `mss` | capture: 좌측 모니터 스크린샷 (Windows + Linux + Mac) |
| `Pillow` | capture: 이미지 처리 + dHash 비교 |
| `pywin32` | capture: 윈도우 열거 / 가림 검사 (Windows 전용) |

### 선택: 버전 핀이 고정된 `requirements.txt` 만들기

재현 가능한 배포가 필요하면 현재 설치 상태를 동결:
```powershell
python -m pip freeze > requirements.txt
git add requirements.txt
```

## 4) Playwright 브라우저 설치

deps 설치 후 별도로 Chromium 바이너리를 받아야 한다 (~150MB).

```powershell
python -m playwright install chromium
```

확인:
```powershell
python -m playwright install --dry-run chromium     # already installed → 출력 없음
```

## 5) 비밀값 파일 두 개

`.env` 와 `config/daily.yaml` 둘 다 git ignore. 템플릿에서 복사 후 채운다.

> 참고: 동작 토글 파일 `config/settings.yaml` 은 비밀이 아니라 git 추적 대상이며,
> 레포에 이미 들어 있다 — `.example` 을 복사할 필요가 없다. 헤드풀 전환 등은
> 본 파일을 직접 편집하면 된다 (자세한 내용은 [02_common_api](02_common_api.md)
> 의 `common.settings` 섹션 참고).

### 5-A) `.env` (거의 안 바뀜)

```powershell
Copy-Item .env.example .env
notepad .env
```

채워야 할 키 (값이 비면 해당 기능 사용 시점에 명확한 에러로 알려준다):

- `PUSHOVER_TOKEN`, `PUSHOVER_USER`
- `TELEGRAM_BOT__ZENIUS`, `TELEGRAM_CHAT__ZENIUS__REPORT`, `TELEGRAM_CHAT__ZENIUS__HEARTBEAT`
- `TELEGRAM_BOT__DAILYSERVICE`, `TELEGRAM_CHAT__DAILYSERVICE__HEARTBEAT`
- `ZENIUS_USER_ID`, `ZENIUS_USER_PW`
- `DAILYSERVICE_USER_ID`, `DAILYSERVICE_USER_PW`
- `JENNIFER_PW__JENNIFER_CLOUD`, `JENNIFER_PW__JENNIFER_GROUP_SITE`, `JENNIFER_PW__JENNIFER_LMS`, `JENNIFER_PW__JENNIFER_REDPEN`
- `SUPABASE_URL`, `SUPABASE_KEY` ← **runner 가 비어 있으면 시작 자체를 거부** (데드맨 스위치 약화 금지 조항)

### 5-B) `config/daily.yaml` (매일 갱신)

```powershell
Copy-Item config\daily.yaml.example config\daily.yaml
notepad config\daily.yaml
```

채울 항목:
- `run_until`: runner 자동 종료 시각 `"YYYY-MM-DD HH:MM"` 또는 빈 문자열(무기한).
- `kworks.user_id`, `kworks.user_pw`, `kworks.target_title`: KWorks 자격증명 + 매일 바뀌는 작업 제목.
- `server_times`: `jobs.server` 의 one_time 항목들. 비어 있으면 `jobs.yaml` 의 폴백을 본다.

## 6) Jennifer 사이트 목록 확인

`config/jennifer_sites.json` 은 git 추적. 사이트 추가/수정 시 본 파일을 편집하고 비밀번호는 `.env` 의 `JENNIFER_PW__<NAME_UPPER>` 키에 추가.

## 7) 첫 동작 검증

세 단계로 확장하며 점검한다. 각 단계가 끝까지 성공해야 다음으로 넘어간다.

### 7-1) dry-run-all — 자격증명/세션/페이지 진입 점검

```powershell
.\scripts\dry-run-all.bat
```
- zenius / daily_service / jennifer 세 개를 `--dry-run` 으로 차례 실행
- 각 job 의 [START] → 로그인 도달 → [DRY-RUN] ... 종료 로그 확인
- Summary 줄의 `zenius=0  daily_service=0  jennifer=0` 면 OK
- 하나라도 0 이 아니면 `logs/<job>.log` 의 마지막 `[FAIL] stage=` 라인부터 본다

### 7-2) capture-baseline — 좌측 모니터 + 4개 창 인식 확인

운영 사이트 4개 (WhatsUp / Zenius / ETL / Dashboard) 가 좌측 모니터에 떠 있는 상태에서:

```powershell
.\scripts\capture-baseline.bat
```
- 4개 창 매칭, 겹침 ≥90%, 가림 없음을 통과해야 baseline 생성
- 산출물: `captures/capture/baseline_left_monitor.png` + `latest_path.txt`
- 실패 메시지 (`Missing targets`, `Minimized`, `Low overlap`, `Occlusion`) 를 보고 창 배치를 조정

### 7-3) runner — 메인 루프

```powershell
.\scripts\runner.bat
```
- 시작 시 `==== [RUNNER START] ====`, jobs 로딩, 1회 heartbeat 송신 로그 확인
- Supabase 콘솔에서 `runner_heartbeat.last_seen_at` 갱신 확인
- `Ctrl+C` 로 종료, `==== [RUNNER END] ====` 확인

## 8) bat 단축 (위치 무관)

`scripts/` 안의 모든 bat 은 자기 위치(`%~dp0`) 기준으로 프로젝트 루트를 잡으므로 **어디서 실행해도** 동작한다 (더블클릭, 작업 스케줄러, cmd 등).

`.venv` 가 없는 상태에서 bat 을 실행하면 다음 메시지를 보고 멈춘다:

```
ERROR: Python not found at ...\.venv\Scripts\python.exe
Please create .venv and install deps. See docs/08_install.md.
```

이 경우 위 2~4단계를 다시 밟는다.

## 9) Windows 전용 주의

- **capture** 는 `pywin32`(윈도우 열거) + `mss`(스크린샷) + 좌표 더블클릭에 의존. 다음 환경에서는 부분 동작 또는 미동작:
  - 원격 데스크톱 세션이 끊긴 상태(콘솔이 잠겨 있음) — `mss` 가 모니터를 인식 못 함
  - WSL / Linux — `pywin32` import 실패
  - 헤드리스 가상 서버 — 디스플레이 부재로 캡처 빈 화면
- **jennifer** 의 캔버스 더블클릭은 헤드리스에서 가끔 불안정. `config/settings.yaml` 의 `jobs.jennifer.headless` 를 `false` 로 바꾸면 헤드풀로 전환된다 (코드 흐름 동일).
- `chcp 65001` 가 bat 첫 줄에 있어 한국어 폴더명 / 작업 제목이 cmd → Python 으로 깨지지 않는다. 콘솔 폰트가 한국어를 못 그리면 표시가 깨질 수 있지만 데이터는 정상.

## 10) 트러블슈팅 한 줄

| 증상 | 확인 |
|------|------|
| bat 더블클릭 시 즉시 "Python not found" | 2~4단계 .venv + deps 설치 |
| `BrowserType.launch: Executable doesn't exist` | 4단계 `playwright install chromium` |
| `Supabase 자격증명 누락` 으로 runner 즉시 종료 | 5-A) `SUPABASE_URL` / `SUPABASE_KEY` |
| `daily.yaml 의 kworks.user_id 가 비어 있습니다` | 5-B) `config/daily.yaml` 의 kworks 채우기 |
| `--target-title 도 daily.yaml ... 도 비어 있습니다` | 5-B) `kworks.target_title` 또는 CLI `--target-title` |
| jennifer 팝업 미발생 | `logs/jennifer.log` 의 `캔버스 box: width=... height=...` 로 캔버스 크기 확인. 0 이면 차트 렌더 실패 |
| heartbeat 3회 연속 실패 Pushover | SUPABASE_KEY 만료 / 네트워크 차단 / 테이블 권한 확인 |

자세한 운영 가이드: [06. 운영](06_operations.md). runner 내부: [07. runner](07_runner.md).
