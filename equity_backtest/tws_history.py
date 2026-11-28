"""Historical-only TWS adapter for audited native five-minute responses."""

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
from time import monotonic

from ib_async import Stock
from ib_async.wrapper import RequestError

from qqq_viewer.tws import TwsHistory, classify_error


class EquityHistory(TwsHistory):
    def __init__(self, *args, wait_for_slot=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.wait_for_slot = wait_for_slot

    @staticmethod
    def wait(seconds):
        asyncio.get_event_loop().run_until_complete(asyncio.sleep(seconds))

    @staticmethod
    def _await(awaitable, timeout):
        return asyncio.get_event_loop().run_until_complete(asyncio.wait_for(awaitable, timeout))

    def __enter__(self):
        self._await(self._ib.client.connectAsync("127.0.0.1", self.port, self.client_id, self.timeout_seconds),
                    self.timeout_seconds * 2)
        return self

    def qualify(self, ticker):
        details = self._await(self._ib.reqContractDetailsAsync(Stock(ticker, "SMART", "USD")), self.timeout_seconds)
        matches = {(c.conId, c.primaryExchange) for row in details if (c := row.contract).symbol == ticker
                   and c.secType == "STK" and c.currency == "USD" and c.conId > 0 and c.primaryExchange}
        if len(matches) != 1:
            raise ValueError(f"Expected one USD TWS contract for {ticker}, found {sorted(matches)}.")
        con_id, exchange = matches.pop()
        return {"ticker": ticker, "con_id": con_id, "primary_exchange": exchange, "routing": "SMART", "currency": "USD"}

    def fetch(self, identity, window, duration):
        begin, last = window[0], window[-1]
        end = datetime.fromtimestamp(last.close_utc, timezone.utc).strftime("%Y%m%d-%H:%M:%S")
        response = {"start": str(begin.date), "end": str(last.date), "request_end_utc": end, "duration": duration,
                    "contract": identity, "rows": [],
                    "params": ["5 mins", "TRADES", 1, 2, False]}
        api, wrapper = self._ib.client, self._ib.wrapper
        if not api.isReady():
            return {**response, "status": "disconnected", "detail": "TWS API is disconnected."}
        delay = self._next_request_at - monotonic()
        if delay > 0:
            self.wait(delay)
        if self.wait_for_slot is not None:
            self.wait_for_slot()
        self._next_request_at = monotonic() + self.min_interval
        contract = Stock(identity["ticker"], "SMART", "USD", conId=identity["con_id"],
                         primaryExchange=identity["primary_exchange"])
        request_id = api.getReqId()
        bars = []
        future = wrapper.startReq(request_id, contract, container=bars)
        warnings = []

        def notice(event_id, code, detail, _contract):
            if event_id == request_id:
                warnings.append({"code": code, "detail": detail})

        self._ib.errorEvent += notice
        try:
            response["requested_at"] = datetime.now(timezone.utc).isoformat()
            api.reqHistoricalData(request_id, contract, end, duration, "5 mins", "TRADES", 1, 2, False, [])
            timeout = {"1 D": self.timeout_seconds, "1 M": 120, "12 M": 240}.get(duration, 600)
            self._await(future, timeout)
            response["status"] = "complete" if bars else "empty"
        except TimeoutError:
            response.update(status="timeout", detail="TWS historical request timed out.")
            if api.isReady():
                with suppress(OSError):
                    api.cancelHistoricalData(request_id)
        except RequestError as error:
            response.update(status=classify_error(error.code, error.message), code=error.code, detail=error.message)
        except (OSError, ConnectionError) as error:
            response.update(status="disconnected", detail=str(error))
        finally:
            response["received_at"] = datetime.now(timezone.utc).isoformat()
            self._ib.errorEvent -= notice
            wrapper._endReq(request_id)
            wrapper._results.pop(request_id, None)
        if warnings:
            response["warnings"] = warnings
            for warning in reversed(warnings):
                failure = classify_error(warning["code"], warning["detail"])
                if response["status"] in {"timeout", "empty"} and failure in {"entitlement", "disconnected", "pacing"}:
                    response.update(status=failure, **warning)
                    break
        for bar in bars:
            stamp = bar.date
            epoch = int(stamp.timestamp()) if isinstance(stamp, datetime) and stamp.utcoffset() is not None else None
            response["rows"].append([epoch, str(stamp), *(str(getattr(bar, name)) for name in
                                    ("open", "high", "low", "close", "volume", "average"))])
        return response
