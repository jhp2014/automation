"""
============================================================================
Project Flattener for AI (Python version)
============================================================================

모노레포/프로젝트를 폴더 단위로 하나의 txt 파일로 flat화하여
AI(ChatGPT, Claude 등)에 업로드하기 쉽게 만들어주는 스크립트입니다.

----------------------------------------------------------------------------
실행 방법
----------------------------------------------------------------------------

1) Python 3.9+ 설치 확인
     python --version
     (또는 python3 --version)

2) 이 파일 상단의 "설정 영역(CONFIG)" 값을 본인 환경에 맞게 수정

3) 실행
     python flatten.py
     (또는 python3 flatten.py)

별도 외부 패키지 설치 불필요 (표준 라이브러리만 사용).

----------------------------------------------------------------------------
동작 방식
----------------------------------------------------------------------------

- 기본적으로 "각 폴더마다" 하나의 txt 파일을 생성합니다.
  (그 폴더에 직접 들어 있는 파일들만 포함)

- BUNDLE_DIRS 에 지정한 폴더는 하위 폴더 전체를 재귀적으로 모아서
  "하나의 txt 파일"로 합쳐 출력합니다. 그 내부 폴더들은 별도 txt를
  생성하지 않습니다.

- 각 txt 파일 상단에는 포함된 파일들의 인덱스(목록)가 먼저 들어가고,
  그 아래에 각 파일의 실제 내용이 구분선과 함께 이어집니다.

- 실행할 때마다 OUT 폴더를 비우고 새로 채웁니다.
  (안전을 위해 OUT은 ROOT 바깥에 두어야 합니다.)
============================================================================
"""

import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# ============================================================================
# CONFIG  ← 여기만 수정하면 됩니다
# ============================================================================

# 탐색을 시작할 루트 경로 (절대경로 또는 이 스크립트 기준 상대경로)
ROOT = "."

# 결과 txt 파일을 저장할 폴더 (반드시 ROOT 바깥에 위치할 것)
OUT = "../kwop-auto-flattened"

# 포함할 확장자 (점 없이, 소문자)
EXTENSIONS = {"ts", "tsx", "js", "jsx", "json", "md", "css", "py", "example"}

# 제외할 폴더명 (이름이 일치하면 통째로 스킵)
EXCLUDE_DIRS = {
    "node_modules",
    "dist",
    "build",
    ".git",
    ".next",
    ".turbo",
    "coverage",
    "out",
    ".venv",
    "__pycache__",
    ".cache"
}

# 하위까지 하나의 txt로 묶을 상위 폴더 (ROOT 기준 상대경로)
# 예: ["packages/ui", "apps/web"]  → 각각 하나의 txt로 통합
BUNDLE_DIRS = [
    "apps/batch",
    "apps/chart-capture",
    "apps/data-view",
    "apps/feature-processor",
    "packages/data-core",
    # "apps/web",
]

# 단일 파일 최대 크기(byte). 이보다 큰 파일은 스킵 (기본 1MB)
MAX_FILE_SIZE = 1 * 1024 * 1024

# ============================================================================
# 이하 구현부 (수정 불필요)
# ============================================================================


def is_target_file(file_name: str) -> bool:
    ext = Path(file_name).suffix.lstrip(".").lower()
    return ext in EXTENSIONS


def find_bundle_root(abs_path: Path, bundle_roots: list) -> Path | None:
    """abs_path가 bundle_roots 중 하나의 하위(또는 동일)면 그 bundle 루트를 반환."""
    for b in bundle_roots:
        try:
            abs_path.relative_to(b)
            return b
        except ValueError:
            continue
    return None


def safe_file_name(rel_path: str) -> str:
    """폴더 상대경로를 파일명으로 변환. 예: packages/ui -> packages__ui.txt"""
    cleaned = rel_path.strip("./\\").replace("\\", "/").replace("/", "__")
    return (cleaned or "root") + ".txt"


def format_file_block(rel_path: str, content: str) -> str:
    sep = "=" * 80
    return f"\n{sep}\nFILE: {rel_path}\n{sep}\n{content}\n"


def main() -> None:
    root = Path(ROOT).resolve()
    out = Path(OUT).resolve()
    bundle_roots = [(root / b).resolve() for b in BUNDLE_DIRS]

    print("--- Flatten Options ---")
    print(f"root         : {root}")
    print(f"out          : {out}")
    print(f"extensions   : {', '.join(sorted(EXTENSIONS))}")
    print(f"excludeDirs  : {', '.join(sorted(EXCLUDE_DIRS))}")
    print(f"bundleDirs   : {', '.join(str(b) for b in bundle_roots) if bundle_roots else '(none)'}")
    print(f"maxFileSize  : {MAX_FILE_SIZE}")
    print("-----------------------\n")

    if not root.exists():
        print(f"Root path does not exist: {root}", file=sys.stderr)
        sys.exit(1)

    # 안전장치: OUT이 ROOT 내부거나 ROOT 자체면 위험하므로 중단
    try:
        out.relative_to(root)
        print(
            f"[abort] OUT 경로가 ROOT 내부입니다. OUT을 ROOT 바깥으로 지정하세요.\n"
            f"  ROOT: {root}\n  OUT : {out}",
            file=sys.stderr,
        )
        sys.exit(1)
    except ValueError:
        pass  # OUT이 ROOT 바깥 → 정상

    # 이전 결과 정리: OUT 폴더가 있으면 통째로 지우고 새로 생성
    if out.exists():
        shutil.rmtree(out)
        print(f"[cleaned] {out}")
    out.mkdir(parents=True, exist_ok=True)

    # 출력 버퍼: {출력 txt 절대경로: [(상대경로, 블록), ...]}
    buffers: dict = {}

    def append_to_buffer(out_file: Path, rel_path: str, block: str) -> None:
        buffers.setdefault(out_file, []).append((rel_path, block))

    # os.walk 로 재귀 탐색 (dirnames를 in-place로 수정하면 가지치기 가능)
    for current_dir, dirnames, filenames in os.walk(root):
        # 제외 폴더 가지치기
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]

        current_path = Path(current_dir)
        bundle_root = find_bundle_root(current_path, bundle_roots)

        for fname in filenames:
            if not is_target_file(fname):
                continue

            file_path = current_path / fname

            try:
                size = file_path.stat().st_size
            except OSError:
                continue

            if size > MAX_FILE_SIZE:
                print(f"[skip-too-large] {file_path} ({size} bytes)")
                continue

            try:
                content = file_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                print(f"[skip-read-fail] {file_path}")
                continue

            rel_from_root = file_path.relative_to(root).as_posix()

            # 출력 대상 결정
            if bundle_root is not None:
                rel_bundle = bundle_root.relative_to(root).as_posix()
                out_file = out / safe_file_name(rel_bundle)
            else:
                rel_dir = current_path.relative_to(root).as_posix()
                out_file = out / safe_file_name(rel_dir)

            append_to_buffer(out_file, rel_from_root, format_file_block(rel_from_root, content))

    # 버퍼 → 파일 기록
    total_files = 0
    total_bytes = 0
    generated_at = datetime.now(timezone.utc).isoformat()

    for out_file, entries in buffers.items():
        # 경로 기준으로 정렬 (인덱스/본문 모두 일관된 순서로)
        entries.sort(key=lambda x: x[0])

        rel_paths = [rel for rel, _ in entries]
        blocks = [blk for _, blk in entries]

        # 인덱스 섹션 구성
        index_sep = "=" * 80
        index_lines = [
            index_sep,
            "INDEX (files included in this bundle)",
            index_sep,
        ]
        for i, rel in enumerate(rel_paths, start=1):
            index_lines.append(f"{i:>4}. {rel}")
        index_lines.append(index_sep)
        index_section = "\n".join(index_lines)

        header = (
            f"# Flattened Source\n"
            f"# Source root: {root}\n"
            f"# Generated  : {generated_at}\n"
            f"# File count : {len(entries)}\n\n"
            f"{index_section}\n"
        )

        body = header + "\n".join(blocks)
        out_file.write_text(body, encoding="utf-8")

        size = len(body.encode("utf-8"))
        total_files += 1
        total_bytes += size
        print(f"[written] {out_file}  ({len(entries)} files, {size} bytes)")

    print(f"\nDone. {total_files} txt files, total {total_bytes} bytes.")


if __name__ == "__main__":
    main()
