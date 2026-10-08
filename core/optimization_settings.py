"""Validated, explicit MT5 tester account/execution configuration."""
from decimal import Decimal, InvalidOperation
import re

from .commission_profile import commission_settings, native_commission_profile


def testing_settings(settings=None):
    if settings is None:
        settings = {}
    if not isinstance(settings, dict) or set(settings) - {
        'deposit', 'currency', 'leverage', 'execution_delay_ms',
        'commission', 'commission_template_sha256'
    }:
        raise ValueError('testing accepts deposit, currency, leverage, execution_delay_ms and native commission settings only')
    deposit = settings.get('deposit', 10000)
    try:
        amount = Decimal(str(deposit))
    except InvalidOperation as exc:
        raise ValueError('deposit must be finite and positive') from exc
    if isinstance(deposit, bool) or not amount.is_finite() or not 0 < amount <= 1000000000:
        raise ValueError('deposit must be finite and positive, at most 1000000000')
    if float(amount) <= 0:
        raise ValueError('deposit underflows representable precision')
    currency = settings.get('currency', 'USD')
    if not isinstance(currency, str) or not re.fullmatch(r'[A-Z]{3}', currency):
        raise ValueError('currency must be a three-letter uppercase code')
    leverage = settings.get('leverage', 100)
    if type(leverage) is not int or not 1 <= leverage <= 10000:
        raise ValueError('leverage must be an integer 1..10000 (100 means 1:100)')
    delay = settings.get('execution_delay_ms', 0)
    if type(delay) is not int or not -1 <= delay <= 600000:
        raise ValueError('execution_delay_ms must be -1 (random), 0, or 1..600000')
    result = dict(deposit=float(amount), currency=currency, leverage=leverage, execution_delay_ms=delay)
    commission = commission_settings(settings.get('commission'))
    frozen_hash = settings.get('commission_template_sha256')
    if commission is not None:
        _, template_hash = native_commission_profile(commission)
        if frozen_hash is not None and frozen_hash != template_hash:
            raise ValueError('Native commission template differs from the saved testing settings')
        result['commission'] = commission
        result['commission_template_sha256'] = template_hash
    elif frozen_hash is not None:
        raise ValueError('commission_template_sha256 requires a native commission configuration')
    return result


def testing_ini(settings=None):
    config = testing_settings(settings)
    lines = [f"Deposit={config['deposit']}", f"Currency={config['currency']}",
             f"Leverage=1:{config['leverage']}", f"ExecutionMode={config['execution_delay_ms']}"]
    if 'commission' in config:
        # Native commission fields belong in Groups/*.txt, never [Tester].
        # Explicitly keep the faster pips-only path from suppressing commissions.
        lines.append('ProfitInPips=0')
    return lines
