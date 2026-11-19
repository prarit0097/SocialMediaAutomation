from django.test import RequestFactory, SimpleTestCase, override_settings

from .throttle import client_ip


class ClientIPTests(SimpleTestCase):
    @override_settings(TRUST_REVERSE_PROXY=True, TRUSTED_PROXY_HOPS=2)
    def test_two_proxies_select_client_from_the_trusted_suffix(self):
        for forwarded in ("192.0.2.20, 192.0.2.30", "192.0.2.10, 192.0.2.20, 192.0.2.30"):
            with self.subTest(forwarded=forwarded):
                request = RequestFactory().get("/", HTTP_X_FORWARDED_FOR=forwarded, REMOTE_ADDR="192.0.2.40")
                self.assertEqual(client_ip(request), "192.0.2.20")

    @override_settings(TRUST_REVERSE_PROXY=True, TRUSTED_PROXY_HOPS=2)
    def test_incomplete_forwarded_chain_uses_direct_peer(self):
        request = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="192.0.2.30", REMOTE_ADDR="192.0.2.40")
        self.assertEqual(client_ip(request), "192.0.2.40")

    @override_settings(TRUST_REVERSE_PROXY=False)
    def test_untrusted_proxy_settings_uses_direct_peer(self):
        request = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="192.0.2.20, 192.0.2.30", REMOTE_ADDR="192.0.2.40")
        self.assertEqual(client_ip(request), "192.0.2.40")
