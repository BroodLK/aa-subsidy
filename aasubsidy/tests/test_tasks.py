from unittest.mock import patch

from django.test import SimpleTestCase

from aasubsidy import tasks


class TestEsiClientBootstrap(SimpleTestCase):
    def tearDown(self):
        tasks._esi_contract_client.cache_clear()
        tasks._esi_universe_client.cache_clear()
        super().tearDown()

    @patch.object(tasks.app_settings, "SUBSIDY_ESI_COMPATIBILITY_DATE", "2025-08-26")
    @patch("aasubsidy.tasks.ESIClientProvider")
    def test_contract_client_uses_bundled_spec(self, provider_cls):
        provider_cls.return_value.client = object()

        client = tasks._esi_contract_client()

        self.assertIs(client, provider_cls.return_value.client)
        provider_cls.assert_called_once_with(
            compatibility_date="2025-08-26",
            ua_appname=tasks.__title__,
            ua_version=tasks.__version__,
            spec_file=str(tasks.ESI_OPENAPI_SPEC_FILE),
            tags=["Contracts"],
        )

    @patch.object(tasks.app_settings, "SUBSIDY_ESI_COMPATIBILITY_DATE", "2025-08-26")
    @patch("aasubsidy.tasks.ESIClientProvider")
    def test_universe_client_uses_bundled_spec(self, provider_cls):
        provider_cls.return_value.client = object()

        client = tasks._esi_universe_client()

        self.assertIs(client, provider_cls.return_value.client)
        provider_cls.assert_called_once_with(
            compatibility_date="2025-08-26",
            ua_appname=tasks.__title__,
            ua_version=tasks.__version__,
            spec_file=str(tasks.ESI_OPENAPI_SPEC_FILE),
            tags=["Universe"],
        )
