# -*- coding: utf-8 -*-
# Copyright (c) 2020, Newmatik and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
# import frappe
from frappe.model.document import Document

from shipment.api.dhl import DHL_PROVIDER, clear_dhl_token_cache, validate_dhl_provider


class ShipmentServiceProvider(Document):

	def validate(self):
		if self.shipment_provider_name == DHL_PROVIDER:
			validate_dhl_provider(self)

	def on_update(self):
		if self.shipment_provider_name == DHL_PROVIDER:
			# A cached OAuth token belongs to the previous credentials.
			clear_dhl_token_cache()
