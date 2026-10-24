"""Test the LetMeShip address fitting and tracking link helpers."""

import unittest
from unittest.mock import Mock, patch

import frappe
from frappe import ValidationError, _dict

from shipment.api.utils import (
	LETMESHIP_STREET_LIMIT,
	_pack_two_slots,
	_split_at_word_boundary,
	fit_letmeship_address,
	format_tracking_url,
	get_tracking_url,
	validate_letmeship_address,
)


class TestSplitAtWordBoundary(unittest.TestCase):
	"""Verify the word-boundary splitter."""

	def test_short_text_is_returned_whole(self):
		"""Keep text that already fits in the head."""
		self.assertEqual(_split_at_word_boundary("Main St 1", 35), ("Main St 1", ""))

	def test_splits_on_the_last_space_within_the_limit(self):
		"""Break between words rather than inside one."""
		self.assertEqual(_split_at_word_boundary("aaa bbb ccc", 8), ("aaa bbb", "ccc"))

	def test_space_just_after_the_limit_is_a_valid_split(self):
		"""Accept a head that ends exactly at the limit."""
		self.assertEqual(_split_at_word_boundary("aaaaa bbbbb", 5), ("aaaaa", "bbbbb"))

	def test_hard_split_without_whitespace(self):
		"""Cut at the limit when no word boundary exists."""
		self.assertEqual(_split_at_word_boundary("abcdefghij", 4), ("abcd", "efghij"))


class TestPackTwoSlots(unittest.TestCase):
	"""Verify the two-slot packer."""

	def test_empty_parts_give_empty_slots(self):
		"""Return empty slots when there is nothing to pack."""
		self.assertEqual(_pack_two_slots(["", None], 10), ("", ""))

	def test_parts_are_joined_with_comma(self):
		"""Join the parts into the first slot when they fit."""
		self.assertEqual(_pack_two_slots(["Hall 3", "Dock 2"], 35), ("Hall 3, Dock 2", ""))

	def test_overflow_goes_to_second_slot_unsplit(self):
		"""Keep the overflow verbatim so validation can reject it."""
		slot1, slot2 = _pack_two_slots(["aaa bbb", "ccc ddd eee"], 8)
		self.assertEqual(slot1, "aaa bbb,")
		self.assertEqual(slot2, "ccc ddd eee")


class TestFitLetmeshipAddress(unittest.TestCase):
	"""Verify that addresses are fitted to the LetMeShip limits."""

	def test_short_address_is_normalized_and_fits(self):
		"""Collapse whitespace and report success."""
		address = _dict(address_line1="  Main\tStreet   1 ", address_line2=" Hall  2 ", address_line1_con="old")
		self.assertTrue(fit_letmeship_address(address))
		self.assertEqual(address.address_line1, "Main Street 1")
		self.assertEqual(address.address_line2, "Hall 2")
		self.assertEqual(address.address_line1_con, "")

	def test_long_address_without_auto_split_fails(self):
		"""Leave overlong lines for validation when splitting is disabled."""
		address = _dict(address_line1="x" * 40, address_line2="")
		self.assertFalse(fit_letmeship_address(address, auto_split=False))
		self.assertEqual(address.address_line1, "x" * 40)

	def test_long_street_overflows_into_address_line2(self):
		"""Move the overflow of line 1 into the free second line."""
		street = "Very Long Street Name Number 1234567890 Hall 4"
		address = _dict(name="ADDR-1", address_line1=street, address_line2="")
		with patch("shipment.api.utils.frappe.logger"):
			self.assertTrue(fit_letmeship_address(address, role="pickup"))
		self.assertLessEqual(len(address.address_line1), LETMESHIP_STREET_LIMIT)
		self.assertEqual(address.address_line1_con, "")
		self.assertEqual(f"{address.address_line1} {address.address_line2}", street)

	def test_overflow_and_line2_use_both_info_slots(self):
		"""Spread the overflow and line 2 over the two info fields."""
		address = _dict(
			name="ADDR-2",
			address_line1="Very Long Street Name Number 1234567890 Hall 4",
			address_line2="Building B, second floor, room 12",
		)
		with patch("shipment.api.utils.frappe.logger"):
			self.assertTrue(fit_letmeship_address(address))
		self.assertTrue(address.address_line1_con)
		self.assertTrue(address.address_line2)

	def test_unfittable_address_reports_failure(self):
		"""Return False when the overflow still exceeds the limit."""
		address = _dict(name="ADDR-3", address_line1="a" * 30 + " " + "b" * 30, address_line2="c " * 40)
		with patch("shipment.api.utils.frappe.logger"):
			self.assertFalse(fit_letmeship_address(address))

	def test_audit_logging_failure_never_raises(self):
		"""Survive a broken logger while splitting."""
		address = _dict(name="ADDR-4", address_line1="x" * 20 + " " + "y" * 30, address_line2="")
		with patch("shipment.api.utils.frappe.logger", side_effect=RuntimeError("no site")):
			self.assertTrue(fit_letmeship_address(address))


class TestValidateLetmeshipAddress(unittest.TestCase):
	"""Verify the user-facing length validation."""

	def test_fitting_address_passes(self):
		"""Accept an address within the limits."""
		validate_letmeship_address(_dict(name="A", address_line1="x" * 35, address_line2=""), "pickup")

	def test_overlong_line_throws_with_escaped_link(self):
		"""Reject an overlong line and escape the address name."""
		address = _dict(name="A&B/<x>", address_line1="x" * 36, address_line2="")
		with self.assertRaises(ValidationError) as ctx:
			validate_letmeship_address(address, "delivery")
		message = str(ctx.exception)
		self.assertIn("A%26B%2F%3Cx%3E", message)
		self.assertNotIn("<x>", message)

	def test_overlong_continuation_throws(self):
		"""Reject an overlong continuation line."""
		address = _dict(name="A", address_line1="x", address_line2="y", address_line1_con="z" * 36)
		with self.assertRaises(ValidationError):
			validate_letmeship_address(address, "pickup")


class TestTrackingUrl(unittest.TestCase):
	"""Verify tracking link rendering."""

	def test_rejects_unsafe_and_empty_urls(self):
		"""Return nothing for non-http schemes and missing hosts."""
		for value in (None, "", "javascript:alert(1)", "ftp://x.example/a", "https://", "no-scheme.example"):
			self.assertEqual(format_tracking_url(value), "", value)

	def test_escapes_the_url_in_the_link(self):
		"""Escape quotes so the href cannot be broken out of."""
		html = format_tracking_url('https://t.example/?a="b"&c=1')
		self.assertIn('rel="noopener noreferrer"', html)
		self.assertNotIn('"b"', html)

	def test_get_tracking_url_renders_the_carrier_template(self):
		"""Render the carrier's URL template with the tracking number."""
		with (
			patch.object(frappe, "get_value", return_value="https://t.example/{{ tracking_number }}"),
			patch.object(frappe, "render_template", return_value="https://t.example/123") as render,
		):
			html = get_tracking_url("UPS", "123")
		render.assert_called_once_with("https://t.example/{{ tracking_number }}", {"tracking_number": "123"})
		self.assertIn("https://t.example/123", html)

	def test_get_tracking_url_without_template_is_empty(self):
		"""Return nothing when the carrier has no URL reference."""
		with patch.object(frappe, "get_value", return_value=None):
			self.assertEqual(get_tracking_url("UPS", "123"), "")


if __name__ == "__main__":
	unittest.main()


class TestGetContact(unittest.TestCase):
	"""get_contact must not fail for a contact without any phone number."""

	def test_contact_without_phone_or_mobile(self):
		from frappe import _dict

		from shipment.api import utils

		row = _dict(first_name="Erika", last_name="Muster", email_id="erika@example.test", phone=None, mobile_no=None, gender=None)
		db = Mock(get_value=Mock(return_value=row))
		with patch("shipment.api.utils.frappe.db", db):
			contact = utils.get_contact("CONTACT-1")
		self.assertEqual((contact.phone_prefix, contact.phone), ("", ""))
		self.assertEqual(contact.email, "erika@example.test")
