"""Network and runtime-side prompt resolvers for setup CLI."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import re
import typer
from urllib.parse import quote, urlsplit

from afkbot.cli.presentation.setup_prompts import (
    HTTP_PROXY_TYPE,
    PromptLanguage,
    PROXY_TYPE_CHOICES,
    SOCKS5_PROXY_TYPE,
    SOCKS5H_PROXY_TYPE,
    prompt_certbot_email,
    msg,
    prompt_nginx_enabled,
    prompt_nginx_https_enabled,
    prompt_nginx_public_host,
    prompt_proxy_config,
)

_SCHEME_BY_TYPE = {
    HTTP_PROXY_TYPE: (HTTP_PROXY_TYPE, "https"),
    SOCKS5_PROXY_TYPE: (SOCKS5_PROXY_TYPE,),
    SOCKS5H_PROXY_TYPE: (SOCKS5H_PROXY_TYPE,),
}
_HOST_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


@dataclass(slots=True, frozen=True)
class ResolvedNginxPlan:
    """Derived nginx/public endpoint settings resolved during install."""

    runtime_host: str
    runtime_public_port: int | None
    runtime_https: bool
    api_host: str
    api_public_port: int | None
    api_https: bool
    certbot_email: str
    public_runtime_url: str
    public_chat_api_url: str


def resolve_proxy(
    *,
    interactive: bool,
    value_type: str | None,
    value_url: str | None,
    default_type: str,
    default_url: str,
    lang: PromptLanguage,
) -> tuple[str, str]:
    """Resolve provider proxy settings from flags or interactive selection."""

    proxy_url = ""
    if value_type is not None:
        proxy_type = value_type.strip().lower()
    elif interactive:
        proxy_type, proxy_url = prompt_proxy_config(
            default_type=default_type,
            default_url=default_url,
            lang=lang,
        )
    else:
        proxy_type = default_type.strip().lower()

    if proxy_type not in PROXY_TYPE_CHOICES:
        raise typer.BadParameter(
            msg(
                lang,
                en="llm proxy type must be one of: none, http, socks5, socks5h",
                ru="тип LLM-прокси должен быть одним из: none, http, socks5, socks5h",
            )
        )
    if proxy_type == "none":
        return "none", ""

    if not proxy_url:
        proxy_url = value_url.strip() if value_url is not None else default_url.strip()
    if not proxy_url:
        raise typer.BadParameter(
            msg(
                lang,
                en="llm proxy URL is required when proxy is enabled",
                ru="URL LLM-прокси обязателен, когда прокси включён",
            )
        )
    return proxy_type, _normalize_proxy_url(proxy_type=proxy_type, raw_url=proxy_url, lang=lang)


def _normalize_proxy_url(
    *,
    proxy_type: str,
    raw_url: str,
    lang: PromptLanguage,
) -> str:
    """Normalize proxy URL, supporting shorthand host:port[:user:pass] syntax."""

    candidate = raw_url.strip()
    if not candidate:
        raise typer.BadParameter(
            msg(
                lang,
                en="LLM proxy URL cannot be empty",
                ru="URL LLM-прокси не может быть пустым",
            )
        )

    lowered = candidate.lower()
    allowed_schemes = _SCHEME_BY_TYPE[proxy_type]
    if "://" in lowered:
        scheme = urlsplit(candidate).scheme.lower()
        if scheme in allowed_schemes:
            return candidate
        expected = ", ".join(f"{name}://" for name in allowed_schemes)
        raise typer.BadParameter(
            msg(
                lang,
                en=f"Proxy URL scheme mismatch for type={proxy_type}. Expected: {expected}",
                ru=f"Схема URL прокси не соответствует type={proxy_type}. Ожидается: {expected}",
            )
        )

    shorthand = candidate.split(":")
    if len(shorthand) not in {2, 4}:
        raise typer.BadParameter(
            msg(
                lang,
                en=(
                    "Unsupported proxy shorthand. Use host:port or host:port:user:pass, "
                    "or full URL with scheme."
                ),
                ru=(
                    "Неподдерживаемый сокращённый формат прокси. Используйте host:port "
                    "или host:port:user:pass, либо полный URL со схемой."
                ),
            )
        )

    host = shorthand[0].strip()
    port = shorthand[1].strip()
    if not host:
        raise typer.BadParameter(msg(lang, en="Proxy host cannot be empty", ru="Хост прокси не может быть пустым"))
    if not port.isdigit() or not (1 <= int(port) <= 65535):
        raise typer.BadParameter(
            msg(
                lang,
                en="Proxy port must be an integer in range 1..65535",
                ru="Порт прокси должен быть целым числом в диапазоне 1..65535",
            )
        )

    scheme = allowed_schemes[0]
    if len(shorthand) == 2:
        return f"{scheme}://{host}:{port}"

    username = quote(shorthand[2], safe="")
    password = quote(shorthand[3], safe="")
    return f"{scheme}://{username}:{password}@{host}:{port}"


def resolve_port(
    *,
    value: int | None,
    interactive: bool,
    prompt: str,
    default: int,
    lang: PromptLanguage,
) -> int:
    """Resolve one TCP port from flags, prompt, or defaults."""

    if value is not None:
        port = value
    elif interactive:
        raw = typer.prompt(prompt, default=str(default)).strip()
        try:
            port = int(raw)
        except ValueError as exc:
            raise typer.BadParameter(
                msg(
                    lang,
                    en=f"{prompt} must be an integer",
                    ru=f"{prompt} должен быть целым числом",
                )
            ) from exc
    else:
        port = default
    if not (1 <= port <= 65535):
        raise typer.BadParameter(
            msg(
                lang,
                en=f"{prompt} must be in range 1..65535",
                ru=f"{prompt} должен быть в диапазоне 1..65535",
            )
        )
    return port


def resolve_nginx_enabled(
    *,
    value: bool | None,
    interactive: bool,
    default: bool,
    lang: PromptLanguage,
) -> bool:
    """Resolve nginx sidecar enable flag."""

    if value is not None:
        return value
    if interactive:
        return prompt_nginx_enabled(default=default, lang=lang)
    return default


def resolve_nginx_plan(
    *,
    nginx_enabled: bool,
    runtime_port: int,
    api_port: int,
    runtime_host_value: str | None,
    api_host_value: str | None,
    runtime_https_value: bool | None,
    api_https_value: bool | None,
    certbot_email_value: str | None,
    runtime_host_default: str,
    api_host_default: str,
    runtime_https_default: bool,
    api_https_default: bool,
    certbot_email_default: str,
    interactive: bool,
    lang: PromptLanguage,
) -> ResolvedNginxPlan:
    """Resolve full nginx/public endpoint plan for runtime and chat/api."""

    if not nginx_enabled:
        return ResolvedNginxPlan(
            runtime_host="",
            runtime_public_port=None,
            runtime_https=False,
            api_host="",
            api_public_port=None,
            api_https=False,
            certbot_email="",
            public_runtime_url="",
            public_chat_api_url="",
        )

    runtime_host = resolve_nginx_public_host(
        value=runtime_host_value,
        interactive=interactive,
        default=runtime_host_default,
        endpoint_label=msg(lang, en="runtime", ru="runtime"),
        internal_port=runtime_port,
        lang=lang,
    )
    api_host = resolve_nginx_public_host(
        value=api_host_value,
        interactive=interactive,
        default=api_host_default,
        endpoint_label=msg(lang, en="chat/api/ws", ru="chat/api/ws"),
        internal_port=api_port,
        lang=lang,
    )

    same_host = runtime_host.lower() == api_host.lower()
    runtime_is_ip = _is_ip_address(runtime_host)

    runtime_https = resolve_nginx_https_enabled(
        value=runtime_https_value,
        interactive=interactive,
        default=runtime_https_default,
        host=runtime_host,
        endpoint_label=msg(lang, en="runtime", ru="runtime"),
        lang=lang,
    )
    api_https = resolve_nginx_https_enabled(
        value=api_https_value,
        interactive=interactive,
        default=api_https_default,
        host=api_host,
        endpoint_label=msg(lang, en="chat/api/ws", ru="chat/api/ws"),
        lang=lang,
    )

    if same_host and not runtime_is_ip and (runtime_https or api_https):
        raise typer.BadParameter(
            msg(
                lang,
                en=(
                    "Runtime and chat/api cannot share the same domain when HTTPS is enabled. "
                    "Use different domains/subdomains."
                ),
                ru=(
                    "Runtime и chat/api не могут использовать один и тот же домен при включённом HTTPS. "
                    "Используйте разные домены или поддомены."
                ),
            )
        )

    certbot_email = ""
    if runtime_https or api_https:
        certbot_email = resolve_certbot_contact_email(
            value=certbot_email_value,
            interactive=interactive,
            default=certbot_email_default,
            lang=lang,
        )

    runtime_public_port = _derive_public_port(
        host=runtime_host,
        https_enabled=runtime_https,
        fallback_port=runtime_port,
        same_host=same_host,
    )
    api_public_port = _derive_public_port(
        host=api_host,
        https_enabled=api_https,
        fallback_port=api_port,
        same_host=same_host,
    )
    public_runtime_url = _build_public_url(
        host=runtime_host,
        https_enabled=runtime_https,
        port=runtime_public_port,
    )
    public_chat_api_url = _build_public_url(
        host=api_host,
        https_enabled=api_https,
        port=api_public_port,
    )

    return ResolvedNginxPlan(
        runtime_host=runtime_host,
        runtime_public_port=runtime_public_port,
        runtime_https=runtime_https,
        api_host=api_host,
        api_public_port=api_public_port,
        api_https=api_https,
        certbot_email=certbot_email,
        public_runtime_url=public_runtime_url,
        public_chat_api_url=public_chat_api_url,
    )


def resolve_nginx_public_host(
    *,
    value: str | None,
    interactive: bool,
    default: str,
    endpoint_label: str,
    internal_port: int,
    lang: PromptLanguage,
) -> str:
    """Resolve public host or IP for one nginx-backed endpoint."""

    if value is not None:
        raw = value
    elif interactive:
        raw = prompt_nginx_public_host(
            endpoint_label=endpoint_label,
            internal_port=internal_port,
            default=default,
            lang=lang,
        )
    else:
        raw = default
    return _normalize_public_host(raw=raw, lang=lang)


def resolve_nginx_https_enabled(
    *,
    value: bool | None,
    interactive: bool,
    default: bool,
    host: str,
    endpoint_label: str,
    lang: PromptLanguage,
) -> bool:
    """Resolve HTTPS flag for one public nginx endpoint."""

    if _is_ip_address(host):
        if value is True:
            raise typer.BadParameter(
                msg(
                    lang,
                    en=f"HTTPS cannot be enabled automatically for IP endpoint: {host}",
                    ru=f"Нельзя автоматически включить HTTPS для IP-адреса: {host}",
                )
            )
        return False
    if value is not None:
        return value
    if interactive:
        return prompt_nginx_https_enabled(
            endpoint_label=endpoint_label,
            host=host,
            default=default,
            lang=lang,
        )
    return default


def resolve_certbot_contact_email(
    *,
    value: str | None,
    interactive: bool,
    default: str,
    lang: PromptLanguage,
) -> str:
    """Resolve Certbot contact email when HTTPS is requested."""

    if value is not None:
        email = value.strip()
    elif interactive:
        email = prompt_certbot_email(default=default, lang=lang).strip()
    else:
        email = default.strip()
    if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        raise typer.BadParameter(
            msg(
                lang,
                en="Valid certbot email is required when HTTPS is enabled",
                ru="Нужен корректный email для Certbot, когда включён HTTPS",
            )
        )
    return email
def _normalize_public_host(*, raw: str, lang: PromptLanguage) -> str:
    """Normalize public endpoint host/domain input."""

    candidate = raw.strip()
    if not candidate:
        raise typer.BadParameter(
            msg(
                lang,
                en="Public domain or IP cannot be empty",
                ru="Публичный домен или IP не может быть пустым",
            )
        )
    if "://" in candidate:
        parsed = urlsplit(candidate)
        if not parsed.hostname:
            raise typer.BadParameter(
                msg(
                    lang,
                    en="Public domain or IP host is missing",
                    ru="Не указан хост для публичного домена или IP",
                )
            )
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise typer.BadParameter(
                msg(
                    lang,
                    en="Do not include path/query in public domain or IP",
                    ru="Не указывайте path/query в публичном домене или IP",
                )
            )
        if parsed.port is not None:
            raise typer.BadParameter(
                msg(
                    lang,
                    en="Do not include port in public domain or IP; install derives ports automatically",
                    ru="Не указывайте порт в публичном домене или IP; установка определяет порты автоматически",
                )
            )
        candidate = parsed.hostname
    if "/" in candidate:
        raise typer.BadParameter(
            msg(
                lang,
                en="Public domain or IP must not contain path separators",
                ru="Публичный домен или IP не должен содержать разделители пути",
            )
        )
    candidate = candidate.strip().rstrip(".").lower()
    if _is_ip_address(candidate):
        return candidate
    labels = candidate.split(".")
    if not all(_HOST_LABEL_RE.fullmatch(label or "") for label in labels):
        raise typer.BadParameter(
            msg(
                lang,
                en="Public host must be a valid domain or IPv4 address",
                ru="Публичный хост должен быть корректным доменом или IPv4-адресом",
            )
        )
    return candidate


def _is_ip_address(value: str) -> bool:
    """Return whether value is a valid IP address."""

    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _derive_public_port(
    *,
    host: str,
    https_enabled: bool,
    fallback_port: int,
    same_host: bool,
) -> int:
    """Derive public listener port for one endpoint."""

    if _is_ip_address(host):
        derived_port = fallback_port + 10000
        if not (1 <= derived_port <= 65535):
            raise typer.BadParameter(
                f"Derived nginx public port is out of range for IP endpoint: {derived_port}"
            )
        return derived_port
    if same_host:
        return fallback_port
    return 443 if https_enabled else 80


def _build_public_url(*, host: str, https_enabled: bool, port: int) -> str:
    """Build normalized public URL from resolved endpoint host and listener port."""

    scheme = "https" if https_enabled else "http"
    default_port = 443 if https_enabled else 80
    if port == default_port:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"
