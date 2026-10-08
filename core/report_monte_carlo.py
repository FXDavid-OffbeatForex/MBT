"""Dark offline SVG charts for verified Monte Carlo artifacts."""
from html import escape
from pathlib import Path

from .monte_carlo import load_analysis
from .report_hover import point_attributes


def _text(value):
    return escape(str(value), quote=True)


def _cash(value):
    return f'{value:,.2f}'


def _frame(title, body, low, high, x_label, hover=''):
    labels = ''.join(f'<text x="58" y="{54+i*49:.1f}" text-anchor="end" fill="#a9b8cc" font-size="11">'
                     f'{high-(high-low)*i/4:,.2f}</text>' for i in range(5))
    return (f'<div class="card"><h3>{_text(title)}</h3><svg viewBox="0 0 760 285" '
            f'role="img"{hover} aria-label="{_text(title)}" style="display:block;width:100%;height:auto">'
            '<rect x="70" y="50" width="660" height="196" fill="#0b1523"/>'
            f'{labels}{body}<text x="400" y="276" text-anchor="middle" fill="#a9b8cc" '
            f'font-size="12">{_text(x_label)}</text></svg></div>')


def _bands(analysis):
    points = analysis['balance_bands']
    low, high = min(p['p05'] for p in points), max(p['p95'] for p in points)
    if low == high:
        low, high = low - 1, high + 1
    padding = (high - low) * .05
    low, high = low - padding, high + padding
    def coords(field):
        return [(70 + 660 * p['closed_trades'] / analysis['trade_count'],
                 246 - 196 * (p[field] - low) / (high - low)) for p in points]
    def path(values):
        return ' '.join(f'{x:.2f},{y:.2f}' for x, y in values)
    body = (f'<polygon points="{path(coords("p95") + list(reversed(coords("p05"))))}" '
            'fill="#53dfb8" fill-opacity=".17"/>'
            f'<polyline points="{path(coords("p50"))}" fill="none" stroke="#53dfb8" stroke-width="2"/>'
            '<text x="78" y="35" fill="#a9b8cc" font-size="12">Shaded P05-P95; line P50 (pointwise)</text>')
    hover = point_attributes([[x, y, f'Completed synthetic trades: {p["closed_trades"]}\n'
              f'P05: {p["p05"]:,.2f}\nP50: {p["p50"]:,.2f}\nP95: {p["p95"]:,.2f}\n'
              f'{analysis["provenance"]["currency"]} / synthetic order, not calendar time']
              for (x, y), p in zip(coords('p50'), points)])
    return _frame(f'Synthetic balance bands ({analysis["provenance"]["currency"]})', body, low, high,
                  'Completed trades (synthetic order; not calendar time)', hover)


def _histogram(analysis, field, title, color):
    histogram = analysis[field]
    counts, edges = histogram['counts'], histogram['edges']
    maximum = max(counts) or 1
    width = 660 / len(counts)
    body = ''.join(f'<rect x="{70+i*width:.2f}" y="{246-196*v/maximum:.2f}" '
                   f'width="{max(width-2,1):.2f}" height="{196*v/maximum:.2f}" fill="{color}">'
                   f'<title>{edges[i]:.2f} to {edges[i+1]:.2f}: {v} paths</title></rect>'
                   for i, v in enumerate(counts))
    body += (f'<text x="70" y="260" fill="#a9b8cc" font-size="11">{edges[0]:,.2f}</text>'
             f'<text x="730" y="260" text-anchor="end" fill="#a9b8cc" font-size="11">{edges[-1]:,.2f}</text>')
    return _frame(title, body, 0, maximum, f'{analysis["provenance"]["currency"]}; vertical axis: simulated path count')


def render_monte_carlo(run: dict, results: dict, folder: Path) -> str:
    references = results.get('monte_carlo') or []
    if not references:
        return ('<div class="card"><p class="empty">No Monte Carlo analysis has been run. '
                'Ask MBT to resample a verified candidate report; summary rows alone are insufficient.</p></div>')
    cards = []
    for reference in references:
        try:
            analysis = load_analysis(reference, run, results, folder)
            provenance = analysis['provenance']
            rows = [('Closed trades', analysis['trade_count']), ('Simulated paths', analysis['simulations']),
                    ('Seed', analysis['seed']), ('Observed net profit', _cash(analysis['observed']['net_profit'])),
                    ('Recorded deal-level max balance DD', _cash(analysis['observed']['max_balance_dd'])),
                    ('Terminal net profit P05 / P50 / P95', ' / '.join(_cash(analysis['terminal_net_profit'][k]) for k in ('p05','p50','p95'))),
                    ('Max balance DD P05 / P50 / P95', ' / '.join(_cash(analysis['max_balance_dd'][k]) for k in ('p05','p50','p95'))),
                    ('Max balance DD % P05 / P50 / P95', ' / '.join(_cash(analysis['max_balance_dd_pct'][k]) for k in ('p05','p50','p95'))),
                    ('Synthetic paths ending at a loss (%)', round(analysis['sampled_loss_fraction']*100, 2)),
                    ('Synthetic paths crossing zero (%)', round(analysis['synthetic_zero_crossing_fraction']*100, 2))]
            metrics = '<dl class="details">' + ''.join(f'<dt>{_text(k)}</dt><dd>{_text(v)}</dd>' for k,v in rows) + '</dl>'
            notes = '<ul>' + ''.join(f'<li>{_text(w)}</li>' for w in analysis['warnings']) + '</ul>'
            label = 'Out of sample' if provenance['period'] == 'holdout' else 'In sample (selection bias remains)'
            heading = f'Pass {provenance["pass_id"]} / {label} / {analysis["method"]}'
            cards.append(f'<details class="candidate-detail" name="mbt-monte-carlo" data-group="mbt-monte-carlo"'
                         f'{" open" if not cards else ""}><summary>{_text(heading)}</summary>'
                         f'<div class="card"><h3>Pass {_text(provenance["pass_id"])} / '
                         f'{_text(label)} / {_text(analysis["method"])}</h3>'
                         f'<p class="muted">Source dates: {_text(provenance["from_date"])} to {_text(provenance["to_date"])}. '
                         'Recorded sizes and costs; fully closed trade blocks.</p>'
                         f'{metrics}<p><a href="{_text(Path(reference["path"]).name)}">Verified analysis JSON</a></p></div>'
                         f'{_bands(analysis)}<div class="grid">'
                         f'{_histogram(analysis,"drawdown_histogram","Sampled maximum balance drawdown","#f1aa6d")}'
                         f'{_histogram(analysis,"profit_histogram","Sampled terminal net profit","#7ca8ff")}</div>'
                         f'<div class="card"><h3>Interpretation and limitations</h3>{notes}</div></details>')
        except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            cards.append(f'<div class="card"><p class="empty">Monte Carlo analysis unavailable: {_text(exc)}</p></div>')
    return ('<style>#monte-carlo .details{column-gap:24px;grid-template-columns:minmax(220px,320px) 1fr}'
            '@media(max-width:700px){#monte-carlo .details{grid-template-columns:1fr;column-gap:0}'
            '#monte-carlo .details dt{border-bottom:0;padding-bottom:0}}</style>' + ''.join(cards))
