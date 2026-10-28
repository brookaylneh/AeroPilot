"""Request failures retain timing without recording sensitive transport metadata."""

import unittest

import httpx

from mission_planner.agents.models import post


class RequestTiming(unittest.TestCase):
    def test_http_failure_retains_status_and_sanitized_timing(self):
        def handler(request):
            trace = request.extensions["trace"]
            trace("http11.send_request_body.started", {"headers": "secret"})
            trace("http11.send_request_body.complete", {})
            return httpx.Response(503, headers={"x-request-id": "request", "secret": "hidden"})

        timing = {}
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(httpx.HTTPStatusError):
                post(client, "https://example.test", {}, timing)
        self.assertEqual(timing["status_code"], 503)
        self.assertFalse(timing["complete"])
        self.assertGreaterEqual(timing["upload_s"], 0)
        self.assertEqual(timing["response_headers"], {"x-request-id": "request"})
        self.assertNotIn("secret", str(timing))

    def test_network_failure_records_partial_transport_without_retry(self):
        calls, timing = [], {}

        def handler(request):
            calls.append(request)
            request.extensions["trace"]("connection.connect_tcp.started", {})
            raise httpx.ConnectTimeout("Connection deadline expired", request=request)

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(httpx.ConnectTimeout):
                post(client, "https://example.test", {}, timing)
        self.assertEqual(len(calls), 1)
        self.assertFalse(timing["complete"])
        self.assertIsNone(timing["status_code"])
        self.assertIsNone(timing["upload_s"])
        self.assertIsNone(timing["response_bytes"])
        self.assertGreaterEqual(timing["roundtrip_s"], 0)

    def test_bad_json_retains_successful_http_status(self):
        timing = {}
        with httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text="invalid"))
        ) as client:
            with self.assertRaises(ValueError):
                post(client, "https://example.test", {}, timing)
        self.assertEqual(timing["status_code"], 200)
        self.assertFalse(timing["complete"])
        self.assertEqual(timing["response_bytes"], 7)


if __name__ == "__main__":
    unittest.main()
