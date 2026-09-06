"""SSRF 安全 HTTP 客户端。

为 ``fetch_url`` 等联网工具提供 SSRF 防护能力：

1. 只允许 HTTP/HTTPS 协议；
2. 请求前解析域名并拒绝内网、本机、链路本地和保留地址；
3. 每次重定向前重新校验目标 URL；
4. 通过 DNS pin 固定已校验的解析结果，降低 DNS rebinding 风险。

本模块不直接暴露给模型调用，而是作为工具层的网络访问基础设施。
"""

from __future__ import annotations

import contextlib
import ipaddress
import socket
import threading
from collections.abc import Iterator
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from urllib3.util import connection as urllib3_connection

MAX_REDIRECTS = 5

# 线程本地状态：不同 Agent 请求可能并发执行，DNS pin 不能跨线程共享。
_pin_state = threading.local()
# 安装 urllib3 create_connection monkey patch 时使用全局锁，避免并发安装/卸载竞争。
_install_lock = threading.Lock()
# 当前正在使用 monkey patch 的上下文数量；计数归零时恢复原始函数。
_install_count = 0
# 保存 urllib3 原始 create_connection，便于最后恢复。
_original_create_connection = None


def _get_pin_stack() -> list[dict[str, list]]:
    """获取当前线程的 DNS pin 栈。

    每个线程独立维护一组 pin，避免并发请求之间互相污染。
    """

    stack = getattr(_pin_state, "stack", None)
    if stack is None:
        stack = []
        _pin_state.stack = stack
    return stack


def _pinned_create_connection(
    address,
    timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
    source_address=None,
    socket_options=None,
):
    """让 urllib3 连接时使用已校验过的 DNS 结果。

    requests 底层通过 urllib3 建立 socket 连接。这里临时替换 urllib3 的连接函数，
    在存在 DNS pin 时使用预先校验过的 IP 地址连接，避免校验后再次解析 DNS 得到不同地址。
    """

    host, port = address
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]

    stack = _get_pin_stack()
    pins = stack[-1] if stack else None
    pinned = pins.get(host) if pins else None

    if pinned is None:
        return _original_create_connection(
            address,
            timeout,
            source_address=source_address,
            socket_options=socket_options,
        )

    last_error = None
    for family, socktype, proto, _canonname, sockaddr in pinned:
        if family == socket.AF_INET:
            target = (sockaddr[0], port)
        elif family == socket.AF_INET6:
            target = (sockaddr[0], port, *sockaddr[2:])
        else:
            continue

        sock = None
        try:
            sock = socket.socket(family, socktype, proto)
            for opt in socket_options or ():
                sock.setsockopt(*opt)
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(target)
            return sock
        except OSError as exc:
            last_error = exc
            if sock is not None:
                sock.close()

    if last_error is not None:
        raise last_error
    raise OSError("DNS pin 没有可用地址")


@contextlib.contextmanager
def _pin_dns(hostname: str, addr_infos: list) -> Iterator[None]:
    """在当前线程内临时固定 hostname 的 DNS 解析结果。

    进入时安装 urllib3 连接函数替换并压栈 pin；退出时弹出当前 pin；
    最后一个使用者退出后恢复 urllib3 原始连接函数。
    """

    global _install_count, _original_create_connection

    with _install_lock:
        if _install_count == 0:
            _original_create_connection = urllib3_connection.create_connection
            urllib3_connection.create_connection = _pinned_create_connection
        _install_count += 1

    stack = _get_pin_stack()
    pins = dict(stack[-1]) if stack else {}
    pins[hostname] = addr_infos
    stack.append(pins)
    try:
        yield
    finally:
        stack.pop()
        with _install_lock:
            _install_count -= 1
            if _install_count == 0 and _original_create_connection is not None:
                urllib3_connection.create_connection = _original_create_connection
                _original_create_connection = None


def _resolve_and_validate(url: str) -> tuple[bool, str, str | None, list | None]:
    """解析 URL 并确认目标不是内网、本机或保留地址。

    Returns:
        ``(is_safe, reason, hostname, addr_infos)``：
        - ``is_safe`` 为 False 时，``reason`` 表示拒绝原因；
        - ``is_safe`` 为 True 时，``hostname`` 和 ``addr_infos`` 可用于后续 DNS pin。
    """

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False, f"不支持的 URL 协议：{parsed.scheme or '<empty>'}", None, None
    if not parsed.hostname:
        return False, "URL 中没有可解析的 hostname", None, None

    try:
        addr_infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        return False, f"无法解析 hostname：{parsed.hostname}", parsed.hostname, None
    if not addr_infos:
        return False, f"无法解析 hostname：{parsed.hostname}", parsed.hostname, None

    for addr_info in addr_infos:
        ip_text = addr_info[4][0]
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError:
            return False, f"无法识别解析地址：{ip_text}", parsed.hostname, None
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified:
            return False, f"URL 解析到被禁止访问的地址：{ip_text}", parsed.hostname, None

    return True, "", parsed.hostname, addr_infos


def blocked_response(url: str, reason: str) -> dict[str, Any]:
    """生成统一的阻断响应。

    返回结构与成功请求结果保持相近字段，调用方可以统一处理 ok/error/status_code。
    """

    return {
        "ok": False,
        "status_code": 0,
        "headers": {},
        "content": "",
        "url": url,
        "error": f"请求被安全策略拦截：{reason}",
    }


def request_with_safe_redirects(
    method: str,
    url: str,
    *,
    timeout: int,
    **kwargs: Any,
) -> tuple[requests.Response | None, dict[str, Any] | None]:
    """发起安全 HTTP 请求，并在每次重定向前重新校验目标地址。

    Args:
        method: HTTP 方法，例如 GET、POST。
        url: 初始 URL。
        timeout: requests 超时时间（秒）。
        **kwargs: 透传给 ``requests.request`` 的其他参数，例如 headers、data、json。

    Returns:
        ``(response, blocked)``：
        - 请求成功时 ``response`` 为 ``requests.Response``，``blocked`` 为 None；
        - 被安全策略拦截时 ``response`` 为 None，``blocked`` 为统一错误字典。
    """

    current_method = method.upper()
    current_url = url
    request_kwargs = dict(kwargs)

    for redirect_count in range(MAX_REDIRECTS + 1):
        is_safe, reason, hostname, addr_infos = _resolve_and_validate(current_url)
        if not is_safe or hostname is None or addr_infos is None:
            return None, blocked_response(current_url, reason)

        with _pin_dns(hostname, addr_infos):
            response = requests.request(
                current_method,
                current_url,
                timeout=timeout,
                allow_redirects=False,
                **request_kwargs,
            )

        if not response.is_redirect and not response.is_permanent_redirect:
            return response, None

        location = response.headers.get("Location")
        if not location:
            return response, None
        if redirect_count == MAX_REDIRECTS:
            return None, blocked_response(current_url, "重定向次数过多")

        current_url = urljoin(str(response.url), location)
        if response.status_code == requests.codes.see_other or (
            response.status_code in {requests.codes.moved, requests.codes.found}
            and current_method not in {"GET", "HEAD"}
        ):
            current_method = "GET"
            request_kwargs.pop("data", None)
            request_kwargs.pop("json", None)

    return None, blocked_response(current_url, "重定向次数过多")
