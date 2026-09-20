"""Prove the observed DART TOC builder graph without executing JavaScript."""
import hashlib
import json
import re

from prism_core.dart_public_filings import _lex_js, _tree, parse_viewer_nodes

_LIMIT = 2 * 1024 * 1024
_FIELDS = ('text', 'id', 'rcpNo', 'dcmNo', 'eleId', 'offset', 'length', 'dtd', 'tocNo', 'atocId')
_NODE = re.compile(r'node[0-9]{1,6}\Z')
_SINK = "var jsTree = $j('#listTree').jstree({'core': {'multiple': false, 'themes': {'icons': false}, 'data': treeData}});"
_TOKEN = re.compile(r'''\s+|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|[A-Za-z_$][A-Za-z0-9_$]*|[0-9]+|\+\+|==|[^\s]''')
_POSTLUDE = '''jsTree.on('loaded.jstree', function(){
if(cnt > 200){$j(this).jstree('select_node', 'ul > li:first');}
else {$j(this).jstree('open_all');$j(this).jstree('select_node', 'ul > li:first');}
jsTree.on("select_node.jstree",function(e,data){var original=data.node.original;
if(data.selected.length){if(isChanged) resetKeyword();
if(fixGubun == "1"){linkDoc(original.rcpNo,original.dcmNo,original.eleId,original.offset,original.length,original.dtd,original.tocNo);}
else{resetKeyword();viewDoc(original.rcpNo,original.dcmNo,original.eleId,original.offset,original.length,original.dtd,original.tocNo);}
if(isMobileSize()) hideTocArea();}});});
if(cnt == 0){hideTocArea();$j('#collapse-button').css('display','none');}
if('' != ""){fixGubun="1";fixKeyword='';
$j("input:radio[name='searchGubun']:input[value='"+fixGubun+"']").prop("checked",true);
$j("#searchWord").val(fixKeyword);}
viewDoc("RCP","DCM","ELE","OFFSET","LENGTH","DTD","");'''
_FLASH_SHIM = '''(function(){var s=function(){
__flash__removeCallback=function(i,n){if(i)i[n]=null;};
window.setTimeout(s,10);};s();})();'''


def _fail(code):
    raise ValueError('VIEWER_TREE_' + code)


def _tokens(source):
    return [m[0] for m in _TOKEN.finditer(source) if not m[0].isspace()]


def _key(token):
    if re.fullmatch(r'''["'][A-Za-z_][A-Za-z0-9_]*["']''', token):
        return token[1:-1]
    return token


def _structure(tokens):
    stack, depths, parents, matches = [], [], [], {}
    for index, token in enumerate(tokens):
        depths.append(len(stack))
        parents.append(stack[-1] if stack else None)
        if token in {'(', '[', '{'}:
            stack.append(index)
            if len(stack) > 64:
                _fail('LIMIT')
        elif token in {')', ']', '}'}:
            if not stack or tokens[stack[-1]] != {')': '(', ']': '[', '}': '{'}[token]:
                _fail('SCRIPT_BOUNDARY')
            matches[stack.pop()] = index
    if stack:
        _fail('SCRIPT_BOUNDARY')
    return depths, parents, matches


def _script_contract(scripts):
    """Recognize declarations/references, not arbitrary browser execution."""
    declarations, references, parsed = {}, [], []
    token_count, statement_count = 0, 0
    bounded_scripts = []
    for script in scripts:
        code, uncommented, _ = _lex_js(script)
        if '\\' in code:
            _fail('LEXICAL')
        code, slashes = _external_slashes(code)
        # Regex character classes are not JS array delimiters. Mask only the
        # already proven external literal before the balanced-token scan.
        masks = []
        for match in re.finditer(r'/\[\^0-9\]/g', uncommented):
            if match.start() in slashes:
                masks.append(match.span())
        if masks:
            chars = list(uncommented)
            for start, end in masks:
                chars[start:end] = ' ' * (end - start)
            scan = ''.join(chars)
        else:
            scan = uncommented
        offsets, tokens = [], []
        for match in _TOKEN.finditer(scan):
            lexeme = match[0]
            if lexeme.isspace():
                continue
            token_count += 1
            statement_count += lexeme == ';'
            if token_count > 250_000 or statement_count > 50_000:
                _fail('LIMIT')
            offsets.append(match.start())
            tokens.append(lexeme)
        bounded_scripts.append((offsets, tokens, slashes))
    # No structural arrays are allocated until the cumulative budgets pass.
    flash_template = _tokens(_FLASH_SHIM)
    for script_index, (offsets, tokens, slashes) in enumerate(bounded_scripts):
        depths, parents, matches = _structure(tokens)
        parsed.append((offsets, tokens, depths, parents, matches, slashes))
        flash_shim = tokens == flash_template
        for index, lexeme in enumerate(tokens):
            literal = None
            if lexeme.startswith('"'):
                try:
                    literal = json.loads(lexeme)
                except ValueError:
                    _fail('LEXICAL')
            elif lexeme.startswith("'"):
                literal = lexeme[1:-1]
            if literal is not None and '\\' in lexeme and index and tokens[index - 1] == '[':
                _fail('LEXICAL')
            name = literal if literal is not None else lexeme
            if name in {'eval', 'Function', 'execScript'}:
                _fail('DYNAMIC_EXECUTION')
            if literal == 'makeToc':
                _fail('FUNCTION_REFERENCE')
            if name in {'setTimeout', 'setInterval'} and not flash_shim:
                _timer(tokens, index, matches)
            if lexeme in {'makeToc', 'initPage'} and index and tokens[index - 1] == 'function':
                start = index - 1
                if depths[start] or start and tokens[start - 1] not in {';', '}'} or tokens[index + 1:index + 4] != ['(', ')', '{']:
                    _fail('FUNCTION')
                if lexeme in declarations:
                    _fail('FUNCTION')
                declarations[lexeme] = (script_index, index, index + 3, matches[index + 3])
            if lexeme == 'makeToc':
                references.append((script_index, index))
    if set(declarations) != {'makeToc', 'initPage'} or len(references) != 2:
        _fail('FUNCTION')
    si, name_index, begin, end = declarations['makeToc']
    init_si, _, init_begin, init_end = declarations['initPage']
    for ref_si, index in references:
        if (ref_si, index) == (si, name_index):
            continue
        _, tokens, depths, parents, _, _ = parsed[ref_si]
        if (ref_si != init_si or not init_begin < index < init_end
                or depths[index] != 1 or parents[index] != init_begin
                or tokens[index - 1] not in {'{', '}', ';'}
                or tokens[index:index + 4] != ['makeToc', '(', ')', ';']):
            _fail('FUNCTION_REFERENCE')
    offsets, tokens, _, _, _, slashes = parsed[si]
    first, last = offsets[begin] + 1, offsets[end]
    if any(first <= position < last for position in slashes):
        _fail('LEXICAL')
    return tokens[begin + 1:end]


def _timer(tokens, index, matches):
    if (tokens[index] != 'setTimeout'
            or index and tokens[index - 1] in {'.', '[', 'new'}
            or tokens[index + 1:index + 6] != ['(', 'function', '(', ')', '{']):
        _fail('STRING_TIMER')
    end = matches[index + 5]
    if (tokens[end + 1:end + 4] != [',', '500', ')']
            or matches.get(index + 1) != end + 3):
        _fail('STRING_TIMER')


def _external_slashes(code):
    """Accept only two observed external lexical forms, never arbitrary regex."""
    allowed, masks = set(), []
    for match in re.finditer(r'\.\s*replace\s*\(\s*(/\[\^0-9\]/g)\s*,', code):
        start, end = match.span(1)
        allowed.update(i for i in range(start, end) if code[i] == '/')
        masks.append((start, end))
    division = r'\(\s*(?:leftPanelWidth|rightPanelWidth)\s*(/)\s*window\s*\.\s*innerWidth\s*\*\s*100\s*\)'
    for match in re.finditer(division, code):
        allowed.add(match.start(1))
    if {m.start() for m in re.finditer('/', code)} != allowed:
        _fail('LEXICAL')
    if not masks:
        return code, allowed
    chars = list(code)
    for start, end in masks:
        chars[start:end] = ' ' * (end - start)
    return ''.join(chars), allowed


def _parse(tokens, flat):
    sink = _tokens(_SINK)
    position, statements = 0, 0
    epochs, live, roots = [], {}, []
    pending = None

    def consume(expected):
        nonlocal position
        if tokens[position:position + len(expected)] != expected:
            _fail('GRAMMAR')
        position += len(expected)

    def current(variable):
        if variable not in live or live[variable]['sealed']:
            _fail('EPOCH')
        return live[variable]

    def complete(item):
        if not item['complete']:
            _fail('INCOMPLETE')

    consume(['cnt', '=', '0', ';', 'var', 'treeData', '=', '[', ']', ';'])
    while position < len(tokens):
        if tokens[position:position + 2] == ['var', 'jsTree']:
            break
        statements += 1
        if statements > 50_000:
            _fail('LIMIT')
        head = tokens[position]
        if head == 'var':
            if position + 1 >= len(tokens) or not _NODE.fullmatch(tokens[position + 1]):
                _fail('GRAMMAR')
            variable = tokens[position + 1]
            if pending is not None or variable in live and not live[variable]['sealed']:
                _fail('EPOCH')
            consume(['var', variable, '=', '{', '}', ';'])
            if len(epochs) >= 2000:
                _fail('LIMIT')
            item = {'fields': {}, 'complete': False, 'sealed': False, 'children': None, 'parent': None}
            epochs.append(item)
            live[variable] = item
            pending = variable
        elif head == 'cnt':
            if pending is None or len(live[pending]['fields']) != len(_FIELDS):
                _fail('INCOMPLETE')
            consume(['cnt', '++', ';'])
            live[pending]['complete'] = True
            pending = None
        elif head == 'treeData':
            if position + 4 >= len(tokens):
                _fail('GRAMMAR')
            variable = tokens[position + 4]
            consume(['treeData', '.', 'push', '(', variable, ')', ';'])
            item = current(variable)
            complete(item)
            item['sealed'] = True
            roots.append(item)
        elif _NODE.fullmatch(head):
            item = current(head)
            if position + 3 >= len(tokens):
                _fail('GRAMMAR')
            literal = tokens[position + 2]
            key = _key(literal)
            if literal == key:
                _fail('GRAMMAR')
            consume([head, '[', literal, ']'])
            if key == 'children':
                complete(item)
                if tokens[position:position + 1] == ['=']:
                    if item['children'] is not None:
                        _fail('EPOCH')
                    consume(['=', '[', ']', ';'])
                    item['children'] = []
                else:
                    if position + 3 >= len(tokens) or item['children'] is None:
                        _fail('GRAMMAR')
                    childvar = tokens[position + 3]
                    consume(['.', 'push', '(', childvar, ')', ';'])
                    child = current(childvar)
                    complete(child)
                    if child is item or child['fields']['dcmNo'] != item['fields']['dcmNo']:
                        _fail('EDGE')
                    child['sealed'], child['parent'] = True, item
                    item['children'].append(child)
            else:
                if pending != head or item['complete'] or len(item['fields']) >= len(_FIELDS) or key != _FIELDS[len(item['fields'])]:
                    _fail('FIELD_ORDER')
                if position + 1 >= len(tokens):
                    _fail('GRAMMAR')
                value = tokens[position + 1]
                consume(['=', value, ';'])
                if not value.startswith('"'):
                    _fail('LITERAL')
                try:
                    decoded = json.loads(value)
                except ValueError:
                    _fail('LITERAL')
                if not isinstance(decoded, str):
                    _fail('LITERAL')
                if key in {'id', 'tocNo', 'atocId'} and not re.fullmatch(r'[0-9]{1,14}', decoded):
                    _fail('LITERAL')
                item['fields'][key] = decoded
        else:
            _fail('GRAMMAR')
    actual = tokens[position:position + len(sink)]
    if len(actual) != len(sink) or any(
        (a not in {s, '"' + s[1:-1] + '"'} if s.startswith("'") else a != s)
        for a, s in zip(actual, sink)
    ):
        _fail('SINK')
    # Normalization only applies to simple keys. The selector is a literal too.
    if len(actual) != len(sink) or actual[5] not in {"'#listTree'", '"#listTree"'}:
        _fail('SINK')
    position += len(sink)
    tail = tokens[position:]
    expected = _tokens(_POSTLUDE)
    # The fixed postlude ends with seven JSON-string arguments. Only the
    # first six may vary, and they must match an already validated root.
    if len(tail) != len(expected):
        _fail('POSTLUDE')
    argument_positions = [len(expected) - 15 + 2 * i for i in range(6)]
    values = []
    for index in argument_positions:
        try:
            value = json.loads(tail[index])
        except ValueError:
            _fail('POSTLUDE')
        if not isinstance(value, str):
            _fail('POSTLUDE')
        values.append(value)
        tail[index] = expected[index]
    if tail != expected or not any(
        values == [root['fields'][key] for key in ('rcpNo', 'dcmNo', 'eleId', 'offset', 'length', 'dtd')]
        for root in roots
    ):
        _fail('POSTLUDE')
    if not epochs or pending is not None or any(not e['sealed'] for e in epochs):
        _fail('UNATTACHED')
    if len(epochs) != len(flat):
        _fail('FLAT_MISMATCH')
    for item, record in zip(epochs, flat):
        if any(item['fields'][k] != v for k, v in record.items() if k != 'viewer_url'):
            _fail('FLAT_MISMATCH')
        item['key'] = record['dcmNo'] + ':' + record['eleId']
    visited = set()
    stack = [(root, 1) for root in roots]
    while stack:
        item, depth = stack.pop()
        if depth > 32 or id(item) in visited:
            _fail('DEPTH_OR_CYCLE')
        visited.add(id(item))
        stack.extend((child, depth + 1) for child in item['children'] or [])
    if len(visited) != len(epochs):
        _fail('UNATTACHED')
    return {
        'nodes': [dict(record, key=item['key'], parent_key=item['parent']['key'] if item['parent'] else None,
                       children_keys=[child['key'] for child in item['children'] or []])
                  for record, item in zip(flat, epochs)],
        'root_keys': [root['key'] for root in roots],
    }


def parse_viewer_tree(html, receipt_id, corp_code):
    """Return only a fully proven explicit graph; all failures are code-only."""
    if not all(isinstance(value, str) for value in (html, receipt_id, corp_code)):
        _fail('INPUT')
    if len(html) > _LIMIT:
        _fail('LIMIT')
    try:
        raw = html.encode('utf-8')
    except UnicodeError:
        _fail('INPUT')
    if len(raw) > _LIMIT:
        _fail('LIMIT')
    try:
        scripts = _tree(html).xpath('//script/text()')
        source = _script_contract(scripts)
        graph = _parse(source, parse_viewer_nodes(html, receipt_id, corp_code))
    except ValueError as exc:
        if str(exc).startswith('VIEWER_TREE_'):
            raise
        _fail('FLAT_INVALID')
    return dict(graph, version=1, main_sha256=hashlib.sha256(raw).hexdigest())
