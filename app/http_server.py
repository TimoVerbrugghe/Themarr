"""Serve Flask and API-key-only Streamable HTTP MCP on the same port."""

import hmac
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx2
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware.wsgi import WSGIMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount

from app.mcp_server import create_server
from app import web_app

logger = logging.getLogger(__name__)


class LocalAPIClient:
    """Dispatch tools through Flask's WSGI API without a second HTTP listener."""

    def request(self, method, path, *, params=None, data=None):
        api_key = web_app._get_api_key()
        if not api_key:
            raise ToolError('MCP access is disabled; configure API_KEY to enable it')
        with httpx2.Client(
            transport=httpx2.WSGITransport(web_app.app),
            base_url='http://themarr.internal',
            headers={'Authorization': f'Bearer {api_key}'},
        ) as client:
            response = client.request(method, path, params=params, json=data)
            try:
                payload = response.json()
            except ValueError as exc:
                raise ToolError(f'Themarr returned a non-JSON response (HTTP {response.status_code}).') from exc
            if not response.is_success:
                detail = payload.get('error') if isinstance(payload, dict) else None
                raise ToolError(f'Themarr HTTP {response.status_code}: {detail or "Request failed"}')
            return payload


class MCPAuthentication:
    """Authenticate every MCP request independently of sessions and DISABLE_AUTH."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        request = Request(scope)
        provided = (request.headers.get('x-themarr-api-key') or '').strip()
        if not provided:
            authorization = request.headers.get('authorization', '')
            scheme, _, value = authorization.partition(' ')
            if scheme.lower() == 'bearer':
                provided = value.strip()
        expected = web_app._get_api_key()
        if not expected:
            logger.warning('Rejected MCP request: API_KEY is not configured')
            response = JSONResponse(
                {'error': 'MCP access is disabled; configure API_KEY to enable it'},
                status_code=503, headers={'Cache-Control': 'no-store'},
            )
            return await response(scope, receive, send)
        if not provided or not hmac.compare_digest(provided.encode('utf-8'), expected.encode('utf-8')):
            logger.warning('Rejected MCP request without a valid API key')
            response = JSONResponse(
                {'error': 'A valid Themarr API key is required'}, status_code=401,
                headers={'WWW-Authenticate': 'Bearer', 'Cache-Control': 'no-store'},
            )
            return await response(scope, receive, send)
        origin = request.headers.get('origin')
        if origin:
            try:
                parsed = urlsplit(origin)
            except ValueError:
                parsed = None
            if (
                parsed is None or parsed.scheme not in ('http', 'https')
                or parsed.netloc != request.headers.get('host')
                or parsed.path or parsed.query or parsed.fragment
            ):
                logger.warning('Rejected cross-origin MCP request')
                response = JSONResponse({'error': 'Cross-origin MCP requests are not allowed'}, status_code=403)
                return await response(scope, receive, send)
        await self.app(scope, receive, send)


def create_http_app():
    server = create_server('http://themarr.internal', 'internal', client=LocalAPIClient())
    mcp_app = server.streamable_http_app(
        stateless_http=True, json_response=True, max_request_body_size=1024 * 1024,
        # API-key and same-origin checks above protect the intentionally remote endpoint.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @asynccontextmanager
    async def lifespan(app):
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    flask_app = WSGIMiddleware(web_app.app)
    protected_mcp = MCPAuthentication(mcp_app)

    async def dispatch(scope, receive, send):
        if scope.get('path') in ('/mcp', '/mcp/'):
            scope = dict(scope)
            scope['path'] = '/mcp'
            scope['raw_path'] = b'/mcp'
            await protected_mcp(scope, receive, send)
        else:
            await flask_app(scope, receive, send)

    return Starlette(routes=[Mount('/', app=dispatch)], lifespan=lifespan)


app = create_http_app()
