"""Rating-weighted picking, shared by Run, Listen, "similar" and the Subsonic API.

Pure (no Flask, no DB): callers resolve each track's per-account ``rating``,
``disliked`` and ``is_new`` first (``BPMDatabase.annotate_marks``), then draw
with ``sample`` (k without replacement) or ``weighted_order`` (a weighted
permutation, for shuffles). Hard filters — the Run cadence rule, the
account's dislikes, exclude lists — happen before this; a disliked row that
reaches here anyway weighs nothing and is never returned.

Weight of a non-disliked track (docs/plans/ratings-weighted-picking.md):

    rated                 -> levels[rating]
    unrated, new          -> levels["unrated"] * new_factor
    unrated, not new      -> levels["unrated"]

Weight 0 means "only if nothing else is left": zero-weight rows come after
every positive-weight row, in random order, so a thin pool never starves a
refill. A disliked row weighs EXCLUDED (< 0) and is dropped outright. With
ratings off (or for the guest) every non-disliked row weighs 1.
"""

import math
import random
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional, Sequence, TypeVar

T = TypeVar("T")

# Order of the six weights in PICK_WEIGHTS / settings.json's pick_weights.
LEVELS = ("1", "2", "3", "unrated", "4", "5")
DEFAULT_WEIGHTS = (0.1, 0.5, 1.0, 1.0, 3.0, 6.0)      # "Moderate" (D7)
NEW_PRESETS = {"never": 0.0, "less": 0.5, "neutral": 1.0, "more": 2.0, "much_more": 4.0}
DEFAULT_NEW_FACTOR = 1.0
MAX_WEIGHT = 100.0
EXCLUDED = -1.0          # weight of a disliked row: never returned


def parse_weights(value) -> tuple[float, ...]:
    """Six weights from a list or a comma-separated string, each clamped to
    [0, MAX_WEIGHT]. Anything malformed falls back to the defaults whole."""
    try:
        if isinstance(value, str):
            value = [v for v in value.replace(";", ",").split(",") if v.strip()]
        vals = tuple(float(v) for v in value)
    except (TypeError, ValueError):
        return DEFAULT_WEIGHTS
    if len(vals) != len(LEVELS) or any(math.isnan(v) for v in vals):
        return DEFAULT_WEIGHTS
    return tuple(min(MAX_WEIGHT, max(0.0, v)) for v in vals)


def parse_new_factor(value) -> float:
    """A preset name or a number, clamped to [0, MAX_WEIGHT]."""
    if isinstance(value, str) and value.strip().lower() in NEW_PRESETS:
        return NEW_PRESETS[value.strip().lower()]
    try:
        v = float(value)
    except (TypeError, ValueError):
        return DEFAULT_NEW_FACTOR
    if math.isnan(v):
        return DEFAULT_NEW_FACTOR
    return min(MAX_WEIGHT, max(0.0, v))


@dataclass(frozen=True)
class PickWeights:
    use_ratings: bool = True
    levels: dict = field(default_factory=lambda: dict(zip(LEVELS, DEFAULT_WEIGHTS)))
    new_factor: float = DEFAULT_NEW_FACTOR

    def weight(self, row: dict) -> float:
        if row.get("disliked"):
            return EXCLUDED
        if not self.use_ratings:
            return 1.0
        rating = row.get("rating")
        if rating:
            return float(self.levels.get(str(int(rating)), 1.0))
        base = float(self.levels["unrated"])
        return base * self.new_factor if row.get("is_new") else base


def weights_from_config(cfg: dict, owner: Optional[str] = None) -> PickWeights:
    """The admin-set global weights (D7). The shared guest picks flat (D6)."""
    use = bool(cfg.get("pick_use_ratings", True)) and owner != "guest"
    levels = dict(zip(LEVELS, parse_weights(cfg.get("pick_weights", DEFAULT_WEIGHTS))))
    return PickWeights(use_ratings=use, levels=levels,
                       new_factor=parse_new_factor(cfg.get("pick_new_factor", DEFAULT_NEW_FACTOR)))


def _keyed(items: Iterable[T], weight_fn: Callable[[T], float],
           rng: random.Random) -> tuple[list[tuple[float, T]], list[T]]:
    """Efraimidis–Spirakis keys (log form: log(u) / w, larger wins) for the
    positive-weight items; zero-weight items returned separately, shuffled.
    Negative (EXCLUDED) items are dropped."""
    keyed, zero = [], []
    for it in items:
        w = weight_fn(it)
        if w > 0:
            u = rng.random() or 1e-300
            keyed.append((math.log(u) / w, it))
        elif w == 0:
            zero.append(it)
    keyed.sort(key=lambda kv: kv[0], reverse=True)
    rng.shuffle(zero)
    return keyed, zero


def sample(items: Iterable[T], k: int, weight_fn: Callable[[T], float],
           rng: Optional[random.Random] = None) -> list[T]:
    """Up to ``k`` items drawn without replacement, P ∝ weight. Returned in
    draw order (heaviest-luck first); callers that want a playback shuffle
    shuffle the result."""
    if k <= 0:
        return []
    keyed, zero = _keyed(items, weight_fn, rng or random.Random())
    out = [it for _, it in keyed[:k]]
    if len(out) < k:
        out.extend(zero[:k - len(out)])
    return out


def weighted_order(items: Iterable[T], weight_fn: Callable[[T], float],
                   rng: Optional[random.Random] = None) -> list[T]:
    """Every (non-disliked) item, in a weighted random order: heavier items
    tend to come earlier. Used for playlist / library shuffles (D12)."""
    keyed, zero = _keyed(items, weight_fn, rng or random.Random())
    return [it for _, it in keyed] + zero


def expected_share(counts: dict, weights: PickWeights) -> dict:
    """Expected share of single picks per level for a pool with ``counts``
    ({'1'..'5', 'unrated', 'new'} -> number of tracks; 'unrated' excludes
    'new'). Drives the Settings preview. Levels with no tracks get 0."""
    rows = {lvl: (counts.get(lvl, 0), weights.weight(
        {"rating": int(lvl)} if lvl.isdigit() else {"is_new": lvl == "new"}))
        for lvl in ("1", "2", "3", "unrated", "new", "4", "5")}
    rows = {lvl: (n, max(0.0, w)) for lvl, (n, w) in rows.items()}
    total = sum(n * w for n, w in rows.values())
    if total <= 0:
        return {lvl: 0.0 for lvl in rows}
    return {lvl: n * w / total for lvl, (n, w) in rows.items()}


def rows_weight_fn(weights: PickWeights, key: Callable[[T], dict] = lambda x: x):
    """weight_fn for sample()/weighted_order() over rows or (row, ...) tuples."""
    return lambda it: weights.weight(key(it))


__all__: Sequence[str] = (
    "LEVELS", "DEFAULT_WEIGHTS", "NEW_PRESETS", "PickWeights", "weights_from_config",
    "parse_weights", "parse_new_factor", "sample", "weighted_order", "expected_share",
    "rows_weight_fn",
)
