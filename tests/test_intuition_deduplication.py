"""Offline regressions for cumulative intuition evidence and semantic reconciliation."""
import json
import importlib.util
from pathlib import Path
import sqlite3
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tracking.compression import CompressionManager
from tracking.db_schema import TABLE_TRADING_INTUITIONS


def test_compression_model_cannot_bypass_database_validation(monkeypatch):
    module = ModuleType('mcp_agent.agents.agent')
    module.Agent = lambda **kwargs: SimpleNamespace(**kwargs)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    path = Path(__file__).parents[1] / 'cores/agents/memory_compressor_agent.py'
    spec = importlib.util.spec_from_file_location('compression_factory_under_test', path)
    factory = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(factory)
    for language in ('ko', 'en'):
        assert factory.create_memory_compressor_agent(language).server_names == []


class ReviewedMemoryFixture(CompressionManager):
    """Dedup tests seed already-reviewed records; policy approval has separate regressions."""

    def _save_intuition(self, intuition, source_ids):
        inserted = super()._save_intuition(intuition, source_ids)
        reviewed = {'version': 1, 'status': 'current_pipeline', 'market': 'KR', 'stage': 'batch_buy',
                    'required_capabilities': ['batch_report', 'entry_advisory'], 'reason': 'Offline fixture review.'}
        self.conn.execute('UPDATE trading_intuitions SET application_context = ?', (json.dumps(reviewed),))
        return inserted


def manager():
    conn = sqlite3.connect(':memory:')
    conn.executescript(TABLE_TRADING_INTUITIONS)
    conn.execute("ALTER TABLE trading_intuitions ADD COLUMN market TEXT DEFAULT 'KR'")
    return ReviewedMemoryFixture(conn.cursor(), conn, enable_journal=True)


def rule(**overrides):
    return dict(category='pattern', subcategory='', condition='변동성 구간 추격 진입',
                insight='추세 정렬 전 추격 진입 금지', confidence=.7,
                supporting_trades=2, success_rate=.5, **overrides)


def test_success_rate_is_measured_from_cited_kr_trades_not_llm_guess():
    mgr = manager()
    mgr.conn.execute('CREATE TABLE trading_journal (id INTEGER PRIMARY KEY, profit_rate REAL, market TEXT)')
    mgr.conn.executemany('INSERT INTO trading_journal VALUES (?, ?, ?)',
                         [(1, 5.0, 'KR'), (2, -3.0, 'KR'), (3, -1.0, 'KR'), (4, 9.0, 'US')])
    guessed = rule()
    guessed['success_rate'] = 1.0
    mgr._save_intuition(guessed, [1, 2])
    assert mgr.conn.execute('SELECT success_rate FROM trading_intuitions').fetchone() == (.5,)
    mgr._save_intuition(rule(), [3, 4])  # the US row never counts toward a KR lesson
    assert mgr.conn.execute('SELECT success_rate FROM trading_intuitions').fetchone() == (.33,)


def test_repeated_corpus_does_not_inflate_evidence_or_confidence():
    mgr = manager()
    assert mgr._save_intuition(rule(), [1, 2])
    changed = rule()
    changed['confidence'] = .99
    assert not mgr._save_intuition(changed, [1, 2])
    row = mgr.conn.execute('SELECT supporting_trades, confidence, source_journal_ids FROM trading_intuitions').fetchone()
    assert row == (2, .7, '[1, 2]')
    mgr._save_intuition(rule(), [2, 3])
    assert mgr.conn.execute('SELECT supporting_trades, source_journal_ids FROM trading_intuitions').fetchone() == (3, '[1, 2, 3]')


def test_semantic_merge_retains_canonical_text_and_archives_duplicates():
    mgr = manager()
    mgr._save_intuition(rule(), [1, 2])
    variant = rule()
    variant.update(condition='변동성 높은 구간 조급한 단기 진입', insight='변동성 축소와 추세 정렬 전 FOMO 진입 금지')
    mgr._save_intuition(variant, [2, 3])
    assert mgr._consolidate_intuitions([{'canonical_id': 2, 'duplicate_ids': [1]}]) == 1
    rows = mgr.conn.execute('SELECT id, condition, is_active, supporting_trades, source_journal_ids FROM trading_intuitions ORDER BY id').fetchall()
    assert rows[0][2] == 0
    assert rows[1][1:] == (variant['condition'], 1, 3, '[1, 2, 3]')
    assert mgr._consolidate_intuitions([{'canonical_id': 2, 'duplicate_ids': [1]}]) == 0


def test_market_scope_and_inactive_boundaries():
    mgr = manager()
    mgr._save_intuition(rule(), [1, 2])
    mgr.conn.execute("UPDATE trading_intuitions SET market='US'")
    assert mgr._save_intuition(rule(), [3, 4])
    assert mgr._consolidate_intuitions([{'canonical_id': 2, 'duplicate_ids': [1]}]) == 0
    other = rule()
    other.update(category='sector', condition='다른 조건')
    mgr._save_intuition(other, [5, 6])
    mgr.conn.execute("UPDATE trading_intuitions SET scope='sector' WHERE id=3")
    assert mgr._consolidate_intuitions([{'canonical_id': 2, 'duplicate_ids': [3]}]) == 0
    prompt = mgr._build_layer3_prompt('records', 2)
    assert 'duplicate_groups' in prompt and 'source_journal_ids' in prompt
    assert '"id": 1,' not in prompt


def test_explicit_evidence_must_be_subset_of_corpus():
    mgr = manager()
    assert not mgr._save_intuition(rule(source_journal_ids=[99, 100]), [1, 2])
    assert mgr.conn.execute('SELECT COUNT(*) FROM trading_intuitions').fetchone()[0] == 0


def test_empty_market_is_not_implicitly_korean():
    mgr = manager()
    mgr._save_intuition(rule(), [1, 2])
    mgr.conn.execute("UPDATE trading_intuitions SET market=''")
    assert mgr._active_intuitions() == []


def test_union_preserves_originals_and_legacy_provenance_is_not_verified_support():
    mgr = manager()
    mgr._save_intuition(rule(), [1, 2])
    variant = rule()
    variant.update(category='market', subcategory='변동성 구간', condition='조급한 FOMO 진입', insight='추세 정렬 전 관망, 첫 진입은 비중 축소')
    mgr._save_intuition(variant, [2, 3])
    mgr.conn.execute("UPDATE trading_intuitions SET verified_source_journal_ids=NULL, source_journal_ids=?, supporting_trades=2", (json.dumps(list(range(40))),))
    group = {'canonical_id': 2, 'duplicate_ids': [1], 'canonical_condition': '변동성 구간 조급한 FOMO 추격 진입',
             'canonical_insight': '추세 정렬 전 추격 진입 금지 및 관망, 첫 진입은 비중 축소'}
    assert mgr._consolidate_intuitions([group]) == 2
    rows = mgr.conn.execute('SELECT condition, insight, is_active, supporting_trades FROM trading_intuitions ORDER BY id').fetchall()
    assert rows[0][:3] == (rule()['condition'], rule()['insight'], 0)
    assert rows[1][:3] == (variant['condition'], variant['insight'], 0)
    assert rows[2] == (group['canonical_condition'], group['canonical_insight'], 1, 2)


def test_numeric_timeframe_conflict_fails_closed():
    mgr = manager()
    first, second = rule(), rule()
    first['condition'] = '3일 하락'
    second['condition'] = '30일 하락'
    mgr._save_intuition(first, [1, 2])
    mgr._save_intuition(second, [3, 4])
    assert mgr._consolidate_intuitions([{'canonical_id': 1, 'duplicate_ids': [2]}]) == 0


def test_maintenance_prompt_has_no_journal_extraction_gate():
    mgr = manager()
    mgr._save_intuition(rule(), [1, 2])
    prompt = mgr._build_reconciliation_prompt()
    assert 'No new journal records are needed' in prompt
    assert 'duplicate_groups' in prompt and 'canonical_insight' in prompt
    assert '"id": 1' in prompt
    assert 'new_intuitions' not in prompt and 'patterns appearing 2+' not in prompt


def stub_llm_dependencies(monkeypatch, llm):
    params = ModuleType('mcp_agent.workflows.llm.augmented_llm')
    params.RequestParams = lambda **kw: kw
    monkeypatch.setitem(sys.modules, params.__name__, params)
    openai = ModuleType('mcp_agent.workflows.llm.augmented_llm_openai')
    openai.OpenAIAugmentedLLM = object
    monkeypatch.setitem(sys.modules, openai.__name__, openai)
    agent = AsyncMock()
    agent.__aenter__.return_value = agent
    agent.attach_llm.return_value = llm
    factory = ModuleType('cores.agents.memory_compressor_agent')
    factory.create_memory_compressor_agent = lambda language: agent
    monkeypatch.setitem(sys.modules, factory.__name__, factory)
    agent_module = ModuleType('mcp_agent.agents.agent')
    agent_module.Agent = lambda **kwargs: agent
    monkeypatch.setitem(sys.modules, agent_module.__name__, agent_module)


@pytest.mark.asyncio
async def test_opposite_action_rejected_by_separate_review(monkeypatch):
    mgr = manager()
    mgr._save_intuition(rule(), [1, 2])
    opposite = rule()
    opposite['insight'] = '추세 정렬 전 추격 진입 허용'
    mgr._save_intuition(opposite, [3, 4])
    llm = SimpleNamespace(generate_str=AsyncMock(return_value='{"approved_groups": []}'))
    stub_llm_dependencies(monkeypatch, llm)
    proposed = [{'canonical_id': 1, 'duplicate_ids': [2]}]
    approved = await mgr._verify_duplicate_groups(llm, proposed)
    assert mgr._consolidate_intuitions(approved) == 0
    assert 'opposite actions' in llm.generate_str.call_args.kwargs['message']
    assert mgr.conn.execute('SELECT COUNT(*) FROM trading_intuitions WHERE is_active=1').fetchone()[0] == 2


@pytest.mark.asyncio
async def test_refresh_consolidates_without_new_journal_and_rejects_missing_evidence(monkeypatch):
    mgr = manager()
    mgr.conn.execute('CREATE TABLE trading_journal (id INTEGER, ticker TEXT, company_name TEXT, trade_date TEXT, profit_rate REAL, compressed_summary TEXT, one_line_summary TEXT, pattern_tags TEXT, buy_scenario TEXT, market TEXT)')
    mgr._save_intuition(rule(), [1, 2])
    variant = rule()
    variant['condition'] = '변동성 높은 구간 추격 진입'
    mgr._save_intuition(variant, [2, 3])
    groups = [{'canonical_id': 1, 'duplicate_ids': [2]}]
    llm = SimpleNamespace(generate_str=AsyncMock(side_effect=[
        json.dumps({'new_intuitions': [dict(rule(), condition='근거 없는 새 규칙')], 'duplicate_groups': groups}),
        json.dumps({'approved_groups': groups}),
    ]))
    stub_llm_dependencies(monkeypatch, llm)
    result = await mgr.refresh_intuitions()
    assert result['errors'] == []
    assert result['corpus'] == 0 and result['intuitions_generated'] == 0
    assert result['intuitions_consolidated'] == 1
    assert mgr.conn.execute('SELECT COUNT(*) FROM trading_intuitions WHERE is_active=1').fetchone()[0] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('already_exists', [False, True])
async def test_same_batch_paraphrases_reconciled_after_ids_assigned(monkeypatch, already_exists):
    mgr = manager()
    first, second = rule(source_journal_ids=[1, 2]), rule(source_journal_ids=[2, 3])
    second['condition'] = '변동성 높은 구간 추격 진입'
    groups = [{'canonical_id': 2, 'duplicate_ids': [1]}]
    llm = SimpleNamespace(generate_str=AsyncMock(side_effect=[
        json.dumps({'duplicate_groups': groups}), json.dumps({'approved_groups': groups}),
    ]))
    stub_llm_dependencies(monkeypatch, llm)
    if already_exists:
        mgr._save_intuition(first, [1, 2])
    result = await mgr._apply_intuition_response(llm, {'new_intuitions': [second] if already_exists else [first, second]}, [1, 2, 3])
    assert result['intuitions_consolidated'] == 1
    assert mgr.conn.execute('SELECT supporting_trades FROM trading_intuitions WHERE is_active=1').fetchall() == [(3,)]


@pytest.mark.asyncio
async def test_evidence_update_precedes_deactivation(monkeypatch):
    mgr = manager()
    mgr._save_intuition(rule(), [1, 2])
    variant = rule()
    variant['condition'] = '변동성 높은 구간 추격 진입'
    mgr._save_intuition(variant, [2, 3])
    update = rule(source_journal_ids=[3, 4], existing_intuition_id=1)
    groups = [{'canonical_id': 2, 'duplicate_ids': [1]}]
    llm = SimpleNamespace(generate_str=AsyncMock(return_value=json.dumps({'approved_groups': groups})))
    stub_llm_dependencies(monkeypatch, llm)
    await mgr._apply_intuition_response(llm, {'new_intuitions': [update], 'duplicate_groups': groups}, [3, 4])
    assert mgr.conn.execute('SELECT supporting_trades FROM trading_intuitions WHERE is_active=1').fetchall() == [(4,)]


@pytest.mark.asyncio
async def test_user_three_intuitions_full_refresh_preserves_all_caveats(monkeypatch):
    mgr = manager()
    mgr.conn.execute('CREATE TABLE trading_journal (id INTEGER, ticker TEXT, company_name TEXT, trade_date TEXT, profit_rate REAL, compressed_summary TEXT, one_line_summary TEXT, pattern_tags TEXT, buy_scenario TEXT, market TEXT)')
    originals = [
        ('pattern', 'fomo_entry', '변동성 구간 조급한 추격 진입·초단기 보유', '단기 피탈 위험 = 추세 정렬·변동성 축소·지지 확인 전 추격 진입 금지'),
        ('pattern', 'volatility_whipsaw', '변동성 구간에서 조급한 추격 진입·초단기 보유', '단기 피탈 위험 = 추세 정렬과 눌림 확인 전 FOMO 진입 금지, 첫 진입은 비중 축소 또는 관망 우선'),
        ('market', '변동성 구간', '변동성 높은 구간의 조급한 단기 진입', '단기 피탈 가능성 매우 높음 = 변동성 축소와 추세 정렬 전에는 진입을 늦추고, 당일성 FOMO 진입은 피한다'),
    ]
    for index, (category, subcategory, condition, insight) in enumerate(originals):
        item = rule()
        item.update(category=category, subcategory=subcategory, condition=condition, insight=insight)
        mgr._save_intuition(item, [index + 1, index + 2])
    group = {'canonical_id': 1, 'duplicate_ids': [2, 3],
             'canonical_condition': '변동성 구간 조급한 추격·단기 진입 및 초단기 보유',
             'canonical_insight': '단기 피탈 위험: 추세 정렬·변동성 축소·지지와 눌림 확인 전 추격·FOMO 진입을 피하고 진입을 늦춘다. 첫 진입은 비중 축소 또는 관망을 우선하며 당일성 FOMO 진입은 피한다.'}
    llm = SimpleNamespace(generate_str=AsyncMock(side_effect=[
        json.dumps({'new_intuitions': [], 'duplicate_groups': [group]}),
        json.dumps({'approved_groups': [group]}),
    ]))
    stub_llm_dependencies(monkeypatch, llm)
    first = await mgr.refresh_intuitions()
    assert first['errors'] == []
    rows = mgr.conn.execute('SELECT category, subcategory, condition, insight, is_active FROM trading_intuitions ORDER BY id').fetchall()
    assert [row[:4] for row in rows[:3]] == originals
    assert [row[4] for row in rows] == [0, 0, 0, 1]
    assert rows[3][3] == group['canonical_insight']
    second = await mgr.refresh_intuitions()
    assert second['reason'] == 'insufficient_corpus'
    assert mgr.conn.execute('SELECT supporting_trades FROM trading_intuitions WHERE is_active=1').fetchall() == [(4,)]


@pytest.mark.asyncio
async def test_review_outage_retains_originals_and_reports_partial_counts(monkeypatch):
    mgr = manager()
    mgr._save_intuition(rule(), [1, 2])
    second = rule(source_journal_ids=[2, 3])
    second['condition'] = '변동성 높은 구간 추격 진입'
    llm = SimpleNamespace(generate_str=AsyncMock(side_effect=RuntimeError('review unavailable')))
    stub_llm_dependencies(monkeypatch, llm)
    result = await mgr._apply_intuition_response(llm, {'new_intuitions': [second]}, [1, 2, 3])
    assert result['intuitions_generated'] == 1 and result['intuitions_consolidated'] == 0
    assert result['errors'] == ['intuition_reconciliation_deferred: review unavailable']
    assert mgr.conn.execute('SELECT COUNT(*) FROM trading_intuitions WHERE is_active=1').fetchone()[0] == 2


@pytest.mark.asyncio
async def test_refresh_filters_market_before_limit_and_preserves_summary_fallback(monkeypatch):
    mgr = manager()
    mgr.conn.execute('CREATE TABLE trading_journal (id INTEGER, ticker TEXT, company_name TEXT, trade_date TEXT, profit_rate REAL, compressed_summary TEXT, one_line_summary TEXT, pattern_tags TEXT, buy_scenario TEXT, market TEXT)')
    mgr.conn.executemany('INSERT INTO trading_journal VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', [
        (1, 'KR_TEST', '한국', '2099-01-01', 1., None, 'fallback KR evidence', '[]', '{}', 'KR'),
        (2, 'US_TEST', '미국', '2099-01-02', 2., None, 'US evidence', '[]', '{}', 'US'),
    ])
    llm = SimpleNamespace(generate_str=AsyncMock(return_value='{"new_intuitions": [], "duplicate_groups": []}'))
    stub_llm_dependencies(monkeypatch, llm)
    result = await mgr.refresh_intuitions(limit=1, min_entries=1)
    assert result['errors'] == [] and result['corpus'] == 1
    prompt = llm.generate_str.call_args.kwargs['message']
    assert 'fallback KR evidence' in prompt and 'US evidence' not in prompt


@pytest.mark.parametrize('rewrite', [False, True])
def test_merge_preserves_earliest_creation_and_latest_actual_validation(rewrite):
    mgr = manager()
    mgr._save_intuition(rule(), [1, 2])
    other = rule()
    other['condition'] = '변동성 높은 구간 추격 진입'
    mgr._save_intuition(other, [2, 3])
    mgr.conn.execute("UPDATE trading_intuitions SET created_at='2026-01-01', last_validated_at='2026-02-01' WHERE id=1")
    mgr.conn.execute("UPDATE trading_intuitions SET created_at='2026-02-01', last_validated_at='2026-09-27' WHERE id=2")
    group = {'canonical_id': 1, 'duplicate_ids': [2]}
    if rewrite:
        group['canonical_condition'] = '변동성이 높은 구간의 추격 진입'
    mgr._consolidate_intuitions([group])
    assert mgr.conn.execute('SELECT created_at, last_validated_at FROM trading_intuitions WHERE is_active=1').fetchall() == [('2026-01-01', '2026-09-27')]


@pytest.mark.asyncio
async def test_model_approved_merge_cannot_drop_observed_support_qualifier(monkeypatch):
    mgr = manager()
    original, paraphrase = rule(), rule()
    original['insight'] = '추세 정렬·변동성 축소·지지 확인 전 추격 진입 금지'
    paraphrase['insight'] = '추세 정렬과 눌림 확인 전 FOMO 진입 금지, 첫 진입은 비중 축소 또는 관망 우선, 당일성 FOMO 금지'
    mgr._save_intuition(original, [1, 2])
    mgr._save_intuition(paraphrase, [2, 3])
    group = {'canonical_id': 1, 'duplicate_ids': [2],
             'canonical_insight': '추세 정렬·변동성 축소·눌림 확인 전 추격 금지. 첫 진입은 비중 축소 또는 관망 우선, 당일성 FOMO 금지'}
    llm = SimpleNamespace(generate_str=AsyncMock(return_value=json.dumps({'approved_groups': [group]})))
    stub_llm_dependencies(monkeypatch, llm)
    approved = await mgr._verify_duplicate_groups(llm, [group])
    assert approved == [group]  # Reproduce the real model's incorrect approval.
    assert mgr._consolidate_intuitions(approved) == 0
    assert mgr.conn.execute('SELECT COUNT(*) FROM trading_intuitions WHERE is_active=1').fetchone()[0] == 2
    prompt = mgr._build_reconciliation_prompt()
    assert 'required_qualifiers' in prompt and '지지 확인 / support confirmation' in prompt
    group['canonical_insight'] = group['canonical_insight'].replace('눌림 확인', '지지 확인과 눌림 확인')
    assert mgr._consolidate_intuitions([group]) == 2


@pytest.mark.asyncio
async def test_semantic_review_allows_conditional_numeric_union(monkeypatch):
    mgr = manager()
    first, second = rule(), rule()
    first.update(condition='추세 실패 시', insight='원래 손절 계획을 지킨다')
    second.update(condition='50일선 지지가 깨진 추세 실패 시', insight='손절 계획을 지키며 재진입 전 지지 회복을 확인한다')
    mgr._save_intuition(first, [1, 2])
    mgr._save_intuition(second, [2, 3])
    group = {'canonical_id': 1, 'duplicate_ids': [2], 'canonical_condition': '추세 실패 시',
             'canonical_insight': '원래 손절 계획을 지킨다. 50일선 지지가 깨진 경우에는 재진입 전 지지 회복을 확인한다.'}
    review = {'reviews': [{'canonical_id': 1, 'approved': True, 'reason': 'Same stop-plan discipline, condition retained.',
                           'source_coverage': [
                               {'source_id': 1, 'canonical_excerpt': '원래 손절 계획을 지킨다'},
                               {'source_id': 2, 'canonical_excerpt': '50일선 지지가 깨진 경우에는 재진입 전 지지 회복을 확인한다'},
                           ], 'qualifier_coverage': [{'qualifier': '지지 확인 / support confirmation',
                                                     'canonical_excerpt': '50일선 지지가 깨진 경우에는 재진입 전 지지 회복을 확인한다'}]}]}
    reviewer = SimpleNamespace(generate_str=AsyncMock(return_value=json.dumps(review)))
    stub_llm_dependencies(monkeypatch, reviewer)
    proposer = SimpleNamespace(generate_str=AsyncMock(side_effect=AssertionError('Verifier must be independent')))
    approved = await mgr._verify_duplicate_groups(proposer, [group])
    assert approved == [group]
    assert mgr._consolidate_intuitions(approved) == 2
    assert mgr.conn.execute('SELECT source_journal_ids FROM trading_intuitions WHERE is_active=1').fetchone()[0] == '[1, 2, 3]'


@pytest.mark.asyncio
async def test_approval_binds_exact_canonical_text_and_source_snapshot(monkeypatch):
    mgr = manager()
    first, second = rule(), rule()
    second['condition'] = '변동성 높은 구간 추격 진입'
    mgr._save_intuition(first, [1, 2])
    mgr._save_intuition(second, [2, 3])
    group = {'canonical_id': 1, 'duplicate_ids': [2], 'canonical_insight': '정렬된 추세를 확인한 뒤 진입한다'}
    review = {'reviews': [{'canonical_id': 1, 'approved': True, 'reason': 'Equivalent paraphrase.', 'source_coverage': [
        {'source_id': 1, 'canonical_excerpt': '정렬된 추세를 확인한 뒤 진입한다'},
        {'source_id': 2, 'canonical_excerpt': '정렬된 추세를 확인한 뒤 진입한다'}],
                           'qualifier_coverage': [{'qualifier': '추세 정렬 / trend alignment',
                                                   'canonical_excerpt': '정렬된 추세를 확인한 뒤 진입한다'}]}]}
    llm = SimpleNamespace(generate_str=AsyncMock(return_value=json.dumps(review)))
    stub_llm_dependencies(monkeypatch, llm)
    assert await mgr._verify_duplicate_groups(llm, [group]) == [group]
    tampered = dict(group, canonical_insight='추세 확인 없이 진입한다')
    assert mgr._consolidate_intuitions([tampered]) == 0
    mgr.conn.execute("UPDATE trading_intuitions SET insight='추격을 허용한다' WHERE id=2")
    assert mgr._consolidate_intuitions([group]) == 0


@pytest.mark.asyncio
async def test_semantic_review_rejects_conflicting_thresholds_and_incomplete_coverage(monkeypatch):
    mgr = manager()
    first, second = rule(), rule()
    first.update(condition='추세 실패 시', insight='손절선 5%를 적용한다')
    second.update(condition='추세 실패 시', insight='손절선 10%를 적용한다')
    mgr._save_intuition(first, [1, 2])
    mgr._save_intuition(second, [2, 3])
    group = {'canonical_id': 1, 'duplicate_ids': [2], 'canonical_insight': '손절선은 5% 또는 10%를 적용한다'}
    llm = SimpleNamespace(generate_str=AsyncMock(return_value=json.dumps({'reviews': [
        {'canonical_id': 1, 'approved': False, 'reason': 'Same condition has incompatible thresholds, not synonymous advice.'}]})))
    stub_llm_dependencies(monkeypatch, llm)
    assert await mgr._verify_duplicate_groups(None, [group]) == []
    llm.generate_str.return_value = json.dumps({'reviews': [
        {'canonical_id': 1, 'approved': True, 'reason': 'Incomplete source audit.',
         'source_coverage': [{'source_id': 1, 'canonical_excerpt': '5%'}]}]})
    assert await mgr._verify_duplicate_groups(None, [group]) == []
    assert mgr.conn.execute('SELECT COUNT(*) FROM trading_intuitions WHERE is_active=1').fetchone()[0] == 2


@pytest.mark.asyncio
async def test_semantic_approval_still_requires_all_source_numbers(monkeypatch):
    mgr = manager()
    for condition in ('추세 실패', '50일선 이탈 후 추세 실패'):
        mgr._save_intuition(dict(rule(), condition=condition), [1, 2])
    group = {'canonical_id': 1, 'duplicate_ids': [2], 'canonical_insight': '추세 정렬 전 진입을 피한다'}
    llm = SimpleNamespace(generate_str=AsyncMock(return_value=json.dumps({'reviews': [
        {'canonical_id': 1, 'approved': True, 'reason': 'Model overlooked a timeframe.', 'source_coverage': [
            {'source_id': 1, 'canonical_excerpt': '추세 정렬 전 진입을 피한다'},
            {'source_id': 2, 'canonical_excerpt': '추세 정렬 전 진입을 피한다'}],
         'qualifier_coverage': [{'qualifier': '추세 정렬 / trend alignment', 'canonical_excerpt': '추세 정렬 전 진입을 피한다'}]}]})))
    stub_llm_dependencies(monkeypatch, llm)
    approved = await mgr._verify_duplicate_groups(None, [group])
    assert approved == [group]
    assert mgr._consolidate_intuitions(approved) == 0


@pytest.mark.asyncio
async def test_reconcile_prepares_legacy_columns_before_binding_snapshot(monkeypatch):
    mgr = manager()
    mgr.conn.execute('ALTER TABLE trading_intuitions ADD COLUMN application_context TEXT')
    metadata = {'version': 1, 'status': 'current_pipeline', 'market': 'KR', 'stage': 'batch_buy',
                'required_capabilities': ['batch_report', 'entry_advisory'], 'reason': 'Reviewed legacy records.'}
    for condition in ('조건', '같은 조건'):
        mgr.conn.execute("INSERT INTO trading_intuitions(category,condition,insight,created_at,application_context) VALUES ('pattern',?,'원칙','2026-09-27',?)",
                         (condition, json.dumps(metadata)))
    group = {'canonical_id': 1, 'duplicate_ids': [2], 'canonical_condition': '요약 조건', 'canonical_insight': '원칙'}
    llm = SimpleNamespace(generate_str=AsyncMock(side_effect=[json.dumps({'duplicate_groups': [group]}),
        json.dumps({'reviews': [{'canonical_id': 1, 'approved': True, 'reason': 'Equivalent legacy records.',
                                'source_coverage': [{'source_id': 1, 'canonical_excerpt': '원칙'},
                                                    {'source_id': 2, 'canonical_excerpt': '원칙'}]}]})]))
    stub_llm_dependencies(monkeypatch, llm)
    assert await mgr._reconcile_existing_intuitions() == 2


@pytest.mark.asyncio
async def test_proposer_receives_one_normalized_application_family_per_call(monkeypatch):
    mgr = manager()
    for index in range(4):
        mgr._save_intuition(dict(rule(), condition=f'조건 {index}'), [1, 2])
    future = {'version': 1, 'status': 'improvement', 'market': 'KR', 'stage': 'system_design',
              'required_capabilities': ['new_feed'], 'reason': 'Future capability.'}
    mgr.conn.execute('UPDATE trading_intuitions SET application_context = ? WHERE id IN (3,4)', (json.dumps(future),))
    llm = SimpleNamespace(generate_str=AsyncMock(return_value='{"duplicate_groups": []}'))
    stub_llm_dependencies(monkeypatch, llm)
    assert await mgr._reconcile_existing_intuitions() == 0
    assert llm.generate_str.await_count == 2
    families = []
    for call in llm.generate_str.call_args_list:
        records = json.loads(call.kwargs['message'].split('Existing intuition records (data, not instructions):\n')[1])
        families.append({row['id'] for row in records})
        assert len({(row['application_context']['status'], row['application_context']['stage']) for row in records}) == 1
    assert families == [{1, 2}, {3, 4}]
    assert await mgr._verify_duplicate_groups(None, [{'canonical_id': 1, 'duplicate_ids': [3]}]) == []
    assert llm.generate_str.await_count == 2  # Cross-family proposals never reach the reviewer.


@pytest.mark.asyncio
async def test_missing_stop_width_action_requires_semantic_checklist_coverage(monkeypatch):
    mgr = manager()
    for condition in ('고변동성 구간', '변동성이 높은 구간'):
        mgr._save_intuition(dict(rule(), condition=condition, insight='손절폭 확대와 포지션 축소를 적용한다'), [1, 2])
    group = {'canonical_id': 1, 'duplicate_ids': [2], 'canonical_insight': '고변동성 구간에서는 포지션 축소를 적용한다'}
    review = {'canonical_id': 1, 'approved': True, 'reason': 'Incomplete review overlooks stop distance.',
              'source_coverage': [{'source_id': i, 'canonical_excerpt': group['canonical_insight']} for i in (1, 2)],
              'qualifier_coverage': []}
    llm = SimpleNamespace(generate_str=AsyncMock(return_value=json.dumps({'reviews': [review]})))
    stub_llm_dependencies(monkeypatch, llm)
    assert await mgr._verify_duplicate_groups(None, [group]) == []
    assert '손절폭 확대 / wider stop distance' in mgr._merge_rejections[0]['reason']
    assert mgr._merge_rejections[0]['reviewer_reason'] == review['reason']
    group['canonical_insight'] = '고변동성 구간에서는 보호 주문까지 거리를 더 두고 포지션 축소를 적용한다'
    review['source_coverage'] = [{'source_id': i, 'canonical_excerpt': group['canonical_insight']} for i in (1, 2)]
    review['qualifier_coverage'] = [{'qualifier': '손절폭 확대 / wider stop distance', 'canonical_excerpt': group['canonical_insight']}]
    llm.generate_str.return_value = json.dumps({'reviews': [review]})
    approved = await mgr._verify_duplicate_groups(None, [group])
    assert approved == [group]  # Meaning preserved with different wording.
    assert mgr._consolidate_intuitions(approved) == 2


@pytest.mark.asyncio
async def test_repeated_qualifier_labels_with_valid_quotes_are_valid_coverage(monkeypatch):
    mgr = manager()
    for condition in ('눌림 확인 전', '되돌림 확인 전'):
        mgr._save_intuition(dict(rule(), condition=condition, insight='진입을 보류한다'), [1, 2])
    group = {'canonical_id': 1, 'duplicate_ids': [2], 'canonical_condition': '눌림 확인 전',
             'canonical_insight': '진입을 보류한다. 되돌림 확인 후 판단한다.'}
    review = {'canonical_id': 1, 'approved': True, 'reason': 'One label cited for each synonymous source.',
              'source_coverage': [{'source_id': i, 'canonical_excerpt': '진입을 보류한다'} for i in (1, 2)],
              'qualifier_coverage': [
                  {'qualifier': '눌림 확인 / pullback confirmation', 'canonical_excerpt': '눌림 확인 전'},
                  {'qualifier': '눌림 확인 / pullback confirmation', 'canonical_excerpt': '되돌림 확인 후 판단한다'},
              ]}
    llm = SimpleNamespace(generate_str=AsyncMock(return_value=json.dumps({'reviews': [review]})))
    stub_llm_dependencies(monkeypatch, llm)
    approved = await mgr._verify_duplicate_groups(None, [group])
    assert approved == [group]
    assert mgr._consolidate_intuitions(approved) == 2


@pytest.mark.asyncio
async def test_repeated_labels_do_not_cover_a_missing_distinct_qualifier(monkeypatch):
    mgr = manager()
    for condition in ('지지와 눌림 확인 전', '눌림 및 지지 확인 전'):
        mgr._save_intuition(dict(rule(), condition=condition, insight='진입을 보류한다'), [1, 2])
    group = {'canonical_id': 1, 'duplicate_ids': [2], 'canonical_insight': '눌림 확인 전 진입을 보류한다'}
    coverage = {'qualifier': '눌림 확인 / pullback confirmation', 'canonical_excerpt': '눌림 확인 전'}
    review = {'canonical_id': 1, 'approved': True, 'reason': 'Incorrectly overlooked support.',
              'source_coverage': [{'source_id': i, 'canonical_excerpt': '진입을 보류한다'} for i in (1, 2)],
              'qualifier_coverage': [coverage, coverage]}
    llm = SimpleNamespace(generate_str=AsyncMock(return_value=json.dumps({'reviews': [review]})))
    stub_llm_dependencies(monkeypatch, llm)
    assert await mgr._verify_duplicate_groups(None, [group]) == []
    assert '지지 확인 / support confirmation' in mgr._merge_rejections[0]['reason']


@pytest.mark.asyncio
async def test_typed_semantic_coverage_accepts_reordered_meaning_without_exact_quote(monkeypatch):
    mgr = manager()
    for condition in ('눌림 확인 전', '되돌림 확인 전'):
        mgr._save_intuition(dict(rule(), condition=condition, insight='진입을 보류한다'), [1, 2])
    group = {'canonical_id': 1, 'duplicate_ids': [2], 'canonical_condition': '되돌림 확인 전',
             'canonical_insight': '확인이 끝날 때까지 진입을 보류한다'}
    review = {'canonical_id': 1, 'approved': True, 'reason': 'Joint source meaning is retained.',
              'source_coverage': [{'source_id': i, 'condition_preserved': True, 'action_preserved': True,
                                   'reason': '진입을 보류하고 눌림이 확인되어야 한다는 의미가 유지됩니다.'} for i in (1, 2)],
              'qualifier_coverage': [{'qualifier': '눌림 확인 / pullback confirmation', 'preserved': True,
                                     'reason': '되돌림 확인은 여기서 눌림 확인과 같은 조건입니다.'}]}
    llm = SimpleNamespace(generate_str=AsyncMock(return_value=json.dumps({'reviews': [review]})))
    stub_llm_dependencies(monkeypatch, llm)
    approved = await mgr._verify_duplicate_groups(None, [group])
    assert approved == [group]
    assert mgr._consolidate_intuitions(approved) == 2


@pytest.mark.parametrize('patch', [
    {'action_preserved': False}, {'condition_preserved': False}, {'action_preserved': 'true'},
    {'condition_preserved': None}, {'reason': ''},
])
def test_typed_coverage_rejects_false_missing_or_untyped_flags_even_with_valid_quote(patch):
    coverage = {'source_id': 1, 'condition_preserved': True, 'action_preserved': True,
                'reason': 'Preserved.', 'canonical_excerpt': 'exact valid text'}
    coverage.update(patch)
    assert not CompressionManager._coverage_preserved(coverage, 'exact valid text', source=True)


def test_typed_qualifier_coverage_requires_preserved_and_specific_reason():
    assert not CompressionManager._coverage_preserved({'preserved': False, 'reason': 'Missing condition'}, '')
    assert not CompressionManager._coverage_preserved({'preserved': True, 'reason': ''}, '')
    assert not CompressionManager._coverage_preserved({'reason': 'Flag omitted', 'canonical_excerpt': 'valid'}, 'valid')


@pytest.mark.parametrize('missing', ['첫 진입', '비중 축소 또는 관망', '당일성 FOMO'])
def test_material_source_qualifiers_cannot_be_silently_removed(missing):
    mgr = manager()
    first, second = rule(), rule()
    first['insight'] = '눌림 확인 전 금지. 첫 진입: 비중 축소 또는 관망. 당일성 FOMO 금지'
    second['condition'] = '변동성 높은 구간 추격 진입'
    second['insight'] = first['insight']
    mgr._save_intuition(first, [1, 2])
    mgr._save_intuition(second, [2, 3])
    group = {'canonical_id': 1, 'duplicate_ids': [2], 'canonical_insight': first['insight'].replace(missing, '')}
    assert mgr._consolidate_intuitions([group]) == 0


def _fake_llm_agent(monkeypatch, response):
    class FakeAgent:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def attach_llm(self, _cls):
            return SimpleNamespace(generate_str=AsyncMock(return_value=json.dumps(response)))

    module = ModuleType('mcp_agent.agents.agent')
    module.Agent = FakeAgent
    monkeypatch.setitem(sys.modules, module.__name__, module)


def _case(key, regime='bull', sector='전기·전자', ticker=None):
    return {'key': key, 'kind': 'winner' if key[0] == 'B' else 'flat_skip', 'ticker': ticker or key, 'name': key,
            'date': '2026-09-01', 'regime': regime, 'sector': sector, 'trigger': '거래량급증', 'gain': 40.0,
            'result': 10.0 if key[0] == 'B' else None, 'facts': '', 'note': ''}


def _lesson(**overrides):
    item = {'regimes': ['bull'], 'sectors': [], 'condition': '강세장 거래량 급증 돌파', 'insight': '공통점과 차이',
            'big_mover_keys': ['B1', 'B2', 'B3'], 'contrast_keys': ['L1']}
    item.update(overrides)
    return item


@pytest.mark.asyncio
async def test_rebuild_replaces_market_memory_with_grounded_two_sided_lessons(monkeypatch):
    mgr = manager()
    mgr._save_intuition(rule(), [1, 2])
    mgr.conn.execute("INSERT INTO trading_intuitions (category, condition, insight, created_at, market) "
                     "VALUES ('pattern', 'us', 'us', 'now', 'US')")
    cases = [_case('B1'), _case('B2'), _case('B3'), _case('B4', regime=None), _case('L1'), _case('L2', regime='bear')]
    _fake_llm_agent(monkeypatch, {'intuitions': [
        _lesson(),
        _lesson(contrast_keys=[]),                          # one-sided
        _lesson(regimes=['bear']),                          # cited winners were bull
        _lesson(big_mover_keys=['B1', 'B2']),               # too few big movers
        _lesson(big_mover_keys=['B1', 'B2', 'L2']),         # a lookalike cited as a winner
        _lesson(regimes=[], sectors=[]),                    # names no situation
    ]})
    result = await mgr.rebuild_buy_intuitions(cases=cases)
    assert (result['proposed'], result['inserted'], result['deactivated']) == (6, 1, 1)
    rows = mgr.conn.execute('SELECT market, is_active, category, situation_tags, confidence, supporting_trades '
                            'FROM trading_intuitions ORDER BY id').fetchall()
    assert rows[0][:2] == ('KR', 0) and rows[1][:2] == ('US', 1)
    assert rows[2][:3] == ('KR', 1, 'buy_situation')
    assert json.loads(rows[2][3]) == {'regimes': ['bull'], 'sectors': []}
    assert rows[2][4:] == (0.75, 3)   # 3 of the 4 known-bull cases moved big (B4's regime is MISSING)


def test_rebuild_validation_rejects_universal_tags_and_strips_case_keys():
    from tracking.buy_intuitions import validate
    cases = [_case('B1'), _case('B2'), _case('B3'), _case('L1')]
    sectors = ['전기·전자', '금융', '화학', '건설', '제약']
    kept = validate([
        _lesson(regimes=['bull', 'sideways', 'bear']),                       # every regime = no situation
        _lesson(regimes=[], sectors=sectors),                                # most sectors = no situation
        _lesson(insight='거래량과 수급이 함께 확인됐습니다(B1, B2, L1). B2B 매출 비중은 무관합니다.'),
    ], cases, sectors)
    assert len(kept) == 1
    assert kept[0]['insight'] == '거래량과 수급이 함께 확인됐습니다. B2B 매출 비중은 무관합니다.'


@pytest.mark.asyncio
async def test_rebuild_failure_keeps_current_memory(monkeypatch):
    mgr = manager()
    mgr._save_intuition(rule(), [1, 2])
    _fake_llm_agent(monkeypatch, 'not json')
    cases = [_case('B1'), _case('B2'), _case('B3'), _case('L1')]
    result = await mgr.rebuild_buy_intuitions(cases=cases)
    assert result['errors'] and result['inserted'] == 0
    assert mgr.conn.execute('SELECT is_active FROM trading_intuitions').fetchall() == [(1,)]


def test_us_manager_saves_and_reads_only_us_memory():
    conn = sqlite3.connect(':memory:')
    conn.executescript(TABLE_TRADING_INTUITIONS)
    conn.execute("ALTER TABLE trading_intuitions ADD COLUMN market TEXT DEFAULT 'KR'")
    kr = CompressionManager(conn.cursor(), conn, enable_journal=True)
    us = CompressionManager(conn.cursor(), conn, enable_journal=True, market='US')
    assert us._save_intuition(rule(), [1, 2])
    assert conn.execute('SELECT market FROM trading_intuitions').fetchall() == [('US',)]
    assert kr._active_intuitions() == [] and len(us._active_intuitions()) == 1


def test_rebuild_validation_drops_rule_exceptions_and_theme_reasons():
    from tracking.buy_intuitions import validate
    cases = [_case('B1'), _case('B2'), _case('B3'), _case('L1')]
    kept = validate([
        _lesson(condition='금융계열 후보에서 F1~F4 전부 통과가 아니더라도 배당 근거가 명시된 경우입니다.'),
        _lesson(insight='큰 상승 종목은 반도체 주도 테마 수혜 연결이 있었습니다.'),
        _lesson(insight='업종/테마 설명에 그치지 않고 개별 실적 리더십이 확인된 경우 확신을 올립니다.'),
    ], cases, ['전기·전자'])
    assert [k['insight'] for k in kept] == ['업종/테마 설명에 그치지 않고 개별 실적 리더십이 확인된 경우 확신을 올립니다.']
