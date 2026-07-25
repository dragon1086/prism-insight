import prism_core.research as research


def test_research_package_exports_safe_foundation_contracts() -> None:
    expected = {
        "BacktestConfig",
        "CostConfig",
        "EvaluationWindow",
        "ExperimentRegistry",
        "ExperimentSpec",
        "PointInTimeBacktester",
        "ResearchCorporateAction",
        "ResearchPortfolio",
        "UniverseSnapshot",
    }

    assert expected <= set(research.__all__)
    assert all(hasattr(research, name) for name in expected)
    assert "OrderIntent" not in research.__all__
