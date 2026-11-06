"""CONNECT-only egress over a Unix socket. Browser containers have no IP network."""
from __future__ import annotations

import ipaddress
import select
import socket
import socketserver
import threading


def allowed_host(host):
    host = host.lower().rstrip(".")
    return any(host == domain or host.endswith("." + domain) for domain in (
        "facebook.com", "facebook.net", "fbcdn.net", "fbsbx.com", "messenger.com",
    ))


def public_addresses(host):
    addresses = []
    for family, _, _, _, address in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM):
        ip = ipaddress.ip_address(address[0])
        if not ip.is_global or ip.is_multicast:
            raise ValueError("Non-public egress address.")
        addresses.append((family, address))
    if not addresses:
        raise ValueError("No public egress address.")
    return addresses


class Proxy(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(*args, **kwargs)

    def verify_request(self, request, client_address):
        return self.slots.acquire(blocking=False)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class Connect(socketserver.BaseRequestHandler):
    def handle(self):
        upstream = None
        try:
            self.request.settimeout(10)
            header = b""
            while not header.endswith(b"\r\n\r\n") and len(header) < 8192:
                block = self.request.recv(1)
                if not block:
                    return
                header += block
            first = header.split(b"\r\n", 1)[0].decode("ascii")
            method, destination, version = first.split(" ")
            host, port = destination.rsplit(":", 1)
            if not header.endswith(b"\r\n\r\n") or method != "CONNECT" or port != "443" or version not in {"HTTP/1.0", "HTTP/1.1"} or len(host) > 253 or not allowed_host(host):
                raise ValueError
            for family, address in public_addresses(host):
                candidate = socket.socket(family, socket.SOCK_STREAM)
                candidate.settimeout(10)
                try:
                    candidate.connect(address)  # Connect to the checked IP, never resolve again.
                    upstream = candidate
                    break
                except OSError:
                    candidate.close()
            if upstream is None:
                raise OSError
            self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            upstream.settimeout(30)
            self.request.settimeout(30)
            while True:
                ready, _, _ = select.select([self.request, upstream], [], [], 60)
                if not ready:
                    break
                for source in ready:
                    block = source.recv(65536)
                    if not block:
                        return
                    (upstream if source is self.request else self.request).sendall(block)
        except Exception:
            if upstream is None:
                try:
                    self.request.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                except OSError:
                    pass
        finally:
            if upstream is not None:
                upstream.close()
