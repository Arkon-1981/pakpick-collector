# current_data 병합 저장 설계 (A안)

## 문제 (근본 원인)

`repository.upsert_store_item` 은 저장할 때마다 `store_items.current_data`(JSON 한 덩어리)를
**통째로 교체**한다. 그런데 이 안에는 서로 다른 경로가 채운 값이 섞여 있다:

| 값 | 채우는 경로 |
|---|---|
| gallery(스크린샷) | 상세 보강 (steam GetItems / nintendo 상세 / PS는 목록) |
| content_kind (new/upcoming/free) | 일정·신작·무료 목록 크롤 |
| popular_rank | 인기 목록 크롤 |
| release_date, publisher, genres, content_type … | 메타 보강 (PS metGetProductById / steam GetItems / xbox catalog) |
| platform_generation | 닌텐도 SW2 필터 |
| subscription | xbox Affirmations / PS 브랜딩 / 닌텐도 NSO |

**어떤 경로가 저장하면 그 경로가 안 챙긴 값은 전부 사라진다.** 그래서 "필드 유실" 버그가
플랫폼×경로 조합마다 반복됐다. 지금은 경로마다 `fetch_item_meta` 로 직전 값을 다시 읽어
재부착하는 방식(수동 땜질)이라, 새 경로가 생기면 또 터진다.

## 목표

병합(merge) 저장을 `upsert_store_item` **한 곳**에 넣어, 경로가 무엇을 저장하든 다른 경로가
채운 값이 자동으로 유지되게 한다. 경로별 보존 코드(steam `_enrich` 복구, nintendo
`KEEP_KEYS`, PS `_keep_meta`, xbox `_prev_kinds`)는 이후 제거한다.

## 핵심 함정: 단순 `{**old, **new}` 는 안 된다

파서가 **갤러리를 항상 `[header]` 1장으로 내보낸다.** 즉 gallery 는 `new` 에 "있지만 빈약한"
상태다. 단순 병합(new 우선)은 old 의 풍부한 갤러리(스샷 6장)를 1장으로 **덮어쓴다** — 지금
버그 그대로다. 그래서 **키별 병합 정책**이 필요하다.

## 병합 정책 (키를 3분류)

### ① 보강-스칼라 (fill-if-empty): new 가 truthy 면 new, 아니면 old
파서가 값을 못 채웠거나 이 경로가 안 건드리면 직전 값을 유지.
```
release_date, publisher, developer, content_type, content_rating,
short_description, top_category, store_classification, platform_generation,
review, korean, is_f2p, gold_required
```

### ② 보강-배열: gallery 는 '더 긴 쪽', 나머지는 fill-if-empty
```
gallery            → len(old) > len(new) 면 old 유지 (스샷 보존)
genres, players, platforms, publishers, developers, franchises → fill-if-empty
```

### ③ 상태-스티키 (preserve-if-key-absent): 키가 new 에 아예 없으면 old 유지
그 값의 '주인 경로'만 명시적으로 설정/삭제한다. 다른 경로는 건드리지 않는다.
```
content_kind, popular_rank, subscription
```
- **삭제는 명시적으로**: 인기 목록에서 빠진 상품의 popular_rank 는 별도 sweep 이
  `popular_rank=None` 을 **명시적으로 실어** 저장한다(키가 new 에 존재 → new(None) 우선 →
  삭제). 즉 "부재=유지, 명시적 None=삭제". xbox 의 '페이지 실패 vs 정상 이탈' 구분 로직은
  이 sweep 쪽에 남는다.

### 그 외 (항상-신선)
`price_raw` 등 파서가 매번 내보내는 값은 자연히 new 로 덮인다(정책 불필요).

## 구현 스케치

```python
# repository.py
_MERGE_SCALAR = {release_date, publisher, developer, content_type, content_rating,
                 short_description, top_category, store_classification,
                 platform_generation, review, korean, is_f2p, gold_required}
_MERGE_FILL_ARRAY = {genres, players, platforms, publishers, developers, franchises}
_MERGE_STICKY = {content_kind, popular_rank, subscription}

def merge_current_data(old: dict | None, new: dict) -> dict:
    if not old:
        return new
    out = dict(new)                                  # 기준 = 새 값(price_raw 등 포함)
    for k in _MERGE_SCALAR:
        if not out.get(k) and old.get(k) is not None:
            out[k] = old[k]
    for k in _MERGE_FILL_ARRAY:
        if not out.get(k) and old.get(k):
            out[k] = old[k]
    if len(old.get("gallery") or []) > len(out.get("gallery") or []):
        out["gallery"] = old["gallery"]              # 스샷 보존(더 긴 쪽)
    for k in _MERGE_STICKY:
        if k not in new and old.get(k) is not None:  # 부재=유지 / 명시적=반영
            out[k] = old[k]
    return out
```

### old 를 어떻게 얻나 (N+1 재발 방지)

방금 N+1 을 없앤 upsert 를 되돌리면 안 된다. old 는 **실행 시작 시 한 번 프리페치**한다:

```python
# collectors/base.py — 플랫폼 수집 시작 시 1회 (paged)
self._prev_data = repository.fetch_item_meta(
    platform, region, MERGE_KEYS)   # 필요한 키만 → 가볍다(이미 쓰던 함수)
# save_item → upsert_store_item(..., prev=self._prev_data.get(store_product_id))
```
- `fetch_item_meta` 는 지금도 경로별 보존이 부르던 함수. 경로마다 부르던 걸 **시작 시 1회**로
  합치므로 요청은 오히려 준다.
- upsert 는 그대로(id 반환·레이스 안전). 병합만 파이썬에서 먼저 하고 upsert 에 넘긴다.
- 신규 상품(prev 없음)은 new 그대로 저장.

### 대안 — DB 측 병합(RPC)은 채택 안 함
`current_data || excluded.current_data`(JSONB shallow merge)는 gallery '더 긴 쪽',
스티키 '명시적 삭제'를 표현 못 한다. 파이썬 병합이 정책을 담기 쉬워 A안으로 간다.

## 롤아웃 (안전 순서)

1. `merge_current_data` + 시작 프리페치 추가, `upsert_store_item(prev=...)` 로 병합.
   경로별 보존 코드는 **일단 그대로 둔다**(이중 보존이라 무해).
2. xbox(작음) 브랜치 실수집으로 검증: 갤러리/kind/rank/메타가 유지되는지, 오류 0.
3. PS·닌텐도·스팀 각각 1회 검증.
4. 검증되면 경로별 보존 코드(steam `_enrich` 복구 블록, nintendo `KEEP_KEYS`,
   PS `_keep_meta`, xbox `_prev_kinds`)를 **제거**(중복 삭제).
5. popular_rank 삭제 sweep 이 명시적 None 을 싣는지 확인(현행 유지).

## 검증 체크리스트

- [ ] 갤러리 6장 상품을 갤러리 없는 경로로 저장 → 6장 유지
- [ ] 신작(content_kind=new) 상품을 할인 경로로 저장 → new 유지
- [ ] 인기에서 빠진 상품 → sweep 이 popular_rank 삭제(스티키가 방해 안 함)
- [ ] 메타(출시일·퍼블리셔) 있는 상품을 할인 경로로 저장 → 유지
- [ ] 신규 상품(prev 없음) → new 그대로, 오류 없음
- [ ] 할인 종료 → price_raw·최상위 가격 컬럼 정상 갱신(병합 무관)

## 예상 효과

- 필드 유실 버그 **구조적 종결**(경로가 늘어도 자동 보존).
- 경로별 보존 코드 4곳 제거 → 유지보수 부담 감소.
- 요청 수: 시작 프리페치 1회 추가(경로별 호출 대체) — 순증 없음.
