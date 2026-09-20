"""Bounded, conservative inline-XBRL revenue evidence; not a taxonomy processor.

Implements nonFraction scale/sign separately from accuracy. Unsupported transforms,
typed dimensions and conflicting duplicates are omissions, not inferred numbers.
Specification: https://www.xbrl.org/specification/inlinexbrl-part1/rec-2013-11-18/inlinexbrl-part1-rec-2013-11-18.html
"""
import re
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from hashlib import sha256

from lxml import etree

MAX_BYTES = 16 * 1024 * 1024
MAX_FACTS = 2000
X = 'http://www.xbrl.org/2003/instance'
D = 'http://xbrl.org/2006/xbrldi'
IX = {'http://www.xbrl.org/2013/inlineXBRL', 'http://www.xbrl.org/2008/inlineXBRL'}
NIL = '{http://www.w3.org/2001/XMLSchema-instance}nil'
REVENUE = {'Revenues', 'SalesRevenueNet', 'SalesRevenueGoodsNet', 'SalesRevenueServicesNet',
           'RevenueFromContractWithCustomerExcludingAssessedTax',
           'RevenueFromContractWithCustomerIncludingAssessedTax', 'Revenue'}


def _valid_id(value):
    if not isinstance(value, str) or not value or any(c in value for c in ':{}'):
        return False
    try:
        etree.QName(value)
        return True
    except ValueError:
        return False


def _source_path(node, sibling_positions):
    """Namespace-independent XPath, counting element siblings, never comments."""
    indices = []
    current = node
    while current is not None:
        parent = current.getparent()
        if parent is None:
            indices.append(1)
        else:
            if parent not in sibling_positions:
                sibling_positions[parent] = {
                    child: index for index, child in enumerate(
                        (child for child in parent if isinstance(child.tag, str)), 1)
                }
            indices.append(sibling_positions[parent][current])
        current = parent
    return ''.join(f'/*[{index}]' for index in reversed(indices))


def _qname(node, value):
    prefix, sep, local = (value or '').partition(':')
    ns = node.nsmap.get(prefix if sep else None)
    if not ns:
        raise ValueError('unresolved_qname')
    return f'{{{ns}}}{local if sep else prefix}'


def _context(node):
    allowed = {f'{{{X}}}{tag}' for tag in ('entity', 'period', 'scenario')}
    children = [child for child in node if isinstance(child.tag, str)]
    if (any(child.tag not in allowed for child in children)
            or len({child.tag for child in children}) != len(children)):
        raise ValueError('unsupported_context_scope')
    entity_nodes = node.findall(f'{{{X}}}entity')
    if len(entity_nodes) != 1:
        raise ValueError('invalid_entity')
    entity_children = [child for child in entity_nodes[0] if isinstance(child.tag, str)]
    if (any(child.tag not in {f'{{{X}}}identifier', f'{{{X}}}segment'} for child in entity_children)
            or len({child.tag for child in entity_children}) != len(entity_children)):
        raise ValueError('unsupported_context_scope')
    ids = node.findall(f'{{{X}}}entity/{{{X}}}identifier')
    periods = node.findall(f'{{{X}}}period')
    if len(ids) != 1 or len(periods) != 1:
        raise ValueError('invalid_context')
    identifier = (ids[0].text or '').strip()
    scheme = ids[0].get('scheme', '')
    if not identifier or not scheme:
        raise ValueError('invalid_entity')
    start = periods[0].findtext(f'{{{X}}}startDate', '')
    end = periods[0].findtext(f'{{{X}}}endDate', '')
    if len(periods[0]) != 2 or date.fromisoformat(start) > date.fromisoformat(end):
        raise ValueError('invalid_duration')
    dims = []
    for container in node.iter():
        if container.tag not in {f'{{{X}}}segment', f'{{{X}}}scenario'}:
            continue
        for member in container:
            if member.tag != f'{{{D}}}explicitMember':
                raise ValueError('unsupported_dimension')
            dims.append({'axis': _qname(member, member.get('dimension')),
                         'member': _qname(member, (member.text or '').strip())})
    if len({d['axis'] for d in dims}) != len(dims):
        raise ValueError('duplicate_dimension')
    return {'entity': {'scheme': scheme, 'identifier': identifier},
            'period': {'start': start, 'end': end},
            'dimensions': sorted(dims, key=lambda d: d['axis'])}


def _number(node):
    if node.get(NIL) in {'true', '1'}:
        raise ValueError('nil_fact')
    if node.get('continuedAt') or node.get('target') or node.get('tupleRef'):
        raise ValueError('unsupported_fact_scope')
    if any(parent.tag in {f'{{{ns}}}{tag}' for ns in IX for tag in ('tuple', 'nonFraction')}
           or parent.get('target')
           for parent in node.iterancestors()):
        raise ValueError('unsupported_fact_scope')
    if any(isinstance(child.tag, str) and etree.QName(child).namespace in IX
           and etree.QName(child).localname != 'exclude' for child in node.iterdescendants()):
        raise ValueError('unsupported_nested_inline')
    decimals, precision = node.get('decimals'), node.get('precision')
    if (decimals is None) == (precision is None):
        raise ValueError('invalid_accuracy')
    accuracy = decimals if decimals is not None else precision
    if accuracy != 'INF' and (not re.fullmatch(r'-?\d{1,4}', accuracy or '') or
                              (precision is not None and int(precision) <= 0)):
        raise ValueError('invalid_accuracy')
    # Excluded text must not contribute to the fact value.
    def content(element):
        value = element.text or ''
        for child in element:
            if not (isinstance(child.tag, str) and child.tag in {f'{{{ns}}}exclude' for ns in IX}):
                value += content(child) if isinstance(child.tag, str) else ''
            value += child.tail or ''
        return value
    raw = content(node).strip()
    text = raw
    transform = node.get('format')
    expanded = None
    if transform:
        expanded = _qname(node, transform)
        ns, local = expanded[1:].split('}', 1)
        if ns not in {f'http://www.xbrl.org/inlineXBRL/transformation/{version}' for version in
                      ('2010-04-20', '2011-07-31', '2015-02-26', '2020-02-12', '2022-02-16')}:
            raise ValueError('unsupported_transform')
        if local not in {'num-dot-decimal', 'numdotdecimal'}:
            raise ValueError('unsupported_transform')
        if not re.fullmatch(r'(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?', text):
            raise ValueError('invalid_numeric')
        text = text.replace(',', '')
    if not re.fullmatch(r'\+?(?:\d+(?:\.\d*)?|\.\d+)', text) or len(text) > 100:
        raise ValueError('invalid_numeric')
    scale_text = node.get('scale', '0')
    if not re.fullmatch(r'-?\d{1,2}', scale_text) or abs(int(scale_text)) > 18 or node.get('sign') not in {None, '-'}:
        raise ValueError('invalid_numeric')
    with localcontext() as ctx:
        ctx.prec = 150
        number = Decimal(text) * (Decimal(10) ** int(scale_text))
        if node.get('sign') == '-':
            number = -number
    return {'value': format(number, 'f').rstrip('0').rstrip('.') if '.' in format(number, 'f') else format(number, 'f'),
            'raw_value': raw, 'scale': int(scale_text), 'sign': node.get('sign'),
            'decimals': decimals, 'precision': precision, 'format': transform, 'format_qname': expanded}


def parse_inline_revenue(html, expected_cik=None):
    """Return JSON-safe facts/gaps. Values are exact base-currency decimal strings.

    No extrapolation, conversion, aggregation, missing-value fill or fiscal-year
    inference is performed. Strict XML is intentional: repaired namespaces or
    truncated trees cannot establish financial provenance.
    """
    raw = html.encode('utf-8') if isinstance(html, str) else html
    result = {'status': 'unsupported', 'source_sha256': sha256(raw).hexdigest(), 'facts': [], 'gaps': []}
    def gap(reason, **details):
        if len(result['gaps']) < 200:
            result['gaps'].append({'reason': reason, **details})
    if len(raw) > MAX_BYTES:
        result['status'] = 'too_large'
        gap('input_byte_limit')
        return result
    try:
        root = etree.fromstring(raw, etree.XMLParser(resolve_entities=False, no_network=True, recover=False))
        if root.getroottree().docinfo.doctype:
            raise ValueError('doctype_unsupported')
    except (etree.XMLSyntaxError, ValueError) as exc:
        result['status'] = 'invalid'
        gap('invalid_xml' if isinstance(exc, etree.XMLSyntaxError) else str(exc))
        return result
    contexts, units, duplicate_ids, seen_ids = {}, {}, set(), set()
    for node in root.iter():
        key = node.get('id')
        if key is not None:
            if key in seen_ids:
                duplicate_ids.add(key)
            seen_ids.add(key)
        if node.tag not in {f'{{{X}}}context', f'{{{X}}}unit'}:
            continue
        mapping = contexts if node.tag == f'{{{X}}}context' else units
        if not _valid_id(key):
            gap('invalid_resource_id')
            continue
        mapping[key] = node
    entities = set()
    for node in contexts.values():
        identifier = node.find(f'{{{X}}}entity/{{{X}}}identifier')
        if identifier is not None:
            entities.add((identifier.get('scheme'), (identifier.text or '').strip().lstrip('0')))
    if expected_cik is None and len(entities) > 1:
        gap('ambiguous_issuer')
        return result
    groups, invalid_groups, sibling_positions = {}, set(), {}
    count = 0
    for node in root.iter():
        if node.tag not in {f'{{{ns}}}nonFraction' for ns in IX}:
            continue
        name = node.get('name', '')
        local_name = name.rsplit(':', 1)[-1].lower()
        if 'revenue' not in local_name and not local_name.startswith('sales'):
            continue
        count += 1
        if count > MAX_FACTS:
            gap('fact_limit')
            # The unscanned suffix might invalidate any prior numeric fact.
            # Never publish a successful prefix when conflict checking is incomplete.
            return result
        ref = node.get('contextRef')
        key = None
        try:
            concept = _qname(node, name)
            ns, local = concept[1:].split('}', 1)
            if local not in REVENUE or not (re.fullmatch(r'https?://fasb.org/us-gaap/\d{4}(?:-\d{2}-\d{2})?', ns) or
                                           re.fullmatch(r'https?://xbrl.ifrs.org/taxonomy/\d{4}-\d{2}-\d{2}/ifrs-full', ns)):
                raise ValueError('unsupported_revenue_concept')
            unit_ref = node.get('unitRef')
            if (not _valid_id(ref) or not _valid_id(unit_ref) or ref not in contexts
                    or unit_ref not in units or ref in duplicate_ids or unit_ref in duplicate_ids):
                raise ValueError('invalid_reference')
            context = _context(contexts[ref])
            entity = context['entity']
            if expected_cik is not None and (entity['scheme'] not in {'http://www.sec.gov/CIK', 'https://www.sec.gov/CIK'} or
                                            not entity['identifier'].isdigit() or entity['identifier'].lstrip('0') != str(expected_cik).lstrip('0')):
                raise ValueError('issuer_mismatch')
            unit_node = units[unit_ref]
            if len(unit_node) != 1 or unit_node[0].tag != f'{{{X}}}measure':
                raise ValueError('unsupported_unit')
            unit = _qname(unit_node[0], (unit_node[0].text or '').strip())
            if not re.fullmatch(r'\{http://www.xbrl.org/2003/iso4217\}[A-Z]{3}', unit):
                raise ValueError('unsupported_unit')
            key = (concept, tuple(entity.items()), tuple(context['period'].items()),
                   tuple((d['axis'], d['member']) for d in context['dimensions']), unit)
            fact = {'concept': concept, 'context_ref': ref, 'unit': unit, 'unit_ref': unit_ref,
                    **context, **_number(node), 'source_line': node.sourceline,
                    'source_xpath': root.getroottree().getpath(node), 'source_path': _source_path(node, sibling_positions)}
            groups.setdefault(key, []).append(fact)
        except (ValueError, InvalidOperation) as exc:
            if key is not None:
                invalid_groups.add(key)
            gap(str(exc), context_ref=ref, concept=name)
    for key, group in groups.items():
        if key in invalid_groups:
            gap('invalid_duplicate_group', context_ref=group[0]['context_ref'], concept=group[0]['concept'])
        elif len({f['value'] for f in group}) > 1:
            gap('conflicting_facts', context_ref=group[0]['context_ref'], concept=group[0]['concept'])
        else:
            fact = group[0]
            fact['duplicate_sources'] = [{'context_ref': f['context_ref'], 'source_xpath': f['source_xpath'],
                                          'source_path': f['source_path'],
                                          'decimals': f['decimals'], 'precision': f['precision']} for f in group[1:]]
            result['facts'].append(fact)
    if result['facts']:
        result['status'] = 'partial' if result['gaps'] else 'ok'
    elif not result['gaps']:
        gap('no_supported_revenue_facts')
    return result
