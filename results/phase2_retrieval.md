# 2단계 검색 비교(Day 10)

실행: `python -m scripts.eval_retrieval --out results/phase2_retrieval.csv` (2026-11-14, 로컬 Docker Desktop)

- 문서 19개: 지식 문서 7개와 평가용 방해 문서 12개(`eval/retrieval/distractors`, 운영 지식 베이스에는 넣지 않는다)
- 질의 10개: 에러 코드, 식별자가 들어간 질의(`eval/retrieval/queries.yaml`). 결과를 보기 전에 정했고, 본 뒤에 고치지 않았다
- 임베딩: 운영과 같은 `paraphrase-multilingual-MiniLM-L12-v2`(384차원). 메타데이터 필터 없이 순위만 비교
- 지연: 질의 10개 x 10회 = 100회, 질의 임베딩 시간 제외

| 방식 | 상위 3개 적중 | 지연 p50 | 지연 p95 |
| --- | --- | --- | --- |
| Qdrant 벡터(1단계) | 8/10 | 1.42ms | 2.11ms |
| pgvector 벡터 | 8/10 | 0.39ms | 0.48ms |
| pgvector + pg_trgm(RRF) | 9/10 | 1.09ms | 1.46ms |

## 해석

- 저장소만 바꾼 효과(Qdrant 벡터 → pgvector 벡터): 10개 질의 모두 순위가 같다. 같은 벡터로 문서 19개를 찾으므로 근사 검색의 차이가 드러나지 않는 규모다.
- 키워드 순위를 더한 효과: `TAX_RATE`가 9위에서 2위로 올라 적중이 1건 늘었다. 식별자가 본문에 그대로 있고 문장 뜻으로는 가깝지 않은 경우다. 반대로 `checkout average_unit_price ZeroDivisionError`는 1위에서 2위로 내려갔다(적중은 유지).
- 세 방식 모두 놓친 질의: `ZeroDivisionError: float division by zero on POST /orders/summary`. 방해 문서(search 서비스 포스트모템)에 같은 에러 문구가 그대로 있고, 정답 문서(shopapi 주문 요약 런북)에는 `ZeroDivisionError`라는 단어가 없다. 운영 경로에서는 서비스 필터(shopapi + common)가 먼저 걸려 이 방해 문서는 후보에서 빠진다.
- 지연(추정): 문서 수가 적어 인덱스보다 접속 방식이 지연을 정한다. Qdrant는 HTTP와 JSON, PostgreSQL은 커넥션 풀과 바이너리 프로토콜이다. 하이브리드는 키워드 순위 계산(`word_similarity`를 필터된 전체 행에 계산)만큼 벡터 단독보다 느리다.
- 표본이 10개라 1건 차이를 일반화하지 않는다. 문서가 늘면 키워드 순위 쿼리가 GIN 인덱스를 타지 않는 점(연산자 대신 함수 정렬)을 다시 본다.

## 상위 3개(질의별)

`results/phase2_retrieval.csv`의 `rank`는 첫 정답 문서의 순위(10위 밖이면 빈칸), `top3`는 청크 단위 상위 3개 문서 ID다. 같은 문서의 청크가 두 번 나올 수 있다.
