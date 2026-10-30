from __future__ import annotations

import ipaddress
from typing import Optional
from urllib.parse import urlparse

_LAN_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


def _is_lan_host(hostname: str) -> bool:
    if hostname == "localhost":
        return True
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return any(addr in net for net in _LAN_NETWORKS)


def is_origin_allowed(origin: Optional[str], allowed_origins: list[str]) -> bool:
    if not origin:
        return True

    if origin in allowed_origins:
        return True

    hostname = urlparse(origin).hostname
    if hostname is None:
        return False
    return _is_lan_host(hostname)
