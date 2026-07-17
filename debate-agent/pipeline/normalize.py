"""Claim normalization for cache keys: lowercase, collapse whitespace, strip punctuation."""

import hashlib
import string

_PUNCT_TABLE = str.maketrans("", "", string.punctuation)


def normalize_claim(claim: str) -> str:
    return " ".join(claim.lower().translate(_PUNCT_TABLE).split())


def claim_hash(claim: str) -> str:
    return hashlib.sha256(normalize_claim(claim).encode("utf-8")).hexdigest()
