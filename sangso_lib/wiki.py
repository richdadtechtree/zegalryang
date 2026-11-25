"""
wiki.py — 주공의 위키(마크다운 노트 폴더)를 읽어 '지난 상소 이후 새로 생긴 것'만 골라내는 곳

왜 '새로 생긴 것'만인가?
  위키 전체를 매일 넘기면 제갈량이 어제 한 말을 또 하게 됩니다(같은 재료 → 같은 조언).
  그래서 위키를 읽을 때마다 사진(스냅숏)을 찍어 archive/wiki_snap/날짜.json 에 저장해 두고,
  다음 날에는 '지난 사진과 비교해 바뀐 줄'만 제갈량에게 건넵니다. (git diff 와 같은 원리)

어떤 순서로 담나요? (글자 수 예산 안에서)
  ① 새로 생긴 파일            → 내용 전체(길면 앞뒤만)
  ② 바뀐 파일                 → 새로 추가·수정된 줄만
  ③ '늘 곁에 둘' 파일(pinned) → 바뀌지 않았어도 짧게 요약용으로 (예: '1년 뒤 나에게')
     config.json 의 wiki_pinned 에 파일·폴더 이름의 일부를 적으면 됩니다.
  • wiki_focus(기본 '어제보다 나은 오늘' 폴더)에 든 것은 ①②③ 안에서 맨 앞에 둡니다.
  • wiki_low(기본 '건강/운동 기록')처럼 매일 쌓이는 기록은 맨 뒤에, 가장 최근 1개만 짧게.
  • 클로드 코드 대화 정리 노트(wiki_summaries, 기본 '코딩/Claude 대화')는 '막혔던 점'·'다음 할 일' 칸만
    꺼내 맨 앞에 둡니다. 최근 todo_days 일 노트의 체크 안 된 '다음 할 일'은 '진행 중'으로 매일 함께 넘깁니다.
  • 군령 파일(orders_file)은 프로그램이 직접 적는 파일이라 여기서 읽지 않습니다(군령 장부로 따로 넘김).

디버깅 힌트
  - "위키 폴더를 찾지 못함" → config.json 의 wiki_dir 에 전체 경로를 적으세요.
    윈도우는 역슬래시를 두 번(C:\\\\Users\\\\나\\\\wiki) 또는 슬래시(C:/Users/나/wiki)로.
  - 첫 실행 날은 '지난 사진'이 없어서, 최근 wiki_days 일 안에 고친 파일을 '새 파일'처럼 넘깁니다.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

log = logging.getLogger("sangso")

EXTS = {".md", ".markdown", ".txt"}
# 노트 앱이 만드는 설정·휴지통 폴더는 주공의 글이 아니므로 건너뜁니다 (Obsidian, git, Logseq 등)
SKIP_DIRS = {".obsidian", ".git", ".trash", ".logseq", "logseq", "node_modules", ".stfolder"}
MAX_FILE_BYTES = 400_000      # 이보다 큰 파일(붙여 넣은 로그 등)은 읽지 않음
PER_FILE_CHARS = 2_500        # 파일 하나에서 넘길 최대 글자 수
KEEP_SNAPS = 5                # 사진은 최근 5장만 남김


def find_dir(configured: str) -> Path | None:
    """config 의 wiki_dir → 없으면 흔한 위치(홈, 문서 폴더 아래 'wiki'·'위키' 이름)를 찾아봅니다."""
    if configured:
        p = Path(configured).expanduser()
        if p.is_dir():
            return p
        log.warning("위키 폴더를 찾지 못함 (config.json 의 wiki_dir 확인): %s", p)
        return None
    home = Path.home()
    for parent in (home, home / "Documents", home / "문서", home / "OneDrive" / "Documents"):
        for name in ("wiki", "Wiki", "WIKI", "위키", "my-wiki", "mywiki"):
            if (parent / name).is_dir():
                return parent / name
    return None


def _files(root: Path) -> list[Path]:
    out = []
    for p in root.rglob("*"):
        rel_parts = p.relative_to(root).parts
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel_parts[:-1]):
            continue
        if p.suffix.lower() in EXTS and p.is_file():
            try:
                if p.stat().st_size <= MAX_FILE_BYTES:
                    out.append(p)
            except OSError:
                pass
    return out


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        log.warning("위키 파일을 읽지 못함 %s: %s", p, e)
        return ""


def _clip(text: str, limit: int = PER_FILE_CHARS) -> str:
    """길면 앞 1/3 + 뒤 2/3 를 남깁니다. (일지처럼 아래에 덧붙이는 글은 뒤쪽이 최신이라서)"""
    text = text.strip()
    if len(text) <= limit:
        return text
    head = limit // 3
    return text[:head] + "\n…(중략)…\n" + text[-(limit - head):]


def _added_lines(old: str, new: str) -> str:
    """지난 사진 대비 새로 생기거나 바뀐 줄만 꺼냅니다. 지운 줄은 넘기지 않습니다."""
    added = [line[1:] for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0)
             if line.startswith("+") and not line.startswith("+++")]
    return "\n".join(l for l in added if l.strip())


# ──────────────────────────────────────────────────────────────
# 사진(스냅숏) 저장·불러오기
# ──────────────────────────────────────────────────────────────
def _snap_dir(archive: Path) -> Path:
    return archive / "wiki_snap"


def load_prev_snapshot(archive: Path, today: date) -> tuple[dict, str | None]:
    """오늘 이전 가장 최근 사진. (같은 날 --force 로 다시 써도 '어제와 비교'가 유지되게 오늘 것은 제외)"""
    snaps = sorted(f for f in _snap_dir(archive).glob("????-??-??.json") if f.stem < today.isoformat())
    if not snaps:
        return {}, None
    try:
        return json.loads(snaps[-1].read_text(encoding="utf-8")), snaps[-1].stem
    except (OSError, json.JSONDecodeError):
        return {}, None


def save_snapshot(archive: Path, today: date, snap: dict) -> None:
    """상소를 다 쓴 뒤에만 부릅니다. (실패한 날 사진을 찍으면 그날 바뀐 내용이 영영 빠지므로)"""
    d = _snap_dir(archive)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{today.isoformat()}.json").write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    for old in sorted(d.glob("????-??-??.json"))[:-KEEP_SNAPS]:
        old.unlink(missing_ok=True)


# ──────────────────────────────────────────────────────────────
# 본체
# ──────────────────────────────────────────────────────────────
def _sections(text: str, names: list[str]) -> str:
    """마크다운에서 '## 이름…' 으로 시작하는 칸들만 이 순서대로 꺼냅니다. (내용이 '없음'뿐인 칸은 뺌)"""
    parts = re.split(r"(?m)^(?=## )", text)
    out = []
    for name in names:
        for part in parts:
            if part.startswith(f"## {name}"):
                body = part.split("\n", 1)[1].strip() if "\n" in part else ""
                if body and body not in ("- 없음", "없음"):
                    out.append(part.strip())
    return "\n\n".join(out)


def collect(cfg: dict, archive: Path, today: date) -> tuple[str, dict, dict]:
    """(제갈량에게 넘길 글, 오늘의 사진, 통계)를 돌려줍니다. 위키가 없으면 빈 글."""
    root = find_dir(cfg.get("wiki_dir", ""))
    stats = {"dir": str(root) if root else "", "new": 0, "changed": 0, "pinned": 0, "low": 0}
    if not root:
        return "(위키 폴더 없음 — config.json 의 wiki_dir 에 경로를 적으면 함께 읽습니다)", {}, stats

    prev, prev_day = load_prev_snapshot(archive, today)
    budget = int(cfg.get("wiki_max_chars", 30000))
    since = datetime.now() - timedelta(days=int(cfg.get("wiki_days", 14)))
    pinned_keys = [k.lower() for k in cfg.get("wiki_pinned", []) if k]
    focus_keys = [k.lower() for k in cfg.get("wiki_focus", ["어제보다 나은 오늘"]) if k]
    low_keys = [k.lower() for k in cfg.get("wiki_low", ["건강/운동 기록"]) if k]
    orders_rel = str(cfg.get("orders_file", "어제보다 나은 오늘/군령.md")).replace("\\", "/").lower()
    low_blocks: list[str] = []
    # 클로드 코드 대화를 날마다 정리한 노트 (claude_to_obsidian.py 가 만드는 YYYY-MM-DD-claude.md)
    # → '막혔던 점'(막힌 일)과 '다음 할 일'(진행 중)을 여기서 직접 꺼냅니다
    summary_keys = [k.lower() for k in cfg.get("wiki_summaries", ["코딩/Claude 대화"]) if k]
    summary_secs = ["막혔던 점", "다음 할 일"]   # 다룬 주제는 클로드 코드 기록으로 따로 들어가므로 뺌
    todo_since = (today - timedelta(days=int(cfg.get("todo_days", 7)))).isoformat()
    open_todos: list[str] = []

    snap, new_blocks, changed_blocks, pinned_blocks = {}, [], [], []
    # 최근에 고친 파일부터 → 예산이 모자라면 오래된 것이 빠집니다
    for p in sorted(_files(root), key=lambda f: f.stat().st_mtime, reverse=True):
        rel = p.relative_to(root).as_posix()
        if rel.lower() == orders_rel:
            continue
        text = _read(p)
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
        mtime = datetime.fromtimestamp(p.stat().st_mtime)
        snap[rel] = {"sha": digest, "text": text}
        when = f"{mtime:%m-%d %H:%M}"
        old = prev.get(rel)
        is_focus = any(k in rel.lower() for k in focus_keys)
        is_low = any(k in rel.lower() for k in low_keys)
        is_summary = any(k in rel.lower() for k in summary_keys)
        if is_summary:
            is_focus = True
            m = re.search(r"(\d{4}-\d{2}-\d{2})", p.name)
            day = m.group(1) if m else f"{mtime:%Y-%m-%d}"
            if todo_since <= day < today.isoformat() or (day == today.isoformat()):
                # 체크 안 된 '다음 할 일' = 아직 진행 중인 일 (체크 안 한 건 매일 다시 올라와도 됨)
                todo = _sections(text, ["다음 할 일"])
                open_todos += [f"- ({day}) {l.strip()[6:]}" for l in todo.splitlines() if l.strip().startswith("- [ ]")]

        def put(blocks: list, block: str) -> None:
            if is_low:                      # 매일 쌓이는 기록: 가장 최근 것 1개만
                if not low_blocks:
                    low_blocks.append(block[:800])
            elif is_focus:
                blocks.insert(sum(1 for b in blocks if "★" in b), block.replace("### [", "### ★[", 1))
            else:
                blocks.append(block)

        if old is None:
            # 첫 실행이면 최근 wiki_days 안에 고친 것만 '새 것'으로 칩니다 (위키 전체가 쏟아지지 않게)
            if prev_day is None and mtime < since:
                pass
            elif text.strip():
                if is_summary:   # 앞에서부터 (막혔던 점 → 다음 할 일), 중간이 잘리지 않게
                    body = _sections(text, summary_secs) or "(막혔던 점·다음 할 일 없음)"
                    body = body if len(body) <= 3500 else body[:3500] + "\n…(뒤 생략)"
                else:
                    body = _clip(text)
                put(new_blocks, f"### [새 파일] {rel} ({when})\n{body}")
                continue
        elif old.get("sha") != digest:
            added = _added_lines(old.get("text", ""), text)
            if added:
                put(changed_blocks, f"### [바뀐 줄] {rel} ({when})\n{_clip(added)}")
                continue
        if pinned_keys and any(k in rel.lower() for k in pinned_keys) and text.strip():
            put(pinned_blocks, f"### [늘 곁에 둘 글] {rel} (바뀌지 않음 — 방향 확인용)\n{_clip(text, 1200)}")

    if open_todos:
        # 진행 중 묶음은 '늘 곁에 둘 글' 맨 앞에 (새 노트가 없는 날에도 들어감)
        pinned_blocks.insert(0, "### ★[진행 중] 클로드 코드 대화 정리의 '다음 할 일' 가운데 아직 체크 안 된 것"
                                f" (최근 {cfg.get('todo_days', 7)}일)\n" + "\n".join(open_todos[:20]))
    out, used = [], 0
    for kind, blocks in (("new", new_blocks), ("changed", changed_blocks), ("pinned", pinned_blocks),
                         ("low", low_blocks)):
        for b in blocks:
            if used + len(b) > budget:
                break
            out.append(b)
            used += len(b)
            stats[kind] += 1

    head = ("(★ 표시는 가장 먼저 볼 것: '어제보다 나은 오늘' 폴더, 그리고 클로드 코드 대화 정리의 '막혔던 점'·'다음 할 일')\n"
            if any("★" in b for b in out) else "") + (f"(지난 상소 때 읽은 위키: {prev_day} 기준. 아래는 그 뒤로 새로 생기거나 바뀐 것만입니다)"
            if prev_day else "(위키를 처음 읽습니다. 최근에 고친 파일만 담았습니다)")
    body = "\n\n".join(out) if out else "(지난 상소 이후 위키에 새로 적힌 것 없음)"
    log.info("위키 %s: 새 파일 %d, 바뀐 파일 %d, 늘 곁에 둘 글 %d", root, stats["new"], stats["changed"], stats["pinned"])
    return f"{head}\n\n{body}", snap, stats
