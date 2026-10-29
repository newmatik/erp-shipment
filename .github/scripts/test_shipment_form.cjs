"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

let handlers;
const source = path.resolve(__dirname, "../../shipment/shipment/doctype/shipment/shipment.js");
const curFrm = {};
const context = {
	frappe: {
		ui: { form: { on: (doctype, events) => {
			if (doctype === "Shipment") handlers = events;
		} } },
		utils: { escape_html: (value) => value.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`) },
	},
	__: (text, args) => text.replace(/\{(\d+)\}/g, (_, i) => args[i]),
	format_currency: (value) => `€ ${Number(value).toFixed(2)}`,
	cur_frm: curFrm,
};
vm.runInNewContext(fs.readFileSync(source, "utf8"), context);

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

const RATES = [
	{ service_provider: "SendCloud", carrier: "dhl_de", carrier_label: "DHL", service_class: "Standard", service_name: "DHL Kleinpaket 0-1kg", total_price: 3.95 },
	{ service_provider: "SendCloud", carrier: "dhl_de", carrier_label: "DHL", service_class: "Standard", service_name: "DHL Kleinpaket GoGreen Plus 0-1kg", total_price: 4.06 },
	{ service_provider: "Packlink", carrier: "DPD", carrier_label: "DPD", service_class: "Standard", service_name: "Paketshop S Paket", total_price: 4.7 },
	{ service_provider: "SendCloud", carrier: "dpd", carrier_label: "DPD", service_class: "Standard", service_name: "DPD Classic KP", total_price: 5.2 },
	{ service_provider: "LetMeShip", carrier: "UPS", carrier_label: "UPS", service_class: "Standard", service_name: "Standard Paket National", total_price: 11.54, is_preferred: 1 },
	{ service_provider: "Packlink", carrier: "UPS", carrier_label: "UPS", service_class: "Standard", service_name: "UPS Standard Service", total_price: 9.28 },
	{ service_provider: "SendCloud", carrier: "dpd", carrier_label: "DPD", service_class: "Express", service_name: "DPD Business Express 12:00", total_price: 14.1 },
	{ service_provider: "SendCloud", carrier: "sendcloud", service_name: "Unstamped letter", total_price: 0 },
];

test("rate matrix: one row per carrier and class, provider columns, cheapest offer recommended", () => {
	const m = curFrm.build_rate_matrix(RATES);
	assert.deepEqual(Array.from(m.providers), ["LetMeShip", "SendCloud", "Packlink"]);
	assert.deepEqual(Array.from(m.rows, (r) => `${r.carrier} ${r.service_class}`), ["DHL Standard", "DPD Standard", "UPS Standard", "DPD Express"]);
	assert.equal(m.rates[m.best].service_name, "DHL Kleinpaket 0-1kg");
	const dpd = m.rows[1];
	assert.equal(m.rates[dpd.best].service_provider, "Packlink");
	assert.equal(m.rates[dpd.cells.SendCloud.best].service_name, "DPD Classic KP");
	const ups = m.rows[2];
	assert.equal(m.rates[ups.best].service_provider, "Packlink");
	assert.equal(m.rates[ups.cells.LetMeShip.best].total_price, 11.54);
	assert.equal(m.rows[0].cells.SendCloud.options.length, 2);
	assert.ok(!m.rates.some((s) => s.service_name === "Unstamped letter"));
});

test("rate matrix renders bookable cells and escapes provider text", () => {
	const m = curFrm.build_rate_matrix([...RATES, { service_provider: "Packlink", carrier: "GLS", carrier_label: "GLS", service_class: "Standard", service_name: "<img src=x>", total_price: 6 }]);
	const html = curFrm.render_rate_matrix(m);
	assert.match(html, /Book for € 3.95/);
	assert.match(html, />\+1</);
	assert.ok(!html.includes("<img src=x>"));
	const buttons = [...html.matchAll(/data-rate="(\d+)"/g)].map((x) => Number(x[1]));
	assert.ok(buttons.every((i) => m.rates[i]));
	assert.ok(buttons.includes(m.best));
});

test("cancel a DHL booking only after confirmation, through the POST-only endpoint", () => {
	const calls = [];
	let confirmed;
	context.frappe.confirm = (message, yes) => { confirmed = { message, yes }; };
	context.frappe.call = (options) => calls.push(options);
	let reloaded = false;
	const frm = { doc: { name: "SHIPMENT-1", shipment_id: "0034<1>" }, reload_doc: () => { reloaded = true; } };

	handlers.cancel_dhl_booking(frm);
	assert.equal(calls.length, 0);
	assert.match(confirmed.message, /0034&#60;1&#62;/);

	confirmed.yes();
	assert.equal(calls.length, 1);
	assert.equal(calls[0].method, "shipment.shipment.doctype.shipment.shipment.cancel_dhl_shipment");
	assert.equal(calls[0].type, "POST");
	assert.equal(JSON.stringify(calls[0].args), JSON.stringify({ shipment: "SHIPMENT-1" }));
	calls[0].callback({ exc: "failed" });
	assert.equal(reloaded, false);
	calls[0].callback({ message: {} });
	assert.equal(reloaded, true);
});

test("rate matrix shows direct DHL rates in their own DHL column", () => {
	const dhl = { service_provider: "DHL", carrier: "DHL", carrier_label: "DHL", service_class: "Standard", service_name: "DHL Paket", product: "V01PAK", total_price: 4.1 };
	const m = curFrm.build_rate_matrix([...RATES, dhl]);
	assert.deepEqual(Array.from(m.providers), ["LetMeShip", "SendCloud", "Packlink", "DHL"]);
	const row = m.rows.find((r) => r.carrier === "DHL" && r.service_class === "Standard");
	assert.equal(m.rates[row.cells.DHL.best].product, "V01PAK");
	assert.match(curFrm.render_rate_matrix(m), /DHL/);
});

test("confirm the DHL portal check only after an explicit yes, through the POST-only endpoint", () => {
	const calls = [];
	let confirmed;
	context.frappe.confirm = (message, yes) => { confirmed = { message, yes }; };
	context.frappe.call = (options) => calls.push(options);
	const frm = { doc: { name: "SHIPMENT-1" }, reload_doc: () => {} };

	handlers.confirm_dhl_portal_checked(frm);
	assert.equal(calls.length, 0);
	assert.match(confirmed.message, /SHIPMENT-1/);
	confirmed.yes();
	assert.equal(calls[0].method, "shipment.shipment.doctype.shipment.shipment.confirm_dhl_portal_checked");
	assert.equal(calls[0].type, "POST");
});

test("a Packlink draft links to its Packlink PRO page until it is bought", () => {
	assert.equal(curFrm.packlink_draft_url("DE2026PRO0003044869"), "https://pro.packlink.de/private/shipments/DE2026PRO0003044869");
	assert.equal(curFrm.packlink_draft_url("XX1/2"), "https://pro.packlink.com/private/shipments/XX1%2F2");
	const draft = { service_provider: "Packlink", shipment_id: "DE2026PRO1", status: "Booked" };
	assert.equal(curFrm.is_packlink_draft(draft), true);
	assert.equal(curFrm.is_packlink_draft({ ...draft, tracking_status_info: "AWAITING_COMPLETION" }), true);
	assert.equal(curFrm.is_packlink_draft({ ...draft, tracking_status_info: "READY_TO_PRINT", awb_number: "1Z" }), false);
	assert.equal(curFrm.is_packlink_draft({ ...draft, tracking_status_info: "READY_TO_PRINT" }), false);
	assert.equal(curFrm.is_packlink_draft({ ...draft, status: "Cancelled" }), false);
	assert.equal(curFrm.is_packlink_draft({ ...draft, service_provider: "SendCloud" }), false);
});
