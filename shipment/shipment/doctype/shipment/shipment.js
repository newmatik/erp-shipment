// Copyright (c) 2020, Newmatik and contributors
// For license information, please see license.txt
var holidays = []
let deviceNow = new Date();
let currentTime = deviceNow.toTimeString().split(" ")[0]; // Get the time in HH:MM:SS format
let cutoffTime = "12:00:00"; 

// Per subscription table: each party's [subscribe flag, contact email field].
const NOTIFICATION_SUBSCRIPTION_FLAGS = {
	shipment_notification_subscriptions: {
		Pickup: ["pickup_from_send_shipping_notification", "pickup_contact_email"],
		Delivery: ["delivery_to_send_shipping_notification", "delivery_contact_email"],
	},
	shipment_status_update_subscriptions: {
		Pickup: ["pickup_from_subscribe_to_status_updates", "pickup_contact_email"],
		Delivery: ["delivery_to_subscribe_to_status_updates", "delivery_contact_email"],
	},
};

// First date after `date` that is neither in `holidays` nor on a weekend.
function next_working_day(date) {
	let next_date = date;
	for (let attempt = 0; attempt < 366; attempt++) {
		next_date = frappe.datetime.add_days(next_date, 1);
		let day_of_week = frappe.datetime.str_to_obj(next_date).getDay();
		if (!holidays.includes(next_date) && day_of_week !== 0 && day_of_week !== 6) {
			break;
		}
	}
	return next_date;
}

// Contact values end up in read-only Text Editor fields that render HTML, so
// escape each value and only add the trusted line breaks ourselves (mirrors
// _format_contact_display in shipment.py).
function format_contact_display(...values) {
	return values
		.filter((value) => value)
		.map((value) => frappe.utils.escape_html(String(value)))
		.join("<br>");
}

frappe.ui.form.on('Shipment', {
	setup: function(frm) {
		if (frm.doc.__islocal) {
			frm.trigger('pickup_type');
		}
	},
	pickup_type: function(frm) {
        
        if (frm.doc.__islocal) {
            if (frm.doc.pickup_type == 'Self delivery') {
                frm.set_value("pickup_date", frappe.datetime.get_today());
            }
			else {
                
				if (currentTime < cutoffTime) {
                    frm.set_value("pickup_date", frappe.datetime.get_today());
                } else {
                    frm.set_value("pickup_date", frappe.datetime.add_days(frappe.datetime.get_today(), 1));
                }
			}
		}
	},
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
		// Frappe deliberately copies no_copy fields when amending a document.
		// Clear carrier results before the new draft can offer an obsolete label.
		if (frm.is_new() && frm.doc.amended_from) {
			const booking_fields = [
				"service_provider", "carrier", "carrier_service", "shipment_id", "awb_number",
				"carrier_booking_data", "tracking_status", "tracking_status_info", "tracking_url",
				"base_price", "net_price", "total_vat", "shipment_amount",
			];
			for (const fieldname of booking_fields) {
				frm.doc[fieldname] = null;
			}
			frm.doc.status = "Draft";
		}
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
		frm.set_query("delivery_note", "shipment_delivery_notes", function() {
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
       
        if(frm.is_new()){
            frappe.call({
                method:'shipment.shipment.doctype.shipment.shipment.get_holidays',
                args:{
                    'company':frm.doc.pickup_company,
                    'exclude_weekend': false,
                    'from_date':frappe.datetime.get_today()
                },
                callback:function(r){
                    if(r.message){
                        r.message.map((item) => {
                            holidays.push(item.holiday_date)
                        })
                    }
                        if (frm.doc.pickup_type == 'Self delivery') {
                            frm.set_value("pickup_date", frappe.datetime.get_today());
                        }
                        else {
                            for (let idx = 0; idx < holidays.length; idx++) {
                            if (!holidays.includes(frappe.datetime.add_days(frappe.datetime.get_today(), idx))) {
                                if (currentTime < cutoffTime) {
                                    frm.set_value("pickup_date", frappe.datetime.get_today(),idx);
                                } else {
                                    frm.set_value("pickup_date", frappe.datetime.add_days(frappe.datetime.get_today(), idx+1));
                                }
                                break;
                            }
                                }
                        }   
                }
            })
        }

        // Add preset to child table on select of preset on click
        $('#awesomplete_list_6').unbind('click').bind('click', function (e) {
            set_presets(e.target.innerText)
        })

        // Add preset to child table on select of preset on key press enter
        var div = $('[data-fieldname="preset"]').closest('div');
            div.prop('id','awesomplete_list_wrapper')
            $('div#awesomplete_list_6').attr('tabindex', '0');
            $('#awesomplete_list_wrapper').unbind('keydown').on('keydown', function (event) {
                if (event.type === 'keydown' && event.which === 13){
                    set_presets(event.target.value);
                }
        });
        
        if(frm.is_new()) setTimeout(function() { $('input[data-fieldname="preset"]').focus()},500);

		if (frm.doc.docstatus==1 && !frm.doc.shipment_id) {
			frm.add_custom_button(__('Fetch Shipping Rates'), function() {
				return frm.events.fetch_shipping_rates(frm);
			});
			if ((frm.doc.carrier_booking_data || '').includes('"uncertain_booking"')) {
				frm.add_custom_button(__('Confirm DHL Portal Checked'), function() {
					return frm.events.confirm_dhl_portal_checked(frm);
				});
			}
		}
		if (frm.doc.docstatus == 1 && cur_frm.is_packlink_draft(frm.doc)) {
			const url = cur_frm.packlink_draft_url(frm.doc.shipment_id);
			frm.dashboard.set_headline_alert(
				__('Not bought yet: Packlink only created a draft. Complete and pay it in Packlink PRO; there is no label and no pickup until then.')
				+ ` <a href="${frappe.utils.escape_html(url)}" target="_blank" rel="noopener noreferrer">${__('Open in Packlink PRO')}</a>`,
				'orange'
			);
			frm.add_custom_button(__('Complete in Packlink PRO'), function() {
				window.open(url, '_blank', 'noopener');
			}).addClass('btn-primary');
		}
		if (frm.doc.shipment_id) {
			frm.add_custom_button(__('Print Shipping Label'), function() {
				return frm.events.print_shipping_label(frm);
			});
			if (frm.doc.tracking_status != 'Delivered') {
				frm.add_custom_button(__('Update Tracking and Cost'), function() {
				    return frm.events.update_tracking(frm, frm.doc.service_provider, frm.doc.shipment_id);
				});
			}
			if (frm.doc.docstatus == 1 && frm.doc.service_provider == 'DHL') {
				frm.add_custom_button(__('Cancel DHL Booking'), function() {
					return frm.events.cancel_dhl_booking(frm);
				});
			}
		}
		$('div[data-fieldname=pickup_address] > div > .clearfix').hide()
		$('div[data-fieldname=pickup_contact] > div > .clearfix').hide()
		$('div[data-fieldname=delivery_address] > div > .clearfix').hide()
		$('div[data-fieldname=delivery_contact] > div > .clearfix').hide()

		if (frm.doc.delivery_to_type != 'Company') {
			frm.set_df_property("delivery_contact_name", "reqd", 1);
		}
		else {
			frm.set_df_property("delivery_contact_name", "reqd", 0);
			frm.toggle_display("delivery_contact_name", false)
		}
		if (frm.doc.pickup_from_type != 'Company') {
			frm.set_df_property("pickup_contact_name", "reqd", 1);
		}
		else {
			frm.set_df_property("pickup_contact_name", "reqd", 0);
			frm.toggle_display("pickup_contact_name", false)
		}
	},
    value_of_goods: function(frm){
        frm.set_value("value_of_goods", Math.ceil(frm.doc.value_of_goods))
    },
	before_save: function(frm) {
		if (frm.doc.delivery_to_type == 'Company') {
			frm.set_value("delivery_to", frm.doc.delivery_company);
		}
		if (frm.doc.delivery_to_type == 'Customer') {
			frm.set_value("delivery_to", frm.doc.delivery_customer);
		}
		if (frm.doc.delivery_to_type == 'Supplier') {
			frm.set_value("delivery_to", frm.doc.delivery_supplier);
		}
		if (frm.doc.pickup_from_type == 'Company') {
			frm.set_value("pickup", frm.doc.pickup_company);
		}
		if (frm.doc.pickup_from_type == 'Customer') {
			frm.set_value("pickup", frm.doc.pickup_customer);
		}
		if (frm.doc.pickup_from_type == 'Supplier') {
			frm.set_value("pickup", frm.doc.pickup_supplier);
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
		frm.events.remove_notific_child_table(frm, 'shipment_notification_subscriptions', 'Pickup')
		frm.events.remove_notific_child_table(frm, 'shipment_status_update_subscriptions', 'Pickup')
	},
	delivery_to_type: function(frm) {
		if (frm.doc.delivery_to_type == 'Company') {
			frm.set_value("delivery_company", frappe.defaults.get_default('company'));
			frm.set_df_property("delivery_contact_name", "reqd", 0);
			frm.set_value("delivery_customer", '');
			frm.set_value("delivery_supplier", '');
			frm.toggle_display("delivery_contact_name", true)
			frm.trigger('delivery_company')
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
			frm.toggle_display("shipment_delivery_notes", false)
		}
		else {
			frm.toggle_display("shipment_delivery_notes", true)
		}
		frm.events.remove_notific_child_table(frm, 'shipment_notification_subscriptions', 'Delivery')
		frm.events.remove_notific_child_table(frm, 'shipment_status_update_subscriptions', 'Delivery')
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
					if (!(r.message.contact_email && (r.message.contact_phone || r.message.contact_mobile))) {
						if (contact_type == 'Delivery') {
							frm.set_value('delivery_contact_name', '')
							frm.set_value('delivery_contact', '')
						}
						else {
							frm.set_value('pickup_contact_name', '')
							frm.set_value('pickup_contact', '')
						}
						frappe.throw(__(`Email or Phone/Mobile of the Contact are mandatory to continue. </br>
							Please set Email/Phone for the contact <a href="#Form/Contact/${encodeURIComponent(contact_name)}">${frappe.utils.escape_html(contact_name)}</a>`))
					}
					let contact_display = format_contact_display(
						r.message.contact_display,
						r.message.contact_email,
						r.message.contact_phone || r.message.contact_mobile
					);
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
        frappe.db.get_value('User', {name: frappe.session.user}, ['full_name', 'last_name', 'email', 'phone', 'mobile_no'], (r) => {
			if (!(r.last_name)) {
				if (delivery_type == 'Delivery') {
					frm.set_value('delivery_company', '')
					frm.set_value('delivery_contact', '')
				}
				else {
					frm.set_value('pickup_company', '')
					frm.set_value('pickup_contact', '')
				}
				frappe.throw(__(`Last Name of the user are mandatory to continue. </br>
					Please first set Last Name for the user <a href="#Form/User/${frappe.session.user}">${frappe.session.user}</a>`))
			}
			let contact_display = format_contact_display(r.full_name, r.email, r.phone || r.mobile_no);
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
		if (frm.doc.pickup_from_type == 'Company'  && frm.doc.pickup_company) {
	        frm.trigger('set_pickup_company_address')
	        frm.events.set_company_contact(frm, 'Pickup')
		}
	},
	delivery_company: function(frm) {
		if (frm.doc.delivery_to_type == 'Company' && frm.doc.delivery_company) {
	        frm.trigger('set_delivery_company_address')
	        frm.events.set_company_contact(frm, 'Delivery')
		}
	},
	delivery_customer: function(frm) {
		frm.trigger('clear_delivery_fields')
		if (frm.doc.delivery_customer) {
			frm.events.set_address_name(frm,'Customer',frm.doc.delivery_customer, 'Delivery')
			frm.events.set_contact_name(frm,'Customer',frm.doc.delivery_customer, 'Delivery')
		}
	},
	delivery_supplier: function(frm) {
		frm.trigger('clear_delivery_fields')
		if (frm.doc.delivery_supplier) {
			frm.events.set_address_name(frm,'Supplier',frm.doc.delivery_supplier, 'Delivery')
			frm.events.set_contact_name(frm,'Supplier',frm.doc.delivery_supplier, 'Delivery')
		}	
	},
	pickup_customer: function(frm) {
		if (frm.doc.pickup_from_type !== 'Customer') {
			return;
		}
		frm.trigger('clear_pickup_fields')
		if (frm.doc.pickup_customer) {
			frm.events.set_address_name(frm,'Customer',frm.doc.pickup_customer, 'Pickup')
			frm.events.set_contact_name(frm,'Customer',frm.doc.pickup_customer, 'Pickup')
		}
	},
	pickup_supplier: function(frm) {
		if (frm.doc.pickup_from_type !== 'Supplier') {
			return;
		}
		frm.trigger('clear_pickup_fields')
		if (frm.doc.pickup_supplier) {
			frm.events.set_address_name(frm,'Supplier',frm.doc.pickup_supplier, 'Pickup')
			frm.events.set_contact_name(frm,'Supplier',frm.doc.pickup_supplier, 'Pickup')
		}
	},
	set_address_name: function(frm, ref_doctype, ref_docname, delivery_type) {
		frappe.call({
			method: "shipment.shipment.doctype.shipment.shipment.get_address_name",
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
			method: "shipment.shipment.doctype.shipment.shipment.get_contact_name",
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
        
        if(holidays.includes(frm.doc.pickup_date)){
            frm.set_value("pickup_date", next_working_day(frm.doc.pickup_date));
            frappe.msgprint(__("The Pickup Date should not be a weekend or a holiday. Please select another date."))
        }

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
	pickup_from_send_shipping_notification: function(frm, cdt, cdn) {
		if (frm.doc.pickup_contact_email && frm.doc.pickup_from_send_shipping_notification 
				&& !validate_duplicate(frm, 'shipment_notification_subscriptions', frm.doc.pickup_contact_email, locals[cdt][cdn].idx)) {
			let row = frappe.model.add_child(frm.doc, "Shipment Notification Subscriptions", "shipment_notification_subscriptions");
			row.email = frm.doc.pickup_contact_email
			frm.refresh_fields("shipment_notification_subscriptions")
		}
		if (!frm.doc.pickup_from_send_shipping_notification) {
			frm.events.remove_email_row(frm, 'shipment_notification_subscriptions', frm.doc.pickup_contact_email)
			frm.refresh_fields("shipment_notification_subscriptions")
		}
	},
	pickup_from_subscribe_to_status_updates: function(frm, cdt, cdn) {
		if (frm.doc.pickup_contact_email && frm.doc.pickup_from_subscribe_to_status_updates
				&& !validate_duplicate(frm, 'shipment_status_update_subscriptions', frm.doc.pickup_contact_email, locals[cdt][cdn].idx)) {
			let row = frappe.model.add_child(frm.doc, "Shipment Status Update Subscriptions", "shipment_status_update_subscriptions");
			row.email = frm.doc.pickup_contact_email
			frm.refresh_fields("shipment_status_update_subscriptions")
		}
		if (!frm.doc.pickup_from_subscribe_to_status_updates) {
			frm.events.remove_email_row(frm, 'shipment_status_update_subscriptions', frm.doc.pickup_contact_email)
			frm.refresh_fields("shipment_status_update_subscriptions")
		}
	},
	delivery_to_send_shipping_notification: function(frm, cdt, cdn) {
		if (frm.doc.delivery_contact_email && frm.doc.delivery_to_send_shipping_notification
				&& !validate_duplicate(frm, 'shipment_notification_subscriptions', frm.doc.delivery_contact_email, locals[cdt][cdn].idx)){
			let row = frappe.model.add_child(frm.doc, "Shipment Notification Subscriptions", "shipment_notification_subscriptions");
			row.email = frm.doc.delivery_contact_email
			frm.refresh_fields("shipment_notification_subscriptions")
		}
		if (!frm.doc.delivery_to_send_shipping_notification) {
			frm.events.remove_email_row(frm, 'shipment_notification_subscriptions', frm.doc.delivery_contact_email)
			frm.refresh_fields("shipment_notification_subscriptions")
		}
	},
	delivery_to_subscribe_to_status_updates: function(frm, cdt, cdn) {
		if (frm.doc.delivery_contact_email && frm.doc.delivery_to_subscribe_to_status_updates
				&& !validate_duplicate(frm, 'shipment_status_update_subscriptions', frm.doc.delivery_contact_email, locals[cdt][cdn].idx)) {
			let row = frappe.model.add_child(frm.doc, "Shipment Status Update Subscriptions", "shipment_status_update_subscriptions");
			row.email = frm.doc.delivery_contact_email
			frm.refresh_fields("shipment_status_update_subscriptions")
		}
		if (!frm.doc.delivery_to_subscribe_to_status_updates) {
			frm.events.remove_email_row(frm, 'shipment_status_update_subscriptions', frm.doc.delivery_contact_email)
			frm.refresh_fields("shipment_status_update_subscriptions")
		}
	},
	remove_email_row: function(frm, table, email) {
		// Keep the row while the other party still subscribes with the same
		// email (pickup and delivery contacts can share one address).
		let flags = Object.values(NOTIFICATION_SUBSCRIPTION_FLAGS[table] || {});
		if (flags.some(([flag, email_field]) => frm.doc[flag] && frm.doc[email_field] === email)) {
			return;
		}
		let stale_rows = (frm.doc[table] || []).filter((detail) => detail.email === email);
		stale_rows.forEach((detail) => frappe.model.clear_doc(detail.doctype, detail.name));
		if (stale_rows.length) {
			frm.dirty();
		}
	},
	remove_notific_child_table: function(frm, table, delivery_type) {
		// Changing one party's type resets that party's notification flags below,
		// so keep only the other (unchanged) party's subscription, and only while
		// that party is actually subscribed to this table. Collect first:
		// removing rows while iterating the same array would skip entries.
		let other_party = delivery_type == 'Delivery' ? 'Pickup' : 'Delivery';
		let [other_flag, other_email_field] = (NOTIFICATION_SUBSCRIPTION_FLAGS[table] || {})[other_party] || [];
		let keep_email = other_flag && frm.doc[other_flag] ? frm.doc[other_email_field] : null;
		let stale_rows = (frm.doc[table] || []).filter(
			(detail) => !(keep_email && detail.email === keep_email)
		);
		stale_rows.forEach((detail) => frappe.model.clear_doc(detail.doctype, detail.name));
		if (stale_rows.length) {
			frm.dirty();
		}
		frm.refresh_fields(table);
		if (delivery_type == 'Delivery') {
			frm.set_value("delivery_to_send_shipping_notification", 0);
			frm.set_value("delivery_to_subscribe_to_status_updates", 0);
			frm.refresh_fields("delivery_to_send_shipping_notification");
			frm.refresh_fields("delivery_to_subscribe_to_status_updates");
		}
		else {
			frm.set_value("pickup_from_send_shipping_notification", 0);
			frm.set_value("pickup_from_subscribe_to_status_updates", 0);
			frm.refresh_fields("pickup_from_send_shipping_notification");
			frm.refresh_fields("pickup_from_subscribe_to_status_updates");
		}
	},
	fetch_shipping_rates: function(frm) {
		if (!frm.doc.shipment_id) {
			if (frm.doc.pickup_date < frappe.datetime.get_today()) {
				frappe.throw(__("Pickup Date cannot be in the past"));
			}
			frappe.call({
				method: "shipment.shipment.doctype.shipment.shipment.fetch_shipping_rates",
				freeze: true,
				freeze_message: __("Fetching Shipping Rates"),
				args: {
					pickup_from_type: frm.doc.pickup_from_type,
					delivery_to_type: frm.doc.delivery_to_type,
					pickup_address_name: frm.doc.pickup_address_name,
					delivery_address_name: frm.doc.delivery_address_name,
					shipment_parcel: frm.doc.shipment_parcel,
					description_of_content: frm.doc.description_of_content,
					pickup_date: frm.doc.pickup_date,
					pickup_contact_name: frm.doc.pickup_contact_name,
					delivery_contact_name: frm.doc.delivery_contact_name,
					value_of_goods: frm.doc.value_of_goods,
					pickup_type: frm.doc.pickup_type,
					pallets: frm.doc.pallets,
				},
				callback: function(r) {
					if (r.message && r.message.length > 0) {
						cur_frm.select_from_available_services(frm, r.message);
					} else {
						// Check if there are detailed error messages from the providers
						let error_msg = __("No Shipment Services available");
						if (r.error_messages && r.error_messages.length > 0) {
							error_msg = r.error_messages.join('<br><br>');
						}
						frappe.msgprint({
							title: __('Error'),
							indicator: 'red',
							message: error_msg
						});
					}
				},
				error: function(r) {
					console.error('Error fetching shipping rates:', r);
				}
			})
		}
		else {
			frappe.throw(__("Shipment already created"));
		}
	},
	print_shipping_label: function(frm) {
		frappe.call({
			method: "shipment.shipment.doctype.shipment.shipment.print_shipping_label",
			freeze: true,
			freeze_message: __("Printing Shipping Label"),
			args: {
				shipment_id: frm.doc.shipment_id,
				service_provider: frm.doc.service_provider
			},
			callback: function(r) {
				if (r.message) {
					if (frm.doc.service_provider == "LetMeShip") {
						var array = JSON.parse(r.message)
						//Uint8Array for unsigned bytes
						array = new Uint8Array(array);
						const file = new Blob([array], {type: "application/pdf"});
						const file_url = URL.createObjectURL(file);
						window.open(file_url);
					}
					else {
						if (Array.isArray(r.message)) {
							r.message.forEach(url => window.open(url))
						} else {
							window.open(r.message);
						}
					}
				}
			}
		})
	},
	cancel_dhl_booking: function(frm) {
		// DHL deletes labels only before its end-of-day closing; the server
		// cancels the Shipment only after DHL confirmed every deletion.
		frappe.confirm(
			__("Delete DHL shipment(s) {0} at DHL and cancel this Shipment?", [frappe.utils.escape_html(frm.doc.shipment_id)]),
			function() {
				frappe.call({
					method: "shipment.shipment.doctype.shipment.shipment.cancel_dhl_shipment",
					type: "POST",
					freeze: true,
					freeze_message: __("Cancelling DHL Booking"),
					args: {shipment: frm.doc.name},
					callback: function(r) {
						if (!r.exc) {
							frm.reload_doc();
						}
					}
				});
			}
		);
	},
	confirm_dhl_portal_checked: function(frm) {
		// The DHL API cannot find shipments by reference, so a person checks the portal.
		frappe.confirm(
			__("Did you check the DHL Geschäftskundenportal for shipments with reference {0} and delete any that should not be shipped?", [frappe.utils.escape_html(frm.doc.name)]),
			function() {
				frappe.call({
					method: "shipment.shipment.doctype.shipment.shipment.confirm_dhl_portal_checked",
					type: "POST",
					args: {shipment: frm.doc.name},
					callback: function(r) {
						if (!r.exc) {
							frm.reload_doc();
						}
					}
				});
			}
		);
	},
	update_tracking: function(frm, service_provider, shipment_id) {
		let delivery_notes = [];
		(frm.doc.shipment_delivery_notes || []).forEach((d) => {
				delivery_notes.push(d.delivery_note)
		});
		frappe.call({
			method: "shipment.shipment.doctype.shipment.shipment.update_tracking",
			freeze: true,
			freeze_message: __("Updating Tracking and Cost"),
			args: {
				shipment: frm.doc.name,
				shipment_id: shipment_id,
				service_provider: service_provider,
				delivery_notes: delivery_notes
			},
			callback: function(r) {
				if (!r.exc) {
					frm.reload_doc();
				}
				frappe.call({
					method: "shipment.shipment.doctype.shipment.shipment.calculate_shipping_cost",
					args: {data: cur_frm.doc},
					callback: function(r){
						frm.reload_doc();
					}
				})
			}
		})
		
	}
});

frappe.ui.form.on('Shipment Delivery Notes', {
	delivery_note: function(frm, cdt, cdn) {
		let row = locals[cdt][cdn];
		if (row.delivery_note) {
			let row_index = row.idx - 1
			if(validate_duplicate(frm, 'shipment_delivery_notes', row.delivery_note, row_index)) {
				cur_frm.get_field('shipment_delivery_notes').grid.grid_rows[row_index].remove();
				frappe.throw(__(`You have entered duplicate Delivery Notes. Please rectify and try again.`))
			}
			frappe.call({
				method: "shipment.shipment.doctype.shipment.shipment.is_mask_shipment",
				args: {
					delivery_note: row.delivery_note
				},
				callback: function(r) {
					if (r.message) {
						frm.set_value("description_of_content", 'Einmal-Mundschutz');
						frm.set_value("pickup_type", 'Self delivery');
						frm.set_value("pickup_date", frappe.datetime.get_today());
						frm.set_value("pickup_address_name", 'ESO Hygiene-Versand');
				        frappe.db.get_value('User', {name: frappe.session.user}, ['full_name', 'phone'], (r) => {
							let contact_display = format_contact_display(r.full_name, 'service@eso-hygiene.com', r.phone);
							frm.set_value('pickup_contact', contact_display)
							frm.set_value('pickup_contact_email', 'service@eso-hygiene.com')
				        });
						if (r.message.qty == 10) {
							frm.set_value('preset', 'Faltkarton 4')
							frm.refresh_fields("preset")
							frm.trigger('add_preset')
						}
					}
				}
			});
		}
	},
	grand_total: function(frm, cdt, cdn) {
		let row = locals[cdt][cdn];
		if (row.grand_total) {
			var value_of_goods = parseFloat(frm.doc.value_of_goods || 0.00)+parseFloat(row.grand_total)
			frm.set_value("value_of_goods", Math.ceil(value_of_goods));
			frm.refresh_fields("value_of_goods")
		}
	},
});

var validate_duplicate =  function(frm, table, fieldname, index){
	let duplicate = false;
	$.each(frm.doc[table], function(i, detail) {
		// Email duplicate validation
		if(detail.email === fieldname && !(index === i)) {
			duplicate = true;
			return;
		}

		// Delivery Note duplicate validation
		if(detail.delivery_note === fieldname && !(index === i)) {
			duplicate = true;
			return;
		}
	});
	return duplicate;
};

// Providers in a fixed column order; any other provider is appended.
const RATE_PROVIDERS = ["LetMeShip", "SendCloud", "Packlink", "DHL"];

// One row per carrier and service class (Standard/Express); one column per
// provider holding its cheapest offer. Indexes point into `rates`.
// Packlink's API only creates drafts; they are bought by paying them in Packlink PRO.
const PACKLINK_DRAFT_STATES = ['AWAITING_COMPLETION', 'READY_TO_PURCHASE'];
const PACKLINK_PRO_DOMAINS = { DE: 'de', ES: 'es', FR: 'fr', IT: 'it' };

cur_frm.packlink_draft_url = function(reference) {
	const domain = PACKLINK_PRO_DOMAINS[(reference || '').slice(0, 2).toUpperCase()] || 'com';
	return `https://pro.packlink.${domain}/private/shipments/${encodeURIComponent(reference || '')}`;
};

cur_frm.is_packlink_draft = function(doc) {
	// Before the first tracking update the state is unknown; a bought shipment soon has a tracking number.
	return doc.service_provider == 'Packlink' && !!doc.shipment_id && doc.status != 'Cancelled'
		&& (PACKLINK_DRAFT_STATES.includes(doc.tracking_status_info) || (!doc.tracking_status_info && !doc.awb_number));
};

cur_frm.build_rate_matrix = function(services) {
	const rates = (services || []).filter((s) => Number(s.total_price) > 0);
	const price = (index) => Number(rates[index].total_price);
	const present = [...new Set(rates.map((s) => s.service_provider))];
	const providers = RATE_PROVIDERS.filter((p) => present.includes(p))
		.concat(present.filter((p) => !RATE_PROVIDERS.includes(p)));
	const groups = new Map();
	rates.forEach((s, index) => {
		const carrier = s.carrier_label || s.carrier || "";
		const level = s.service_class || "Standard";
		const key = `${carrier}|${level}`;
		if (!groups.has(key)) groups.set(key, { carrier, service_class: level, cells: {}, best: null });
		const row = groups.get(key);
		const cell = row.cells[s.service_provider] || (row.cells[s.service_provider] = { best: null, options: [] });
		cell.options.push(index);
		if (cell.best === null || price(index) < price(cell.best)) cell.best = index;
		if (row.best === null || price(index) < price(row.best)) row.best = index;
	});
	const rows = [...groups.values()].sort((a, b) => price(a.best) - price(b.best)
		|| (a.service_class === b.service_class ? 0 : a.service_class === "Standard" ? -1 : 1));
	return { rates, providers, rows, best: rows.length ? rows[0].best : null };
};

cur_frm.render_rate_matrix = function(matrix) {
	const esc = (value) => frappe.utils.escape_html(String(value == null ? "" : value));
	const money = (value) => esc(format_currency(value, "EUR", 2));
	const star = (s) => s.is_preferred ? `<span class="rate-star" title="${esc(__("Preferred service"))}">★</span>` : "";
	// The price is the booking button; the cheapest offer of a row is highlighted.
	const offer = (index, cheapest, extra = 0) => {
		const s = matrix.rates[index];
		return `<button type="button" class="btn btn-xs btn-default rate-book${cheapest ? " rate-book-best" : ""}" data-rate="${index}"
			title="${esc(__("Book {0} with {1}", [s.service_name, s.service_provider]))}">${money(s.total_price)}</button>
			<span class="rate-service" title="${esc(s.service_name)}">${esc(s.service_name)}</span>${star(s)}${extra ? `<span class="rate-more" title="${esc(__("{0} more offers under All offers", [extra]))}">+${extra}</span>` : ""}`;
	};
	const best = matrix.rates[matrix.best];
	const header = matrix.providers.map((p) => `<th>${esc(p)}</th>`).join("");
	const rows = matrix.rows.map((row) => `<tr><td class="rate-carrier">${esc(row.carrier)}${row.service_class === "Express" && !/express/i.test(row.carrier) ? ` <span class="rate-class">${esc(__("Express"))}</span>` : ""}</td>${
		matrix.providers.map((provider) => {
			const cell = row.cells[provider];
			return cell ? `<td class="${cell.best === row.best ? "rate-cheapest" : ""}"><div class="rate-cell">${offer(cell.best, cell.best === row.best, cell.options.length - 1)}</div></td>`
				: `<td class="text-muted">–</td>`;
		}).join("")}</tr>`).join("");
	const all = matrix.rates.map((s, index) => `<tr><td>${esc(s.service_provider)}</td><td>${esc(s.carrier_label || s.carrier)}</td>
		<td>${esc(s.service_name)}${star(s)}</td><td class="text-right"><button type="button" class="btn btn-xs btn-default rate-book" data-rate="${index}">${money(s.total_price)}</button></td></tr>`).join("");
	return `<div class="rate-matrix">
		<div class="rate-recommendation">
			<span class="text-muted">${esc(__("Recommended"))}</span>
			<b>${esc(best.carrier_label || best.carrier)}${best.service_class === "Express" && !/express/i.test(best.carrier_label || best.carrier) ? " " + esc(__("Express")) : ""} · ${esc(best.service_provider)}</b>
			<span class="rate-service" title="${esc(best.service_name)}">${esc(best.service_name)}</span>${star(best)}
			<button type="button" class="btn btn-xs btn-primary rate-book" data-rate="${matrix.best}">${esc(__("Book for {0}", [format_currency(best.total_price, "EUR", 2)]))}</button>
		</div>
		<table class="table table-bordered rate-table">
			<colgroup><col class="rate-carrier-col">${matrix.providers.map(() => "<col>").join("")}</colgroup>
			<thead><tr><th>${esc(__("Carrier"))}</th>${header}</tr></thead><tbody>${rows}</tbody></table>
		<details class="rate-all"><summary><span>${esc(__("All offers ({0})", [matrix.rates.length]))}</span>
			<span class="text-muted">${esc(__("Net prices excl. VAT, all parcels"))}</span></summary>
			<table class="table table-bordered rate-list"><tbody>${all}</tbody></table></details>
	</div>
	<style>
		.rate-matrix { font-size: var(--text-sm, 13px); }
		.rate-matrix .rate-recommendation { display: flex; align-items: center; gap: 8px; padding: 6px 10px; margin-bottom: 8px;
			border: 1px solid var(--border-color); border-radius: var(--border-radius, 6px); background: var(--subtle-fg); white-space: nowrap; }
		.rate-matrix .rate-recommendation .rate-service { flex: 1; }
		.rate-matrix .rate-table { margin-bottom: 0; table-layout: fixed; }
		.rate-matrix .rate-table th, .rate-matrix .rate-table td { padding: 4px 8px; vertical-align: middle; }
		.rate-matrix .rate-table thead th { font-weight: 600; background: var(--subtle-fg); }
		.rate-matrix .rate-carrier-col { width: 150px; }
		.rate-matrix .rate-carrier { font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
		.rate-matrix .rate-book-best { color: var(--green-700, #16794c); background: var(--green-100, #e4f5e9); border: 1px solid var(--green-200, #b6e3c4); font-weight: 600; }
		.rate-matrix .rate-class { font-size: 11px; font-weight: 500; padding: 0 5px; border-radius: 4px; background: var(--orange-highlight-color, #fff1e0); color: var(--orange-600, #b05a00); }
		.rate-matrix .rate-cell { display: flex; align-items: center; gap: 6px; min-width: 0; }
		.rate-matrix .rate-book { font-variant-numeric: tabular-nums; flex: none; }
		.rate-matrix .rate-service { color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
		.rate-matrix .rate-more { flex: none; font-size: 11px; color: var(--text-muted); }
		.rate-matrix .rate-star { flex: none; color: var(--blue-500, #2490ef); }
		.rate-matrix .rate-cheapest { background: var(--green-highlight-color, rgba(40, 167, 69, .08)); }
		.rate-matrix .rate-all { margin-top: 6px; }
		.rate-matrix .rate-all summary { cursor: pointer; display: flex; justify-content: space-between; font-size: 12px; }
		.rate-matrix .rate-all summary::before { content: "▸"; margin-right: 4px; }
		.rate-matrix .rate-all[open] summary::before { content: "▾"; }
		.rate-matrix .rate-all summary span:first-child { margin-right: auto; }
		.rate-matrix .rate-all summary::-webkit-details-marker { display: none; }
		.rate-matrix .rate-list { width: 100%; margin: 6px 0 0; }
		.rate-matrix .rate-list td { padding: 3px 8px; white-space: nowrap; vertical-align: middle; }
		.rate-matrix .rate-list td:nth-child(3) { width: 100%; white-space: normal; }
	</style>`;
};

cur_frm.select_from_available_services = function(frm, available_services) {
	const matrix = cur_frm.build_rate_matrix(available_services);
	if (!matrix.rates.length) {
		frappe.msgprint({ title: __("Error"), indicator: "red", message: __("No Shipment Services available") });
		return;
	}
	const d = new frappe.ui.Dialog({
		title: __("Compare shipping rates"),
		size: "large",
		fields: [
			{
				fieldtype:'HTML',
				fieldname:"available_services",
				label: __('Available Services')
			}
		]
	});
	d.fields_dict.available_services.$wrapper.html(cur_frm.render_rate_matrix(matrix));
	d.$wrapper.on("click", "[data-rate]", (event) => {
		cur_frm.select_row(matrix.rates[Number(event.currentTarget.dataset.rate)]);
	});
	let shipment_notific_email = [];
	let tracking_notific_email = [];
	(frm.doc.shipment_notification_subscriptions || []).forEach((d) => {
		if (!d.unsubscribed) {
			shipment_notific_email.push(d.email)
		}
	});
	(frm.doc.shipment_status_update_subscriptions || []).forEach((d) => {
		if (!d.unsubscribed) {
			tracking_notific_email.push(d.email)
		}
	});
	let delivery_notes = [];
	(frm.doc.shipment_delivery_notes || []).forEach((d) => {
			delivery_notes.push(d.delivery_note)
	});
	cur_frm.select_row = function(service_data){
		frappe.call({
			method: "shipment.shipment.doctype.shipment.shipment.create_shipment",
			freeze: true,
			freeze_message: __("Creating Shipment"),
			args: {
				shipment: frm.doc.name,
				pickup_from_type: frm.doc.pickup_from_type,
				delivery_to_type: frm.doc.delivery_to_type,
				pickup_address_name: frm.doc.pickup_address_name,
				delivery_address_name: frm.doc.delivery_address_name,
				shipment_parcel: frm.doc.shipment_parcel,
				description_of_content: frm.doc.description_of_content,
				pickup_date: frm.doc.pickup_date,
				pickup_contact_name: frm.doc.pickup_contact_name,
				delivery_contact_name: frm.doc.delivery_contact_name,
				value_of_goods: frm.doc.value_of_goods,
				service_data: service_data,
				shipment_notific_email: shipment_notific_email,
				tracking_notific_email: tracking_notific_email,
				pickup_type: frm.doc.pickup_type,
				delivery_notes: delivery_notes
			},
			callback: function(r) {
				if (!r.exc) {
					frm.reload_doc();
					if (r.message && r.message.service_provider && r.message.shipment_id) {
						frappe.msgprint(__("Shipment created with {0}, ID is {1}", [r.message.service_provider, r.message.shipment_id]))
						frm.events.update_tracking(frm, r.message.service_provider, r.message.shipment_id);
					}
				}
			}
		})
		d.hide()
	}
	d.show();
}

const set_presets = (preset) => {
    var frm = cur_frm
    frappe.model.with_doc("Shipment Parcel Preset",preset, () => {
        let parcel_preset = frappe.model.get_doc("Shipment Parcel Preset", preset);
        let row = frappe.model.add_child(frm.doc, "Shipment Parcel", "shipment_parcel");
        row.length = parcel_preset.length
        row.width = parcel_preset.width
        row.height = parcel_preset.height
        row.weight = parcel_preset.weight
        frm.refresh_fields("shipment_parcel")
    }
)}
