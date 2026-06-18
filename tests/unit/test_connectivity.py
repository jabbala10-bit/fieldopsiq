"""Unit tests for src/services/sync/connectivity.py."""
from __future__ import annotations

import httpx
import pytest

from src.domain.schemas import ConnectivityState
from src.services.sync.connectivity import ConnectivityProbe


def _mock_transport(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


class TestCheck:
    def test_online_when_reachable_and_fast(self, test_settings):
        def handler(request):
            return httpx.Response(200)

        client = _mock_transport(handler)
        probe = ConnectivityProbe(test_settings, client=client)
        assert probe.check() == ConnectivityState.ONLINE

    def test_offline_when_connection_error(self, test_settings):
        def handler(request):
            raise httpx.ConnectError("refused")

        client = _mock_transport(handler)
        probe = ConnectivityProbe(test_settings, client=client)
        assert probe.check() == ConnectivityState.OFFLINE

    def test_offline_when_server_5xx(self, test_settings):
        def handler(request):
            return httpx.Response(503)

        client = _mock_transport(handler)
        probe = ConnectivityProbe(test_settings, client=client)
        assert probe.check() == ConnectivityState.OFFLINE

    def test_online_on_4xx_since_server_is_reachable(self, test_settings):
        # A 404 on /health still proves the server itself is up.
        def handler(request):
            return httpx.Response(404)

        client = _mock_transport(handler)
        probe = ConnectivityProbe(test_settings, client=client)
        assert probe.check() == ConnectivityState.ONLINE

    def test_last_known_state_updates_after_check(self, test_settings):
        def handler(request):
            return httpx.Response(200)

        client = _mock_transport(handler)
        probe = ConnectivityProbe(test_settings, client=client)
        assert probe.last_known_state == ConnectivityState.OFFLINE  # default before any check
        probe.check()
        assert probe.last_known_state == ConnectivityState.ONLINE


class TestDeriveHealthUrl:
    def test_derives_health_endpoint_from_reports_url(self, test_settings):
        probe = ConnectivityProbe(test_settings)
        url = probe._derive_health_url("https://central.example.com/api/v1/reports")
        assert url == "https://central.example.com/api/v1/health"

    def test_passthrough_when_already_health(self, test_settings):
        probe = ConnectivityProbe(test_settings)
        url = probe._derive_health_url("https://central.example.com/health")
        assert url == "https://central.example.com/health"
