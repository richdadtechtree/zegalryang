"""
to_wiki.py — 오늘의 상소를 마크다운 한 장으로 위키(my-wiki)에 올리는 곳

왜?
  richdadtechtree.com 의 '내 공간'(지메일 로그인)에서 상소를 보기 위해서입니다.
  웹앱은 이미 깃허브 my-wiki 를 읽고 쓰므로, 상소를 그 저장소에 마크다운으로 두면
  웹앱이 그대로 읽어 보여 줄 수 있습니다. 옵시디언에서도 같은 글이 보입니다.

어디에?   my-wiki/제갈량 상소/YYYY-MM-DD.md   (config.json 의 sangso_wiki_folder 로 바꿀 수 있음)
  • 깃허브 토큰(github.json)이 있으면 → 깃허브에 바로 올림 (웹앱이 곧바로 읽을 수 있음)
  • 없으면 → 노트북 위키 폴더에 저장 (옵시디언 깃 플러그인이 올려 줌)
  • 이 폴더는 wiki.py 가 읽지 않습니다. (상소가 자기 글을 다시 재료로 삼으면 같은 말을 되풀이하므로)

군령 체크는 여기서 하지 않습니다. 체크는 '어제보다 나은 오늘/군령.md' 한 곳에서만 → 이 글에는 체크박스 없이 적습니다.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from . import orders

log = logging.getLogger("sangso")

DEFAULT_FOLDER = "제갈량 상소"


def folder(cfg: dict) -> str:
    return str(cfg.get("sangso_wiki_folder") or DEFAULT_FOLDER).strip("/\\")


def _one_line(s) -> str:
    return " ".join(str(s or "").split())


def to_markdown(data: dict, day: date, orders_file: str) -> str:
    """상소 JSON → 사람이 읽기 좋은 마크다운. 웹앱은 이 글을 그대로 렌더링하면 됩니다."""
    court = data.get("court") or {}
    idiom = court.get("idiom") or {}
    tasks = data.get("today_task") or {}
    motto = data.get("motto") or {}
    rival = court.get("rival")
    rival = rival.get("text") if isinstance(rival, dict) else rival
    weekday = "월화수목금토일"[day.weekday()]
    orders_link = orders_file[:-3] if orders_file.endswith(".md") else orders_file

    out = [
        "---",
        f"date: {day.isoformat()}",
        "source: 제갈량 상소",
        f"title: \"{_one_line(data.get('title')).replace(chr(34), '')}\"",
        "tags: [상소, 제갈량]",
        "---",
        "",
        f"# {_one_line(data.get('title'))}",
        "",
        f"{day.year}년 {day.month}월 {day.day}일 {weekday}요일 · 승상 제갈량이 주공께 올리옵니다",
        "",
    ]
    if court.get("saying"):
        out += ["## 조회(朝會)", "", f"> **승상의 한마디** — {_one_line(court['saying'])}"]
        if court.get("event"):
            out += [">", f"> 근거: {_one_line(court['event'])}"]
        out.append("")
        for r in court.get("replies") or []:
            if isinstance(r, dict) and r.get("text"):
                out.append(f"- **{_one_line(r.get('who'))}**: {_one_line(r['text'])}")
        if rival:
            out.append(f"- **사마의(맞수)**: {_one_line(rival)}")
        if idiom.get("hanja"):
            out += ["", f"**오늘의 사자성어** {_one_line(idiom['hanja'])}({_one_line(idiom.get('reading'))}) — "
                        f"{_one_line(idiom.get('meaning'))} *{_one_line(idiom.get('source'))}*"]
        out.append("")

    out += ["## 오늘의 상소", ""]
    if data.get("salutation"):
        out += [_one_line(data["salutation"]), ""]
    for pt in data.get("points") or []:
        if not isinstance(pt, dict):
            continue
        out += [f"### [{_one_line(pt.get('tag'))}] {_one_line(pt.get('head'))}", "", _one_line(pt.get("body"))]
        if pt.get("basis"):
            out += ["", f"근거: {_one_line(pt['basis'])}"]
        out.append("")

    out += ["## 오늘의 군령", "", f"체크는 [[{orders_link}|군령 장부]]에서 합니다.", ""]
    if tasks.get("claude_code"):
        out.append(f"- **道具** · {_one_line(tasks['claude_code'])}")
    if tasks.get("life"):
        out.append(f"- **大業** · {_one_line(tasks['life'])}")
    out.append("")

    if motto.get("hanja"):
        out += ["## 오늘의 한 구절", "", f"**{_one_line(motto['hanja'])}** ({_one_line(motto.get('reading'))})", "",
                f"{_one_line(motto.get('meaning'))} — {_one_line(motto.get('source'))}", ""]
    return "\n".join(out)


def publish(base: Path, cfg: dict, data: dict, day: date) -> str | None:
    """오늘 상소를 위키에 올리고, 올린 곳을 돌려줍니다. 위키도 토큰도 없으면 None."""
    from . import wiki
    rel = f"{folder(cfg)}/{day.isoformat()}.md"
    text = to_markdown(data, day, cfg.get("orders_file", "어제보다 나은 오늘/군령.md"))
    gh = orders.load_github(base)
    if gh:
        store = orders.GitHubStore(gh["wiki_repo"], rel, gh["token"])
    else:
        root = wiki.find_dir(cfg.get("wiki_dir", ""))
        if not root:
            return None
        store = orders.LocalStore(root / rel)
    store.update(lambda old: None if old == text else text, f"상소: {day.isoformat()} {_one_line(data.get('title'))}")
    return store.where
