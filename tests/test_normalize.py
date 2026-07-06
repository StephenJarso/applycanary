"""Normalisation and fingerprint tests.

The important cases are the *negatives*: over-normalising merges genuinely
distinct requisitions and silently hides a real job opening.
"""

from __future__ import annotations

from app.pipeline.normalize import (
    canonical_url,
    fingerprint,
    is_remote,
    location_bucket,
    norm_company,
    norm_title,
)


class TestNormCompany:
    def test_strips_legal_suffixes(self):
        assert norm_company("Acme Corp., Inc.") == "acme"
        assert norm_company("Foo Ltd") == "foo"
        assert norm_company("Bar GmbH") == "bar"

    def test_case_and_punctuation_insensitive(self):
        assert norm_company("ACME  inc.") == norm_company("Acme, Inc")

    def test_keeps_meaningful_words(self):
        # "Labs"/"Group" distinguish real companies and must survive.
        assert norm_company("Acme Labs") == "acme labs"
        assert norm_company("Acme Group") == "acme group"

    def test_never_empties_a_real_name(self):
        # A company literally named "Limited" must not normalise to "".
        assert norm_company("Limited") != ""


class TestNormTitle:
    def test_strips_gender_markers(self):
        assert norm_title("Backend Engineer (m/f/d)") == "backend engineer"
        assert norm_title("Data Analyst m/w/d") == "data analyst"

    def test_strips_location_tail_and_noise(self):
        assert norm_title("Backend Engineer - Remote") == "backend engineer"
        assert "urgent" not in norm_title("URGENT: Backend Engineer")

    def test_preserves_seniority_and_level(self):
        assert "senior" in norm_title("Senior Backend Engineer")
        assert norm_title("Engineer II") != norm_title("Engineer III")

    def test_keeps_technical_punctuation(self):
        assert "c++" in norm_title("C++ Developer")
        assert "c#" in norm_title("C# Developer")
        assert "node.js" in norm_title("Node.js Engineer")


class TestLocationBucket:
    def test_remote_collapses(self):
        assert location_bucket("Remote - US") == "remote"
        assert location_bucket("Anywhere") == "remote"
        assert location_bucket("Nairobi", remote=True) == "remote"

    def test_city_aliases(self):
        assert location_bucket("SF, CA") == location_bucket("San Francisco, CA")
        assert location_bucket("Bangalore") == location_bucket("Bengaluru")

    def test_distinct_cities_stay_distinct(self):
        assert location_bucket("Nairobi, Kenya") != location_bucket("Lagos, Nigeria")

    def test_empty_is_unspecified(self):
        assert location_bucket("") == "unspecified"


class TestIsRemote:
    def test_explicit_flag_wins(self):
        assert is_remote("Nairobi", flag=True) is True
        assert is_remote("Remote", flag=False) is False

    def test_infers_from_location_then_description(self):
        assert is_remote("Remote - EU") is True
        assert is_remote("", description="This is a fully remote role.") is True

    def test_ignores_remote_mentioned_late(self):
        # "remote" deep in a benefits section should not make an onsite job remote.
        assert is_remote("Nairobi, Kenya", description="x" * 900 + " remote") is False


class TestCanonicalUrl:
    def test_strips_tracking_params(self):
        assert canonical_url("https://x.com/j/1?utm_source=li&ref=abc") == "https://x.com/j/1"

    def test_keeps_identifying_params(self):
        assert "gh_jid=7" in canonical_url("https://x.com/j?gh_jid=7&utm_medium=x")

    def test_normalises_host_scheme_and_slash(self):
        a = canonical_url("http://WWW.X.com/j/1/")
        b = canonical_url("https://x.com/j/1")
        assert a == b

    def test_param_order_irrelevant(self):
        assert canonical_url("https://x.com/j?b=2&a=1") == canonical_url("https://x.com/j?a=1&b=2")

    def test_empty_and_malformed_are_safe(self):
        assert canonical_url("") == ""
        assert canonical_url("not a url") == "not a url"


class TestFingerprint:
    def test_same_job_decorated_differently_collides(self):
        a = fingerprint("Acme Inc.", "Backend Engineer (m/f/d)", "Remote - US")
        b = fingerprint("Acme", "Backend Engineer", "Anywhere")
        assert a == b

    def test_different_seniority_does_not_collide(self):
        a = fingerprint("Acme", "Senior Backend Engineer", "Remote")
        b = fingerprint("Acme", "Backend Engineer", "Remote")
        assert a != b

    def test_different_company_does_not_collide(self):
        a = fingerprint("Acme", "Backend Engineer", "Remote")
        b = fingerprint("Globex", "Backend Engineer", "Remote")
        assert a != b

    def test_different_city_does_not_collide(self):
        a = fingerprint("Acme", "Backend Engineer", "Nairobi, Kenya")
        b = fingerprint("Acme", "Backend Engineer", "Berlin, Germany")
        assert a != b

    def test_is_stable_hex(self):
        fp = fingerprint("Acme", "Engineer", "Remote")
        assert len(fp) == 64
        assert fp == fingerprint("Acme", "Engineer", "Remote")
