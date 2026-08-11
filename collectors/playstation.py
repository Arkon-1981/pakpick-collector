"""플레이스테이션 한국 스토어 수집기.

대상: https://store.playstation.com/ko-kr/pages/deals  (할인 프로모션 모음)

PS 스토어 웹은 Next.js 기반이라, 페이지 HTML 안의
<script id="__NEXT_DATA__"> 태그에 상품 데이터가 JSON으로 통째로 들어 있다.
HTML 셀렉터 파싱보다 이 JSON을 읽는 것이 훨씬 안정적이다.

동작:
  1. /pages/deals 페이지 요청 → 원본 저장 → JSON에서 상품 추출
  2. deals 페이지에 연결된 카테고리(프로모션) 목록 URL 수집
  3. 각 카테고리의 페이지들을 순서대로 요청 → 원본 저장 → 상품 추출
"""
import json
import re
import time
from urllib.parse import quote

from collectors.base import BaseCollector
from common import config
from common.http_client import fetch
from common.logging_util import get_logger
from db import repository
from parsers.playstation import (
    extract_next_data,
    graphql_total_count,
    parse_cta_price,
    parse_product_meta,
    parse_products_from_graphql,
    parse_concepts_from_next_data,
    parse_detail_end_time,
    parse_products_from_next_data,
)

logger = get_logger(__name__)

BASE = "https://store.playstation.com"
DEALS_URL = f"{BASE}/ko-kr/pages/deals"

# deals 페이지 안의 카테고리(프로모션) 링크 형식
CATEGORY_URL_RE = re.compile(r"/ko-kr/category/([0-9a-f-]{36})")
MAX_CATEGORY_PAGES = 100  # 카테고리당 최대 페이지 수 (안전장치)
MAX_CATEGORIES = 30       # 한 번에 수집할 최대 프로모션 수

# 신작·출시예정 카테고리 (할인과 같은 __NEXT_DATA__ 구조라 기존 파서를 그대로 쓴다).
# 규모가 작아(수십 개) 할인 크롤보다 **먼저** 수집한다 — 할인 크롤이 시간예산을
# 다 쓰면 뒤에 둔 단계는 영영 실행되지 않기 때문.
RELEASE_CATEGORIES = [
    ("new", "e1699f77-77e1-43ca-a296-26d08abacb0f"),       # 신규 발매
    ("upcoming", "3bf499d7-7acf-4931-97dd-2667494ee2c9"),  # 출시 예정
    ("free", "4dfd67ab-4ed7-40b0-a937-a549aece13d0"),      # 무료 게임
]
RELEASE_MAX_PAGES = 3  # 카테고리당 최대 페이지 (페이지당 ~24개)
# 단품 오퍼레이션으로 보강하는 필드 — 다음 실행에서 그대로 되살릴 값들
META_KEYS = ("release_date", "publisher", "genres", "content_rating",
             "short_description", "players", "platforms",
             "content_type", "top_category", "store_classification")

# GraphQL 카테고리 조회 — HTML(24개/요청) 대비 100개/요청이라 요청 수가 1/4.
# 해시는 외부에서 얻어 직접 호출로 검증했지만(한 카테고리 5,906건 확인) 소니가
# 스키마를 바꾸면 무효가 될 수 있어, 실패 시 기존 HTML 경로로 폴백한다.
GQL_URL = "https://web.np.playstation.com/api/graphql/v1/op"
# '국내 Top 10' 카테고리 — /pages/latest 의 "Top 10 Games in your Country" 스트랜드가
# 가리키는 categoryId (탐사 실측). 항목이 concepts(순위 순)로 오는 특수 카테고리다.
POPULAR_CATEGORY = "fbb563aa-c602-476d-bb92-fe7f35080205"
GQL_HASH = "9845afc0dbaab4965f6563fffc703f588c8e76792000e8610843b8d3ee9c4c09"
GQL_PAGE_SIZE = 100
GQL_MAX_PAGES = 60  # 카테고리당 최대 6,000개 (무한 루프 방지)

# 단품 조회 오퍼레이션 — 상세 HTML(1건 400KB) 대신 쓴다.
# 종료일만 필요할 땐 CTA 쪽이 2.7KB라 약 150배 가볍다(실측). 둘 다 직접 호출 검증함.
GQL_CTA_OP = "productRetrieveForCtasWithPrice"
GQL_CTA_HASH = "8532da7eda369efdad054ca8f885394a2d0c22d03c5259a422ae2bb3b98c5c99"
GQL_META_OP = "metGetProductById"
GQL_META_HASH = "a128042177bd93dd831164103d53b73ef790d56f51dae647064cb8f9d9fc9d1a"


class PlaystationCollector(BaseCollector):
    platform = "playstation"

    # 출시예정작은 가격이 없는 것이 정상이고(is_on_sale=False 로 저장된다),
    # Concept 경로로만 들어온 상품은 이미지가 비는 경우가 있다.
    FIELD_FLOORS = {"title": 0.95, "image_url": 0.70, "final_price": 0.80}

    # collect()에서 실제 값으로 설정된다 (단위 테스트/부분 호출 시의 기본값)
    _gql_ok = _cta_ok = _meta_ok = True
    _job_deadline = float("inf")
    _meta_fetched = 0   # 실행 전체에서 새로 조회한 메타 건수 (상한 기준)

    def collect(self) -> None:
        self._gql_ok = True   # 카테고리 그리드 GraphQL (실패 시 HTML 폴백)
        self._cta_ok = True   # 종료일 CTA 오퍼레이션 (실패 시 상세 HTML 폴백)
        self._meta_ok = True  # 출시일·퍼블리셔 오퍼레이션 (실패 시 보강 생략)
        self._meta_fetched = 0
        seen_ids: set[str] = set()

        # 할인 경로 저장은 node+가격만 실어 current_data 를 덮으므로, 신작/무료 때
        # 보강해 둔 출시일·퍼블리셔·장르·DLC판별(content_type)이 지워지고 재보강
        # 경로가 없다. 시작 시 직전 값을 한 번 받아 저장 직전 되살린다(_keep_meta).
        try:
            self._prev_meta = repository.fetch_item_meta(
                self.platform, config.STORE_REGION, list(META_KEYS)
            )
        except Exception:
            logger.exception("[playstation] 기존 메타 조회 실패 — 보존 없이 진행")
            self._prev_meta = {}
        # 이미 '저장된' 상품들(종료일 보강 후보). 저장은 페이지 단위로 즉시 하고,
        # 종료일 보강은 목록 크롤이 끝난 뒤 할인율 상위 N개만 상세로 덧입힌다.
        saved: list = []

        # 목록 크롤 시간예산. 이 시각을 넘기면 남은 카테고리/페이지를 건너뛰고
        # 종료일 보강 단계로 넘어간다(잡 타임아웃 전에 보강이 반드시 실행되도록).
        crawl_deadline = time.monotonic() + config.PS_CRAWL_BUDGET_SECONDS
        # 보강 단계까지 포함한 잡 전체 상한 — 넘기면 남은 보강을 접고 정상 종료한다
        # (Actions 잡 타임아웃에 걸려 실행이 통째로 취소되는 것보다 낫다)
        self._job_deadline = time.monotonic() + config.PS_TOTAL_BUDGET_SECONDS

        # 워밍업: 사람처럼 첫 화면부터 방문 (쿠키 획득 → 차단 확률 감소)
        try:
            fetch(f"{BASE}/ko-kr")
        except Exception:
            pass  # 워밍업 실패는 무시하고 진행

        # 0. 신작·출시예정 (소량) — 할인 크롤이 시간예산을 다 써도 반드시 수집되도록 먼저
        self._collect_releases(seen_ids)

        # 1. deals 허브 페이지
        result = fetch(DEALS_URL)
        if result.status_code != 200:
            self.record_parse_error(DEALS_URL, f"deals 페이지 상태코드 {result.status_code}")
            return

        raw_doc_id = self.save_raw(
            result, document_type="list", filename="deals-hub.html",
            content_type="text/html",
        )
        self.pages_found += 1

        next_data = extract_next_data(result.text)
        if next_data:
            for item in parse_products_from_next_data(next_data):
                if item.store_product_id not in seen_ids:
                    seen_ids.add(item.store_product_id)
                    self._keep_meta(item)   # 직전 보강값 보존
                    self.save_item(item, raw_doc_id)  # 즉시 저장(부분 실패해도 데이터 보존)
                    saved.append(item)
        else:
            self.record_parse_error(DEALS_URL, "__NEXT_DATA__를 찾지 못함")

        # 2. 카테고리(프로모션) 링크 수집
        category_ids = list(dict.fromkeys(CATEGORY_URL_RE.findall(result.text)))
        logger.info("[playstation] 프로모션 카테고리 %d개 발견", len(category_ids))
        if not category_ids:
            # 링크가 0개면 할인 수집 전체가 조용히 건너뛰어진다(허브 마크업 변경·차단)
            self.record_parse_error(DEALS_URL, "프로모션 카테고리 링크 0개 (차단/마크업 의심)")

        # 3. 각 카테고리를 페이지 단위로 순회 (페이지마다 즉시 저장)
        #    시간예산이 짧으면 앞쪽 카테고리만 반복해서 훑고 뒤쪽은 영영 못 보게 된다
        #    (→ 뒤쪽 상품은 last_seen_at 이 안 갱신돼 웹에서 사라짐).
        #    실행마다 시작 위치를 회전시켜 여러 번에 걸쳐 전체를 covering 한다.
        #    12시간 단위로 회전 = 하루 2회 실행에서 매번 다른 지점부터 시작.
        targets = category_ids[:MAX_CATEGORIES]
        if targets:
            start = int(time.time() // (12 * 3600)) % len(targets)
            targets = targets[start:] + targets[:start]
            logger.info("[playstation] 카테고리 %d개 중 %d번째부터 순회 (회전)", len(targets), start)
        for category_id in targets:
            if time.monotonic() >= crawl_deadline:
                logger.info(
                    "[playstation] 크롤 시간예산(%d분) 소진 — 남은 카테고리 건너뛰고 종료일 보강으로",
                    config.PS_CRAWL_BUDGET_SECONDS // 60,
                )
                break
            if self._gql_ok:
                if self._collect_category_gql(category_id, seen_ids, saved, crawl_deadline):
                    continue
                # 한 번 실패하면 이 실행 내내 검증된 HTML 경로를 쓴다
                self._gql_ok = False
                logger.warning('[playstation] GraphQL 사용 불가 — HTML 경로로 전환')
            self._collect_category(category_id, seen_ids, saved, crawl_deadline)

        # 4. 할인 종료일 보강 — 이미 저장된 상품의 최신 스냅샷에 in-place 갱신.
        #    크롤이 시간예산으로 조기 종료되어도 이 단계는 반드시 실행된다.
        self._enrich_end_dates(saved)

        # 5. 인기(국내 Top 10) — 요청 몇 번이라 시간예산과 무관하게 마지막에 붙인다.
        self._collect_popular()

    def _collect_popular(self) -> None:
        """스토어 '국내 Top 10' 카테고리에서 인기 순위를 수집한다.

        다른 플랫폼과 달리 재저장(save_item)하지 않는다 — PS 는 current_data 에
        원본 노드를 통째로 보관하므로, 이 단계의 부분 정보로 upsert 하면 기존
        정보가 통째로 지워진다. 기존 행의 current_data 에 popular_rank 만 병합하고,
        순위에서 빠진 행은 키를 걷어낸다.

        주의: 이 카테고리는 variables 에 sortBy 를 넣으면(널이라도) products 로
        모드가 바뀌어 빈 목록이 온다(실측). sortBy 없이 불러야 '순위 순 concepts'다.
        """
        variables = json.dumps({
            "id": POPULAR_CATEGORY,
            "pageArgs": {"size": 20, "offset": 0},
            "filterBy": [], "facetOptions": [],
        }, separators=(",", ":"))
        extensions = json.dumps({
            "persistedQuery": {"version": 1, "sha256Hash": GQL_HASH}
        }, separators=(",", ":"))
        url = (
            f"{GQL_URL}?operationName=categoryGridRetrieve"
            f"&variables={quote(variables)}&extensions={quote(extensions)}"
        )
        try:
            result = fetch(url, api=True, extra_headers={
                "content-type": "application/json",
                "x-psn-store-locale-override": "ko-kr",
            })
            if result.status_code != 200:
                self.record_parse_error(url, f"인기 카테고리 상태코드 {result.status_code}")
                return
            data = json.loads(result.text)
            concepts = (data.get("data") or {}).get("categoryGridRetrieve", {}).get("concepts") or []
        except Exception:
            logger.exception("[playstation] 인기 카테고리 조회 실패")
            return
        if not concepts:
            logger.warning("[playstation] 인기 카테고리가 비어 있음 — 응답 구조 변경 가능성")
            return

        self.save_raw(
            result, document_type="list", filename="popular-top10.json",
            content_type="application/json",
        )
        self.pages_found += 1

        # 순위 → 상품ID (concept 의 첫 product 가 대표 판본)
        ranked: list[tuple[int, str, str]] = []
        for rank, c in enumerate(concepts, start=1):
            products = c.get("products") or []
            pid = products[0].get("id") if products else None
            if pid:
                ranked.append((rank, pid, c.get("name") or ""))

        try:
            rows = repository.fetch_items_by_product_ids(
                self.platform, config.STORE_REGION, [p for _, p, _ in ranked]
            )
            # 지난 순위 정리 — 이번 목록에 없는 행에서 popular_rank 를 걷어낸다
            stale = repository.fetch_item_meta(
                self.platform, config.STORE_REGION, ["popular_rank"]
            )
        except Exception:
            logger.exception("[playstation] 인기 순위 저장 준비 실패")
            return

        updated = missing = 0
        current_pids = {p for _, p, _ in ranked}
        for rank, pid, name in ranked:
            row = rows.get(pid)
            if row is None:
                missing += 1   # 아직 카탈로그에 없는 상품 — 다음 크롤이 발견하면 합류
                logger.info("[playstation] 인기 #%d %s 는 미보유 상품 (%s)", rank, name[:24], pid)
                continue
            cur = dict(row["current_data"])
            cur["popular_rank"] = rank
            try:
                # 스토어 인기 목록에 있다 = 지금 팔리고 있다 → 신선도도 함께 갱신
                repository.update_current_data(row["id"], cur, touch=True)
                self.items_seen.add(row["id"])
                updated += 1
            except Exception:
                logger.exception("[playstation] 인기 순위 저장 실패: %s", pid)

        cleared = 0
        stale_pids = [
            pid for pid, meta in stale.items()
            if meta.get("popular_rank") is not None and pid not in current_pids
        ]
        if stale_pids:
            try:
                old_rows = repository.fetch_items_by_product_ids(
                    self.platform, config.STORE_REGION, stale_pids
                )
            except Exception:
                logger.exception("[playstation] 지난 순위 조회 실패")
                old_rows = {}
            for pid, old in old_rows.items():
                try:
                    cur = dict(old["current_data"])
                    cur.pop("popular_rank", None)
                    repository.update_current_data(old["id"], cur)
                    cleared += 1
                except Exception:
                    logger.exception("[playstation] 지난 순위 정리 실패: %s", pid)

        logger.info("[playstation] 인기 Top10 — 순위 %d개 저장, 미보유 %d개, 지난 순위 %d개 정리",
                    updated, missing, cleared)

    def _collect_releases(self, seen_ids: set[str]) -> None:
        """신작·출시예정 카테고리를 수집한다 (할인과 동일한 __NEXT_DATA__ 구조).

        출시예정작은 아직 가격이 없거나 정가만 있어 is_on_sale=False 로 저장되므로
        할인 목록에는 섞이지 않는다. content_kind 로 종류를 표시한다.
        실패해도 이후 할인 수집은 계속되도록 예외를 흡수한다.

        목록엔 출시일이 없어서 여기서 단품 오퍼레이션으로 보강한다. 이미 보강해 둔
        상품은 DB에서 한 번에 읽어와 재요청 없이 값을 유지한다.
        """
        try:
            cached_meta = repository.fetch_item_meta(
                self.platform, config.STORE_REGION, list(META_KEYS)
            )
        except Exception:
            logger.exception("[playstation] 기존 메타 조회 실패 — 보강만 새로 수행")
            cached_meta = {}
        meta_fetched = 0

        for kind, category_id in RELEASE_CATEGORIES:
            count = 0
            for page in range(1, RELEASE_MAX_PAGES + 1):
                url = f"{BASE}/ko-kr/category/{category_id}/{page}"
                try:
                    result = fetch(url)
                    if result.status_code != 200:
                        break
                    raw_doc_id = self.save_raw(
                        result, document_type="list",
                        filename=f"{kind}-{category_id}-p{page}.html",
                        content_type="text/html",
                    )
                    self.pages_found += 1

                    next_data = extract_next_data(result.text)
                    if not next_data:
                        self.record_parse_error(url, f"{kind} __NEXT_DATA__ 없음 (차단/마크업 의심)")
                        break
                    items = parse_products_from_next_data(next_data)
                    if not items:
                        # '신규 발매'처럼 Product가 껍데기인 카테고리는 Concept에 실제 데이터가 있다
                        items = parse_concepts_from_next_data(next_data)
                    if not items:
                        # 1페이지가 0건이면 그 종류를 이번 실행에 전혀 못 붙인다 →
                        # 뒤따르는 할인 저장이 content_kind 를 지운 채로 끝난다(신작·무료
                        # 탭이 빈다). 정상 종료와 구분해 오류로 남긴다.
                        if page == 1:
                            logger.warning("[playstation] %s 1페이지 0건 — 실패로 기록", kind)
                            self.record_parse_error(url, f"{kind} 1페이지 0건 (마크업/차단 의심)")
                        break
                    new_items = [i for i in items if i.store_product_id not in seen_ids]
                    meta_fetched += self._enrich_release_meta(new_items, cached_meta)
                    for item in new_items:
                        seen_ids.add(item.store_product_id)
                        item.extracted_data["content_kind"] = kind
                        self.save_item(item, raw_doc_id)
                    count += len(new_items)
                    if not new_items:
                        break  # 더 볼 게 없음
                except Exception:
                    logger.exception("[playstation] %s 카테고리 수집 실패 (page %d)", kind, page)
                    break
            logger.info("[playstation] %s %d개 저장", kind, count)

        logger.info("[playstation] 출시일·퍼블리셔 보강 — 신규 조회 %d건 (기보유 %d건은 재사용)",
                    meta_fetched, len(cached_meta))

    def _collect_category_gql(
        self, category_id: str, seen_ids: set[str], saved: list, deadline: float
    ) -> bool:
        """GraphQL로 카테고리를 훑는다. 성공하면 True.

        HTML 페이지는 24개씩 주지만 이쪽은 100개씩 준다(실측). 요청 수가 1/4이라
        같은 시간예산으로 훨씬 넓게 덮는다.

        주의: persistedQuery 해시는 소니가 스키마를 바꾸면 무효가 될 수 있다(외부에서
        얻은 값이라 수명을 보장 못 함). 그래서 실패하면 False를 돌려주고 호출부가
        검증된 HTML 경로로 되돌아가게 한다 — 최악의 경우에도 지금 동작 그대로다.
        """
        offset = 0
        got_any = False
        for _ in range(GQL_MAX_PAGES):
            if time.monotonic() >= deadline:
                break
            variables = json.dumps({
                "id": category_id,
                "pageArgs": {"size": GQL_PAGE_SIZE, "offset": offset},
                "sortBy": {"name": "productReleaseDate", "isAscending": False},
                "filterBy": [], "facetOptions": [],
            }, separators=(",", ":"))
            extensions = json.dumps({
                "persistedQuery": {"version": 1, "sha256Hash": GQL_HASH}
            }, separators=(",", ":"))
            url = (
                f"{GQL_URL}?operationName=categoryGridRetrieve"
                f"&variables={quote(variables)}&extensions={quote(extensions)}"
            )
            try:
                result = fetch(url, api=True, extra_headers={
                    # 이 헤더가 없으면 CSRF 방어에 막혀 400이 난다 (실측)
                    "content-type": "application/json",
                    "x-psn-store-locale-override": "ko-kr",
                })
                if result.status_code != 200:
                    return got_any
                data = json.loads(result.text)
                if data.get("errors"):
                    return got_any
                items = parse_products_from_graphql(data)
                total = graphql_total_count(data)
            except Exception:
                logger.exception("[playstation] GraphQL 실패 %s", category_id[:8])
                return got_any

            if not items:
                # 빈 응답을 'GraphQL 사용 불가'로 승격하면 실행 전체가 24개/요청 HTML로
                # 내려가 커버리지가 급감한다(요청 4배). 이 카테고리만 접는다.
                if offset == 0:
                    self.record_parse_error(GQL_URL, f"카테고리 {category_id[:8]} GQL 0건 (일시 응답 의심)")
                return got_any or offset > 0
            got_any = True

            raw_doc_id = self.save_raw(
                result, document_type="list",
                filename=f"gql-{category_id}-o{offset}.json",
                content_type="application/json",
            )
            self.pages_found += 1

            new_items = [i for i in items if i.store_product_id not in seen_ids]
            for item in new_items:
                seen_ids.add(item.store_product_id)
                self._keep_meta(item)   # 직전 보강값 보존
                self.save_item(item, raw_doc_id)
                saved.append(item)
            logger.info("[playstation] GQL %s offset %d: %d개 (신규 %d/전체 %s)",
                        category_id[:8], offset, len(items), len(new_items), total)

            offset += len(items)
            if total is not None and offset >= total:
                break
        return got_any

    def _collect_category(
        self, category_id: str, seen_ids: set[str], saved: list, deadline: float
    ) -> None:
        no_new_streak = 0  # 신규 상품 0건인 페이지가 연속으로 나온 횟수
        for page in range(1, MAX_CATEGORY_PAGES + 1):
            if time.monotonic() >= deadline:
                break  # 시간예산 소진 — 이 카테고리도 중단
            url = f"{BASE}/ko-kr/category/{category_id}/{page}"
            result = fetch(url)

            if result.status_code == 404:
                break  # 페이지 끝
            if result.status_code != 200:
                self.record_parse_error(url, f"카테고리 페이지 상태코드 {result.status_code}")
                break

            raw_doc_id = self.save_raw(
                result, document_type="list",
                filename=f"category-{category_id}-p{page}.html",
                content_type="text/html",
            )
            self.pages_found += 1

            next_data = extract_next_data(result.text)
            if not next_data:
                self.record_parse_error(url, "__NEXT_DATA__를 찾지 못함")
                break

            items = parse_products_from_next_data(next_data)
            if not items:
                # 1페이지 0건이면 이 프로모션 전체가 이번 실행에서 누락된다 → 보이게 기록
                if page == 1:
                    self.record_parse_error(url, "카테고리 1페이지 상품 0건 (마크업/차단 의심)")
                break  # 상품이 아예 없으면 카테고리 끝

            new_items = [i for i in items if i.store_product_id not in seen_ids]
            for item in new_items:
                seen_ids.add(item.store_product_id)
                self._keep_meta(item)   # 직전 보강값 보존
                self.save_item(item, raw_doc_id)  # 즉시 저장
                saved.append(item)

            logger.info(
                "[playstation] 카테고리 %s %d페이지: 상품 %d개 (신규 %d)",
                category_id[:8], page, len(items), len(new_items),
            )

            # 카테고리끼리 상품이 크게 겹쳐, 신규가 0건인 페이지가 연속 2번이면
            # 이 카테고리는 사실상 소진된 것으로 보고 조기 종료한다(크롤 시간 대폭 단축).
            if not new_items:
                no_new_streak += 1
                if no_new_streak >= 2:
                    logger.info("[playstation] 카테고리 %s 연속 %d페이지 신규 없음 — 조기 종료",
                                category_id[:8], no_new_streak)
                    break
            else:
                no_new_streak = 0

    def _enrich_end_dates(self, saved: list) -> None:
        """할인율 상위 N개 상품의 상세 페이지에서 할인 종료일을 받아 최신 스냅샷에 덧입힌다.

        목록 페이지엔 endTime이 없고 상세 페이지(Price 노드)에만 있다. 상품 자체는 이미
        저장돼 있으므로, 여기서는 최신 price_snapshot의 sale_end_at만 in-place 갱신한다.
        상세 fetch/갱신 실패나 요청상한 도달은 흡수한다(이미 저장된 데이터는 안전).
        """
        limit = config.PS_DETAIL_END_MAX
        if limit <= 0:
            return
        targets = [
            it for it in saved
            if it.is_on_sale and it.sale_end_at is None and it.store_url
        ]
        targets.sort(key=lambda it: it.discount_percent or 0, reverse=True)

        done = 0
        stopped = False
        for item in targets[:limit]:
            if time.monotonic() >= self._job_deadline:
                stopped = True
                break
            try:
                end_at = self._end_date_via_gql(item)
                if end_at is None and not self._cta_ok:
                    end_at = self._end_date_via_html(item)
                if end_at and repository.update_latest_sale_end(
                    self.platform, config.STORE_REGION, item.store_product_id, end_at
                ):
                    done += 1
            except Exception:
                # 요청상한/네트워크/파싱 실패는 무시 — 상품은 이미 저장됨
                logger.exception("[playstation] 종료일 보강 실패: %s", item.store_product_id)
                continue
        logger.info("[playstation] 할인 종료일 보강 %d건 (대상 상위 %d, 경로 %s%s)",
                    done, min(limit, len(targets)),
                    "GraphQL" if self._cta_ok else "HTML",
                    ", 시간상한으로 조기 종료" if stopped else "")

    def _end_date_via_gql(self, item) -> str | None:
        """CTA 오퍼레이션으로 할인 종료일을 받는다 (상세 HTML의 1/150 크기).

        오퍼레이션이 무효화되면(소니 스키마 변경) 이 실행 내내 HTML 경로로 내려간다.
        """
        if not self._cta_ok:
            return None
        data = self._gql_product(GQL_CTA_OP, GQL_CTA_HASH, item.store_product_id)
        if data is None:
            self._cta_ok = False
            logger.warning("[playstation] CTA 오퍼레이션 사용 불가 — 상세 HTML 경로로 전환")
            return None
        info = parse_cta_price(data, item.final_price)
        return info.get("sale_end_at") if info else None

    def _end_date_via_html(self, item) -> str | None:
        """예비 경로: 상품 상세 HTML에서 종료일을 뽑는다 (무겁지만 검증된 경로)."""
        if not item.store_url:
            return None
        res = fetch(item.store_url)
        if res.status_code != 200:
            return None
        return parse_detail_end_time(res.text, item.final_price)

    def _gql_product(self, op: str, sha256: str, product_id: str) -> dict | None:
        """단품 GraphQL 오퍼레이션 1회 호출. 실패하면 None (호출부가 폴백 판단)."""
        variables = json.dumps({"productId": product_id}, separators=(",", ":"))
        extensions = json.dumps(
            {"persistedQuery": {"version": 1, "sha256Hash": sha256}}, separators=(",", ":")
        )
        url = (
            f"{GQL_URL}?operationName={op}"
            f"&variables={quote(variables)}&extensions={quote(extensions)}"
        )
        try:
            res = fetch(url, api=True, extra_headers={
                "content-type": "application/json",     # 없으면 CSRF 방어에 막혀 400
                "x-psn-store-locale-override": "ko-kr",
            })
            if res.status_code != 200:
                return None
            data = json.loads(res.text)
        except Exception:
            logger.exception("[playstation] %s 호출 실패: %s", op, product_id)
            return None
        return None if data.get("errors") else data

    def _keep_meta(self, item) -> None:
        """할인 경로 저장 직전, 직전에 보강된 메타(출시일·퍼블리셔·장르·DLC판별)를
        되살린다. 목록에서 온 값이 있으면 건드리지 않고 빈 자리만 채운다."""
        keep = getattr(self, "_prev_meta", {}).get(item.store_product_id)
        if not keep:
            return
        for k in META_KEYS:
            if keep.get(k) is not None and not item.extracted_data.get(k):
                item.extracted_data[k] = keep[k]

    def _enrich_release_meta(self, items: list, cached: dict) -> int:
        """신작·출시예정 상품에 출시일·퍼블리셔·장르를 채운다.

        목록(카테고리 그리드)에는 출시일이 아예 없어서, PS만 '신작/출시예정' 탭에서
        D-day 배지도 출시일 정렬도 못 하는 상태였다.

        upsert_store_item 은 current_data 를 통째로 덮어쓰므로, 이미 채워 둔 상품은
        DB 값(cached)을 그대로 다시 실어 준다 → 재요청 없이 값이 유지된다.
        새로 나타난 상품만 요청하므로 실행마다 드는 비용은 '신규분'뿐이다.
        """
        # 상한은 '실행 전체' 기준이다. 이 함수는 페이지마다 불리므로 지역 변수로 세면
        # 페이지당 상한이 되어 사실상 무제한이 된다(실측: 상한 150인데 216건 조회됨).
        budget = config.PS_RELEASE_META_MAX
        fetched = 0
        for item in items:
            have = cached.get(item.store_product_id)
            if have:
                # 기존 값 되살리기는 요청이 들지 않으므로 상한과 무관하게 항상 한다
                item.extracted_data.update(have)
                # 다만 나중에 추가된 필드는 예전에 보강해 둔 상품에 없다. 있는 값만 보고
                # 넘기면 그 상품은 영영 새 필드를 못 받는다(실측: 216건이 재사용으로
                # 넘어가 DLC 판별이 아예 시작되지 않았다). 최신 필드가 없으면 다시 받는다.
                if "content_type" in have:
                    continue
            # self._meta_fetched 로 세는 이유: 이 함수는 페이지마다 불린다.
            # 지역 변수로 세면 '페이지당 상한'이 되어 사실상 무제한이 된다
            # (실측: 상한 150인데 216건 조회됨 — 9페이지 × 24개).
            if self._meta_fetched >= budget or not self._meta_ok:
                continue
            if time.monotonic() >= self._job_deadline:
                continue
            data = self._gql_product(GQL_META_OP, GQL_META_HASH, item.store_product_id)
            if data is None:
                self._meta_ok = False
                logger.warning("[playstation] 메타 오퍼레이션 사용 불가 — 출시일 보강 중단")
                continue
            meta = parse_product_meta(data)
            fetched += 1
            self._meta_fetched += 1
            if meta:
                item.extracted_data.update(meta)
        return fetched
