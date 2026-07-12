# -*- coding: utf-8 -*-
"""
지역 영문 슬러그(URL용 영어 이름) 생성기

regions.json의 각 지역에 다음을 추가한다:
  - sido_slug : 시/도 영문 (예: seoul, gyeonggi)
  - sido_ko   : 시/도 표시용 한글 (광주전남은 광주/전남으로 분리)
  - slug      : 시/군/구 영문 (예: gangdong, goyang-deogyang)

표기는 국어의 로마자 표기법(문화체육관광부 고시) 기준.
실행: python build_slugs.py
"""

import json
from pathlib import Path

REGIONS_PATH = Path(__file__).parent / "data" / "regions.json"

# 시/도 슬러그
SIDO_SLUG = {
    "서울": "seoul", "부산": "busan", "대구": "daegu", "인천": "incheon",
    "대전": "daejeon", "울산": "ulsan", "세종": "sejong", "경기": "gyeonggi",
    "강원": "gangwon", "충북": "chungbuk", "충남": "chungnam", "전북": "jeonbuk",
    "경북": "gyeongbuk", "경남": "gyeongnam", "제주": "jeju",
    # 광주전남(siCd=12)은 아래 GWANGJU_GU_CODES로 광주/전남을 구분한다
    "광주": "gwangju", "전남": "jeonnam",
}

# siCd=12에서 '광주광역시'에 속하는 구 코드 (나머지는 전남)
GWANGJU_GU_CODES = {"330", "270", "210", "300", "240"}  # 광산·남·동·북·서구

# 지역명 단어 → 로마자. '고양시 덕양구'는 단어별로 변환해 하이픈으로 잇는다.
# 행정 접미사(시/군/구)는 슬러그에서 뗀다 (강동구 → gangdong).
WORD_ROMAN = {
    # 서울
    "강남구": "gangnam", "강동구": "gangdong", "강북구": "gangbuk", "강서구": "gangseo",
    "관악구": "gwanak", "광진구": "gwangjin", "구로구": "guro", "금천구": "geumcheon",
    "노원구": "nowon", "도봉구": "dobong", "동대문구": "dongdaemun", "동작구": "dongjak",
    "마포구": "mapo", "서대문구": "seodaemun", "서초구": "seocho", "성동구": "seongdong",
    "성북구": "seongbuk", "송파구": "songpa", "양천구": "yangcheon", "영등포구": "yeongdeungpo",
    "용산구": "yongsan", "은평구": "eunpyeong", "종로구": "jongno", "중구": "jung",
    "중랑구": "jungnang",
    # 방위명 구 (여러 도시 공통)
    "남구": "nam", "동구": "dong", "서구": "seo", "북구": "buk",
    # 부산
    "금정구": "geumjeong", "기장군": "gijang", "동래구": "dongnae", "부산진구": "busanjin",
    "사상구": "sasang", "사하구": "saha", "수영구": "suyeong", "연제구": "yeonje",
    "영도구": "yeongdo", "해운대구": "haeundae",
    # 대구
    "군위군": "gunwi", "달서구": "dalseo", "달성군": "dalseong", "수성구": "suseong",
    # 인천
    "강화군": "ganghwa", "검단구": "geomdan", "계양구": "gyeyang", "남동구": "namdong",
    "미추홀구": "michuhol", "부평구": "bupyeong", "서해구": "seohae", "연수구": "yeonsu",
    "영종구": "yeongjong", "옹진군": "ongjin", "제물포구": "jemulpo",
    # 광주·전남
    "강진군": "gangjin", "고흥군": "goheung", "곡성군": "gokseong", "광산구": "gwangsan",
    "광양시": "gwangyang", "구례군": "gurye", "나주시": "naju", "담양군": "damyang",
    "목포시": "mokpo", "무안군": "muan", "보성군": "boseong", "순천시": "suncheon",
    "신안군": "sinan", "여수시": "yeosu", "영광군": "yeonggwang", "영암군": "yeongam",
    "완도군": "wando", "장성군": "jangseong", "장흥군": "jangheung", "진도군": "jindo",
    "함평군": "hampyeong", "해남군": "haenam", "화순군": "hwasun",
    # 대전
    "대덕구": "daedeok", "유성구": "yuseong",
    # 울산
    "울주군": "ulju",
    # 세종
    "세종시": "sejong",
    # 경기
    "가평군": "gapyeong", "고양시": "goyang", "덕양구": "deogyang",
    "일산동구": "ilsandong", "일산서구": "ilsanseo",
    "과천시": "gwacheon", "광명시": "gwangmyeong", "광주시": "gwangju", "구리시": "guri",
    "군포시": "gunpo", "김포시": "gimpo", "남양주시": "namyangju", "동두천시": "dongducheon",
    "부천시": "bucheon", "소사구": "sosa", "오정구": "ojeong", "원미구": "wonmi",
    "성남시": "seongnam", "분당구": "bundang", "수정구": "sujeong", "중원구": "jungwon",
    "수원시": "suwon", "권선구": "gwonseon", "영통구": "yeongtong", "장안구": "jangan",
    "팔달구": "paldal",
    "시흥시": "siheung", "안산시": "ansan", "단원구": "danwon", "상록구": "sangnok",
    "안성시": "anseong", "안양시": "anyang", "동안구": "dongan", "만안구": "manan",
    "양주시": "yangju", "양평군": "yangpyeong", "여주시": "yeoju", "연천군": "yeoncheon",
    "오산시": "osan", "용인시": "yongin", "기흥구": "giheung", "수지구": "suji",
    "처인구": "cheoin",
    "의왕시": "uiwang", "의정부시": "uijeongbu", "이천시": "icheon", "파주시": "paju",
    "평택시": "pyeongtaek", "포천시": "pocheon", "하남시": "hanam",
    "화성시": "hwaseong", "동탄구": "dongtan", "만세구": "manse", "병점구": "byeongjeom",
    "효행구": "hyohaeng",
    # 강원
    "강릉시": "gangneung", "고성군": "goseong", "동해시": "donghae", "삼척시": "samcheok",
    "속초시": "sokcho", "양구군": "yanggu", "양양군": "yangyang", "영월군": "yeongwol",
    "원주시": "wonju", "인제군": "inje", "정선군": "jeongseon", "철원군": "cheorwon",
    "춘천시": "chuncheon", "태백시": "taebaek", "평창군": "pyeongchang", "홍천군": "hongcheon",
    "화천군": "hwacheon", "횡성군": "hoengseong",
    # 충북
    "괴산군": "goesan", "단양군": "danyang", "보은군": "boeun", "영동군": "yeongdong",
    "옥천군": "okcheon", "음성군": "eumseong", "제천시": "jecheon", "증평군": "jeungpyeong",
    "진천군": "jincheon", "청주시": "cheongju", "상당구": "sangdang", "서원구": "seowon",
    "청원구": "cheongwon", "흥덕구": "heungdeok", "충주시": "chungju",
    # 충남
    "계룡시": "gyeryong", "공주시": "gongju", "금산군": "geumsan", "논산시": "nonsan",
    "당진시": "dangjin", "보령시": "boryeong", "부여군": "buyeo", "서산시": "seosan",
    "서천군": "seocheon", "아산시": "asan", "예산군": "yesan", "천안시": "cheonan",
    "동남구": "dongnam", "서북구": "seobuk", "청양군": "cheongyang", "태안군": "taean",
    "홍성군": "hongseong",
    # 전북
    "고창군": "gochang", "군산시": "gunsan", "김제시": "gimje", "남원시": "namwon",
    "무주군": "muju", "부안군": "buan", "순창군": "sunchang", "완주군": "wanju",
    "익산시": "iksan", "임실군": "imsil", "장수군": "jangsu", "전주시": "jeonju",
    "덕진구": "deokjin", "완산구": "wansan", "정읍시": "jeongeup", "진안군": "jinan",
    # 경북
    "경산시": "gyeongsan", "경주시": "gyeongju", "고령군": "goryeong", "구미시": "gumi",
    "김천시": "gimcheon", "문경시": "mungyeong", "봉화군": "bonghwa", "상주시": "sangju",
    "성주군": "seongju", "안동시": "andong", "영덕군": "yeongdeok", "영양군": "yeongyang",
    "영주시": "yeongju", "영천시": "yeongcheon", "예천군": "yecheon", "울릉군": "ulleung",
    "울진군": "uljin", "의성군": "uiseong", "청도군": "cheongdo", "청송군": "cheongsong",
    "칠곡군": "chilgok", "포항시": "pohang",
    # 경남
    "거제시": "geoje", "거창군": "geochang", "김해시": "gimhae", "남해군": "namhae",
    "밀양시": "miryang", "사천시": "sacheon", "산청군": "sancheong", "양산시": "yangsan",
    "의령군": "uiryeong", "진주시": "jinju", "창녕군": "changnyeong", "창원시": "changwon",
    "마산합포구": "masanhappo", "마산회원구": "masanhoewon", "성산구": "seongsan",
    "의창구": "uichang", "진해구": "jinhae", "통영시": "tongyeong", "하동군": "hadong",
    "함안군": "haman", "함양군": "hamyang", "합천군": "hapcheon",
    # 제주
    "서귀포시": "seogwipo", "제주시": "jeju",
}


def region_slug(name: str) -> str:
    """'고양시 덕양구' → 'goyang-deogyang', '강동구' → 'gangdong'"""
    parts = name.split()
    romans = []
    for w in parts:
        if w not in WORD_ROMAN:
            raise KeyError(f"로마자 표기가 없는 지역명: '{w}' (WORD_ROMAN에 추가 필요)")
        romans.append(WORD_ROMAN[w])
    return "-".join(romans)


def main():
    data = json.loads(REGIONS_PATH.read_text(encoding="utf-8"))

    seen = set()   # (sido_slug, slug) 중복 감지
    for r in data["regions"]:
        # 광주전남(12)은 구 코드로 광주/전남을 구분
        if r["siCd"] == "12":
            sido_ko = "광주" if r["guCd"] in GWANGJU_GU_CODES else "전남"
        else:
            sido_ko = r["sido"]
        r["sido_ko"] = sido_ko
        r["sido_slug"] = SIDO_SLUG[sido_ko]
        r["slug"] = region_slug(r["name"])

        key = (r["sido_slug"], r["slug"])
        if key in seen:
            raise ValueError(f"슬러그 중복: {key} ({r['name']})")
        seen.add(key)

    REGIONS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"완료 — {len(data['regions'])}개 지역에 슬러그 부여, 중복 없음")
    # 확인용 샘플 출력
    for r in data["regions"][:3] + [x for x in data["regions"] if x["siCd"] == "12"][:2]:
        print(f"  {r['sido_ko']} {r['name']} → /{r['sido_slug']}/{r['slug']}/")


if __name__ == "__main__":
    main()
