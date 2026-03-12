from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import requests

from .config import settings


# ── 번역 로딩 ──────────────────────────────────────────────
_TRAN_DIR = Path(__file__).parent / "tran"

def _load_translations() -> dict[str, str]:
    trans: dict[str, str] = {}
    if _TRAN_DIR.exists():
        for f in sorted(_TRAN_DIR.glob("*.json")):
            try:
                trans.update(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                pass
    return trans

_TRANSLATIONS = _load_translations()

def _tr(name: str) -> str:
    kr = _TRANSLATIONS.get(name)
    return f"{kr} ({name})" if kr else name


def send_slack_message(text: str) -> None:
    if not settings.slack_webhook_url:
        return
    try:
        resp = requests.post(
            settings.slack_webhook_url,
            data=json.dumps({"text": text}),
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException:
        print("[Slack] 메시지 전송 실패")


def format_price_change_message(
    title: str,
    changes: Iterable[dict],
) -> str:
    lines = [f"*{title}*"]
    for c in changes:
        name = _tr(c["name"])
        old = c["old_chaos"]
        new = c["new_chaos"]
        diff = new - old
        pct = c["percent"]
        arrow = "📈" if pct > 0 else "📉"
        lines.append(f"{arrow} *{name}*: {old:.1f}c → {new:.1f}c ({diff:+.1f}c, {pct:+.1f}%)")
    return "\n".join(lines)

