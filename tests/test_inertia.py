import unittest.mock
from typing import Dict

import pytest
import starlette.applications
import starlette.responses
import starlette.routing
import starlette.testclient

import starlette_inertia as target


def index_handler(request: starlette.requests.Request) -> starlette.responses.Response:
    return target.InertiaResponse({"foo": "bar"}, component="Test")


class TestMiddleware:
    @pytest.mark.parametrize(
        "headers, url, expected_status, expected_html",
        [
            # Regular 200, not AJAX so just passed through and the response is
            # embedded in the returned HTML object.
            [
                {},
                "/",
                200,
                True,
            ],
            # Basic AJAX request, with a matching inertia verison. Should just get a
            # back a basic JSON object.
            [
                {
                    "x-requested-with": "XMLHttpRequest",
                    "x-inertia": "true",
                    "x-inertia-version": "foo",
                },
                "/",
                200,
                False,
            ],
            # Basic AJAX request, but erroneously missing the inertia header.
            [
                {
                    "x-requested-with": "XMLHttpRequest",
                },
                "/",
                400,
                False,
            ],
            [
                {
                    "x-requested-with": "XMLHttpRequest",
                    "x-inertia-version": "foo",
                },
                "/",
                400,
                False,
            ],
            # Basic AJAX request, with a non-matching inertia verison.
            # Should get 409 telling inertia to fetch the whole page fresh.
            [
                {
                    "x-requested-with": "XMLHttpRequest",
                    "x-inertia": "true",
                    "x-inertia-version": "bar",
                },
                "/",
                409,
                False,
            ],
            [
                {
                    "x-requested-with": "XMLHttpRequest",
                    "x-inertia": "true",
                },
                "/",
                409,
                False,
            ],
        ],
    )
    def test_basic(
        self,
        headers: Dict[str, str],
        url: str,
        expected_status: int,
        expected_html: bool,
    ) -> None:
        app = starlette.applications.Starlette(
            debug=True,
            routes=[
                starlette.routing.Route("/", index_handler),
            ],
            middleware=[
                starlette.middleware.Middleware(
                    target.InertiaMiddleware, asset_version="foo"
                ),
            ],
        )

        client = starlette.testclient.TestClient(app)
        response = client.get(url, headers=headers)
        assert response.status_code == expected_status
        if expected_html:
            assert response.headers.get("Content-Type", None) == "text/html"
        else:
            assert response.headers.get("Content-Type", None) == "application/json"
        assert response.headers.get("X-Inertia", None) == "true"


# TODO add test that assert the structure of the returned JSON objects.

# TODO add tests that assert that the returned X-Inertia-Location header on 409s is
# correct.

# TODO add tests that AJAX requests return the wrapper HTML, and that partial
# requests return JSON
