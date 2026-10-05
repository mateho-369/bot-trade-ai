"""TEST ONLY: no real MT5 import, network or owner/trading authorization."""

from datetime import timedelta
from decimal import Decimal
from threading import Event, RLock, get_ident
from types import SimpleNamespace as NS

from trading.authorization import WriteGrant
from trading.types import ResultStatus

D = Decimal


class FakeSDK:
    __reflexbot_test_sdk__ = True
    TIMEFRAME_M5 = 5
    TIMEFRAME_H1 = 60

    def __init__(self, clock, magic=20260217):
        self.clock, self.magic = clock, magic
        self.connected = False
        self.tradeapi_disabled = False
        self.permission = True
        self.account = NS(
            login=123,
            server="TEST-server",
            currency="USD",
            trade_mode=0,
            balance=1000.0,
            equity=1000.0,
            margin=0.0,
            margin_free=1000.0,
            credit=0.0,
            leverage=100,
            trade_allowed=True,
            trade_expert=True,
        )
        self.metadata = NS(
            name="XAUUSD",
            point=0.01,
            trade_tick_size=0.01,
            trade_tick_value_profit=1.0,
            trade_tick_value_loss=1.0,
            trade_contract_size=100.0,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            digits=2,
            currency_base="XAU",
            currency_profit="USD",
            trade_mode=4,
            trade_stops_level=10,
            trade_freeze_level=0,
            filling_mode=3,
            trade_exemode=2,
            order_mode=127,
            volume_limit=0.0,
            visible=True,
            trade_calc_mode=0,
        )
        self.bid, self.ask = 2610.0, 2610.2
        self.tick_time = None
        self.positions, self.pending, self.deals = (), (), ()
        self.ack = NS(retcode=10009, order=777, deal=888, volume=0.01, price=2610.2)
        self.check = NS(retcode=0)
        self.requests, self.calls = [], []
        self.init_count, self.shutdown_count = 0, 0
        self.send_entered, self.send_release, self.shutdown_done = Event(), Event(), Event()
        self.block_send = False
        self.send_error = None
        self.send_hook = None
        self.check_hook = None
        self.account_hook = None
        self._lock = RLock()

    def called(self, name):
        with self._lock:
            self.calls.append((name, get_ident()))

    def initialize(self, *args, **kwargs):
        self.called("initialize")
        self.init_count += 1
        self.connected = True
        return True

    def login(self, *args, **kwargs):
        self.called("login")
        return True

    def shutdown(self):
        self.called("shutdown")
        self.shutdown_count += 1
        self.connected = False
        self.shutdown_done.set()

    def last_error(self):
        return (-1, "TEST-secret-should-never-be-logged")

    def terminal_info(self):
        self.called("terminal_info")
        return NS(
            connected=self.connected,
            trade_allowed=self.permission,
            tradeapi_disabled=self.tradeapi_disabled,
            path="C:/Program Files/MetaTrader 5",
        )

    def account_info(self):
        self.called("account_info")
        if self.account_hook:
            self.account_hook()
        return self.account

    def symbol_info(self, name):
        self.called("symbol_info")
        return self.metadata if name == self.metadata.name else None

    def symbol_select(self, name, value):
        self.called("symbol_select")
        return name == self.metadata.name

    def symbols_get(self):
        self.called("symbols_get")
        return (self.metadata,)

    def symbol_info_tick(self, name):
        self.called("symbol_info_tick")
        instant = self.tick_time or self.clock.now()
        return NS(
            bid=self.bid,
            ask=self.ask,
            time=int(instant.timestamp()),
            time_msc=int(instant.timestamp() * 1000),
        )

    def positions_get(self):
        self.called("positions_get")
        return self.positions

    def orders_get(self):
        self.called("orders_get")
        return self.pending

    def history_deals_get(self, since, until):
        self.called("history_deals_get")
        return self.deals

    def order_calc_profit(self, kind, symbol, volume, entry, exit_price):
        self.called("order_calc_profit")
        return float((D(str(exit_price)) - D(str(entry))) * D(str(volume)) * 100 * (1 if kind == 0 else -1))

    def order_calc_margin(self, kind, symbol, volume, entry):
        self.called("order_calc_margin")
        return float(D(str(entry)) * D(str(volume)))

    def order_check(self, request):
        self.called("order_check")
        if self.check_hook:
            self.check_hook()
        return self.check

    def order_send(self, request):
        self.called("order_send")
        self.requests.append(dict(request))
        self.send_entered.set()
        if self.block_send and not self.send_release.wait(3):
            raise RuntimeError("test must release the fake worker")
        if self.send_hook:
            self.send_hook()
        if self.send_error:
            raise self.send_error
        return self.ack

    def rates(self, count, cutoff, timeframe):
        # Include an unfinished last bar; adapter must remove it.
        minutes = timeframe
        end = int(cutoff.timestamp()) // (minutes * 60) * minutes * 60
        return [
            dict(
                time=end - (count - 1 - n) * minutes * 60,
                open=2610.0,
                high=2611.0,
                low=2609.0,
                close=2610.5,
                tick_volume=100,
                spread=20,
                real_volume=0,
            )
            for n in range(count)
        ]

    def copy_rates_from_pos(self, symbol, timeframe, start, count):
        self.called("copy_rates_from_pos")
        return self.rates(count, self.clock.now() - timedelta(minutes=start * timeframe), timeframe)

    def copy_rates_from(self, symbol, timeframe, cutoff, count):
        self.called("copy_rates_from")
        return self.rates(count, cutoff, timeframe)

    def owned_position(self, **overrides):
        values = dict(
            ticket=42,
            identifier=50042,
            symbol="XAUUSD",
            type=0,
            volume=0.01,
            price_open=2610.2,
            sl=2608.0,
            tp=2616.0,
            time=int(self.clock.now().timestamp()),
            magic=self.magic,
            profit=0.0,
            swap=0.0,
            comment="test",
        )
        values.update(overrides)
        return NS(**values)


class FixtureAuthority:
    """Only fake clients may use this; deliberately not a production authority."""

    def __init__(self, settings, clock):
        self.settings, self.clock = settings, clock
        self.authorized, self.results, self.uncertain = [], [], []
        self.latest = None
        self.result_event = Event()
        self._lock = RLock()
        self.mutate_grant = None
        self.after_authorize = None
        self.fail_result = False

    def authorize(self, command, snapshot):
        assert snapshot.account.source.value == "test_sdk", "NEVER use this fixture on a real account"
        with self._lock:
            self.authorized.append((command, snapshot))
        permit = WriteGrant(
            snapshot.account.key,
            command.request_hash,
            self.settings.safety_fingerprint(),
            self.clock.now() + timedelta(seconds=20),
            command.order.volume if command.order else D("0"),
            snapshot.worst_loss_account,
            snapshot.required_margin_account,
            entry_gates_verified=True,
            owned_position_verified=True,
        )
        if self.after_authorize:
            self.after_authorize()
        return self.mutate_grant(permit) if self.mutate_grant else permit

    def on_result(self, command, result):
        if self.fail_result:
            raise RuntimeError("TEST-persistence-failure-secret")
        with self._lock:
            self.results.append(result)
            self.latest = result.status
            self.result_event.set()

    def on_uncertain(self, command, reason):
        with self._lock:
            self.uncertain.append(reason)
            # A timeout observer MUST NOT downgrade a later terminal result.
            if self.latest not in {ResultStatus.FILLED, ResultStatus.REJECTED}:
                self.latest = ResultStatus.UNKNOWN
