"""Verification dependencies load without opening unrelated egress or capture."""
import socket
import unittest
from unittest.mock import patch

from browser.policy import RECAPTCHA_HOSTS, allowed_host, allowed_url, facebook_url
from dashboard.browser_proxy import public_addresses


class BrowserPolicyTests(unittest.TestCase):
    def test_documented_verification_scripts_frames_and_payloads_are_allowed(self):
        for url in (
            "https://www.google.com/recaptcha/enterprise.js?render=synthetic",
            "https://www.google.com/recaptcha/api2/anchor?synthetic=1",
            "https://www.google.com/recaptcha/enterprise/reload",
            "https://www.google.com/recaptcha/api2/payload",
            "https://recaptcha.google.com/recaptcha/enterprise/bframe",
            "https://www.gstatic.com/recaptcha/releases/synthetic/recaptcha__en.js",
            "https://www.recaptcha.net/recaptcha/enterprise.js",
            "https://www.recaptcha.net/recaptcha/api2/anchor",
        ):
            with self.subTest(url=url):
                self.assertTrue(allowed_url(url))

    def test_proxy_and_browser_share_exact_third_party_hosts(self):
        for host in RECAPTCHA_HOSTS:
            with self.subTest(host=host):
                self.assertTrue(allowed_host(host))
                self.assertTrue(allowed_url("https://" + host + "/recaptcha/api.js"))
        for host in ("google.com", "accounts.google.com", "evil.www.google.com", "www.google.com.evil.example", "unlisted.gstatic.com", "recaptcha.net", "evilrecaptcha.net", "127.0.0.1", "169.254.169.254"):
            with self.subTest(host=host):
                self.assertFalse(allowed_host(host))
                self.assertFalse(allowed_url("https://" + host + "/recaptcha/api.js"))

    def test_third_party_requests_are_restricted_to_recaptcha_paths(self):
        for path in ("/", "/search?q=synthetic", "/accounts", "/unrelated.js", "/recaptcha-evil/api.js", "/recaptcha/../search", "/recaptcha/%2e%2e/search", "/recaptcha/%2e%2e%5csearch"):
            for host in RECAPTCHA_HOSTS:
                with self.subTest(host=host, path=path):
                    self.assertFalse(allowed_url("https://" + host + path))

    def test_verification_resources_cannot_confirm_a_facebook_session(self):
        for host in RECAPTCHA_HOSTS:
            self.assertFalse(facebook_url("https://" + host + "/recaptcha/api.js"))
        self.assertTrue(facebook_url("https://www.facebook.com/"))
        self.assertTrue(facebook_url("https://m.facebook.com/"))

    def test_https_port_and_credential_restrictions_apply_to_all_hosts(self):
        for host in ("www.facebook.com", "www.google.com", "www.gstatic.com"):
            for url in ("http://" + host + "/recaptcha/api.js", "https://" + host + ":8443/recaptcha/api.js", "https://user:secret@" + host + "/recaptcha/api.js", "https://" + host + ":invalid/recaptcha/api.js"):
                with self.subTest(url=url):
                    self.assertFalse(allowed_url(url))
                    self.assertFalse(facebook_url(url))
        for url in (None, {}, "https://[", "file:///etc/passwd"):
            self.assertFalse(allowed_url(url))

    def test_captcha_dns_cannot_resolve_to_private_or_metadata_addresses(self):
        for host in RECAPTCHA_HOSTS:
            for address in ("127.0.0.1", "169.254.169.254", "10.0.0.1", "::1", "fd00::1"):
                with patch("dashboard.browser_proxy.socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", (address, 443))]):
                    with self.assertRaises(ValueError):
                        public_addresses(host)


if __name__ == "__main__":
    unittest.main()
