"""Test the rate normalisation behind the rate comparison dialog."""

import unittest

from shipment.api.rates import carrier_label, comparable_rates, fits_all_parcels, fits_weight, parcel_weights, service_class


class TestRates(unittest.TestCase):
	def test_carrier_names_are_unified_across_providers(self):
		self.assertEqual(carrier_label("dhl_de"), "DHL")
		self.assertEqual(carrier_label("DHL"), "DHL")
		self.assertEqual(carrier_label("dpd"), "DPD")
		self.assertEqual(carrier_label("UPS"), "UPS")
		self.assertEqual(carrier_label("DHL Express"), "DHL Express")
		self.assertEqual(carrier_label("FedEx"), "FedEx")
		self.assertEqual(carrier_label("cargoboard"), "Cargoboard")

	def test_express_products_are_separated_from_standard(self):
		self.assertEqual(service_class("DPD Business Express 12:00"), "Express")
		self.assertEqual(service_class("UPS - Express Saver"), "Express")
		self.assertEqual(service_class("Standard Paket National"), "Standard")
		self.assertEqual(service_class("DHL Kleinpaket 0-1kg"), "Standard")
		self.assertEqual(service_class("International Priority", "FedEx"), "Express")

	def test_weight_bands(self):
		self.assertTrue(fits_weight("0.001", "1.001", 1))
		self.assertFalse(fits_weight("10.001", "20.001", 1))
		self.assertFalse(fits_weight("0.001", "1.001", 2.5))
		self.assertTrue(fits_weight(None, None, 5))
		self.assertTrue(fits_weight("0.001", "1.001", 0))
		self.assertEqual(parcel_weights([{"weight": 1}, {"weight": "2.5"}, {}]), [1.0, 2.5])

	def test_every_parcel_must_fit_the_band(self):
		parcels = [{"weight": 1}, {"weight": 15}]
		self.assertFalse(fits_all_parcels("10.001", "20.001", parcels))
		self.assertFalse(fits_all_parcels("0.001", "5.001", parcels))
		self.assertTrue(fits_all_parcels("0.001", "31.501", parcels))
		self.assertTrue(fits_all_parcels("10.001", "20.001", [{"weight": 12}, {}]))

	def test_parcel_shop_services_are_dropped(self):
		rates = comparable_rates([
			{"service_provider": "Packlink", "carrier": "DPD", "service_name": "Paketshop S Paket", "total_price": 4.7, "dropoff": True},
			{"service_provider": "Packlink", "carrier": "UPS", "service_name": "Standard Access Point™", "total_price": 5.8},
			{"service_provider": "Packlink", "carrier": "GLS", "service_name": "Abgabe im PaketShop", "total_price": 7.4},
			{"service_provider": "SendCloud", "carrier": "dhl_de", "service_name": "DHL Paket Servicepoint", "total_price": 4.9},
			{"service_provider": "SendCloud", "carrier": "dpd", "service_name": "DPD Shop2Home XS 0-20kg", "total_price": 6.06},
			{"service_provider": "Packlink", "carrier": "DPD", "service_name": "Classic", "total_price": 5.63, "dropoff": False},
			{"service_provider": "SendCloud", "carrier": "dhl_de", "service_name": "DHL Kleinpaket + Filial Routing 0-1kg", "total_price": 4.98},
		])
		self.assertEqual([r["service_name"] for r in rates], ["DHL Kleinpaket + Filial Routing 0-1kg", "Classic"])

	def test_pallet_freight_only_for_heavy_shipments(self):
		services = lambda: [
			{"service_provider": "LetMeShip", "carrier": "DSV Road", "service_name": "DSV Pallet national", "total_price": 124.65},
			{"service_provider": "LetMeShip", "carrier": "Hellmann", "service_name": "Palette", "total_price": 172.99},
			{"service_provider": "LetMeShip", "carrier": "TNT", "service_name": "Express national Palette", "total_price": 41.86},
			{"service_provider": "LetMeShip", "carrier": "TNT", "service_name": "Express national", "total_price": 41.86},
		]
		self.assertEqual([r["service_name"] for r in comparable_rates(services(), [{"weight": 1}])], ["Express national"])
		self.assertEqual(len(comparable_rates(services(), [{"weight": 120}])), 4)
		self.assertEqual(len(comparable_rates(services())), 4)

	def test_dhl_express_products_share_one_row(self):
		rates = comparable_rates([
			{"service_provider": "LetMeShip", "carrier": "DHL", "service_name": "Domestic Express Paket", "total_price": 37.36},
			{"service_provider": "Packlink", "carrier": "DHL Express", "service_name": "Domestic Express", "total_price": 25.33},
			{"service_provider": "SendCloud", "carrier": "dhl_de", "service_name": "DHL Paket 0-2kg", "total_price": 7.1},
		])
		self.assertEqual([r["carrier_label"] for r in rates], ["DHL", "DHL Express", "DHL Express"])

	def test_unstamped_letter_and_free_methods_are_dropped(self):
		rates = comparable_rates([
			{"service_provider": "SendCloud", "carrier": "sendcloud", "service_name": "Unstamped letter", "total_price": 0},
			{"service_provider": "SendCloud", "carrier": "dhl_de", "service_name": "DHL Kleinpaket 0-1kg", "total_price": 3.95},
			{"service_provider": "LetMeShip", "carrier": "UPS", "service_name": "Standard Paket National", "total_price": 11.54},
			{"service_provider": "Packlink", "carrier": "DPD", "service_name": "Classic", "total_price": 5.63},
			{"service_provider": "Packlink", "carrier": "GLS", "service_name": "Broken", "total_price": None},
		])
		self.assertEqual([r["service_name"] for r in rates], ["DHL Kleinpaket 0-1kg", "Classic", "Standard Paket National"])
		self.assertEqual(rates[0]["carrier_label"], "DHL")
		self.assertEqual(rates[0]["service_class"], "Standard")


if __name__ == "__main__":
	unittest.main()
