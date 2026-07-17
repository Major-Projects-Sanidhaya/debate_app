from pipeline.normalize import claim_hash, normalize_claim


def test_lowercases_and_strips_punctuation():
    assert normalize_claim("The U.S. has 390 MILLION guns!") == "the us has 390 million guns"


def test_collapses_whitespace():
    assert normalize_claim("  gun \t deaths\n rose  ") == "gun deaths rose"


def test_equivalent_claims_share_a_hash():
    a = claim_hash("Violent crime FELL since 1991.")
    b = claim_hash("violent crime fell since 1991")
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_different_claims_differ():
    assert claim_hash("crime fell") != claim_hash("crime rose")
