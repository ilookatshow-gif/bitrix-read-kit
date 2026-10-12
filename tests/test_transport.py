"""Offline transport tests: every HTTP response is a synthetic in-memory mock."""

import io
import json
import math
import os
import ssl
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, ProxyHandler

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bitrix_transport import (  # noqa: E402
    MAX_RESPONSE_BYTES,
    TransportError,
    WebhookClient,
    _NoRedirect,
)


FAKE_URL = "https://synthetic-portal.bitrix24.ru/rest/1/not-a-real-secret/"


class FakeResponse:
    def __init__(self, payload=b'{"result":{"items":[]}}', status=200):
        self.payload = payload
        self.status = status
        self.closed = False
        self.read_sizes = []

    def getcode(self):
        return self.status

    def read(self, size):
        self.read_sizes.append(size)
        return self.payload[:size] if isinstance(self.payload, bytes) else self.payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True


class FakeOpener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_response(value):
    return FakeResponse(json.dumps(value, allow_nan=False).encode("utf-8"))


class BaseTransportCase(unittest.TestCase):
    def client(self, *responses, **kwargs):
        opener = FakeOpener(*responses)
        return WebhookClient(FAKE_URL, opener=opener, min_interval=0, **kwargs), opener

    def assert_code(self, code, fn, *args):
        with self.assertRaises(TransportError) as raised:
            fn(*args)
        self.assertEqual(raised.exception.code, code)
        self.assertNotIn(FAKE_URL, str(raised.exception))
        self.assertNotIn("not-a-real-secret", repr(raised.exception))
        return raised.exception


class TransportTests(BaseTransportCase):
    def test_cloud_ru_and_com_urls_supported(self):
        for endpoint in (FAKE_URL, "https://a.bitrix24.com/rest/12/synthetic_token/"):
            client = WebhookClient(endpoint, opener=FakeOpener(), min_interval=0)
            self.assertNotIn(endpoint, repr(client))

    def test_url_boundary_rejects_noncloud_or_ambiguous_endpoints(self):
        invalid = [
            "http://synthetic.bitrix24.ru/rest/1/token/",
            "https://synthetic.bitrix24.ru.evil.example/rest/1/token/",
            "https://x.y.bitrix24.ru/rest/1/token/",
            "https://synthetic.bitrix24.de/rest/1/token/",
            "https://127.0.0.1/rest/1/token/",
            "https://user:pass@synthetic.bitrix24.ru/rest/1/token/",
            "https://synthetic.bitrix24.ru:443/rest/1/token/",
            "https://synthetic.bitrix24.ru/rest/0/token/",
            "https://synthetic.bitrix24.ru/rest/-1/token/",
            "https://synthetic.bitrix24.ru/rest/01/token/",
            "https://synthetic.bitrix24.ru/rest/1/token/?x=1",
            "https://synthetic.bitrix24.ru/rest/1/token/#x",
            "https://synthetic.bitrix24.ru/rest/1/to%6ben/",
            "https://synthetic.bitrix24.ru/rest/1/token",
            "https://synthetic.bitrix24.ru/rest/1/token//",
            "https://synthetic.bitrix24.ru/rest/1/token/../",
            " https://synthetic.bitrix24.ru/rest/1/token/",
            "https://synthetic.bitrix24.ru/rest/1/token/\n",
            "https://synthetic.bitrix24.ru/rest/1/token\\/",
            "https://" + "x" * 64 + ".bitrix24.ru/rest/1/token/",
        ]
        for endpoint in invalid:
            with self.subTest(endpoint=endpoint):
                self.assert_code("CONFIG_INVALID", WebhookClient, endpoint)

    def test_from_env_missing_and_valid(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assert_code("CONFIG_MISSING", WebhookClient.from_env)
        with patch.dict(os.environ, {"B24_WEBHOOK_URL": FAKE_URL}, clear=True):
            client = WebhookClient.from_env(opener=FakeOpener(), min_interval=0)
            self.assertEqual(client._base_url, FAKE_URL)

    def test_safe_error_never_echoes_arbitrary_code_or_message(self):
        for error in (TransportError(FAKE_URL, FAKE_URL), TransportError("API_ERROR", FAKE_URL)):
            self.assertNotIn(FAKE_URL, str(error))
            self.assertNotIn("not-a-real-secret", repr(error))
            self.assertNotIn("not-a-real-secret", str(error.args))

    def test_builtin_opener_uses_no_proxy_default_tls_and_no_redirect(self):
        client = WebhookClient(FAKE_URL, min_interval=0)
        handlers = client._opener.handlers
        # Empty ProxyHandler is not retained by urllib, and no proxy handler
        # with an ambient environment configuration must appear instead.
        proxies = [handler for handler in handlers if isinstance(handler, ProxyHandler)]
        self.assertTrue(all(handler.proxies == {} for handler in proxies))
        tls = [handler for handler in handlers if isinstance(handler, HTTPSHandler)]
        self.assertEqual(len(tls), 1)
        self.assertTrue(tls[0]._context.check_hostname)
        self.assertEqual(tls[0]._context.verify_mode, ssl.CERT_REQUIRED)
        redirect = [handler for handler in handlers if isinstance(handler, _NoRedirect)]
        self.assertEqual(len(redirect), 1)
        self.assertIsNone(redirect[0].redirect_request(None, None, 302, "", {}, FAKE_URL))

    def test_valid_read_is_json_post_and_bounded(self):
        response = make_response({"result": {"items": [{"id": 1}]}, "next": 50})
        client, opener = self.client(response, timeout=3)
        body = {"entityTypeId": 2, "select": ["id"], "filter": {"stageSemanticId": "P"}}
        result = client.call("crm.item.list", body)
        request, timeout = opener.calls[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url, FAKE_URL + "crm.item.list")
        self.assertEqual(json.loads(request.data), body)
        self.assertEqual(request.get_header("Accept"), "application/json")
        self.assertEqual(timeout, 3)
        self.assertEqual(result["result"]["items"], [{"id": 1}])
        self.assertEqual(response.read_sizes, [MAX_RESPONSE_BYTES + 1])
        self.assertTrue(response.closed)

    def test_activity_shape_supported(self):
        client, _ = self.client(make_response({"result": [{"ID": "1"}]}))
        self.assertEqual(client.call("crm.activity.list", {})["result"], [{"ID": "1"}])

    def test_method_allowlist_blocks_write_batch_and_injected_paths(self):
        client, opener = self.client()
        for method in ("crm.item.update", "batch", "crm.item.list?auth=x", "../crm.item.list", "CRM.ITEM.LIST", None):
            with self.subTest(method=method):
                self.assert_code("METHOD_BLOCKED", client.call, method, {})
        self.assertEqual(opener.calls, [])

    def test_parameters_reject_auth_nonfinite_and_nonjson_types(self):
        client, opener = self.client()
        cases = [[], {"auth": "synthetic"}, {"filter": {1: "value"}}, {"filter": {"x": math.nan}},
                 {"filter": {"x": math.inf}}, {"select": ("id",)}, {"start": True}, {"start": -1}]
        for params in cases:
            with self.subTest(params=params):
                self.assert_code("PARAMS_INVALID", client.call, "crm.item.list", params)
        self.assertEqual(opener.calls, [])

    def test_request_size_rejected_before_network(self):
        client, opener = self.client()
        self.assert_code("REQUEST_TOO_LARGE", client.call, "crm.item.list", {"filter": {"x": "x" * 300000}})
        self.assertEqual(opener.calls, [])

    def test_response_size_rejected(self):
        client, _ = self.client(FakeResponse(b"x" * (MAX_RESPONSE_BYTES + 1)))
        self.assert_code("RESPONSE_TOO_LARGE", client.call, "crm.item.list", {})

    def test_invalid_json_boundaries(self):
        cases = [b"not json", b"[]", b"null", b'{"result":{"items":[]},"result":{"items":[]}}',
                 b'{"result":{"items":[],"x":1,"x":2}}', b'{"result":{"items":[]},"x":NaN}',
                 b'{"result":{"items":[]},"x":Infinity}', b'{"result":{"items":[]},"x":1e999}',
                 b"\xff", b'{"result":[]}', b'{"result":{"items":null}}', b'{}']
        for payload in cases:
            with self.subTest(payload=payload):
                client, _ = self.client(FakeResponse(payload))
                self.assert_code("RESPONSE_INVALID", client.call, "crm.item.list", {})

    def test_excessive_nested_response_rejected(self):
        nested = "value"
        for _ in range(45):
            nested = [nested]
        client, _ = self.client(make_response({"result": {"items": []}, "x": nested}))
        self.assert_code("RESPONSE_INVALID", client.call, "crm.item.list", {})

    def test_oversized_integer_literal_rejected_before_decimal_conversion(self):
        for sign in (b"", b"-"):
            payload = b'{"result":{"items":[]},"x":' + sign + b"9" * 5000 + b"}"
            client, _ = self.client(FakeResponse(payload))
            self.assert_code("RESPONSE_INVALID", client.call, "crm.item.list", {})

    def test_nonbytes_response_and_wrong_activity_shape_rejected(self):
        client, _ = self.client(FakeResponse("not bytes"))
        self.assert_code("RESPONSE_INVALID", client.call, "crm.item.list", {})
        client, _ = self.client(make_response({"result": {"items": []}}))
        self.assert_code("RESPONSE_INVALID", client.call, "crm.activity.list", {})

    def test_api_errors_are_mapped_and_descriptions_hidden(self):
        for provider, expected in (("ACCESS_DENIED", "ACCESS_DENIED"), ("QUERY_LIMIT_EXCEEDED", "RATE_LIMITED"),
                                   ("invalid_token", "AUTH_ERROR"), ("PORTAL_BLOCKED_BY_LICENSE_SCANNER", "PORTAL_BLOCKED"),
                                   (FAKE_URL, "API_ERROR"), ("", "API_ERROR"), (None, "API_ERROR")):
            client, opener = self.client(make_response({"error": provider, "error_description": FAKE_URL}))
            self.assert_code(expected, client.call, "crm.item.list", {})
            self.assertEqual(len(opener.calls), 1)
        client, _ = self.client(make_response({"result": {"items": []}, "error_description": FAKE_URL}))
        self.assert_code("API_ERROR", client.call, "crm.item.list", {})

    def test_http_errors_are_sanitized_without_reading_error_body(self):
        for status, expected in ((301, "REDIRECT_BLOCKED"), (302, "REDIRECT_BLOCKED"), (401, "AUTH_ERROR"),
                                 (403, "ACCESS_DENIED"), (429, "RATE_LIMITED"), (500, "HTTP_ERROR")):
            error = HTTPError(FAKE_URL, status, FAKE_URL, {"Location": FAKE_URL}, io.BytesIO(FAKE_URL.encode()))
            client, opener = self.client(error)
            raised = self.assert_code(expected, client.call, "crm.item.list", {})
            self.assertTrue(raised.__suppress_context__)
            self.assertEqual(len(opener.calls), 1)

    def test_mocked_redirect_response_never_followed(self):
        client, opener = self.client(FakeResponse(status=307))
        self.assert_code("REDIRECT_BLOCKED", client.call, "crm.item.list", {})
        self.assertEqual(len(opener.calls), 1)

    def test_network_exception_suppressed_and_not_retried(self):
        for error in (URLError(FAKE_URL), TimeoutError(FAKE_URL), ValueError(FAKE_URL)):
            client, opener = self.client(error)
            raised = self.assert_code("NETWORK_ERROR", client.call, "crm.item.list", {})
            self.assertTrue(raised.__suppress_context__)
            self.assertEqual(len(opener.calls), 1)

    def test_transport_configuration_bounds(self):
        for kwargs in ({"timeout": 0}, {"timeout": True}, {"timeout": math.nan}, {"timeout": 121},
                       {"timeout": 10 ** 1000}, {"min_interval": -1}, {"min_interval": math.inf},
                       {"min_interval": 61}, {"clock": None}):
            self.assert_code("CONFIG_INVALID", lambda: WebhookClient(FAKE_URL, opener=FakeOpener(), **kwargs))

    def test_default_pacing_is_sequential(self):
        now = [10.0]
        sleeps = []

        def sleep(delay):
            sleeps.append(delay)
            now[0] += delay

        opener = FakeOpener(make_response({"result": {"items": []}}), make_response({"result": {"items": []}}))
        client = WebhookClient(FAKE_URL, opener=opener, clock=lambda: now[0], sleeper=sleep)
        client.call("crm.item.list", {})
        client.call("crm.item.list", {})
        self.assertEqual(len(opener.calls), 2)
        self.assertAlmostEqual(sleeps[0], 0.6)

    def test_bad_clock_fails_before_network(self):
        opener = FakeOpener()
        client = WebhookClient(FAKE_URL, opener=opener, clock=lambda: math.nan)
        self.assert_code("CONFIG_INVALID", client.call, "crm.item.list", {})
        self.assertEqual(opener.calls, [])


class PaginationTests(BaseTransportCase):
    def test_item_pages_complete_and_does_not_mutate_params(self):
        client, opener = self.client(
            make_response({"result": {"items": [{"id": 1}]}, "next": 50}),
            make_response({"result": {"items": [{"id": 2}]}}),
        )
        params = {"entityTypeId": 2, "select": ["id"]}
        result = client.pages("crm.item.list", params, 3)
        self.assertEqual(result, {"records": [{"id": 1}, {"id": 2}], "complete": True,
                                  "pages": 2, "next": None, "duplicate_records": 0})
        self.assertNotIn("start", params)
        self.assertEqual([json.loads(call[0].data)["start"] for call in opener.calls], [0, 50])

    def test_activity_pages_accept_positive_legacy_string_ids(self):
        client, _ = self.client(make_response({"result": [{"ID": "21"}], "next": 50}),
                                make_response({"result": [{"ID": "22"}]}))
        result = client.pages("crm.activity.list", {}, 2)
        self.assertTrue(result["complete"])
        self.assertEqual(len(result["records"]), 2)

    def test_page_limit_reports_incomplete_and_resume_cursor(self):
        client, opener = self.client(make_response({"result": {"items": [{"id": 1}]}, "next": 50}))
        result = client.pages("crm.item.list", {}, 1)
        self.assertEqual(result, {"records": [{"id": 1}], "complete": False, "pages": 1,
                                  "next": 50, "duplicate_records": 0})
        self.assertEqual(len(opener.calls), 1)

    def test_empty_last_page_is_complete(self):
        client, _ = self.client(make_response({"result": {"items": []}}))
        self.assertTrue(client.pages("crm.item.list", {}, 1)["complete"])

    def test_next_must_be_strict_monotonic_integer(self):
        for cursor in (0, -1, True, "50", None, 1.5):
            client, opener = self.client(make_response({"result": {"items": []}, "next": cursor}))
            self.assert_code("PAGINATION_INVALID", client.pages, "crm.item.list", {}, 2)
            self.assertEqual(len(opener.calls), 1)

    def test_repeated_or_backward_cursor_rejected(self):
        for cursor in (50, 0, 25):
            client, _ = self.client(make_response({"result": {"items": []}, "next": 50}),
                                    make_response({"result": {"items": []}, "next": cursor}))
            self.assert_code("PAGINATION_INVALID", client.pages, "crm.item.list", {}, 3)

    def test_identical_duplicate_records_are_counted_and_deduplicated(self):
        record = {"ID": "21", "COMPLETED": "N", "DEADLINE": "2026-10-10T11:00:00+03:00"}
        reordered = dict(reversed(list(record.items())))
        client, _ = self.client(make_response({"result": [record, reordered], "next": 50}),
                                make_response({"result": [record]}))
        result = client.pages("crm.activity.list", {}, 2)
        self.assertEqual(result["records"], [record])
        self.assertEqual(result["duplicate_records"], 2)
        self.assertTrue(result["complete"])

    def test_conflicting_duplicate_ids_fail_without_raw_record(self):
        client, _ = self.client(make_response({"result": {"items": [{"id": 1, "title": "PRIVATE"}]}, "next": 50}),
                                make_response({"result": {"items": [{"id": 1, "title": "DIFFERENT"}]}}))
        error = self.assert_code("PAGINATION_CONFLICT", client.pages, "crm.item.list", {}, 2)
        self.assertNotIn("PRIVATE", str(error))
        self.assertNotIn("DIFFERENT", repr(error))

    def test_bad_record_ids_are_rejected(self):
        for record in ({}, {"id": True}, {"id": 0}, {"id": -1}, {"id": "01"}, {"id": "x"}, [], {"id": 1.0}):
            client, _ = self.client(make_response({"result": {"items": [record]}}))
            self.assert_code("PAGINATION_INVALID", client.pages, "crm.item.list", {}, 1)

    def test_invalid_page_limits_and_initial_cursors_do_not_call(self):
        client, opener = self.client()
        for limit in (0, True, 101, 1.5):
            self.assert_code("PARAMS_INVALID", client.pages, "crm.item.list", {}, limit)
        for start in (-1, True, "0"):
            self.assert_code("PARAMS_INVALID", client.pages, "crm.item.list", {"start": start}, 1)
        self.assertEqual(opener.calls, [])

    def test_terminal_total_deficit_cannot_claim_complete_absence(self):
        for records in ([], [{"ID": "1"}]):
            client, _ = self.client(make_response({"result": records, "total": 5}))
            self.assert_code("PAGINATION_INVALID", client.pages, "crm.activity.list", {}, 1)

    def test_terminal_total_accounts_for_initial_offset(self):
        client, _ = self.client(make_response({"result": [{"ID": "6"}], "total": 6}))
        self.assertTrue(client.pages("crm.activity.list", {"start": 5}, 1)["complete"])

    def test_total_zero_with_rows_and_unique_count_above_total_are_invalid(self):
        for rows, total in (([{"ID": "1"}], 0), ([{"ID": "1"}, {"ID": "2"}], 1)):
            client, _ = self.client(make_response({"result": rows, "total": total}))
            self.assert_code("PAGINATION_INVALID", client.pages, "crm.activity.list", {}, 1)

    def test_declared_total_requires_bounded_nonnegative_integer(self):
        for total in (-1, True, "1", 1.0, None, 1 << 63):
            client, _ = self.client(make_response({"result": [], "total": total}))
            self.assert_code("PAGINATION_INVALID", client.pages, "crm.activity.list", {}, 1)

    def test_stable_total_across_pages_is_accepted(self):
        client, _ = self.client(make_response({"result": [{"ID": "1"}], "total": 2, "next": 50}),
                                make_response({"result": [{"ID": "2"}], "total": 2}))
        result = client.pages("crm.activity.list", {}, 2)
        self.assertTrue(result["complete"])
        self.assertEqual(len(result["records"]), 2)

    def test_changing_total_is_conflict_not_complete(self):
        for next_total in (1, 3):
            client, _ = self.client(make_response({"result": [{"ID": "1"}], "total": 2, "next": 50}),
                                    make_response({"result": [{"ID": "2"}], "total": next_total}))
            self.assert_code("PAGINATION_CONFLICT", client.pages, "crm.activity.list", {}, 2)

    def test_missing_total_preserves_documented_next_based_completion(self):
        client, _ = self.client(make_response({"result": [], "next": 50}),
                                make_response({"result": []}))
        self.assertTrue(client.pages("crm.activity.list", {}, 2)["complete"])

    def test_total_once_seen_still_constrains_later_missing_total(self):
        client, _ = self.client(make_response({"result": [{"ID": "1"}], "total": 5, "next": 50}),
                                make_response({"result": [{"ID": "2"}]}))
        self.assert_code("PAGINATION_INVALID", client.pages, "crm.activity.list", {}, 2)

    def test_total_uses_raw_rows_including_identical_binding_duplicates(self):
        rows = [{"ID": "1"}, {"ID": "1"}, {"ID": "2"}, {"ID": "3"}, {"ID": "3"}]
        client, _ = self.client(make_response({"result": rows, "total": 5}))
        result = client.pages("crm.activity.list", {}, 1)
        self.assertTrue(result["complete"])
        self.assertEqual(len(result["records"]), 3)
        self.assertEqual(result["duplicate_records"], 2)

    def test_page_cap_with_total_deficit_remains_explicitly_incomplete(self):
        client, _ = self.client(make_response({"result": [{"ID": "1"}], "total": 5, "next": 50}))
        result = client.pages("crm.activity.list", {}, 1)
        self.assertFalse(result["complete"])
        self.assertEqual(result["next"], 50)

    def test_page_row_count_cannot_exceed_static_provider_size(self):
        client, _ = self.client(make_response({"result": [{"ID": str(index)} for index in range(1, 52)]}))
        self.assert_code("PAGINATION_INVALID", client.pages, "crm.activity.list", {}, 1)


if __name__ == "__main__":
    unittest.main()
