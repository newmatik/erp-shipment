"""Customs data shared by the carrier integrations: when a shipment crosses a
customs border, who declares what, and which data is still missing.

Imports nothing from the providers, so the bench-free Sendcloud tests can load it.
"""
import re

import frappe
from frappe import _
from frappe.utils import escape_html

# EU customs union (alpha-2) and its territories outside the customs or VAT area:
# Helgoland and Buesingen (Germany), Canary Islands, Ceuta, Melilla, Livigno,
# Campione d'Italia, Aland, Mount Athos and the French overseas departments and
# collectivities (postal code prefixes).
EU_CUSTOMS_COUNTRIES = {
	"AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR", "HR", "HU",
	"IE", "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK",
}
CUSTOMS_TERRITORIES = {
	"DE": ("27498", "78266"),
	"FR": ("97", "98"),
	"ES": ("35", "38", "51", "52"),
	"IT": ("23041", "22061"),
	"FI": ("22",),
	"GR": ("63086", "63087"),
}
HS_CODE = re.compile(r"^\d{6,10}$")


def _number(value):
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0


def _customs_area(address):
	"""'EU' inside the EU customs union, else the country code (a territory counts apart)."""
	country = (address.country_code or "").upper()
	postal_code = (address.pincode or "").replace(" ", "")
	if country in EU_CUSTOMS_COUNTRIES and not postal_code.startswith(CUSTOMS_TERRITORIES.get(country, ())):
		return "EU"
	return country + ":" + postal_code if country in CUSTOMS_TERRITORIES else country


def crosses_customs_border(pickup_address, delivery_address):
	"""True when goods leave one customs area for another (Germany -> USA, not Germany -> Austria)."""
	return _customs_area(pickup_address) != _customs_area(delivery_address)


def _party(party_type, company, customer, supplier):
	"""(business name or None, tax ID) of a Shipment party; Company parties are businesses."""
	if party_type == "Company":
		return company, frappe.db.get_value("Company", company, "tax_id")
	doctype, name = ("Customer", customer) if party_type == "Customer" else ("Supplier", supplier)
	prefix = doctype.lower()
	party = frappe.db.get_value(doctype, name, [prefix + "_name", prefix + "_type", "tax_id"], as_dict=True) or frappe._dict()
	return (party.get(prefix + "_name") if party.get(prefix + "_type") == "Company" else None), party.get("tax_id")


def customs_parties(shipment_doc):
	"""Who declares: sender and recipient (business name or None) with their tax IDs; EORI of a sending Company."""
	company = shipment_doc.pickup_company if shipment_doc.pickup_from_type == "Company" else None
	if company:
		sender_company = company
		sender_tax_id, eori = frappe.db.get_value("Company", company, ["tax_id", "eori_number"]) or (None, None)
	else:
		sender_company, sender_tax_id = _party(shipment_doc.pickup_from_type, None, shipment_doc.pickup_customer, shipment_doc.pickup_supplier)
		eori = None
	receiver_company, receiver_tax_id = _party(
		shipment_doc.delivery_to_type, shipment_doc.delivery_company, shipment_doc.delivery_customer, shipment_doc.delivery_supplier)
	return frappe._dict(
		company=company,
		sender_party=company or shipment_doc.pickup_customer or shipment_doc.pickup_supplier,
		sender_company=sender_company,
		vat_id=(sender_tax_id or "").replace(" ", ""),
		eori=(eori or "").replace(" ", ""),
		receiver_company=receiver_company,
		receiver_tax_id=(receiver_tax_id or "").strip(),
	)


def require_customs_data(shipment_doc, provider, parties=None):
	"""Throw one message listing everything a customs declaration still lacks; return the parties."""
	parties = parties or customs_parties(shipment_doc)
	missing = []
	if parties.company and not parties.vat_id:
		missing.append(_("VAT ID (Tax ID) of Company {0}").format(parties.company))
	if parties.company and not parties.eori:
		missing.append(_("EORI Number of Company {0}").format(parties.company))
	if not parties.company and not parties.vat_id:
		# Packlink and the customs office need the sender's tax ID, a private person's too.
		missing.append(_("Tax ID of the sender {0}").format(parties.sender_party or ""))
	if shipment_doc.reason_for_export not in ("Sale", "Sample", "Return", "Documents"):
		missing.append(_("Reason for Export"))
	if not shipment_doc.customs_items:
		missing.append(_('Customs Items: use "Get Customs Items"'))
	for row in shipment_doc.customs_items or []:
		gaps = [label for label, ok in (
			(_("description"), (row.description or "").strip()),
			(_("HS code of 6 to 10 digits"), HS_CODE.match((row.customs_tariff_number or "").replace(" ", ""))),
			(_("country of origin"), row.country_of_origin and row.country_of_origin != "Unknown"),
			(_("quantity"), _number(row.qty) > 0),
			(_("unit value"), _number(row.unit_value) > 0),
		) if not ok]
		if gaps:
			missing.append(_("Customs item {0}: {1}").format(row.idx, ", ".join(gaps)))
	if missing:
		frappe.throw(
			_("{0} needs customs data for this shipment. Complete the Customs section first:").format(provider)
			+ "<br>" + "<br>".join(escape_html(entry) for entry in missing),
			title=_("Customs data incomplete"),
		)
	return parties


def invoice_number(shipment_doc):
	"""The Delivery Notes of the Shipment, else its own name."""
	notes = list(dict.fromkeys(row.delivery_note for row in shipment_doc.shipment_delivery_notes or [] if row.delivery_note))
	return ", ".join(notes) or shipment_doc.name


def country_code(country):
	return (frappe.db.get_value("Country", country, "code") or "").upper()


def goods_value(shipment_doc):
	return round(sum(_number(row.unit_value) * _number(row.qty) for row in shipment_doc.customs_items or []), 2)
