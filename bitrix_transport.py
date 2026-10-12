"""Narrow, credential-redacting Bitrix24 Cloud read transport.

Original implementation, not a provider SDK or an MCP server. Method shapes are
documented at https://apidocs.bitrix24.com/api-reference/crm/universal/crm-item-list.html
and https://apidocs.bitrix24.com/api-reference/crm/timeline/activities/activity-base/crm-activity-list.html.
Only the two declared list methods can be requested. This does not reduce the
permissions of the webhook itself: configure a least-privileged portal user.
"""

import json
import math
import os
import re
import ssl
import time
from urllib.error import HTTPError
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REQUEST_BYTES = 256 * 1024
MAX_JSON_DEPTH = 40
MAX_PAGES = 100
MAX_PAGE_RECORDS = 50
MAX_DECLARED_TOTAL = (1 << 63) - 1
ALLOWED_METHODS = frozenset({"crm.item.list", "crm.activity.list"})
_PARAMETERS = {
    "crm.item.list": frozenset(
        {"entityTypeId", "select", "filter", "order", "start", "useOriginalUfNames"}
    ),
    "crm.activity.list": frozenset({"select", "filter", "order", "start"}),
}
_WEBHOOK = re.compile(
    r"https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"\.bitrix24\.(?:ru|com)/rest/[1-9][0-9]*/[A-Za-z0-9_-]{1,256}/\Z"
)
_POSITIVE_ID = re.compile(r"[1-9][0-9]*\Z")
_MESSAGES = {
    "CONFIG_MISSING": "Set the webhook environment variable locally.",
    "CONFIG_INVALID": "The local transport configuration is not supported.",
    "METHOD_BLOCKED": "Only the declared CRM list methods are permitted.",
    "PARAMS_INVALID": "Request parameters must match the supported JSON contract.",
    "REQUEST_TOO_LARGE": "The request exceeds the local size limit.",
    "NETWORK_ERROR": "The read request did not complete; inspect connectivity locally.",
    "HTTP_ERROR": "The provider returned an unsuccessful HTTP status.",
    "AUTH_ERROR": "The provider rejected authentication; inspect local credentials.",
    "ACCESS_DENIED": "The provider denied access; inspect the portal user's permissions.",
    "RATE_LIMITED": "The provider rate-limited the read; no automatic retry was made.",
    "PORTAL_BLOCKED": "The provider reports that portal API access is blocked.",
    "REDIRECT_BLOCKED": "Redirects are not permitted for credential-bearing requests.",
    "RESPONSE_TOO_LARGE": "The response exceeds the local size limit.",
    "RESPONSE_INVALID": "The provider response does not match the supported JSON contract.",
    "API_ERROR": "The provider returned an API error; no raw error text is exposed.",
    "PAGINATION_INVALID": "The page cursor or record identifier is invalid.",
    "PAGINATION_CONFLICT": "Repeated record identifiers have conflicting selected data.",
    "TRANSPORT_ERROR": "The transport could not complete the read safely.",
}
_API_ERROR_CODES = {
    "NO_AUTH_FOUND": "AUTH_ERROR",
    "INVALID_CREDENTIALS": "AUTH_ERROR",
    "invalid_token": "AUTH_ERROR",
    "expired_token": "AUTH_ERROR",
    "user_access_error": "AUTH_ERROR",
    "ACCESS_DENIED": "ACCESS_DENIED",
    "insufficient_scope": "ACCESS_DENIED",
    "QUERY_LIMIT_EXCEEDED": "RATE_LIMITED",
    "OPERATION_TIME_LIMIT": "RATE_LIMITED",
    "OVERLOAD_LIMIT": "RATE_LIMITED",
    "PORTAL_BLOCKED_BY_LICENSE_SCANNER": "PORTAL_BLOCKED",
}


class TransportError(Exception):
    """Stable safe error: neither arbitrary code nor arbitrary message is echoed."""

    def __init__(self, code, message=None):
        # The second argument is accepted for callers, but never interpolated.
        # Error strings can themselves contain a full secret-bearing webhook.
        self.code = code if isinstance(code, str) and code in _MESSAGES else "TRANSPORT_ERROR"
        self.message = _MESSAGES[self.code]
        super().__init__(self.code, self.message)

    def __str__(self):
        return "{}: {}".format(self.code, self.message)

    def __repr__(self):
        return "TransportError(code={!r}, message={!r})".format(self.code, self.message)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # urllib raises HTTPError rather than opening a second URL.
        return None


def _validate_json(value, depth=0):
    if depth > MAX_JSON_DEPTH:
        raise ValueError("depth")
    if value is None or type(value) in (bool, str, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("number")
        return
    if type(value) is list:
        for item in value:
            _validate_json(item, depth + 1)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("key")
            _validate_json(item, depth + 1)
        return
    raise ValueError("type")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("constant")


def _bounded_integer(value):
    # Python 3.9 does not have a built-in decimal literal length guard. Bound
    # before int conversion so an otherwise size-bounded body cannot force a
    # costly conversion of a multi-megabyte numeric literal.
    digits = value[1:] if value.startswith("-") else value
    if len(digits) > 19:
        raise ValueError("integer")
    return int(value)


def _decode_response(raw):
    try:
        if type(raw) is not bytes:
            raise ValueError("bytes")
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_int=_bounded_integer,
        )
        if type(parsed) is not dict:
            raise ValueError("object")
        _validate_json(parsed)
        return parsed
    except (ValueError, TypeError, RecursionError, UnicodeError, OverflowError):
        raise TransportError("RESPONSE_INVALID") from None


def _http_error_code(status):
    if status == 401:
        return "AUTH_ERROR"
    if status == 403:
        return "ACCESS_DENIED"
    if status == 429:
        return "RATE_LIMITED"
    if type(status) is int and 300 <= status <= 399:
        return "REDIRECT_BLOCKED"
    return "HTTP_ERROR"


def _record_id(record, method):
    field = "id" if method == "crm.item.list" else "ID"
    if type(record) is not dict or field not in record:
        raise TransportError("PAGINATION_INVALID")
    value = record[field]
    if type(value) is int and value > 0:
        return str(value)
    if type(value) is str and _POSITIVE_ID.fullmatch(value):
        # The regex already requires a canonical positive decimal identifier.
        # Do not parse unbounded provider strings as potentially huge integers.
        return value
    raise TransportError("PAGINATION_INVALID")


class WebhookClient:
    """Two-method synchronous client; no retries, ambient proxies, or redirects.

    Production entry point is from_env(). The injectable opener, clock and
    sleeper make offline transport tests possible; they are not CLI options.
    """

    def __init__(
        self,
        webhook_url,
        *,
        opener=None,
        timeout=20.0,
        min_interval=0.6,
        sleeper=time.sleep,
        clock=time.monotonic,
    ):
        if (
            type(webhook_url) is not str
            or len(webhook_url) > 2048
            or not _WEBHOOK.fullmatch(webhook_url)
        ):
            raise TransportError("CONFIG_INVALID")
        for value, lower, upper, inclusive in (
            (timeout, 0, 120, False),
            (min_interval, 0, 60, True),
        ):
            if (
                type(value) not in (int, float)
                or value > upper
                or (value < lower if inclusive else value <= lower)
                or not math.isfinite(value)
            ):
                raise TransportError("CONFIG_INVALID")
        if not callable(sleeper) or not callable(clock):
            raise TransportError("CONFIG_INVALID")
        self._base_url = webhook_url
        self._timeout = float(timeout)
        self._min_interval = float(min_interval)
        self._sleeper = sleeper
        self._clock = clock
        self._last_start = None
        if opener is None:
            opener = build_opener(
                ProxyHandler({}),
                HTTPSHandler(context=ssl.create_default_context()),
                _NoRedirect(),
            )
        if not callable(getattr(opener, "open", None)):
            raise TransportError("CONFIG_INVALID")
        self._opener = opener

    def __repr__(self):
        return "WebhookClient(methods=2, credential_source='local', retries=0)"

    @classmethod
    def from_env(cls, **kwargs):
        endpoint = os.environ.get("B24_WEBHOOK_URL")
        if not endpoint:
            raise TransportError("CONFIG_MISSING")
        return cls(endpoint, **kwargs)

    def _pace(self):
        if self._min_interval == 0:
            return
        try:
            now = self._clock()
            if type(now) not in (int, float) or not math.isfinite(now):
                raise ValueError("clock")
            if self._last_start is not None:
                delta = now - self._last_start
                if delta < 0:
                    raise ValueError("clock")
                delay = self._min_interval - delta
                if delay > 0:
                    self._sleeper(delay)
                    now = self._clock()
                    if type(now) not in (int, float) or not math.isfinite(now):
                        raise ValueError("clock")
                    if now < self._last_start + self._min_interval:
                        raise ValueError("clock")
            self._last_start = now
        except Exception:
            raise TransportError("CONFIG_INVALID") from None

    def call(self, method, params):
        """Return the validated response envelope of one permitted read request."""
        if type(method) is not str or method not in ALLOWED_METHODS:
            raise TransportError("METHOD_BLOCKED")
        try:
            if type(params) is not dict or not set(params).issubset(_PARAMETERS[method]):
                raise ValueError("params")
            _validate_json(params)
            if "start" in params and (type(params["start"]) is not int or params["start"] < 0):
                raise ValueError("start")
            body = json.dumps(params, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        except (ValueError, TypeError, RecursionError, UnicodeError, OverflowError):
            raise TransportError("PARAMS_INVALID") from None
        if len(body) > MAX_REQUEST_BYTES:
            raise TransportError("REQUEST_TOO_LARGE")
        request = Request(
            self._base_url + method,
            data=body,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "User-Agent": "Lookatshow-Bitrix-Read-Kit/0.1",
            },
            method="POST",
        )
        self._pace()
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                status = response.getcode()
                if type(status) is not int or not 200 <= status <= 299:
                    raise TransportError(_http_error_code(status))
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if type(raw) is not bytes:
                    raise TransportError("RESPONSE_INVALID")
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise TransportError("RESPONSE_TOO_LARGE")
        except TransportError:
            raise
        except HTTPError as error:
            safe_code = _http_error_code(error.code)
            try:
                error.close()
            except Exception:
                pass
            raise TransportError(safe_code) from None
        except Exception:
            # Never format the original exception: urllib errors may include
            # the full URL, Location header, credentials, or provider text.
            raise TransportError("NETWORK_ERROR") from None
        envelope = _decode_response(raw)
        if "error" in envelope or "error_description" in envelope:
            provider_code = envelope.get("error")
            safe_code = _API_ERROR_CODES.get(provider_code, "API_ERROR") if type(provider_code) is str else "API_ERROR"
            raise TransportError(safe_code)
        result = envelope.get("result")
        if method == "crm.item.list":
            if type(result) is not dict or type(result.get("items")) is not list:
                raise TransportError("RESPONSE_INVALID")
        elif type(result) is not list:
            raise TransportError("RESPONSE_INVALID")
        return envelope

    def pages(self, method, params, max_pages):
        """Return bounded unique records and explicit pagination completeness.

        complete means the chosen request stream reached an absent next cursor;
        it does not establish coverage outside the user's read permissions.
        Identical repeated selected records are deduplicated and counted.
        Conflicting repeats fail instead of silently choosing a version.
        """
        if type(method) is not str or method not in ALLOWED_METHODS:
            raise TransportError("METHOD_BLOCKED")
        if type(max_pages) is not int or not 1 <= max_pages <= MAX_PAGES:
            raise TransportError("PARAMS_INVALID")
        if type(params) is not dict:
            raise TransportError("PARAMS_INVALID")
        cursor = params.get("start", 0)
        if type(cursor) is not int or cursor < 0:
            raise TransportError("PARAMS_INVALID")
        parameters = dict(params)
        initial_start = cursor
        records = []
        selected_by_id = {}
        seen_cursors = set()
        duplicate_records = 0
        raw_records = 0
        declared_total = None
        for page_count in range(1, max_pages + 1):
            if cursor in seen_cursors:
                raise TransportError("PAGINATION_INVALID")
            seen_cursors.add(cursor)
            parameters["start"] = cursor
            envelope = self.call(method, parameters)
            result = envelope["result"]
            page_records = result["items"] if method == "crm.item.list" else result
            if len(page_records) > MAX_PAGE_RECORDS:
                raise TransportError("PAGINATION_INVALID")
            if "total" in envelope:
                total = envelope["total"]
                if type(total) is not int or not 0 <= total <= MAX_DECLARED_TOTAL:
                    raise TransportError("PAGINATION_INVALID")
                if declared_total is not None and total != declared_total:
                    raise TransportError("PAGINATION_CONFLICT")
                declared_total = total
            # A repeated binding is still a raw row in the provider count.
            # Do not compare total only against the deduplicated records.
            raw_records += len(page_records)
            for record in page_records:
                identifier = _record_id(record, method)
                canonical = json.dumps(record, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
                if identifier in selected_by_id:
                    if canonical != selected_by_id[identifier]:
                        raise TransportError("PAGINATION_CONFLICT")
                    duplicate_records += 1
                else:
                    selected_by_id[identifier] = canonical
                    records.append(record)
            if declared_total is not None and len(records) > declared_total:
                raise TransportError("PAGINATION_INVALID")
            if "next" not in envelope:
                # An absent cursor cannot establish complete coverage when the
                # provider's own stable count says more rows remain unseen.
                if declared_total is not None and declared_total > initial_start + raw_records:
                    raise TransportError("PAGINATION_INVALID")
                return {
                    "records": records,
                    "complete": True,
                    "pages": page_count,
                    "next": None,
                    "duplicate_records": duplicate_records,
                }
            following = envelope["next"]
            if type(following) is not int or following <= cursor or following in seen_cursors:
                raise TransportError("PAGINATION_INVALID")
            cursor = following
        return {
            "records": records,
            "complete": False,
            "pages": max_pages,
            "next": cursor,
            "duplicate_records": duplicate_records,
        }
