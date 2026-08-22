"""浏览器驱动 JD 导入使用的默认拒绝式 URL 校验。"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

Resolver = Callable[[str], Iterable[str]]
_METADATA_HOSTS = {"metadata.google.internal", "instance-data", "metadata"}


@dataclass(frozen=True)
class ValidatedUrl:
    url: str
    host: str
    addresses: tuple[str, ...]


def _system_resolver(host: str) -> Iterable[str]:
    return {item[4][0] for item in socket.getaddrinfo(host, None)}


class UrlPolicy:
    def __init__(
        self,
        resolver: Resolver = _system_resolver,
        *,
        resolve_hostnames: bool = True,
    ) -> None:
        self._resolver = resolver
        self._resolve_hostnames = resolve_hostnames

    def validate(self, url: str) -> ValidatedUrl:
        parsed = urlsplit(url.strip())
        if parsed.scheme.lower() not in {"http", "https"}:
            raise ValueError("url_scheme_not_allowed")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("url_credentials_not_allowed")
        if not parsed.hostname:
            raise ValueError("url_host_required")
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("url_port_invalid") from error
        if port not in (None, 80, 443):
            raise ValueError("url_port_not_allowed")

        host = parsed.hostname.rstrip(".").lower()
        if host in _METADATA_HOSTS or host.endswith(".internal"):
            raise ValueError("url_host_not_allowed")
        try:
            direct_ip = ipaddress.ip_address(host)
            addresses = (str(direct_ip),)
        except ValueError:
            if not self._resolve_hostnames:
                addresses = ()
            else:
                try:
                    addresses = tuple(dict.fromkeys(self._resolver(host)))
                except OSError as error:
                    raise ValueError("url_dns_failed") from error
        if self._resolve_hostnames and not addresses:
            raise ValueError("url_dns_failed")
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
                ip = ip.ipv4_mapped
            if not ip.is_global:
                raise ValueError("url_address_not_public")

        netloc = host if port is None else f"{host}:{port}"
        normalized = urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", parsed.query, ""))
        return ValidatedUrl(normalized, host, addresses)
