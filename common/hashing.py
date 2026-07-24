"""내용 해시 계산 — 같은 원본/같은 가격을 중복 저장하지 않기 위해 사용."""
import hashlib
import json


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_json(obj) -> str:
    """딕셔너리 등을 항상 같은 순서로 직렬화해서 해시를 만든다."""
    canonical = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
