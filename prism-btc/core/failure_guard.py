"""Pure frozen Round-10 failure-cluster classifier.

The classifier is observational only.  It has no authority to reject an entry;
``live.failure_observer`` records what a C1 pyramid block would have done.
Missing or non-finite inputs always fail open with ``None``.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

MODEL_VERSION = "round10-2022_2023-k4-v1"
WARNING_CLUSTER = 1
FEATURE_NAMES = (
    "ts_4h",
    "ts_1d",
    "extension_4h",
    "ret_24h",
    "atr_ratio_4h",
    "volume_ratio_4h",
    "range_ratio_4h",
    "funding_z",
    "oi_change_6h",
    "lane_swing",
)

_CENTER = (
    0.22077473107425338,
    1.246810475018748,
    2.110552182959182,
    0.01938826931093446,
    0.016609123752453634,
    1.336932547270516,
    0.8772517056629541,
    0.0031752663463547647,
    0.0023003330621647056,
    1.0,
)
_SCALE = (
    2.268875188828862,
    1.247072666239262,
    3.1264448742312525,
    0.03629689657773766,
    0.00659549986886329,
    1.2111669763772843,
    0.7625704941851653,
    0.4827920803832078,
    0.035270482217584664,
    1.0,
)
_CENTROIDS = (
    (
        -0.015240024104227964, -0.04173435276209658,
        -0.2442122150187716, 0.6761481192936055,
        0.0022525180149838005, -0.4523529452117481,
        -0.45616311265694065, -15.314710921128565,
        -0.7807826119398328, 0.0,
    ),
    (
        0.8472049189416202, 0.2246601144579282,
        0.4200830252419811, 0.28408325824904473,
        -0.012728576869714528, -0.04706462933707095,
        -0.013653822682535403, 3.3813532999092084,
        -0.1673738536818687, -0.7,
    ),
    (
        -0.00039321081039055215, -0.562697231003933,
        0.5568184298093494, 0.6749680875600186,
        -1.1195455650222639, 9.418861176745938,
        3.7644488809697014, -4.521995778341833,
        2.2019875994783384, 0.0,
    ),
    (
        0.3787380361657339, 0.16450867977738248,
        0.040447084662658846, 0.20468846520213194,
        0.062380613878293543, 0.1292265386756466,
        0.18483437885953233, -0.18879043319577798,
        -0.02460243905914311, -0.40625,
    ),
)


@dataclass(frozen=True)
class FailureGuardFeatures:
    ts_4h: float | None
    ts_1d: float | None
    extension_4h: float | None
    ret_24h: float | None
    atr_ratio_4h: float | None
    volume_ratio_4h: float | None
    range_ratio_4h: float | None
    funding_z: float | None
    oi_change_6h: float | None
    lane_swing: float | None = 0.0

    def to_dict(self) -> dict[str, float | None]:
        return asdict(self)


def classify_failure_cluster(features: FailureGuardFeatures) -> int | None:
    """Return nearest frozen centroid, or ``None`` on any invalid input."""
    raw = features.to_dict()
    values: list[float] = []
    for name in FEATURE_NAMES:
        value = raw.get(name)
        if value is None:
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(numeric):
            return None
        values.append(numeric)
    normalized = [
        (value - center) / scale
        for value, center, scale in zip(values, _CENTER, _SCALE)
    ]
    distances = [
        sum((value - centroid_value) ** 2
            for value, centroid_value in zip(normalized, centroid))
        for centroid in _CENTROIDS
    ]
    return int(min(range(len(distances)), key=distances.__getitem__))


def should_observe_pyramid_block(
    *, cluster: int | None, tranche_index: int,
) -> bool:
    """C1 affects add-on tranches only; initial entries always pass through."""
    return cluster == WARNING_CLUSTER and int(tranche_index) > 0
