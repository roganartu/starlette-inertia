"""
FastAPI bindings for Inertia.js
"""

import os
from typing import Any, Optional

from flask import Flask, Markup, Response, current_app, request
from flask_inertia.version import get_asset_version
from jinja2 import Template
from jsmin import jsmin
import starlette
from werkzeug.exceptions import BadRequest


class InertiaMiddleware:
    """Inertia.js Middleware for FastAPI."""

    def __init__(self, app: starlette.types.ASGIApp) -> None:
        self.app = app
        # TODO other options?
        # TODO add an asset version arg that can either be a const or a callable.
        # TODO add a jinja template var (optional) for overriding the index render.
        # TODO add vars to control various knobs in the rendered index html.
        # TODO add callable for inclusion in all response bodies.
        # TODO add an option for setting the Inertia.js asset URL (default to JS CDN).

    async def __call__(
        self,
        scope: starlette.types.Scope,
        receive: starlette.types.Receive,
        send: starlette.types.Send,
    ) -> None:
        """Process incoming Inertia requests.

        AJAX requests must be issued by Inertia.

        Whenever an Inertia request is made, Inertia will include the current asset
        version in the X-Inertia-Version header. If the asset versions are the same,
        the request simply continues as expected. However, if they are different,
        the server immediately returns a 409 Conflict response (only for GET request),
        and includes the URL in a X-Inertia-Location header.
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope["method"]
        headers = starlette.datastructures.Headers(scope=scope)
        render_html = False
        if headers.get("x-requested-with") != "XMLHttpRequest":
            # Request is not AJAX, so it's probably a regular old browser GET.
            # Call the endpoint to get the data and then render the HTML.
            # TODO
            render_html = True
        else:
            if not headers.get("x-inertia"):
                response = starlette.responses.Response(
                    "Inertia headers not found.",
                    status_code=400,
                    media_type="text/plain",
                )
                await response(scope, receive, send)
                return

            # Must be an Inertia request
            # TODO check asset version and return x-inertia-location if required
            # TODO call next step in app and wrap it, unless it returns an error.

        # TODO if render_html is true, render the JSON response into the HTML body via
        # jinja.
        raise NotImplementedError()

    def process_incoming_inertia_requests(self) -> Optional[Response]:
        # check inertia version
        server_version = get_asset_version()
        inertia_version = request.headers.get("X-Inertia-Version")
        if (
            request.method == "GET"
            and inertia_version
            and inertia_version != server_version
        ):
            response = Response("Inertia versions does not match", status=409)
            response.headers["X-Inertia-Location"] = request.full_path
            return response

        return None

    def update_redirect(self, response: Response) -> Response:
        """Update redirect to set 303 status code.

        409 conflict responses are only sent for GET requests, and not for
        POST/PUT/PATCH/DELETE requests. That said, they will be sent in the
        event that a GET redirect occurs after one of these requests. To force
        Inertia to use a GET request after a redirect, the 303 HTTP status is used

        :param response: The generated response to update
        """
        if request.method in ["PUT", "PATCH", "DELETE"] and response.status_code == 302:
            response.status_code = 303

        return response

    def share(self, key: str, value: Any):
        """Preassign shared data for each request.

        Sometimes you need to access certain data on numerous pages within your
        application. For example, a common use-case for this is showing the
        current user in the site header. Passing this data manually in each
        response isn't practical. In these situations shared data can be useful.

        :param key: Data key to share between requests
        :param value: Data value or Function returning the data value
        """
        self._shared_data[key] = value

    @staticmethod
    def context_processor():
        """Add an `inertia` directive to Jinja2 template to allow router inclusion

        .. code-block:: html

           <head>
             <script lang="javascript">
               {{ inertia.include_router() }}
             </script>
           </head>
        """
        return {
            "inertia": current_app.extensions["inertia"],
        }

    def include_router(self) -> Markup:
        """Include JS router in Templates."""
        router_file = os.path.join(
            os.path.abspath(os.path.dirname(__file__)), "router.js"
        )
        routes = {rule.endpoint: rule.rule for rule in current_app.url_map.iter_rules()}
        with open(router_file, "r") as jsfile:
            template = Template(jsfile.read())
            # Jinja2 template automatically get rid of ['<'|'>'] chars
            content = (
                template.render(routes=routes)
                .replace("\\u003c", "<")
                .replace("\\u003e", ">")
            )
            content_minified = jsmin(content)

        return Markup(content_minified)
