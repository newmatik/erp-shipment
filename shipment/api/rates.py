"""Normalise carrier rates from all providers for the rate comparison dialog.

Pure functions without Frappe, so they can be tested offline.
"""

import re

CARRIER_LABELS = {
	"dhl": "DHL",
	"dhl_de": "DHL",
	"dhl_parcel": "DHL",
	"dhl_express": "DHL Express",
	"deutsche_post": "Deutsche Post",
	"dpd": "DPD",
	"dpd_de": "DPD",
	"ups": "UPS",
	"gls": "GLS",
	"gls_de": "GLS",
	"hermes": "Hermes",
	"hermes_de": "Hermes",
	"fedex": "FedEx",
	"tnt": "TNT",
	"postnl": "PostNL",
	"go": "GO!",
}

# Provider-internal methods that are no carrier service, e.g. SendCloud's
# "Unstamped letter" (carrier "sendcloud"), which books nothing and costs 0.
NON_CARRIERS = {"sendcloud"}

# We hand parcels to the carrier at our door; services that need a drop-off at a
# parcel shop or access point, or deliver to one, are not offered.
DROP_OFF = re.compile(
	r"paket ?shop|parcel ?shop|shop ?2 ?home|access ?point|abgabe|drop[ -]?off|service ?point|pick[ -]?up ?point|packstation",
	re.IGNORECASE,
)

# Pallet freight; only offered when a parcel is too heavy for parcel services.
PALLET = re.compile(r"palette|pallet|stückgut|freight", re.IGNORECASE)
PARCEL_LIMIT_KG = 31.5

EXPRESS = re.compile(
	r"express|\b\d{1,2}[:.]\d{2}\b|saver|priority|next[ -]?day|overnight|same[ -]?day|expedited",
	re.IGNORECASE,
)


def _key(carrier):
	return re.sub(r"[\s\-]+", "_", (carrier or "").strip().lower())


def carrier_label(carrier):
	"""One display name per carrier across providers ("dhl_de", "DHL" -> "DHL")."""
	key = _key(carrier)
	if key in CARRIER_LABELS:
		return CARRIER_LABELS[key]
	if "dhl" in key and "express" in key:
		return "DHL Express"
	text = (carrier or "").strip()
	return text if text != text.lower() else text.title()


def service_class(service_name, carrier=None):
	"""Express for time-definite or express products, otherwise Standard."""
	text = "{} {}".format(carrier or "", service_name or "")
	return "Express" if EXPRESS.search(text) else "Standard"


def fits_weight(min_weight, max_weight, weight):
	"""True if the parcel weight lies in a method's weight band; unknown bounds fit."""
	if not weight:
		return True
	try:
		low = float(min_weight) if min_weight is not None else 0.0
		high = float(max_weight) if max_weight is not None else float("inf")
	except (TypeError, ValueError):
		return True
	return low <= float(weight) <= high


def parcel_weights(shipment_parcel):
	"""Weights of the parcel rows in kg; rows without a weight are left out."""
	weights = []
	for row in shipment_parcel or []:
		try:
			weight = float(row.get("weight") or 0)
		except (TypeError, ValueError):
			continue
		if weight:
			weights.append(weight)
	return weights


def fits_all_parcels(min_weight, max_weight, shipment_parcel):
	"""True if every parcel fits the band: one method is booked for all parcels of a shipment."""
	return all(fits_weight(min_weight, max_weight, weight) for weight in parcel_weights(shipment_parcel))


def is_drop_off(service):
	"""True for parcel-shop/access-point services (provider flag or service name)."""
	return bool(service.get("dropoff") or service.get("delivery_to_parcelshop") or DROP_OFF.search(service.get("service_name") or ""))


def is_pallet(service):
	"""True for pallet/freight services (service name or pallet carriers like DSV Road and Hellmann)."""
	return bool(PALLET.search(service.get("service_name") or "")) or _key(service.get("carrier")) in {"dsvroad", "dsv_road", "hellmann", "cargoboard"}


def comparable_rates(services, shipment_parcel=None):
	"""Drop non-carrier, zero-priced, parcel-shop and (for parcel-sized shipments) pallet methods,
	add carrier_label and service_class, sort by price."""
	weights = parcel_weights(shipment_parcel)
	parcels_only = bool(weights) and max(weights) <= PARCEL_LIMIT_KG
	rates = []
	for service in services or []:
		try:
			price = float(service.get("total_price") or 0)
		except (TypeError, ValueError):
			continue
		if price <= 0 or _key(service.get("carrier")) in NON_CARRIERS or is_drop_off(service):
			continue
		if parcels_only and is_pallet(service):
			continue
		service["service_class"] = service_class(service.get("service_name"), service.get("carrier"))
		label = carrier_label(service.get("carrier"))
		# DHL's express products are DHL Express, whichever provider sells them.
		service["carrier_label"] = "DHL Express" if label == "DHL" and service["service_class"] == "Express" else label
		rates.append(service)
	return sorted(rates, key=lambda s: float(s.get("total_price") or 0))
