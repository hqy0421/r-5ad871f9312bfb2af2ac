#!/usr/bin/env python
import logging
import os
import unittest

import pytest

import pdfplumber

logging.disable(logging.ERROR)

HERE = os.path.abspath(os.path.dirname(__file__))
LINKS_PDF = os.path.join(HERE, "pdfs/links.pdf")
LINKS_ROTATED_PDF = os.path.join(HERE, "pdfs/links-rotated-90.pdf")


def by_anchor(links, anchor):
    matches = [r for r in links if r["anchor_text"] == anchor]
    assert len(matches) == 1
    return matches[0]


class TestLinks(unittest.TestCase):
    @classmethod
    def setup_class(self):
        self.pdf = pdfplumber.open(LINKS_PDF)
        self.links = self.pdf.links

    @classmethod
    def teardown_class(self):
        self.pdf.close()

    def test_one_record_per_link_annotation(self):
        # The fixture has 11 Link annotations on page 1 (and none elsewhere)
        assert len(self.links) == 11
        assert len(self.pdf.pages[0].links) == 11
        assert self.pdf.pages[1].links == []
        for record in self.links:
            assert record["object_type"] == "link"
            assert record["page_number"] == 1

    def test_pdf_links_aggregates_page_links(self):
        page_links = [r for p in self.pdf.pages for r in p.links]
        assert len(self.links) == len(page_links)

    def test_external_uri_preserved_as_is(self):
        record = by_anchor(self.links, "visit example dot com")
        assert record["target"] == {
            "kind": "external",
            "uri": "https://example.com/docs?a=1&b=2",
        }

    def test_goto_action_with_direct_destination(self):
        record = by_anchor(self.links, "See the full appendix table\nfor all details")
        assert record["target"] == {
            "kind": "internal",
            "page_number": 3,
            "fit": "XYZ",
            "left": 72,
            "top": 720,
            "zoom": None,
            "named": None,
        }

    def test_direct_destination_on_annotation(self):
        record = by_anchor(self.links, "Same target again")
        assert record["target"]["kind"] == "internal"
        assert record["target"]["page_number"] == 3
        assert record["target"]["fit"] == "XYZ"
        assert record["target"]["named"] is None

    def test_named_destination_via_name_tree(self):
        record = by_anchor(self.links, "Jump to named section")
        assert record["target"] == {
            "kind": "internal",
            "page_number": 3,
            "fit": "FitH",
            "top": 700,
            "named": "sec.appendix",
        }

    def test_named_destination_via_catalog_dests(self):
        record = by_anchor(self.links, "Old style named dest")
        assert record["target"] == {
            "kind": "internal",
            "page_number": 2,
            "fit": "Fit",
            "named": "old.chapter",
        }

    def test_multiline_link_uses_quadpoints(self):
        record = by_anchor(self.links, "See the full appendix table\nfor all details")
        # One quad per line; record bbox is their union
        assert len(record["quads"]) == 2
        assert record["quads"][0] == pytest.approx((72, 103, 266.4, 115))
        assert record["quads"][1] == pytest.approx((72, 123, 180, 135))
        assert (record["x0"], record["top"]) == (72, 103)
        assert (record["x1"], record["bottom"]) == (266.4, 135)
        assert record["doctop"] == record["top"]
        # y0/y1 use the bottom-origin coordinate system, as elsewhere
        assert record["y0"] == pytest.approx(792 - 135)
        assert record["y1"] == pytest.approx(792 - 103)

    def test_rect_fallback_when_no_quadpoints(self):
        record = by_anchor(self.links, "visit example dot com")
        assert len(record["quads"]) == 1
        quad = record["quads"][0]
        assert quad == pytest.approx(
            (record["x0"], record["top"], record["x1"], record["bottom"])
        )

    def test_overlapping_quads_count_chars_once(self):
        # Two quads both cover the word "Overlap"; its characters must
        # appear only once, and the same-line quads form one segment
        record = by_anchor(self.links, "Overlap dedupe")
        assert len(record["quads"]) == 2
        assert record["target"]["page_number"] == 2

    def test_broken_references_carry_reasons(self):
        record = by_anchor(self.links, "This goes nowhere fast")
        assert record["target"]["kind"] == "broken"
        assert "missing.dest" in record["target"]["reason"]

        record = by_anchor(self.links, "No action here")
        assert record["target"]["kind"] == "broken"
        assert "neither an action" in record["target"]["reason"]

        record = by_anchor(self.links, "Bad object target")
        assert record["target"]["kind"] == "broken"
        assert "page object" in record["target"]["reason"]

    def test_unsupported_actions_carry_reasons(self):
        record = by_anchor(self.links, "Launch the thing")
        assert record["target"]["kind"] == "unsupported"
        assert record["target"]["action"] == "Launch"
        assert record["target"]["reason"]

        record = by_anchor(self.links, "Remote file link")
        assert record["target"]["kind"] == "unsupported"
        assert record["target"]["action"] == "GoToR"

    def test_same_target_records_are_not_merged(self):
        target = {
            "kind": "internal",
            "page_number": 3,
            "fit": "XYZ",
            "left": 72,
            "top": 720,
            "zoom": None,
            "named": None,
        }
        matches = [r for r in self.links if r["target"] == target]
        assert len(matches) == 2
        assert {r["anchor_text"] for r in matches} == {
            "See the full appendix table\nfor all details",
            "Same target again",
        }

    def test_page_filter_does_not_break_targets(self):
        # Opening only page 1 must not break links targeting pages 2/3
        with pdfplumber.open(LINKS_PDF, pages=[1]) as pdf:
            links = pdf.links
            assert len(links) == 11
            internal = [r for r in links if r["target"]["kind"] == "internal"]
            assert len(internal) == 5
            target_pages = sorted(r["target"]["page_number"] for r in internal)
            assert target_pages == [2, 2, 3, 3, 3]

    def test_rotated_page(self):
        with pdfplumber.open(LINKS_ROTATED_PDF) as pdf:
            rotated = pdf.pages[0].links
        assert len(rotated) == len(self.links)
        for record, expected in zip(rotated, self.links):
            assert record["anchor_text"] == expected["anchor_text"]
            assert record["target"] == expected["target"]

    def test_cropped_page(self):
        page = self.pdf.pages[0]
        cropped = page.crop((0, 0, 612, 100))
        assert len(cropped.links) == 1
        assert cropped.links[0]["anchor_text"] == "visit example dot com"

    def test_non_link_annotations_produce_no_records(self):
        path = os.path.join(HERE, "pdfs/annotations.pdf")
        with pdfplumber.open(path) as pdf:
            assert len(pdf.annots) > 0
            assert pdf.links == []

    def test_existing_interfaces_unchanged(self):
        # The pre-existing annots/hyperlinks interfaces are unaffected
        assert len(self.pdf.annots) == 11
        assert len(self.pdf.hyperlinks) == 1
        assert self.pdf.hyperlinks[0]["uri"] == "https://example.com/docs?a=1&b=2"
