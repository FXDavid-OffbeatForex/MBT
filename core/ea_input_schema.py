"""Read-only, bounded MQL5 input metadata. Never evaluate or execute EA code.

This is a conservative declaration parser, not an MQL compiler. Every accepted
source/include file is hashed; opaque binaries and dynamic tester ranges remain
outside its authority. Native SET files provide values/ranges, never types.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import re

MAX_FILES = 128
MAX_BYTES = 8 * 1024 * 1024
BUILTIN_ENUMS = {
    'ENUM_MA_METHOD': dict(MODE_SMA=0, MODE_EMA=1, MODE_SMMA=2, MODE_LWMA=3),
    'ENUM_APPLIED_PRICE': dict(PRICE_CLOSE=1, PRICE_OPEN=2, PRICE_HIGH=3, PRICE_LOW=4,
                               PRICE_MEDIAN=5, PRICE_TYPICAL=6, PRICE_WEIGHTED=7),
    'ENUM_DAY_OF_WEEK': dict(SUNDAY=0, MONDAY=1, TUESDAY=2, WEDNESDAY=3,
                             THURSDAY=4, FRIDAY=5, SATURDAY=6),
    'ENUM_TIMEFRAMES': {
        'PERIOD_CURRENT': 0,
        **{f'PERIOD_M{v}': v for v in (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30)},
        **{f'PERIOD_H{v}': 16384 + v for v in (1, 2, 3, 4, 6, 8, 12)},
        'PERIOD_D1': 16408, 'PERIOD_W1': 32769, 'PERIOD_MN1': 49153,
    },
}
_PRIMITIVES = {'int', 'long', 'float', 'double', 'bool', 'string'}
_LITERAL = r'"(?:\\.|[^"\\])*"'
_TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|/\*[\s\S]*?\*/|//[^\n]*')
_INPUT = re.compile(
    r'\b(?P<storage>static\s+input|sinput|input)\b\s*'
    r'(?:\(\s*name\s*=\s*(?P<label>' + _LITERAL + r')\s*\)\s*)?'
    r'(?P<type>[A-Za-z_]\w*)\s+(?P<name>[A-Za-z_]\w*)\s*=\s*'
    r'(?P<value>(?:' + _LITERAL + r'|[^;"\\])+);', re.S)
_GROUP = re.compile(r'\binput\s+group\s+(' + _LITERAL + r')\s*;?')


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def _comments(code: str) -> str:
    return _TOKEN.sub(lambda m: m[0] if m[0].startswith('"') else
                      ''.join('\n' if ch == '\n' else ' ' for ch in m[0]), code)


def _mask_strings(code: str) -> str:
    return re.sub(_LITERAL, lambda m: ' ' * len(m[0]), code)


def _string(literal: str) -> str:
    # Deliberately bounded escape handling; no Python/MQL eval and no accidental
    # reinterpretation of UTF-8 text via unicode_escape.
    if not re.fullmatch(_LITERAL, literal):
        raise ValueError('Unsupported MQL string literal')
    def decode_escape(match):
        if match[1] not in '\\"nrt':
            raise ValueError('Unsupported MQL string escape')
        return {'n': '\n', 'r': '\r', 't': '\t'}.get(match[1], match[1])
    return re.sub(r'\\(.)', decode_escape, literal[1:-1])


def _integer(value: str, constants: dict) -> int:
    value = value.strip()
    if value in constants:
        value = str(constants[value])
    if not re.fullmatch(r'[+-]?(?:0[xX][0-9a-fA-F]+|\d+)', value):
        raise ValueError('Enum members require integer literals or already-known constants')
    return int(value, 16 if 'x' in value.lower() else 10)


def _strip_guard(code: str) -> tuple[str, str | None]:
    lines = code.splitlines(keepends=True)
    positions = [i for i, line in enumerate(lines) if line.strip()]
    if len(positions) >= 3:
        first, second, last = positions[0], positions[1], positions[-1]
        match = re.fullmatch(r'\s*#\s*ifndef\s+([A-Za-z_]\w*)\s*', lines[first])
        if (match and re.fullmatch(r'\s*#\s*define\s+' + re.escape(match[1]) + r'\s*', lines[second])
                and re.fullmatch(r'\s*#\s*endif\s*', lines[last])):
            # Ensure this endif closes the first guard, not a separate block.
            depth = 0
            for index in range(first, last + 1):
                directive = re.match(r'\s*#\s*(if|ifdef|ifndef|endif|else|elif)\b', lines[index])
                if directive:
                    if directive[1] in ('if', 'ifdef', 'ifndef'):
                        depth += 1
                    elif directive[1] == 'endif':
                        depth -= 1
                    elif depth == 1:
                        break
                    if depth == 0 and index != last:
                        break
            else:
                if depth == 0:
                    for index in (first, second, last):
                        lines[index] = '\n'
                    return ''.join(lines), match[1]
    return code, None


def parse_source_schema(source: Path, mql5_root: Path | None = None) -> dict:
    """Resolve confined includes and extract verified declaration metadata.

    If no root is supplied, infer an ancestor literally named MQL5; otherwise
    use source.parent (suitable for bounded standalone fixtures). Conditional
    blocks that could alter inputs, enums, includes or macros fail closed.
    """
    source = Path(source).resolve()
    if mql5_root is None:
        mql5_root = next((p for p in source.parents if p.name.casefold() == 'mql5'), source.parent)
    root = Path(mql5_root).resolve()
    if not source.is_relative_to(root):
        raise ValueError('EA source is outside its MQL5 source root')
    dependencies, seen, active, constants, macro_names = [], {}, set(), {}, set()
    total = 0

    def visit(path: Path) -> str:
        nonlocal total
        path = path.resolve()
        if not path.is_relative_to(root) or path.suffix.lower() not in ('.mq5', '.mqh'):
            raise ValueError('Include escapes MQL5 or is not MQL source')
        if path in seen:
            if not seen[path]:
                raise ValueError('Repeated or cyclic unguarded include is unsupported')
            return ''
        if len(seen) >= MAX_FILES or not path.is_file():
            raise ValueError('Missing include or source tree exceeds file limit')
        if path.stat().st_size > MAX_BYTES - total:
            raise ValueError('EA source tree exceeds byte limit')
        raw = path.read_bytes()
        total += len(raw)
        if total > MAX_BYTES:
            raise ValueError('EA source tree exceeds byte limit')
        try:
            code = raw.decode('utf-16') if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else raw.decode('utf-8-sig')
        except UnicodeError as exc:
            raise ValueError('Unsupported EA source encoding') from exc
        dependencies.append({'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()})
        code, guard = _strip_guard(_comments(code))
        if guard:
            if guard in macro_names:
                raise ValueError('Include guard collides with an already-defined macro')
            macro_names.add(guard)
        seen[path] = guard
        active.add(path)
        # Runtime range changes cannot be justified by a saved SET file alone.
        if re.search(r'\b(?:ParameterSetRange|OnTesterInit)\b', _mask_strings(code)):
            raise ValueError('Runtime tester ranges unsupported: supply a separate reviewed native range metadata contract; SET files alone cannot establish effective ranges')
        output, conditional = [], []
        for line in code.splitlines(keepends=True):
            match = re.match(r'\s*#\s*([A-Za-z_]\w*)\b(.*)', line)
            if not match:
                if conditional and re.search(r'\b(?:input|sinput|enum)\b', _mask_strings(line)):
                    raise ValueError('Unresolved conditional compilation affects EA input metadata')
                output.append(line if not conditional else '\n')
                continue
            directive, argument = match[1], match[2].strip()
            if directive in ('if', 'ifdef', 'ifndef'):
                conditional.append(directive)
            elif directive in ('else', 'elif'):
                if not conditional:
                    raise ValueError('Unbalanced preprocessor condition')
            elif directive == 'endif':
                if not conditional:
                    raise ValueError('Unbalanced preprocessor condition')
                conditional.pop()
            elif conditional:
                if directive in ('include', 'define', 'undef'):
                    raise ValueError('Unresolved conditional compilation affects dependencies or macros')
            elif directive == 'include':
                include = re.fullmatch(r'(?:(<)([^<>\r\n]+)>|"([^"\r\n]+)")', argument)
                if not include:
                    raise ValueError('Macro-generated include paths are unsupported')
                relative = (include[2] or include[3]).replace('\\', '/')
                if ':' in relative or relative.startswith('/'):
                    raise ValueError('Absolute include path is unsupported')
                base = root / 'Include' if include[1] else path.parent
                output.append(visit(base / relative))
            elif directive == 'define':
                if '\\' in argument:
                    raise ValueError('Multiline metadata macro definitions are unsupported')
                macro_name = re.match(r'([A-Za-z_]\w*)', argument)
                if not macro_name:
                    raise ValueError('Malformed preprocessor macro')
                if macro_name[1] in macro_names:
                    raise ValueError('Repeated metadata macro definition is unsupported')
                macro_names.add(macro_name[1])
                numeric = re.fullmatch(r'([A-Za-z_]\w*)\s+([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)', argument)
                if numeric:
                    if numeric[1] in constants:
                        raise ValueError('Repeated metadata macro definition is unsupported')
                    constants[numeric[1]] = numeric[2]
                elif re.search(r'\b(?:input|sinput|enum|ParameterSetRange|OnTesterInit)\b|##', argument):
                    raise ValueError('Macro-generated EA input/range metadata is unsupported')
                # Other macros cannot authorize a type/default: those uses will
                # fail declaration parsing rather than trusting their expansion.
            elif directive not in ('property', 'pragma'):
                raise ValueError('Unsupported preprocessor directive: ' + directive)
            output.append('\n')
        if conditional:
            raise ValueError('Unbalanced preprocessor condition')
        active.remove(path)
        expanded_part = ''.join(output)
        # Standard library dependencies commonly repeat Object.mqh without an
        # explicit include guard. Re-reading metadata-free declarations adds no
        # input authority; metadata-bearing unguarded repeats still fail closed.
        if guard is None and not re.search(r'\b(?:input|sinput|enum)\b', _mask_strings(expanded_part)):
            seen[path] = 'metadata_free'
        return expanded_part

    expanded = visit(source)
    masked = _mask_strings(expanded)
    enum_bodies = {}
    for match in re.finditer(r'\benum\s+([A-Za-z_]\w*)\s*\{([^{}]*)\}\s*;', masked, re.S):
        if match[1] in enum_bodies or match[1] in BUILTIN_ENUMS:
            raise ValueError('Duplicate or reserved enum definition')
        enum_bodies[match[1]] = match[2]
    inputs, group, events = {}, None, []
    events.extend((m.start(), 'group', m) for m in _GROUP.finditer(expanded) if not masked[m.start()].isspace())
    events.extend((m.start(), 'input', m) for m in _INPUT.finditer(expanded) if not masked[m.start()].isspace())
    consumed = list(masked)
    for offset, kind, match in sorted(events, key=lambda item: item[0]):
        consumed[match.start():match.end()] = ' ' * (match.end() - match.start())
        if kind == 'group':
            group = _string(match[1])
            continue
        typ, name, literal = match['type'], match['name'], match['value'].strip()
        if name in inputs or len(name) > 63:
            raise ValueError('Duplicate or invalid EA input name')
        members = None
        if typ not in _PRIMITIVES:
            members = BUILTIN_ENUMS.get(typ)
            if members is None and typ in enum_bodies:
                members, previous = {}, -1
                for declaration in enum_bodies[typ].rstrip().rstrip(',').split(','):
                    part = re.fullmatch(r'\s*([A-Za-z_]\w*)(?:\s*=\s*(.*?))?\s*', declaration, re.S)
                    if not part or part[1] in members:
                        raise ValueError('Unsupported enum member declaration')
                    number = _integer(part[2], {**constants, **members}) if part[2] is not None else previous + 1
                    if not -2**31 <= number < 2**31 or number in members.values():
                        raise ValueError('Enum value overflow or aliases are unsupported')
                    members[part[1]], previous = number, number
            if not members:
                raise ValueError('Unsupported or unresolved EA input type: ' + typ)
        if literal in constants:
            literal = str(constants[literal])
        if members is not None:
            value = _integer(literal, members)
            if value not in members.values():
                raise ValueError('EA enum default is not a declared member')
        elif typ == 'string':
            value = _string(literal)
        elif typ == 'bool':
            if literal.lower() not in ('true', 'false', '0', '1'):
                raise ValueError('Unsupported EA boolean default')
            value = literal.lower() in ('true', '1')
        else:
            try:
                number = Decimal(literal)
            except InvalidOperation as exc:
                raise ValueError('Unsupported EA numeric default expression') from exc
            if not number.is_finite() or not math.isfinite(float(number)):
                raise ValueError('Nonfinite EA numeric default')
            if typ in ('int', 'long'):
                bits = 32 if typ == 'int' else 64
                if number != number.to_integral_value() or not -2**(bits-1) <= number < 2**(bits-1):
                    raise ValueError('EA integer default is out of bounds')
                value = int(number)
            else:
                value = float(number)
        item = {'type': typ, 'value': value, 'fixed_only': match['storage'] != 'input', 'group': group}
        if match['label']:
            item['display_name'] = _string(match['label'])
        if members is not None:
            item['enum_values'] = dict(members)
        inputs[name] = item
    if re.search(r'\b(?:input|sinput)\b', ''.join(consumed)):
        raise ValueError('EA input declaration cannot be safely parsed')
    schema = {'inputs': inputs, 'dependencies': dependencies, 'source_path': str(source), 'mql5_root': str(root),
              'source_sha256': dependencies[0]['sha256']}
    schema['schema_sha256'] = _digest(schema)
    return schema


def inspect_ea_inputs(expert: str) -> dict:
    """Inspect installed EA source; report binary identity without running MT5."""
    from . import optimization as opt
    data = opt._data_dir()
    binary, source, name = opt._ea_paths(expert, data)
    schema = parse_source_schema(source, data / 'MQL5')
    return {'expert': name, 'binary_path': str(binary), 'binary_sha256': opt._hash(binary),
            'compiled_source_binding': 'adjacent source and timestamp only; not cryptographic proof', **schema}


def import_native_set(path: Path, schema: dict) -> dict:
    """Import native values/ranges using freshly revalidated source authority.

    Types are absent in native SET files. A caller-supplied schema is never
    trusted directly: its source tree is re-read and its schema hash compared.
    """
    verified = parse_source_schema(Path(schema['source_path']), Path(schema['mql5_root']))
    if verified['schema_sha256'] != schema.get('schema_sha256'):
        raise ValueError('EA input metadata or source tree changed')
    path = Path(path)
    if path.suffix.lower() != '.set' or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError('Missing or oversized native SET file')
    raw = path.read_bytes()
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        text = raw.decode('utf-16')
    else:
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = raw.decode('cp1252')
    result = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith(';'):
            continue
        if '=' not in line:
            raise ValueError('Unsupported native SET line')
        name, raw_value = line.split('=', 1)
        if name not in verified['inputs'] or name in result:
            raise ValueError('Unknown or duplicate native SET input')
        meta = verified['inputs'][name]
        parts = raw_value.split('||')
        if len(parts) not in (1, 5) or (len(parts) == 5 and parts[4] not in ('Y', 'N')):
            raise ValueError('Unsupported native SET range format')
        def convert(value):
            if meta['type'] == 'string':
                return value
            if meta['type'] == 'bool':
                if value.lower() not in ('true', 'false', '0', '1'):
                    raise ValueError('Unsupported native SET boolean')
                return value.lower() in ('true', '1')
            if 'enum_values' in meta:
                number = _integer(value, meta['enum_values'])
                if number not in meta['enum_values'].values():
                    raise ValueError('Native SET enum value is undeclared')
                return number
            try:
                number = Decimal(value)
            except InvalidOperation as exc:
                raise ValueError('Invalid native SET number') from exc
            if not number.is_finite() or not math.isfinite(float(number)):
                raise ValueError('Nonfinite native SET number')
            if meta['type'] in ('int', 'long') and number != number.to_integral_value():
                raise ValueError('Native SET integer is fractional')
            if meta['type'] in ('int', 'long'):
                bits = 32 if meta['type'] == 'int' else 64
                if not -2**(bits-1) <= number < 2**(bits-1):
                    raise ValueError('Native SET integer is out of bounds')
            return int(number) if meta['type'] in ('int', 'long') else float(number)
        item = {'type': meta['type'], 'value': convert(parts[0])}
        if 'enum_values' in meta:
            item['enum_values'] = meta['enum_values']
        if len(parts) == 5 and parts[4] == 'Y':
            if meta['fixed_only'] or meta['type'] == 'string':
                raise ValueError('Native SET tries to optimize a fixed-only input')
            item.update(start=convert(parts[1]), stop=convert(parts[3]))
            # Bool/enum steps are ordinary numeric increments, not enum members.
            try:
                step = Decimal(parts[2])
            except InvalidOperation as exc:
                raise ValueError('Invalid native SET step') from exc
            if not step.is_finite() or step <= 0 or not math.isfinite(float(step)):
                raise ValueError('Invalid native SET step')
            item['step'] = int(step) if step == step.to_integral_value() else float(step)
            if meta['type'] in ('int', 'long', 'bool') or 'enum_values' in meta:
                if step != step.to_integral_value():
                    raise ValueError('Native SET discrete range requires an integer step')
            if meta['type'] == 'bool':
                if item['start'] is not False or item['stop'] is not True or step != 1:
                    raise ValueError('Native SET boolean sweep must be false to true with step 1')
            elif item['start'] >= item['stop']:
                raise ValueError('Native SET sweep requires an increasing range')
            elif 'enum_values' in meta:
                count = (item['stop'] - item['start']) // item['step'] + 1
                if count > len(meta['enum_values']) or any(
                        item['start'] + index * item['step'] not in meta['enum_values'].values()
                        for index in range(count)):
                    raise ValueError('Native SET enum sweep includes undeclared values')
        result[name] = item
    if set(result) != set(verified['inputs']):
        raise ValueError('Native SET must name every source-verified EA input')
    return {'parameters': result, 'set_sha256': hashlib.sha256(raw).hexdigest(),
            'schema_sha256': verified['schema_sha256'], 'type_authority': 'verified_source_tree'}
