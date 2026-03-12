#!/usr/bin/env python3
"""
poe.ninja 스킬 젬 수익 분석기
- 레벨 1 (no corrupt) 젬을 사서 레벨 20 (no corrupt) 젬으로 팔 때의 수익을 계산
"""

import json
import requests
import sys
from dataclasses import dataclass
from pathlib import Path


LEAGUE = "Mirage"
API_URL = f"https://poe.ninja/poe1/api/economy/stash/current/item/overview?league={LEAGUE}&type=SkillGem"

# 수익 계산 시 최소 리스팅 수 (데이터 신뢰성 확보)
MIN_LISTING_COUNT = 5

# 한글 번역 딕셔너리 로드
_BASE = Path(__file__).parent
def _load_translations() -> dict[str, str]:
    trans: dict[str, str] = {}
    for fname in ("poe1_gem_skill.json", "poe1_gem_support.json"):
        path = _BASE / fname
        if path.exists():
            trans.update(json.loads(path.read_text(encoding="utf-8")))
    return trans

TRANSLATIONS = _load_translations()


@dataclass
class GemEntry:
    name: str
    gem_level: int
    gem_quality: int
    chaos_value: float
    divine_value: float
    listing_count: int
    count: int
    corrupted: bool
    variant: str


def fetch_gem_data() -> list[GemEntry]:
    print(f"poe.ninja API에서 {LEAGUE} 리그 스킬 젬 데이터를 가져오는 중...")
    try:
        resp = requests.get(API_URL, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"API 요청 실패: {e}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    gems = []
    for item in data.get("lines", []):
        gems.append(GemEntry(
            name=item["name"],
            gem_level=item.get("gemLevel", 0),
            gem_quality=item.get("gemQuality", 0),
            chaos_value=item.get("chaosValue", 0),
            divine_value=item.get("divineValue", 0),
            listing_count=item.get("listingCount", 0),
            count=item.get("count", 0),
            corrupted=item.get("corrupted", False),
            variant=item.get("variant", ""),
        ))
    print(f"총 {len(gems)}개 항목 로드 완료")
    return gems


def analyze_profits(gems: list[GemEntry], sell_level: int = 20, min_listing: int = MIN_LISTING_COUNT):
    """lv1 0% 품질 no corrupt → lv{sell_level} 0% 품질 no corrupt 수익 분석"""
    no_corrupt = [g for g in gems if not g.corrupted]

    # lv1, 0% 품질 젬 (구매가 = 가장 저렴한 기준)
    lv1_map: dict[str, GemEntry] = {}
    for g in no_corrupt:
        if g.gem_level == 1 and g.gem_quality == 0:
            if g.name not in lv1_map or g.chaos_value < lv1_map[g.name].chaos_value:
                lv1_map[g.name] = g

    # lv{sell_level}, 0% 품질 젬 (판매가 = 가장 높은 가격 기준)
    sell_map: dict[str, GemEntry] = {}
    for g in no_corrupt:
        if g.gem_level == sell_level and g.gem_quality == 0:
            if g.name not in sell_map or g.chaos_value > sell_map[g.name].chaos_value:
                sell_map[g.name] = g

    results = []
    for name, lv1 in lv1_map.items():
        if name not in sell_map:
            continue
        sell = sell_map[name]

        if lv1.listing_count < min_listing or sell.listing_count < min_listing:
            continue

        profit = sell.chaos_value - lv1.chaos_value
        profit_divine = sell.divine_value - lv1.divine_value

        results.append({
            "name": name,
            "buy_chaos": lv1.chaos_value,
            "sell_chaos": sell.chaos_value,
            "profit_chaos": profit,
            "profit_divine": profit_divine,
            "buy_listing": lv1.listing_count,
            "sell_listing": sell.listing_count,
            "buy_divine": lv1.divine_value,
            "sell_divine": sell.divine_value,
        })

    results.sort(key=lambda x: x["profit_chaos"], reverse=True)
    return results


def print_results(results: list[dict], sell_level: int = 20, top_n: int = 30):
    print(f"\n{'='*100}")
    print(f"  스킬 젬 수익 분석 — {LEAGUE} 리그 | Lv1 0% no corrupt → Lv{sell_level} 0% no corrupt")
    print(f"  최소 리스팅 수: {MIN_LISTING_COUNT}개 이상만 표시")
    print(f"{'='*100}")

    if not results:
        print("  조건에 맞는 젬이 없습니다.")
        return

    header = f"{'#':>3}  {'젬 이름 (한글)':<30}  {'구매(c)':>8}  {'판매(c)':>8}  {'수익(c)':>8}  {'수익(div)':>9}  {'판매목록':>6}"
    print(header)
    print("-" * 100)

    for i, r in enumerate(results[:top_n], 1):
        kr_name = TRANSLATIONS.get(r["name"], r["name"])
        profit_sign = "+" if r["profit_chaos"] >= 0 else ""
        print(
            f"{i:>3}  "
            f"{kr_name:<30}  "
            f"{r['buy_chaos']:>8.1f}  "
            f"{r['sell_chaos']:>8.1f}  "
            f"{profit_sign}{r['profit_chaos']:>7.1f}  "
            f"{profit_sign}{r['profit_divine']:>8.2f}  "
            f"{r['sell_listing']:>6}"
        )

    print("-" * 100)
    profitable = [r for r in results if r["profit_chaos"] > 0]
    print(f"\n  전체 분석: {len(results)}개 젬 / 수익 플러스: {len(profitable)}개")
    if profitable:
        best = profitable[0]
        kr = TRANSLATIONS.get(best["name"], best["name"])
        print(f"  최고 수익: {kr} → +{best['profit_chaos']:.1f}c (+{best['profit_divine']:.2f} div)")


def main():
    gems = fetch_gem_data()

    results_lv20 = analyze_profits(gems, sell_level=20)
    print_results(results_lv20, sell_level=20, top_n=30)

    results_lv2 = analyze_profits(gems, sell_level=2)
    print_results(results_lv2, sell_level=2, top_n=30)


if __name__ == "__main__":
    main()
