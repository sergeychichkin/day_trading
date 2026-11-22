from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic, sleep

from ib_async import IB, Stock, util
from ib_async.wrapper import RequestError

from .sessions import Session
from .store import Attempt


@dataclass(frozen=True)
class ContractIdentity:
    con_id: int
    primary_exchange: str


def historical_parameters(session: Session, duration: str = "1 D") -> tuple:
    end = datetime.fromtimestamp(session.close_utc, timezone.utc).strftime("%Y%m%d-%H:%M:%S")
    return end, duration, "1 min", "TRADES", 1, 2, False, []


def classify_error(code: int, message: str) -> str:
    detail = message.lower()
    if code in {502, 504, 507, 1100, 1300}:
        return "disconnected"
    if code in {354, 10167, 10186, 10089} or "permission" in detail or "subscription" in detail:
        return "entitlement"
    if "pacing" in detail or code in {420, 10182}:
        return "pacing"
    if code == 162 and "no data" in detail:
        return "empty"
    return "broker_error"


class TwsHistory:
    def __init__(
        self, port: int, client_id: int, timeout_seconds: float, *,
        volume_unit: str | None = None, min_interval: float = 2.0,
    ):
        if client_id <= 0:
            raise ValueError("Use a positive TWS client ID.")
        self.port = port
        self.client_id = client_id
        self.timeout_seconds = timeout_seconds
        self.volume_unit = volume_unit
        self.min_interval = min_interval
        self._next_request_at = 0.0
        self._ib = IB()
        self._ib.RaiseRequestErrors = True

    def __enter__(self):
        # The socket handshake avoids account, order, and execution startup requests.
        try:
            self._ib.client.connect("127.0.0.1", self.port, self.client_id, timeout=self.timeout_seconds)
        except TimeoutError as exc:
            raise ConnectionError(f"TWS API handshake timed out on port {self.port}.") from exc
        return self

    def __exit__(self, *_):
        self._ib.disconnect()

    @staticmethod
    def _contract(identity: ContractIdentity | None = None):
        stock = Stock("QQQ", "SMART", "USD")
        stock.primaryExchange = identity.primary_exchange if identity else "NASDAQ"
        if identity:
            stock.conId = identity.con_id
        return stock

    def qualify_qqq(self) -> ContractIdentity:
        try:
            details = util.run(
                self._ib.reqContractDetailsAsync(self._contract()), timeout=self.timeout_seconds
            )
        except TimeoutError as exc:
            raise ConnectionError("Timed out qualifying QQQ through TWS.") from exc
        except RequestError as exc:
            raise ConnectionError(str(exc)) from exc
        matches = {
            (contract.conId, contract.primaryExchange)
            for detail in details if (contract := detail.contract).symbol == "QQQ"
            and contract.secType == "STK" and contract.currency == "USD"
            and contract.conId and contract.primaryExchange in {"NASDAQ", "ISLAND"}
        }
        if len(matches) != 1:
            raise ValueError(f"Expected one Nasdaq-listed QQQ contract, TWS returned {len(matches)} matches.")
        return ContractIdentity(*matches.pop())

    def request_session(self, contract: ContractIdentity, session: Session) -> Attempt:
        return self.request_bars(contract, session)

    def request_bars(self, contract: ContractIdentity, session: Session, duration: str = "1 D") -> Attempt:
        params = historical_parameters(session, duration)
        timeout = self.timeout_seconds if duration == "1 D" else max(75, self.timeout_seconds)
        request = {
            "source": "IBKR TWS", "symbol": "QQQ", "con_id": contract.con_id,
            "exchange": "SMART", "primary_exchange": contract.primary_exchange, "currency": "USD",
            "port": self.port, "server_version": self._ib.client.serverVersion(),
            "end_utc": params[0], "duration": duration, "bar_size": "1 min",
            "type": "TRADES", "rth": True, "volume_unit": self.volume_unit,
        }
        if not self._ib.client.isReady():
            return Attempt("disconnected", diagnostic="TWS API is disconnected.", request=request)
        delay = self._next_request_at - monotonic()
        if delay > 0:
            self._ib.sleep(delay)
        self._next_request_at = monotonic() + self.min_interval
        stock = self._contract(contract)
        client, wrapper = self._ib.client, self._ib.wrapper
        request_id = client.getReqId()
        request["request_id"] = request_id
        bars = []
        future = wrapper.startReq(request_id, stock, container=bars)
        status, diagnostic = "complete", ""
        try:
            client.reqHistoricalData(request_id, stock, *params)
            # Waiting on the callback ourselves keeps partial rows available on timeout.
            util.run(future, timeout=timeout)
        except TimeoutError:
            diagnostic = f"No completed response in {timeout:g} seconds."
            status = "timeout"
            if client.isReady():
                client.cancelHistoricalData(request_id)
        except RequestError as exc:
            status, diagnostic = classify_error(exc.code, exc.message), str(exc)
        except OSError as exc:
            status, diagnostic = "disconnected", str(exc)
        finally:
            wrapper._endReq(request_id)

        raw = [
            tuple(map(str, (int(bar.date.timestamp()), bar.open, bar.high, bar.low, bar.close, bar.volume)))
            for bar in bars
        ]
        request["provider_bar_count"] = sum(max(0, bar.barCount) for bar in bars)
        if status == "complete" and not raw:
            status = "empty"
        return Attempt(status, raw, diagnostic, request)
