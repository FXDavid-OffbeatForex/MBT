import hashlib
import json
from pathlib import Path

from core import chart_launcher


def test_button_only_links_owned_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(chart_launcher,'ROOT',tmp_path)
    monkeypatch.setattr(chart_launcher,'CLIENT',tmp_path/'.local/chart_launcher/client.js')
    path=tmp_path/'reports'/'report with space.htm'
    path.parent.mkdir()
    path.write_text('native')
    button=chart_launcher.chart_button(path,path.with_suffix('.html'))
    assert 'Open trades in MT5' in button
    assert hashlib.sha256(b'native').hexdigest() in button
    assert 'client.js' in button
    assert 'X-MBT-Token' not in button and 'http://' not in button
    assert chart_launcher.chart_button(tmp_path/'outside.htm',path)==''


def test_live_bridge_security_and_lifecycle():
    """No chart/MT5 launch: validate loopback health/auth/origin and stop only."""
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    import pytest
    ready=chart_launcher.start_chart_launcher()
    assert ready['status']=='ready'
    session=json.loads(chart_launcher.SESSION.read_text())
    endpoint=f'http://127.0.0.1:{session["port"]}'
    with pytest.raises(HTTPError) as caught:
        urlopen(endpoint+'/health',timeout=2)
    assert caught.value.code==403
    request=Request(endpoint+'/health',headers={'X-MBT-Token':session['token'],'Origin':'https://example.com'})
    with pytest.raises(HTTPError) as caught:
        urlopen(request,timeout=2)
    assert caught.value.code==403
    assert chart_launcher._session_request(session,'health')['status']=='ready'
    # Oversized/unsupported requests must not invoke a chart launch.
    request=Request(endpoint+'/open',data=b'{}',headers={'X-MBT-Token':session['token'],'Origin':'null'})
    with pytest.raises(HTTPError) as caught:
        urlopen(request,timeout=2)
    assert caught.value.code==400
