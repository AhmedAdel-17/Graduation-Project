"""Tests for point-in-time index membership / survivorship guard (Phase 0)."""
from tradingagents.eval.universe import (
    Membership,
    members_as_of,
    is_active,
    survivorship_coverage,
    load_membership,
    current_universe,
)


# A synthetic membership with a REMOVED name, to exercise the point-in-time logic
# (the shipped seed file intentionally has no removed names yet).
SYNTH = [
    Membership("AAA.CA", "EGX30", add_date="2020-01-01", remove_date=None),
    Membership("BBB.CA", "EGX30", add_date="2020-01-01", remove_date="2022-06-01"),  # delisted
    Membership("CCC.CA", "EGX30", add_date="2023-01-01", remove_date=None),          # added later
    Membership("DDD.CA", "EGX70", add_date=None, remove_date=None),                  # unknown add
]


def test_members_as_of_excludes_not_yet_added():
    assert "CCC.CA" not in members_as_of("2022-01-01", index="EGX30", membership=SYNTH)
    assert "CCC.CA" in members_as_of("2023-06-01", index="EGX30", membership=SYNTH)


def test_members_as_of_includes_later_delisted_on_old_date():
    # The whole point of survivorship safety: BBB was a member in 2021 even though
    # it was later delisted; an eval as of 2021 must see it.
    assert "BBB.CA" in members_as_of("2021-03-01", index="EGX30", membership=SYNTH)
    assert "BBB.CA" not in members_as_of("2023-03-01", index="EGX30", membership=SYNTH)


def test_index_filter():
    assert "DDD.CA" not in members_as_of("2023-01-01", index="EGX30", membership=SYNTH)
    assert "DDD.CA" in members_as_of("2023-01-01", index="EGX70", membership=SYNTH)


def test_is_active():
    assert is_active("BBB.CA", "2021-01-01", membership=SYNTH) is True
    assert is_active("BBB.CA", "2022-12-01", membership=SYNTH) is False
    assert is_active("ZZZ.CA", "2021-01-01", membership=SYNTH) is False


def test_unknown_add_date_treated_active():
    assert is_active("DDD.CA", "1999-01-01", membership=SYNTH, index="EGX70") is True


def test_coverage_flags_safe_vs_unsafe():
    # SYNTH has a removed name but DDD is undated ⇒ not fully covered ⇒ not safe.
    cov = survivorship_coverage(SYNTH)
    assert cov["removed_names"] == 1
    assert cov["date_coverage"] < 1.0
    assert cov["is_survivorship_safe"] is False

    # A fully-dated set WITH a removed name is the genuinely safe case.
    fully_dated = [
        Membership("AAA.CA", "EGX30", add_date="2020-01-01", remove_date=None),
        Membership("BBB.CA", "EGX30", add_date="2020-01-01", remove_date="2022-06-01"),
    ]
    safe_cov = survivorship_coverage(fully_dated)
    assert safe_cov["is_survivorship_safe"] is True

    seed = load_membership()
    assert seed, "seed membership file should load"
    seed_cov = survivorship_coverage(seed)
    # The shipped seed is current-members-only ⇒ honestly reports NOT safe yet.
    assert seed_cov["removed_names"] == 0
    assert seed_cov["is_survivorship_safe"] is False


def test_current_universe_nonempty():
    assert len(current_universe()) > 0
