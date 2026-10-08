"""Standalone dark MT5 optimization report. Never invent time-series evidence."""

from __future__ import annotations

import html
import hashlib
import json
import math
import os
import re
from pathlib import Path
from urllib.parse import quote

from .report_balance_curve import deal_balance_points
from .report_monte_carlo import render_monte_carlo
from .report_hover import HOVER_SCRIPT, point_attributes

_MBT_ROOT = Path(__file__).resolve().parent.parent
_URL_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


def _text(value: object) -> str:
    if value is None:
        return "Unknown"
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return html.escape(str(value), quote=True)


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _fmt(value: object) -> str:
    number = _number(value)
    if number is None:
        return _text(value)
    return f"{number:,.2f}" if not number.is_integer() else f"{number:,.0f}"


def _definition_list(items: dict) -> str:
    if not items:
        return '<p class="empty">Unavailable.</p>'
    return '<dl class="details">' + ''.join(
        f'<dt>{_text(key)}</dt><dd>{_text(value)}</dd>' for key, value in items.items()
    ) + '</dl>'


def _parameter_table(parameters: object, selected: object, schema: object = None) -> str:
    if not isinstance(parameters, dict) or not parameters:
        return '<p class="empty">No EA input settings supplied.</p>'
    selected = selected if isinstance(selected, dict) else {}
    rows = []
    for name, item in parameters.items():
        if not isinstance(item, dict):
            continue
        swept = all(key in item for key in ('start', 'step', 'stop'))
        setting = (f'{_text(item["start"])} to {_text(item["stop"])} '
                   f'(step {_text(item["step"])})') if swept else _text(item.get('value'))
        chosen = selected.get(name) if swept else item.get('value')
        meta = ((schema.get('inputs') or {}).get(name) or {}) if isinstance(schema, dict) else {}
        members = item.get('enum_values') or {}
        def enum_label(value):
            labels = [label for label, number in members.items() if number == value]
            return f'{labels[0]} ({value})' if labels else value
        if members:
            setting = (f'{_text(enum_label(item["start"]))} to {_text(enum_label(item["stop"]))} '
                       f'(step {_text(item["step"])})') if swept else _text(enum_label(item.get('value')))
            chosen = enum_label(chosen)
        rows.append('<tr>' + ''.join(f'<td>{value}</td>' for value in (
            _text(name) + (f'<small class="muted"> — {_text(meta["group"])}</small>' if meta.get('group') else ''),
            _text(item.get('type')) + (' (fixed-only input)' if meta.get('fixed_only') else ''), 'Swept' if swept else 'Fixed',
            setting, _text(chosen),
        )) + '</tr>')
    if not rows:
        return '<p class="empty">No supported EA input settings supplied.</p>'
    return ('<div class="table-wrap"><table class="param-table"><thead><tr>'
            '<th scope="col">EA input</th><th scope="col">Type</th>'
            '<th scope="col">Mode</th><th scope="col">Tested setting</th>'
            '<th scope="col">Selected value</th></tr></thead><tbody>'
            + ''.join(rows) + '</tbody></table></div>')


def _experiment_setup(request: dict, holdout: object) -> str:
    labels = {'expert': 'Expert Advisor', 'symbol': 'Market', 'timeframe': 'Timeframe',
              'from_date': 'Optimization starts', 'to_date': 'Optimization ends',
              'mode': 'Search mode', 'criterion': 'Ranking criterion',
              'forward_mode': 'Native MT5 forward mode', 'forward_date': 'Native forward start',
              'model': 'Price model', 'min_trades': 'Minimum trades for selection'}
    setup = {labels[key]: value for key, value in request.items()
             if key in labels and value is not None}
    for key, value in request.items():
        if key not in labels and key not in ('parameters', 'testing'):
            setup[key] = value
    testing = request.get('testing') or {}
    if testing:
        setup.update({'Initial deposit': testing.get('deposit'),
                      'Account currency': testing.get('currency'),
                      'Leverage': f"1:{testing.get('leverage')}",
                      'Execution delay (ms; -1 means random)': testing.get('execution_delay_ms'),
                      'Costs': (f"Custom: {testing['commission']['per_lot']} {testing.get('currency')} per lot, "
                                f"{testing['commission']['entry']} deals (native MT5)"
                                if testing.get('commission') else 'Broker/history settings; no custom commission override')})
    if isinstance(holdout, dict) and holdout.get('status') == 'completed':
        setup['Independent holdout starts'] = holdout.get('from_date')
        setup['Independent holdout ends'] = holdout.get('to_date')
    return _definition_list(setup)


def _evidence_counts(counts: dict, holdout: object, summary: object = None) -> str:
    if 'planned_combinations' not in counts:
        return _definition_list(counts)
    holdout_count = 1 if isinstance(holdout, dict) and holdout.get('status') == 'completed' else 0
    if isinstance(summary, dict):
        holdout_count = summary.get('holdout_completed', summary.get('completed', 0))
    display = {
        'Planned input combinations': counts.get('planned_combinations'),
        'Saved optimization passes': counts.get('parsed_rows'),
        'Distinct input combinations': counts.get('distinct_parameter_tuples'),
        'Passes eligible for selection': counts.get('eligible_rows'),
        'Native MT5 forward rows': counts.get('forward_rows'),
        'Independent holdout backtests': holdout_count,
        'MT5-reported optimization passes': counts.get('mt5_reported_passes'),
        'Failed passes': (counts.get('failed_rows') if counts.get('failed_rows') is not None
                          else 'Not reported by MT5'),
    }
    if isinstance(summary, dict):
        display.update({'Frozen holdout candidates': summary.get('planned'),
                        'Detailed in-sample backtests': summary.get('in_sample_completed'),
                        'Failed holdout tests': summary.get('holdout_failed', summary.get('failed')),
                        'Failed in-sample tests': summary.get('in_sample_failed', 0 if summary.get('failed') == 0 else None),
                        'Holdout tests not run': summary.get('not_run')})
    note = ('<p class="muted">Native forward rows and independent holdout backtests are '
            'different evidence. A missing failed-pass count means MT5 did not '
            'provide a reliable number.</p>')
    return _definition_list(display) + note


def _detail_graph(detail: object, report_path: Path, title: str) -> str:
    if not isinstance(detail, dict):
        return '<p class="empty">No detailed MT5 deal history was saved for this period.</p>'
    raw = detail.get('report_path')
    if not isinstance(raw, str):
        return '<p class="empty">No detailed MT5 deal history was saved for this period.</p>'
    path = Path(raw).resolve()
    digest = detail.get('report_sha256')
    if (path.parent != report_path.parent or path.suffix.lower() not in ('.htm', '.html')
            or not path.is_file() or not isinstance(digest, str)):
        return '<p class="empty">Verified MT5 deal history is unavailable; no balance curve was inferred.</p>'
    try:
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Report hash mismatch')
        points = deal_balance_points(path)
    except (OSError, ValueError, UnicodeError):
        return '<p class="empty">The saved MT5 deal history could not be verified or parsed; no balance curve was inferred.</p>'
    return _balance_charts(points, title) + _equity_graph(detail, report_path, title)


def _equity_graph(detail: dict, report_path: Path, title: str) -> str:
    from .equity_capture import parse_equity_capture
    capture = detail.get('equity_capture')
    if not isinstance(capture, dict):
        error = detail.get('equity_capture_error')
        return ('<p class="muted">Equity observations not captured. '
                'Use capture_candidate_equity for a verified instrumented replay.'
                + (f' Last attempt: {_text(error)}' if error else '') + '</p>')
    try:
        path = Path(capture['path']).resolve()
        replay = Path(capture['replay_report_path']).resolve()
        if (path.parent != report_path.parent or replay.parent != report_path.parent
                or capture['original_report_sha256'] != detail['report_sha256']
                or not capture.get('original_deal_balances_verified')
                or hashlib.sha256(replay.read_bytes()).hexdigest() != capture['replay_report_sha256']
                or capture['from_date'] != detail['from_date'] or capture['to_date'] != detail['to_date']):
            raise ValueError('Equity provenance mismatch')
        parsed = parse_equity_capture(path, expected_sha256=capture['sha256'], **{
            name: capture[name] for name in ('run_id', 'source_sha256', 'symbol',
                                           'timeframe', 'model', 'from_date', 'to_date')})
        points = [(stamp, equity) for stamp, balance, equity in parsed['points']]
        return '<h3>Measured account equity</h3>' + _balance_charts(points, title, equity=True)
    except (OSError, ValueError, KeyError, TypeError):
        return '<p class="empty">Saved equity evidence could not be verified; no equity curve was inferred.</p>'


def _balance_charts(points: list, title: str, equity: bool = False) -> str:
    """Draw dark, offline SVGs from the report's recorded deal balances."""
    # Preserve the first/last and local extrema when a long deal history is displayed.
    shown = points
    if len(points) > 3000:
        bucket = math.ceil((len(points) - 2) / 1499)
        shown = [points[0]]
        for start in range(1, len(points) - 1, bucket):
            segment = points[start:min(start + bucket, len(points) - 1)]
            low = min(range(len(segment)), key=lambda i: segment[i][1])
            high = max(range(len(segment)), key=lambda i: segment[i][1])
            shown.extend(segment[i] for i in sorted({low, high}))
        shown.append(points[-1])
    times = [(point[0] - shown[0][0]).total_seconds() for point in shown]
    first, last = times[0], times[-1]
    def x(index: int) -> float:
        fraction = ((times[index] - first) / (last - first)) if last > first else index / max(len(shown) - 1, 1)
        return 70 + fraction * 1040
    def chart(values: list[float], heading: str, unit: str, stroke: str) -> str:
        low, high = min(values), max(values)
        padding = max((high - low) * .12, 1 if unit == '%' else .01)
        low, high = low - padding, high + padding
        if unit == '%':
            low, high = 0, max(.1, max(values) * 1.12)
        def y(value: float) -> float:
            return 24 + (high - value) / (high - low) * 246
        path = ' '.join(f'{"M" if i == 0 else "L"}{x(i):.1f},{y(value):.1f}'
                        for i, value in enumerate(values))
        grid = ''.join(
            f'<line x1="70" x2="1110" y1="{y(low + (high-low)*i/4):.1f}" y2="{y(low + (high-low)*i/4):.1f}" stroke="#294157"/>'
            f'<text x="62" y="{y(low + (high-low)*i/4)+4:.1f}" text-anchor="end">{_fmt(low + (high-low)*i/4)}{unit}</text>'
            for i in range(5))
        dates = ((shown[0][0].strftime('%Y-%m-%d'), 70),
                 (shown[-1][0].strftime('%Y-%m-%d'), 1110))
        labels = ''.join(f'<text x="{position}" y="302" text-anchor="{"start" if position == 70 else "end"}">{_text(date)}</text>'
                         for date, position in dates)
        hover = point_attributes([[round(x(i), 3), round(y(value), 3),
                  f'{shown[i][0]:%Y-%m-%d %H:%M:%S} (report time)\n{heading}: {value:,.2f}{unit}\n'
                  f'{"Equity" if equity else "Balance"}: {shown[i][1]:,.2f}\n'
                  f'Drawdown: {display_cash_dd[i]:,.2f} / {display_dd[i]:.2f}%\nNearest displayed recorded observation']
                  for i, value in enumerate(values)])
        return (f'<div class="curve-panel"><div class="chart-head"><h4>{_text(heading)}</h4>'
                f'<span>{_text(len(points))} recorded {"equity" if equity else "balance"} points</span></div>'
                f'<svg viewBox="0 0 1140 320" role="img"{hover} aria-label="{_text(title)} {_text(heading)}">'
                f'{grid}<path d="{path}" fill="none" stroke="{stroke}" stroke-width="2.5" '
                f'stroke-linejoin="round"/>{labels}</svg></div>')
    balances = [value for _, value in shown]
    peak = float('-inf')
    drawdowns = []
    cash_drawdowns = []
    for _, balance in points:
        peak = max(peak, balance)
        drawdowns.append(max(0, (peak - balance) / peak * 100) if peak > 0 else 0)
        cash_drawdowns.append(max(0, peak - balance))
    # Recompute display drawdown from the complete history, never from decimated points.
    by_identity = {id(point): dd for point, dd in zip(points, drawdowns)}
    display_dd = [by_identity[id(point)] for point in shown]
    cash_by_identity = {id(point): dd for point, dd in zip(points, cash_drawdowns)}
    display_cash_dd = [cash_by_identity[id(point)] for point in shown]
    if equity:
        return (f'<div class="curve-grid">{chart(balances, "Measured account equity at tester ticks", "", "#7baeff")}'
                f'{chart(display_dd, "Drawdown from observed equity peak", "%", "#dc84f2")}</div>'
                '<p class="muted curve-caption">Actual ACCOUNT_EQUITY observations from a generated EA replay '
                'verified against original trade balances. Sampled before/after main-symbol ticks; '
                'open-price models remain sparse. This is not every hidden intratick movement or '
                'a replacement for MT5 maximum equity drawdown.</p>')
    return (f'<div class="curve-grid">{chart(balances, "Account balance after each deal", "", "#53dfb8")}'
            f'{chart(display_dd, "Drawdown from recorded balance peak", "%", "#f0a66d")}</div>'
            '<p class="muted curve-caption">Charts use recorded MT5 deal balances, including costs posted at deals. '
            'Drawdown here is sampled at deals and may differ from MT5’s maximum drawdown. '
            'These are not tick-by-tick equity curves.</p>')


def _period_metrics(detail: dict) -> str:
    names = {'net_profit': 'Net profit', 'profit_factor': 'Profit factor',
             'expected_payoff': 'Profit per trade', 'recovery_factor': 'Recovery factor',
             'sharpe_ratio': 'Sharpe ratio', 'total_trades': 'Trades',
             'balance_dd_max': 'Maximum balance drawdown',
             'equity_dd_max': 'Maximum equity drawdown',
             'balance_dd_relative_pct': 'Relative balance drawdown (%)',
             'equity_dd_relative_pct': 'Relative equity drawdown (%)'}
    values = {'From': detail.get('from_date'), 'To': detail.get('to_date')}
    values.update({names.get(key, key): value for key, value in (detail.get('metrics') or {}).items()})
    fee = detail.get('commission_deals') or {}
    if fee:
        values['Native commission proof'] = 'Verified per deal' if fee.get('verified') else 'No trading deals to verify'
        values['Deals checked for commissions'] = fee.get('native_deals_verified')
        values['Total deal commissions'] = fee.get('commission_total')
        values['Tester profile restored'] = (detail.get('commission_profile') or {}).get('status') == 'restored'
    return _definition_list(values)


def _period_report_link(detail: dict, report_path: Path) -> str:
    raw = detail.get('report_path')
    if not isinstance(raw, str):
        return ''
    target = Path(raw).resolve()
    if target.parent != report_path.parent or target.suffix.lower() not in ('.htm', '.html') or not target.is_file():
        return ''
    href = quote(target.name, safe='')
    from .chart_launcher import chart_button
    return (f'<p><a class="artifact" href="{html.escape(href, quote=True)}">'
            '<strong>Open full MT5 test report</strong><span>Settings, statistics, graphs and trade history</span>'
            '</a></p>' + chart_button(target, report_path))


def _result_table(dataset: dict | None, table_id: str) -> str:
    if not dataset:
        return '<p class="empty">No results supplied.</p>'
    columns = list(dataset.get("columns") or [])
    rows = list(dataset.get("rows") or [])
    if not columns and rows:
        columns = list(dict.fromkeys(key for row in rows if isinstance(row, dict) for key in row))
    count = dataset.get("row_count")
    body = ''.join(
        '<tr>' + ''.join(f'<td>{_text(row.get(column))}</td>' for column in columns) + '</tr>'
        for row in rows if isinstance(row, dict)
    ) or f'<tr><td colspan="{max(len(columns), 1)}">No rows supplied.</td></tr>'
    headers = ''.join(f'<th scope="col">{_text(column)}</th>' for column in columns)
    return (f'<p class="table-meta">Reported row count: {_text(count)}. Rows shown: {len(rows)}.</p>'
            f'<div class="table-wrap"><table id="{table_id}"><thead><tr>{headers}</tr></thead>'
            f'<tbody>{body}</tbody></table></div>')


def _artifact_links(artifacts: object, report_path: Path) -> str:
    if not isinstance(artifacts, dict):
        return '<p class="empty">No local artifacts supplied.</p>'
    links = []
    labels = {
        'Optimization XML': ('Optimization data (XML)', 'Raw MT5 table; see Parameter Results for the readable view.'),
        'Forward XML': ('Native forward data (XML)', 'Raw MT5 table; see Forward Check for the readable view.'),
        'Tester INI': ('Optimization settings (INI)', 'Raw instructions sent to MT5, not a report page.'),
        'Tester SET': ('EA input ranges (SET)', 'Raw MT5 input file; Y means swept, N means fixed.'),
        'Selected in-sample HTML': ('Full in-sample MT5 report', 'Detailed selected-pass backtest on training dates.'),
        'Independent holdout HTML': ('Full MT5 holdout report', 'Detailed single-backtest report from MT5.'),
    }
    for label, value in artifacts.items():
        if not isinstance(value, (str, os.PathLike)):
            continue
        raw = os.fspath(value)
        if not raw or any(ord(c) < 32 for c in raw) or raw.startswith(('\\\\', '//')):
            continue
        if _URL_SCHEME.match(raw) and not _WINDOWS_DRIVE.match(raw):
            continue
        target = Path(raw)
        if not target.is_absolute():
            target = report_path.parent / target
        target = target.resolve()
        if not (target.is_relative_to(_MBT_ROOT) or target.is_relative_to(report_path.parent)):
            continue
        try:
            relative = os.path.relpath(target, report_path.parent)
        except ValueError:
            continue
        href = quote(relative.replace('\\', '/'), safe='/')
        title, description = labels.get(label, (str(label), 'Saved source artifact.'))
        links.append(f'<a class="artifact" href="{html.escape(href, quote=True)}">'
                     f'<strong>{_text(title)}</strong><span>{_text(description)}</span></a>')
    return ('<p class="muted">These are original evidence files. XML, INI and SET are raw '
            'inputs/data, not styled reports.</p><div class="artifacts">'
            + ''.join(links) + '</div>') if links else '<p class="empty">No safe local artifact links supplied.</p>'


def _metric_cards(selected: object, counts: dict, holdout: object = None, summary: object = None) -> str:
    cards = [("Planned combinations", counts.get("planned_combinations", counts.get("planned combinations"))),
             ("Saved passes", counts.get("parsed_rows", counts.get("parsed rows"))),
             ("Native forward rows", counts.get("forward_rows", counts.get("forward-tested rows")))]
    if isinstance(summary, dict):
        cards.append(('Independent holdout tests', summary.get('holdout_completed', summary.get('completed', 0))))
    elif isinstance(holdout, dict) and holdout.get('status') == 'completed':
        cards.append(('Independent holdout tests', 1))
    if isinstance(selected, dict):
        cards.extend((label, selected.get(key)) for label, key in
                     (("Selected result", "Result"), ("Selected profit", "Profit"),
                      ("Selected trades", "Trades"), ("Selected equity DD", "Equity DD %")))
    return '<div class="metric-grid">' + ''.join(
        f'<div class="metric"><span>{_text(label)}</span><strong>{_fmt(value)}</strong></div>'
        for label, value in cards if value is not None
    ) + '</div>'


def _bar_chart(rows: list[dict], metric: str, title: str, color: str) -> str:
    points = [(str(row.get("Pass", index)), _number(row.get(metric)))
              for index, row in enumerate(rows) if isinstance(row, dict)]
    points = [(label, value) for label, value in points if value is not None]
    if not points:
        return f'<div class="empty">{_text(title)} unavailable: no {_text(metric)} values in saved rows.</div>'
    points = sorted(points, key=lambda item: item[1], reverse=True)[:24]
    width, height, left, right, top, bottom = 760, 290, 54, 18, 30, 44
    low, high = min(0.0, *(value for _, value in points)), max(0.0, *(value for _, value in points))
    if high == low:
        high = low + 1
    def y(value: float) -> float:
        return top + (high - value) / (high - low) * (height - top - bottom)
    slot = (width - left - right) / len(points)
    baseline = y(0)
    bars = []
    for index, (label, value) in enumerate(points):
        x = left + index * slot + slot * .18
        bar_top = min(y(value), baseline)
        bar_height = max(1, abs(y(value) - baseline))
        row = next((r for r in rows if str(r.get('Pass')) == label), {})
        info = '\n'.join(f'{key}: {item}' for key, item in row.items())
        bars.append(f'<rect x="{x:.1f}" y="{bar_top:.1f}" width="{slot * .64:.1f}" height="{bar_height:.1f}" rx="3" fill="{color}"><title>Pass {_text(label)} / {_text(metric)}: {_fmt(value)}\n{_text(info)}</title></rect>')
    ticks = ''.join(f'<g><line x1="{left}" x2="{width-right}" y1="{y(low+(high-low)*i/4):.1f}" y2="{y(low+(high-low)*i/4):.1f}" stroke="#25344b"/><text x="{left-8}" y="{y(low+(high-low)*i/4)+4:.1f}" text-anchor="end">{_fmt(low+(high-low)*i/4)}</text></g>' for i in range(5))
    return (f'<div class="chart"><div class="chart-head"><h3>{_text(title)}</h3><span>Top {len(points)} saved passes</span></div>'
            f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{_text(title)} bar chart">{ticks}{"".join(bars)}'
            f'<text x="{width/2}" y="{height-8}" text-anchor="middle">Sorted by {_text(metric)} · hover for pass ID</text></svg></div>')


def _heatmap(rows: list[dict], request: dict) -> str:
    params = request.get("parameters") or {}
    if not isinstance(params, dict):
        return ''
    varied = [name for name, item in params.items() if isinstance(item, dict) and "start" in item]
    if len(varied) != 2:
        return '<div class="empty">A two-parameter heatmap appears when exactly two numeric inputs are swept.</div>'
    x_name, y_name = varied
    values = [(row.get(x_name), row.get(y_name), _number(row.get("Result"))) for row in rows]
    values = [(x, y, result) for x, y, result in values if x is not None and y is not None and result is not None]
    if not values:
        return '<div class="empty">Parameter landscape unavailable: input and Result columns were not saved.</div>'
    try:
        xs = sorted({x for x, _, _ in values}, key=float)
        ys = sorted({y for _, y, _ in values}, key=float)
    except (TypeError, ValueError):
        return '<div class="empty">Parameter landscape requires numeric input values.</div>'
    if len(xs) * len(ys) > 600:
        return '<div class="empty">Parameter landscape omitted for a large grid; use the results table.</div>'
    by_pair = {(str(x), str(y)): result for x, y, result in values}
    low, high = min(result for _, _, result in values), max(result for _, _, result in values)
    cells = []
    for y in ys:
        cells.append('<tr><th scope="row">' + _text(y) + '</th>')
        for x in xs:
            result = by_pair.get((str(x), str(y)))
            if result is None:
                cells.append('<td class="heat missing">—</td>')
            else:
                ratio = .5 if high == low else (result - low) / (high - low)
                alpha = .13 + .57 * ratio
                info = f'{x_name}={x}\n{y_name}={y}\nResult: {_fmt(result)}'
                cells.append(f'<td class="heat" style="background:rgba(32,214,177,{alpha:.3f})" data-mbt-tooltip="{_text(info)}">{_fmt(result)}</td>')
        cells.append('</tr>')
    headers = ''.join(f'<th scope="col">{_text(x)}</th>' for x in xs)
    return (f'<div class="chart"><div class="chart-head"><h3>Parameter landscape</h3><span>Result by tested input pair · brighter is higher</span></div>'
            f'<div class="table-wrap"><table class="heatmap"><thead><tr><th>{_text(y_name)} ↓ / {_text(x_name)} →</th>{headers}</tr></thead><tbody>{"".join(cells)}</tbody></table></div></div>')


def _forward_comparison(selected: object, forward: dict | None) -> str:
    if not isinstance(selected, dict) or not forward:
        return '<p class="empty">Selected pass forward comparison unavailable.</p>'
    rows = [row for row in (forward.get('rows') or []) if isinstance(row, dict)]
    for key in ('pass_id', 'Pass', 'Pass ID'):
        if key not in selected or selected[key] is None:
            continue
        matches = [row for row in rows if row.get(key) == selected[key]]
        if len(matches) != 1:
            continue
        match = matches[0]
        common = [column for column in selected if column in match and column != key]
        if not common:
            return '<p class="empty">Matching forward pass found; no comparable values supplied.</p>'
        cells = ''.join(f'<tr><th scope="row">{_text(column)}</th><td>{_text(selected[column])}</td><td>{_text(match[column])}</td></tr>' for column in common)
        return ('<h3>Selected pass: optimization versus forward</h3><div class="table-wrap"><table>'
                '<thead><tr><th>Field</th><th>Optimization</th><th>Forward</th></tr></thead>'
                f'<tbody>{cells}</tbody></table></div>')
    return '<p class="empty">Selected pass forward comparison unavailable: no unique matching pass ID.</p>'


def _holdout_comparison(results: dict, report_path: Path, request: dict) -> str:
    candidates = results.get('holdout_candidates')
    selection = results.get('holdout_selection')
    if not isinstance(candidates, list) or not isinstance(selection, dict):
        return ''
    summary = results.get('holdout_summary') or {}
    swept = {name for name, item in (request.get('parameters') or {}).items()
             if isinstance(item, dict) and 'start' in item}
    table_rows = []
    for candidate in candidates:
        base = candidate.get('optimization_pass') or {}
        detail = candidate.get('holdout') or {}
        metrics = detail.get('metrics') or {}
        winner = base.get('Pass') == (results.get('selected_pass') or {}).get('Pass')
        table_rows.append({
            'IS rank': candidate.get('rank'), 'Pass': str(base.get('Pass')) + (' (IS winner)' if winner else ''),
            'Inputs': ', '.join(f'{name}={value}' for name, value in (candidate.get('parameters') or {}).items()
                               if not swept or name in swept),
            'Status': candidate.get('status'),
            'IS profit': _fmt(base.get('Profit')), 'OOS profit': _fmt(metrics.get('net_profit')),
            'IS trades': _fmt(base.get('Trades')), 'OOS trades': _fmt(metrics.get('total_trades')),
            'IS profit factor': _fmt(base.get('Profit Factor')), 'OOS profit factor': _fmt(metrics.get('profit_factor')),
            'IS profit per trade': _fmt(base.get('Expected Payoff')), 'OOS profit per trade': _fmt(metrics.get('expected_payoff')),
            'IS equity DD %': _fmt(base.get('Equity DD %')),
            'OOS relative equity DD %': _fmt(metrics.get('equity_dd_relative_pct')),
        })
    table = _result_table({'rows': table_rows, 'row_count': len(table_rows)}, 'holdout-candidates-table')
    policy = ('MT5 sizing: 10% complete / 25% genetic, at least 256 where available; applied to distinct eligible passes'
              if selection.get('method') == 'mt5_sized_eligible_subset' else 'Explicit top-N override')
    stats = _definition_list({'Selection policy': policy,
                              'Frozen candidates': selection.get('frozen_count'),
                              'Completed candidate pairs': summary.get('completed'),
                              'Detailed in-sample tests': summary.get('in_sample_completed'),
                              'Out-of-sample tests': summary.get('holdout_completed', summary.get('completed')),
                              'Failed candidate pairs': summary.get('failed'), 'Not run': summary.get('not_run'),
                              'Selection frozen at': selection.get('frozen_at')})
    return (f'<div class="card"><h3>Frozen shortlist: in-sample versus out-of-sample</h3>{stats}'
            '<p class="note">Candidates were chosen by in-sample Result before their holdout tests. '
            'The original in-sample winner remains selected. Later dates were excluded from this optimization; '
            'earlier external inspection is not verified. If you revise the strategy after viewing these results, '
            'reserve a fresh untouched period for the final check.</p>'
            '<p class="muted">The time budget controls execution, without shrinking the frozen selection. '
            'Unrun candidates can be continued later. Periods can have different lengths: '
            'compare trade counts and profit per trade as well as totals. '
            'IS = in-sample; OOS = out-of-sample. Missing results stay unknown.</p>'
            f'{_paired_profit_chart(candidates)}{table}</div>')


def _candidate_accordions(results: dict, report_path: Path, period: str) -> str:
    """Every candidate has the same embedded charts, loaded only when opened."""
    candidates = results.get('holdout_candidates') or []
    selected = results.get('selected_pass') or {}
    label = 'In-sample' if period == 'in_sample' else 'Out-of-sample'
    tab = 'selected' if period == 'in_sample' else 'forward'
    group = f'mbt-{period}-candidates'
    sections = []
    for index, candidate in enumerate(candidates, 1):
        base = candidate.get('optimization_pass') or {}
        winner = base.get('Pass') == selected.get('Pass')
        detail = candidate.get(period) or (results.get(period) if winner else None)
        heading = f'Pass {base.get("Pass")} / {label.lower()}'
        if isinstance(detail, dict) and detail.get('status') == 'completed':
            page = report_path.with_name(f'{report_path.stem}_{period}_{index}.html')
            page.write_text(
                '<!doctype html><html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                f'<title>MBT | {_text(heading)}</title><style>{_CSS}'
                'body{background:#0e1a2a}.shell{padding:18px 0 0}.card{border:0;box-shadow:none;margin:0;padding:0}'
                '</style></head><body><main class="shell">'
                f'<h2>{_text(heading)}</h2><div class="card">'
                f'{_definition_list(candidate.get("parameters") or {})}{_period_metrics(detail)}'
                f'{_detail_graph(detail, report_path, heading)}{_period_report_link(detail, report_path)}'
                f'<p><a href="{quote(report_path.name, safe="")}#{tab}" target="_top">Back to experiment</a></p>'
                '</div></main><script>function sendHeight(){parent.postMessage({kind:"mbt-detail-height",'
                'height:Math.ceil(document.body.getBoundingClientRect().height)},"*");}'
                'new ResizeObserver(sendHeight).observe(document.body);window.addEventListener("load",sendHeight);'
                'window.addEventListener("message",e=>{if(e.data?.kind==="mbt-request-height")sendHeight();});'
                '</script>' + HOVER_SCRIPT + '</body></html>', encoding='utf-8')
            src = quote(page.name, safe='')
            source_attr = f'src="{src}"' if winner else f'data-src="{src}"'
            content = (f'<iframe class="candidate-frame" {source_attr} loading="lazy" '
                       f'title="{_text(heading)} balance and drawdown"></iframe>'
                       f'<p><a class="artifact" href="{src}" target="_blank" rel="noopener">'
                       '<strong>Open this detailed report separately</strong></a></p>')
        else:
            error = (candidate.get('error') if candidate.get('failed_period', 'holdout') == period else None)
            content = '<p class="empty">' + _text(error or f'Detailed {label.lower()} test not available yet.') + '</p>'
        sections.append(f'<details class="candidate-detail" name="{group}" '
                        f'data-group="{group}" data-pass="{_text(base.get("Pass"))}"'
                        f'{" open" if winner else ""}><summary>Rank {_text(candidate.get("rank"))} / '
                        f'pass {_text(base.get("Pass"))}{" · In-sample winner" if winner else ""}</summary>'
                        f'{content}</details>')
    return (f'<div class="card"><h3>{label} candidate reports</h3>'
            '<p class="muted">Open a candidate to see its statistics, balance and drawdown. '
            'Only one candidate is open at a time.</p>' + ''.join(sections) + '</div>')


def _paired_profit_chart(candidates: list[dict]) -> str:
    pairs = [(c.get('optimization_pass') or {}, ((c.get('holdout') or {}).get('metrics') or {}))
             for c in candidates]
    values = [v for base, metrics in pairs for v in
              (_number(base.get('Profit')), _number(metrics.get('net_profit'))) if v is not None]
    if not values:
        return ''
    low, high = min(0, *values), max(0, *values)
    if high == low:
        high = low + 1
    def y(value: float) -> float:
        return 24 + (high - value) / (high - low) * 226
    baseline, slot = y(0), 1040 / max(len(pairs), 1)
    bars = []
    for index, (base, metrics) in enumerate(pairs):
        for series, value in enumerate((_number(base.get('Profit')), _number(metrics.get('net_profit')))):
            if value is None:
                continue
            bars.append(f'<rect x="{70 + slot*(index + .15 + series*.35):.1f}" '
                        f'y="{min(y(value), baseline):.1f}" width="{slot*.30:.1f}" '
                        f'height="{max(1, abs(y(value)-baseline)):.1f}" rx="3" '
                        f'fill="{("#4eddb6", "#72a7ff")[series]}"><title>'
                        f'Pass {_text(base.get("Pass"))} {("IS", "OOS")[series]} profit: {_fmt(value)}</title></rect>')
        bars.append(f'<text x="{70 + slot*(index+.5):.1f}" y="280" text-anchor="middle">'
                    f'Pass {_text(base.get("Pass"))}</text>')
    ticks = ''.join(f'<line x1="70" x2="1110" y1="{y(low+(high-low)*i/4):.1f}" '
                    f'y2="{y(low+(high-low)*i/4):.1f}" stroke="#294157"/>'
                    f'<text x="62" y="{y(low+(high-low)*i/4)+4:.1f}" text-anchor="end">'
                    f'{_fmt(low+(high-low)*i/4)}</text>' for i in range(5))
    return ('<div class="curve-panel"><div class="chart-head"><h4>Profit by frozen candidate</h4>'
            '<span><span style="color:#4eddb6">In-sample</span> / '
            '<span style="color:#72a7ff">Out-of-sample</span></span></div>'
            f'<svg viewBox="0 0 1140 300" role="img" aria-label="In-sample and out-of-sample profit comparison">'
            f'{ticks}{"".join(bars)}</svg></div>')


_CSS = """
:root{font-family:Inter,ui-sans-serif,system-ui,Segoe UI,sans-serif;color-scheme:dark;background:#09111e;color:#e8f1ff}*{box-sizing:border-box}
body{margin:0;background:radial-gradient(circle at 80% -20%,#183451 0,transparent 40%),#09111e;color:#e8f1ff}.shell{max-width:1460px;margin:auto;padding:28px 32px 72px}
.topline{display:flex;justify-content:space-between;align-items:center;color:#8ca3bd;font-size:12px;text-transform:uppercase;letter-spacing:.18em}.brand{color:#45e0bb;font-weight:800;letter-spacing:.2em}
h1{font-size:clamp(30px,4vw,48px);letter-spacing:-.045em;margin:26px 0 8px}h2{font-size:21px;letter-spacing:-.02em;margin:0 0 18px}h3{font-size:15px;margin:0 0 12px}
.lead,.muted,.table-meta{color:#9aafc5}.lead{max-width:760px;line-height:1.6}.status{display:inline-block;border:1px solid #2a765e;color:#69e5bd;background:#103629;padding:7px 12px;border-radius:999px;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.08em}
.hero{display:flex;justify-content:space-between;gap:20px;align-items:end;margin-bottom:28px}.runid{font-family:ui-monospace,Consolas,monospace;color:#69809b;font-size:12px;word-break:break-all}
.tabs{position:sticky;top:0;z-index:2;display:flex;gap:6px;padding:10px 0;background:#09111ef2;border-bottom:1px solid #26364a;overflow:auto}.tabs button{background:transparent;color:#9aafc5;border:0;border-radius:8px;padding:11px 17px;font:inherit;font-size:13px;font-weight:700;cursor:pointer;white-space:nowrap}.tabs button[aria-selected="true"]{background:#173e41;color:#69e5bd}
.panel{padding-top:28px}.panel[hidden]{display:none}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.card,.chart{border:1px solid #24354b;background:linear-gradient(145deg,#111f31,#0e1a2a);border-radius:16px;padding:24px;box-shadow:0 18px 35px #0002}.card{margin-bottom:18px}
.metric-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:12px;margin:0 0 18px}.metric{background:#11253a;border:1px solid #25405a;border-radius:12px;padding:17px}.metric span{display:block;color:#8fa9c2;font-size:12px;margin-bottom:10px}.metric strong{font-size:23px;letter-spacing:-.03em;font-variant-numeric:tabular-nums}
.details{display:grid;grid-template-columns:minmax(150px,230px) 1fr;gap:0;margin:0}.details dt,.details dd{margin:0;padding:10px 0;border-bottom:1px solid #203247;overflow-wrap:anywhere}.details dt{color:#8da5bd}.details dd{font-family:ui-monospace,Consolas,monospace;font-size:13px}
.chart{margin-bottom:18px}.chart-head{display:flex;justify-content:space-between;gap:15px;align-items:baseline;margin-bottom:14px}.chart-head span{color:#8fa8be;font-size:12px}.chart svg{width:100%;height:auto;max-height:340px}svg text{fill:#8fa8be;font:11px ui-sans-serif,system-ui}.empty{padding:20px;border:1px dashed #34506a;border-radius:10px;color:#8fa8be;background:#102236}
.table-wrap{overflow:auto;max-height:680px}table{border-collapse:collapse;width:100%;font-size:12px;font-variant-numeric:tabular-nums}th,td{text-align:left;padding:12px 14px;border-bottom:1px solid #23354b;white-space:nowrap}thead th{position:sticky;top:0;background:#1b3046;color:#c7d9ec;z-index:1}tbody tr:hover{background:#18344a}.heatmap td{text-align:center;font-weight:700}.heatmap th{min-width:95px}.heat.missing{color:#6d8095}
.filter{display:block;margin:0 0 17px;color:#a4bad0;font-size:13px}input{display:block;margin-top:7px;background:#0a1828;color:#e8f1ff;border:1px solid #35516b;border-radius:8px;padding:11px 13px;width:min(100%,360px);font:inherit}input:focus,button:focus-visible,a:focus-visible{outline:2px solid #64e9c5;outline-offset:2px}
.artifacts{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}.artifact{display:block;border:1px solid #35516b;background:#112a3e;color:#9be9d1;border-radius:8px;padding:13px;text-decoration:none;font-size:12px}.artifact strong,.artifact span{display:block}.artifact strong{font-size:13px}.artifact span{color:#9aafc5;margin-top:7px;line-height:1.45}.note{border-left:3px solid #53bdac;padding:13px 17px;background:#123141;color:#a8cbda;border-radius:0 8px 8px 0;line-height:1.55}footer{color:#748da8;font-size:12px;line-height:1.6;margin-top:40px}
.curve-grid{display:grid;grid-template-columns:1fr;gap:14px;margin-top:20px}.curve-panel{background:#0b1a2b;border:1px solid #2a4055;border-radius:12px;padding:18px 18px 8px}.curve-panel h4{font-size:14px;margin:0}.curve-panel svg{display:block;width:100%;height:auto}.curve-caption{font-size:12px;line-height:1.55}
.candidate-detail{border:1px solid #2a4055;border-radius:12px;padding:18px;margin-top:14px}.candidate-detail summary{cursor:pointer;color:#9be9d1;font-weight:600}.candidate-detail[open] summary{margin-bottom:20px}
.candidate-frame{display:block;width:100%;height:360px;border:0;background:#0e1a2a;color-scheme:dark}
@media(max-width:760px){.shell{padding:18px}.grid{grid-template-columns:1fr}.hero{display:block}.status{margin-top:14px}.card,.chart{padding:17px}.details{grid-template-columns:1fr}.details dt{border-bottom:0;padding-bottom:0}.details dd{padding-top:3px}}
@media print{:root,body{background:white;color:#111}.shell{max-width:none;padding:0}.tabs,.filter{display:none}.panel[hidden]{display:block}.card,.chart,.metric{background:white;color:#111;box-shadow:none;border-color:#aaa;break-inside:avoid}.details dt,.muted,.lead{color:#333}svg text{fill:#333}}
"""

_ACCORDION_JS = """
const candidateDetails=[...document.querySelectorAll('details.candidate-detail')];
const candidateFrames=[...document.querySelectorAll('iframe.candidate-frame')];
window.addEventListener('message',event=>{
  if(event.data?.kind!=='mbt-detail-height')return;
  const frame=candidateFrames.find(f=>f.contentWindow===event.source);
  const height=event.data.height;
  if(frame&&typeof height==='number'&&Number.isFinite(height))frame.style.height=Math.max(200,Math.min(50000,height+4))+'px';
});
candidateFrames.forEach(frame=>{
  frame.addEventListener('load',()=>frame.contentWindow.postMessage({kind:'mbt-request-height'},'*'));
  if(frame.hasAttribute('src'))frame.contentWindow.postMessage({kind:'mbt-request-height'},'*');
});
function loadCandidate(detail){
  const frame=detail.querySelector('iframe[data-src]');
  if(frame){frame.src=frame.dataset.src;frame.removeAttribute('data-src');}
}
candidateDetails.forEach(detail=>{
  detail.addEventListener('toggle',()=>{
    if(!detail.open)return;
    candidateDetails.forEach(other=>{if(other!==detail&&other.dataset.group===detail.dataset.group)other.open=false;});
    loadCandidate(detail);
  });
  if(detail.open)loadCandidate(detail);
});
"""


def render_optimization_report(run: dict, results: dict, output_path: str) -> str:
    """Write a standalone, offline report from normalized MT5 evidence."""
    report_path = Path(output_path).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    optimization = results.get('optimization') or {}
    forward = results.get('forward')
    in_sample = results.get('in_sample')
    holdout = results.get('holdout')
    selected = results.get('selected_pass')
    counts = results.get('counts') or {}
    request = run.get('request') or {}
    if not isinstance(request, dict):
        request = {'Request': request}
    if not isinstance(counts, dict):
        counts = {'Counts': counts}
    rows = [row for row in optimization.get('rows', []) if isinstance(row, dict)]
    selected_html = _definition_list(selected) if isinstance(selected, dict) and selected else '<p class="empty">No selected pass supplied.</p>'
    if isinstance(in_sample, dict) and in_sample.get('status') == 'completed':
        selected_detail_html = ('<div class="card"><h3>Selected-pass in-sample backtest</h3>'
                                + _period_metrics(in_sample)
                                + _detail_graph(in_sample, report_path, 'In-sample')
                                + _period_report_link(in_sample, report_path) + '</div>')
    else:
        selected_detail_html = ('<div class="card"><h3>In-sample balance curve</h3>'
                                '<p class="empty">Detailed selected-run backtest unavailable. '
                                'Optimization summary rows contain totals, not a trade-by-trade curve.</p></div>')
    selected_pass_card = f'<div class="card"><h3>Selected optimization pass</h3>{selected_html}</div>'
    if results.get('holdout_candidates'):
        selected_pass_card = ''
        selected_detail_html = _candidate_accordions(results, report_path, 'in_sample')
    setup_html = _experiment_setup(request, holdout)
    parameters_html = _parameter_table(request.get('parameters'), selected, run.get('input_schema'))
    counts_html = _evidence_counts(counts, holdout, results.get('holdout_summary'))
    forward_state = ('Native forward results were saved and parsed.' if forward else 'No verified native MT5 forward rows are available for this run.')
    if holdout and holdout.get('status') == 'completed':
        forward_state = 'Independent MT5 single backtest on unseen dates. This is not the native optimization Forward Results subset.'
        holdout_html = (f'<div class="card"><h3>Independent holdout: selected pass {_text(holdout.get("selected_pass"))}</h3>'
                        f'{_period_metrics(holdout)}{_detail_graph(holdout, report_path, "Out-of-sample")}'
                        f'{_period_report_link(holdout, report_path)}</div>')
    else:
        holdout_html = ''
        if (results.get('holdout_summary') or {}).get('completed', 0):
            forward_state = 'Independent MT5 holdout tests are available for the completed candidates below.'
    if results.get('holdout_candidates'):
        holdout_html = _candidate_accordions(results, report_path, 'holdout')
    holdout_html = _holdout_comparison(results, report_path, request) + holdout_html
    native_forward_html = (f'<div class="card">{_forward_comparison(selected, forward)}</div>'
                           f'<div class="card"><h3>Native MT5 forward result rows</h3>{_result_table(forward, "forward-table")}</div>') if forward else (
                           '' if holdout_html else '<div class="card"><p class="empty">No results supplied.</p></div>')
    document = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark"><title>MBT | Optimization {_text(run.get('run_id'))}</title><style>{_CSS}</style></head><body><div class="shell">
<div class="topline"><span class="brand">MBT / Research</span><span>MT5 native optimization · evidence report</span></div>
<div class="hero"><div><h1>Strategy optimization</h1><p class="lead">Tested parameter combinations, selection rules, and evidence saved by MT5.</p><span class="runid">Run {_text(run.get('run_id'))}</span></div><span class="status">{_text(run.get('status'))}</span></div>
<nav class="tabs" role="tablist" aria-label="Report sections"><button type="button" role="tab" id="tab-overview" aria-controls="overview" aria-selected="true">Overview</button><button type="button" role="tab" id="tab-parameters" aria-controls="parameters" aria-selected="false">Parameter Results</button><button type="button" role="tab" id="tab-selected" aria-controls="selected" aria-selected="false">In-sample candidates</button><button type="button" role="tab" id="tab-forward" aria-controls="forward" aria-selected="false">Out-of-sample check</button><button type="button" role="tab" id="tab-monte-carlo" aria-controls="monte-carlo" aria-selected="false">Monte Carlo</button></nav>
<main><section id="overview" class="panel" role="tabpanel" aria-labelledby="tab-overview"><h2>Overview</h2>{_metric_cards(selected, counts, holdout, results.get('holdout_summary'))}<div class="grid"><div class="card"><h3>Experiment setup</h3>{setup_html}</div><div class="card"><h3>Evidence and counts</h3>{counts_html}</div></div><div class="card"><h3>EA input settings</h3><p class="muted">Swept inputs define the search grid. Selected values are from the highest eligible saved pass.</p>{parameters_html}</div><div class="card"><h3>Tester diagnostics</h3>{_definition_list(run.get('tester_diagnostics') or {})}</div><div class="card"><h3>Source artifacts</h3>{_artifact_links(run.get('artifacts'), report_path)}</div><p class="note">{_text(run.get('warning') or 'Result charts compare saved optimization passes. They are not account-balance or equity curves over time.')}</p></section>
<section id="parameters" class="panel" role="tabpanel" aria-labelledby="tab-parameters" hidden><h2>Parameter Results</h2><p class="muted">Each row is one saved MT5 pass. MBT selects the highest Result among rows meeting the requested minimum Trades; this is an in-sample ranking, not a profitability verdict.</p>{_heatmap(rows, request)}<div class="grid">{_bar_chart(rows, 'Result', 'Optimization result by pass', '#4eddb6')}{_bar_chart(rows, 'Equity DD %', 'Equity drawdown by pass', '#f1aa6d')}</div><div class="card"><h3>All saved passes</h3><label class="filter" for="row-filter">Filter rows<input id="row-filter" type="search" placeholder="Pass, input value, metric…"></label>{_result_table(optimization, 'optimization-table')}</div></section>
<section id="selected" class="panel" role="tabpanel" aria-labelledby="tab-selected" hidden><h2>In-sample candidate reports</h2><p class="note">In-sample means the earlier dates used to compare parameter combinations. Each detailed backtest below shows a candidate's balance path on those same training dates. The in-sample winner is open by default.</p>{selected_pass_card}{selected_detail_html}</section>
<section id="forward" class="panel" role="tabpanel" aria-labelledby="tab-forward" hidden><h2>Out-of-sample check</h2><p class="note">Out-of-sample means later dates excluded from parameter selection. The selected inputs were frozen before this check. {_text(forward_state)}</p>{holdout_html}{native_forward_html}</section>
<section id="monte-carlo" class="panel" role="tabpanel" aria-labelledby="tab-monte-carlo" hidden><h2>Monte Carlo diagnostics</h2><p class="note">Resampled historical trades, not new MT5 backtests. Fixed recorded cash sizes/costs. These diagnostics do not prove resistance to overfitting or future profitability.</p>{render_monte_carlo(run, results, report_path.parent)}</section></main>
<footer>Method: parameter comparisons use MT5 optimization summaries; balance and deal-level drawdown charts use recorded balances from verified MT5 single-run reports. Monte Carlo is displayed only from verified saved analyses. Historical and out-of-sample results do not establish future profitability. Nearby-parameter stability is not inferred.</footer></div>
<script>const tabs=[...document.querySelectorAll('[role="tab"]')];function showTab(tab){{tabs.forEach(t=>{{const active=t===tab;t.setAttribute('aria-selected',String(active));document.getElementById(t.getAttribute('aria-controls')).hidden=!active;}});history.replaceState(null,'','#'+tab.getAttribute('aria-controls'));}}tabs.forEach((tab,i)=>{{tab.addEventListener('click',()=>showTab(tab));tab.addEventListener('keydown',e=>{{if(e.key==='ArrowRight'||e.key==='ArrowLeft'){{e.preventDefault();const next=tabs[(i+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length];showTab(next);next.focus();}}}});}});const requestedTab=tabs.find(t=>t.getAttribute('aria-controls')===location.hash.slice(1));if(requestedTab)showTab(requestedTab);document.getElementById('row-filter').addEventListener('input',function(){{const term=this.value.toLocaleLowerCase();document.querySelectorAll('#optimization-table tbody tr').forEach(row=>{{row.hidden=!row.textContent.toLocaleLowerCase().includes(term);}});}});{_ACCORDION_JS}</script></body></html>'''
    document = document.replace('</body>', HOVER_SCRIPT + '</body>')
    report_path.write_text(document, encoding='utf-8')
    return str(report_path)
