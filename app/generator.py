# -*- coding: utf-8 -*-
"""
통계 페이지 생성기 (Phase 2)

data/raw/regions/*.json (크롤러 산출물) → site/{시도}/{시군구}/{유형}/index.html

  - 물건유형 5개 그룹(아파트/빌라·다세대/상가/토지/단독주택)별로 집계
  - 마스터 문서 4장 명세를 따름: 통계 카드, 6개월 추이 그래프, 물건 리스트,
    알림 신청 폼(CTA1), 본사이트 링크(CTA2), JSON-LD, 내부 링크
  - 4-4 규칙: 진행 물건 0건이어도 페이지는 만들되, 통계 이력도 없으면 생성 제외

실행:
  python generator.py            data/raw/regions/ 안의 모든 지역 생성
  python generator.py 11-740     특정 지역만 생성 (서울 강동구)
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader

BASE_DIR = Path(__file__).parent
RAW_REGIONS_DIR = BASE_DIR / "data" / "raw" / "regions"
REGIONS_PATH = BASE_DIR / "data" / "regions.json"
SITE_DIR = BASE_DIR / "site"
KST = ZoneInfo("Asia/Seoul")

MAIN_SITE_URL = "https://www.dooinauction.com"
BASE_SITE_URL = "https://giyong8312005-max.github.io/dooin-stats-pages"  # GitHub Pages 주소 — sitemap/canonical에 사용
FORM_ENDPOINT = ""     # Phase 3에서 전화번호 수집 주소(Google Sheets 웹훅) 입력
LISTING_TOP_N = 8      # 물건 리스트에 보여줄 최대 건수

# 물건유형 5개 그룹 — 사이트의 세부 유형을 페이지 5종으로 묶는다
TYPE_GROUPS = [
    {"key": "apartment", "ko": "아파트", "types": ["아파트"]},
    {"key": "villa", "ko": "빌라·다세대",
     "types": ["다세대주택", "연립주택", "도시형생활주택"]},
    {"key": "commercial", "ko": "상가",
     "types": ["근린상가", "근린생활시설", "상가주택", "오피스텔(상업)", "업무시설"]},
    {"key": "land", "ko": "토지",
     "types": ["전", "답", "과수원", "임야", "대지", "잡종지", "도로", "목장용지",
               "공장용지", "학교용지", "주차장", "주유소용지", "창고용지", "철도용지",
               "수도용지", "체육용지", "종교용지", "제방", "하천", "구거", "광천지",
               "염전", "유지", "양어장", "유원지", "사적지", "묘지"]},
    {"key": "house", "ko": "단독주택", "types": ["단독주택", "다가구주택"]},
]


# ─────────────────────────────────────────────
# 표시용 포맷 도우미
# ─────────────────────────────────────────────
def fmt_money(won) -> str:
    """1430000000 → '14억 3,000만'"""
    if not won:
        return "-"
    eok, rest = divmod(int(won), 100_000_000)
    man = rest // 10_000
    parts = []
    if eok:
        parts.append(f"{eok}억")
    if man:
        parts.append(f"{man:,}만")
    return " ".join(parts) if parts else f"{won:,}원"


def mask_case_no(case_no: str) -> str:
    """사건번호 일부 마스킹: '2025-51375' → '2025-513**' (마스터 문서 4-3)"""
    if len(case_no) >= 7 and "-" in case_no:
        return case_no[:-2] + "**"
    return case_no


def fmt_bid_date(yy_mm_dd: str) -> str:
    """'26.07.13' → '7/13'"""
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{2})", yy_mm_dd or "")
    if m:
        return f"{int(m.group(2))}/{int(m.group(3))}"
    return yy_mm_dd or "-"


def bid_date_sortkey(yy_mm_dd: str):
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{2})", yy_mm_dd or "")
    return (m.group(1), m.group(2), m.group(3)) if m else ("99", "99", "99")


# ─────────────────────────────────────────────
# 집계
# ─────────────────────────────────────────────
def aggregate_monthly(monthly_stats: list, types: list[str]) -> list[dict]:
    """월별 통계에서 해당 유형 그룹만 합산한다.
    낙찰가율·평균감정가는 낙찰 건수로 가중평균 (근사치 — 개별 낙찰가는 비공개라서)."""
    out = []
    for stat in monthly_stats:
        rows = [r for r in stat.get("rows", []) if r["type"] in types]
        total = sum(r["total"] for r in rows)
        sold = sum(r["sold"] for r in rows)
        failed = sum(r["failed"] for r in rows)

        weighted_price, weighted_appr, weight = 0.0, 0.0, 0
        for r in rows:
            if r["sold"] and r.get("sale_price_rate"):
                weighted_price += r["sale_price_rate"] * r["sold"]
                weighted_appr += (r.get("avg_appraisal") or 0) * r["sold"]
                weight += r["sold"]

        out.append({
            "year": stat["year"], "month": stat["month"],
            "label": f"{stat['year']%100}.{stat['month']:02d}",
            "total": total, "sold": sold, "failed": failed,
            "sold_rate": round(sold / total * 100, 1) if total else None,
            "sale_price_rate": round(weighted_price / weight, 1) if weight else None,
            "avg_appraisal": int(weighted_appr / weight) if weight else None,
        })
    return sorted(out, key=lambda x: (x["year"], x["month"]))


def build_page_context(region_data: dict, region_meta: dict, group: dict,
                       now: datetime) -> dict | None:
    """페이지 1장에 들어갈 모든 값을 계산한다. 데이터가 전혀 없으면 None (생성 제외)."""
    region_name = region_meta["name"]
    listings_all = [l for l in region_data["listings"] if l["type"] in group["types"]]
    monthly = aggregate_monthly(region_data["monthly_stats"], group["types"])

    # 4-4 규칙: 진행 물건도 없고 6개월 통계 이력도 전혀 없으면 페이지 생성 제외
    has_history = any(m["total"] for m in monthly)
    if not listings_all and not has_history:
        return None

    # 물건 리스트: 매각기일 빠른 순 → 감정가 큰 순, 상위 N건
    listings_all.sort(key=lambda l: (bid_date_sortkey(l["bid_date"]), -(l["appraisal"] or 0)))
    listings = [{
        "address": l["address"],
        "case_masked": mask_case_no(l["case_no"]) + (f"({l['item_no']})" if l["item_no"] else ""),
        "court": l["court"],
        "status": l["status"],
        "appraisal_fmt": fmt_money(l["appraisal"]),
        "min_price_fmt": fmt_money(l["min_price"]),
        "min_price_pct": l["min_price_pct"],
        "bid_date_fmt": fmt_bid_date(l["bid_date"]),
    } for l in listings_all[:LISTING_TOP_N]]

    # 통계 카드: 최근 데이터가 있는 달 기준
    latest_stat = next((m for m in reversed(monthly) if m["total"]), None)
    latest_price = next((m for m in reversed(monthly) if m["sale_price_rate"]), None)
    appraisals = [l["appraisal"] for l in listings_all if l["appraisal"]]
    avg_appr = (sum(appraisals) // len(appraisals)) if appraisals else \
               (latest_price["avg_appraisal"] if latest_price else None)

    # meta description: 핵심 수치 포함 1~2문장 (4-2)
    desc_bits = [f"{now.year}년 {now.month}월 {region_name} {group['ko']} 경매 진행 {len(listings_all)}건"]
    if latest_price:
        desc_bits.append(f"최근 낙찰가율 {latest_price['sale_price_rate']}%")
    meta_description = (", ".join(desc_bits)
                        + ". 감정가·최저가·매각기일과 최근 6개월 낙찰 통계를 무료로 확인하세요.")

    # JSON-LD (Dataset)
    json_ld = json.dumps({
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": f"{region_name} {group['ko']} 법원경매 통계",
        "description": meta_description,
        "creator": {"@type": "Organization", "name": "두인경매",
                    "url": MAIN_SITE_URL, "telephone": "1661-9910"},
        "dateModified": region_data["collected_at"],
        "spatialCoverage": f"{region_meta['sido_ko']} {region_name}",
    }, ensure_ascii=False, indent=2)

    campaign = f"{region_meta['sido_slug']}-{region_meta['slug']}-{group['key']}"
    return {
        "region_name": region_name,
        "type_ko": group["ko"],
        "now_label": f"{now.year}년 {now.month}월",
        "updated_at": region_data["collected_at"],
        "meta_description": meta_description,
        "json_ld": json_ld,
        "canonical_url": (f"{BASE_SITE_URL}/{region_meta['sido_slug']}/{region_meta['slug']}/{group['key']}/"
                          if BASE_SITE_URL else ""),
        "main_site_url": f"{MAIN_SITE_URL}/?utm_source=stats&utm_medium=organic&utm_campaign={campaign}",
        "cta2_url": f"{MAIN_SITE_URL}/?utm_source=stats&utm_medium=organic&utm_campaign={campaign}",
        "form_endpoint": FORM_ENDPOINT,
        "current_count": len(listings_all),
        "latest_stat_label": f"{latest_stat['month']}월" if latest_stat else "-",
        "latest_sold_rate": latest_stat["sold_rate"] if latest_stat else None,
        "latest_price_label": f"{latest_price['month']}월" if latest_price else "-",
        "latest_price_rate": latest_price["sale_price_rate"] if latest_price else None,
        "avg_appraisal": fmt_money(avg_appr),
        "listings": listings,
        "chart_months": json.dumps([m["label"] for m in monthly]),
        "chart_price_rates": json.dumps([m["sale_price_rate"] for m in monthly]),
        "chart_counts": json.dumps([m["total"] for m in monthly]),
        "sibling_links": [{"href": f"../{g['key']}/", "ko": g["ko"]}
                          for g in TYPE_GROUPS if g["key"] != group["key"]],
    }


# ─────────────────────────────────────────────
# 생성 실행
# ─────────────────────────────────────────────
def load_region_meta() -> dict:
    """(siCd, guCd) → 지역 메타(슬러그 포함) 매핑"""
    data = json.loads(REGIONS_PATH.read_text(encoding="utf-8"))
    return {(r["siCd"], r["guCd"]): r for r in data["regions"]}


def write_site_extras(generated: list[dict], now: datetime):
    """sitemap.xml, robots.txt, 첫 화면(index.html), .nojekyll 생성"""
    # .nojekyll: GitHub Pages가 파일을 가공하지 않도록
    (SITE_DIR / ".nojekyll").write_text("", encoding="utf-8")

    # 첫 화면: 시/도 → 지역 → 유형별 링크 (내부 링크 허브 겸 전체 목록)
    # 지역마다 실제로 생성된 유형(아파트/빌라/상가/토지/단독)을 모두 링크로 노출한다.
    type_ko = {g["key"]: g["ko"] for g in TYPE_GROUPS}
    type_order = {g["key"]: i for i, g in enumerate(TYPE_GROUPS)}
    sido_order = ["서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종",
                  "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주"]

    # (시도, 지역명) → 지역 정보 + 가진 유형 목록으로 모으기
    regions_map: dict[tuple, dict] = {}
    for g in generated:
        entry = regions_map.setdefault((g["sido_ko"], g["region_name"]), {
            "sido_ko": g["sido_ko"], "name": g["region_name"],
            "sido_slug": g["sido_slug"], "slug": g["slug"], "types": []})
        entry["types"].append(g["type_key"])

    by_sido: dict[str, list[dict]] = {}
    for entry in regions_map.values():
        by_sido.setdefault(entry["sido_ko"], []).append(entry)

    sections = []
    total_regions = len(regions_map)
    for sido_ko in sorted(by_sido, key=lambda s: sido_order.index(s) if s in sido_order else 99):
        rows = []
        for r in sorted(by_sido[sido_ko], key=lambda x: x["name"]):
            type_links = "".join(
                f'<a href="./{r["sido_slug"]}/{r["slug"]}/{t}/">{type_ko[t]}</a>'
                for t in sorted(r["types"], key=lambda t: type_order[t]))
            rows.append(f'<div class="region"><span class="rn">{r["name"]}</span>'
                        f'<span class="types">{type_links}</span></div>')
        sections.append(f'<section><h2>{sido_ko} '
                        f'<small>{len(by_sido[sido_ko])}개 지역</small></h2>{"".join(rows)}</section>')

    index_html = f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>전국 지역별 법원경매 통계 | 두인경매</title>
<meta name="description" content="전국 {total_regions}개 시·군·구별 아파트·빌라·상가·토지·단독주택 법원경매 진행 물건과 낙찰가율 통계. 두인경매 공식 부속 서비스.">
<style>
:root{{--navy:#16325c;--blue:#2b6cb0;--bg:#f7f9fc;--card:#fff;--line:#e2e8f0;--muted:#718096}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Apple SD Gothic Neo','Malgun Gothic',sans-serif;background:var(--bg);color:#2d3748;line-height:1.6}}
.wrap{{max-width:820px;margin:0 auto;padding:16px}}
header{{background:var(--navy);color:#fff}}
header .wrap{{padding-top:20px;padding-bottom:20px}}
header h1{{font-size:20px;letter-spacing:-.5px}}
header p{{font-size:13px;opacity:.85;margin-top:6px}}
section{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:14px 0}}
section h2{{font-size:16px;color:var(--navy);margin-bottom:10px;letter-spacing:-.3px}}
section h2 small{{font-weight:400;color:var(--muted);font-size:12px}}
.region{{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:8px 0;border-bottom:1px solid var(--line)}}
.region:last-child{{border-bottom:none}}
.rn{{font-weight:700;min-width:104px;font-size:14px}}
.types{{display:flex;flex-wrap:wrap;gap:6px}}
.types a{{font-size:12px;color:var(--blue);background:#ebf4ff;border-radius:12px;padding:3px 11px;text-decoration:none;white-space:nowrap}}
.types a:hover{{background:var(--blue);color:#fff}}
footer{{text-align:center;color:var(--muted);font-size:12px;padding:20px 16px 40px}}
</style></head>
<body>
<header><div class="wrap">
<h1>전국 지역별 법원경매 통계</h1>
<p>시·군·구별 아파트·빌라·상가·토지·단독주택 경매 진행 물건과 낙찰가율을 확인하세요 · 갱신 {now.date().isoformat()}</p>
</div></header>
<main class="wrap">
{"".join(sections)}
</main>
<footer>두인경매 공식 부속 통계 서비스 · 대표번호 1661-9910</footer>
</body></html>"""
    (SITE_DIR / "index.html").write_text(index_html, encoding="utf-8")

    if not BASE_SITE_URL:
        return
    # sitemap.xml
    urls = [f"{BASE_SITE_URL}/"] + [
        f"{BASE_SITE_URL}/{g['sido_slug']}/{g['slug']}/{g['type_key']}/" for g in generated]
    entries = "\n".join(
        f"  <url><loc>{u}</loc><lastmod>{now.date().isoformat()}</lastmod></url>" for u in urls)
    sitemap = ('<?xml version="1.0" encoding="UTF-8"?>\n'
               '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
               f"{entries}\n</urlset>\n")
    (SITE_DIR / "sitemap.xml").write_text(sitemap, encoding="utf-8")
    # robots.txt
    (SITE_DIR / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\n\nSitemap: {BASE_SITE_URL}/sitemap.xml\n", encoding="utf-8")


def generate():
    env = Environment(loader=FileSystemLoader(BASE_DIR / "templates"), autoescape=True)
    template = env.get_template("region_type.html")
    meta_by_code = load_region_meta()
    now = datetime.now(KST)

    only = sys.argv[1] if len(sys.argv) > 1 else None
    files = sorted(RAW_REGIONS_DIR.glob("*.json"))
    if only:
        files = [f for f in files if f.stem == only]
    if not files:
        print("생성할 지역 데이터가 없습니다. 먼저 crawler.py를 실행하세요.")
        return

    made, skipped = 0, 0
    generated = []
    for f in files:
        region_data = json.loads(f.read_text(encoding="utf-8"))
        si_cd, gu_cd = f.stem.split("-", 1)
        meta = meta_by_code.get((si_cd, gu_cd))
        if not meta:
            print(f"  ! {f.stem}: regions.json에 없는 지역 — 건너뜀")
            continue

        for group in TYPE_GROUPS:
            ctx = build_page_context(region_data, meta, group, now)
            if ctx is None:
                skipped += 1
                continue
            out = SITE_DIR / meta["sido_slug"] / meta["slug"] / group["key"] / "index.html"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(template.render(**ctx), encoding="utf-8")
            made += 1
            generated.append({"sido_ko": meta["sido_ko"], "sido_slug": meta["sido_slug"],
                              "slug": meta["slug"], "region_name": meta["name"],
                              "type_key": group["key"]})
        print(f"  {meta['sido_ko']} {meta['name']}: 생성 완료")

    write_site_extras(generated, now)
    print(f"\n총 {made}개 페이지 생성, {skipped}개 조합 제외(데이터 없음)")
    print(f"결과 폴더: {SITE_DIR} (+ index.html, sitemap.xml, robots.txt)")


if __name__ == "__main__":
    generate()
