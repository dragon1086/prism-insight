"""Chronological train/validation/test splits with a conservative embargo."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PurgedSplit:
    """Half-open integer ranges suitable for ``iloc`` slicing."""

    train: tuple[int, int]
    validation: tuple[int, int]
    test: tuple[int, int]
    embargo_size: int


def purged_chronological_splits(
    n_samples: int,
    *,
    train_size: int,
    validation_size: int,
    test_size: int,
    label_horizon: int,
    execution_lag: int = 1,
    step_size: int | None = None,
    expanding: bool = True,
) -> list[PurgedSplit]:
    """Return causal windows separated by ``label_horizon + execution_lag``.

    The embargo prevents a label or delayed execution originating in an earlier
    window from reaching observations in the next window.  No shuffling is
    allowed.  An empty list means the sample cannot support one complete split.
    """
    sizes = {
        "n_samples": n_samples,
        "train_size": train_size,
        "validation_size": validation_size,
        "test_size": test_size,
        "label_horizon": label_horizon,
        "execution_lag": execution_lag,
    }
    if any(int(value) < 0 for value in sizes.values()):
        raise ValueError("split sizes must be non-negative")
    if min(train_size, validation_size, test_size) <= 0:
        raise ValueError("train, validation, and test sizes must be positive")
    if label_horizon <= 0:
        raise ValueError("label_horizon must be positive")
    step = test_size if step_size is None else int(step_size)
    if step <= 0:
        raise ValueError("step_size must be positive")

    embargo = int(label_horizon) + int(execution_lag)
    splits: list[PurgedSplit] = []
    train_start = 0
    train_end = int(train_size)
    while True:
        validation_start = train_end + embargo
        validation_end = validation_start + int(validation_size)
        test_start = validation_end + embargo
        test_end = test_start + int(test_size)
        if test_end > int(n_samples):
            break
        splits.append(
            PurgedSplit(
                train=(train_start, train_end),
                validation=(validation_start, validation_end),
                test=(test_start, test_end),
                embargo_size=embargo,
            )
        )
        if not expanding:
            train_start += step
        train_end += step
    return splits


__all__ = ["PurgedSplit", "purged_chronological_splits"]
