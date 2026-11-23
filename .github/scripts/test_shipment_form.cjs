"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

let handlers;
const childHandlers = {};
const source = path.resolve(__dirname, "../../shipment/shipment/doctype/shipment/shipment.js");
const curFrm = {};
const context = {
	frappe: {
		ui: { form: { on: (doctype, events) => {
			if (doctype === "Shipment") handlers = events;
			else childHandlers[doctype] = events;
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
	const booking = { service_provider: "Packlink", shipment_id: "DE2026PRO1", status: "Booked" };
	assert.equal(curFrm.packlink_booking_state(booking), "unknown");
	assert.equal(curFrm.packlink_booking_state({ ...booking, tracking_status_info: "AWAITING_COMPLETION" }), "draft");
	assert.equal(curFrm.packlink_booking_state({ ...booking, tracking_status_info: "READY_TO_PURCHASE" }), "draft");
	assert.equal(curFrm.packlink_booking_state({ ...booking, tracking_status_info: "READY_TO_PRINT", awb_number: "1Z" }), null);
	assert.equal(curFrm.packlink_booking_state({ ...booking, tracking_status_info: "READY_TO_PRINT" }), null);
	assert.equal(curFrm.packlink_booking_state({ ...booking, status: "Cancelled" }), null);
	assert.equal(curFrm.packlink_booking_state({ ...booking, service_provider: "SendCloud" }), null);
});


test("LetMeShip cancellation displays the escaped provider reason", () => {
	const doc = { service_provider: 'LetMeShip', shipment_id: '123', status: 'Booking Failed',
		carrier_booking_data: JSON.stringify({letmeship: {shipmentState: 'CANCELLED', text: 'Invalid <zip>'}}) };
	const alert = curFrm.letmeship_booking_alert(doc);
	assert.equal(alert.indicator, 'red');
	assert.match(alert.message, /Invalid &#60;zip&#62;/);
	assert.doesNotMatch(alert.message, /<zip>/);
});

test("LetMeShip pending and legacy unconfirmed bookings are visibly unconfirmed", () => {
	for (const doc of [
		{status: 'Booking Pending'}, {status: 'Booked'},
		{status: 'Booked', carrier_booking_data: 'invalid json'},
	]) {
		const alert = curFrm.letmeship_booking_alert({service_provider: 'LetMeShip', shipment_id: '123', ...doc});
		assert.equal(alert.indicator, 'orange');
		assert.match(alert.message, /not confirmed/);
	}
	assert.equal(curFrm.letmeship_booking_alert({service_provider: 'LetMeShip', shipment_id: '123', status: 'Booked', awb_number: 'AWB'}), null);
	assert.equal(curFrm.letmeship_booking_alert({service_provider: 'DHL', shipment_id: '123', status: 'Booking Pending'}), null);
	assert.equal(curFrm.letmeship_booking_alert({service_provider: 'LetMeShip', shipment_id: '123', status: 'Cancelled'}), null);
});


test("LetMeShip settings mismatch remains visible after booking confirmation", () => {
	const alert = curFrm.letmeship_booking_alert({service_provider: 'LetMeShip', shipment_id: '123', status: 'Booked',
		carrier_booking_data: JSON.stringify({letmeship: {shipmentState: 'BOOKING_FINISHED',
			requested_settings: {pickupOrder: false, insurance: false}, acknowledged_settings: {pickupOrder: true, insurance: true}}})});
	assert.equal(alert.indicator, 'orange');
	assert.match(alert.message, /different pickup or insurance settings/);
});


test("recipient-paid zero-fee offer remains bookable without allowing ordinary free rates", () => {
	const offer = { service_provider: "LetMeShip", carrier: "UPS", service_name: "Standard Paket National", total_price: 0, recipient_billing: { type: "RECEIVER_PAYS" } };
	const m = curFrm.build_rate_matrix([...RATES, offer]);
	assert.ok(m.rates.includes(offer));
	assert.ok(!m.rates.some((s) => s.service_name === "Unstamped letter"));
});

test("rate matrix never recommends a Packlink draft while another provider books directly", () => {
	const cheap = { service_provider: "Packlink", carrier: "DPD", carrier_label: "DPD", service_class: "Standard", service_name: "DPD Classic", total_price: 1.5 };
	const m = curFrm.build_rate_matrix([...RATES, cheap]);
	assert.equal(m.rates[m.rows[0].best].service_provider, "Packlink", "the row still shows Packlink as cheapest");
	assert.equal(m.rates[m.best].service_name, "DHL Kleinpaket 0-1kg");
	const html = curFrm.render_rate_matrix(m);
	assert.match(html, /Pay in Packlink PRO/);
	const only = curFrm.build_rate_matrix([cheap]);
	assert.equal(only.rates[only.best].service_provider, "Packlink");
});

// Set stubs on the shared Frappe mock for one test and restore what was there
// before, also when an assertion fails.
function withFrappe(stubs, run) {
	const saved = Object.keys(stubs).map((key) => [key, Object.hasOwn(context.frappe, key), context.frappe[key]]);
	Object.assign(context.frappe, stubs);
	try {
		run();
	} finally {
		for (const [key, had, value] of saved) {
			if (had) context.frappe[key] = value;
			else delete context.frappe[key];
		}
	}
}

const SESSION_USER_CONTACT = "shipment.shipment.doctype.shipment.shipment.get_session_user_contact";
const noUserRead = { get_value: () => assert.fail("must not read User through frappe.db.get_value") };

test("the company contact is the logged-in user's own contact, read on the server", () => {
	const calls = [];
	withFrappe({
		call: (options) => calls.push(options),
		db: noUserRead,
		throw: (message) => { throw new Error(message); },
		session: { user: "ada@example.com" },
	}, () => {
		const doc = {};
		const frm = { doc, set_value: (field, value) => { doc[field] = value; } };

		handlers.set_company_contact(frm, "Delivery");
		assert.equal(calls.length, 1);
		assert.equal(calls[0].method, SESSION_USER_CONTACT);
		calls[0].callback({ message: { full_name: "Ada Lovelace", last_name: "Lovelace", email: "ada@example.com", phone: "123" } });
		assert.equal(doc.delivery_contact, "Ada Lovelace<br>ada@example.com<br>123");
		assert.equal(doc.delivery_contact_email, "ada@example.com");

		handlers.set_company_contact(frm, "Pickup");
		assert.throws(() => calls[1].callback({ message: { full_name: "Ada", email: "ada@example.com" } }), /Last Name/);
		assert.equal(doc.pickup_contact, "");
	});
});

test("the missing-last-name message escapes the user name", () => {
	const calls = [];
	withFrappe({
		call: (options) => calls.push(options),
		throw: (message) => { throw new Error(message); },
		session: { user: "<img src=x onerror=alert(1)>" },
	}, () => {
		const frm = { doc: {}, set_value: () => {} };
		handlers.set_company_contact(frm, "Pickup");
		assert.throws(() => calls[0].callback({ message: {} }), (error) => {
			assert.doesNotMatch(error.message, /<img/);
			assert.equal(error.message.match(/&#60;img/g).length, 2, "href and link text are both escaped");
			return true;
		});
	});
});

test("a failed contact read clears the company and says so", () => {
	const calls = [];
	const messages = [];
	withFrappe({
		call: (options) => calls.push(options),
		msgprint: (message) => messages.push(message),
	}, () => {
		const doc = { pickup_company: "Newmatik GmbH", pickup_contact: "old", pickup_contact_email: "old@example.com" };
		const frm = { doc, set_value: (field, value) => { doc[field] = value; } };
		handlers.set_company_contact(frm, "Pickup");
		calls[0].error();
		assert.equal(doc.pickup_company, "");
		assert.equal(doc.pickup_contact, "");
		assert.equal(doc.pickup_contact_email, "");
		assert.equal(messages.length, 1);
		assert.match(messages[0], /Could not read/);

	});
});

test("a superseded contact read neither clears nor overwrites the current selection", () => {
	const calls = [];
	const messages = [];
	withFrappe({
		call: (options) => calls.push(options),
		msgprint: (message) => messages.push(message),
		throw: (message) => { throw new Error(message); },
		session: { user: "ada@example.com" },
	}, () => {
		const doc = {};
		const frm = { doc, set_value: (field, value) => { doc[field] = value; } };
		// Company A, then B, then A again: the first read for A fails last.
		for (const company of ["Company A", "Company B", "Company A"]) {
			doc.delivery_company = company;
			handlers.set_company_contact(frm, "Delivery");
		}
		calls[2].callback({ message: { full_name: "Ada Lovelace", last_name: "Lovelace", email: "ada@example.com", phone: "123" } });
		calls[0].error();
		calls[1].callback({ message: {} });
		assert.equal(doc.delivery_company, "Company A");
		assert.equal(doc.delivery_contact, "Ada Lovelace<br>ada@example.com<br>123");
		assert.equal(doc.delivery_contact_email, "ada@example.com");
		assert.equal(messages.length, 0);

		// The pickup side keeps its own counter.
		handlers.set_company_contact(frm, "Pickup");
		calls[3].error();
		assert.equal(messages.length, 1);
	});
});

test("a mask shipment's pickup contact is the user's name with the mobile number as fallback", () => {
	const calls = [];
	const doc = { shipment_delivery_notes: [] };
	const frm = { doc, set_value: (field, value) => { doc[field] = value; }, refresh_fields: () => {}, trigger: () => {} };
	context.locals = { "Shipment Delivery Notes": { row1: { idx: 1, delivery_note: "DN-1" } } };
	context.$ = { each: (items, fn) => (items || []).forEach((item, i) => fn(i, item)) };
	try {
		withFrappe({
			call: (options) => calls.push(options),
			db: noUserRead,
			datetime: { get_today: () => "2026-11-23" },
		}, () => {
			childHandlers["Shipment Delivery Notes"].delivery_note(frm, "Shipment Delivery Notes", "row1");
			assert.equal(calls[0].method, "shipment.shipment.doctype.shipment.shipment.is_mask_shipment");
			calls[0].callback({ message: { qty: 5 } });
			assert.equal(doc.pickup_address_name, "ESO Hygiene-Versand");
			assert.equal(calls[1].method, SESSION_USER_CONTACT);
			calls[1].callback({ message: { full_name: "Ada Lovelace", phone: null, mobile_no: "+49 1" } });
			assert.equal(doc.pickup_contact, "Ada Lovelace<br>service@eso-hygiene.com<br>+49 1");
			assert.equal(doc.pickup_contact_email, "service@eso-hygiene.com");
		});
	} finally {
		delete context.locals;
		delete context.$;
	}
});

test("a failed contact read on a mask shipment leaves no stale sender contact", () => {
	const calls = [];
	const messages = [];
	const doc = { shipment_delivery_notes: [], pickup_contact: "Company contact", pickup_contact_email: "me@example.com" };
	const frm = { doc, set_value: (field, value) => { doc[field] = value; }, refresh_fields: () => {}, trigger: () => {} };
	context.locals = { "Shipment Delivery Notes": { row1: { idx: 1, delivery_note: "DN-1" } } };
	context.$ = { each: (items, fn) => (items || []).forEach((item, i) => fn(i, item)) };
	try {
		withFrappe({
			call: (options) => calls.push(options),
			msgprint: (message) => messages.push(message),
			datetime: { get_today: () => "2026-11-23" },
		}, () => {
			childHandlers["Shipment Delivery Notes"].delivery_note(frm, "Shipment Delivery Notes", "row1");
			calls[0].callback({ message: { qty: 5 } });
			calls[1].error();
			assert.equal(doc.pickup_contact, "");
			assert.equal(doc.pickup_contact_email, "service@eso-hygiene.com");
			assert.equal(messages.length, 1);
		});
	} finally {
		delete context.locals;
		delete context.$;
	}
});

test("booking a Packlink rate asks first; other providers book at once", () => {
	let confirmed;
	context.frappe.confirm = (message, yes) => { confirmed = { message, yes }; };
	context.frappe.model = { can_read: (doctype) => doctype === "Webshop Logins" };
	let booked = 0;
	curFrm.confirm_packlink_draft({ service_provider: "SendCloud" }, () => booked++);
	assert.equal(booked, 1);
	assert.equal(confirmed, undefined);

	curFrm.confirm_packlink_draft({ service_provider: "Packlink" }, () => booked++);
	assert.equal(booked, 1);
	assert.match(confirmed.message, /only creates a draft/);
	assert.match(confirmed.message, /href="\/desk\/webshop-logins"/);
	confirmed.yes();
	assert.equal(booked, 2);

	context.frappe.model = { can_read: () => false };
	curFrm.confirm_packlink_draft({ service_provider: "Packlink" }, () => booked++);
	assert.ok(!confirmed.message.includes("webshop-logins"));
	delete context.frappe.model;
});
