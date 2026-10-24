# -*- coding: utf-8 -*-
# Copyright (c) 2020, Newmatik and Contributors
# See license.txt
from __future__ import unicode_literals

import json
import unittest
from pathlib import Path


DOCTYPE_PATH = Path(__file__).with_name("shipment_service_provider.json")


class TestShipmentServiceProvider(unittest.TestCase):
	"""Verify the Shipment Service Provider security contract."""

	def setUp(self):
		"""Load the durable DocType definition."""
		self.doctype = json.loads(DOCTYPE_PATH.read_text())

	def test_restricts_provider_credentials_to_system_managers(self):
		"""Keep carrier credentials outside Stock User read and export access."""
		fields = {field["fieldname"]: field for field in self.doctype["fields"]}

		self.assertEqual(fields["api_key"]["permlevel"], 1)
		self.assertEqual(fields["api_password"]["permlevel"], 1)

		level_one_readers = {
			permission["role"]
			for permission in self.doctype["permissions"]
			if permission.get("permlevel") == 1 and permission.get("read")
		}
		self.assertEqual(level_one_readers, {"System Manager"})
		system_manager_permission = next(
			permission
			for permission in self.doctype["permissions"]
			if permission.get("permlevel") == 1 and permission["role"] == "System Manager"
		)
		self.assertEqual(system_manager_permission.get("write"), 1)

	def test_dhl_secrets_are_password_fields_for_system_managers(self):
		"""Store the GKP password and API secret encrypted and outside Stock User access."""
		fields = {field["fieldname"]: field for field in self.doctype["fields"]}
		for fieldname in ("dhl_gkp_password", "dhl_api_secret"):
			with self.subTest(fieldname=fieldname):
				self.assertEqual(fields[fieldname]["fieldtype"], "Password")
				self.assertEqual(fields[fieldname]["permlevel"], 1)
		self.assertEqual(fields["dhl_gkp_username"]["permlevel"], 1)

	def test_dhl_defaults_are_safe(self):
		"""Start in the sandbox and share no recipient contact data without an explicit opt-in."""
		fields = {field["fieldname"]: field for field in self.doctype["fields"]}
		self.assertEqual(fields["dhl_sandbox"]["default"], "1")
		self.assertEqual(fields["dhl_send_recipient_contact"]["default"], "0")
		self.assertEqual(fields["dhl_poll_tracking"]["default"], "0")
		self.assertEqual(fields["dhl_products"]["options"], "Shipment DHL Product")
		self.assertEqual(
			[name for name in self.doctype["field_order"] if name not in fields], [],
		)
