"""
FastAPI bindings for Inertia.js
"""
import functools
import json
import os
import pathlib
from typing import Any, Callable, Dict, List, Optional, Union

import jinja2
import starlette
import starlette.requests
import starlette.responses
import starlette.types


class InertiaResponse(starlette.responses.JSONResponse):
    def __init__(self, *args, component: str = None, **kwargs) -> None:
        if component is None:
            raise ValueError("Must provide component to InertiaResponse.")
        self.component = component
        self.content = None
        super().__init__(*args, **kwargs)

    def render(self, content: Any) -> bytes:
        # Delay rendering until we've gotten a header back from the send of
        # http.response.start.
        self.content = content
        return b""

    async def __call__(
        self,
        scope: starlette.types.Scope,
        receive: starlette.types.Receive,
        send: starlette.types.Send,
    ) -> None:
        del scope, receive
        await send(
            {
                "type": "http.response.start",
                "status": self.status_code,
                "headers": self.raw_headers,
            }
        )
        inertia_version = self.headers.get("X-Inertia-Internal-Version", None)
        inertia_url = self.headers.get("X-Inertia-Internal-Path", None)
        del self.headers["X-Inertia-Internal-Version"]
        del self.headers["X-Inertia-Internal-Path"]
        content = {
            "component": self.component,
            "version": inertia_version,
            "props": self.content,
            "url": inertia_url,
        }
        self.body = super().render(content)
        await send({"type": "http.response.body", "body": self.body})

        if self.background is not None:
            await self.background()


class InertiaMiddleware:
    """Inertia.js Middleware for FastAPI."""

    def __init__(
        self,
        app: starlette.types.ASGIApp,
        asset_version: Union[Callable[[], str], str],
        scripts: Optional[List[str]] = None,
        links: Optional[List[str]] = None,
        index_template_path: Optional[Union[str, pathlib.Path]] = None,
    ) -> None:
        self.app = app
        self.asset_version = asset_version
        template_path = pathlib.Path(__file__).parent / "index.html.jinja2"
        if index_template_path is not None:
            template_path = pathlib.Path(index_template_path)
        with open(template_path) as f:
            self.template = jinja2.Template(f.read())
        self.extra_template_data = {
            "scripts": scripts or [],
            "links": links or [],
        }
        # TODO add callable for inclusion in all response bodies.
        # TODO render func to replace the default template logic?
        # TODO add an option for the route object to generate js routes with. This needs
        # to be able to exclude this wrapped app somehow, and maybe others.

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
        request = starlette.requests.Request(scope, receive)
        responder = InertiaResponder(
            self.app,
            inertia_version=self._inertia_version,
            template=self.template,
            extra_template_data=self.extra_template_data,
        )
        wrapped_send = functools.partial(responder.send, send=send, request=request)
        if scope["type"] != "http":
            await self.app(scope, receive, wrapped_send)
            return

        method = scope["method"]
        if request.headers.get("x-requested-with") != "XMLHttpRequest":
            # Request is not AJAX, so it's probably a regular old browser GET.
            # Call the endpoint to get the data and then render the HTML.
            resp = await self.app(
                scope, receive, functools.partial(wrapped_send, as_html=True)
            )
            return
        else:
            if not request.headers.get("x-inertia"):
                response = starlette.responses.PlainTextResponse(
                    "Inertia headers not found.",
                    status_code=400,
                )
                await response(scope, receive, wrapped_send)
                return

            # Must be an Inertia request
            server_version = self._inertia_version
            client_version = request.headers.get("x-inertia-version")
            if method == "GET" and client_version != server_version:
                # Version doesn't match, return header telling inertia to refresh
                response = starlette.responses.PlainTextResponse(
                    "Inertia version does not match",
                    status_code=409,
                    headers={"X-Inertia-Location": str(request.url)},
                )
                await response(scope, receive, wrapped_send)
                return

        await self.app(scope, receive, wrapped_send)

    @property
    def _inertia_version(self) -> str:
        return (
            self.asset_version() if callable(self.asset_version) else self.asset_version
        )


class InertiaResponder:
    def __init__(
        self,
        app: starlette.types.ASGIApp,
        inertia_version: str,
        template: jinja2.Template,
        extra_template_data: Dict[str, Any],
    ) -> None:
        self.app = app
        self._inertia_version = inertia_version
        self.started = False
        self.template = template
        self.extra_template_data = extra_template_data

    async def send(
        self,
        message: starlette.types.Message,
        send: starlette.types.Send,
        request: starlette.requests.Request,
        as_html: bool = False,
    ) -> None:
        """
        Wrap the processing of the request/response with the inertia protocol.

        This function is where most of the magic happens.
        """
        # Update redirect to set 303 status code.
        #
        # 409 conflict responses are only sent for GET requests, and not for
        # POST/PUT/PATCH/DELETE requests. That said, they will be sent in the
        # event that a GET redirect occurs after one of these requests.
        #
        # Returning a 303 ensures that the browser follows with a GET, while a 302
        # doesn't necessarily guarantee it.
        if "headers" not in message:
            message["headers"] = []
        headers = starlette.datastructures.MutableHeaders(scope=message)
        if message["type"] == "http.response.start":
            if (
                request.method in {"PUT", "PATCH", "DELETE"}
                and message["status"] == 302
            ):
                # TODO does this work?
                headers["Location"] = headers.get("Location")
                await send(
                    {
                        "type": message["type"],
                        "status": 303,
                        "headers": headers.raw,
                    }
                )
            else:
                headers["X-Inertia"] = "true"
                if as_html:
                    headers["Content-Type"] = "text/html"
                else:
                    headers["Content-Type"] = "application/json"
                    if not any(
                        [
                            x in headers
                            for x in [
                                "x-inertia-partial-data",
                                "x-inertia-partial-component",
                            ]
                        ]
                    ):
                        headers.add_vary_header("Accept")
                # Pass a header back to the response __call__ so it can add the asset
                # version and url to the JSON response object.
                headers["X-Inertia-Internal-Version"] = self._inertia_version
                # TODO does this need more? fragment?
                headers["X-Inertia-Internal-Path"] = request.url.path
                # Don't send the message until we can figure out what the content-length
                # needs to be.
                self.message = message
            return
        elif message["type"] == "http.response.body" and not self.started:
            self.started = True
            body = message.get("body", b"")
            more_body = message.get("more_body", False)
            # TODO figure out what to do if more_body is true, how can we handle that?
            if more_body:
                raise ValueError("Streaming responses are not supported.")
            # TODO add support for partial reloading via X-Inertia-Partial-* headers.
            if as_html:
                # TODO call provided callable if not None, pass in jinja context
                # TODO should we only do this on 2xx?
                template_context = {
                    "body": body,
                }
                template_context.update(self.extra_template_data)
                if "X-Inertia-Internal-Context" in headers:
                    template_context.update(
                        json.loads(headers["X-Inertia-Internal-Context"])
                    )
                    del headers["X-Inertia-Internal-Context"]
                message["body"] = self.template.render(**template_context).encode()
                headers["Content-Length"] = str(len(message["body"]))
            await send(self.message)
            await send(message)
        else:
            await send(message)

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

    def include_router(self) -> None:
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
