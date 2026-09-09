"""플레이스테이션 한국 스토어 수집기.

수집 경로: web.np.playstation.com 의 공개 GraphQL `categoryGridRetrieve`.

⚠️ 2026-08-11 사고 기록 — 왜 HTML 을 안 읽는가:
  예전에는 스토어 페이지 HTML 의 <script id="__NEXT_DATA__"> 에서 상품을 읽었다.
  소니가 목록을 클라이언트 렌더링으로 바꾸면서 그 JSON 의 apolloState 에 내비게이션
  5개만 남고 상품이 통째로 사라졌다(실측: "Product: 0건). /pages/deals 허브도 같이
  비어 프로모션 카테고리 링크가 0개가 됐고, 할인 수집 전체가 조용히 건너뛰어져
  8회 연속 수집 실패했다(이상 감지 가드가 기존 데이터는 지켜 냈다).

  그래서 HTML 목록 경로는 폴백으로도 남기지 않는다 — 0건을 돌려주는 경로로
  되돌아가면 시간예산만 태우고 고장을 '조용한 부분 수집'으로 감춘다.

동작:
  1. 아래 고정 카테고리들을 GraphQL 로 100개씩 페이지네이션 → 원본 저장 → 즉시 저장
  2. concepts 로만 오는 카테고리는 단품 오퍼레이션으로 가격을 채운다
  3. 할인 종료일 보강 → 인기 Top10 순위
"""
import json
import time
from urllib.parse import quote

from collectors.base import BaseCollector
from common import config
from common.http_client import fetch
from common.logging_util import get_logger
from db import repository
from parsers.playstation import (
    graphql_total_count,
    parse_concepts_from_graphql,
    parse_cta_price,
    parse_product_meta,
    parse_products_from_graphql,
)

logger = get_logger(__name__)

BASE = "https://store.playstation.com"

# GMA(스토어가 자동 편성하는) 고정 카테고리. UUID 가 회전하지 않아 허브 파싱이 필요없다.
# reportingName·건수는 실측(2026-08-13, ko-kr).
#
# 회전하는 프로모션 카테고리를 굳이 찾지 않는 이유: 프로모션 상품은 정의상 할인 중이고
# '모든 할인'(AllDeals)은 그 상위집합이다. 즉 프로모션을 30개 훑던 예전 방식과 범위가
# 같으면서 요청 수는 훨씬 적다(예전 68분 → 실측 기준 20분 이하).
#
# ⚠️ 순서가 곧 우선순위다. 한 실행에서 전부 훑을 시간이 없다 —
# 실측(run 250): 저장까지 포함해 grid 1페이지(100개)에 약 84초가 걸린다. 카탈로그가
# 11,000건이면 3시간이 필요한데 잡 타임아웃은 2시간이다. 그래서 작고 필수적인 것부터
# 돌리고, 폭(breadth)용 카테고리는 남는 시간만 쓰되 실행마다 시작 지점을 옮긴다.
#
# (이름, 카테고리 ID, 붙일 content_kind, 실행마다 시작 offset 을 회전할지)
CATALOG_CATEGORIES = [
    # GMA_PRE-ORDERS_DYNAMIC / cat.gma.Pre-Orders — 약 105건. 작고 '출시예정' 탭의
    # 유일한 공급원이라 맨 앞에 둔다(뒤에 두면 시간예산에 밀려 탭이 통째로 안 갱신된다).
    ("PreOrders", "3bf499d7-7acf-4931-97dd-2667494ee2c9", "upcoming", False),
    # GMA_ALL_DEALS_(DYNAMIC)_2025 / cat.gma.AllDeals — 할인 피드의 근원. 이 서비스의
    # 본체다. 세일 시즌에 크게 불어난다(2026-08-25 2,329건 → 09-09 5,820건 실측).
    ("AllDeals", "3f772501-f6f8-49b7-abac-874a88ca4897", None, True),
    # GMA_ALL_PS4_GAMES_DYNA / cat.gma.x_All_PS4_games — 5,200여 건. 상세페이지용 폭이라
    # 신선도 요구가 낮다. 남는 시간에 조금씩, 실행마다 다른 구간을 훑는다.
    ("AllPS4", "30e3fe35-8f2d-4496-95bc-844f56952e3c", None, True),
]

# 이 카테고리들은 grid 가 products 대신 concepts 로만 응답한다(실측). concepts 에는
# 가격이 안 실려 오므로 상품당 1요청으로 가격을 채운다 — 안 채우면 무료·신작 탭이
# 가격 없는 카드로 채워진다.
CONCEPT_CATEGORIES = [
    ("NewGames", "e1699f77-77e1-43ca-a296-26d08abacb0f", "new"),    # 134건
    ("FreeToPlay", "4dfd67ab-4ed7-40b0-a937-a549aece13d0", "free"),  # 148건
]

# 단품 오퍼레이션으로 보강하는 필드 — 다음 실행에서 그대로 되살릴 값들
META_KEYS = ("release_date", "publisher", "genres", "content_rating",
             "short_description", "players", "platforms",
             "content_type", "top_category", "store_classification")

# GraphQL 카테고리 조회 — HTML(24개/요청) 대비 100개/요청이라 요청 수가 1/4.
# 해시는 외부에서 얻어 직접 호출로 검증했다. 소니가 스키마를 바꾸면 무효가 되며
# 그때는 `Query not whitelisted` 가 온다 — 폴백은 없다(HTML 경로는 죽었다).
GQL_URL = "https://web.np.playstation.com/api/graphql/v1/op"
# '국내 Top 10' 카테고리 — /pages/latest 의 "Top 10 Games in your Country" 스트랜드가
# 가리키는 categoryId (탐사 실측). 항목이 concepts(순위 순)로 오는 특수 카테고리다.
POPULAR_CATEGORY = "fbb563aa-c602-476d-bb92-fe7f35080205"
GQL_HASH = "9845afc0dbaab4965f6563fffc703f588c8e76792000e8610843b8d3ee9c4c09"
GQL_PAGE_SIZE = 100
GQL_MAX_PAGES = 100  # 카테고리당 최대 10,000개 (스토어의 offset 상한과 동일)

# 단품 조회 오퍼레이션 — 상세 HTML(1건 400KB) 대신 쓴다.
# 종료일만 필요할 땐 CTA 쪽이 2.7KB라 약 150배 가볍다(실측). 둘 다 직접 호출 검증함.
GQL_CTA_OP = "productRetrieveForCtasWithPrice"
GQL_CTA_HASH = "8532da7eda369efdad054ca8f885394a2d0c22d03c5259a422ae2bb3b98c5c99"
GQL_META_OP = "metGetProductById"
GQL_META_HASH = "a128042177bd93dd831164103d53b73ef790d56f51dae647064cb8f9d9fc9d1a"
# 단품 오퍼레이션을 '무효화됐다'고 판단하기까지 견디는 연속 실패 횟수.
# 1회로 두면 일시적 실패 하나에 보강 단계 전체가 접힌다(실측 run 250).
CTA_FAIL_LIMIT = 3


class PlaystationCollector(BaseCollector):
    platform = "playstation"

    # 출시예정작은 가격이 없는 것이 정상이고(is_on_sale=False 로 저장된다),
    # Concept 경로로만 들어온 상품은 이미지가 비는 경우가 있다.
    FIELD_FLOORS = {"title": 0.95, "image_url": 0.70, "final_price": 0.80}

    # collect()에서 실제 값으로 설정된다 (단위 테스트/부분 호출 시의 기본값)
    _cta_ok = _meta_ok = True
    _cta_fails = 0       # CTA 오퍼레이션 연속 실패 횟수 (CTA_FAIL_LIMIT 에서 포기)
    _job_deadline = float("inf")
    _meta_fetched = 0    # 실행 전체에서 새로 조회한 메타 건수 (상한 기준)
    _price_fetched = 0   # 실행 전체에서 새로 조회한 concept 가격 건수 (상한 기준)
    _meta_cache: dict | None = None

    def collect(self) -> None:
        self._cta_ok = True   # 종료일·concept 가격 CTA 오퍼레이션
        self._cta_fails = 0
        self._meta_ok = True  # 출시일·퍼블리셔 오퍼레이션 (실패 시 보강 생략)
        self._meta_fetched = 0
        self._price_fetched = 0
        self._meta_cache = None
        seen_ids: set[str] = set()

        # 보강 메타(출시일·퍼블리셔·장르·DLC판별) 보존은 이제 저장 계층의
        # current_data 병합(repository.merge_current_data)이 처리한다 — META_KEYS 가
        # 전부 MERGE_FILL_KEYS 에 포함되므로 여기서 따로 프리페치하지 않는다
        # (같은 데이터를 실행당 두 번 받던 중복 제거).
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

        # 1. 신작·무료 (concepts 전용, 소량) — 할인 크롤이 시간예산을 다 써도 반드시
        #    수집되도록 먼저 돌린다. content_kind 가 여기서만 붙으므로 밀리면 탭이 빈다.
        self._collect_releases(seen_ids, saved, crawl_deadline)

        # 2. 고정 카테고리 (출시예정 → 할인 → PS4 카탈로그 순 = 우선순위 순)
        for name, category_id, kind, rotate in CATALOG_CATEGORIES:
            if time.monotonic() >= crawl_deadline:
                logger.info(
                    "[playstation] 크롤 시간예산(%d분) 소진 — 남은 카테고리 건너뛰고 종료일 보강으로",
                    config.PS_CRAWL_BUDGET_SECONDS // 60,
                )
                break
            self._collect_grid(name, category_id, kind, seen_ids, saved, crawl_deadline,
                               rotate=rotate)

        # 3. 할인 종료일 보강 — 이미 저장된 상품의 최신 스냅샷에 in-place 갱신.
        #    크롤이 시간예산으로 조기 종료되어도 이 단계는 반드시 실행된다.
        self._enrich_end_dates(saved)

        # 4. 인기(국내 Top 10) — 요청 몇 번이라 시간예산과 무관하게 마지막에 붙인다.
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

    def _collect_releases(self, seen_ids: set[str], saved: list, deadline: float) -> None:
        """concepts 로만 오는 카테고리(신작·무료)를 수집한다.

        가격이 안 실려 오므로(_collect_grid 의 concepts 경로) 상품당 1요청으로 채운다.
        content_kind 가 이 단계에서만 붙어서, 밀리면 웹의 신작·무료 탭이 빈다.
        실패해도 이후 할인 수집은 계속되도록 카테고리 단위로 예외를 흡수한다.
        """
        for name, category_id, kind in CONCEPT_CATEGORIES:
            if time.monotonic() >= deadline:
                logger.warning("[playstation] 시간예산 소진 — %s 건너뜀 (해당 탭이 빈다)", name)
                continue
            try:
                self._collect_grid(name, category_id, kind, seen_ids, saved, deadline,
                                   concepts=True)
            except Exception:
                logger.exception("[playstation] %s 카테고리 수집 실패", name)

    def _collect_grid(
        self, name: str, category_id: str, kind: str | None,
        seen_ids: set[str], saved: list, deadline: float, *,
        concepts: bool = False, rotate: bool = False,
    ) -> None:
        """GraphQL 로 카테고리를 100개씩 훑는다 (페이지마다 즉시 저장).

        concepts=True 인 카테고리는 가격이 없는 채로 오므로, 새로 본 상품에만
        가격·메타를 단품 오퍼레이션으로 덧입힌 뒤 저장한다.

        rotate=True 면 실행마다 다른 offset 에서 시작하고, 끝에 닿으면 0 으로 감아
        시작점까지 돈다. 시간예산에 늘 잘리는 큰 카테고리가 매번 앞부분만 반복해서
        훑는 것을 막는다 — 그러면 뒤쪽 상품은 last_seen_at 이 영영 안 갱신돼
        웹에서 사라진다(실측 run 250: AllDeals 5,820건 중 앞 1,700건만 훑었다).
        """
        offset = 0
        total = None
        got = 0
        cached_meta = self._release_meta_cache() if concepts else {}
        requests = 0
        start_offset = 0      # 이번 실행이 실제로 시작한 지점 (회전 시 0 이 아니다)
        seen_pages = 0        # 감아 돌 때 한 바퀴를 넘지 않도록 세는 값
        wrapped = False
        for _ in range(GQL_MAX_PAGES):
            if time.monotonic() >= deadline:
                break
            requests += 1
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
                    self.record_parse_error(url, f"{name} 상태코드 {result.status_code}")
                    return
                data = json.loads(result.text)
                if data.get("errors"):
                    self.record_parse_error(url, f"{name} GraphQL 오류: {str(data['errors'])[:180]}")
                    return
                items = (parse_concepts_from_graphql(data) if concepts
                         else parse_products_from_graphql(data))
                total = graphql_total_count(data)
            except Exception:
                logger.exception("[playstation] %s GraphQL 실패 (offset %d)", name, offset)
                return

            if not items:
                if offset == 0:
                    # 1페이지 0건 = 이 카테고리가 이번 실행에서 통째로 빠진다.
                    # AllDeals 가 이렇게 되면 할인 피드가 갱신되지 않으므로 반드시 남긴다.
                    self.record_parse_error(url, f"{name} 1페이지 0건 (스키마 변경/차단 의심)")
                    logger.warning("[playstation] %s 1페이지 0건 — 실패로 기록", name)
                return

            raw_doc_id = self.save_raw(
                result, document_type="list",
                filename=f"gql-{name}-o{offset}.json",
                content_type="application/json",
            )
            self.pages_found += 1

            new_items = [i for i in items if i.store_product_id not in seen_ids]
            if concepts:
                self._enrich_concept_prices(new_items)
                self._enrich_release_meta(new_items, cached_meta)
            for item in new_items:
                seen_ids.add(item.store_product_id)
                if kind:
                    item.extracted_data["content_kind"] = kind
                self.save_item(item, raw_doc_id)
                saved.append(item)
            got += len(new_items)
            logger.info("[playstation] %s offset %d: %d개 (신규 %d/전체 %s)",
                        name, offset, len(items), len(new_items), total)

            seen_pages += 1
            offset += len(items)

            if rotate and total and requests == 1:
                # 첫 응답으로 total 을 알았다. 실행 시각(12시간 단위)으로 시작 지점을
                # 옮긴다 — 하루 2회 실행에서 매번 다른 구간을 훑게 된다.
                pages = max(1, -(-total // GQL_PAGE_SIZE))
                bucket = int(time.time() // (12 * 3600))
                start_offset = (bucket % pages) * GQL_PAGE_SIZE
                if start_offset:
                    offset = start_offset
                    logger.info("[playstation] %s 회전 — 이번 실행은 offset %d 부터 (전체 %d)",
                                name, start_offset, total)
                    continue

            if total is not None and offset >= total:
                if not (rotate and start_offset):
                    break
                # 끝에 닿았다 — 처음으로 감아 시작 지점까지 마저 훑는다
                if wrapped:
                    break
                wrapped = True
                offset = 0
            if wrapped and offset >= start_offset:
                break

        # 절단은 반드시 남긴다 — 시간예산이든 페이지 상한이든, 조용히 끊기면
        # 뒤쪽 상품은 last_seen_at 이 안 갱신돼 웹에서 사라지는데 로그만 보면 정상이다.
        # 회전 중이면 offset 이 되감기므로 '본 페이지 수'로 재야 한다.
        scanned = seen_pages * GQL_PAGE_SIZE
        if total is not None and scanned < total:
            logger.warning("[playstation] %s 절단 — %d/%d 만 훑었다 (offset %d 부터, %s)",
                           name, scanned, total, start_offset,
                           "시간예산 소진" if time.monotonic() >= deadline
                           else f"페이지 상한 {GQL_MAX_PAGES}")
        logger.info("[playstation] %s %d개 저장 (전체 %s, 요청 %d회)", name, got, total, requests)

    def _release_meta_cache(self) -> dict:
        """이미 보강해 둔 출시일·퍼블리셔 등을 한 번에 읽어 재요청을 막는다."""
        if self._meta_cache is None:
            try:
                self._meta_cache = repository.fetch_item_meta(
                    self.platform, config.STORE_REGION, list(META_KEYS)
                )
            except Exception:
                logger.exception("[playstation] 기존 메타 조회 실패 — 보강만 새로 수행")
                self._meta_cache = {}
        return self._meta_cache

    def _enrich_concept_prices(self, items: list) -> int:
        """concepts 로 온 상품의 가격을 단품 CTA 오퍼레이션으로 채운다.

        grid 의 concepts 노드엔 price 가 없다(실측). 안 채우면 무료·신작 탭이 가격
        없는 카드로 채워지고, final_price 커버리지 하한(FIELD_FLOORS)도 위태롭다.

        PS Plus 가입자 전용가는 일반 이용자의 체감가가 아니므로 할인으로 보지 않고
        정가만 채운다 (parse_cta_price 의 plus_only).
        """
        budget = config.PS_CONCEPT_PRICE_MAX
        filled = 0
        for item in items:
            if item.final_price is not None:
                continue
            if self._price_fetched >= budget or not self._cta_ok:
                continue
            if time.monotonic() >= self._job_deadline:
                continue
            data = self._gql_product(GQL_CTA_OP, GQL_CTA_HASH, item.store_product_id)
            if data is None:
                self._cta_fails += 1
                if self._cta_fails >= CTA_FAIL_LIMIT:
                    self._cta_ok = False
                    logger.warning("[playstation] CTA 오퍼레이션 연속 %d회 실패 — concept 가격 보강 중단",
                                   CTA_FAIL_LIMIT)
                continue
            self._cta_fails = 0
            self._price_fetched += 1
            info = parse_cta_price(data)
            if not info:
                continue
            base = info.get("base_price")
            disc = info.get("discounted_price")
            if base is None and disc is None:
                continue
            on_sale = (
                not info.get("plus_only")
                and base is not None and disc is not None and disc < base
            )
            item.regular_price = base if base is not None else disc
            item.final_price = disc if (on_sale and disc is not None) else item.regular_price
            item.sale_price = item.final_price if on_sale else None
            item.is_on_sale = on_sale
            if on_sale and base:
                item.discount_percent = round((base - item.final_price) / base * 100, 2)
            if on_sale:
                item.sale_end_at = info.get("sale_end_at")
            filled += 1
        return filled

    def _enrich_end_dates(self, saved: list) -> None:
        """할인율 상위 N개의 할인 종료일을 단품 CTA 오퍼레이션으로 받아 스냅샷에 덧입힌다.

        목록(grid)에는 endTime 이 없다. 상품 자체는 이미 저장돼 있으므로 여기서는
        최신 price_snapshot 의 sale_end_at 만 in-place 갱신한다.

        ⚠️ 예전엔 CTA 가 실패하면 상세 HTML 을 긁는 폴백이 있었다. 소니가 상세도
        클라이언트 렌더링으로 바꾼 뒤로 그 경로는 죽었다 — 실측(run 250): 300건을
        상품당 10초씩 33분 태워서 종료일 10건을 얻었다(1%). 그 33분 때문에 AllPS4·
        PreOrders 가 통째로 시간예산 밖으로 밀려났다. 그래서 폴백을 지웠다.
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
            if not self._cta_ok:
                break
            try:
                end_at = self._end_date_via_gql(item)
                if end_at and repository.update_latest_sale_end(
                    self.platform, config.STORE_REGION, item.store_product_id, end_at
                ):
                    done += 1
            except Exception:
                # 네트워크/파싱 실패는 무시 — 상품은 이미 저장됨
                logger.exception("[playstation] 종료일 보강 실패: %s", item.store_product_id)
                continue
        logger.info("[playstation] 할인 종료일 보강 %d건 (대상 상위 %d%s%s)",
                    done, min(limit, len(targets)),
                    "" if self._cta_ok else ", CTA 연속 실패로 중단",
                    ", 시간상한으로 조기 종료" if stopped else "")

    def _end_date_via_gql(self, item) -> str | None:
        """CTA 오퍼레이션으로 할인 종료일을 받는다.

        ⚠️ 한 번 실패했다고 오퍼레이션이 죽은 것으로 보지 않는다 — 실측(run 250):
        일시적 실패 1회에 이 실행 내내 보강을 접고 죽은 HTML 경로로 내려갔다.
        연속 실패가 CTA_FAIL_LIMIT 번 쌓여야 '무효화됐다'고 판단한다.
        """
        if not self._cta_ok:
            return None
        data = self._gql_product(GQL_CTA_OP, GQL_CTA_HASH, item.store_product_id)
        if data is None:
            self._cta_fails += 1
            if self._cta_fails >= CTA_FAIL_LIMIT:
                self._cta_ok = False
                logger.warning(
                    "[playstation] CTA 오퍼레이션 연속 %d회 실패 — 종료일 보강 중단 "
                    "(해시 무효화 의심: 스토어 JS 번들에서 새 해시를 캐야 한다)",
                    CTA_FAIL_LIMIT)
            return None
        self._cta_fails = 0
        info = parse_cta_price(data, item.final_price)
        return info.get("sale_end_at") if info else None

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
