"""MCP tool contracts, HTTP transport safety and real stdio protocol smoke test."""

import asyncio
import sys
import threading
from unittest.mock import MagicMock, patch

import pytest
import requests
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.mcpserver.exceptions import ToolError
from werkzeug.serving import make_server

from app.cache import set_library_cache_for_section
from app.mcp_server import ThemarrClient, create_server
from tests.helpers import make_agent_library_item as library_item


@pytest.mark.parametrize('url', [
    'file:///tmp/server', 'https://user:secret@example.com', 'http://example.com?token=secret',
    'http://example.com#fragment', 'http:///missing-host',
])
def test_should_reject_unsafe_server_urls(url):
    with pytest.raises(ValueError, match='Themarr URL must be'):
        ThemarrClient(url, 'test-key')


def test_should_require_api_key():
    with pytest.raises(ValueError, match='API_KEY is required'):
        ThemarrClient('http://localhost:8080', '')


def mock_response(payload, status=200):
    response = MagicMock()
    response.status_code = status
    response.json.return_value = payload
    return response


def test_should_send_auth_with_bounded_timeout_and_disable_redirects():
    client = ThemarrClient('https://themarr.example/prefix/', 'test-key')
    response = mock_response({'version': '1'})
    with patch('app.mcp_server.requests.request', return_value=response) as send:
        assert client.request('GET', '/api/agent/v1/capabilities') == {'version': '1'}
    assert send.call_args.args == ('GET', 'https://themarr.example/prefix/api/agent/v1/capabilities')
    assert send.call_args.kwargs['headers']['Authorization'] == 'Bearer test-key'
    assert send.call_args.kwargs['timeout'] == (5, 110)
    assert send.call_args.kwargs['allow_redirects'] is False
    response.__exit__.assert_called_once()


@pytest.mark.parametrize('status,message', [(401, 'Unauthorized'), (409, 'Theme already exists'), (502, 'Source failed')])
def test_should_report_http_failures_as_tool_errors(status, message):
    client = ThemarrClient('http://localhost:8080', 'test-key')
    with patch('app.mcp_server.requests.request', return_value=mock_response({'error': message}, status)):
        with pytest.raises(ToolError, match=f'Themarr HTTP {status}: {message}'):
            client.request('POST', '/api/agent/v1/items/plex/1/download')


@pytest.mark.parametrize('status', [200, 302, 500])
def test_should_report_non_json_responses(status):
    response = mock_response(None, status)
    response.json.side_effect = ValueError('private HTML body')
    with patch('app.mcp_server.requests.request', return_value=response):
        with pytest.raises(ToolError, match=f'non-JSON response \\(HTTP {status}\\)'):
            ThemarrClient('http://localhost:8080', 'test-key').request('GET', '/api/libraries')


@pytest.mark.parametrize('exception,message', [
    (requests.Timeout('private details'), 'download may still be running'),
    (requests.ConnectionError('private details'), 'Cannot reach Themarr'),
])
def test_should_report_transport_failure_without_leaking_details(exception, message):
    with patch('app.mcp_server.requests.request', side_effect=exception):
        with pytest.raises(ToolError, match=message) as error:
            ThemarrClient('http://localhost:8080', 'test-key').request('GET', '/api/libraries')
    assert 'private details' not in str(error.value)


def test_should_expose_typed_tools_and_mark_download_as_mutating():
    server = create_server('http://localhost:8080', 'test-key')
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    assert set(tools) == {
        'list_libraries', 'list_items', 'get_theme_options', 'search_youtube',
        'download_theme', 'get_capabilities', 'get_cache_status',
    }
    assert tools['list_items'].annotations.read_only_hint is True
    assert tools['download_theme'].annotations.read_only_hint is False
    assert tools['download_theme'].annotations.destructive_hint is True
    schema = tools['download_theme'].input_schema
    assert schema['required'] == ['provider', 'item_id', 'source']
    assert schema['properties']['overwrite']['default'] is False


def test_should_forward_download_arguments_and_keep_overwrite_disabled():
    server = create_server('http://localhost:8080', 'test-key')
    with patch('app.mcp_server.requests.request', return_value=mock_response({'success': True})) as send:
        result = asyncio.run(server.call_tool('download_theme', {
            'provider': 'jellyfin', 'item_id': 'abc', 'source': 'youtube',
            'url': 'https://youtu.be/abc', 'start_time': '10', 'end_time': '30',
        }))
    assert result.is_error is False
    assert result.structured_content == {'success': True}
    assert send.call_args.kwargs['json'] == {
        'source': 'youtube', 'overwrite': False, 'url': 'https://youtu.be/abc',
        'start_time': '10', 'end_time': '30',
    }


@pytest.mark.parametrize('tool,arguments', [
    ('get_theme_options', {'provider': 'plex', 'item_id': '../settings'}),
    ('list_items', {'provider': 'jellyfin', 'library_id': 'abc?token=secret'}),
    ('search_youtube', {'query': 'theme', 'limit': 0}),
    ('search_youtube', {'query': '', 'limit': 5}),
])
def test_should_reject_invalid_tool_arguments_before_http_request(tool, arguments):
    server = create_server('http://localhost:8080', 'test-key')
    with patch('app.mcp_server.requests.request') as send:
        with pytest.raises(ToolError):
            asyncio.run(server.call_tool(tool, arguments))
    send.assert_not_called()


def test_should_initialize_list_and_call_tools_over_real_stdio(app, monkeypatch):
    monkeypatch.setenv('API_KEY', 'stdio-test-key')
    monkeypatch.setenv('DISABLE_AUTH', 'false')
    set_library_cache_for_section(1, [library_item('1', 'A Show', remote=True)])
    http_server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=http_server.serve_forever, daemon=True)
    thread.start()

    async def exercise_protocol():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=['-m', 'app.mcp_server', '--url', f'http://127.0.0.1:{http_server.server_port}'],
            env={'API_KEY': 'stdio-test-key'},
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=15) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert any(tool.name == 'download_theme' for tool in tools.tools)
                capabilities = await session.call_tool('get_capabilities', {})
                assert capabilities.is_error is False
                assert capabilities.structured_content['version'] == '1'
                items = await session.call_tool('list_items', {
                    'provider': 'plex', 'library_id': '1', 'theme_status': 'missing',
                })
                assert items.structured_content['items'][0]['title'] == 'A Show'
                invalid_download = await session.call_tool('download_theme', {
                    'provider': 'jellyfin', 'item_id': 'abc', 'source': 'plex',
                })
                assert invalid_download.is_error is True

    try:
        asyncio.run(exercise_protocol())
    finally:
        http_server.shutdown()
        http_server.server_close()
        thread.join(timeout=5)
    assert not thread.is_alive()
