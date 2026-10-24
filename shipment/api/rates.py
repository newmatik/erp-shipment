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


def heaviest_parcel(shipment_parcel):
	"""Weight of the heaviest parcel row, in kg (0 if none is given)."""
	weights = []
	for row in shipment_parcel or []:
		try:
			weights.append(float(row.get("weight") or 0))
		except (TypeError, ValueError):
			continue
	return max(weights) if weights else 0


def is_drop_off(service):
	"""True for parcel-shop/access-point services (provider flag or service name)."""
	return bool(service.get("dropoff") or service.get("delivery_to_parcelshop") or DROP_OFF.search(service.get("service_name") or ""))


def comparable_rates(services):
	"""Drop non-carrier, zero-priced and parcel-shop methods, add carrier_label and service_class, sort by price."""
	rates = []
	for service in services or []:
		try:
			price = float(service.get("total_price") or 0)
		except (TypeError, ValueError):
			continue
		if price <= 0 or _key(service.get("carrier")) in NON_CARRIERS or is_drop_off(service):
			continue
		service["carrier_label"] = carrier_label(service.get("carrier"))
		service["service_class"] = service_class(service.get("service_name"), service.get("carrier"))
		rates.append(service)
	return sorted(rates, key=lambda s: float(s.get("total_price") or 0))
