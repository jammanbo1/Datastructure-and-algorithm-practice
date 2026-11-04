# 집콕체크

전월세 계약 전에 확인할 질문을 단계별로 알려 주는 앱이에요. 첫 화면에서는 신촌·서강대 근처의 최근 전월세 실거래를 보여 줘요.

## 구조 (늘 켜 둔 서버 없이 돌아가요)

```
GitHub Actions (매일 새벽 3시)
  └─ scripts/fetch_rent.py ─▶ 국토교통부 실거래가 API
        └─ data/listings.json 저장·커밋
GitHub Pages
  └─ index.html 이 같은 주소의 data/listings.json 을 읽어 첫 화면에 보여 줘요
```

| 파일 | 하는 일 |
|---|---|
| `index.html` | 앱 화면 전체 |
| `data/listings.json` | 매일 갱신되는 거래 목록 (처음엔 비어 있어서 예시 데이터가 보여요) |
| `scripts/fetch_rent.py` | API를 불러 신촌 근처 동만 골라 저장 (파이썬 기본 기능만 사용) |
| `.github/workflows/fetch-rent.yml` | 위 스크립트를 매일 돌리고 결과를 커밋 |

## 처음 한 번 설정하기

1. **API 활용신청**: [공공데이터포털](https://www.data.go.kr)에서 아래를 검색해 각각 활용신청해요. 보통 바로 또는 1~2시간 안에 승인돼요.
   - 필수: 국토교통부 아파트 / 연립다세대 / 단독다가구 / 오피스텔 **전월세** 실거래가 자료
   - 선택 (전세가율 계산용): 아파트 / 연립다세대 / 오피스텔 **매매** 실거래가 자료
2. **저장소 만들기**: GitHub에서 새 저장소(예: `zipkok-check`)를 만들고 이 폴더의 파일을 모두 올려요. 무료 계정에서 Pages를 쓰려면 Public이어야 해요.
3. **인증키 넣기**: 저장소 → Settings → Secrets and variables → Actions → New repository secret
   - Name: `MOLIT_KEY`
   - Secret: 마이페이지의 **일반 인증키(Decoding)** (Encoding 키를 넣어도 돼요)
4. **Pages 켜기**: Settings → Pages → Source: `Deploy from a branch`, Branch: `main` / `/ (root)`
5. **한 번 돌려 보기**: Actions 탭 → "전월세 실거래 받기" → Run workflow.
   끝나면 `data/listings.json` 이 채워지고, `https://내아이디.github.io/zipkok-check/` 에서 실거래가 보여요.

컴퓨터에서 `index.html` 을 바로 열 때도 실제 거래를 보려면, `index.html` 의 `LISTING_REMOTE` 에 Pages 주소의 `data/listings.json` 을 넣어 주세요.

## 직접 돌려 보기

```bash
MOLIT_KEY=발급받은_키 python scripts/fetch_rent.py
```

첫 실행 뒤 `data/listings.json` 을 열어 면적·층 값이 맞는지 한 번 확인해 주세요. API 응답 태그 이름은 영문(2024년~)과 예전 한글 이름을 모두 읽게 해 뒀어요. 그래도 칸이 비면 응답 XML을 저장해 아래처럼 확인할 수 있어요.

```bash
python scripts/fetch_rent.py --fixture 응답.xml --type sh --lawd 11410
```

## 알아 둘 점

- 실거래는 **이미 계약된 거래**라 지금 나와 있는 매물이 아니에요. 화면에도 그렇게 적혀 있어요.
- 단독·다가구는 개인정보 때문에 지번이 공개되지 않아서 동까지만 보여요.
- 공개 저장소에서 60일 동안 활동이 없으면 GitHub가 예약 실행을 멈출 수 있어요. 멈췄다는 메일이 오면 Actions 탭에서 다시 켜 주세요.
- "들은 말로 빠르게 채우기"의 AI 분석(`127.0.0.1:8000/extract-info`)은 GitHub Pages에서 돌릴 수 없어서 따로 서버가 필요해요.
- 출처: 국토교통부 실거래가 공개시스템
