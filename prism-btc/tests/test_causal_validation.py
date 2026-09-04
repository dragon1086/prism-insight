from __future__ import annotations

from research.causal_validation import purged_chronological_splits


def test_purged_split_keeps_label_horizon_out_of_later_windows() -> None:
    splits = purged_chronological_splits(
        140,
        train_size=60,
        validation_size=20,
        test_size=20,
        label_horizon=6,
        execution_lag=1,
        step_size=20,
    )

    assert len(splits) == 2
    first = splits[0]
    assert first.embargo_size == 7
    assert first.train == (0, 60)
    assert first.validation == (67, 87)
    assert first.test == (94, 114)
    assert first.train[1] + first.embargo_size <= first.validation[0]
    assert first.validation[1] + first.embargo_size <= first.test[0]
    assert splits[1].train == (0, 80)


def test_purged_split_returns_empty_when_sample_cannot_fit_all_windows() -> None:
    assert purged_chronological_splits(
        50,
        train_size=30,
        validation_size=10,
        test_size=10,
        label_horizon=5,
        execution_lag=1,
    ) == []


def test_rolling_train_window_does_not_expand() -> None:
    splits = purged_chronological_splits(
        100,
        train_size=30,
        validation_size=10,
        test_size=10,
        label_horizon=2,
        execution_lag=1,
        step_size=10,
        expanding=False,
    )

    assert splits[0].train == (0, 30)
    assert splits[1].train == (10, 40)
