"""Fill "Pickup From" and "Delivery To" on Shipments created through the API."""

import frappe

from shipment.shipment.doctype.shipment.shipment import party_display


def execute():
	fields = [
		"name", "pickup", "delivery_to", "pickup_from_type", "delivery_to_type",
		"pickup_company", "pickup_customer", "pickup_supplier",
		"delivery_company", "delivery_customer", "delivery_supplier",
	]
	for row in frappe.get_all("Shipment", fields=fields):
		values = {"pickup": party_display(row, "pickup"), "delivery_to": party_display(row, "delivery")}
		changed = {key: value for key, value in values.items() if value and value != row.get(key)}
		if changed:
			frappe.db.set_value("Shipment", row.name, changed, update_modified=False)
