import json

import pytest

from prism_core.dart_viewer_tree import parse_viewer_tree

RECEIPT = '20260318000123'
CORP = '00123456'
SINK = "var jsTree = $j('#listTree').jstree({'core': {'multiple': false, 'themes': {'icons': false}, 'data': treeData}});"
UI = '''jsTree.on('loaded.jstree', function(){
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
viewDoc("20260318000123","123","1","100","50","dart4.xsd","");'''
FLASH_SHIM = '''(function(){var s=function(){
__flash__removeCallback=function(i,n){if(i)i[n]=null;};
window.setTimeout(s,10);};s();})();'''


def node(var, ele, **changes):
    values = {'text': '주석', 'id': str(ele), 'rcpNo': RECEIPT, 'dcmNo': '123', 'eleId': str(ele),
              'offset': '100', 'length': '50', 'dtd': 'dart4.xsd', 'tocNo': '9', 'atocId': '0'}
    values.update(changes)
    return f'var {var} = {{}};' + ''.join(
        f"{var}['{key}'] = {json.dumps(value, ensure_ascii=False)};" for key, value in values.items()) + 'cnt++;'


def page(builder, suffix='', sink=SINK):
    return f'''<html><script>function viewDoc() {{ var url = "/report/viewer.do"; }}
    function initPage() {{makeToc();}}
    function makeToc() {{cnt=0;var treeData=[];{builder}{sink}{UI}{suffix}}}
    </script><a onclick="openCorpInfoNew('{CORP}')"></a></html>'''


def valid():
    return node('node1', 1) + "node1['children']=[];" + node('node2', 2) + "node1['children'].push(node2);treeData.push(node1);"


def test_epochs_preserve_explicit_edges_and_original_tuple():
    builder = valid() + node('node1', 3) + "node1['children']=[];" + node('node2', 4, offset='999') + "node1['children'].push(node2);treeData.push(node1);"
    graph = parse_viewer_tree(page(builder), RECEIPT, CORP)
    assert graph['root_keys'] == ['123:1', '123:3']
    assert [n['parent_key'] for n in graph['nodes']] == [None, '123:1', None, '123:3']
    assert graph['nodes'][0]['children_keys'] == ['123:2']
    assert graph['nodes'][3]['offset'] == '999'
    assert len(graph['main_sha256']) == 64


@pytest.mark.parametrize('bad', [
    "node1['children'].push(node1);", 'treeData.push(node9);',
    "node1['children'].push(node2);", "node1['text']=\"late\";",
    'var node1={};', 'var alias=node1;', 'treeData.reverse();',
    'if(true){}', 'node1.text="x";', 'cnt--;', 'cnt+=1;',
])
def test_unknown_or_post_freeze_statements_rejected(bad):
    with pytest.raises(ValueError, match='VIEWER_TREE_'):
        parse_viewer_tree(page(valid() + bad), RECEIPT, CORP)


@pytest.mark.parametrize('suffix', ['treeData=[];', 'node1=null;', 'cnt++;', 'foo(cnt);', '/makeToc/;', '`fake`;'])
def test_tail_cannot_mutate_or_disguise_boundaries(suffix):
    with pytest.raises(ValueError, match='VIEWER_TREE_'):
        parse_viewer_tree(page(valid(), suffix), RECEIPT, CORP)


@pytest.mark.parametrize('replace', [
    ('cnt++;', ''), ("['id']", "['tocNo']"), ('"9"', '"bad"'),
    ("['children']=[];", ''), ('treeData.push(node1);', ''),
    ('"123"', '"124"'),
])
def test_missing_order_and_document_mismatch(replace):
    source = valid()
    old, new = replace
    source = source.replace(old, new, 1)
    with pytest.raises(ValueError, match='VIEWER_TREE_'):
        parse_viewer_tree(page(source), RECEIPT, CORP)


@pytest.mark.parametrize('sink', [SINK.replace('data', 'other'), SINK.replace('treeData}', 'treeData.slice()}'), SINK.replace('false', 'true', 1), SINK.replace("'data': treeData", "'data': treeData, 'data': treeData")])
def test_sink_must_match_all_tokens(sink):
    with pytest.raises(ValueError, match='VIEWER_TREE_'):
        parse_viewer_tree(page(valid(), sink=sink), RECEIPT, CORP)


def test_comments_and_string_lookalikes_do_not_create_edges():
    source = valid().replace('주석', "treeData.push(node7); function makeToc() {}")
    graph = parse_viewer_tree(page('/* node9[\"children\"].push(node8); */' + source), RECEIPT, CORP)
    assert len(graph['nodes']) == 2


def test_html_and_object_depth_limits():
    with pytest.raises(ValueError, match='VIEWER_TREE_'):
        parse_viewer_tree(' ' * (2 * 1024 * 1024 + 1), RECEIPT, CORP)
    builder = ''.join(node(f'node{i}', i) + f"node{i}['children']=[];" for i in range(1, 34))
    builder += ''.join(f"node{i}['children'].push(node{i+1});" for i in reversed(range(1, 33))) + 'treeData.push(node1);'
    with pytest.raises(ValueError, match='VIEWER_TREE_'):
        parse_viewer_tree(page(builder), RECEIPT, CORP)


@pytest.mark.parametrize('suffix', [r'treeD\u0061ta=[];', r'nod\u00651=null;', 'var alias=node1;alias.text="bad";', 'var alias=treeData;alias.pop();'])
def test_escaped_and_alias_tail_rejected(suffix):
    with pytest.raises(ValueError, match='VIEWER_TREE_'):
        parse_viewer_tree(page(valid(), suffix), RECEIPT, CORP)


@pytest.mark.parametrize('external', [
    'var fake=/function makeToc() { treeData.push(node7); }/;',
    'var fake=/["function makeToc() {}"]/;',
    'var fake=/[^0-9]/g;',
    'x.replace(/[^0-9]/gi, "");',
    'x.replace(/[^0-8]/g, "");',
    'x=leftPanelWidth/window.innerWidth;',
    'x=(leftPanelWidth/window.innerWidth*101);',
])
def test_unknown_external_slash_or_fake_function_rejected(external):
    source = page(valid()).replace('function makeToc()', external + 'function makeToc()')
    with pytest.raises(ValueError, match='VIEWER_TREE_'):
        parse_viewer_tree(source, RECEIPT, CORP)


def test_observed_external_lexical_forms_and_counter_reads():
    external = '''x=(leftPanelWidth / window.innerWidth * 100);
    y=(rightPanelWidth / window.innerWidth * 100); x.replace(/[^0-9]/g, "");'''
    source = page(valid()).replace('function makeToc()', external + 'function makeToc()')
    assert len(parse_viewer_tree(source, RECEIPT, CORP)['nodes']) == 2


@pytest.mark.parametrize('inside', [
    'x.replace(/[^0-9]/g, "");', 'x=(leftPanelWidth/window.innerWidth*100);',
])
def test_even_observed_slash_forms_forbidden_inside_function(inside):
    with pytest.raises(ValueError, match='VIEWER_TREE_'):
        parse_viewer_tree(page(valid(), inside), RECEIPT, CORP)


@pytest.mark.parametrize('builder', [
    node('node1', 1) + 'var node1={};',
    node('node1', 1) + "node1['children']=[];" + node('node2', 2) + "node1['children'].push(node2);node2['children']=[];treeData.push(node1);",
    node('node1', 1) + "node1['children']=[];" + node('node2', 2) + "node1['children'].push(node2);treeData.push(node2);treeData.push(node1);",
    node('node1', 1) + "node1['children']=[];node1['children']=[];treeData.push(node1);",
    node('node1', 1).replace('cnt++;', 'cnt++;cnt++;') + 'treeData.push(node1);',
])
def test_epochs_and_duplicate_attachment_fail_closed(builder):
    with pytest.raises(ValueError, match='VIEWER_TREE_'):
        parse_viewer_tree(page(builder), RECEIPT, CORP)


def test_all_sink_quote_styles_and_comment_whitespace():
    source = page(valid(), sink=SINK.replace("'", '"')).replace('cnt=0;', 'cnt /*ok*/ = 0;')
    assert parse_viewer_tree(source, RECEIPT, CORP)['root_keys'] == ['123:1']


def test_unquoted_sink_keys_and_leading_wrapper_rejected():
    for source in [page(valid(), sink=SINK.replace("'core'", 'core')),
                   page(valid()).replace('cnt=0;', 'if(true){}cnt=0;'),
                   page(valid()).replace('function makeToc()', 'function makeToc(x)')]:
        with pytest.raises(ValueError, match='VIEWER_TREE_'):
            parse_viewer_tree(source, RECEIPT, CORP)


def test_object_limit():
    builder = ''.join(node('node1', i) + 'treeData.push(node1);' for i in range(1, 2002))
    with pytest.raises(ValueError, match='VIEWER_TREE_'):
        parse_viewer_tree(page(builder), RECEIPT, CORP)


def test_statement_limit_includes_post_sink_code():
    with pytest.raises(ValueError, match='VIEWER_TREE_LIMIT'):
        parse_viewer_tree(page(valid(), 'foo();' * 50_001), RECEIPT, CORP)


def test_global_escaped_identifier_is_not_a_second_hidden_declaration():
    source = page(valid()).replace('function makeToc()', r'var ma\u006beToc=other;function makeToc()')
    with pytest.raises(ValueError, match='VIEWER_TREE_LEXICAL'):
        parse_viewer_tree(source, RECEIPT, CORP)


def test_duplicate_function_or_missing_function_rejected():
    for source in [page(valid()).replace('</script>', 'function makeToc() {}</script>'),
                   page(valid()).replace('function makeToc()', 'function other()')]:
        with pytest.raises(ValueError, match='VIEWER_TREE_FUNCTION'):
            parse_viewer_tree(source, RECEIPT, CORP)


def test_flat_node_validation_still_controls_identity_and_tuple():
    for source in [page(valid()).replace(RECEIPT, '20260318000456'),
                   page(valid()).replace(CORP, '00876543'),
                   page(valid()).replace('"50"', '"0"'),
                   page(valid()).replace('"dart4.xsd"', '"evil.xsd"')]:
        with pytest.raises(ValueError, match='VIEWER_TREE_FLAT_INVALID'):
            parse_viewer_tree(source, RECEIPT, CORP)


def test_non_string_and_surrogate_inputs_fail_with_static_codes():
    for source in [None, b'html', '\ud800']:
        with pytest.raises(ValueError, match='VIEWER_TREE_INPUT'):
            parse_viewer_tree(source, RECEIPT, CORP)


@pytest.mark.parametrize('suffix', [
    "eval('treeData[0].children=[]');",
    'jsTree.jstree(true).settings.core.data[0].children=[];',
])
def test_indirect_post_sink_graph_mutation_rejected(suffix):
    with pytest.raises(ValueError, match='VIEWER_TREE_'):
        parse_viewer_tree(page(valid(), suffix), RECEIPT, CORP)


@pytest.mark.parametrize('prefix, suffix', [
    ('if(false){', '}'),
    ('function outer(){', '}'),
    ('', 'makeToc=function(){};'),
])
def test_unreachable_nested_or_reassigned_declaration_rejected(prefix, suffix):
    source = page(valid()).replace('function makeToc()', prefix + 'function makeToc()')
    source = source.replace('</script>', suffix + '</script>')
    with pytest.raises(ValueError, match='VIEWER_TREE_'):
        parse_viewer_tree(source, RECEIPT, CORP)


@pytest.mark.parametrize('transform', [
    lambda s: s.replace('function makeToc()', 'var alias=function makeToc()'),
    lambda s: s.replace('cnt=0;', 'cnt=</script><script>0;'),
    lambda s: s.replace('</script>', '</script><script>makeToc=function(){};</script>'),
    lambda s: s.replace('</script>', '</script><script>var alias=makeToc;</script>'),
    lambda s: s.replace('</script>', '</script><script>window["makeToc"]=other;</script>'),
    lambda s: s.replace('makeToc();', 'if(false)makeToc();'),
    lambda s: s.replace('makeToc();', 'function nested(){makeToc();}'),
    lambda s: s.replace('makeToc();', 'makeToc();makeToc();'),
    lambda s: s.replace('makeToc();', 'alias(makeToc);'),
    lambda s: s.replace('"123","1","100"', '"123","2","100"'),
    lambda s: s.replace("'open_all'", "'close_all'"),
    lambda s: s.replace('hideTocArea();', 'hideTocArea();foo();'),
    lambda s: s.replace('</script>', '<!--x--></script><script>setTimeout("makeToc()",10);</script>'),
    lambda s: s.replace('</script>', '</script><script>setInterval("code",10);</script>'),
    lambda s: s.replace('</script>', '</script><script>Function("code")();</script>'),
    lambda s: s.replace('</script>', '</script><script>window["eval"]("code");</script>'),
    lambda s: s.replace('</script>', '</script><script>setTimeout(("code"),10);</script>'),
    lambda s: s.replace('</script>', '</script><script>setTimeout.call(window,"code",10);</script>'),
    lambda s: s.replace('</script>', r"</script><script>window['ma\u006beToc']=other;</script>"),
])
def test_static_function_and_ui_contract_counterexamples(transform):
    with pytest.raises(ValueError, match='VIEWER_TREE_'):
        parse_viewer_tree(transform(page(valid())), RECEIPT, CORP)


@pytest.mark.parametrize('extra_scripts', [
    '<script>' + ';' * 1_000_000 + '</script>',
    ''.join('<script>' + ';' * 20_000 + '</script>' for _ in range(3)),
    '<script>' + '@' * 250_001 + '</script>',
    ''.join('<script>' + '@' * 90_000 + '</script>' for _ in range(3)),
], ids=['million-statements', 'split-statements', 'token-limit', 'split-token-limit'])
def test_cumulative_script_resources_rejected_before_parsing(extra_scripts, monkeypatch):
    monkeypatch.setattr('prism_core.dart_viewer_tree._structure',
                        lambda _: pytest.fail('Structural allocation before cumulative limit rejection'))
    source = page(valid()).replace('<script>', extra_scripts + '<script>', 1)
    with pytest.raises(ValueError, match='VIEWER_TREE_LIMIT'):
        parse_viewer_tree(source, RECEIPT, CORP)


def test_variable_string_timer_cannot_redefine_builder():
    extra = "<script>var payload='makeToc=function(){}';setTimeout(payload,0);</script>"
    source = page(valid()).replace('<script>', extra + '<script>', 1)
    with pytest.raises(ValueError, match='VIEWER_TREE_'):
        parse_viewer_tree(source, RECEIPT, CORP)


@pytest.mark.parametrize('script', ['setTimeout(function(){foo();},500);', FLASH_SHIM])
def test_confirmed_timer_callback_shapes(script):
    source = page(valid()).replace('<script>', '<script>' + script + '</script><script>', 1)
    assert len(parse_viewer_tree(source, RECEIPT, CORP)['nodes']) == 2


@pytest.mark.parametrize('script', [
    'setTimeout(callback,500);', 'setTimeout(callback(),500);',
    'setTimeout(function(){},501);', 'setTimeout(function(){},500,1);',
    'setTimeout(function(x){},500);', 'setTimeout(async function(){},500);',
    'setTimeout(function*(){},500);', 'setTimeout(()=>{},500);',
    'setTimeout(function(){}.bind(null),500);', 'window.setTimeout(function(){},500);',
    'setInterval(function(){},500);', 'var alias=setTimeout;',
    'var s="code";window.setTimeout(s,10);',
    FLASH_SHIM + ';', 'var other=1;' + FLASH_SHIM,
    FLASH_SHIM.replace('s();', 's="code";s();'),
    FLASH_SHIM.replace('10', '11'), FLASH_SHIM.replace('window.setTimeout', 'setTimeout'),
])
def test_unproven_or_modified_callback_shapes_rejected(script):
    source = page(valid()).replace('<script>', '<script>' + script + '</script><script>', 1)
    with pytest.raises(ValueError, match='VIEWER_TREE_'):
        parse_viewer_tree(source, RECEIPT, CORP)


@pytest.mark.parametrize('receipt, corp', [(RECEIPT, []), (RECEIPT, {}), ([], CORP), ({}, CORP), (None, CORP), (RECEIPT, 123)])
def test_non_string_identity_inputs_use_static_error(receipt, corp):
    with pytest.raises(ValueError, match='^VIEWER_TREE_INPUT$'):
        parse_viewer_tree(page(valid()), receipt, corp)
