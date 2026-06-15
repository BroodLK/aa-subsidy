import json
from pathlib import Path
from unittest.mock import Mock, patch

from django.core.exceptions import FieldDoesNotExist
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

    def test_bundled_spec_sets_extensions_on_all_operations(self):
        with Path(tasks.ESI_OPENAPI_SPEC_FILE).open(encoding="utf-8") as fp:
            spec = json.load(fp)

        for path_item in spec["paths"].values():
            for operation in path_item.values():
                self.assertTrue(operation.get("x-aasubsidy-operation"))


class TestOptionalModelFieldSave(SimpleTestCase):
    def test_saves_when_field_is_concrete(self):
        field = Mock(concrete=True, many_to_many=False, primary_key=False)
        audit = Mock()
        audit._meta.get_field.return_value = field

        result = tasks._save_optional_model_field(
            audit,
            "last_update_contracts",
            "2026-06-15T12:00:00Z",
        )

        self.assertTrue(result)
        self.assertEqual(audit.last_update_contracts, "2026-06-15T12:00:00Z")
        audit.save.assert_called_once_with(update_fields=["last_update_contracts"])

    def test_skips_when_field_is_missing(self):
        audit = Mock()
        audit._meta.get_field.side_effect = FieldDoesNotExist("last_update_contracts")

        result = tasks._save_optional_model_field(
            audit,
            "last_update_contracts",
            "2026-06-15T12:00:00Z",
        )

        self.assertFalse(result)
        audit.save.assert_not_called()
