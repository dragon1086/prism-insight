"""Offline, issuer-independent heading routing; no financial truth inference.

Primary statements and material driver disclosures are the financial baseline;
routine breakdown exclusions are explicit retrieval scope, not a claim that the
omitted source contains no risks. Unknown primary note topics remain eligible.
Annual supplements retain
structural changes, contingent obligations, impairment and policy interpretation;
routine balance-note repetitions are not a second baseline. Annual risk chapters
remain supplements even when primary has a similarly named chapter. All choices
are explicit in the ledger; this is scoped retrieval, not completeness inference.
"""
import re

from prism_core.dart_source_tree_catalog import decode_table

OWNERS = ('company_overview', 'company_status', 'news_analysis')
POLICY = 'source-heading-disclosure-multi-owner-v2'
_FAMILIES = (
    ('policy', r'회계정책|작성기준|작성 기준'),
    ('general', r'일반사항|회사의\s*개요|일반적\s*사항'),
    ('segment', r'부문정보|영업부문'),
    ('investments', r'관계기업|공동기업|공동영업'),
    ('related', r'특수관계'),
    ('transactions', r'매각예정|중단영업|사업결합|사업양수|사업양도|지배력|종속기업.*처분'),
    ('subsequent', r'보고기간\s*후|보고기간후|후속사건'),
    ('commitments', r'우발|약정|담보|보증'),
    ('risk', r'위험관리|위험\s*관리|위험회피|파생상품'),
    ('restricted', r'사용.*제한'),
    ('impairment', r'유형자산|무형자산|영업권|사용권자산|손상'),
    ('inventory', r'재고자산'),
    ('provisions', r'충당부채|배출권|배출부채'),
    ('debt', r'차입|사채|리스|미지급금'),
    ('capital', r'신종자본|자본|배당|이익잉여'),
    ('tax', r'법인세'),
    ('cashflow', r'현금흐름|현금 흐름'),
    ('other_income', r'기타.*수익|기타.*비용|기타.*손익'),
)
_OVERVIEW = {'general', 'segment', 'investments', 'related', 'transactions',
             'subsequent', 'capital', 'policy'}
_NEWS = {'transactions', 'subsequent', 'commitments', 'risk', 'restricted',
         'impairment', 'inventory', 'provisions', 'debt', 'tax', 'cashflow',
         'other_income', 'related'}
_ANNUAL_FINANCE = {'general', 'transactions', 'commitments', 'restricted',
                   'impairment', 'other_income', 'risk', 'debt', 'capital'}
_POLICY_SUBTOPICS = re.compile(
    r'영업권|외화|초인플레이션|손상|중요한.*판단|추정|불확실|연결|사업결합')
_ANNUAL_DISCLOSURES = {
    'risk': re.compile(r'신용위험|유동성|만기|미할인|할인되지|공급자금융'),
    'debt': re.compile(r'약정|위반|미충족|유예|담보|보증'),
    'capital': re.compile(r'신종|영구|후순위|신주인수권'),
    'impairment': re.compile(r'손상|현금창출|영업권'),
    'general': re.compile(r'신종|영구|후순위|신주인수권'),
}
_TRANSACTION_DISCLOSURE = re.compile(
    r'사업결합|사업양수|사업양도|취득.*종속|종속.*취득|처분.*종속|종속.*처분|'
    r'매각|중단영업|지배력|지분거래|구조.*혁신|구조.*조정|사업재편|분할|합병|신종|영구|신주인수권')
_EQUITY_DISCLOSURE = re.compile(r'지분상품|지분증권|주식.*보유|비상장|당기손익.*금융자산')
_ROUTINE_FINANCE_DETAIL = re.compile(r'판매비와관리비|판매비.*관리비|비용의\s*성격|주당(?:손익|이익)|투자부동산')
_FINANCE_SEGMENT = re.compile(r'부문별.*(?:수익|이익|손익|자산|부채)|보고부문.*수익|부문.*손익')
_FINANCE_ENTITY = re.compile(r'요약.*재무|재무정보|비지배|소유지분.*변동|신종|영구|신주인수권')
_MATERIAL_DISCLOSURE = re.compile(r'손상|우발|약정|보증|담보|소송|위반|유예|구조조정|사업재편')


def _caption(unit):
    """Use authored disclosure labels, never arbitrary value-cell keywords."""
    if unit['kind'] != 'table':
        return ''
    table = decode_table(unit['payload'], unit['path'])
    first = [c['text'] for c in table['cells'] if c['row'] == 0]
    if len(first) != 1 or len(first[0]) > 220:
        return ''
    text = first[0]
    return text if re.search(r'(공시|기술|정보|내역)\s*$', text) else ''


def _disclosures(catalog, index):
    """A caption owns its complete following disclosure, through the next label.

    Chapter boundaries reset labels. Period/unit tables, current/prior data and
    footnotes therefore travel together, without selecting matching sentences.
    """
    labels, explicit = {}, set()
    current_chapter, current_label = None, ''
    for unit in catalog['units']:
        key = _chapter_key(unit, index)
        if key != current_chapter:
            current_chapter, current_label = key, ''
        label = _caption(unit)
        if label:
            current_label = label
            explicit.add(key)
        labels[unit['path']] = current_label
    return labels, explicit


def family(title):
    for name, pattern in _FAMILIES:
        if re.search(pattern, title):
            return name
    return 'other'


def _headings(unit, index):
    return [index[path] for path in unit.get('context', [])
            if path in index] + ([unit] if 'heading_level' in unit else [])


def chapter(unit, index):
    headings = _headings(unit, index)
    # Chapter titles are explicit source headings, not inner numbered clauses.
    top = next((h for h in headings if h.get('heading_level') == 2), None)
    return top['payload'] if top else ''


def _chapter_key(unit, index):
    top = next((h for h in _headings(unit, index) if h.get('heading_level') == 2), None)
    return top['path'] if top else ''


def route_catalogs(catalogs, metadata):
    """Return owner/source ordered unit lists plus an auditable selection ledger.

    ``metadata`` contains source role and section, never company-specific rules.
    No count frontier or byte-driven dropping is performed. Capacity is checked
    downstream and must reject the entire oversized owner transfer.
    """
    if set(catalogs) != set(metadata):
        raise ValueError('source metadata/catalog mismatch')
    selected = {owner: {} for owner in OWNERS}
    ledger = []
    for sid, catalog in catalogs.items():
        role, section = metadata[sid]['role'], metadata[sid]['section']
        if role not in {'primary', 'annual_supplement'}:
            raise ValueError('unsupported source role')
        index = {u['path']: u for u in catalog['units']}
        if len(index) != len(catalog['units']):
            raise ValueError('duplicate catalog path')
        disclosures, explicit = _disclosures(catalog, index)
        preambles = {}
        for unit in catalog['units']:
            key = _chapter_key(unit, index)
            if key in explicit and not disclosures[unit['path']]:
                preambles.setdefault(key, []).append(unit['path'])
        keep = {owner: set() for owner in OWNERS}
        source_ledger = []
        for unit in catalog['units']:
            title = chapter(unit, index) or metadata[sid].get('verified_fragment_title', '')
            key = _chapter_key(unit, index)
            topic = family(title)
            disclosure = disclosures[unit['path']]
            subtopics = [h['payload'] for h in _headings(unit, index)
                         if h.get('heading_level', 0) > 2]
            owners = set()
            if section == 'financial_statements':
                owners.update(('company_status', 'company_overview'))
            elif section not in {'financial_notes', 'financial_notes_fragment'}:
                raise ValueError('unsupported source section')
            else:
                if topic in _OVERVIEW:
                    owners.add('company_overview')
                if topic in _NEWS:
                    owners.add('news_analysis')
                if role == 'primary' or topic in _ANNUAL_FINANCE:
                    owners.add('company_status')
                if role == 'primary':
                    # Finance consumes statement totals and complete material
                    # driver disclosures, not every routine note breakdown.
                    if _ROUTINE_FINANCE_DETAIL.search(title):
                        owners.discard('company_status')
                        if _MATERIAL_DISCLOSURE.search(disclosure):
                            owners.update(('company_status', 'news_analysis'))
                    if topic == 'segment' and key in explicit and not _FINANCE_SEGMENT.search(disclosure):
                        owners.discard('company_status')
                    if topic == 'general' and subtopics and not any(_FINANCE_ENTITY.search(t) for t in subtopics):
                        owners.discard('company_status')
                if role == 'annual_supplement' and topic in _ANNUAL_DISCLOSURES:
                    if key in explicit and not _ANNUAL_DISCLOSURES[topic].search(title + ' ' + disclosure):
                        owners.discard('company_status')
                    if topic == 'general' and subtopics and not any(
                            re.search(r'소유지분.*변동|신종|영구|신주인수권', t) for t in subtopics):
                        owners.discard('company_status')
                # The same authored evidence can answer more than one report
                # question. Ownership is not a mutually exclusive topic label.
                if topic == 'segment':
                    owners.add('news_analysis')
                if topic == 'risk' and re.search(r'파생|위험회피', title):
                    owners.update(('company_overview', 'news_analysis'))
                if _TRANSACTION_DISCLOSURE.search(disclosure):
                    owners.add('company_overview')
                if topic == 'other' and _EQUITY_DISCLOSURE.search(disclosure):
                    owners.add('company_overview')
                # Unlabelled issuer-authored chapters remain whole; labelled
                # chapters route only full transaction disclosures here.
                if topic in {'cashflow', 'commitments'} and (key not in explicit or _TRANSACTION_DISCLOSURE.search(disclosure)):
                    owners.add('company_overview')
                if role == 'annual_supplement':
                    if topic in {'segment', 'related', 'capital'}:
                        owners.discard('company_overview')
                        if _TRANSACTION_DISCLOSURE.search(title + ' ' + disclosure):
                            owners.add('company_overview')
                    if topic == 'risk' and key in explicit:
                        if not re.search(r'파생|위험회피|스왑|선도|계약', disclosure):
                            owners.discard('company_overview')
                        if not re.search(r'파생|위험회피|스왑|선도|계약|공급자금융|만기|미할인|할인되지', disclosure):
                            owners.discard('news_analysis')
                    if topic in {'segment', 'tax', 'inventory', 'cashflow'}:
                        owners.discard('news_analysis')
                        if topic == 'segment' and re.search(r'수익.*손익|부문.*수익.*합계|부문.*손익', disclosure):
                            owners.add('news_analysis')
                    if topic in {'impairment', 'debt'} and key in explicit and not _ANNUAL_DISCLOSURES[topic].search(title + ' ' + disclosure):
                        owners.discard('news_analysis')
                if topic == 'policy' and role == 'annual_supplement':
                    policy_headings = [h for h in _headings(unit, index)
                                       if h.get('heading_level', 0) > 2]
                    if policy_headings:
                        depth = min(h['heading_level'] for h in policy_headings)
                        subtopics = [h['payload'] for h in policy_headings
                                     if h['heading_level'] == depth]
                    # Complete authored subtopics, not selected sentences/cells.
                    if any(_POLICY_SUBTOPICS.search(t) for t in subtopics):
                        if any(re.search(r'연결|사업결합|판단|추정|불확실', t) for t in subtopics):
                            owners.add('company_overview')
                        else:
                            owners.discard('company_overview')
                        if any(re.search(r'영업권|외화|초인플레이션|손상|판단|추정|불확실', t)
                               for t in subtopics):
                            owners.add('company_status')
                        if any(re.search(r'영업권|손상', t) for t in subtopics):
                            owners.add('news_analysis')
                    else:
                        owners.discard('company_overview')
            for owner in owners:
                keep[owner].add(unit['path'])
            source_ledger.append({'source_id': sid, 'path': unit['path'],
                                  'family': topic, 'disclosure': disclosure,
                                  'owners': sorted(owners)})
        # Include literal headings and structural containers needed by every
        # selected descendant. Do not pull every sibling of a layout wrapper.
        for owner, paths in keep.items():
            # A chapter-wide qualifier can precede its first disclosure label
            # without looking like a heading. Retain the whole authored preamble
            # whenever any part of that chapter is selected.
            for key in {_chapter_key(index[path], index) for path in paths}:
                paths.update(preambles.get(key, []))
            pending = list(paths)
            while pending:
                unit = index[pending.pop()]
                dependencies = list(unit.get('context', []))
                parent = unit['path'].rsplit('/', 1)[0]
                while parent:
                    if parent in index and index[parent]['kind'] == 'container':
                        dependencies.append(parent)
                    parent = parent.rsplit('/', 1)[0]
                for path in dependencies:
                    if path not in index:
                        raise ValueError('missing heading dependency')
                    if path not in paths:
                        paths.add(path)
                        pending.append(path)
            selected[owner][sid] = [u for u in catalog['units'] if u['path'] in paths]
        for row in source_ledger:
            row['final_owners'] = sorted(owner for owner, paths in keep.items()
                                         if row['path'] in paths)
            row['dependency_owners'] = sorted(set(row['final_owners']) - set(row['owners']))
        ledger.extend(source_ledger)
    return {'policy': POLICY, 'selected': selected, 'ledger': ledger}
