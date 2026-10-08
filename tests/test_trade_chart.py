import hashlib
from pathlib import Path

import pytest

from core import trade_chart


@pytest.fixture
def native(tmp_path):
    path = tmp_path / 'test.htm'
    header = ['Time','Deal','Symbol','Type','Direction','Volume','Price','Order','Commission','Swap','Profit','Balance','Comment']
    rows = [['Expert:', 'EA'], ['Symbol:', 'EURUSD'], ['Period:', 'H1 (2025.01.01 - 2025.02.01)'], header,
            ['2025.01.02 12:00:00','1','EURUSD','buy','in','0.1','1.12345','1','0','0','0','10000','']]
    path.write_text('<table>'+''.join('<tr>'+''.join('<td>'+v+'</td>' for v in row)+'</tr>' for row in rows)+'</table>',encoding='utf-16')
    return path


def test_exact_native_deals(native):
    identity, deals, digest = trade_chart._read_deals(native)
    assert identity['Symbol'] == 'EURUSD'
    assert deals[0][0].strftime('%Y-%m-%d %H:%M:%S') == '2025-01-02 12:00:00'
    assert deals[0][1:3] == (1.12345, True)
    assert digest == hashlib.sha256(native.read_bytes()).hexdigest()


def test_tampered_candidate_rejected(native):
    with pytest.raises(ValueError, match='hash'):
        trade_chart._read_deals(native, 'wrong')


def test_multisymbol_rejected(native):
    text = native.read_text(encoding='utf-16').replace('<td>EURUSD</td><td>buy', '<td>GBPUSD</td><td>buy')
    native.write_text(text,encoding='utf-16')
    with pytest.raises(ValueError, match='Multi-symbol'):
        trade_chart._read_deals(native)


def test_report_scope_and_arguments(native):
    with pytest.raises(ValueError,match='either'):
        trade_chart._saved_report(None,None,None,'in_sample')
    with pytest.raises(ValueError,match='inside MBT'):
        trade_chart._saved_report(str(native),None,None,'in_sample')
    with pytest.raises(ValueError,match='pass_id'):
        trade_chart._saved_report(str(native),None,3,'in_sample')


def test_mql_escaping():
    assert trade_chart._mql_string('a"\\\nb') == '"a\\"\\\\ b"'


def test_display_script_has_no_trading_or_external_apis():
    text = (trade_chart.ROOT/'mql5/MBT_TradeChart.mq5').read_text()
    for name in ('OrderSend(', 'CTrade', 'WebRequest(', '#import', 'HistorySelect('):
        assert name not in text
    assert 'OBJ_ARROW_BUY' in text and 'OBJPROP_TOOLTIP' in text
