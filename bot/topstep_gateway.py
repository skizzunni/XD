"""Small ProjectX/TopstepX REST client with order writes disabled by default."""

from datetime import datetime
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


API_URL = "https://api.topstepx.com"
ORDER_STAGES = {"practice", "trading_combine", "express_funded"}


class TopstepAPIError(RuntimeError):
    pass


class AmbiguousOrderError(TopstepAPIError):
    def __init__(self, message, order_id=None):
        super().__init__(message)
        self.order_id = order_id


class TopstepGateway:
    def __init__(self, token=None, execution_enabled=False, transport=None):
        self.token = token
        self.execution_enabled = execution_enabled
        self.transport = transport or self._http

    @staticmethod
    def _http(path, payload, token):
        body = json.dumps(payload, separators=(",", ":")).encode()
        headers = {"Accept": "text/plain", "Content-Type": "application/json",
                   "User-Agent": "mnq-topstep-research/1"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(API_URL + path, body, headers, method="POST")
        try:
            with urlopen(request, timeout=20) as response:
                return json.loads(response.read())
        except HTTPError as exc:
            raise TopstepAPIError(f"Topstep HTTP {exc.code} for {path}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise TopstepAPIError(f"Topstep request failed for {path}: {type(exc).__name__}") from exc

    def _post(self, path, payload, require_token=True):
        if require_token and not self.token:
            raise TopstepAPIError("Authenticate before calling Topstep")
        result = self.transport(path, payload, self.token)
        if not isinstance(result, dict):
            raise TopstepAPIError("Topstep returned a non-object response")
        return result

    @staticmethod
    def _require_success(result, operation):
        if result.get("success") is not True or result.get("errorCode") != 0:
            message = result.get("errorMessage") or "unspecified API error"
            raise TopstepAPIError(f"{operation} failed ({result.get('errorCode')}): {message}")
        return result

    def login(self, username, api_key):
        if not username or not api_key:
            raise TopstepAPIError("Topstep username and API key are required")
        result = self._post("/api/Auth/loginKey",
                            {"userName": username, "apiKey": api_key},
                            require_token=False)
        self._require_success(result, "Authentication")
        token = result.get("token")
        if not isinstance(token, str) or not token:
            raise TopstepAPIError("Authentication succeeded without a session token")
        self.token = token
        return token

    def login_from_environment(self):
        return self.login(os.environ.get("TOPSTEP_USERNAME"),
                          os.environ.get("TOPSTEP_API_KEY"))

    def accounts(self):
        result = self._require_success(
            self._post("/api/Account/search", {"onlyActiveAccounts": True}),
            "Account search")
        return result.get("accounts", [])

    def contracts(self, search_text="MNQ"):
        result = self._require_success(
            self._post("/api/Contract/search", {"live": False, "searchText": search_text}),
            "Contract search")
        return result.get("contracts", [])

    def bars(self, contract_id, start, end, limit=20_000, unit=2, unit_number=1):
        if not 1 <= limit <= 20_000:
            raise ValueError("ProjectX bar requests are limited to 20,000")
        def stamp(value):
            return value.isoformat() if isinstance(value, datetime) else value
        result = self._require_success(self._post("/api/History/retrieveBars", {
            "contractId": contract_id, "live": False, "startTime": stamp(start),
            "endTime": stamp(end), "unit": unit, "unitNumber": unit_number,
            "limit": limit, "includePartialBar": False,
        }), "Bar retrieval")
        return result.get("bars", [])

    def trades(self, account_id, start, end=None):
        payload = {"accountId": account_id,
                   "startTimestamp": start.isoformat() if isinstance(start, datetime) else start,
                   "endTimestamp": end.isoformat() if isinstance(end, datetime) else end}
        result = self._require_success(self._post("/api/Trade/search", payload),
                                       "Trade search")
        return result.get("trades", [])

    def place_bracketed_market_entry(self, *, stage, account_id, contract_id,
                                     side, size, stop_ticks, target_ticks,
                                     custom_tag):
        """Submit once; an ambiguous/rejected response must be reconciled, never retried."""
        if not self.execution_enabled:
            raise TopstepAPIError("Order writes are disabled")
        if stage not in ORDER_STAGES:
            raise TopstepAPIError("Orders are allowed only on Practice, Combine or XFA")
        if side not in {0, 1} or type(size) is not int or size <= 0:
            raise ValueError("Invalid order side or size")
        if type(stop_ticks) is not int or stop_ticks <= 0:
            raise ValueError("A positive stop bracket is mandatory")
        if type(target_ticks) is not int or target_ticks < 2 * stop_ticks:
            raise ValueError("Target must be at least twice the stop distance")
        if not custom_tag or len(custom_tag) > 64:
            raise ValueError("A short unique custom tag is required")
        result = self._post("/api/Order/place", {
            "accountId": account_id, "contractId": contract_id, "type": 2,
            "side": side, "size": size, "limitPrice": None, "stopPrice": None,
            "trailPrice": None, "customTag": custom_tag,
            "stopLossBracket": {"ticks": stop_ticks, "type": 4},
            "takeProfitBracket": {"ticks": target_ticks, "type": 1},
        })
        if result.get("success") is not True or result.get("errorCode") != 0:
            raise AmbiguousOrderError(
                f"Entry was not confirmed ({result.get('errorCode')}): "
                f"{result.get('errorMessage') or 'unspecified API error'}; reconcile orders/positions",
                result.get("orderId"))
        if not isinstance(result.get("orderId"), int):
            raise AmbiguousOrderError("Entry response omitted orderId; reconcile orders/positions")
        return result["orderId"]
