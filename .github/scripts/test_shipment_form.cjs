"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

let handlers;
const source = path.resolve(__dirname, "../../shipment/shipment/doctype/shipment/shipment.js");
vm.runInNewContext(fs.readFileSync(source, "utf8"), {
	frappe: { ui: { form: { on: (doctype, events) => {
		if (doctype === "Shipment") handlers = events;
	} } } },
	cur_frm: {},
});

function load(doc, isNew) {
	handlers.onload({ doc, is_new: () => isNew, set_query: () => {} });
}

test("clear the cancelled carrier booking on an unsaved amendment", () => {
	const doc = {
		amended_from: "SHIPMENT-05340", shipment_id: "34115532", service_provider: "LetMeShip",
		carrier: "UPS", carrier_service: "Standard", awb_number: "previous-label", status: "Cancelled",
		tracking_status: "In Progress", tracking_status_info: "INFO", tracking_url: "previous-url",
		base_price: 10, net_price: 11, total_vat: 2, shipment_amount: 13,
		shipment_parcel: [{ weight: 2 }], shipment_delivery_notes: [{ delivery_note: "DN-1" }],
		pickup_address_name: "Sender", value_of_goods: 100,
	};
	load(doc, true);
	for (const field of [
		"service_provider", "carrier", "carrier_service", "shipment_id", "awb_number",
		"tracking_status", "tracking_status_info", "tracking_url", "base_price", "net_price",
		"total_vat", "shipment_amount",
	]) assert.equal(doc[field], null, field);
	assert.equal(doc.status, "Draft");
	assert.equal(doc.amended_from, "SHIPMENT-05340");
	assert.deepEqual(doc.shipment_parcel, [{ weight: 2 }]);
	assert.deepEqual(doc.shipment_delivery_notes, [{ delivery_note: "DN-1" }]);
	assert.equal(doc.pickup_address_name, "Sender");
	assert.equal(doc.value_of_goods, 100);
});

test("preserve a saved amendment's independently booked carrier data", () => {
	const doc = { amended_from: "SHIPMENT-1", shipment_id: "fresh-booking", status: "Booked" };
	load(doc, false);
	assert.equal(doc.shipment_id, "fresh-booking");
	assert.equal(doc.status, "Booked");
});

test("preserve non-amended form load behavior", () => {
	const doc = { shipment_id: "imported-booking", status: "Booked" };
	load(doc, true);
	assert.equal(doc.shipment_id, "imported-booking");
	assert.equal(doc.status, "Booked");
});
