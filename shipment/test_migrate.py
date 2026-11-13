"""Tests for the before_migrate hook that keeps this app's Shipment DocTypes live (#236)."""

import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from frappe.modules.import_file import calculate_hash

from shipment import hooks
from shipment.migrate import SHADOWED_ERPNEXT_DOCTYPES, keep_app_doctypes

MODULE = "shipment.migrate"


class TestKeepAppDoctypes(unittest.TestCase):
	"""Verify ERPNext's sync is made to skip the DocTypes this app owns."""

	def setUp(self):
		self.tmp = tempfile.TemporaryDirectory()
		self.addCleanup(self.tmp.cleanup)

	def _erpnext_file(self, scrubbed, content):
		folder = os.path.join(self.tmp.name, "stock", "doctype", scrubbed)
		os.makedirs(folder)
		path = os.path.join(folder, f"{scrubbed}.json")
		with open(path, "w") as f:
			f.write(content)
		return path

	def _app_path(self, app, *parts):
		self.assertEqual(app, "erpnext")
		return os.path.join(self.tmp.name, *parts)

	def test_hook_is_registered(self):
		self.assertIn("shipment.migrate.keep_app_doctypes", hooks.before_migrate)

	def test_stores_erpnext_hash_for_each_shadowed_doctype(self):
		"""ERPNext's sync compares this hash with its file's and skips the import."""
		self.assertEqual(SHADOWED_ERPNEXT_DOCTYPES, ("Shipment", "Shipment Parcel"))
		paths = {
			"Shipment": self._erpnext_file("shipment", '{"name": "Shipment", "module": "Stock"}'),
			"Shipment Parcel": self._erpnext_file("shipment_parcel", '{"name": "Shipment Parcel"}'),
		}
		db = Mock(exists=Mock(return_value=True))
		with (
			patch(f"{MODULE}.frappe.get_installed_apps", return_value=["frappe", "erpnext", "shipment"]),
			patch(f"{MODULE}.frappe.get_app_path", side_effect=self._app_path),
			patch(f"{MODULE}.frappe.db", db),
		):
			keep_app_doctypes()

		self.assertEqual(
			[c.args for c in db.set_value.call_args_list],
			[("DocType", doctype, "migration_hash", calculate_hash(paths[doctype]))
				for doctype in ("Shipment", "Shipment Parcel")],
		)
		for call in db.set_value.call_args_list:
			self.assertIs(call.kwargs["update_modified"], False)

	def test_leaves_missing_doctypes_and_files_to_the_sync(self):
		"""A DocType not yet on the site, or a file ERPNext dropped, needs nothing from the hook."""
		self._erpnext_file("shipment", "{}")
		db = Mock(exists=Mock(return_value=False))
		with (
			patch(f"{MODULE}.frappe.get_installed_apps", return_value=["frappe", "erpnext", "shipment"]),
			patch(f"{MODULE}.frappe.get_app_path", side_effect=self._app_path),
			patch(f"{MODULE}.frappe.db", db),
		):
			keep_app_doctypes()
		db.exists.assert_called_once_with("DocType", "Shipment")
		db.set_value.assert_not_called()

	def test_does_nothing_without_erpnext(self):
		db = Mock()
		with (
			patch(f"{MODULE}.frappe.get_installed_apps", return_value=["frappe", "shipment"]),
			patch(f"{MODULE}.frappe.db", db),
		):
			keep_app_doctypes()
		db.set_value.assert_not_called()
		db.exists.assert_not_called()


if __name__ == "__main__":
	unittest.main()
