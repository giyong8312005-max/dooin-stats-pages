# -*- coding: utf-8 -*-
"""
통계 페이지 생성기 (Phase 2 + 2026-07-16 디자인·구성 대개편)

data/raw/regions/*.json (크롤러 산출물) →
  site/{시도}/{시군구}/{유형}/index.html   지역×유형 통계 페이지 (1,300여 개)
  site/{시도}/index.html                   시/도 허브 페이지 (17개)
  site/index.html                          첫 화면 (전국 목록 + 지역 검색)

  - 물건유형 5개 그룹(아파트/빌라·다세대/상가/토지/단독주택)별로 집계
  - 지역별 자동 요약 문단, 전월 대비 증감, FAQ, 경로표시줄, 인접 지역 링크
  - 구조화 데이터: Dataset + BreadcrumbList + FAQPage
  - 4-4 규칙: 진행 물건 0건이어도 이력이 있으면 생성, 이력조차 없으면 제외
  - 두인 브랜드(로고·파랑 #2295F0) 적용, 공통 스타일은 static/style.css

실행:
  python generator.py            전체 생성 (기존 결과물 비우고 새로)
  python generator.py 11-740     특정 지역 페이지만 다시 생성 (서울 강동구)
"""

import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader

BASE_DIR = Path(__file__).parent
RAW_REGIONS_DIR = BASE_DIR / "data" / "raw" / "regions"
REGIONS_PATH = BASE_DIR / "data" / "regions.json"
STATIC_DIR = BASE_DIR / "static"
SITE_DIR = BASE_DIR / "site"
KST = ZoneInfo("Asia/Seoul")

MAIN_SITE_URL = "https://www.dooinauction.com"
BASE_SITE_URL = "https://dooin-stats.netlify.app"  # 사이트 공개 주소 — sitemap/canonical에 사용
# 알림 신청 접수 주소 — 카톡 알림 서비스(dooin-kakao-alert)의 구글시트 접수 창구를 공유한다.
# 신청 즉시 '경매 유저 조건 DB' 시트에 조건 세트로 등록되어 실제 카톡 알림 구독자가 된다.
# (공개 페이지에 원래 노출되는 접수용 주소라 비밀정보 아님)
FORM_ENDPOINT = "https://script.google.com/macros/s/AKfycbw7k34JFFjIpmob7xDllAGBsHiOBF0qTS878gJz38QXOeQabOTQZ74OcjWXYCkhEatPug/exec"
LISTING_TOP_N = 8      # 물건 리스트에 보여줄 최대 건수
NEIGHBOR_TOP_N = 12    # 인접 지역 링크 최대 개수

# 시/도 표준 표시 순서
SIDO_ORDER = ["서울", "부산", "대구", "인천", "광주전남", "대전", "울산", "세종",
              "경기", "강원", "충북", "충남", "전북", "경북", "경남", "제주"]

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
TYPE_KO = {g["key"]: g["ko"] for g in TYPE_GROUPS}
TYPE_ORDER = {g["key"]: i for i, g in enumerate(TYPE_GROUPS)}


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


# ─────────────────────────────────────────────
# 자동 요약 문단 · FAQ (페이지마다 데이터로 고유 생성 — SEO 핵심)
# ─────────────────────────────────────────────
def build_summary(region_meta, group, now, current_count, latest_stat,
                  latest_price, prev_price, appraisals) -> str:
    name, type_ko = region_meta["name"], group["ko"]
    s = []
    if current_count:
        s.append(f"{now.year}년 {now.month}월 현재 {region_meta['sido_ko']} {name} "
                 f"{type_ko} 법원경매는 총 {current_count}건이 진행 중입니다.")
    else:
        s.append(f"{now.year}년 {now.month}월 현재 {region_meta['sido_ko']} {name}에는 "
                 f"진행 중인 {type_ko} 법원경매가 없습니다.")

    if latest_stat:
        if latest_stat["sold"]:
            line = (f"지난 {latest_stat['month']}월에는 {latest_stat['total']}건이 경매에 부쳐져 "
                    f"{latest_stat['sold']}건이 낙찰되었으며(낙찰률 {latest_stat['sold_rate']}%)")
            if latest_stat["sale_price_rate"] is not None:
                line += f", 평균 낙찰가율은 {latest_stat['sale_price_rate']}%였습니다."
            else:
                line += "."
        else:
            line = (f"지난 {latest_stat['month']}월에는 {latest_stat['total']}건이 "
                    f"경매에 부쳐졌으나 낙찰 사례는 없었습니다.")
        s.append(line)

    if latest_price and prev_price:
        diff = round(latest_price["sale_price_rate"] - prev_price["sale_price_rate"], 1)
        if abs(diff) < 0.05:
            s.append(f"낙찰가율은 전월({prev_price['month']}월 {prev_price['sale_price_rate']}%)과 "
                     f"비슷한 수준입니다.")
        else:
            word = "상승" if diff > 0 else "하락"
            s.append(f"낙찰가율은 전월({prev_price['month']}월 {prev_price['sale_price_rate']}%) "
                     f"대비 {abs(diff)}%p {word}했습니다.")

    if appraisals and len(appraisals) >= 2 and min(appraisals) != max(appraisals):
        s.append(f"현재 진행 물건의 감정가는 {fmt_money(min(appraisals))} 원부터 "
                 f"{fmt_money(max(appraisals))} 원까지 분포합니다.")
    return " ".join(s)


def build_faq(region_meta, group, latest_price) -> list[dict]:
    name, type_ko = region_meta["name"], group["ko"]
    a2 = ("감정가 대비 낙찰가의 비율입니다. 예를 들어 감정가 1억 원인 물건이 "
          "9,000만 원에 낙찰되면 낙찰가율은 90%입니다.")
    if latest_price:
        a2 += f" {name} {type_ko}의 최근 낙찰가율은 {latest_price['sale_price_rate']}%입니다."
    return [
        {"q": f"{name} {type_ko} 경매 물건은 어디서 확인하나요?",
         "a": (f"이 페이지에서 {name} {type_ko} 경매의 진행 물건과 감정가·최저가·매각기일, "
               "최근 6개월 낙찰가율 통계를 무료로 확인할 수 있습니다. 등기·임차인 권리분석 등 "
               "상세 정보는 두인경매 본사이트에서 제공합니다.")},
        {"q": "낙찰가율이란 무엇인가요?", "a": a2},
        {"q": f"{name}에 새 {type_ko} 경매가 나오면 알림을 받을 수 있나요?",
         "a": (f"네. 이 페이지의 '무료 알림 신청'에 휴대전화 번호를 등록하면 {name}에 새 "
               f"{type_ko} 경매가 등록될 때 카카오톡으로 알려드립니다. 알림은 무료이며 "
               "언제든 해지할 수 있습니다.")},
    ]


# ─────────────────────────────────────────────
# 페이지 1장에 들어갈 값 계산
# ─────────────────────────────────────────────
def build_page_context(region_data: dict, region_meta: dict, group: dict,
                       now: datetime) -> dict | None:
    """페이지 1장의 컨텍스트. 데이터가 전혀 없으면 None (생성 제외).
    인접 지역 링크(neighbors)와 다른 유형 링크(sibling_links)는 전체 페이지 목록이
    확정된 뒤 generate()에서 채운다 (없는 페이지로 링크하지 않기 위해)."""
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

    # 최근 데이터가 있는 달 + 그 직전 달 (전월 대비 증감용)
    stat_months = [m for m in monthly if m["total"]]
    latest_stat = stat_months[-1] if stat_months else None
    prev_stat = stat_months[-2] if len(stat_months) > 1 else None
    price_months = [m for m in monthly if m["sale_price_rate"] is not None]
    latest_price = price_months[-1] if price_months else None
    prev_price = price_months[-2] if len(price_months) > 1 else None

    delta_rate = (round(latest_stat["sold_rate"] - prev_stat["sold_rate"], 1)
                  if latest_stat and prev_stat
                  and latest_stat["sold_rate"] is not None
                  and prev_stat["sold_rate"] is not None else None)
    delta_price = (round(latest_price["sale_price_rate"] - prev_price["sale_price_rate"], 1)
                   if latest_price and prev_price else None)

    appraisals = [l["appraisal"] for l in listings_all if l["appraisal"]]
    avg_appr = (sum(appraisals) // len(appraisals)) if appraisals else \
               (latest_price["avg_appraisal"] if latest_price else None)

    # meta description: 핵심 수치 포함 1~2문장 (4-2)
    desc_bits = [f"{now.year}년 {now.month}월 {region_name} {group['ko']} 경매 진행 {len(listings_all)}건"]
    if latest_price:
        desc_bits.append(f"최근 낙찰가율 {latest_price['sale_price_rate']}%")
    meta_description = (", ".join(desc_bits)
                        + ". 감정가·최저가·매각기일과 최근 6개월 낙찰 통계를 무료로 확인하세요.")

    summary = build_summary(region_meta, group, now, len(listings_all),
                            latest_stat, latest_price, prev_price, appraisals)
    faq = build_faq(region_meta, group, latest_price)

    page_url = f"{BASE_SITE_URL}/{region_meta['sido_slug']}/{region_meta['slug']}/{group['key']}/"
    hub_url = f"{BASE_SITE_URL}/{region_meta['sido_slug']}/"

    # 구조화 데이터: Dataset + 경로표시줄 + FAQ (한 script 태그에 배열로)
    json_ld = json.dumps([
        {"@context": "https://schema.org", "@type": "Dataset",
         "name": f"{region_name} {group['ko']} 법원경매 통계",
         "description": meta_description,
         "creator": {"@type": "Organization", "name": "두인경매",
                     "url": MAIN_SITE_URL, "telephone": "1661-9910"},
         "dateModified": region_data["collected_at"],
         "spatialCoverage": f"{region_meta['sido_ko']} {region_name}"},
        {"@context": "https://schema.org", "@type": "BreadcrumbList",
         "itemListElement": [
             {"@type": "ListItem", "position": 1, "name": "홈", "item": f"{BASE_SITE_URL}/"},
             {"@type": "ListItem", "position": 2, "name": region_meta["sido_ko"], "item": hub_url},
             {"@type": "ListItem", "position": 3, "name": f"{region_name} {group['ko']}", "item": page_url}]},
        {"@context": "https://schema.org", "@type": "FAQPage",
         "mainEntity": [{"@type": "Question", "name": f["q"],
                         "acceptedAnswer": {"@type": "Answer", "text": f["a"]}} for f in faq]},
    ], ensure_ascii=False, indent=2)

    campaign = f"{region_meta['sido_slug']}-{region_meta['slug']}-{group['key']}"
    return {
        "region_name": region_name,
        "sido_ko": region_meta["sido_ko"],
        # 알림 신청 시 카톡 알림 크롤러가 이해하는 물건종류 문자열 (쉼표 구분)
        "alert_types": ", ".join(group["types"]),
        "type_ko": group["ko"],
        "now_label": f"{now.year}년 {now.month}월",
        "updated_at": region_data["collected_at"],
        "meta_description": meta_description,
        "summary": summary,
        "faq": faq,
        "json_ld": json_ld,
        "canonical_url": page_url if BASE_SITE_URL else "",
        "hub_href": f"/{region_meta['sido_slug']}/",
        "main_site_url": f"{MAIN_SITE_URL}/?utm_source=stats&utm_medium=organic&utm_campaign={campaign}",
        "cta2_url": f"{MAIN_SITE_URL}/?utm_source=stats&utm_medium=organic&utm_campaign={campaign}",
        "form_endpoint": FORM_ENDPOINT,
        "current_count": len(listings_all),
        "latest_stat_label": f"{latest_stat['month']}월" if latest_stat else "-",
        "latest_sold_rate": latest_stat["sold_rate"] if latest_stat else None,
        "latest_price_label": f"{latest_price['month']}월" if latest_price else "-",
        "latest_price_rate": latest_price["sale_price_rate"] if latest_price else None,
        "delta_rate": delta_rate,
        "delta_price": delta_price,
        "avg_appraisal": fmt_money(avg_appr),
        "listings": listings,
        "chart_months": json.dumps([m["label"] for m in monthly]),
        "chart_price_rates": json.dumps([m["sale_price_rate"] for m in monthly]),
        "chart_counts": json.dumps([m["total"] for m in monthly]),
    }


# ─────────────────────────────────────────────
# 생성 실행
# ─────────────────────────────────────────────
def load_region_meta() -> dict:
    """(siCd, guCd) → 지역 메타(슬러그 포함) 매핑"""
    data = json.loads(REGIONS_PATH.read_text(encoding="utf-8"))
    return {(r["siCd"], r["guCd"]): r for r in data["regions"]}


def region_types_ctx(rr: dict) -> list[dict]:
    """허브·첫 화면의 지역 한 줄에 붙는 유형 링크 목록"""
    m = rr["meta"]
    return [{"href": f"/{m['sido_slug']}/{m['slug']}/{k}/", "ko": TYPE_KO[k]}
            for k in sorted(rr["types"], key=lambda k: TYPE_ORDER[k])]


def write_site_extras(page_urls: list[str], now: datetime):
    """정적 자원 복사 + sitemap.xml, robots.txt, _redirects, 소유확인 파일, .nojekyll"""
    # 공통 스타일·로고·파비콘
    assets_dir = SITE_DIR / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(STATIC_DIR / "style.css", assets_dir / "style.css")
    shutil.copy(STATIC_DIR / "logo.png", SITE_DIR / "logo.png")
    shutil.copy(STATIC_DIR / "favicon.svg", SITE_DIR / "favicon.svg")

    # .nojekyll: 정적 호스팅이 파일을 가공하지 않도록
    (SITE_DIR / ".nojekyll").write_text("", encoding="utf-8")

    # 옛 주소 자동 이동 (Netlify 규칙): 2026-07 광주/전남 통합 이전 주소 대응
    (SITE_DIR / "_redirects").write_text(
        "/gwangju/*  /gwangju-jeonnam/:splat  301\n"
        "/jeonnam/*  /gwangju-jeonnam/:splat  301\n", encoding="utf-8")

    # Google Search Console 소유권 확인 파일 (2026-07-16 등록) — 삭제 금지, 지우면 소유권 해제됨
    (SITE_DIR / "google9e9ed6ceb3075194.html").write_text(
        "google-site-verification: google9e9ed6ceb3075194.html", encoding="utf-8")

    # 네이버 서치어드바이저 소유확인 파일 (2026-07-16 등록) — 삭제 금지
    (SITE_DIR / "naver11bd2cdab3a3c6829113138a94cb5a7b.html").write_text(
        "naver-site-verification: naver11bd2cdab3a3c6829113138a94cb5a7b.html", encoding="utf-8")

    if not BASE_SITE_URL:
        return
    # sitemap.xml
    entries = "\n".join(
        f"  <url><loc>{u}</loc><lastmod>{now.date().isoformat()}</lastmod></url>" for u in page_urls)
    sitemap = ('<?xml version="1.0" encoding="UTF-8"?>\n'
               '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
               f"{entries}\n</urlset>\n")
    (SITE_DIR / "sitemap.xml").write_text(sitemap, encoding="utf-8")
    # robots.txt
    (SITE_DIR / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\n\nSitemap: {BASE_SITE_URL}/sitemap.xml\n", encoding="utf-8")


def generate():
    env = Environment(loader=FileSystemLoader(BASE_DIR / "templates"), autoescape=True)
    tpl_region = env.get_template("region_type.html")
    tpl_hub = env.get_template("sido_hub.html")
    tpl_home = env.get_template("home.html")
    meta_by_code = load_region_meta()
    now = datetime.now(KST)

    only = sys.argv[1] if len(sys.argv) > 1 else None
    files = sorted(RAW_REGIONS_DIR.glob("*.json"))
    if not files:
        print("생성할 지역 데이터가 없습니다. 먼저 crawler.py를 실행하세요.")
        return

    # 전체 생성 시 기존 결과물을 깨끗이 비운다 — 지역명/주소가 바뀌어도 옛 파일이 남지 않도록
    if not only and SITE_DIR.exists():
        shutil.rmtree(SITE_DIR)
    SITE_DIR.mkdir(parents=True, exist_ok=True)

    # ── 1단계: 모든 지역의 페이지 내용 계산 (링크 대상 파악을 위해 항상 전체 계산) ──
    entries = []          # 만들 페이지들: {"meta", "group", "ctx"}
    region_rows = {}      # 지역 요약: (siCd,guCd) → {"meta", "listing_count", "types"}
    skipped = 0
    for f in files:
        region_data = json.loads(f.read_text(encoding="utf-8"))
        si_cd, gu_cd = f.stem.split("-", 1)
        meta = meta_by_code.get((si_cd, gu_cd))
        if not meta:
            print(f"  ! {f.stem}: regions.json에 없는 지역 — 건너뜀")
            continue
        rr = {"meta": meta, "listing_count": len(region_data["listings"]), "types": []}
        for group in TYPE_GROUPS:
            ctx = build_page_context(region_data, meta, group, now)
            if ctx is None:
                skipped += 1
                continue
            entries.append({"meta": meta, "group": group, "ctx": ctx, "file_stem": f.stem})
            rr["types"].append(group["key"])
        if rr["types"]:
            region_rows[(si_cd, gu_cd)] = rr

    existing = {(e["meta"]["sido_slug"], e["meta"]["slug"], e["group"]["key"]) for e in entries}

    # 시/도별 지역 묶음 (표준 순서, 지역명 가나다순)
    by_sido: dict[str, dict] = {}
    for rr in region_rows.values():
        m = rr["meta"]
        by_sido.setdefault(m["sido_ko"], {"sido_slug": m["sido_slug"], "regions": []})
        by_sido[m["sido_ko"]]["regions"].append(rr)
    for v in by_sido.values():
        v["regions"].sort(key=lambda r: r["meta"]["name"])
    sido_sorted = sorted(by_sido, key=lambda s: SIDO_ORDER.index(s) if s in SIDO_ORDER else 99)

    # ── 2단계: 지역×유형 페이지 렌더 (존재하는 페이지로만 링크) ──
    made = 0
    for e in entries:
        if only and e["file_stem"] != only:
            continue
        meta, group, ctx = e["meta"], e["group"], e["ctx"]
        ctx["sibling_links"] = [
            {"href": f"/{meta['sido_slug']}/{meta['slug']}/{k}/", "ko": TYPE_KO[k]}
            for k in TYPE_KO
            if k != group["key"] and (meta["sido_slug"], meta["slug"], k) in existing]
        neighbors = []
        for rr in by_sido[meta["sido_ko"]]["regions"]:
            m2 = rr["meta"]
            if m2["slug"] == meta["slug"]:
                continue
            if (m2["sido_slug"], m2["slug"], group["key"]) in existing:
                neighbors.append({"name": m2["name"],
                                  "href": f"/{m2['sido_slug']}/{m2['slug']}/{group['key']}/"})
            if len(neighbors) >= NEIGHBOR_TOP_N:
                break
        ctx["neighbors"] = neighbors

        out = SITE_DIR / meta["sido_slug"] / meta["slug"] / group["key"] / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(tpl_region.render(**ctx), encoding="utf-8")
        made += 1

    # ── 3단계: 시/도 허브 페이지 ──
    hubs = 0
    for sido_ko in sido_sorted:
        v = by_sido[sido_ko]
        total_listings = sum(rr["listing_count"] for rr in v["regions"])
        regions_ctx = [{"name": rr["meta"]["name"], "listing_count": rr["listing_count"],
                        "types": region_types_ctx(rr)} for rr in v["regions"]]
        hub_url = f"{BASE_SITE_URL}/{v['sido_slug']}/"
        json_ld = json.dumps([
            {"@context": "https://schema.org", "@type": "CollectionPage",
             "name": f"{sido_ko} 법원경매 통계",
             "description": f"{sido_ko} {len(v['regions'])}개 시·군·구 법원경매 진행 물건·낙찰가율 통계",
             "url": hub_url},
            {"@context": "https://schema.org", "@type": "BreadcrumbList",
             "itemListElement": [
                 {"@type": "ListItem", "position": 1, "name": "홈", "item": f"{BASE_SITE_URL}/"},
                 {"@type": "ListItem", "position": 2, "name": sido_ko, "item": hub_url}]},
        ], ensure_ascii=False, indent=2)
        campaign = f"hub-{v['sido_slug']}"
        html = tpl_hub.render(
            sido_ko=sido_ko, now_label=f"{now.year}년 {now.month}월",
            updated_at=now.date().isoformat(),
            canonical_url=hub_url, json_ld=json_ld,
            total_listings_fmt=f"{total_listings:,}", region_count=len(v["regions"]),
            regions=regions_ctx,
            main_site_url=f"{MAIN_SITE_URL}/?utm_source=stats&utm_medium=organic&utm_campaign={campaign}",
            cta2_url=f"{MAIN_SITE_URL}/?utm_source=stats&utm_medium=organic&utm_campaign={campaign}")
        out = SITE_DIR / v["sido_slug"] / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
        hubs += 1
        print(f"  {sido_ko}: 지역 {len(v['regions'])}개, 진행 물건 {total_listings:,}건")

    # ── 4단계: 첫 화면 ──
    total_all = sum(rr["listing_count"] for rr in region_rows.values())
    sidos_ctx = [{"sido_ko": s, "hub_href": f"/{by_sido[s]['sido_slug']}/",
                  "regions": [{"name": rr["meta"]["name"], "listing_count": rr["listing_count"],
                               "types": region_types_ctx(rr)} for rr in by_sido[s]["regions"]]}
                 for s in sido_sorted]
    home_html = tpl_home.render(
        base_url=BASE_SITE_URL, updated_at=now.date().isoformat(),
        total_listings_fmt=f"{total_all:,}", region_count=len(region_rows),
        page_count_fmt=f"{len(entries):,}", sidos=sidos_ctx,
        main_site_url=f"{MAIN_SITE_URL}/?utm_source=stats&utm_medium=organic&utm_campaign=home")
    (SITE_DIR / "index.html").write_text(home_html, encoding="utf-8")

    # ── 5단계: sitemap·robots·정적 자원 등 ──
    page_urls = [f"{BASE_SITE_URL}/"]
    page_urls += [f"{BASE_SITE_URL}/{by_sido[s]['sido_slug']}/" for s in sido_sorted]
    page_urls += [f"{BASE_SITE_URL}/{e['meta']['sido_slug']}/{e['meta']['slug']}/{e['group']['key']}/"
                  for e in entries]
    write_site_extras(page_urls, now)

    print(f"\n지역 페이지 {made}개 + 허브 {hubs}개 + 첫 화면 1개 생성, "
          f"{skipped}개 조합 제외(데이터 없음)")
    print(f"결과 폴더: {SITE_DIR} (+ sitemap.xml, robots.txt, 로고·스타일)")


if __name__ == "__main__":
    generate()
