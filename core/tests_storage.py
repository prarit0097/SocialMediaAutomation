"""Static files storage behaviour that production caching depends on."""

import json
import shutil
import tempfile
from pathlib import Path

from django.contrib.staticfiles.storage import ManifestStaticFilesStorage
from django.test import SimpleTestCase, override_settings

from core.storage import ResilientManifestStaticFilesStorage


class ResilientManifestStorageTests(SimpleTestCase):
    """Nginx serves /static/ with `expires 30d`, so filenames must be content-hashed for a
    deploy to reach returning visitors. The storage must do that when a manifest exists and
    stay serveable (never raise) when one does not."""

    def setUp(self):
        self.static_root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.static_root, ignore_errors=True)
        (self.static_root / "dashboard").mkdir(parents=True, exist_ok=True)
        (self.static_root / "dashboard" / "app.js").write_text("console.log(1);", encoding="utf-8")

    def _write_manifest(self, paths):
        (self.static_root / "staticfiles.json").write_text(
            json.dumps({"version": "1.1", "paths": paths}), encoding="utf-8"
        )

    def test_serves_hashed_name_when_manifest_has_the_entry(self):
        self._write_manifest({"dashboard/app.js": "dashboard/app.deadbeef1234.js"})
        with override_settings(STATIC_ROOT=self.static_root, DEBUG=False):
            url = ResilientManifestStaticFilesStorage().url("dashboard/app.js")
        self.assertEqual(url, "/static/dashboard/app.deadbeef1234.js")

    def test_falls_back_to_unhashed_name_when_manifest_is_missing(self):
        """collectstatic has not run: a fresh checkout running tests, or the window before
        collectstatic finishes on deploy. Must serve the plain URL, not raise."""
        with override_settings(STATIC_ROOT=self.static_root, DEBUG=False):
            url = ResilientManifestStaticFilesStorage().url("dashboard/app.js")
        self.assertEqual(url, "/static/dashboard/app.js")

    def test_falls_back_when_manifest_exists_but_lacks_the_entry(self):
        self._write_manifest({"dashboard/other.js": "dashboard/other.abc123.js"})
        with override_settings(STATIC_ROOT=self.static_root, DEBUG=False):
            url = ResilientManifestStaticFilesStorage().url("dashboard/app.js")
        self.assertEqual(url, "/static/dashboard/app.js")

    def test_stock_storage_raises_without_a_manifest(self):
        """Guards the reason this subclass exists: the stock storage turns a missing
        manifest entry into a hard failure of every page that renders {% static %}."""
        with override_settings(STATIC_ROOT=self.static_root, DEBUG=False):
            with self.assertRaises(ValueError):
                ManifestStaticFilesStorage().url("dashboard/app.js")


class StaticStorageConfigurationTests(SimpleTestCase):
    def test_production_static_storage_is_hash_busting(self):
        from django.conf import settings

        self.assertEqual(
            settings.STORAGES["staticfiles"]["BACKEND"],
            "core.storage.ResilientManifestStaticFilesStorage",
        )
