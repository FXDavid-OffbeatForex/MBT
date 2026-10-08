from pathlib import Path

import pytest

from core.ea_input_schema import parse_source_schema, import_native_set


def source(tmp_path, code):
    root = tmp_path / 'MQL5'
    path = root / 'Experts' / 'sample.mq5'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding='utf-8')
    return path, root


def test_builtin_enums_and_defaults_groups_static_inputs(tmp_path):
    path, root = source(tmp_path, '''
input group "Signal"
input(name="Price source") ENUM_APPLIED_PRICE Price=PRICE_TYPICAL;
input ENUM_TIMEFRAMES Period=PERIOD_H4;
input ENUM_DAY_OF_WEEK Day=FRIDAY;
input group "Management"
sinput int Magic=123;
static input bool Debug=false;
input string Label="hello";
''')
    schema = parse_source_schema(path, root)
    assert schema['inputs']['Price']['value'] == 6
    assert schema['inputs']['Price']['display_name'] == 'Price source'
    assert schema['inputs']['Period']['value'] == 16388
    assert schema['inputs']['Day']['value'] == 5
    assert schema['inputs']['Magic']['fixed_only'] is True
    assert schema['inputs']['Debug']['fixed_only'] is True
    assert schema['inputs']['Label']['group'] == 'Management'
    assert schema['dependencies'][0]['path'] == str(path.resolve())


def test_quoted_include_implicit_enums_and_constant_defaults(tmp_path):
    path, root = source(tmp_path, '''#include "inputs.mqh"
#define PERIOD 25
input int Length=PERIOD;
input Direction Side=SHORT;
''')
    include = path.parent / 'inputs.mqh'
    include.write_text('''#ifndef INPUTS_GUARD
#define INPUTS_GUARD
enum Direction { BOTH, LONG=4, SHORT, };
input bool Filter=true;
#endif
''')
    schema = parse_source_schema(path, root)
    assert schema['inputs']['Side']['enum_values'] == {'BOTH': 0, 'LONG': 4, 'SHORT': 5}
    assert schema['inputs']['Side']['value'] == 5
    assert schema['inputs']['Length']['value'] == 25
    assert len(schema['dependencies']) == 2
    old = schema['schema_sha256']
    include.write_text(include.read_text().replace('Filter=true', 'Filter=false'))
    assert parse_source_schema(path, root)['schema_sha256'] != old


def test_angle_include_and_repeated_guarded_include(tmp_path):
    path, root = source(tmp_path, '#include <Common\\Inputs.mqh>\n#include <Common\\Inputs.mqh>\ninput Mode Which=A;')
    include = root / 'Include' / 'Common' / 'Inputs.mqh'
    include.parent.mkdir(parents=True)
    include.write_text('#ifndef GUARD\n#define GUARD\nenum Mode { A, B };\n#endif\n')
    schema = parse_source_schema(path, root)
    assert len(schema['dependencies']) == 2
    assert schema['inputs']['Which']['enum_values'] == {'A': 0, 'B': 1}


def test_metadata_free_standard_dependency_can_repeat(tmp_path):
    path, root = source(tmp_path, '#include "Object.mqh"\n#include "Object.mqh"\ninput int Period=20;')
    (path.parent / 'Object.mqh').write_text('class CObject {};')
    assert len(parse_source_schema(path, root)['dependencies']) == 2


def test_metadata_bearing_unguarded_dependency_cannot_repeat(tmp_path):
    path, root = source(tmp_path, '#include "inputs.mqh"\n#include "inputs.mqh"')
    (path.parent / 'inputs.mqh').write_text('input int Period=20;')
    with pytest.raises(ValueError, match='unguarded'):
        parse_source_schema(path, root)


def test_conflicting_guard_macros_do_not_create_phantom_inputs(tmp_path):
    path, root = source(tmp_path, '#define INPUTS_GUARD\n#include "inputs.mqh"\ninput int Period=20;')
    (path.parent / 'inputs.mqh').write_text('#ifndef INPUTS_GUARD\n#define INPUTS_GUARD\ninput int Hidden=3;\n#endif')
    with pytest.raises(ValueError, match='guard collides'):
        parse_source_schema(path, root)


@pytest.mark.parametrize('code', [
    '#ifdef UNKNOWN\ninput int X=1;\n#endif',
    '#if 1\n#include "inputs.mqh"\n#endif\ninput int X=1;',
    '#ifdef UNKNOWN\n#define DEFAULT 2\n#endif\ninput int X=DEFAULT;',
    '#define MAKE input int X=2;\nMAKE',
    '#define WORD i##nput\nWORD int X=2;',
    '#define DECLARE \\\n+input int X=2;\nDECLARE',
    'input int X[]= {1,2};',
    'input UnknownType X=1;',
    'enum Mode { A=0, B=A+1 };\ninput Mode X=A;',
    'input int X=2+2;',
    'input int X=1;\nvoid OnTesterInit() {}',
    'input int X=1;\nvoid Test(){ParameterSetRange("X",true,1,1,1,3);}',
])
def test_unsupported_metadata_fails_closed(tmp_path, code):
    path, root = source(tmp_path, code)
    with pytest.raises(ValueError):
        parse_source_schema(path, root)


def test_unrelated_conditional_and_enum_code_does_not_break_metadata(tmp_path):
    path, root = source(tmp_path, '''
enum LibraryMode { A=SomeLibraryConstant(), B };
#ifdef UNKNOWN
void ExtraFunction(){ Print("input enum sinput"); }
#endif
input int Length=20;
void Foo(){ Print("input int Fake=1;"); }
''')
    assert set(parse_source_schema(path, root)['inputs']) == {'Length'}


def test_escape_include_cannot_read_outside_root(tmp_path):
    path, root = source(tmp_path, '#include "../../secret.mqh"\ninput int X=1;')
    (tmp_path / 'secret.mqh').write_text('input int Hidden=1;')
    with pytest.raises(ValueError, match='escapes'):
        parse_source_schema(path, root)


def test_string_literals_escape_backslash_and_empty_default(tmp_path):
    path, root = source(tmp_path, r'input string Directory="C:\\folder";' + '\ninput string Empty="";')
    inputs = parse_source_schema(path, root)['inputs']
    assert inputs['Directory']['value'] == 'C:\\folder'
    assert inputs['Empty']['value'] == ''


def test_native_set_import_derives_types_and_fixed_only_from_verified_source(tmp_path):
    path, root = source(tmp_path, 'sinput int Magic=12;\ninput ENUM_MA_METHOD Method=MODE_SMA;\ninput bool Filter=true;')
    schema = parse_source_schema(path, root)
    set_path = tmp_path / 'native.set'
    set_path.write_text('Magic=42||42||1||42||N\nMethod=0||0||1||1||Y\nFilter=true||false||1||true||Y\n')
    imported = import_native_set(set_path, schema)
    assert imported['type_authority'] == 'verified_source_tree'
    assert imported['parameters']['Magic'] == {'type': 'int', 'value': 42}
    assert imported['parameters']['Method']['enum_values']['MODE_EMA'] == 1
    assert imported['parameters']['Filter']['step'] == 1
    set_path.write_text(set_path.read_text().replace('Magic=42||42||1||42||N', 'Magic=42||42||1||44||Y'))
    with pytest.raises(ValueError, match='fixed-only'):
        import_native_set(set_path, schema)


def test_forged_schema_or_changed_source_is_not_type_authority(tmp_path):
    path, root = source(tmp_path, 'input int Length=20;')
    schema = parse_source_schema(path, root)
    set_path = tmp_path / 'native.set'
    set_path.write_text('Length=20||20||1||22||Y')
    forged = dict(schema, schema_sha256='0'*64)
    with pytest.raises(ValueError, match='changed'):
        import_native_set(set_path, forged)
    path.write_text('input int Length=30;')
    with pytest.raises(ValueError, match='changed'):
        import_native_set(set_path, schema)


@pytest.mark.parametrize('data', ['Length=20\nOther=30', 'Length=20\nLength=30',
                                 'Length=nan', 'Length=2147483648', 'Length=20||30||1||20||Y',
                                 'Length=20||20||0.5||30||Y', 'Length=20||20||0||30||Y', 'Length=20||20||1||30||M'])
def test_bad_native_set_rejected(tmp_path, data):
    path, root = source(tmp_path, 'input int Length=20;')
    set_path = tmp_path / 'native.set'
    set_path.write_text(data)
    with pytest.raises(ValueError):
        import_native_set(set_path, parse_source_schema(path, root))
