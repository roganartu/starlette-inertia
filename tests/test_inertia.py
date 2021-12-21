import starlette.responses
import starlette.testclient

import starlette_inertia as target


async def app(scope, receive, send):
    assert scope["type"] == "http"
    response = starlette.responses.HTMLResponse(
        "<html><body>Hello, world!</body></html>"
    )
    await response(scope, receive, send)


def test_app():
    client = starlette.testclient.TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
