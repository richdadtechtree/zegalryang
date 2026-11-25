"""
orders.py — 군령(오늘 할 일 2가지)의 체크를 위키 파일에 적는 곳 + 그 일을 맡는 작은 '서기(書記)' 서버

왜 서버가 필요한가?
  브라우저 화면(상소)은 보안 때문에 내 컴퓨터의 파일을 직접 고칠 수 없습니다.
  그래서 상소를 열 때 이 컴퓨터 안에서만 들리는 작은 서버(127.0.0.1)를 함께 띄워 두고,
  화면에서 체크하면 → 서버에 알리고 → 서버가 위키의 군령 파일에 [x] 를 적습니다.
  옵시디언 깃 플러그인이 그 파일을 깃허브까지 올려 줍니다.

군령 파일 (위키_폴더/어제보다 나은 오늘/군령.md, config 의 orders_file 로 바꿀 수 있음)
    ## 2026-11-25
    - [ ] 道具 · 막혔던 오류를 메시지 전체와 함께 다시 물어보기
    - [x] 大業 · 완성한 화면 캡처 한 장을 한 사람에게 보내기
  옵시디언에서 직접 체크해도 됩니다. 다음 날 상소는 이 파일에서 '체크 안 된 군령'을 읽어 다시 올릴 수 있습니다.
  위키 폴더가 없으면 archive/군령.md 에 같은 모양으로 적습니다.

디버깅 힌트
  - 화면 아래에 "위키에 반영되지 않습니다"가 뜨면 → 서기가 꺼진 것. python sangso.py 로 다시 열면 함께 켜집니다.
  - 서기 기록: logs/clerk.log
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import date, datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("sangso")

PORT = 47717                       # 서기가 듣는 번호 (다른 프로그램과 겹치면 config 의 clerk_port 로 바꾸기)
TAGS = {"cc": "道具", "life": "大業"}  # 화면의 체크박스 이름 → 파일 속 꼬리표
FIELDS = {"cc": "claude_code", "life": "life"}  # 화면의 체크박스 이름 → 상소 데이터(today_task)의 항목 이름
HEADER = ("# 군령(軍令)\n\n"
          "> 승상 제갈량의 상소에서 내려온 '오늘 할 일'. 상소 화면에서 체크하면 여기에 자동으로 적힙니다.\n"
          "> 옵시디언에서 직접 체크해도 됩니다. 체크 안 된 군령은 다음 상소에서 다시 올라올 수 있습니다.\n")
_lock = threading.Lock()           # 체크가 동시에 두 번 들어와도 파일이 엉키지 않게


def orders_path(base: Path, cfg: dict) -> Path:
    from . import wiki
    root = wiki.find_dir(cfg.get("wiki_dir", ""))
    rel = cfg.get("orders_file", "어제보다 나은 오늘/군령.md")
    return (root / rel) if root else (base / "archive" / "군령.md")


# ──────────────────────────────────────────────────────────────
# 군령 파일 읽기·쓰기
# ──────────────────────────────────────────────────────────────
_LINE = re.compile(r"^- \[( |x|X)\] (道具|大業) · (.*)$")


def parse(text: str) -> dict:
    """{ '2026-11-25': {'cc': {'done': False, 'text': '…'}, 'life': {...}} }"""
    out, day = {}, None
    for line in text.splitlines():
        m = re.match(r"^## (\d{4}-\d{2}-\d{2})\s*$", line)
        if m:
            day = m.group(1)
            out.setdefault(day, {})
            continue
        m = _LINE.match(line.strip())
        if day and m:
            key = "cc" if m.group(2) == TAGS["cc"] else "life"
            out[day][key] = {"done": m.group(1) != " ", "text": m.group(3).strip()}
    return out


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def add_day(path: Path, day: str, tasks: dict) -> None:
    """오늘 상소의 군령 2개(today_task)를 '체크 안 됨'으로 적어 둡니다. 같은 날이 이미 있으면 글만 바꾸고 체크는 그대로."""
    tasks = {k: tasks.get(f) for k, f in FIELDS.items()}
    with _lock:
        text = _read(path) or HEADER
        lines = [f"- [ ] {TAGS[k]} · {' '.join(str(tasks.get(k) or '').split())}"
                 for k in ("cc", "life") if tasks.get(k)]
        if not lines:
            return
        old = parse(text).get(day, {})
        for i, k in enumerate(k for k in ("cc", "life") if tasks.get(k)):
            if old.get(k, {}).get("done"):
                lines[i] = lines[i].replace("- [ ]", "- [x]", 1)
        block = f"## {day}\n" + "\n".join(lines) + "\n"
        pat = re.compile(rf"^## {re.escape(day)}\s*\n(?:- \[.\].*\n?)*", re.M)
        if pat.search(text):
            text = pat.sub(block, text, count=1)
        else:
            # 최신 날짜가 위로 오게 머리말 바로 아래에 끼워 넣습니다
            m = re.search(r"^## \d{4}-\d{2}-\d{2}", text, re.M)
            text = (text[:m.start()] + block + "\n" + text[m.start():]) if m else text.rstrip("\n") + "\n\n" + block
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def set_done(path: Path, day: str, key: str, done: bool) -> bool:
    """체크 하나를 바꿉니다. 그 날짜·꼬리표 줄을 찾지 못하면 False."""
    with _lock:
        text = _read(path)
        sec = re.search(rf"^## {re.escape(day)}\s*\n((?:- \[.\].*\n?)*)", text, re.M)
        if not sec:
            return False
        body = sec.group(1)
        new_body, n = re.subn(rf"^- \[( |x|X)\] {TAGS[key]} · ", f"- [{'x' if done else ' '}] {TAGS[key]} · ",
                              body, count=1, flags=re.M)
        if not n:
            return False
        path.write_text(text[:sec.start(1)] + new_body + text[sec.end(1):], encoding="utf-8")
        return True


def pending_text(path: Path, today: date, days: int = 14) -> str:
    """제갈량에게 넘길 '아직 못 한 군령'과 '해낸 군령' 목록 (최근 days 일)."""
    data = parse(_read(path))
    since = (today - timedelta(days=days)).isoformat()
    todo, done = [], []
    for day in sorted(data, reverse=True):
        if not (since <= day < today.isoformat()):
            continue
        for k, v in data[day].items():
            (done if v["done"] else todo).append(f"- {day} [{TAGS[k]}] {v['text']}")
    return ("[아직 못 한 군령 — 다시 올려도 됨]\n" + ("\n".join(todo) or "(없음)") +
            "\n\n[해낸 군령 — 되풀이 금지, 다음 단계로]\n" + ("\n".join(done) or "(없음)"))


# ──────────────────────────────────────────────────────────────
# 서기(書記) 서버 — 상소 화면을 보여 주고, 체크를 받아 파일에 적음
# ──────────────────────────────────────────────────────────────
def make_handler(base: Path, cfg: dict):
    path = orders_path(base, cfg)

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(base), **kw)   # output/·assets/ 를 그대로 보여 줌

        def log_message(self, fmt, *args):  # 접속 기록은 남기지 않음 (조용히)
            pass

        def _json(self, code: int, obj) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/api/ping":
                return self._json(200, {"ok": True, "file": str(path)})
            if u.path == "/api/checks":
                day = (parse_qs(u.query).get("date") or [""])[0]
                return self._json(200, {k: v["done"] for k, v in parse(_read(path)).get(day, {}).items()})
            # 개인 기록이 든 폴더는 보여 주지 않음 (output·assets 만)
            if not (u.path.startswith("/output/") or u.path.startswith("/assets/")):
                return self.send_error(404)
            return super().do_GET()

        def do_POST(self):
            # 다른 웹사이트가 몰래 체크를 바꾸지 못하게: 우리 화면만 붙이는 머리글이 있어야 받음
            # (이 머리글이 붙은 요청은 브라우저가 다른 사이트에서 보내기 전에 막습니다)
            if self.headers.get("X-Sangso") != "1" or urlparse(self.path).path != "/api/check":
                return self.send_error(403)
            try:
                n = min(int(self.headers.get("Content-Length") or 0), 2000)
                req = json.loads(self.rfile.read(n) or b"{}")
                day, key, done = str(req.get("date")), str(req.get("key")), bool(req.get("done"))
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) or key not in TAGS:
                    raise ValueError("잘못된 값")
            except (ValueError, json.JSONDecodeError) as e:
                return self._json(400, {"ok": False, "error": str(e)})
            if not set_done(path, day, key, done):
                # 그날 줄이 아직 없으면(예전 상소) 상소 기록에서 군령을 꺼내 먼저 적습니다
                src = base / "archive" / f"{day}.json"
                if src.exists():
                    add_day(path, day, json.loads(src.read_text(encoding="utf-8")).get("today_task") or {})
                    set_done(path, day, key, done)
            log.info("군령 체크: %s %s → %s (%s)", day, TAGS[key], "완료" if done else "취소", path)
            return self._json(200, {"ok": True})

    return Handler


def is_running(port: int) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1) as r:
            return r.status == 200
    except OSError:
        return False


def serve(base: Path, cfg: dict) -> None:
    """서기를 켭니다. 다음 날 새벽 4시가 되면 스스로 꺼집니다(내일 상소가 새로 켬)."""
    port = int(cfg.get("clerk_port", PORT))
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(base, cfg))   # 이 컴퓨터 안에서만 들림
    stop = datetime.now().replace(hour=4, minute=0, second=0, microsecond=0) + timedelta(days=1)
    threading.Timer((stop - datetime.now()).total_seconds(), srv.shutdown).start()
    log.info("서기 켜짐: http://127.0.0.1:%d  (군령 파일: %s, %s에 꺼짐)", port, orders_path(base, cfg), stop)
    srv.serve_forever()


def start_background(base: Path, cfg: dict) -> str | None:
    """서기가 안 떠 있으면 따로(창 없이) 띄웁니다. 성공하면 주소 앞부분을 돌려줌, 실패하면 None."""
    import subprocess
    import sys
    import time
    port = int(cfg.get("clerk_port", PORT))
    url = f"http://127.0.0.1:{port}"
    if is_running(port):
        return url
    exe = sys.executable
    if sys.platform == "win32" and Path(exe).name.lower() == "python.exe":
        w = Path(exe).with_name("pythonw.exe")      # 검은 창 없이 실행
        exe = str(w) if w.exists() else exe
    kw = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "stdin": subprocess.DEVNULL, "cwd": str(base)}
    if sys.platform == "win32":
        kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000  # 따로 떨어져·새 그룹·창 없음
    else:
        kw["start_new_session"] = True                               # 터미널을 닫아도 계속
    try:
        subprocess.Popen([exe, str(base / "sangso.py"), "--clerk"], **kw)
    except OSError as e:
        log.warning("서기를 띄우지 못함: %s", e)
        return None
    for _ in range(20):            # 최대 4초 기다림
        time.sleep(0.2)
        if is_running(port):
            return url
    log.warning("서기가 응답하지 않음 (번호 %d 이 이미 쓰이는지 확인)", port)
    return None
