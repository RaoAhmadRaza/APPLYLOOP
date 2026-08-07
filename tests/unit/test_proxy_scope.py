"""M2 gate: "JobSpy goes through the proxy; feeds don't."

Part 13 rule 12 says never proxy the ATS layer. Until M2 that rule had nothing enforcing
it, because nothing in the repo set a proxy — so the first `.env` with `HTTPS_PROXY` in
it would have silently routed every Greenhouse, Lever and Remotive request through
metered residential bandwidth, and no test would have noticed.

The ATS half is asserted against the real `http.client()` factory, deliberately NOT
through `httpx.MockTransport`. httpx skips environment-proxy resolution entirely when a
transport is supplied (`allow_env_proxies = trust_env and transport is None`), so a
mock-transport test passes whether or not the factory is proxied. That blind spot is the
whole reason this file exists.

Offline: no network, no proxy, no container. The proxy string below is fake.
"""

import inspect

import pytest
from workers.scraping import http

# Every spelling httpx honours. `no_proxy` is not here: it only ever *removes* a proxy.
PROXY_ENV_VARS = ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"]


@pytest.mark.parametrize("var", PROXY_ENV_VARS)
def test_the_shared_client_ignores_an_ambient_proxy(
    monkeypatch: pytest.MonkeyPatch, var: str
) -> None:
    """Verified failing before `trust_env=False`: httpx built a proxy mount from each of
    these, and every ATS and feed request would have been billed."""
    monkeypatch.setenv(var, "http://leak.test:3128")

    with http.client() as client:
        assert client._mounts == {}, f"{var} reached the shared client"


def test_the_shared_client_takes_no_arguments() -> None:
    """The next person's guard. The test above keeps passing if someone adds an optional
    `proxy=` parameter defaulting to off — this one does not. The aggregator's proxy is
    an argument to `scrape_jobs()`, and there is no route from it to this factory."""
    assert inspect.signature(http.client).parameters == {}


def test_the_http_module_cannot_see_a_proxy_setting() -> None:
    """The value never enters this module. `http.py` does not import settings, so there
    is nothing here for a proxy to be read from even by accident.

    The matching half — that a proxy never becomes a Celery task argument, where it
    would be logged — is in tests/integration/test_scraping_tasks.py, which already has
    the app fixture."""
    assert "workers.settings" not in inspect.getsource(http)
