// Copyright (c) 2020, Newmatik and contributors
// For license information, please see license.txt

frappe.ui.form.on('Shipment', {
	address_query: function(frm, link_doctype, link_name, is_your_company_address) {
		return {
			query: 'frappe.contacts.doctype.address.address.address_query',
			filters: {
				link_doctype: link_doctype,
				link_name: link_name,
				is_your_company_address: is_your_company_address
			}
		}
	},
	contact_query: function(frm, link_doctype, link_name) {
		return {
			query: 'frappe.contacts.doctype.contact.contact.contact_query',
			filters: {
				link_doctype: link_doctype,
				link_name: link_name
			}
		}
	},
	onload: function(frm) {
		frm.set_query("delivery_address_name", () => {
			let link_doctype = ''
			let link_name = ''
			let is_your_company_address = 0
			if (frm.doc.delivery_to_type == 'Customer') {
				link_doctype = 'Customer'
				link_name = frm.doc.delivery_customer
			}
			if (frm.doc.delivery_to_type == 'Supplier') {
				link_doctype = 'Supplier'
				link_name = frm.doc.delivery_supplier
			}
			if (frm.doc.delivery_to_type == 'Company') {
				link_doctype = 'Company'
				link_name = frm.doc.delivery_company
				is_your_company_address = 1
			}
			return frm.events.address_query(frm, link_doctype, link_name, is_your_company_address);
		});
		frm.set_query("pickup_address_name", () => {
			let link_doctype = ''
			let link_name = ''
			let is_your_company_address = 0
			if (frm.doc.pickup_from_type == 'Customer') {
				link_doctype = 'Customer'
				link_name = frm.doc.pickup_customer
			}
			if (frm.doc.pickup_from_type == 'Supplier') {
				link_doctype = 'Supplier'
				link_name = frm.doc.pickup_supplier
			}
			if (frm.doc.pickup_from_type == 'Company') {
				link_doctype = 'Company'
				link_name = frm.doc.pickup_company
				is_your_company_address = 1
			}
			return frm.events.address_query(frm, link_doctype, link_name, is_your_company_address);
		});
		frm.set_query("delivery_contact_name", () => {
			let link_doctype = ''
			let link_name = ''
			if (frm.doc.delivery_to_type == 'Customer') {
				link_doctype = 'Customer'
				link_name = frm.doc.delivery_customer
			}
			if (frm.doc.delivery_to_type == 'Supplier') {
				link_doctype = 'Supplier'
				link_name = frm.doc.delivery_supplier
			}
			if (frm.doc.delivery_to_type == 'Company') {
				link_doctype = 'Company'
				link_name = frm.doc.delivery_company
			}
			return frm.events.contact_query(frm, link_doctype, link_name);
		});
		frm.set_query("pickup_contact_name", () => {
			let link_doctype = ''
			let link_name = ''
			if (frm.doc.pickup_from_type == 'Customer') {
				link_doctype = 'Customer'
				link_name = frm.doc.pickup_customer
			}
			if (frm.doc.pickup_from_type == 'Supplier') {
				link_doctype = 'Supplier'
				link_name = frm.doc.pickup_supplier
			}
			if (frm.doc.pickup_from_type == 'Company') {
				link_doctype = 'Company'
				link_name = frm.doc.pickup_company
			}
			return frm.events.contact_query(frm, link_doctype, link_name);
		});
		frm.set_query("delivery_note", "shipment_delivery_notes", function(doc, cdt, cdn) {
			let row = locals[cdt][cdn]
			let customer = ''
			if (frm.doc.delivery_to_type == "Customer") {
				customer = frm.doc.delivery_customer
			}
			if (frm.doc.delivery_to_type == "Company") {
				customer = frm.doc.delivery_company
			}
			if (customer) {
				return {
					filters: {
						customer: customer,
						docstatus: 1,
						status: ["not in", ["Cancelled"]]
					}
				};
			}
		})
	},
	refresh: function(frm) {
		frm.set_value("pickup_date", frappe.datetime.add_days(frappe.datetime.get_today(), 1));
		if (frm.doc.delivery_from_type != 'Company') {
			frm.set_df_property("delivery_contact_name", "reqd", 1);
		}
		if (frm.doc.pickup_from_type != 'Company') {
			frm.set_df_property("pickup_contact_name", "reqd", 1);
		}
		else {
			frm.toggle_display("pickup_contact_name", false)
		}
	},
	set_pickup_company_address: function(frm) {
        frappe.db.get_value('Address', {
			address_title: frm.doc.pickup_company, is_your_company_address: 1}, 'name', (r) => {
				frm.set_value("pickup_address_name", r.name);
        });
	},
	set_delivery_company_address: function(frm) {
        frappe.db.get_value('Address', {
			address_title: frm.doc.delivery_company, is_your_company_address: 1}, 'name', (r) => {
				frm.set_value("delivery_address_name", r.name);
        });
	},
	pickup_from_type: function(frm) {
		if (frm.doc.pickup_from_type == 'Company') {
			frm.set_value("pickup_company", frappe.defaults.get_default('company'));
			frm.trigger('set_pickup_company_address');
			frm.events.set_company_contact(frm, 'Pickup')
			frm.set_df_property("pickup_contact_name", "reqd", 0);
			frm.set_value("pickup_customer", '');
			frm.set_value("pickup_supplier", '');
			frm.toggle_display("pickup_contact_name", false)
		}
		else {
			frm.set_df_property("pickup_contact_name", "reqd", 1);
			frm.toggle_display("pickup_contact_name", true)
			frm.trigger('clear_pickup_fields')
		}
		if (frm.doc.pickup_from_type == 'Customer') {
			frm.set_value("pickup_company", '');
			frm.set_value("pickup_supplier", '');
		}
		if (frm.doc.pickup_from_type == 'Supplier') {
			frm.set_value("pickup_customer", '');
			frm.set_value("pickup_company", '');
		}
	},
	delivery_to_type: function(frm) {
		if (frm.doc.delivery_to_type == 'Company') {
			frm.set_value("delivery_company", frappe.defaults.get_default('company'));
			frm.trigger('set_delivery_company_address');
			frm.events.set_company_contact(frm, 'Delivery')
			frm.set_df_property("delivery_contact_name", "reqd", 0);
			frm.set_value("delivery_customer", '');
			frm.set_value("delivery_supplier", '');
			frm.toggle_display("delivery_contact_name", false)
			
		}
		else {
			frm.set_df_property("delivery_contact_name", "reqd", 1);
			frm.toggle_display("delivery_contact_name", true)
			frm.trigger('clear_delivery_fields')
		}
		if (frm.doc.delivery_to_type == 'Customer') {
			frm.set_value("delivery_company", '');
			frm.set_value("delivery_supplier", '');
		}
		if (frm.doc.delivery_to_type == 'Supplier') {
			frm.set_value("delivery_customer", '');
			frm.set_value("delivery_company", '');
		}
	},
	delivery_supplier: function(frm) {
		frm.trigger('clear_delivery_fields')
	},
	pickup_supplier: function(frm) {
		frm.trigger('clear_pickup_fields')
	},
	delivery_customer: function(frm) {
		frm.trigger('clear_delivery_fields')
	},
	pickup_customer: function(frm) {
		frm.trigger('clear_pickup_fields')
	},
	delivery_address_name: function(frm) {
		if (frm.doc.delivery_to_type == 'Company') {
			erpnext.utils.get_address_display(frm, 'delivery_address_name', 'delivery_address', true);
		}
		else {
			erpnext.utils.get_address_display(frm, 'delivery_address_name', 'delivery_address', false);
		}
	},
	pickup_address_name: function(frm) {
		if (frm.doc.pickup_from_type == 'Company') {
			erpnext.utils.get_address_display(frm, 'pickup_address_name', 'pickup_address', true);
		}
		else {
			erpnext.utils.get_address_display(frm, 'pickup_address_name', 'pickup_address', false);
		}
	},
	get_contact_display: function(frm, contact_name, contact_type) {
		frappe.call({
			method: "frappe.contacts.doctype.contact.contact.get_contact_details",
			args: { contact: contact_name },
			callback: function(r) {
				if(r.message) {
					let contact_display = r.message.contact_display
					if (r.message.contact_email) {
						contact_display += '<br>' + r.message.contact_email
					}
					if (r.message.contact_mobile) {
						contact_display += '<br>' + r.message.contact_mobile
					}
					if (r.message.contact_phone) {
						contact_display += '<br>' + r.message.contact_phone
					}
					if (contact_type == 'Delivery'){
						frm.set_value('delivery_contact', contact_display)
						if (r.message.contact_email) {
							frm.set_value('delivery_contact_email', r.message.contact_email)
						}	
					}
					else {
						frm.set_value('pickup_contact', contact_display)
						if (r.message.contact_email) {
							frm.set_value('pickup_contact_email', r.message.contact_email)
						}
					}
				}
			}
		})
	},
	delivery_contact_name: function(frm) {
		if (frm.doc.delivery_contact_name) {
			frm.events.get_contact_display(frm, frm.doc.delivery_contact_name, 'Delivery')
		}
	},
	pickup_contact_name: function(frm) {
		if (frm.doc.pickup_contact_name) {
			frm.events.get_contact_display(frm, frm.doc.pickup_contact_name, 'Pickup')
		}
	},
	set_company_contact: function(frm, delivery_type) {
        frappe.db.get_value('User', {name: frappe.session.user}, ['full_name', 'email', 'phone'], (r) => {
			let contact_display = r.full_name
			if (r.email) {
				contact_display += '<br>' + r.email
			}
			if (r.phone) {
				contact_display += '<br>' + r.phone
			}
			if (r.mobile_no) {
				contact_display += '<br>' + r.mobile_no
			}
			if (delivery_type == 'Delivery') {
				frm.set_value('delivery_contact', contact_display)
				if (r.email) {
					frm.set_value('delivery_contact_email', r.email)
				}
			}
			else {
				frm.set_value('pickup_contact', contact_display)
				if (r.email) {
					frm.set_value('pickup_contact_email', r.email)
				}
			}
        });
	},
	pickup_company: function(frm) {
		if (frm.doc.pickup_from_type == 'Company') {
	        frm.trigger('set_pickup_company_address')
	        frm.events.set_company_contact(frm, 'Pickup')
		}
	},
	delivery_company: function(frm) {
		if (frm.doc.delivery_to_type == 'Company') {
	        frm.trigger('set_delivery_company_address')
	        frm.events.set_company_contact(frm, 'Delivery')
		}
	},
	delivery_customer: function(frm) {
		frm.events.set_address_name(frm,'Customer',frm.doc.delivery_customer, 'Delivery')
		frm.events.set_contact_name(frm,'Customer',frm.doc.delivery_customer, 'Delivery')
	},
	delivery_supplier: function(frm) {
		frm.events.set_address_name(frm,'Supplier',frm.doc.delivery_supplier, 'Delivery')
		frm.events.set_contact_name(frm,'Supplier',frm.doc.delivery_supplier, 'Delivery')
	},
	pickup_customer: function(frm) {
		frm.events.set_address_name(frm,'Customer',frm.doc.pickup_customer, 'Pickup')
		frm.events.set_contact_name(frm,'Customer',frm.doc.pickup_customer, 'Pickup')
	},
	pickup_supplier: function(frm) {
		frm.events.set_address_name(frm,'Supplier',frm.doc.pickup_supplier, 'Pickup')
		frm.events.set_contact_name(frm,'Supplier',frm.doc.pickup_supplier, 'Pickup')
	},
	set_address_name: function(frm, ref_doctype, ref_docname, delivery_type) {
		frappe.call({
			method: "shipment.shipment.doctype.shipment.shipment.get_address",
			args: {
				ref_doctype: ref_doctype,
				docname: ref_docname
			},
			callback: function(r) {
				if(r.message) {
					if (delivery_type == 'Delivery') {
						frm.set_value('delivery_address_name', r.message)
					}
					else {
						frm.set_value('pickup_address_name', r.message)
					}
				}
			}
		})
	},
	set_contact_name: function(frm, ref_doctype, ref_docname, delivery_type) {
		frappe.call({
			method: "shipment.shipment.doctype.shipment.shipment.get_contact",
			args: {
				ref_doctype: ref_doctype,
				docname: ref_docname
			},
			callback: function(r) {
				if(r.message) {
					if (delivery_type == 'Delivery') {
						frm.set_value('delivery_contact_name', r.message)
					}
					else {
						frm.set_value('pickup_contact_name', r.message)
					}
				}
			}
		})
	},
	add_preset: function(frm) {
		if (frm.doc.preset) {
			frappe.model.with_doc("Shipment Parcel Preset", frm.doc.preset, () => {
				let parcel_preset = frappe.model.get_doc("Shipment Parcel Preset", frm.doc.preset);
				let row = frappe.model.add_child(frm.doc, "Shipment Parcel", "shipment_parcel");
				row.length = parcel_preset.length
				row.width = parcel_preset.width
				row.height = parcel_preset.height
				row.weight = parcel_preset.weight
				frm.refresh_fields("shipment_parcel")
			});
		}
	},
	pickup_date: function(frm) {
		if (frm.doc.pickup_date < frappe.datetime.get_today()) {
			frappe.throw(__("Pickup Date cannot be in the past"));
		}
		if (frm.doc.pickup_date == frappe.datetime.get_today()) {
			var pickup_time = frm.events.get_pickup_time(frm);
			frm.set_value("pickup_from", pickup_time);
			frm.trigger('set_pickup_to_time')
		}	
	},
	pickup_from: function(frm) {
		var pickup_time = frm.events.get_pickup_time(frm) 
		if (frm.doc.pickup_from && frm.doc.pickup_date == frappe.datetime.get_today()) {
			let current_hour = pickup_time.split(':')[0]
			let current_min = pickup_time.split(':')[1]
			let pickup_hour = frm.doc.pickup_from.split(':')[0]
			let pickup_min = frm.doc.pickup_from.split(':')[1]
			if (pickup_hour < current_hour || (pickup_hour == current_hour && pickup_min < current_min)) {
				frm.set_value("pickup_from", pickup_time);
				frappe.throw(__("Pickup Time cannot be in the past"));
			}
		}
		frm.trigger('set_pickup_to_time')
	},
	get_pickup_time: function(frm) {
		let current_hour = new Date().getHours()
		let current_min = new Date().toLocaleString('en-US', {minute: 'numeric'})
		if (current_min < 30) {
			current_min = '30'
		}
		else {
			current_min = '00'
			current_hour = Number(current_hour)+1
		}
		if (Number(current_hour) > 19 || Number(current_hour) == 19){
			frappe.throw(__("Today's pickup time is over, please select different date"));
		}
		current_hour = (current_hour < 10) ? '0' + current_hour : current_hour;
		let pickup_time = current_hour +':'+ current_min
		return pickup_time;
	},
	set_pickup_to_time: function(frm) {
		let pickup_to_hour = Number(frm.doc.pickup_from.split(':')[0])+5
		if (Number(pickup_to_hour) > 19 || Number(pickup_to_hour) == 19){
			pickup_to_hour = 19
		}
		let pickup_to_min = frm.doc.pickup_from.split(':')[1]
		let pickup_to = pickup_to_hour +':'+ pickup_to_min
		frm.set_value("pickup_to", pickup_to);
	},
	clear_pickup_fields: function(frm) {
		frm.set_value("pickup_address_name", '');
		frm.set_value("pickup_contact_name", '');
		frm.set_value("pickup_address", '');
		frm.set_value("pickup_contact", '');
		frm.set_value("pickup_contact_email", '');
	},
	clear_delivery_fields: function(frm) {
		frm.set_value("delivery_address_name", '');
		frm.set_value("delivery_contact_name", '');
		frm.set_value("delivery_address", '');
		frm.set_value("delivery_contact", '');
		frm.set_value("delivery_contact_email", '');
	},
	pickup_from_send_shipping_notification: function(frm) {
		if (frm.doc.pickup_contact_email && frm.doc.pickup_from_send_shipping_notification 
				&& !validate_duplicate(frm, 'shipment_notification_subscriptions', frm.doc.pickup_contact_email)) {
			let row = frappe.model.add_child(frm.doc, "Shipment Notification Subscriptions", "shipment_notification_subscriptions");
			row.email = frm.doc.pickup_contact_email
			frm.refresh_fields("shipment_notification_subscriptions")
		}
		else {
			frm.events.remove_email(frm, 'shipment_notification_subscriptions', frm.doc.pickup_contact_email)
			frm.refresh_fields("shipment_notification_subscriptions")
		}
	},
	pickup_from_subscribe_to_status_updates: function(frm) {
		if (frm.doc.pickup_contact_email && frm.doc.pickup_from_subscribe_to_status_updates
				&& !validate_duplicate(frm, 'shipment_status_update_subscriptions', frm.doc.pickup_contact_email)) {
			let row = frappe.model.add_child(frm.doc, "Shipment Status Update Subscriptions", "shipment_status_update_subscriptions");
			row.email = frm.doc.pickup_contact_email
			frm.refresh_fields("shipment_status_update_subscriptions")
		}
		else {
			frm.events.remove_email(frm, 'shipment_status_update_subscriptions', frm.doc.pickup_contact_email)
			frm.refresh_fields("shipment_status_update_subscriptions")
		}
	},
	delivery_to_send_shipping_notification: function(frm) {
		if (frm.doc.delivery_contact_email && frm.doc.delivery_to_send_shipping_notification
				&& !validate_duplicate(frm, 'shipment_notification_subscriptions', frm.doc.delivery_contact_email)){
			let row = frappe.model.add_child(frm.doc, "Shipment Notification Subscriptions", "shipment_notification_subscriptions");
			row.email = frm.doc.delivery_contact_email
			frm.refresh_fields("shipment_notification_subscriptions")
		}
		else {
			frm.events.remove_email(frm, 'shipment_notification_subscriptions', frm.doc.delivery_contact_email)
			frm.refresh_fields("shipment_notification_subscriptions")
		}
	},
	delivery_to_subscribe_to_status_updates: function(frm) {
		if (frm.doc.delivery_contact_email && frm.doc.delivery_to_subscribe_to_status_updates
				&& !validate_duplicate(frm, 'shipment_status_update_subscriptions', frm.doc.delivery_contact_email)) {
			let row = frappe.model.add_child(frm.doc, "Shipment Status Update Subscriptions", "shipment_status_update_subscriptions");
			row.email = frm.doc.delivery_contact_email
			frm.refresh_fields("shipment_status_update_subscriptions")
		}
		else {
			frm.events.remove_email(frm, 'shipment_status_update_subscriptions', frm.doc.delivery_contact_email)
			frm.refresh_fields("shipment_status_update_subscriptions")
		}
	},
	remove_email: function(frm, table, fieldname) {
		$.each(frm.doc[table] || [], function(i, detail) {
			if(detail.email === fieldname){
				delete detail.email;
			}
		});
	}
});

frappe.ui.form.on('Shipment Delivery Notes', {
	grand_total: function(frm, cdt, cdn) {
		let row = locals[cdt][cdn];
		if (row.grand_total) {
			var value_of_goods = parseFloat(frm.doc.value_of_goods)+parseFloat(row.grand_total)
			frm.set_value("value_of_goods", Math.round(value_of_goods));
			frm.refresh_fields("value_of_goods")
		}
	},
});

var validate_duplicate =  function(frm, table, fieldname){
	let duplicate = false;
	$.each(frm.doc[table], function(i, detail) {
		if(detail.email === fieldname){
			duplicate = true;
			return;
		}
	});
	return duplicate;
};