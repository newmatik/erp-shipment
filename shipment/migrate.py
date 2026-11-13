"""Keep ERPNext's own Shipment DocTypes from replacing this app's during a migrate.

ERPNext ships its own `Shipment` and `Shipment Parcel` DocTypes (module Stock).
This app owns both on the site. `bench migrate` syncs apps in install order and
re-imports a DocType JSON whenever its hash differs from the stored
`migration_hash`. The two apps' files never match, so every migrate imported
ERPNext's definition first and this app's again later. In between, the live
`Shipment` had core's fields (`shipment_delivery_note`, not
`shipment_delivery_notes`), and a scheduled job or a user loading a Shipment
failed (#236).

Before the sync, store the hash of ERPNext's file as the DocType's
`migration_hash`: ERPNext's sync then counts it as already imported and skips
it, and this app's sync imports its own file as before, since that hash differs.
"""

import os

import frappe
from frappe.modules.import_file import calculate_hash

# DocTypes this app owns that ERPNext also ships under erpnext/stock/doctype.
SHADOWED_ERPNEXT_DOCTYPES = ("Shipment", "Shipment Parcel")


def keep_app_doctypes():
	"""Make ERPNext's sync skip the Shipment DocTypes this app owns (before_migrate hook)."""
	if "erpnext" not in frappe.get_installed_apps():
		return

	for doctype in SHADOWED_ERPNEXT_DOCTYPES:
		scrubbed = frappe.scrub(doctype)
		path = frappe.get_app_path("erpnext", "stock", "doctype", scrubbed, f"{scrubbed}.json")
		# A DocType not yet on the site is created by the sync itself.
		if not os.path.exists(path) or not frappe.db.exists("DocType", doctype):
			continue
		frappe.db.set_value(
			"DocType", doctype, "migration_hash", calculate_hash(path), update_modified=False
		)
