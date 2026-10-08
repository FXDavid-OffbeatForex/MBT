"""Local HTML presentation of one native MT5 EA backtest, without optimization."""
from __future__ import annotations

import hashlib
import configparser
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote

from .report_balance_curve import _DealTable, deal_balance_points
from .report_optimization_html import _CSS, _balance_charts, _definition_list, _fmt, _text
from .report_hover import HOVER_SCRIPT


class _Rows(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.row, self.cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self.row = []
        elif tag in ('td', 'th') and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None:
            self.row.append(' '.join(''.join(self.cell).split()))
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row = self.cell = None


def render_single_backtest_report(result: dict) -> str:
    """Render native report data, never a requested-settings substitute or equity guess."""
    from .tester import _read_text_any, parse_tester_report
    native = Path(result.get('native_report_html') or result['report_html']).resolve()
    if not native.is_file() or not 0 < native.stat().st_size <= 32 * 1024 * 1024:
        raise ValueError('Native report is missing or too large')
    if result.get('error') or result.get('timed_out'):
        raise ValueError('Failed or timed-out backtests cannot receive a completed report')
    markup = _read_text_any(str(native))
    parser = _Rows()
    parser.feed(markup)
    settings, inputs, reading_inputs = {}, [], False
    allowed = {'Expert:', 'Symbol:', 'Period:', 'Currency:', 'Initial Deposit:', 'Leverage:'}
    for row in parser.rows:
        cells = [cell for cell in row if cell]
        if not cells:
            continue
        if cells[0] == 'Inputs:':
            reading_inputs = True
            inputs.extend(cells[1:])
            continue
        if reading_inputs:
            if all('=' in cell for cell in cells):
                inputs.extend(cells)
                continue
            reading_inputs = False
        for index, cell in enumerate(cells[:-1]):
            if cell in allowed:
                settings[cell.rstrip(':')] = cells[index + 1]
    if not all(key in settings for key in ('Expert', 'Symbol', 'Period')):
        raise ValueError('Native report lacks readable EA/symbol/period identity')
    configuration_note = 'Execution configuration unavailable; no delay was inferred.'
    ini_raw = result.get('ini')
    if ini_raw:
        ini_path = Path(ini_raw).resolve()
        if ini_path.parent == native.parent and ini_path.is_file() and ini_path.stat().st_size <= 1024 * 1024:
            try:
                ini = configparser.ConfigParser(interpolation=None)
                ini.read_string(_read_text_any(str(ini_path)))
                delay = ini.getint('Tester', 'ExecutionMode')
                from .tester import _validate_execution_delay
                _validate_execution_delay(delay)
                settings['Execution delay (tester configuration)'] = (
                    'Random delay (MT5)' if delay == -1 else 'No delay' if delay == 0 else f'Fixed {delay:,} ms')
                settings['Model (tester configuration)'] = {
                    '0': 'Every tick', '1': '1 minute OHLC', '2': 'Open prices only',
                    '3': 'Math calculations', '4': 'Every tick based on real ticks'
                }.get(ini.get('Tester', 'Model', fallback=''), 'Unknown')
                configuration_note = ('Execution settings are read from the linked tester INI. '
                    'Random delay is stochastic; repeated tests may differ.' if delay == -1 else
                    'Execution settings are read from the linked tester INI. Delay sensitivity depends on the EA and price model.')
            except (ValueError, configparser.Error, OSError):
                pass
    metrics = {key: value for key, value in parse_tester_report(str(native)).items()
               if not key.startswith('_')}
    if not {'net_profit', 'total_trades'} <= metrics.keys():
        raise ValueError('Native report lacks required result metrics')
    output = native.with_name(native.stem + '_mbt.html')
    try:
        points = deal_balance_points(native)
        charts = _balance_charts(points, 'Standalone backtest')
    except (OSError, ValueError, UnicodeError):
        charts = '<p class="empty">Chronological deal balances unavailable. No curve inferred from summary statistics.</p>'
    deals = _DealTable()
    deals.feed(markup)
    headers = ['Time', 'Deal', 'Symbol', 'Type', 'Direction', 'Volume', 'Price',
               'Order', 'Commission', 'Swap', 'Profit', 'Balance', 'Comment']
    # Bound presentation independently of chart parsing; the native report keeps all rows.
    rows = deals.rows[:2000]
    history = ('<p class="muted">Recorded deal events, not a list of completed trades. '
               'Showing up to 2,000 events; the original report contains the full history.</p>'
               '<div class="table-wrap"><table><thead><tr>'
               + ''.join(f'<th>{_text(cell)}</th>' for cell in headers)
               + '</tr></thead><tbody>'
               + ''.join('<tr>' + ''.join(f'<td>{_text(cell)}</td>' for cell in row) + '</tr>' for row in rows)
               + '</tbody></table></div>') if rows else '<p class="empty">Deal history unavailable in this report format.</p>'
    labels = {'net_profit': 'Net profit', 'total_trades': 'Trades', 'profit_factor': 'Profit factor',
              'balance_dd_max': 'Maximum balance drawdown', 'equity_dd_max': 'Maximum equity drawdown'}
    cards = '<div class="metric-grid">' + ''.join(
        f'<div class="metric"><span>{_text(label)}</span><strong>{_fmt(metrics.get(key))}</strong></div>'
        for key, label in labels.items()) + '</div>'
    input_html = '<ul>' + ''.join(f'<li>{_text(value)}</li>' for value in inputs) + '</ul>' if inputs else '<p class="muted">No inputs listed in the native report.</p>'
    links = f'<a class="artifact" href="{quote(native.name, safe="")}">Original MT5 report</a>'
    for raw in (result.get('ini'), str(native.with_suffix('.set'))):
        if raw:
            path = Path(raw).resolve()
            if path.parent == output.parent and path.is_file():
                links += f' <a class="artifact" href="{quote(path.name, safe="")}">{_text(path.suffix.upper()[1:])} file</a>'
    document = f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>MBT | Standalone EA backtest</title>
<style>{_CSS} table{{width:100%;border-collapse:collapse}}td,th{{padding:9px;text-align:left;border-bottom:1px solid #24354b}}.table-wrap{{overflow:auto}}.metric strong{{font-size:24px}}</style>
</head><body><main class="shell"><div class="topline"><span class="brand">MBT</span><span>Native MT5 execution</span></div>
<h1>Standalone EA backtest</h1><p class="lead">One fixed-input historical test. Account amounts use the report's currency. Historical results do not predict future returns.</p>
{cards}<section class="card"><h2>Test settings</h2>{_definition_list(settings)}<h3>EA inputs</h3>{input_html}
<p class="muted">{_text(configuration_note)}</p></section>
<section class="card"><h2>Balance and drawdown</h2>{charts}<p class="muted">No measured equity series was captured for this standalone test. Native equity drawdown is a summary statistic, not an equity curve.</p></section>
<section class="card"><h2>Statistics</h2>{_definition_list({key.replace('_', ' ').capitalize(): value for key, value in metrics.items()})}</section>
<section class="card"><h2>Deal history</h2>{history}</section><section class="card"><h2>Source files</h2>{links}
<p class="runid">Native report SHA-256: {hashlib.sha256(native.read_bytes()).hexdigest()}</p></section></main></body></html>'''
    document = document.replace('</body>', HOVER_SCRIPT + '</body>')
    from .chart_launcher import chart_button
    document = document.replace('<h1>Standalone EA backtest</h1>',
                                '<h1>Standalone EA backtest</h1>' + chart_button(native, output))
    output.write_text(document, encoding='utf-8')
    return str(output)
