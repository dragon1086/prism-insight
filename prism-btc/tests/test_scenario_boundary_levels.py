"""Historical boundary placeholders never become observed MA/price references."""
import pytest

from backtest.scenario_data import HistoricalScenarioData
from engine.scenario_ma_context import build_ma_structure_context
from live.scenario_explanation import source_from_structure
from tests.test_scenario_data import bars, ms


@pytest.mark.parametrize('index', [480, 485])
def test_only_observed_forming_bars_enter_structure_and_price_catalogue(index):
    snapshot = HistoricalScenarioData(bars()).snapshot(ms(bars().index[index]))
    context = dict(now=snapshot['as_of_ms']/1000)
    structure = build_ma_structure_context(snapshot, context)
    source = source_from_structure(structure, context)
    for frame in ('15m', '30m', '1h'):
        fact = snapshot['timeframes'][frame]
        synthetic = fact['forming']['observation_kind'] == 'synthetic_boundary'
        path = fact['ma_path']
        if synthetic:
            assert path['forming_point'] is None
            assert path['forming_gap'] is None
            assert 'forming' not in path['available_components']
        else:
            assert path['forming_point'] is not None
            assert path['forming_gap'] is not None
        points = structure['primary'][frame]['points']
        assert any(p['phase'] == 'FORMING' for p in points) is not synthetic
        assert any(p['phase'] == 'CONFIRMED' for p in points)
        assert any(ref.startswith('P:'+frame+':') and phase == 'FORMING'
                   for ref,price,phase,at in source['points']) is not synthetic
