frappe.listview_settings['Shipment'] = {
	add_fields: ["status"],
	get_indicator: function(doc) {
		if (doc.status == 'Booking Pending') return [__('Booking Pending'), 'orange'];
		if (doc.status == 'Booking Failed') return [__('Booking Failed'), 'red'];
		if(doc.status=='Booked') {
			return [__("Booked"), "green"];
		}
	}
};