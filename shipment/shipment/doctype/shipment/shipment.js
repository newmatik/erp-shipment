// Copyright (c) 2020, Newmatik and contributors
// For license information, please see license.txt
var holidays = []
let deviceNow = new Date();
let currentTime = deviceNow.toTimeString().split(" ")[0]; // Get the time in HH:MM:SS format
let cutoffTime = "12:00:00"; 

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
                                const item = holidays[idx];
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
		}
		$('div[data-fieldname=pickup_address] > div > .clearfix').hide()
		$('div[data-fieldname=pickup_contact] > div > .clearfix').hide()
		$('div[data-fieldname=delivery_address] > div > .clearfix').hide()
		$('div[data-fieldname=delivery_contact] > div > .clearfix').hide()

		if (frm.doc.delivery_from_type != 'Company') {
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
			frm.set_df_property("delivery_contact_name", "reqd", 0);
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
							Please set Email/Phone for the contact <a href="#Form/Contact/${contact_name}">${contact_name}</a>`))
					}
					let contact_display = r.message.contact_display
					if (r.message.contact_email) {
						contact_display += '<br>' + r.message.contact_email
					}
					if (r.message.contact_phone) {
						contact_display += '<br>' + r.message.contact_phone
					}
					if (r.message.contact_mobile && !r.message.contact_phone) {
						contact_display += '<br>' + r.message.contact_mobile
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
			let contact_display = r.full_name
			if (r.email) {
				contact_display += '<br>' + r.email
			}
			if (r.phone) {
				contact_display += '<br>' + r.phone
			}
			if (r.mobile_no && !r.phone) {
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
		frm.trigger('clear_pickup_fields')
		if (frm.doc.pickup_customer) {
			frm.events.set_address_name(frm,'Customer',frm.doc.pickup_customer, 'Pickup')
			frm.events.set_contact_name(frm,'Customer',frm.doc.pickup_customer, 'Pickup')
		}
	},
	pickup_supplier: function(frm) {
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
            frm.set_value("pickup_date", frappe.datetime.add_days(frappe.datetime.get_today()));
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
	remove_email_row: function(frm, table, fieldname) {
		$.each(frm.doc[table] || [], function(i, detail) {
			if(detail.email === fieldname){
				cur_frm.get_field(table).grid.grid_rows[i].remove();
			}
		});
	},
	remove_notific_child_table: function(frm, table, delivery_type) {
		$.each(frm.doc[table] || [], function(i, detail) {
			if (detail.email != frm.doc.pickup_email ||  detail.email != frm.doc.delivery_email){
				cur_frm.get_field(table).grid.grid_rows[i].remove();
			}
		});
		frm.refresh_fields(table)
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
				},
				callback: function(r) {
					if (r.message) {
						cur_frm.select_from_available_services(frm, r.message)
					}
					else {
						frappe.throw(__("No Shipment Services available"));
					}
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
							let contact_display = r.full_name
							contact_display += '<br> service@eso-hygiene.com'
							contact_display += '<br>' + r.phone
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

cur_frm.select_from_available_services = function(frm, available_services) {
	var headers = [ __("Service Provider"), __("Carrier"), __("Carrier’s Service"), __("Price"), "" ]
	cur_frm.render_available_services = function(d, headers, data){
		d.fields_dict.available_services.$wrapper.html(
			frappe.render_template('shipment_service_selector',
				{'header_columns': headers, 'data': data}
			)
		)
	}
	const d = new frappe.ui.Dialog({
		title: __("Select Shipment Service to create Shipment"),
		fields: [
			{
				fieldtype:'HTML',
				fieldname:"available_services",
				label: __('Available Services')
			}
		]
	});
	cur_frm.render_available_services(d, headers, available_services)
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
					frappe.msgprint(__("Shipment created with {0}, ID is {1}", [r.message.service_provider, r.message.shipment_id]))
					frm.events.update_tracking(frm, r.message.service_provider, r.message.shipment_id);
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

