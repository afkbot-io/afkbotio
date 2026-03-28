"""Tests for install proxy/network resolvers."""

from __future__ import annotations

import pytest
import typer

from afkbot.cli.presentation.prompt_i18n import PromptLanguage
from afkbot.services.setup.provider_network import resolve_nginx_plan, resolve_proxy


def test_resolve_proxy_accepts_http_url() -> None:
    """HTTP proxy URL should be accepted as-is."""

    proxy_type, proxy_url = resolve_proxy(
        interactive=False,
        value_type="http",
        value_url="http://127.0.0.1:7890",
        default_type="none",
        default_url="",
        lang=PromptLanguage.EN,
    )
    assert proxy_type == "http"
    assert proxy_url == "http://127.0.0.1:7890"


def test_resolve_proxy_supports_shorthand_without_auth() -> None:
    """Shorthand host:port should normalize to URL with selected scheme."""

    proxy_type, proxy_url = resolve_proxy(
        interactive=False,
        value_type="socks5",
        value_url="172.120.177.140:62842",
        default_type="none",
        default_url="",
        lang=PromptLanguage.EN,
    )
    assert proxy_type == "socks5"
    assert proxy_url == "socks5://172.120.177.140:62842"


def test_resolve_proxy_supports_shorthand_with_auth() -> None:
    """Shorthand host:port:user:pass should normalize to auth URL."""

    proxy_type, proxy_url = resolve_proxy(
        interactive=False,
        value_type="socks5",
        value_url="172.120.177.140:62842:JXjyVv2u:X7Pc8rza",
        default_type="none",
        default_url="",
        lang=PromptLanguage.EN,
    )
    assert proxy_type == "socks5"
    assert proxy_url == "socks5://JXjyVv2u:X7Pc8rza@172.120.177.140:62842"


def test_resolve_proxy_rejects_scheme_type_mismatch() -> None:
    """Proxy URL scheme should match selected proxy type."""

    with pytest.raises(typer.BadParameter):
        resolve_proxy(
            interactive=False,
            value_type="socks5",
            value_url="http://127.0.0.1:8080",
            default_type="none",
            default_url="",
            lang=PromptLanguage.EN,
        )


def test_resolve_nginx_plan_returns_empty_fields_when_disabled() -> None:
    """Disabled nginx should leave all public endpoint fields empty."""

    plan = resolve_nginx_plan(
        nginx_enabled=False,
        runtime_port=8080,
        api_port=8081,
        runtime_host_value=None,
        api_host_value=None,
        runtime_https_value=None,
        api_https_value=None,
        certbot_email_value=None,
        runtime_host_default="",
        api_host_default="",
        runtime_https_default=False,
        api_https_default=False,
        certbot_email_default="",
        interactive=False,
        lang=PromptLanguage.EN,
    )

    assert plan.runtime_host == ""
    assert plan.api_host == ""
    assert plan.public_runtime_url == ""
    assert plan.public_chat_api_url == ""


def test_resolve_nginx_plan_rejects_same_domain_when_https_enabled() -> None:
    """HTTPS setup must use different domains for runtime and chat/api."""

    with pytest.raises(typer.BadParameter):
        resolve_nginx_plan(
            nginx_enabled=True,
            runtime_port=8080,
            api_port=8081,
            runtime_host_value="app.example.com",
            api_host_value="app.example.com",
            runtime_https_value=True,
            api_https_value=False,
            certbot_email_value="ops@example.com",
            runtime_host_default="",
            api_host_default="",
            runtime_https_default=False,
            api_https_default=False,
            certbot_email_default="",
            interactive=False,
            lang=PromptLanguage.EN,
        )


def test_resolve_nginx_plan_rejects_https_for_ip_endpoint() -> None:
    """Automatic HTTPS should be rejected for raw IP endpoints."""

    with pytest.raises(typer.BadParameter):
        resolve_nginx_plan(
            nginx_enabled=True,
            runtime_port=8080,
            api_port=8081,
            runtime_host_value="192.168.1.10",
            api_host_value="chat.example.com",
            runtime_https_value=True,
            api_https_value=False,
            certbot_email_value=None,
            runtime_host_default="",
            api_host_default="",
            runtime_https_default=False,
            api_https_default=False,
            certbot_email_default="",
            interactive=False,
            lang=PromptLanguage.EN,
        )


def test_resolve_nginx_plan_rejects_invalid_public_host() -> None:
    """Public endpoint host input must not contain path or explicit port."""

    with pytest.raises(typer.BadParameter):
        resolve_nginx_plan(
            nginx_enabled=True,
            runtime_port=8080,
            api_port=8081,
            runtime_host_value="https://app.example.com:4443/path",
            api_host_value="chat.example.com",
            runtime_https_value=False,
            api_https_value=False,
            certbot_email_value=None,
            runtime_host_default="",
            api_host_default="",
            runtime_https_default=False,
            api_https_default=False,
            certbot_email_default="",
            interactive=False,
            lang=PromptLanguage.EN,
        )


def test_resolve_nginx_plan_derives_https_public_urls() -> None:
    """Domain-backed HTTPS endpoints should derive standard 443 URLs."""

    plan = resolve_nginx_plan(
        nginx_enabled=True,
        runtime_port=8080,
        api_port=8081,
        runtime_host_value="app.example.com",
        api_host_value="chat.example.com",
        runtime_https_value=True,
        api_https_value=True,
        certbot_email_value="ops@example.com",
        runtime_host_default="",
        api_host_default="",
        runtime_https_default=False,
        api_https_default=False,
        certbot_email_default="",
        interactive=False,
        lang=PromptLanguage.EN,
    )

    assert plan.runtime_public_port == 443
    assert plan.api_public_port == 443
    assert plan.public_runtime_url == "https://app.example.com"
    assert plan.public_chat_api_url == "https://chat.example.com"


def test_resolve_nginx_plan_derives_prefixed_ports_for_ip_endpoints() -> None:
    """Raw IP endpoints must use prefixed public ports to avoid proxy self-loops."""

    plan = resolve_nginx_plan(
        nginx_enabled=True,
        runtime_port=8080,
        api_port=8081,
        runtime_host_value="213.226.126.89",
        api_host_value="213.226.126.89",
        runtime_https_value=False,
        api_https_value=False,
        certbot_email_value=None,
        runtime_host_default="",
        api_host_default="",
        runtime_https_default=False,
        api_https_default=False,
        certbot_email_default="",
        interactive=False,
        lang=PromptLanguage.EN,
    )

    assert plan.runtime_public_port == 18080
    assert plan.api_public_port == 18081
    assert plan.public_runtime_url == "http://213.226.126.89:18080"
    assert plan.public_chat_api_url == "http://213.226.126.89:18081"
