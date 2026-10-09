"""bpm_tagger/web/weighting.py — the pure rating-weighted sampler, and the
settings.json / env migration of the retired run_prefer_starred/familiar
toggles into pick_use_ratings/pick_new_factor (config.py).

Pure DB/weighting logic, but weighting.py lives in web/ (an optional layer),
so this is NOT marked pytest.mark.core.
"""

import random

from bpm_tagger.web.weighting import (
    DEFAULT_WEIGHTS, EXCLUDED, LEVELS, PickWeights, expected_share,
    parse_new_factor, parse_weights, sample, weighted_order, weights_from_config,
)


def _rows(by_id=None, **kw_by_id):
    """{id: (rating, disliked, is_new)} -> a list of row dicts with file_path=id.
    Accepts a dict (for non-string ids) or keyword args (for string ids)."""
    by_id = dict(by_id or {}, **kw_by_id)
    return [{"file_path": i, "rating": rating, "disliked": disliked, "is_new": is_new}
            for i, (rating, disliked, is_new) in by_id.items()]


# ── parse_weights / parse_new_factor ────────────────────────────────────────

def test_parse_weights_from_string_and_list():
    assert parse_weights("0.1,0.5,1,1,3,6") == (0.1, 0.5, 1.0, 1.0, 3.0, 6.0)
    assert parse_weights([1, 2, 3, 4, 5, 6]) == (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)


def test_parse_weights_falls_back_on_garbage():
    assert parse_weights("nope") == DEFAULT_WEIGHTS
    assert parse_weights([1, 2, 3]) == DEFAULT_WEIGHTS              # wrong count
    assert parse_weights(None) == DEFAULT_WEIGHTS
    assert parse_weights("1,2,3,4,5,nan") == DEFAULT_WEIGHTS


def test_parse_weights_clamps_to_max():
    assert parse_weights("0,-5,1,1,3,999") == (0.0, 0.0, 1.0, 1.0, 3.0, 100.0)


def test_parse_new_factor_presets_and_numbers():
    assert parse_new_factor("never") == 0.0
    assert parse_new_factor("Less") == 0.5
    assert parse_new_factor("NEUTRAL") == 1.0
    assert parse_new_factor("more") == 2.0
    assert parse_new_factor("much_more") == 4.0
    assert parse_new_factor("2.5") == 2.5
    assert parse_new_factor("garbage") == 1.0
    assert parse_new_factor(-1) == 0.0
    assert parse_new_factor(999) == 100.0


def test_weights_from_config_owner_guest_is_flat():
    cfg = {"pick_use_ratings": True, "pick_weights": DEFAULT_WEIGHTS, "pick_new_factor": 1.0}
    admin = weights_from_config(cfg, "admin")
    guest = weights_from_config(cfg, "guest")
    assert admin.use_ratings is True
    assert guest.use_ratings is False
    assert guest.weight({"rating": 5}) == 1.0
    assert guest.weight({"rating": 1}) == 1.0


# ── PickWeights.weight ───────────────────────────────────────────────────────

def test_weight_disliked_is_excluded():
    w = PickWeights()
    assert w.weight({"disliked": True, "rating": 5}) == EXCLUDED


def test_weight_ratings_off_is_flat_but_respects_dislike():
    w = PickWeights(use_ratings=False)
    assert w.weight({"rating": 5}) == 1.0
    assert w.weight({"rating": 1}) == 1.0
    assert w.weight({"rating": None}) == 1.0
    assert w.weight({"disliked": True}) == EXCLUDED


def test_weight_new_factor_applies_only_to_unrated():
    w = PickWeights(new_factor=0.5)
    assert w.weight({"rating": None, "is_new": True}) == w.levels["unrated"] * 0.5
    assert w.weight({"rating": None, "is_new": False}) == w.levels["unrated"]
    assert w.weight({"rating": 5, "is_new": True}) == w.levels["5"]   # rated wins


# ── sample() / weighted_order() ─────────────────────────────────────────────

def test_sample_is_deterministic_with_a_seeded_rng():
    rows = _rows({i: (None, False, False) for i in range(20)})
    w = PickWeights()
    a = sample(rows, 5, w.weight, random.Random(42))
    b = sample(rows, 5, w.weight, random.Random(42))
    assert [r["file_path"] for r in a] == [r["file_path"] for r in b]


def test_sample_excluded_never_returned():
    rows = _rows({"good": (5, False, False), "bad": (None, True, False)})
    w = PickWeights()
    for _ in range(50):
        picked = sample(rows, 2, w.weight, random.Random())
        assert all(r["file_path"] != "bad" for r in picked)


def test_sample_k_greater_than_n_returns_everything_non_excluded():
    rows = _rows({"a": (3, False, False), "b": (None, True, False)})
    w = PickWeights()
    picked = sample(rows, 10, w.weight, random.Random(1))
    assert {r["file_path"] for r in picked} == {"a"}


def test_sample_zero_weight_fills_in_last():
    """A zero-weight ('Never' new, or a 0-weighted level) row is only drawn
    once every positive-weight row is already taken."""
    rows = _rows(pos1=(3, False, False), pos2=(3, False, False), zero=(None, False, True))
    w = PickWeights(levels={**dict(zip(LEVELS, DEFAULT_WEIGHTS))}, new_factor=0.0)
    picked = sample(rows, 2, w.weight, random.Random(7))
    assert {r["file_path"] for r in picked} == {"pos1", "pos2"}
    picked_all = sample(rows, 3, w.weight, random.Random(7))
    assert picked_all[-1]["file_path"] == "zero"


def test_weighted_order_keeps_every_non_excluded_item():
    rows = _rows({i: (i % 5 + 1, False, False) for i in range(15)})
    rows.append({"file_path": "hated", "rating": 5, "disliked": True, "is_new": False})
    w = PickWeights()
    order = weighted_order(rows, w.weight, random.Random(3))
    assert len(order) == 15
    assert all(r["file_path"] != "hated" for r in order)


def test_weighted_order_is_a_permutation_not_a_strict_sort():
    """A 1-star and a 5-star row are both present; a weighted permutation
    doesn't guarantee the 5-star is always first (unlike the old prefer-starred
    strict sort) — but across many seeds the 5-star should lead more often."""
    rows = _rows(one=(1, False, False), five=(5, False, False))
    w = PickWeights()
    five_first = sum(
        1 for seed in range(200)
        if weighted_order(rows, w.weight, random.Random(seed))[0]["file_path"] == "five")
    assert five_first > 100                      # more often than not, not "always"


# ── statistical tendency ─────────────────────────────────────────────────────

def test_five_star_drawn_roughly_proportional_to_weight():
    """~10k single draws from a pool of one 4★, one unrated: the 4★ (weight 3)
    should come up roughly 3x as often as the unrated one (weight 1), within a
    loose tolerance — this is a probability, not an exact count."""
    rows = _rows(fav=(4, False, False), plain=(None, False, False))
    w = PickWeights()
    rng = random.Random(2024)
    counts = {"fav": 0, "plain": 0}
    for _ in range(10_000):
        counts[sample(rows, 1, w.weight, rng)[0]["file_path"]] += 1
    ratio = counts["fav"] / counts["plain"]
    assert 2.3 < ratio < 3.7                      # target 3.0, generous band


# ── expected_share ───────────────────────────────────────────────────────────

def test_expected_share_sums_to_one():
    w = PickWeights()
    counts = {"1": 10, "2": 10, "3": 10, "unrated": 50, "new": 20, "4": 5, "5": 2}
    share = expected_share(counts, w)
    assert abs(sum(share.values()) - 1.0) < 1e-9
    assert share["5"] > share["1"]                # heavier level, smaller pool, still ahead


def test_expected_share_empty_pool_is_all_zero():
    w = PickWeights()
    share = expected_share({}, w)
    assert all(v == 0.0 for v in share.values())


# ── config migration (run_prefer_* -> pick_*) ───────────────────────────────

def test_settings_json_migrates_prefer_starred_false(tmp_path, monkeypatch):
    import json

    from bpm_tagger.config import build_config, load_settings_override

    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"run_prefer_starred": False}))
    monkeypatch.delenv("RUN_PREFER_STARRED", raising=False)
    monkeypatch.delenv("RUN_PREFER_FAMILIAR", raising=False)
    monkeypatch.delenv("PICK_USE_RATINGS", raising=False)
    cfg = build_config()
    cfg["db_path"] = str(tmp_path / "bpm.db")
    load_settings_override(cfg)
    assert cfg["pick_use_ratings"] is False
    assert "run_prefer_starred" not in cfg


def test_settings_json_migrates_prefer_familiar_true(tmp_path, monkeypatch):
    import json

    from bpm_tagger.config import build_config, load_settings_override

    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"run_prefer_familiar": True}))
    monkeypatch.delenv("RUN_PREFER_FAMILIAR", raising=False)
    monkeypatch.delenv("PICK_NEW_FACTOR", raising=False)
    cfg = build_config()
    cfg["db_path"] = str(tmp_path / "bpm.db")
    load_settings_override(cfg)
    assert cfg["pick_new_factor"] == 0.5
    assert "run_prefer_familiar" not in cfg


def test_env_run_prefer_starred_honoured_when_pick_use_ratings_unset(monkeypatch):
    from bpm_tagger.config import build_config

    monkeypatch.setenv("RUN_PREFER_STARRED", "false")
    monkeypatch.delenv("PICK_USE_RATINGS", raising=False)
    cfg = build_config()
    assert cfg["pick_use_ratings"] is False


def test_env_pick_use_ratings_wins_over_old_toggle(monkeypatch):
    from bpm_tagger.config import build_config

    monkeypatch.setenv("RUN_PREFER_STARRED", "false")
    monkeypatch.setenv("PICK_USE_RATINGS", "true")
    cfg = build_config()
    assert cfg["pick_use_ratings"] is True
