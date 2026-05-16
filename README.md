# trip_optimizer

홍콩(+ 마카오) 여행 최적화 스크립트. 외부 LLM 없이 SerpAPI(Google Flights/Hotels) + Python 표준 라이브러리로 동작.

## 빠른 실행 (모의 데이터)

```bash
pip install pyyaml
python -m trip_optimizer.main --config trip_optimizer/config.yaml --out results
```

→ `results/results.md` (랭킹 리포트) + `results/results.csv`. 별도 설정 없이 즉시 동작.

## 실제 가격으로 전환 (SerpAPI)

### 1. API 키 발급 (무료)
1. https://serpapi.com 가입 (카드 등록 불필요)
2. Dashboard → **Your Private API Key** 복사
3. 무료 등급: 월 100 검색까지

### 2. 키 설정
`.env.example`을 `.env`로 복사 후 키 값만 채우기:
```
SERPAPI_KEY=발급받은_키
```
(`.env`는 `.gitignore`에 들어있어 절대 커밋되지 않음)

### 3. config.yaml에서 adapter 변경
```yaml
adapter: serpapi    # mock → serpapi
```

### 4. 실행
```bash
python -m trip_optimizer.main --config trip_optimizer/config.yaml --out results
```

## 마카오 시나리오

`config.yaml`의 `macau:` 블록으로 켜고 끔:
- **day_trip** — N박 모두 HK + 마카오 당일치기 (페리 왕복)
- **overnight_rt** — (N-1)박 HK + 1박 마카오, HKG 왕복 항공권 + 페리 왕복
- **overnight_mfm_out** — (N-1)박 HK + 1박 마카오, ICN→HKG 입국 / MFM→ICN 귀국, 페리 편도

옵티마이저는 활성화된 시나리오를 모두 후보로 만들어 점수로 비교합니다. 가격이 안 맞으면 자동으로 day_trip이 위로 올라옴.

## 구조

```
trip_optimizer/
├── config.yaml             # 사용자 편집
├── main.py                 # CLI
├── models.py               # 데이터 클래스
├── optimizer.py            # 일자 생성 · 시나리오 enum · 필터 · 점수 · 랭킹
├── report.py               # Markdown/CSV 출력
├── cache.py                # SQLite 캐시 (SerpAPI 응답)
├── fx.py                   # 환율 (Frankfurter API, 키 불필요)
└── adapters/
    ├── base.py             # 추상 인터페이스
    ├── mock.py             # 모의 데이터 (즉시 실행용)
    ├── serpapi.py          # ★ SerpAPI 실제 데이터
    └── playwright_skel.py  # 스크래핑 대안 (직접 구현 필요)
```

## 한계와 주의

- SerpAPI 무료 등급은 월 100 검색. 한 번 실행에 ~30~80 검색 소비. `cache.py`가 12시간 TTL로 같은 일자 재호출을 막아주지만, 일자 범위를 너무 넓히면 한도 초과.
- 4인 동일 항공편 좌석 확보는 실제 예약 단계에서 재확인.
- Google Hotels는 일부 부티크/소규모 호텔이 빠질 수 있음.
