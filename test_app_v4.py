import app as web


def test_api_state_has_authoritative_v4_shape():
    c = web.app.test_client()
    r = c.get('/api/state')
    assert r.status_code == 200
    s = r.get_json()
    assert {'core','paper','consensus','ok','errors'} <= set(s)
    assert s['core'].get('official_rule') == 'T2_upup'
    assert s['core'].get('mode') == 'PAPER_ONLY'
    assert s['core'].get('live_order_capability') is False


def test_dashboard_is_v4_not_legacy_intraday_engine():
    c = web.app.test_client()
    body = c.get('/').get_data(as_text=True)
    assert 'QuantBot v4' in body
    assert 'T2_upup' in body
    assert 'PAPER ONLY' in body
    assert 'EMA/RSI' not in body
    assert '/api/toggle' not in body


def test_no_live_trading_route_exists():
    routes = {r.rule for r in web.app.url_map.iter_rules()}
    assert '/api/order' not in routes
    assert '/api/trade' not in routes
    assert '/api/toggle' not in routes
