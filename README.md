# 두인경매 지역별 경매 통계 (dooin-stats-pages)

전국 시·군·구 × 물건유형별 법원경매 통계 페이지를 자동 생성하는 프로젝트입니다.
[두인경매](https://www.dooinauction.com)의 공식 부속 통계 서비스입니다.

## 무엇을 하나요

- 두인경매의 공개 매각통계·진행물건 정보를 주기적으로 수집합니다 (요청 간 딜레이 준수, 새벽 시간대만)
- 지역(269개) × 물건유형(아파트/빌라·다세대/상가/토지/단독주택) 조합별 통계 페이지를 생성합니다
- GitHub Actions로 수집→생성→배포가 자동으로 돌아갑니다 (GitHub Pages 호스팅)

## 구조

```
app/
├── crawler.py        수집기 (공개 정보만, 자동 딜레이 내장, 새벽 시간대 가드)
├── build_slugs.py    지역 영문 슬러그 생성
├── generator.py      통계 집계 + 페이지 생성 (Jinja2)
├── templates/        페이지 템플릿
├── data/
│   ├── regions.json  지역 코드·슬러그 매핑표
│   └── raw/regions/  지역별 수집 데이터 (JSON)
└── site/             생성된 정적 페이지 (배포 산출물, 커밋 안 함)
```

## 실행

```
pip install -r app/requirements.txt
python app/crawler.py --test        # 소량 테스트 (서울 강동구 1곳)
python app/crawler.py --full        # 전국 수집 (KST 새벽 2~8시에만 동작)
python app/generator.py             # 페이지 생성 → app/site/
```

## 데이터 정책

- 로그인 없이 볼 수 있는 공개 정보만 수집·게시합니다
- 개별 물건의 상세 권리분석 등은 [두인경매 본사이트](https://www.dooinauction.com)에서 제공합니다
- 문의: 두인경매 대표번호 1661-9910
