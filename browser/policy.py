"""Shared HTTPS egress policy for the browser and its CONNECT proxy."""
from __future__ import annotations

import posixpath
from urllib.parse import unquote, urlsplit

FACEBOOK_DOMAINS = ("facebook.com", "facebook.net", "fbcdn.net", "fbsbx.com", "messenger.com")
# Google's documented reCAPTCHA scripts, frames, API and alternate endpoint:
# https://docs.cloud.google.com/recaptcha/docs/faq
RECAPTCHA_HOSTS = frozenset({"www.google.com", "recaptcha.google.com", "www.gstatic.com", "www.recaptcha.net"})
# Permit only the specific Arkose client API, challenge frame and CDN hosts
# used by this integration; do not open the entire provider domain family.
# https://developer.arkoselabs.com/docs/domain-policy
ARKOSE_HOSTS = frozenset({"meta-api.arkoselabs.com", "client-api.arkoselabs.com", "iframe.arkoselabs.com", "cdn.arkoselabs.com"})


def facebook_host(host):
    if not isinstance(host, str):
        return False
    host = host.lower().rstrip(".")
    return any(host == domain or host.endswith("." + domain) for domain in FACEBOOK_DOMAINS)


def allowed_host(host):
    """CONNECT sees a hostname, never the encrypted request path."""
    return isinstance(host, str) and (facebook_host(host) or arkose_host(host) or host.lower().rstrip(".") in RECAPTCHA_HOSTS)


def arkose_host(host):
    return isinstance(host, str) and host.lower().rstrip(".") in ARKOSE_HOSTS


def https_url(url):
    if not isinstance(url, str):
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme == "https" and parsed.port in {None, 443} and parsed.hostname and not parsed.username and not parsed.password:
            return parsed
    except (TypeError, ValueError):
        pass
    return None


def facebook_url(url):
    """Only Facebook URLs can be used to confirm the captured session."""
    parsed = https_url(url)
    return parsed is not None and facebook_host(parsed.hostname)


def allowed_url(url):
    parsed = https_url(url)
    if parsed is None:
        return False
    if facebook_host(parsed.hostname) or arkose_host(parsed.hostname):
        return True
    # The browser can restrict paths before TLS; the host proxy also checks
    # these exact hosts and pins them to verified public DNS addresses.
    path = posixpath.normpath(unquote(parsed.path).replace("\\", "/"))
    return parsed.hostname.lower().rstrip(".") in RECAPTCHA_HOSTS and path.startswith("/recaptcha/")
