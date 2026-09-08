import math

import quantbot_v4_core as core


def test_valid_long_cash_target():
    v=core.validate_target({'BTCUSDT':.2,'ETHUSDT':.15},{'BTCUSDT','ETHUSDT'})
    assert v.ok
    assert math.isclose(v.gross,.35)


def test_rejects_short_unknown_and_overgross():
    v=core.validate_target({'BTCUSDT':1.1,'BADUSDT':-.1},{'BTCUSDT'})
    assert not v.ok
    text=' '.join(v.errors)
    assert 'unknown assets' in text
    assert 'long/cash' in text
    assert 'exceeds' in text


def test_rebalance_plan_from_cash():
    p=core.rebalance_plan({}, {'LINKUSDT':.05,'UNIUSDT':.04})
    assert [x['symbol'] for x in p]==['LINKUSDT','UNIUSDT']
    assert all(x['delta_weight']>0 for x in p)


def test_rebalance_plan_closes_removed_asset():
    p=core.rebalance_plan({'LINKUSDT':.05},{})
    assert p==[{'symbol':'LINKUSDT','current_weight':.05,'target_weight':0.0,'delta_weight':-.05}]


def test_core_has_no_live_order_api():
    forbidden=('place_order','send_order','api_secret','api_key','binance_client','bybit_client')
    attrs={x.lower() for x in dir(core)}
    for x in forbidden:
        assert x not in attrs
