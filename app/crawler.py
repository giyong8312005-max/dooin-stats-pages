# -*- coding: utf-8 -*-
"""
두인경매 지역별 통계 페이지용 크롤러 (v0.2 — 검토 반영판)

무엇을 하는가:
  1. 매각통계(caStats.php)   : 시/군/구 × 월 단위로 물건종류별 낙찰률·낙찰가율을 읽는다.
  2. 진행물건(AuctList.php)  : 시/군/구 단위로 현재 진행 중인 물건 목록(JSON)을 읽는다.

원칙 (마스터 문서 CLAUDE.md 6장):
  - 모든 요청은 자동으로 2~5초 간격이 보장된다 (요청 함수 안에 내장 — 누락 불가능)
  - 전국 실행(--full)은 한국시간 새벽 2~8시에만 동작 (코드가 직접 확인)
  - 로그인 없이 보이는 공개 정보만 수집 (낙찰가 등 회원 전용 정보는 건드리지 않음)
  - 응답이 이상하면(차단·세션만료) 빈 데이터로 저장하지 않고 실패로 기록

실행 방법:
  python crawler.py --test              서울 강동구 1곳 소량 테스트
  python crawler.py --full              전국 전체 수집 (새벽에만 동작, 지역별 저장)
  python crawler.py --full --months 6   과거 6개월 통계까지 수집 (최초 1회 백필용)
  python crawler.py --full --force      시간대 가드 무시 (수동 테스트용 — 평소 금지)
"""

import json
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────
# 기본 설정
# ─────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
REGIONS_PATH = DATA_DIR / "regions.json"

BASE_URL = "https://www.dooinauction.com"
LIST_PAGE_URL = f"{BASE_URL}/ca/caList.php"          # 종합검색 화면 (세션 쿠키 받는 용도)
LIST_API_URL = f"{BASE_URL}/ca/res/AuctList.php"     # 물건 목록 JSON
STATS_URL = f"{BASE_URL}/ca/caStats.php"             # 매각통계 HTML

KST = ZoneInfo("Asia/Seoul")   # 모든 날짜·시간 판단은 한국시간 기준 (GitHub Actions 서버는 UTC라서 필수)

# 브라우저와 같은 User-Agent (사이트가 프로그램 접근을 막고 있어 필요)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# 요청 사이 대기 시간 (초) — 서버 부하 방지, 절대 줄이지 말 것
DELAY_MIN = 2.0
DELAY_MAX = 5.0

PAGE_SIZE = 50          # 한 번에 받는 물건 수
MAX_PAGES = 40          # 지역당 페이지 상한 (폭주 방지)
CRAWL_HOUR_BEGIN = 2    # 전국 실행 허용 시간대 (KST)
CRAWL_HOUR_END = 8
CIRCUIT_BREAKER = 5     # 연속 실패 지역이 이만큼 쌓이면 전체 중단


def now_kst() -> datetime:
    return datetime.now(KST)


def strip_html(text: str) -> str:
    """HTML 태그를 걷어내고 순수 글자만 남긴다. (예: '유찰 <br>2회' → '유찰 2회')"""
    no_tags = re.sub(r"<[^>]+>", " ", str(text or ""))
    return re.sub(r"\s+", " ", no_tags).strip()


def to_int(text):
    """'437,863,636' 같은 문자열 → 정수. 이미 숫자면 그대로. 숫자가 없으면 None."""
    if isinstance(text, int):
        return text
    digits = re.sub(r"[^\d]", "", str(text or ""))
    return int(digits) if digits else None


def to_pct(text: str):
    """'37.29%' → 37.29 (숫자). '-'나 빈 값은 None."""
    m = re.search(r"[\d.]+", str(text or ""))
    return float(m.group(0)) if m else None


class BadResponse(Exception):
    """차단·세션만료·화면구조 변경 등 '정상이 아닌 응답'을 뜻하는 오류.
    빈 데이터와 구분하기 위해 반드시 예외로 처리한다 (조용히 넘어가지 않음)."""


# ─────────────────────────────────────────────
# 크롤러 본체
# ─────────────────────────────────────────────
class DooinCrawler:
    """세션 관리 + 자동 딜레이 + 재시도를 책임지는 크롤러.

    딜레이는 _get() 안에 내장되어 있어서, 어떤 함수를 어떤 순서로 불러도
    요청 사이 2~5초 간격이 항상 지켜진다 (호출자가 신경 쓸 필요 없음).
    """

    def __init__(self):
        self._last_request_at = 0.0   # 마지막 요청 시각 (자동 딜레이용)
        self.session = None
        self._new_session()

    # ── 내부: 요청 공통 처리 ──
    def _wait_turn(self):
        """직전 요청으로부터 2~5초가 지날 때까지 기다린다. (모든 요청의 공통 관문)"""
        gap = random.uniform(DELAY_MIN, DELAY_MAX)
        wake_at = self._last_request_at + gap
        sleep_for = wake_at - time.monotonic()
        if sleep_for > 0:
            time.sleep(sleep_for)

    def _get(self, url: str, *, params=None, headers=None, max_tries: int = 3) -> requests.Response:
        """딜레이 + 3회 재시도가 내장된 GET 요청."""
        last_error = None
        for attempt in range(1, max_tries + 1):
            self._wait_turn()
            try:
                self._last_request_at = time.monotonic()
                r = self.session.get(url, params=params, headers=headers, timeout=30)
                r.raise_for_status()
                return r
            except Exception as e:
                last_error = e
                wait = 5 * attempt * attempt   # 5초, 20초
                print(f"    ! 요청 실패 ({attempt}/{max_tries}회): {type(e).__name__}"
                      + (f" — {wait}초 후 재시도" if attempt < max_tries else ""))
                if attempt < max_tries:
                    time.sleep(wait)
        raise BadResponse(f"요청이 계속 실패했습니다: {url}") from last_error

    def _new_session(self):
        """세션(연결 통로)을 새로 만든다. 물건 목록 API는 '직접접근'을 막고 있어서
        먼저 검색 화면을 한 번 방문해 쿠키를 받아야 한다. (재시도 포함)"""
        print("[세션] 쿠키 받는 중 (검색 화면 1회 방문)...")
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        r = self._get(LIST_PAGE_URL)
        if "dooinauction" not in (r.text or "").lower():
            raise BadResponse("검색 화면이 정상적으로 열리지 않았습니다.")
        print(f"    OK (쿠키 {len(self.session.cookies)}개)")

    # ── 1. 매각통계 (월간 · 지역별) — 낙찰률/낙찰가율의 출처 ──
    def fetch_monthly_stats(self, si_cd: str, gu_cd: str, year: int, month: int,
                            region_name: str = "") -> dict:
        """특정 시/군/구의 특정 달 매각통계.

        돌려주는 값: {"year", "month", "siCd", "guCd", "title", "rows": [...]}
        각 row: {"type": "아파트", "total": 14, "failed": 7, "sold": 4,
                 "sold_rate": 28.57, "avg_appraisal": 4725250000, "sale_price_rate": 96.77}
        (sold_rate = 매각건율 = 낙찰률, sale_price_rate = 매각가율 = 낙찰가율)

        응답 표의 제목에 요청한 연·월·지역명이 없으면 차단/오응답으로 보고
        BadResponse를 던진다 — 빈 데이터를 정상인 척 저장하지 않기 위함.
        """
        params = {
            "statDvsn": "2",              # 2 = 지역별 통계
            "ymdDvsn": "2",               # 2 = 월간 통계
            "syear": str(year), "smnth": f"{month:02d}",
            "year": str(year), "mnth": f"{month:02d}",
            "day": "0",
            "siCd": si_cd, "guCd": gu_cd,
        }
        r = self._get(STATS_URL, params=params)
        soup = BeautifulSoup(r.text, "html.parser")

        table = soup.find("table", id="list_box")
        if table is None:
            raise BadResponse("통계 표(list_box)가 없음 — 차단이거나 화면 구조 변경")

        # 제목 검증: "2026. 06 월간매각 통계자료 : 서울 강동구" 형식이어야 함
        title = strip_html(str(table.find("tr") or ""))
        expected_ym = f"{year}. {month:02d}"
        if expected_ym not in title:
            raise BadResponse(f"통계 제목에 요청한 연·월({expected_ym})이 없음: '{title[:60]}'")
        if region_name:
            # '고양시 덕양구'처럼 두 단어 지역도 그대로 제목에 들어간다
            if region_name.split()[-1] not in title:
                raise BadResponse(f"통계 제목에 요청 지역({region_name})이 없음: '{title[:60]}'")

        rows = []
        for tr in table.find_all("tr"):
            cells = [strip_html(str(td)) for td in tr.find_all("td")]
            # 데이터 행은 12칸: 구분/총건수/유찰/매각/변경/정지/취하/기각/기타/매각건율/평균감정가/매각가율
            if len(cells) != 12:
                continue
            name = cells[0]
            if name in ("구분", "합계"):   # 머리글 제외, 합계는 사이트 표시가 부정확해 직접 계산
                continue
            rows.append({
                "type": name,
                "total": to_int(cells[1]) or 0,
                "failed": to_int(cells[2]) or 0,          # 유찰
                "sold": to_int(cells[3]) or 0,            # 매각(낙찰)
                "changed": to_int(cells[4]) or 0,         # 변경
                "withdrawn": to_int(cells[6]) or 0,       # 취하
                "sold_rate": to_pct(cells[9]),            # 매각건율(낙찰률) %
                "avg_appraisal": to_int(cells[10]),       # 평균감정가 (원)
                "sale_price_rate": to_pct(cells[11]),     # 매각가율(낙찰가율) %
            })

        return {"year": year, "month": month, "siCd": si_cd, "guCd": gu_cd,
                "title": title, "rows": rows}

    # ── 2. 진행물건 목록 — 페이지의 '현재 진행 물건' 블록 출처 ──
    def _listing_params(self, si_cd: str, gu_cd: str, page_no: int) -> dict:
        """물건 목록 API에 보낼 검색 조건. 사이트가 쓰는 형식을 그대로 따른다."""
        return {
            "dataSize": str(PAGE_SIZE), "pageNo": str(page_no),
            "ck_photo": "0", "lsType": "0", "odrCol": "14", "odrAds": "0",
            "srchFR": "0", "idxFR": "0",
            "stat": "11",                  # 11 = 진행물건 전체 (신건+유찰)
            "srchCase": "srchAll",
            "siCd": si_cd, "guCd": gu_cd, "dnCd": "0",
            "sptArr[0][]": ["0", "0"],
            "ctgr": "0",                   # 0 = 물건종류 전체 (종류 구분은 받은 데이터로 처리)
            "chkAllCtgr": "0",
            "fbCntBgn": "0", "fbCntEnd": "0",
            "apslAmtBgn": "0", "apslAmtEnd": "0",
            "minbAmtBgn": "0", "minbAmtEnd": "0",
            "totFlrBgn": "0", "totFlrEnd": "0", "flrBgn": "0", "flrEnd": "0",
            "baseFlr": "0", "dpslDvsn": "0", "local": "0", "line": "0",
            "station": "0", "distance": "0", "adrsEtcType": "0", "adrsEtc": "",
            "sn1": "0", "sn2": "0", "pn": "0", "bgnDt": "", "endDt": "",
            "landSqmBgn": "0", "landSqmEnd": "0",
            "bldgSqmBgn": "0", "bldgSqmEnd": "0",
            "prsvBgn": "0", "prsvEnd": "0", "preBgnDt": "", "preEndDt": "",
            "auctType": "0", "splSrchType": "0",
        }

    @staticmethod
    def _parse_listing_item(raw: dict) -> dict:
        """API가 주는 물건 1건을 우리가 쓸 필드만 남겨 정리한다. (공개 정보만)"""
        # 사건번호: 개별매각이면 "2024-3552<span ...>(1)</span>" 형태로 옴 → 태그 제거 후 본번호/물건번호 분리
        sano_clean = strip_html(raw.get("saNo", ""))
        m = re.match(r"(\d{4}-\d+)\s*(?:\((\d+)\))?", sano_clean)
        case_no = m.group(1) if m else sano_clean
        item_no = int(m.group(2)) if (m and m.group(2)) else None

        return {
            "tid": raw.get("tid"),                              # 사이트 내부 물건 번호
            "case_no": case_no,                                 # 사건번호 (예: 2025-51375)
            "item_no": item_no,                                 # 개별매각 물건번호 (없으면 None)
            "court": strip_html(raw.get("crtDpt", "")),         # 담당 법원/계
            "type": strip_html(raw.get("ctgr", "")),            # 물건종류 (아파트 등)
            "address": strip_html(raw.get("regnAdrs", "")),     # 소재지
            "area": strip_html(raw.get("areaInfo", "")),        # 면적
            "appraisal": to_int(raw.get("apslAmt")),            # 감정가 (원)
            "min_price": to_int(raw.get("minbAmt")),            # 최저가 (원)
            "min_price_pct": raw.get("minbPct"),                # 최저가/감정가 %
            "status": strip_html(raw.get("statNm", "")),        # 상태 (신건/유찰 N회)
            "bid_date": raw.get("bidDt", ""),                   # 매각기일 (YY.MM.DD)
            "special": strip_html(raw.get("splCdtn", "")),      # 특수조건
            "dpsl": strip_html(raw.get("dpsl", "")),            # 매각구분
        }

    def _listing_page(self, si_cd: str, gu_cd: str, page_no: int) -> dict:
        """목록 1페이지를 JSON으로 받는다. 200인데 JSON이 아니면(차단/세션만료)
        세션을 새로 받아 1회 더 시도하고, 그래도 안 되면 BadResponse."""
        headers = {"Referer": LIST_PAGE_URL, "X-Requested-With": "XMLHttpRequest"}
        for refresh in (False, True):
            if refresh:
                print("    ! JSON이 아닌 응답 — 세션 재발급 후 1회 재시도")
                self._new_session()
            r = self._get(LIST_API_URL, params=self._listing_params(si_cd, gu_cd, page_no),
                          headers=headers)
            try:
                data = r.json()
            except ValueError:
                continue
            total = to_int(data.get("totalCnt"))
            if total is None:   # totalCnt가 없거나 이상하면 응답 자체를 불신
                continue
            data["totalCnt"] = total
            return data
        raise BadResponse(f"목록 API가 올바른 JSON을 주지 않음 (차단 가능성): {r.text[:80]}")

    def fetch_listings(self, si_cd: str, gu_cd: str) -> list[dict]:
        """특정 시/군/구의 진행물건 전체를 (여러 페이지에 걸쳐) 읽는다."""
        items = []
        total = None
        for page_no in range(1, MAX_PAGES + 1):
            data = self._listing_page(si_cd, gu_cd, page_no)
            if total is None:
                total = data["totalCnt"]
                print(f"    사이트 표시 총 {total}건")
            page_items = data.get("item") or []
            items.extend(self._parse_listing_item(x) for x in page_items)
            print(f"    {page_no}페이지: {len(page_items)}건 (누적 {len(items)}/{total})")
            if len(items) >= total or not page_items:
                break
        if total is not None and len(items) < total:
            print(f"    ! 주의: 수집 {len(items)}건 < 사이트 표시 {total}건 (페이지 상한 {MAX_PAGES} 도달?)")
        return items


# ─────────────────────────────────────────────
# 수집 대상 달 계산 (KST 기준)
# ─────────────────────────────────────────────
def recent_months(n: int, today=None) -> list[tuple[int, int]]:
    """지난달부터 거슬러 n개월의 (연도, 월) 목록. 이번 달은 아직 집계 중이라 제외.
    반드시 한국시간 기준 — 실행 서버(GitHub Actions)는 UTC라서 그냥 today()를 쓰면
    매월 1일 새벽 실행 때 한 달이 밀린다."""
    today = today or now_kst().date()
    result = []
    y, m = today.year, today.month
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        result.append((y, m))
    return result


# ─────────────────────────────────────────────
# 지역 1곳 수집 (통계 + 물건) → 지역별 파일로 즉시 저장
# ─────────────────────────────────────────────
def region_file(si_cd: str, gu_cd: str) -> Path:
    return RAW_DIR / "regions" / f"{si_cd}-{gu_cd}.json"


def crawl_region(crawler: DooinCrawler, region: dict, months: list[tuple[int, int]]) -> dict:
    """지역 1곳의 통계+물건을 수집해 파일로 저장한다.
    이미 저장된 과거 달 통계는 다시 요청하지 않는다 (확정된 데이터라 불필요한 부하)."""
    si_cd, gu_cd, name = region["siCd"], region["guCd"], f"{region['sido']} {region['name']}"
    path = region_file(si_cd, gu_cd)

    # 기존 파일이 있으면 과거 달 통계를 재활용 (증분 수집)
    old_stats = {}
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            old_stats = {(s["year"], s["month"]): s for s in old.get("monthly_stats", [])}
        except Exception:
            pass   # 깨진 파일이면 무시하고 새로 수집

    monthly_stats = []
    for y, m in sorted(months):
        cached = old_stats.get((y, m))
        if cached and cached.get("rows"):
            monthly_stats.append(cached)
            continue
        stats = crawler.fetch_monthly_stats(si_cd, gu_cd, y, m, region_name=region["name"])
        monthly_stats.append(stats)

    listings = crawler.fetch_listings(si_cd, gu_cd)

    result = {
        "collected_at": now_kst().date().isoformat(),
        "region": {"siCd": si_cd, "guCd": gu_cd, "sido": region["sido"], "name": region["name"]},
        "monthly_stats": monthly_stats,
        "listings": listings,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


# ─────────────────────────────────────────────
# 전국 수집 (--full)
# ─────────────────────────────────────────────
def in_crawl_window(now=None) -> bool:
    """전국 실행이 허용되는 시간대(KST 새벽 2~8시)인지 확인.
    GitHub Actions의 cron은 몇 시간씩 밀릴 수 있어서 코드가 직접 확인해야 한다."""
    now = now or now_kst()
    return CRAWL_HOUR_BEGIN <= now.hour < CRAWL_HOUR_END


def run_full(months_back: int, force: bool):
    if not force and not in_crawl_window():
        print(f"지금은 KST {now_kst():%H:%M} — 전국 수집은 새벽 {CRAWL_HOUR_BEGIN}~{CRAWL_HOUR_END}시에만 실행합니다.")
        print("(서버 부하 규칙. 수동 테스트가 꼭 필요하면 --force)")
        sys.exit(0)

    crawler = DooinCrawler()   # 시간대 확인을 통과한 뒤에야 사이트에 접속한다
    regions = json.loads(REGIONS_PATH.read_text(encoding="utf-8"))["regions"]
    months = recent_months(months_back)
    print(f"===== 전국 수집: {len(regions)}개 지역, 통계 {months_back}개월 ({months[-1]}~{months[0]}) =====")

    ok, failed = [], []
    consecutive_fails = 0
    started = now_kst()

    for i, region in enumerate(regions, start=1):
        name = f"{region['sido']} {region['name']}"
        print(f"\n[{i}/{len(regions)}] {name}")
        try:
            result = crawl_region(crawler, region, months)
            ok.append(name)
            consecutive_fails = 0
            print(f"    저장 완료 — 통계 {len(result['monthly_stats'])}개월, 물건 {len(result['listings'])}건")
        except Exception as e:
            failed.append({"region": name, "error": f"{type(e).__name__}: {e}"})
            consecutive_fails += 1
            print(f"    ✖ 실패: {type(e).__name__}: {e}")
            if consecutive_fails >= CIRCUIT_BREAKER:
                # 연속 실패 = 사이트 차단/장애 가능성 → 서버를 더 괴롭히지 말고 즉시 중단
                print(f"\n연속 {CIRCUIT_BREAKER}개 지역 실패 — 차단/장애로 판단하고 전체 중단합니다.")
                break

    summary = {
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": now_kst().isoformat(timespec="seconds"),
        "ok": len(ok), "failed": len(failed),
        "failures": failed,
        "aborted_by_circuit_breaker": consecutive_fails >= CIRCUIT_BREAKER,
    }
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_DIR / "crawl_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n===== 전국 수집 요약 =====")
    print(f"  성공 {len(ok)}개 지역 / 실패 {len(failed)}개 지역")
    for f in failed[:10]:
        print(f"    - {f['region']}: {f['error'][:80]}")
    if summary["aborted_by_circuit_breaker"] or (failed and len(failed) > len(ok) * 0.1):
        sys.exit(1)   # 자동 실행(Actions)에서 '실패한 날'임을 알 수 있게


# ─────────────────────────────────────────────
# 소량 테스트 (--test): 서울 강동구 1곳
# ─────────────────────────────────────────────
def run_test():
    region = {"siCd": "11", "guCd": "740", "sido": "서울", "name": "강동구"}
    print("===== 소량 테스트: 서울 강동구 =====")
    crawler = DooinCrawler()

    months = recent_months(6)
    result = crawl_region(crawler, region, months)

    by_type = {}
    for it in result["listings"]:
        by_type[it["type"]] = by_type.get(it["type"], 0) + 1

    print("\n===== 테스트 결과 요약 =====")
    print(f"  월간통계 : {len(result['monthly_stats'])}개월치")
    for s in result["monthly_stats"]:
        apt = next((r for r in s["rows"] if r["type"] == "아파트"), None)
        line = (f"낙찰률 {apt['sold_rate']}%, 낙찰가율 {apt['sale_price_rate']}%"
                if apt else "아파트 데이터 없음")
        print(f"      {s['year']}.{s['month']:02d}: {line}")
    print(f"  진행물건 : 총 {len(result['listings'])}건")
    for t, cnt in sorted(by_type.items(), key=lambda x: -x[1])[:5]:
        print(f"      {t}: {cnt}건")
    print(f"  저장 위치: {region_file(region['siCd'], region['guCd'])}")


if __name__ == "__main__":
    if "--test" in sys.argv:
        run_test()
    elif "--full" in sys.argv:
        months_back = 1
        if "--months" in sys.argv:
            months_back = int(sys.argv[sys.argv.index("--months") + 1])
        run_full(months_back, force="--force" in sys.argv)
    else:
        print("사용법: python crawler.py --test           (서울 강동구 소량 테스트)")
        print("       python crawler.py --full [--months N] [--force]  (전국 수집, 새벽에만)")
