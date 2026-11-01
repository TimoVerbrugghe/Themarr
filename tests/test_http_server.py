"""Hosted MCP authentication and real Streamable HTTP protocol tests."""

import asyncio
import socket
import threading
from contextlib import contextmanager

import httpx2
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.testclient import TestClient

from app.cache import set_library_cache_for_section
from app.http_server import create_http_app
from tests.helpers import make_agent_library_item


@pytest.mark.parametrize('method', ['GET', 'POST', 'DELETE'])
@pytest.mark.parametrize('path', ['/mcp', '/mcp/'])
@pytest.mark.parametrize('disabled', ['true', 'false'])
def test_should_require_key_regardless_of_ui_auth(app, monkeypatch, method, path, disabled):
    monkeypatch.setenv('API_KEY', 'http-test-key')
    monkeypatch.setenv('DISABLE_AUTH', disabled)
    with TestClient(create_http_app()) as client:
        response = client.request(method, path)
        assert response.status_code == 401
        assert response.headers['www-authenticate'] == 'Bearer'
        response = client.request(method, path, headers={'Authorization': 'Bearer incorrect'})
        assert response.status_code == 401


def test_should_not_accept_ui_session_or_query_key(app, monkeypatch):
    monkeypatch.setenv('API_KEY', 'http-test-key')
    flask_client = app.test_client()
    with flask_client.session_transaction() as session:
        session['authenticated'] = True
    cookie = flask_client.get_cookie('session')
    with TestClient(create_http_app()) as client:
        response = client.get('/mcp?api_key=http-test-key', headers={'Cookie': f'session={cookie.value}'})
    assert response.status_code == 401


def test_should_reject_cross_origin_with_valid_key(app, monkeypatch):
    monkeypatch.setenv('API_KEY', 'http-test-key')
    with TestClient(create_http_app()) as client:
        response = client.post('/mcp', headers={
            'Authorization': 'Bearer http-test-key', 'Origin': 'https://untrusted.example',
        })
    assert response.status_code == 403


def test_should_reject_malformed_origin_without_server_error(app, monkeypatch):
    monkeypatch.setenv('API_KEY', 'http-test-key')
    with TestClient(create_http_app()) as client:
        response = client.post('/mcp', headers={
            'Authorization': 'Bearer http-test-key', 'Origin': 'http://[invalid',
        })
    assert response.status_code == 403


@pytest.mark.parametrize('key', [None, '', '   '])
@pytest.mark.parametrize('disabled', ['true', 'false'])
def test_should_disable_mcp_without_configured_key(app, monkeypatch, key, disabled):
    if key is None:
        monkeypatch.delenv('API_KEY', raising=False)
    else:
        monkeypatch.setenv('API_KEY', key)
    monkeypatch.setenv('DISABLE_AUTH', disabled)
    with TestClient(create_http_app()) as client:
        for method in ('GET', 'POST', 'DELETE'):
            response = client.request(method, '/mcp', headers={'X-Themarr-Api-Key': 'anything'})
            assert response.status_code == 503
            assert response.json()['error'] == 'MCP access is disabled; configure API_KEY to enable it'


@pytest.mark.parametrize('header', [
    {'Authorization': 'Bearer http-test-key'},
    {'X-Themarr-Api-Key': 'http-test-key'},
])
def test_should_accept_key_and_initialize_at_exact_path(app, monkeypatch, header):
    monkeypatch.setenv('API_KEY', 'http-test-key')
    with TestClient(create_http_app()) as client:
        response = client.post('/mcp', headers={
            **header, 'Accept': 'application/json, text/event-stream',
        }, json={
            'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
            'params': {
                'protocolVersion': '2025-11-25', 'capabilities': {},
                'clientInfo': {'name': 'test', 'version': '1'},
            },
        })
    assert response.status_code == 200
    assert response.json()['result']['serverInfo']['name'] == 'Themarr'


def test_should_keep_flask_routes_available(app):
    with TestClient(create_http_app()) as client:
        assert client.get('/health').status_code == 200
        assert client.get('/api/init').status_code == 200


@contextmanager
def running_server(application):
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    sock.listen()
    server = uvicorn.Server(uvicorn.Config(application, log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{sock.getsockname()[1]}'
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
    assert not thread.is_alive()


def test_should_call_tools_with_real_streamable_http_client(app, monkeypatch):
    monkeypatch.setenv('API_KEY', 'http-test-key')
    monkeypatch.setenv('DISABLE_AUTH', 'false')
    set_library_cache_for_section(1, [make_agent_library_item('1', 'HTTP Show')])

    async def exercise(base_url):
        async with httpx2.AsyncClient(headers={'Authorization': 'Bearer http-test-key'}) as http_client:
            async with streamable_http_client(base_url + '/mcp', http_client=http_client) as streams:
                async with ClientSession(streams[0], streams[1], read_timeout_seconds=15) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    assert len(tools.tools) == 7
                    capabilities = await session.call_tool('get_capabilities', {})
                    assert capabilities.structured_content['version'] == '1'
                    items = await session.call_tool('list_items', {
                        'provider': 'plex', 'library_id': '1', 'theme_status': 'missing',
                    })
                    assert items.structured_content['items'][0]['title'] == 'HTTP Show'
                    invalid = await session.call_tool('download_theme', {
                        'provider': 'jellyfin', 'item_id': 'abc', 'source': 'plex',
                    })
                    assert invalid.is_error
                    http_client.headers['Authorization'] = 'Bearer wrong-key'
                    rejected = await http_client.post(base_url + '/mcp', json={})
                    assert rejected.status_code == 401

    with running_server(create_http_app()) as base_url:
        asyncio.run(exercise(base_url))
