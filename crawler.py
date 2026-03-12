from __future__ import annotations

"""
1시간마다 실행되는 크롤러 스크립트
- gem_profit.py 의 젬 데이터
- poe.ninja 화폐 / 스카라브 / 디비네이션 카드
를 수집해서 DB 에 저장하고, 가격 변동이 임계값 이상이면 슬랙으로 알림.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

import requests

from .config import settings
from .gem_profit import fetch_gem_data, analyze_profits
from .models import (
    SessionLocal,
    Snapshot,
    GemPrice,
    MarketPrice,
    Threshold,
    init_db,
)
from .slack_notifier import send_slack_message, format_price_change_message

Category = Literal["currency", "scarab", "divination-card"]


@dataclass
class MarketEntry:
    name: str
    chaos_value: float
    divine_value: float
    icon: Optional[str] = None
    details_id: Optional[str] = None
    item_level: Optional[int] = None


def _fetch_currency_overview(item_type: str = "Currency") -> list[MarketEntry]:
    """Currency / Fragment 등 currency overview API 공통 fetcher"""
    url = f"https://poe.ninja/api/data/currencyoverview?league={settings.league}&type={item_type}"
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    result: list[MarketEntry] = []
    lines = data.get("lines", [])
    details = {d["id"]: d for d in data.get("currencyDetails", [])}
    divine_rate = data.get("chaosEquivalentDivine", 150) or 150
    for line in lines:
        currency_type_id = line.get("currencyTypeName") or line.get("currencyTypeId")
        name = line.get("currencyTypeName") or ""
        chaos_value = line.get("chaosEquivalent") or 0.0
        divine_value = chaos_value / divine_rate
        detail = None
        if isinstance(currency_type_id, int):
            detail = details.get(currency_type_id)
        result.append(
            MarketEntry(
                name=name,
                chaos_value=chaos_value,
                divine_value=divine_value,
                icon=(detail or {}).get("icon"),
                details_id=str((detail or {}).get("id")) if (detail or {}).get("id") else None,
            )
        )
    return result


def _fetch_currency() -> list[MarketEntry]:
    return _fetch_currency_overview("Currency")


def _fetch_fragment() -> list[MarketEntry]:
    return _fetch_currency_overview("Fragment")


def _fetch_item_overview(item_type: str) -> list[MarketEntry]:
    url = f"https://poe.ninja/api/data/itemoverview?league={settings.league}&type={item_type}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    # 이름 기준 최고가 항목만 유지 (동명 중복 제거)
    best: dict[str, dict] = {}
    for line in data.get("lines", []):
        name = line.get("name") or ""
        if not name:
            continue
        chaos_value = line.get("chaosValue") or 0.0
        if name not in best or chaos_value > (best[name].get("chaosValue") or 0.0):
            best[name] = line

    result: list[MarketEntry] = []
    for line in best.values():
        chaos_value = line.get("chaosValue") or 0.0
        divine_value = line.get("divineValue") or 0.0
        result.append(
            MarketEntry(
                name=line.get("name", ""),
                chaos_value=chaos_value,
                divine_value=divine_value,
                icon=line.get("icon"),
                details_id=str(line.get("id")) if line.get("id") else None,
            )
        )
    return result


def _fetch_base_types_level80() -> list[MarketEntry]:
    """ilvl 83+ 모드 없는 베이스 아이템 (variant 없음 = 무영향, 이름 중복 시 최고가 유지)"""
    url = f"https://poe.ninja/api/data/itemoverview?league={settings.league}&type=BaseType"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    # 이름 기준 최고가 항목만 유지 (lv83+, no variant)
    best: dict[str, dict] = {}
    for line in data.get("lines", []):
        if line.get("levelRequired", 0) < 83:
            continue
        if line.get("variant"):
            continue
        name = line.get("name") or ""
        if not name:
            continue
        chaos_value = line.get("chaosValue") or 0.0
        if name not in best or chaos_value > (best[name].get("chaosValue") or 0.0):
            best[name] = line

    result: list[MarketEntry] = []
    for line in best.values():
        chaos_value = line.get("chaosValue") or 0.0
        divine_value = line.get("divineValue") or 0.0
        result.append(
            MarketEntry(
                name=line.get("name") or "",
                chaos_value=chaos_value,
                divine_value=divine_value,
                icon=line.get("icon"),
                details_id=str(line.get("id")) if line.get("id") else None,
                item_level=line.get("levelRequired"),
            )
        )
    return result


def _get_thresholds(db, category: str, name: Optional[str]) -> tuple[float, float]:
    """(percent_threshold, chaos_threshold) 반환. 두 조건 모두 충족해야 알림."""
    def _find(cat: str, nm) -> object:
        return (
            db.query(Threshold)
            .filter(Threshold.category == cat, Threshold.name.is_(nm))
            .one_or_none()
        )

    # 우선순위: 아이템명 지정 → 카테고리 전체 → global
    t = (_find(category, name) if name else None) or _find(category, None) or _find("global", None)

    pct   = t.threshold_percent if t else settings.default_threshold_percent
    chaos = (t.chaos_threshold if (t and t.chaos_threshold is not None)
             else settings.default_threshold_chaos)
    return pct, chaos


def _compare_with_previous_market(
    db,
    category: Category,
    snapshot: Snapshot,
    entries: list[MarketEntry],
) -> list[dict]:
    """
    바로 이전 스냅샷과 비교해서 가격 변동률 계산
    """
    # 이전 스냅샷 찾기
    prev_snapshot = (
        db.query(Snapshot)
        .filter(Snapshot.id < snapshot.id)
        .order_by(Snapshot.id.desc())
        .first()
    )
    if not prev_snapshot:
        return []

    prev_items = (
        db.query(MarketPrice)
        .filter(MarketPrice.snapshot_id == prev_snapshot.id, MarketPrice.category == category)
        .all()
    )
    prev_map = {p.name: p for p in prev_items}

    changes: list[dict] = []
    for e in entries:
        prev = prev_map.get(e.name)
        if not prev or prev.chaos_value <= 0:
            continue
        old = prev.chaos_value
        new = e.chaos_value
        if new <= 0:
            continue
        diff_pct = (new - old) / old * 100.0
        diff_chaos = abs(new - old)
        pct_thr, chaos_thr = _get_thresholds(db, category, e.name)
        if abs(diff_pct) >= pct_thr or diff_chaos >= chaos_thr:
            changes.append(
                {
                    "category": category,
                    "name": e.name,
                    "old_chaos": old,
                    "new_chaos": new,
                    "percent": diff_pct,
                    "threshold": pct_thr,
                    "chaos_threshold": chaos_thr,
                }
            )
    return changes


def _compare_with_previous_gems(
    db,
    snapshot: Snapshot,
    gems_lv20: list[dict],
) -> list[dict]:
    prev_snapshot = (
        db.query(Snapshot)
        .filter(Snapshot.id < snapshot.id)
        .order_by(Snapshot.id.desc())
        .first()
    )
    if not prev_snapshot:
        return []

    prev_items = (
        db.query(GemPrice)
        .filter(GemPrice.snapshot_id == prev_snapshot.id, GemPrice.sell_level == 20)
        .all()
    )
    prev_map = {p.name: p for p in prev_items}

    changes: list[dict] = []
    for g in gems_lv20:
        name = g["name"]
        prev = prev_map.get(name)
        if not prev or prev.sell_chaos <= 0:
            continue
        old = prev.sell_chaos
        new = g["sell_chaos"]
        if new <= 0:
            continue
        diff_pct = (new - old) / old * 100.0
        diff_chaos = abs(new - old)
        pct_thr, chaos_thr = _get_thresholds(db, "gem", name)
        if abs(diff_pct) >= pct_thr or diff_chaos >= chaos_thr:
            changes.append(
                {
                    "category": "gem",
                    "name": name,
                    "old_chaos": old,
                    "new_chaos": new,
                    "percent": diff_pct,
                    "threshold": pct_thr,
                    "chaos_threshold": chaos_thr,
                }
            )
    return changes


def run_crawl() -> None:
    """
    한 번 크롤링 실행
    """
    init_db()
    db = SessionLocal()
    try:
        snapshot = Snapshot(created_at=datetime.utcnow())
        db.add(snapshot)
        db.flush()  # snapshot.id 확보

        # 1) 젬 데이터 수집
        gems = fetch_gem_data()
        results_lv20 = analyze_profits(gems, sell_level=20)
        results_lv2 = analyze_profits(gems, sell_level=2)

        for r in results_lv20:
            db.add(
                GemPrice(
                    snapshot_id=snapshot.id,
                    name=r["name"],
                    sell_level=20,
                    buy_chaos=r["buy_chaos"],
                    sell_chaos=r["sell_chaos"],
                    profit_chaos=r["profit_chaos"],
                    profit_divine=r["profit_divine"],
                    buy_divine=r["buy_divine"],
                    sell_divine=r["sell_divine"],
                    buy_listing=r["buy_listing"],
                    sell_listing=r["sell_listing"],
                )
            )

        for r in results_lv2:
            db.add(
                GemPrice(
                    snapshot_id=snapshot.id,
                    name=r["name"],
                    sell_level=2,
                    buy_chaos=r["buy_chaos"],
                    sell_chaos=r["sell_chaos"],
                    profit_chaos=r["profit_chaos"],
                    profit_divine=r["profit_divine"],
                    buy_divine=r["buy_divine"],
                    sell_divine=r["sell_divine"],
                    buy_listing=r["buy_listing"],
                    sell_listing=r["sell_listing"],
                )
            )

        # 2) 마켓 데이터 수집
        currency = _fetch_currency()
        scarabs = _fetch_item_overview("Scarab")
        div_cards = _fetch_item_overview("DivinationCard")
        fragments = _fetch_fragment()
        wombgifts = _fetch_item_overview("Wombgift")
        runegrafts = _fetch_item_overview("Runegraft")
        base_types = _fetch_base_types_level80()

        market_batch = [
            ("currency",        currency),
            ("scarab",          scarabs),
            ("divination-card", div_cards),
            ("fragment",        fragments),
            ("wombgift",        wombgifts),
            ("runegraft",       runegrafts),
            ("base-type",       base_types),
        ]
        for cat, entries in market_batch:
            for e in entries:
                db.add(
                    MarketPrice(
                        snapshot_id=snapshot.id,
                        category=cat,
                        name=e.name,
                        chaos_value=e.chaos_value,
                        divine_value=e.divine_value,
                        icon=e.icon,
                        details_id=e.details_id,
                        item_level=e.item_level,
                    )
                )

        db.commit()

        # 3) 이전 스냅샷과 비교 후 알림
        changes_gem = _compare_with_previous_gems(db, snapshot, results_lv20)
        alert_market = [
            ("젬 가격 변동",              changes_gem, None),
        ]
        alert_labels = {
            "currency":        "화폐 가격 변동",
            "scarab":          "스카라브 가격 변동",
            "divination-card": "디비네이션 카드 가격 변동",
            "fragment":        "프래그먼트 가격 변동",
            "wombgift":        "웜기프트 가격 변동",
            "runegraft":       "룬그래프트 가격 변동",
        }
        for cat, entries in market_batch:
            if cat not in alert_labels:  # base-type 알림 제외
                continue
            changes = _compare_with_previous_market(db, cat, snapshot, entries)
            alert_market.append((alert_labels[cat], None, changes))

        for label, gem_changes, market_changes in alert_market:
            items = gem_changes if gem_changes is not None else market_changes
            if items:
                send_slack_message(
                    format_price_change_message(
                        f"{label} (임계값 이상)",
                        sorted(items, key=lambda c: abs(c["percent"]), reverse=True)[:10],
                    )
                )

    finally:
        db.close()


if __name__ == "__main__":
    run_crawl()

