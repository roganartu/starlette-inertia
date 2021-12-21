import unittest.mock
from typing import Dict

import pytest
import starlette.applications
import starlette.responses
import starlette.routing
import starlette.testclient

import starlette_inertia as target


class TestMiddleware:
    @pytest.mark.parametrize(
        "headers, url, expected",
        [
            # Regular 200, not AJAX so just passed right through
            [
                {},
                "/",
                200,
            ],
        ],
    )
    def test_basic(self, headers: Dict[str, str], url: str, expected: int) -> None:
        index_handler = unittest.mock.Mock(
            side_effect=starlette.responses.PlainTextResponse("foo")
        )
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
        assert response.status_code == expected


# TODO add tests that AJAX requests return the wrapper HTML, and that partial
# requests return JSON
