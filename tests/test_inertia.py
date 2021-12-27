import json
import re
from typing import Any, Callable, Dict, Optional, Union

import bs4
import pytest
import starlette.applications
import starlette.responses
import starlette.routing
import starlette.testclient

import starlette_inertia as target


def index_handler(request: starlette.requests.Request) -> starlette.responses.Response:
    del request
    return target.InertiaResponse({"foo": "bar"}, component="Test")


def multi_component_handler(
    request: starlette.requests.Request,
) -> starlette.responses.Response:
    del request
    return target.InertiaResponse(
        {"foo": "bar", "bar": "baz", "baz": "foo"}, component="Test"
    )


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
            # Unknown route
            [
                {},
                "/unknown",
                404,
                True,
            ],
            [
                {
                    "x-requested-with": "XMLHttpRequest",
                    "x-inertia": "true",
                    "x-inertia-version": "foo",
                },
                "/unknown",
                404,
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

    def test_html(self) -> None:
        # TODO add tests for custom templates
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
        response = client.get("/")
        assert response.status_code == 200
        assert response.headers.get("Content-Type", None) == "text/html"
        soup = bs4.BeautifulSoup(response.text, "html.parser")
        assert len(soup.body.find_all("div")) == 1
        data = json.loads(soup.body.find_all("div")[0].get("data-page"))
        assert data.get("component", None) == "Test"
        assert data.get("version", None) == "foo"
        assert data.get("url", None) == "/"
        assert data.get("props", None) == {"foo": "bar"}

    @pytest.mark.parametrize(
        "callback, extra_headers, expected",
        [
            # No callable
            [
                None,
                {},
                {"foo": "bar"},
            ],
            # Callable adds bar
            [
                lambda x: {"bar": "foo"},
                {},
                {"foo": "bar", "bar": "foo"},
            ],
            # Callable overrides foo
            [
                lambda x: {"foo": "baz"},
                {},
                {"foo": "bar"},
            ],
        ],
    )
    def test_props_callback(
        self,
        callback: Optional[Callable[[starlette.requests.Request], Dict[str, Any]]],
        extra_headers: Dict[str, str],
        expected: Dict[str, Any],
    ) -> None:
        app = starlette.applications.Starlette(
            debug=True,
            routes=[
                starlette.routing.Route("/", index_handler),
            ],
            middleware=[
                starlette.middleware.Middleware(
                    target.InertiaMiddleware,
                    asset_version="foo",
                    props_callback=callback,
                ),
            ],
        )

        client = starlette.testclient.TestClient(app)
        headers = dict(
            {
                "x-requested-with": "XMLHttpRequest",
                "x-inertia": "true",
                "x-inertia-version": "foo",
            },
            **extra_headers
        )
        response = client.get("/", headers=headers)
        assert response.status_code == 200
        assert response.headers.get("Content-Type", None) == "application/json"
        assert response.json() == {
            "component": "Test",
            "props": expected,
            "version": "foo",
            "url": "/",
        }

    @pytest.mark.parametrize(
        "extra_headers, expected",
        [
            # Component matches, no partial headers
            [
                {},
                {"foo": "bar", "bar": "baz", "baz": "foo"},
            ],
            # Component matches, partial headers return only a single prop
            [
                {
                    "X-Inertia-Partial-Component": "Test",
                    "X-Inertia-Partial-Data": "foo",
                },
                {"foo": "bar"},
            ],
            # Component matches, multiple props specified as csv
            [
                {
                    "X-Inertia-Partial-Component": "Test",
                    "X-Inertia-Partial-Data": "foo,bar",
                },
                {"foo": "bar", "bar": "baz"},
            ],
            # Component doesn't match, all props returned
            [
                {
                    "X-Inertia-Partial-Component": "Nomatch",
                    "X-Inertia-Partial-Data": "foo",
                },
                {"foo": "bar", "bar": "baz", "baz": "foo"},
            ],
            # Component matches, but no -Data header present
            [
                {
                    "X-Inertia-Partial-Component": "Test",
                },
                {"foo": "bar", "bar": "baz", "baz": "foo"},
            ],
        ],
    )
    def test_partial(
        self,
        extra_headers: Dict[str, str],
        expected: Dict[str, Any],
    ) -> None:
        app = starlette.applications.Starlette(
            debug=True,
            routes=[
                starlette.routing.Route("/", multi_component_handler),
            ],
            middleware=[
                starlette.middleware.Middleware(
                    target.InertiaMiddleware,
                    asset_version="foo",
                ),
            ],
        )

        client = starlette.testclient.TestClient(app)
        headers = dict(
            {
                "x-requested-with": "XMLHttpRequest",
                "x-inertia": "true",
                "x-inertia-version": "foo",
            },
            **extra_headers
        )
        response = client.get("/", headers=headers)
        assert response.status_code == 200
        assert response.headers.get("Content-Type", None) == "application/json"
        assert response.json() == {
            "component": "Test",
            "props": expected,
            "version": "foo",
            "url": "/",
        }

    @pytest.mark.parametrize(
        "pattern, req_path, expected_status",
        [
            # No path regex, should match everything
            [None, "/foo", 409],
            [None, "/bar", 409],
            [None, "/baz", 409],
            # Bad path should 409 too, I think
            # I guess the refresh this forces will itself get a 404 instead?
            [None, "/notfound", 409],
            # Uncompiled regex should be compiled and match the expected paths
            [r"/(foo|bar)", "/foo", 409],
            [r"/(foo|bar)", "/bar", 409],
            [r"/(foo|bar)", "/baz", 200],
            # Bad path should still 404
            [r"/(foo|bar)", "/notfound", 404],
            # Precompiled regex should also work
            [re.compile(r"/(foo|bar)"), "/foo", 409],
            [re.compile(r"/(foo|bar)"), "/bar", 409],
            [re.compile(r"/(foo|bar)"), "/baz", 200],
            # Bad path should still 404
            [re.compile(r"/(foo|bar)"), "/notfound", 404],
        ],
    )
    def test_path_matching(
        self,
        pattern: Optional[Union[str, re.Pattern]],
        req_path: str,
        expected_status: int,
    ) -> None:
        app = starlette.applications.Starlette(
            debug=True,
            routes=[
                starlette.routing.Route(
                    "/foo",
                    lambda x: target.InertiaResponse({"foo": "bar"}, component="Test"),
                ),
                starlette.routing.Route(
                    "/bar",
                    lambda x: target.InertiaResponse({"foo": "bar"}, component="Test"),
                ),
                starlette.routing.Route(
                    "/baz",
                    lambda x: target.InertiaResponse({"foo": "bar"}, component="Test"),
                ),
            ],
            middleware=[
                starlette.middleware.Middleware(
                    target.InertiaMiddleware,
                    asset_version="foo",
                    paths=pattern,
                ),
            ],
        )

        client = starlette.testclient.TestClient(app)
        headers = {
            "x-requested-with": "XMLHttpRequest",
            "x-inertia": "true",
            # No version, to force a 409 if the middleware matches
        }
        response = client.get(req_path, headers=headers)
        assert response.status_code == expected_status


# TODO add test that asserts that passed templates are rendered correctly

# TODO add test that assert the structure of the returned JSON objects.

# TODO add tests that assert that the returned X-Inertia-Location header on 409s is
# correct.
