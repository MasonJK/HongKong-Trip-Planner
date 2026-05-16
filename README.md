# trip_optimizer

홍콩 여행 최적화 스크립트. **외부 LLM 없이** 무료 등급 Amadeus API + Python 표준 라이브러리로 동작.

## 빠른 실행 (모의 데이터)

```bash
pip install pyyaml
python -m trip_optimizer.main --config trip_optimizer/config.yaml --out results
```

→ `results/results.md` (랭킹 리포트) + `results/results.csv`. 별도 설정 없이 즉시 동작.

## 실제 가격으로 전환 (Amadeus)

### 1. API 키 발급 (무료)

1. https://developers.amadeus.com 가입 (신용카드 불필요)
2. My Self-Service Workspace → **Create New App**
3. API Key, API Secret 복사

### 2. 환경변수 설정

```bash
export AMADEUS_API_KEY=발급받은_키
export AMADEUS_API_SECRET=발급받은_시크릿
export AMADEUS_ENV=test         # 무료, 데이터 일부 제한
# export AMADEUS_ENV=production # 신청 후 승인되면 월 2000건 무료
```

`.env` 파일을 쓰려면 `.env.example` 참고.

### 3. config.yaml에서 adapter 변경

```yaml
adapter: amadeus    # mock → amadeus
```

### 4. 실행

```bash
python -m trip_optimizer.main --config trip_optimizer/config.yaml --out results
```

## test vs production

| 구분 | 무료 | 데이터 |
|---|---|---|
| `test` | ✓ 가입 즉시 | 일부 노선·일자 비어있을 수 있음. 호텔은 더 제한적. |
| `production` | ✓ 월 2000건까지 | 실제 운영 데이터. 신청 후 며칠 승인 대기. |

개인 여행 계획 정도라면 `test`에서 시작, 데이터 부족할 때만 production 신청.

## 구조

```
trip_optimizer/
├── config.yaml             # 사용자 편집
├── main.py                 # CLI
├── models.py               # 데이터 클래스
├── optimizer.py            # 일자 생성 · 필터 · 점수 · 랭킹
├── report.py               # Markdown/CSV 출력
├── cache.py                # SQLite 캐시
├── fx.py                   # 환율 (Frankfurter API, 키 불필요)
└── adapters/
    ├── base.py             # 추상 인터페이스
    ├── mock.py             # 모의 데이터 (즉시 실행용)
    ├── amadeus.py          # ★ Amadeus 실제 데이터
    └── playwright_skel.py  # 스크래핑 대안 (직접 구현 필요)
```

## 한계와 주의

- Amadeus가 모든 LCC(진에어 등)를 항상 노출하지는 않습니다. 결과가 빈약하면 일자 범위를 넓히거나 `max_stops`를 1로.
- 호텔 리뷰 점수는 Amadeus가 제공하지 않아 임시값 8.0을 씁니다 — 가중치를 낮추거나 별도 보강 권장.
- 4인 동일 항공편 좌석 동시 확보 여부는 실제 예약 단계에서 재확인.
- API 호출 한도가 있으니 같은 일자를 반복 조회하지 않도록 주의.
