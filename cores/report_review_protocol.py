"""Typed report-review protocol: reporting a conflict grants no source authority."""
from types import MappingProxyType
from typing import Literal

from pydantic import ConfigDict, Field, create_model


BASE_SECTIONS = ('price_volume_analysis', 'investor_trading_analysis', 'company_status',
                 'company_overview', 'news_analysis', 'market_index_analysis')
DART_SOURCE_ROLES = ('finance', 'business', 'risks')
EDIT_REASONS = ('profit_attribution', 'comparison_basis', 'availability_scope', 'session_timing')
SECTION_POLICIES = MappingProxyType({
    **{key: 'REGENERATE_FACTS' for key in BASE_SECTIONS},
    'investment_strategy': 'FRESH_SYNTHESIS', 'dart_deep_analysis': 'REBUILD_DART',
    **{key: 'IMMUTABLE_SOURCE' for key in
       ('shared_reference', 'peer_comparison', 'macro_context', 'dart_depth_limit')},
})
ERROR_CODES = frozenset({
    'READY_GUARD', 'INVALID_SCHEMA', 'UNKNOWN_EVIDENCE', 'UNKNOWN_SECTION',
    'MISSING_SECTION', 'MISSING_EVIDENCE', 'INVALID_INPUT', 'INVALID_ENVELOPE',
    'CAPACITY_EXCEEDED', 'SOURCE_CONFLICT', 'FACT_CONFLICTS',
    'STRUCTURED_OUTPUT_MISSING', 'CANCELLED', 'BACKEND_ERROR',
    'MALFORMED_JSON', 'DUPLICATE_JSON_KEY', 'SUMMARY_LENGTH', 'SUMMARY_NUMBER',
    'SUMMARY_URL', 'SUMMARY_INTERNAL', 'EDIT_FENCE', 'EDIT_PARAGRAPH_PROTECTED',
    'EDIT_SCHEMA', 'EDIT_TARGET', 'EDIT_SESSION', 'EDIT_MATCH', 'EDIT_STRATEGY',
    'EDIT_NUMBER', 'EDIT_URL', 'EDIT_STRUCTURE', 'EDIT_DECISION', 'EDIT_OVERLAP',
    'BASE_SECTION_FAILED', 'FACT_REPAIR_EXHAUSTED', 'DART_REPAIR_FAILED',
    'STRATEGY_GENERATION_FAILED', 'RECOVERY_TARGET', 'SOURCE_BASIS_MISSING',
    'SOURCE_CONSERVATION', 'RECOVERY_CAPACITY', 'RECOVERY_OUTPUT_LENGTH',
    'RECOVERY_OUTPUT_STRUCTURE', 'RECOVERY_NUMBER', 'RECOVERY_URL',
    'RECOVERY_PROTECTED', 'RECOVERY_BACKEND_ERROR',
})


class ReportFactEditorError(ValueError):
    """Safe code and field metadata; no draft/issue text in diagnostics or message."""
    def __init__(self, message='Report review failed', *, code='READY_GUARD', details=None):
        super().__init__(message)
        self.code = code if code in ERROR_CODES else 'INVALID_INPUT'
        allowed = {'field', 'index', 'count', 'limit', 'section', 'stage'}
        self.details = {key: value for key, value in (details or {}).items()
                        if key in allowed and isinstance(value, (str, int, bool))
                        and (not isinstance(value, str) or len(value) <= 80)}
        self.diagnostic_id = None


class ReportSourceConflictError(ReportFactEditorError):
    """Known source conflicts require investigation, never model-source repair."""


class ReportFactConflictError(ReportFactEditorError):
    def __init__(self, conflicts, *, evidence_sections=(), kinds=(), source_roles=()):
        if (not isinstance(conflicts, tuple) or not 1 <= len(conflicts) <= 8
                or any(not isinstance(item, tuple) or len(item) != 2
                       or not isinstance(item[0], str) or item[0] not in SECTION_POLICIES
                       or SECTION_POLICIES[item[0]] == 'IMMUTABLE_SOURCE'
                       or not isinstance(item[1], str) or not item[1].strip()
                       or len(item[1]) > 2000 for item in conflicts)):
            raise ReportFactEditorError('Invalid conflict contract', code='INVALID_SCHEMA')
        if not isinstance(kinds, tuple):
            raise ReportFactEditorError('Invalid conflict kinds', code='INVALID_SCHEMA')
        if not kinds:
            kinds = ('contradiction',) * len(conflicts)
        if (not isinstance(kinds, tuple) or len(kinds) != len(conflicts)
                or any(kind not in ('contradiction', 'unsupported_claim') for kind in kinds)):
            raise ReportFactEditorError('Invalid conflict kinds', code='INVALID_SCHEMA')
        if (not isinstance(evidence_sections, tuple)
                or (evidence_sections and len(evidence_sections) != len(conflicts))
                or any((key is None and kinds[index] != 'unsupported_claim')
                       or (key is not None and (not isinstance(key, str) or key not in SECTION_POLICIES))
                       for index, key in enumerate(evidence_sections))):
            raise ReportFactEditorError('Invalid evidence pointers', code='UNKNOWN_EVIDENCE')
        self._conflicts = conflicts
        self._evidence_sections = evidence_sections
        self._kinds = kinds
        if not isinstance(source_roles, tuple):
            raise ReportFactEditorError('Invalid source role locators', code='INVALID_SCHEMA')
        if not source_roles:
            source_roles = ((),) * len(conflicts)
        if (len(source_roles) != len(conflicts)
                or any(not isinstance(roles, tuple) or len(roles) > 3
                       or any(role not in DART_SOURCE_ROLES for role in roles)
                       or len(set(roles)) != len(roles) for roles in source_roles)):
            raise ReportFactEditorError('Invalid source role locators', code='INVALID_SCHEMA')
        self._source_roles = source_roles
        super().__init__('Report factual conflicts require bounded regeneration', code='FACT_CONFLICTS',
                         details={'count': len(conflicts)})

    @property
    def conflicts(self):
        return self._conflicts

    @property
    def evidence_sections(self):
        return self._evidence_sections

    @property
    def kinds(self):
        return self._kinds

    @property
    def source_roles(self):
        return self._source_roles

    @property
    def targets(self):
        return tuple(key for key in BASE_SECTIONS if any(section == key for section, _ in self.conflicts))

    @property
    def repair_dart(self):
        return any(key == 'dart_deep_analysis' for key, _ in self.conflicts)

    @property
    def strategy_only(self):
        return all(key == 'investment_strategy' for key, _ in self.conflicts)


def _fail(code, field, **details):
    raise ReportFactEditorError('Report review protocol rejected output', code=code,
                                details={'field': field, **details})


def review_output_schema(sections):
    """SDK enum is input-specific; local validation still controls authority."""
    keys = tuple(key for key in sections if key in SECTION_POLICIES)
    if not keys:
        _fail('INVALID_INPUT', 'sections')
    key_type = Literal[keys]
    config = ConfigDict(extra='forbid', strict=True)
    edit = create_model('ReportReviewEdit', __config__=config,
                        section=(key_type, ...), original=(str, ...), replacement=(str, ...),
                        reason=(Literal[EDIT_REASONS], ...))
    conflict = create_model('ReportReviewConflict', __config__=config,
                            section=(key_type, ...), issue=(str, ...), evidence_section=(key_type | None, ...),
                            kind=(Literal['contradiction', 'unsupported_claim'], ...),
                            source_roles=(list[Literal['finance', 'business', 'risks']], Field(max_length=3)))
    return create_model('ReportReviewEnvelope', __config__=config,
                        status=(Literal['READY', 'CONFLICTS'], ...), summary=(str | None, ...),
                        edits=(list[edit], ...), unresolved=(list[conflict], ...))


def validate_review_envelope(sections, payload, *, stage='final'):
    """Common bounds first, then disjoint ready/conflict branches (no repairs)."""
    if stage not in ('assessment', 'final'):
        _fail('INVALID_INPUT', 'stage')
    if not isinstance(payload, dict) or set(payload) != {'status', 'summary', 'edits', 'unresolved'}:
        _fail('INVALID_SCHEMA', 'envelope')
    if payload['status'] not in ('READY', 'CONFLICTS'):
        _fail('INVALID_SCHEMA', 'status')
    summary = payload['summary']
    if summary is not None and not isinstance(summary, str):
        _fail('INVALID_SCHEMA', 'summary')
    if isinstance(summary, str) and len(summary) > 6000:
        _fail('CAPACITY_EXCEEDED', 'summary', limit=6000)
    for field in ('edits', 'unresolved'):
        if not isinstance(payload[field], list):
            _fail('INVALID_SCHEMA', field)
        if len(payload[field]) > 8:
            _fail('CAPACITY_EXCEEDED', field, count=len(payload[field]), limit=8)
    if payload['status'] == 'READY':
        if payload['unresolved']:
            _fail('INVALID_ENVELOPE', 'unresolved')
        if stage == 'assessment' and (summary is not None or payload['edits']):
            _fail('INVALID_ENVELOPE', 'assessment')
        for index, edit in enumerate(payload['edits']):
            if (not isinstance(edit, dict) or set(edit) != {'section', 'original', 'replacement', 'reason'}
                    or not all(isinstance(value, str) and value.strip() for value in edit.values())):
                _fail('INVALID_SCHEMA', 'edits', index=index)
            if edit['reason'] not in EDIT_REASONS:
                _fail('EDIT_SCHEMA', 'reason', index=index)
            if max(len(edit['original']), len(edit['replacement'])) > 6000:
                _fail('CAPACITY_EXCEEDED', 'edits', index=index, limit=6000)
        return payload
    if summary is not None or payload['edits'] or not payload['unresolved']:
        _fail('INVALID_ENVELOPE', 'conflicts')
    conflicts, sources, kinds, source_roles, blocked = [], [], [], [], []
    for index, item in enumerate(payload['unresolved']):
        if not isinstance(item, dict) or set(item) != {'section', 'issue', 'evidence_section', 'kind', 'source_roles'}:
            _fail('INVALID_SCHEMA', 'unresolved', index=index)
        section, issue, evidence = item['section'], item['issue'], item['evidence_section']
        if not isinstance(section, str) or section not in SECTION_POLICIES:
            _fail('UNKNOWN_SECTION', 'section', index=index)
        if section not in sections:
            _fail('MISSING_SECTION', 'section', section=section, index=index)
        if not isinstance(issue, str) or not issue.strip():
            _fail('INVALID_SCHEMA', 'issue', index=index)
        if len(issue) > 2000:
            _fail('CAPACITY_EXCEEDED', 'issue', index=index, limit=2000)
        kind = item['kind']
        if kind not in ('contradiction', 'unsupported_claim'):
            _fail('INVALID_SCHEMA', 'kind', index=index)
        immutable = SECTION_POLICIES[section] == 'IMMUTABLE_SOURCE'
        if evidence is None and kind == 'contradiction' and not immutable:
            _fail('MISSING_EVIDENCE', 'evidence_section', index=index)
        if evidence is not None and (not isinstance(evidence, str) or evidence not in SECTION_POLICIES):
            _fail('UNKNOWN_EVIDENCE', 'evidence_section', index=index)
        if evidence is not None and (not isinstance(sections.get(evidence), str) or not sections[evidence].strip()):
            _fail('MISSING_EVIDENCE', 'evidence_section', index=index)
        roles = item['source_roles']
        if (not isinstance(roles, list) or len(roles) > 3
                or any(not isinstance(role, str) or role not in DART_SOURCE_ROLES for role in roles)
                or len(set(roles)) != len(roles)):
            _fail('INVALID_SCHEMA', 'source_roles', index=index)
        if ((evidence == 'dart_deep_analysis' and kind == 'contradiction' and not roles)
                or (evidence != 'dart_deep_analysis' and roles)):
            _fail('INVALID_ENVELOPE', 'source_roles', index=index)
        conflicts.append((section, issue))
        sources.append(evidence)
        kinds.append(kind)
        source_roles.append(tuple(roles))
        if immutable:
            blocked.append(section)
    if blocked:
        raise ReportSourceConflictError('Report source conflict requires investigation', code='SOURCE_CONFLICT',
                                         details={'section': blocked[0], 'count': len(blocked)})
    raise ReportFactConflictError(tuple(conflicts), evidence_sections=tuple(sources),
                                  kinds=tuple(kinds), source_roles=tuple(source_roles))
