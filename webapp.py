from __future__ import annotations

import json
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Optional

from flask import Flask, render_template_string, request, redirect, url_for, session
from werkzeug.security import check_password_hash

from .models import (
    SessionLocal,
    Snapshot,
    GemPrice,
    MarketPrice,
    Threshold,
    User,
    init_db,
    init_admin,
)
from .config import settings


# ── 번역 로딩 ──────────────────────────────────────────────
_TRAN_DIR = Path(__file__).parent / "tran"

def _load_translations() -> dict:
    trans: dict[str, str] = {}
    if _TRAN_DIR.exists():
        for f in sorted(_TRAN_DIR.glob("*.json")):
            try:
                trans.update(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                pass
    return trans

TRANSLATIONS = _load_translations()

def tr(name: str) -> str:
    return TRANSLATIONS.get(name, name)


# ── Flask 앱 ────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = "poe-economy-s3cr3t-2026"
app.jinja_env.globals.update(enumerate=enumerate, tr=tr)

init_db()
init_admin()


# ── 인증 ────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "admin" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def _get_snapshots(db, limit: int = 3) -> list[Snapshot]:
    return (
        db.query(Snapshot)
        .order_by(Snapshot.created_at.desc())
        .limit(limit)
        .all()
    )


# ── 라우트 ──────────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.username == username).one_or_none()
            if user and check_password_hash(user.password_hash, password):
                session["admin"] = username
                return redirect(url_for("admin_thresholds"))
            error = "아이디 또는 비밀번호가 틀립니다."
        finally:
            db.close()
    return render_template_string(LOGIN_TEMPLATE, error=error)


@app.route("/logout")
def logout():
    session.pop("admin", None)
    return redirect(url_for("index"))


@app.route("/")
def index():
    db = SessionLocal()
    try:
        snapshots = _get_snapshots(db, limit=3)
        if not snapshots:
            return "아직 수집된 데이터가 없습니다. 크론으로 crawler.py를 먼저 실행해주세요."

        snap_ids = [s.id for s in snapshots]

        # 젬: 최신 상위 20개
        gems_latest = (
            db.query(GemPrice)
            .filter(GemPrice.snapshot_id == snap_ids[0], GemPrice.sell_level == 20)
            .order_by(GemPrice.profit_chaos.desc())
            .limit(20)
            .all()
        )

        # 이전 스냅샷 젬 맵
        def _gem_map(sid):
            return {g.name: g for g in
                    db.query(GemPrice).filter(GemPrice.snapshot_id == sid, GemPrice.sell_level == 20).all()}

        prev_gem_maps = [_gem_map(sid) for sid in snap_ids[1:]]

        gems = []
        for g in gems_latest:
            prevs = [pm.get(g.name) for pm in prev_gem_maps]
            gems.append({"obj": g, "prev": prevs})

        # 마켓 데이터
        def top_market(cat: str, limit: int = 30):
            return (
                db.query(MarketPrice)
                .filter(MarketPrice.snapshot_id == snap_ids[0], MarketPrice.category == cat)
                .order_by(MarketPrice.chaos_value.desc())
                .limit(limit)
                .all()
            )

        def _market_map(sid, cat):
            return {m.name: m for m in
                    db.query(MarketPrice).filter(MarketPrice.snapshot_id == sid, MarketPrice.category == cat).all()}

        def enrich_market(cat: str, limit: int = 30):
            items = top_market(cat, limit)
            prev_maps = [_market_map(sid, cat) for sid in snap_ids[1:]]
            return [{"obj": m, "prev": [pm.get(m.name) for pm in prev_maps]} for m in items]

        return render_template_string(
            DASHBOARD_TEMPLATE,
            league=settings.league,
            snapshots=snapshots,
            gems=gems,
            currency=enrich_market("currency"),
            scarabs=enrich_market("scarab"),
            div_cards=enrich_market("divination-card"),
            fragments=enrich_market("fragment"),
            wombgifts=enrich_market("wombgift"),
            runegrafts=enrich_market("runegraft"),
        )
    finally:
        db.close()


@app.route("/base-types")
def base_types_page():
    db = SessionLocal()
    try:
        snapshots = _get_snapshots(db, limit=3)
        if not snapshots:
            return redirect(url_for("index"))

        snap_ids = [s.id for s in snapshots]

        items_latest = (
            db.query(MarketPrice)
            .filter(MarketPrice.snapshot_id == snap_ids[0], MarketPrice.category == "base-type")
            .order_by(MarketPrice.chaos_value.desc())
            .all()
        )

        def _prev_map(sid):
            return {m.name: m for m in
                    db.query(MarketPrice).filter(MarketPrice.snapshot_id == sid, MarketPrice.category == "base-type").all()}

        prev_maps = [_prev_map(sid) for sid in snap_ids[1:]]

        rows = []
        for m in items_latest:
            prevs = [pm.get(m.name) for pm in prev_maps]
            p1 = prevs[0] if prevs else None
            pct1 = ((m.chaos_value - p1.chaos_value) / p1.chaos_value * 100) if (p1 and p1.chaos_value > 0) else None
            rows.append({
                "name": m.name,
                "kr_name": tr(m.name),
                "level": m.item_level or 0,
                "chaos": m.chaos_value,
                "divine": m.divine_value,
                "prev1_chaos": p1.chaos_value if p1 else None,
                "pct1": pct1,
            })

        return render_template_string(
            BASE_TYPES_TEMPLATE,
            league=settings.league,
            snapshots=snapshots,
            rows=rows,
        )
    finally:
        db.close()


@app.route("/admin/thresholds", methods=["GET", "POST"])
@login_required
def admin_thresholds():
    db = SessionLocal()
    try:
        if request.method == "POST":
            action = request.form.get("action")
            if action == "delete":
                tid = int(request.form.get("id", 0))
                t = db.query(Threshold).filter(Threshold.id == tid).one_or_none()
                if t:
                    db.delete(t)
                    db.commit()
            else:
                category = request.form.get("category") or "global"
                name = request.form.get("name") or None
                threshold = float(request.form.get("threshold_percent") or settings.default_threshold_percent)
                chaos_raw = request.form.get("chaos_threshold", "").strip()
                chaos_threshold = float(chaos_raw) if chaos_raw else None
                existing = (
                    db.query(Threshold)
                    .filter(Threshold.category == category, Threshold.name.is_(name))
                    .one_or_none()
                )
                if existing:
                    existing.threshold_percent = threshold
                    existing.chaos_threshold = chaos_threshold
                else:
                    db.add(Threshold(category=category, name=name,
                                     threshold_percent=threshold,
                                     chaos_threshold=chaos_threshold))
                db.commit()
            return redirect(url_for("admin_thresholds"))

        thresholds = db.query(Threshold).order_by(Threshold.category.asc(), Threshold.name.asc()).all()
        return render_template_string(
            ADMIN_TEMPLATE,
            thresholds=thresholds,
            default_threshold=settings.default_threshold_percent,
            default_chaos=settings.default_threshold_chaos,
            admin_user=session.get("admin"),
        )
    finally:
        db.close()


# ── 헬퍼 필터 ──────────────────────────────────────────────
def _pct_class(pct: float) -> str:
    if pct >= 5:
        return "text-success fw-bold"
    if pct <= -5:
        return "text-danger fw-bold"
    return "text-warning"

def _arrow(pct: float) -> str:
    if pct > 0:
        return f"▲ +{pct:.1f}%"
    if pct < 0:
        return f"▼ {pct:.1f}%"
    return "→ 0%"


# ── 템플릿 ──────────────────────────────────────────────────
DASHBOARD_TEMPLATE = """
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PoE 경제 대시보드 - {{ league }}</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    .snap-badge { font-size:0.72rem; padding:2px 7px; }
    .prev-val { font-size:0.82rem; color:#aaa; }
    th { white-space:nowrap; font-size:0.85rem; }
    td { font-size:0.83rem; vertical-align:middle; }
    .trend-up   { color:#4fc96b; font-size:0.78rem; }
    .trend-down { color:#e05c5c; font-size:0.78rem; }
    .trend-flat { color:#888;    font-size:0.78rem; }
  </style>
</head>
<body class="bg-dark text-light">
<nav class="navbar navbar-dark bg-secondary px-3 mb-3">
  <span class="navbar-brand">PoE Economy — {{ league }}</span>
  <a href="{{ url_for('login') }}" class="btn btn-outline-light btn-sm">관리자 로그인</a>
</nav>

{# 스냅샷 타임라인 #}
<div class="container-fluid mb-3">
  <div class="d-flex gap-2 align-items-center flex-wrap">
    <span class="text-muted me-1">크롤링 시각:</span>
    {% for i, s in enumerate(snapshots) %}
      <span class="badge bg-{{ 'primary' if i==0 else 'secondary' }} snap-badge">
        {{ '최신' if i==0 else (i|string)+'h전' }} — {{ s.created_at.strftime('%m/%d %H:%M') }} UTC
      </span>
    {% endfor %}
  </div>
</div>

<div class="container-fluid">

  {# ── 젬 수익 ── #}
  <div class="card bg-secondary text-light mb-4">
    <div class="card-header fw-bold">스킬 젬 수익 (Lv1→Lv20, 상위 20개)</div>
    <div class="card-body p-0">
      <div class="table-responsive">
      <table class="table table-dark table-striped table-hover mb-0">
        <thead>
          <tr>
            <th>#</th>
            <th>젬 이름</th>
            <th class="text-end">구매(c)</th>
            <th class="text-end">판매(c)</th>
            <th class="text-end">수익(c)</th>
            {% if snapshots|length > 1 %}<th class="text-end">1h전</th>{% endif %}
            {% if snapshots|length > 2 %}<th class="text-end">2h전</th>{% endif %}
            <th class="text-end">목록</th>
          </tr>
        </thead>
        <tbody>
        {% for i, row in enumerate(gems, 1) %}
          {% set g = row.obj %}
          <tr>
            <td>{{ i }}</td>
            <td>{{ tr(g.name) }}</td>
            <td class="text-end">{{ "%.1f"|format(g.buy_chaos) }}</td>
            <td class="text-end">{{ "%.1f"|format(g.sell_chaos) }}</td>
            <td class="text-end {{ 'text-success' if g.profit_chaos >= 0 else 'text-danger' }}">
              {{ "%+.1f"|format(g.profit_chaos) }}
            </td>
            {% if snapshots|length > 1 %}
              {% set p1 = row.prev[0] %}
              <td class="text-end">
                {% if p1 %}
                  <span class="prev-val">{{ "%+.1f"|format(p1.profit_chaos) }}</span>
                  {% set d1 = g.profit_chaos - p1.profit_chaos %}
                  <br><span class="{{ 'trend-up' if d1>0 else ('trend-down' if d1<0 else 'trend-flat') }}">
                    {{ ('▲' if d1>0 else ('▼' if d1<0 else '→')) }} {{ "%+.1f"|format(d1) }}c
                  </span>
                {% else %}<span class="text-muted">-</span>{% endif %}
              </td>
            {% endif %}
            {% if snapshots|length > 2 %}
              {% set p2 = row.prev[1] %}
              <td class="text-end">
                {% if p2 %}
                  <span class="prev-val">{{ "%+.1f"|format(p2.profit_chaos) }}</span>
                  {% set d2 = g.profit_chaos - p2.profit_chaos %}
                  <br><span class="{{ 'trend-up' if d2>0 else ('trend-down' if d2<0 else 'trend-flat') }}">
                    {{ ('▲' if d2>0 else ('▼' if d2<0 else '→')) }} {{ "%+.1f"|format(d2) }}c
                  </span>
                {% else %}<span class="text-muted">-</span>{% endif %}
              </td>
            {% endif %}
            <td class="text-end">{{ g.sell_listing }}</td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
      </div>
    </div>
  </div>

  {# ── 마켓 테이블 매크로 ── #}
  {% macro market_table(title, items) %}
  <div class="card bg-secondary text-light mb-4">
    <div class="card-header fw-bold">{{ title }}</div>
    <div class="card-body p-0">
      <div class="table-responsive">
      <table class="table table-dark table-striped table-hover mb-0">
        <thead>
          <tr>
            <th>#</th>
            <th>이름</th>
            <th class="text-end">현재(c)</th>
            {% if snapshots|length > 1 %}<th class="text-end">1h전(c)</th>{% endif %}
            {% if snapshots|length > 2 %}<th class="text-end">2h전(c)</th>{% endif %}
            <th class="text-end">변동%</th>
          </tr>
        </thead>
        <tbody>
        {% for i, row in enumerate(items, 1) %}
          {% set m = row.obj %}
          {% set p1 = row.prev[0] if row.prev|length > 0 else none %}
          {% set p2 = row.prev[1] if row.prev|length > 1 else none %}
          {% set pct1 = ((m.chaos_value - p1.chaos_value) / p1.chaos_value * 100) if (p1 and p1.chaos_value > 0) else none %}
          <tr>
            <td>{{ i }}</td>
            <td>{{ tr(m.name) }}</td>
            <td class="text-end">{{ "%.2f"|format(m.chaos_value) }}</td>
            {% if snapshots|length > 1 %}
              <td class="text-end prev-val">{{ "%.2f"|format(p1.chaos_value) if p1 else '-' }}</td>
            {% endif %}
            {% if snapshots|length > 2 %}
              <td class="text-end prev-val">{{ "%.2f"|format(p2.chaos_value) if p2 else '-' }}</td>
            {% endif %}
            <td class="text-end">
              {% if pct1 is not none %}
                <span class="{{ 'trend-up' if pct1>0 else ('trend-down' if pct1<0 else 'trend-flat') }}">
                  {{ ('▲' if pct1>0 else ('▼' if pct1<0 else '→')) }} {{ "%+.1f"|format(pct1) }}%
                </span>
              {% else %}<span class="text-muted">-</span>{% endif %}
            </td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
      </div>
    </div>
  </div>
  {% endmacro %}

  <div class="row">
    <div class="col-xl-4">{{ market_table("화폐 (상위 30)", currency) }}</div>
    <div class="col-xl-4">{{ market_table("스카라브 (상위 30)", scarabs) }}</div>
    <div class="col-xl-4">{{ market_table("디비네이션 카드 (상위 30)", div_cards) }}</div>
  </div>

  <div class="row">
    <div class="col-xl-4">{{ market_table("프래그먼트 (상위 30)", fragments) }}</div>
    <div class="col-xl-4">{{ market_table("웜기프트 (전체)", wombgifts) }}</div>
    <div class="col-xl-4">{{ market_table("룬그래프트 (전체)", runegrafts) }}</div>
  </div>

  <div class="row mt-2 mb-4">
    <div class="col-12">
      <a href="{{ url_for('base_types_page') }}" class="btn btn-outline-info w-100">
        🔍 베이스 타입 Lv83+ 전체 보기 / 검색
      </a>
    </div>
  </div>

</div>
</body>
</html>
"""


BASE_TYPES_TEMPLATE = """
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>베이스 타입 Lv83+ — {{ league }}</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    th { white-space:nowrap; font-size:0.84rem; cursor:pointer; user-select:none; }
    td { font-size:0.82rem; vertical-align:middle; }
    .trend-up   { color:#4fc96b; font-size:0.78rem; }
    .trend-down { color:#e05c5c; font-size:0.78rem; }
    .hidden { display:none !important; }
  </style>
</head>
<body class="bg-dark text-light">
<nav class="navbar navbar-dark bg-secondary px-3 mb-3">
  <a class="navbar-brand" href="{{ url_for('index') }}">← PoE Economy ({{ league }})</a>
  <span class="text-muted small">베이스 타입 Lv83+ · 모드 없음</span>
</nav>

<div class="container-fluid">
  {# 스냅샷 타임라인 #}
  <div class="d-flex gap-2 align-items-center flex-wrap mb-3">
    <span class="text-muted me-1">크롤링 시각:</span>
    {% for i, s in enumerate(snapshots) %}
      <span class="badge bg-{{ 'primary' if i==0 else 'secondary' }}" style="font-size:0.72rem">
        {{ '최신' if i==0 else (i|string)+'h전' }} — {{ s.created_at.strftime('%m/%d %H:%M') }} UTC
      </span>
    {% endfor %}
  </div>

  {# 검색 필터 #}
  <div class="card bg-secondary mb-3">
    <div class="card-body py-2">
      <div class="row g-2 align-items-end">
        <div class="col-md-5">
          <label class="form-label mb-1 small">이름 검색 (한글/영문)</label>
          <input type="text" id="nameSearch" class="form-control form-control-sm bg-dark text-light border-secondary"
                 placeholder="예: 부츠, Boots, Amulet...">
        </div>
        <div class="col-md-2">
          <label class="form-label mb-1 small">최소 가격 (c)</label>
          <input type="number" id="minPrice" class="form-control form-control-sm bg-dark text-light border-secondary"
                 placeholder="0" min="0" step="1">
        </div>
        <div class="col-md-2">
          <label class="form-label mb-1 small">최소 레벨</label>
          <input type="number" id="minLevel" class="form-control form-control-sm bg-dark text-light border-secondary"
                 value="83" min="83" max="100" step="1">
        </div>
        <div class="col-md-2">
          <label class="form-label mb-1 small">최대 레벨</label>
          <input type="number" id="maxLevel" class="form-control form-control-sm bg-dark text-light border-secondary"
                 placeholder="100" min="83" max="100" step="1">
        </div>
        <div class="col-md-1">
          <button class="btn btn-sm btn-outline-secondary w-100" onclick="resetFilters()">초기화</button>
        </div>
      </div>
      <div class="mt-2 small text-muted">표시: <span id="visibleCount">{{ rows|length }}</span> / 전체 {{ rows|length }}개</div>
    </div>
  </div>

  {# 테이블 #}
  <div class="card bg-secondary">
    <div class="card-body p-0">
      <div class="table-responsive" style="max-height:75vh; overflow-y:auto;">
      <table class="table table-dark table-striped table-hover mb-0" id="baseTable">
        <thead class="sticky-top" style="top:0; z-index:1;">
          <tr>
            <th onclick="sortTable(0)">#</th>
            <th onclick="sortTable(1)">이름 ↕</th>
            <th onclick="sortTable(2)">레벨 ↕</th>
            <th onclick="sortTable(3)" class="text-end">현재(c) ↕</th>
            {% if snapshots|length > 1 %}<th class="text-end">1h전(c)</th>{% endif %}
            <th onclick="sortTable({% if snapshots|length > 1 %}5{% else %}4{% endif %})" class="text-end">변동% ↕</th>
          </tr>
        </thead>
        <tbody id="tableBody">
        {% for i, row in enumerate(rows, 1) %}
          <tr data-name="{{ row.name|lower }} {{ row.kr_name|lower }}"
              data-price="{{ row.chaos }}"
              data-level="{{ row.level }}">
            <td>{{ i }}</td>
            <td>
              {% if row.kr_name != row.name %}
                <span class="fw-semibold">{{ row.kr_name }}</span>
                <br><span class="text-muted" style="font-size:0.75rem">{{ row.name }}</span>
              {% else %}
                {{ row.name }}
              {% endif %}
            </td>
            <td>{{ row.level }}</td>
            <td class="text-end">{{ "%.1f"|format(row.chaos) }}</td>
            {% if snapshots|length > 1 %}
              <td class="text-end" style="color:#aaa; font-size:0.8rem">
                {{ "%.1f"|format(row.prev1_chaos) if row.prev1_chaos is not none else '-' }}
              </td>
            {% endif %}
            <td class="text-end"
                data-pct="{{ row.pct1 if row.pct1 is not none else '' }}">
              {% if row.pct1 is not none %}
                <span class="{{ 'trend-up' if row.pct1 > 0 else ('trend-down' if row.pct1 < 0 else '') }}">
                  {{ ('▲' if row.pct1>0 else ('▼' if row.pct1<0 else '→')) }} {{ "%+.1f"|format(row.pct1) }}%
                </span>
              {% else %}<span class="text-muted">-</span>{% endif %}
            </td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
      </div>
    </div>
  </div>
</div>

<script>
  // ── 필터링 ───────────────────────────────────────────────
  const nameInput  = document.getElementById('nameSearch');
  const priceInput = document.getElementById('minPrice');
  const minLvInput = document.getElementById('minLevel');
  const maxLvInput = document.getElementById('maxLevel');
  const countEl    = document.getElementById('visibleCount');

  function applyFilters() {
    const name    = nameInput.value.toLowerCase();
    const minP    = parseFloat(priceInput.value) || 0;
    const minL    = parseInt(minLvInput.value)   || 0;
    const maxL    = parseInt(maxLvInput.value)   || 999;
    let visible   = 0;

    document.querySelectorAll('#tableBody tr').forEach(row => {
      const rName  = row.dataset.name;
      const rPrice = parseFloat(row.dataset.price);
      const rLevel = parseInt(row.dataset.level);
      const show = rName.includes(name) && rPrice >= minP && rLevel >= minL && rLevel <= maxL;
      row.classList.toggle('hidden', !show);
      if (show) visible++;
    });
    countEl.textContent = visible;
  }

  function resetFilters() {
    nameInput.value  = '';
    priceInput.value = '';
    minLvInput.value = '83';
    maxLvInput.value = '';
    applyFilters();
  }

  [nameInput, priceInput, minLvInput, maxLvInput].forEach(el =>
    el.addEventListener('input', applyFilters)
  );

  // ── 컬럼 정렬 ────────────────────────────────────────────
  let sortCol = 3, sortAsc = false;  // 기본: 가격 내림차순

  function sortTable(col) {
    if (sortCol === col) sortAsc = !sortAsc;
    else { sortCol = col; sortAsc = col < 3; }

    const tbody = document.getElementById('tableBody');
    const rows  = Array.from(tbody.querySelectorAll('tr:not(.hidden)'));

    rows.sort((a, b) => {
      let va, vb;
      if (col === 0) { va = parseInt(a.cells[0].textContent); vb = parseInt(b.cells[0].textContent); }
      else if (col === 1) { va = a.dataset.name; vb = b.dataset.name; }
      else if (col === 2) { va = parseInt(a.dataset.level);  vb = parseInt(b.dataset.level); }
      else if (col === 3) { va = parseFloat(a.dataset.price); vb = parseFloat(b.dataset.price); }
      else {  // 변동%
        va = parseFloat(a.querySelector('[data-pct]')?.dataset.pct) || -9999;
        vb = parseFloat(b.querySelector('[data-pct]')?.dataset.pct) || -9999;
      }
      if (va < vb) return sortAsc ? -1 : 1;
      if (va > vb) return sortAsc ?  1 : -1;
      return 0;
    });

    rows.forEach(r => tbody.appendChild(r));
  }
</script>
</body>
</html>
"""


LOGIN_TEMPLATE = """
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>관리자 로그인</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-dark text-light d-flex justify-content-center align-items-center" style="min-height:100vh">
<div class="card bg-secondary text-light p-4" style="width:340px">
  <h5 class="mb-4 text-center">PoE Economy 관리자</h5>
  {% if error %}<div class="alert alert-danger py-2">{{ error }}</div>{% endif %}
  <form method="post">
    <div class="mb-3">
      <label class="form-label">아이디</label>
      <input type="text" name="username" class="form-control" autofocus>
    </div>
    <div class="mb-3">
      <label class="form-label">비밀번호</label>
      <input type="password" name="password" class="form-control">
    </div>
    <button class="btn btn-primary w-100">로그인</button>
  </form>
  <div class="mt-3 text-center">
    <a href="{{ url_for('index') }}" class="text-muted small">← 대시보드로</a>
  </div>
</div>
</body>
</html>
"""


ADMIN_TEMPLATE = """
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>임계값 설정</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-dark text-light">
<nav class="navbar navbar-dark bg-secondary px-3 mb-4">
  <a class="navbar-brand" href="{{ url_for('index') }}">PoE Economy 관리자</a>
  <div class="d-flex align-items-center gap-3">
    <span class="text-light small">{{ admin_user }}</span>
    <a href="{{ url_for('logout') }}" class="btn btn-outline-light btn-sm">로그아웃</a>
  </div>
</nav>

<div class="container">
  <div class="row">
    <div class="col-lg-5 mb-4">
      <div class="card bg-secondary text-light">
        <div class="card-header fw-bold">임계값 추가 / 수정</div>
        <div class="card-body">
          <form method="post">
            <input type="hidden" name="action" value="upsert">
            <div class="mb-3">
              <label class="form-label">카테고리</label>
              <select class="form-select" name="category">
                <option value="global">global (전체 기본값)</option>
                <option value="currency">currency</option>
                <option value="scarab">scarab</option>
                <option value="divination-card">divination-card</option>
                <option value="gem">gem</option>
              </select>
            </div>
            <div class="mb-3">
              <label class="form-label">이름 <span class="text-muted small">(비우면 카테고리 전체 적용)</span></label>
              <input type="text" class="form-control" name="name" placeholder="예: Divine Orb">
            </div>
            <div class="mb-3">
              <label class="form-label">임계값 (%)</label>
              <input type="number" class="form-control" name="threshold_percent"
                     step="0.1" value="{{ default_threshold }}">
            </div>
            <div class="mb-3">
              <label class="form-label">절대값 임계값 (c)
                <span class="text-muted small">— 비우면 기본값 {{ default_chaos }}c 사용</span>
              </label>
              <input type="number" class="form-control" name="chaos_threshold"
                     step="0.1" placeholder="{{ default_chaos }}">
            </div>
            <div class="alert alert-secondary py-2 small">
              % 조건 <strong>OR</strong> 절대값 조건 — <strong>둘 중 하나</strong>만 충족해도 알림 발송
            </div>
            <button class="btn btn-primary w-100">저장</button>
          </form>
        </div>
      </div>
    </div>

    <div class="col-lg-7 mb-4">
      <div class="card bg-secondary text-light">
        <div class="card-header fw-bold">설정된 임계값 목록</div>
        <div class="card-body p-0">
          <table class="table table-dark table-striped table-hover mb-0">
            <thead>
              <tr><th>#</th><th>카테고리</th><th>이름</th><th>임계값(%)</th><th>절대값(c)</th><th></th></tr>
            </thead>
            <tbody>
            {% for i, t in enumerate(thresholds, 1) %}
              <tr>
                <td>{{ i }}</td>
                <td>{{ t.category }}</td>
                <td>{{ t.name or '(전체)' }}</td>
                <td>{{ "%.1f"|format(t.threshold_percent) }}</td>
                <td>{{ "%.1f"|format(t.chaos_threshold) if t.chaos_threshold is not none else '(기본 '+default_chaos|string+'c)' }}</td>
                <td>
                  <form method="post" class="d-inline" onsubmit="return confirm('삭제할까요?')">
                    <input type="hidden" name="action" value="delete">
                    <input type="hidden" name="id" value="{{ t.id }}">
                    <button class="btn btn-danger btn-sm py-0">삭제</button>
                  </form>
                </td>
              </tr>
            {% endfor %}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</div>
</body>
</html>
"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
