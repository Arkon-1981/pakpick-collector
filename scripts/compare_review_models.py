"""AI 한 줄 평 모델 비교용 일회성 테스트 스크립트.

같은 게임 1개에 대해 Haiku와 Sonnet으로 각각 "웹 검색 → 한 줄 평 + 출처"를
생성해 나란히 출력한다. DB에 저장하지 않는 읽기 전용 테스트다.

사용법:
  ANTHROPIC_API_KEY=... python scripts/compare_review_models.py \
      --title "호그와트 레거시 Hogwarts Legacy" --platform "Nintendo Switch"

필요한 환경변수: ANTHROPIC_API_KEY
"""
import argparse
import os
import sys

import anthropic

# 비교할 모델들 (친숙한 이름 → 모델 ID)
MODELS = [
    ("Haiku 4.5", "claude-haiku-4-5"),
    ("Sonnet 5", "claude-sonnet-5"),
]

# 웹 검색 도구 — 모든 모델에서 동작하는 기본 버전
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}

PROMPT_TEMPLATE = (
    "게임 '{title}' ({platform} 버전)의 평가를 조사해서, 한국어 한 문장으로 요약해줘.\n"
    "규칙:\n"
    "- 먼저 웹을 검색해서 실제 평론가·유저 반응을 확인할 것\n"
    "- 40~80자 사이의 자연스러운 한국어 한 문장\n"
    "- 장점과 단점을 균형 있게, 과장 없이 사실 기반으로\n"
    "- 해당 플랫폼(예: 스위치판 성능) 특유의 평가가 있으면 반영\n"
    "- 스포일러 금지, 마크다운/따옴표 없이 문장만 출력\n"
    "- 신뢰할 만한 출처(평론 매체, Metacritic/OpenCritic 등)를 우선 참고"
)


def summarize(client: anthropic.Anthropic, model_id: str, title: str, platform: str):
    """한 게임에 대해 한 줄 평 + 참고한 출처를 생성해 돌려준다."""
    resp = client.messages.create(
        model=model_id,
        max_tokens=1024,
        tools=[WEB_SEARCH_TOOL],
        messages=[{"role": "user", "content": PROMPT_TEMPLATE.format(title=title, platform=platform)}],
    )

    # 최종 한 줄 평 = 텍스트 블록들을 이어붙인 것
    text_parts = [b.text for b in resp.content if b.type == "text"]
    summary = " ".join(t.strip() for t in text_parts if t.strip()).strip()

    # 참고한 출처 = 웹 검색 결과 블록에서 URL/제목 수집 (중복 제거, 최대 5개)
    sources: list[dict] = []
    seen = set()
    for block in resp.content:
        if block.type != "web_search_tool_result":
            continue
        content = block.content
        if not isinstance(content, list):  # 오류면 content가 객체 → 건너뜀
            continue
        for r in content:
            url = getattr(r, "url", None)
            if url and url not in seen:
                seen.add(url)
                sources.append({"title": getattr(r, "title", "") or url, "url": url})
        if len(sources) >= 5:
            break

    usage = resp.usage
    return summary, sources[:5], usage


def main() -> int:
    parser = argparse.ArgumentParser(description="AI 한 줄 평 모델 비교 테스트")
    parser.add_argument("--title", required=True, help="게임 제목")
    parser.add_argument("--platform", default="Nintendo Switch", help="플랫폼 표기")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY 환경변수가 없습니다.", file=sys.stderr)
        return 1

    client = anthropic.Anthropic()

    print(f"\n{'='*70}\n게임: {args.title}  ·  플랫폼: {args.platform}\n{'='*70}")
    for label, model_id in MODELS:
        print(f"\n[{label}]  ({model_id})")
        try:
            summary, sources, usage = summarize(client, model_id, args.title, args.platform)
            print(f"  한 줄 평: {summary or '(비어 있음)'}")
            print("  출처:")
            for s in sources:
                print(f"    - {s['title']}  ({s['url']})")
            if not sources:
                print("    (검색 결과 블록 없음)")
            print(
                f"  토큰: 입력 {usage.input_tokens} / 출력 {usage.output_tokens}"
                + (f" / 웹검색 {usage.server_tool_use.web_search_requests}회"
                   if getattr(usage, 'server_tool_use', None) else "")
            )
        except Exception as exc:  # 한 모델이 실패해도 나머지는 계속
            print(f"  실패: {exc}")

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
