#!/usr/bin/env python

"""Generate ``links.pdf`` and ``links-rotated-90.pdf`` in this directory.

Both files contain three pages. Page 1 carries eleven ``Link`` annotations
exercising: external URIs, direct destinations, ``GoTo`` actions, named
destinations (both the name-tree and the catalog ``Dests`` styles),
multi-line ``QuadPoints``, overlapping quads, a ``Rect``-only fallback,
broken references, and unsupported actions. The rotated variant is
identical except every page has ``/Rotate 90``.

Run from anywhere:  python tests/pdfs/make_links.py
"""

import os

HERE = os.path.abspath(os.path.dirname(__file__))

# Courier at 12pt has a fixed advance of 600/1000 em = 7.2pt, so the
# horizontal extent of every text run is exactly computable.
ADV = 7.2
X0 = 72

# (baseline y, text) for each line drawn on page 1
LINES = [
    (700, "URI: visit example dot com now"),
    (680, "See the full appendix table"),
    (660, "for all details here"),
    (640, "Jump to named section"),
    (620, "Old style named dest"),
    (600, "This goes nowhere fast"),
    (580, "Launch the thing"),
    (560, "Remote file link"),
    (540, "Same target again"),
    (520, "Overlap dedupe test"),
    (500, "No action here"),
    (480, "Bad object target"),
]

URI = "https://example.com/docs?a=1&b=2"


def num(x):
    """Format a number for PDF output."""
    if float(x) == int(x):
        return str(int(x))
    return f"{x:.2f}".rstrip("0").rstrip(".")


def rect_for(y, text, x0=X0):
    """Annotation Rect covering ``text`` drawn at baseline ``y``."""
    w = ADV * len(text)
    return [x0, y - 3, x0 + w, y + 9]


def quad_for(y, text, x0=X0):
    """One QuadPoints quadrilateral (TL, TR, BL, BR) covering ``text``."""
    w = ADV * len(text)
    return [x0, y + 9, x0 + w, y + 9, x0, y - 3, x0 + w, y - 3]


def fmt_array(values):
    return "[" + " ".join(num(v) for v in values) + "]"


def link_annot(y, text, extra, rect=None, quads=None):
    """Assemble one Link annotation dictionary covering ``text``."""
    parts = ["<< /Type /Annot /Subtype /Link /Rect"]
    parts.append(fmt_array(rect if rect is not None else rect_for(y, text)))
    if quads is not None:
        parts.append(f"/QuadPoints {fmt_array(quads)}")
    parts.append(f"/Border [0 0 0] {extra} >>")
    return " ".join(parts)


def build_annots():
    """Return the annotation dictionaries (page-1 objects 10..20)."""
    uri_rect = rect_for(700, "visit example dot com", x0=X0 + 5 * ADV)
    multi_quads = quad_for(680, "See the full appendix table") + quad_for(
        660, "for all details"
    )
    overlap_quads = quad_for(520, "Overlap") + quad_for(520, "Overlap dedupe")
    goto_xyz = "/A << /S /GoTo /D [6 0 R /XYZ 72 720 null] >>"
    return [
        # 10: external URI, Rect only (no QuadPoints)
        link_annot(700, None, f"/A << /S /URI /URI ({URI}) >>", rect=uri_rect),
        # 11: GoTo action with a direct destination, two-line QuadPoints
        link_annot(
            680,
            None,
            goto_xyz,
            rect=[72, 657, 266.4, 689],
            quads=multi_quads,
        ),
        # 12: named destination via the catalog name tree
        link_annot(640, "Jump to named section", "/Dest (sec.appendix)"),
        # 13: named destination via the (pre-1.2) catalog Dests dictionary
        link_annot(620, "Old style named dest", "/Dest (old.chapter)"),
        # 14: broken: named destination does not exist
        link_annot(600, "This goes nowhere fast", "/Dest (missing.dest)"),
        # 15: unsupported action type
        link_annot(580, "Launch the thing", "/A << /S /Launch /F (run.exe) >>"),
        # 16: unsupported remote GoTo
        link_annot(
            560,
            "Remote file link",
            "/A << /S /GoToR /F (other.pdf) /D [0 /Fit] >>",
        ),
        # 17: direct destination array on the annotation itself;
        #     same target as annotation 11 (records must not be merged)
        link_annot(540, "Same target again", "/Dest [6 0 R /XYZ 72 720 null]"),
        # 18: overlapping quads — shared characters count once
        link_annot(
            520,
            None,
            "/A << /S /GoTo /D [5 0 R /Fit] >>",
            rect=[72, 517, 172.8, 529],
            quads=overlap_quads,
        ),
        # 19: broken: neither action nor destination
        link_annot(500, "No action here", ""),
        # 20: broken: destination references a non-page object (the font)
        link_annot(480, "Bad object target", "/A << /S /GoTo /D [3 0 R /Fit] >>"),
    ]


def content_stream(lines):
    parts = [f"BT /F1 12 Tf {X0} {y} Td ({text}) Tj ET" for y, text in lines]
    return "\n".join(parts) + "\n"


def build_pdf(rotation=0):
    rotate = f"/Rotate {rotation} " if rotation else ""
    objects = {}

    objects[1] = (
        "<< /Type /Catalog /Pages 2 0 R"
        " /Names << /Dests << /Names [ (sec.appendix) [6 0 R /FitH 700] ] >> >>"
        " /Dests << /old.chapter [5 0 R /Fit] >>"
        " >>"
    )
    objects[2] = "<< /Type /Pages /Kids [4 0 R 5 0 R 6 0 R] /Count 3 >>"
    objects[3] = "<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>"

    annots = build_annots()
    annot_refs = " ".join(f"{10 + i} 0 R" for i in range(len(annots)))

    page_tpl = (
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        + rotate
        + "/Resources << /Font << /F1 3 0 R >> >> /Contents {contents} 0 R{annots} >>"
    )
    objects[4] = page_tpl.format(contents=7, annots=f" /Annots [{annot_refs}]")
    objects[5] = page_tpl.format(contents=8, annots="")
    objects[6] = page_tpl.format(contents=9, annots="")

    for objid, body in [
        (7, content_stream(LINES)),
        (8, content_stream([(700, "Chapter one target page")])),
        (9, content_stream([(720, "Appendix target")])),
    ]:
        data = body.encode("ascii")
        objects[objid] = (
            f"<< /Length {len(data)} >>\nstream\n".encode("ascii") + data + b"endstream"
        )

    for i, annot in enumerate(annots):
        objects[10 + i] = annot

    # Assemble the file, tracking byte offsets for the xref table.
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for objid in sorted(objects):
        offsets[objid] = len(out)
        out += f"{objid} 0 obj\n".encode("ascii")
        body = objects[objid]
        out += body if isinstance(body, bytes) else body.encode("ascii")
        out += b"\nendobj\n"

    startxref = len(out)
    size = max(objects) + 1
    out += f"xref\n0 {size}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for objid in range(1, size):
        out += f"{offsets[objid]:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {size} /Root 1 0 R >>\n" f"startxref\n{startxref}\n%%EOF\n"
    ).encode("ascii")
    return bytes(out)


if __name__ == "__main__":
    for rotation, name in [(0, "links.pdf"), (90, "links-rotated-90.pdf")]:
        path = os.path.join(HERE, name)
        with open(path, "wb") as f:
            f.write(build_pdf(rotation=rotation))
        print(f"wrote {path}")
