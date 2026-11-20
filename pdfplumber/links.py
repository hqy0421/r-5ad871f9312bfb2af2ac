"""Extract link annotations as normalized citation records.

This module powers ``Page.links`` and ``PDF.links``. For every annotation
of subtype ``Link`` it produces exactly one record containing the anchor
text found beneath the annotation, the source-page coordinates, and a
normalized target:

- Direct destinations, ``GoTo`` actions, and named destinations are all
  resolved to the actual 1-based page number in the original document —
  regardless of any ``pages=`` filter used when opening the PDF — plus the
  destination's positioning ("fit") mode and coordinates.
- External ``URI`` actions are preserved as-is.
- References that cannot be resolved are marked ``broken`` with a reason;
  action types pdfplumber does not follow (e.g. ``Launch``, ``GoToR``,
  ``JavaScript``) are marked ``unsupported`` with a reason.

Records are never merged: two annotations pointing at the same target
still yield two records.
"""

from math import atan2
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
    cast,
)
from weakref import WeakKeyDictionary

from pdfminer.pdfdocument import PDFDestinationNotFound
from pdfminer.pdfpage import PDFPage
from pdfminer.pdftypes import PDFObjRef, resolve1
from pdfminer.psparser import PSLiteral

from ._typing import T_num, T_obj, T_obj_list
from .utils import decode_text

if TYPE_CHECKING:  # pragma: nocover
    from .page import Page
    from .pdf import PDF

# Destination "fit" modes and their parameter names, per
# PDF 32000-1:2008, table 151 ("Destination syntax").
FIT_PARAMS: Dict[str, Tuple[str, ...]] = {
    "XYZ": ("left", "top", "zoom"),
    "Fit": (),
    "FitH": ("top",),
    "FitV": ("left",),
    "FitR": ("left", "bottom", "right", "top"),
    "FitB": (),
    "FitBH": ("top",),
    "FitBV": ("left",),
}

T_point = Tuple[T_num, T_num]
T_quad = Tuple[List[T_point], Tuple[T_num, T_num, T_num, T_num]]

# Cache of page-object-id -> 1-based page number maps, keyed by PDF.
_page_maps: "WeakKeyDictionary[PDF, Dict[int, int]]" = WeakKeyDictionary()


def _broken(reason: str) -> T_obj:
    return {"kind": "broken", "reason": reason}


def _decode_uri(value: Any) -> Optional[str]:
    """Decode a URI action entry, mirroring ``Page.annots``' decoding."""
    if isinstance(value, str):
        return value
    if not isinstance(value, bytes):
        return None
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return value.decode("utf-16")
        except UnicodeDecodeError:
            return None


def _get_page_map(pdf: "PDF") -> Dict[int, int]:
    """Map page-dictionary object IDs to 1-based page numbers.

    Built from *all* pages in the document, so that destinations resolve
    to their original page numbers even when the PDF was opened with a
    ``pages=`` filter that excludes the target page.
    """
    page_map = _page_maps.get(pdf)
    if page_map is None:
        page_map = {}
        for i, page in enumerate(PDFPage.create_pages(pdf.doc)):
            page_map[cast(int, page.pageid)] = i + 1
        _page_maps[pdf] = page_map
    return page_map


def _resolve_named_dest(pdf: "PDF", name: Union[str, bytes]) -> Any:
    """Look up a named destination, tolerating str/bytes key mismatches
    between the referencing annotation and the document's name tree."""
    candidates: List[Union[str, bytes]] = [name]
    if isinstance(name, str):
        candidates.append(name.encode("utf-8"))
    else:
        candidates.append(decode_text(name))
    last_err: Optional[Exception] = None
    for candidate in candidates:
        try:
            return pdf.doc.get_dest(candidate)
        except (PDFDestinationNotFound, KeyError, TypeError) as e:
            last_err = e
    raise PDFDestinationNotFound(name) from last_err


def _normalize_dest(pdf: "PDF", dest: Any, named: Optional[str] = None) -> T_obj:
    """Normalize a raw destination (array, or dict with a ``D`` entry)
    into an ``internal`` target record, or a ``broken`` one."""
    dest = resolve1(dest)
    if isinstance(dest, dict):
        dest = resolve1(dest.get("D"))
    if not isinstance(dest, (list, tuple)) or len(dest) == 0:
        return _broken(f"malformed destination: {dest!r}")

    page_ref = dest[0]
    if not isinstance(page_ref, PDFObjRef):
        return _broken("destination does not reference a page object")
    page_number = _get_page_map(pdf).get(page_ref.objid)
    if page_number is None:
        return _broken("destination page not found in document")

    fit = "Fit"
    if len(dest) > 1:
        mode = dest[1]
        if isinstance(mode, PSLiteral):
            mode = mode.name
        if isinstance(mode, bytes):
            fit = decode_text(mode)
        elif isinstance(mode, str):
            fit = mode
        else:
            return _broken(f"malformed destination fit mode: {mode!r}")
    if fit not in FIT_PARAMS:
        return _broken(f"unknown destination fit mode: {fit!r}")

    target: T_obj = {
        "kind": "internal",
        "page_number": page_number,
        "fit": fit,
    }
    values = dest[2:]
    for i, param in enumerate(FIT_PARAMS[fit]):
        target[param] = values[i] if i < len(values) else None
    target["named"] = named
    return target


def _resolve_dest_value(pdf: "PDF", dest: Any) -> T_obj:
    """Resolve a destination value — a direct array/dict or a named
    destination (string, bytes, or name object) — to a target record."""
    if isinstance(dest, (list, tuple, dict)):
        return _normalize_dest(pdf, dest)
    name = dest.name if isinstance(dest, PSLiteral) else dest
    if not isinstance(name, (str, bytes)):
        return _broken(f"unrecognized destination: {dest!r}")
    decoded = decode_text(name) if isinstance(name, bytes) else name
    try:
        resolved = _resolve_named_dest(pdf, name)
    except (PDFDestinationNotFound, KeyError, TypeError):
        return _broken(f"named destination not found: {decoded!r}")
    return _normalize_dest(pdf, resolved, named=decoded)


def _resolve_target(pdf: "PDF", annot: T_obj) -> T_obj:
    """Build the normalized target record for a resolved raw ``Link``
    annotation dictionary."""
    action = annot.get("A")
    if action is not None:
        if not isinstance(action, dict):
            return _broken(f"malformed action: {action!r}")
        s = action.get("S")
        if isinstance(s, PSLiteral):
            s = s.name
        s_name = decode_text(s) if isinstance(s, bytes) else s
        if s_name == "GoTo":
            if "D" not in action:
                return _broken("GoTo action has no destination (D)")
            return _resolve_dest_value(pdf, action["D"])
        if s_name == "URI":
            uri = _decode_uri(action.get("URI"))
            if uri is None:
                return _broken("URI action has no decodable URI")
            return {"kind": "external", "uri": uri}
        if s_name is None:
            return _broken("link action has no action type (S)")
        return {
            "kind": "unsupported",
            "action": str(s_name),
            "reason": f"action type not supported: {s_name}",
        }
    if annot.get("Dest") is not None:
        return _resolve_dest_value(pdf, annot["Dest"])
    return _broken("link annotation has neither an action (A) nor a destination (Dest)")


def _rotate_point(pt: T_point, rotation: int, width: T_num, height: T_num) -> T_point:
    """Rotate a point from the page's default user space into display
    space. Uses the same math ``Page.annots`` applies to annotation
    Rects, so quads stay consistent with the existing annotations."""
    turns = rotation // 90
    for i in range(turns):
        x, y = pt
        comp = width if i == turns % 2 else height
        pt = (y, (comp - x))
    return pt


def _quad_to_points(page: "Page", quad: Sequence[T_num]) -> List[T_point]:
    """Convert one QuadPoints quadrilateral (8 numbers, PDF user space)
    into 4 points in pdfplumber's top-left-origin coordinate system."""
    rh = page.root_page.height
    points = []
    for i in range(0, 8, 2):
        x, y = _rotate_point(
            (quad[i], quad[i + 1]), page.rotation, page.width, page.height
        )
        points.append((x, rh - y))
    return _order_quad_points(points)


def _order_quad_points(points: List[T_point]) -> List[T_point]:
    """Order a quadrilateral's vertices into a perimeter ring.

    QuadPoints vertices arrive as (top-left, top-right, bottom-left,
    bottom-right) — not a polygon traversal order — so sort them by
    angle around the centroid to get a proper ring for the
    point-in-polygon test."""
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    return sorted(points, key=lambda p: atan2(p[1] - cy, p[0] - cx))


def _points_bbox(points: Sequence[T_point]) -> Tuple[T_num, T_num, T_num, T_num]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _get_quads(page: "Page", annot: T_obj, parsed: T_obj) -> List[T_quad]:
    """Return the link's clickable regions as (points, bbox) pairs.

    Multi-line links describe each line with its own QuadPoints
    quadrilateral; those are used as-is. Only when QuadPoints is absent
    or malformed do we fall back to the annotation's Rect.
    """
    qp = annot.get("QuadPoints")
    quads: List[T_quad] = []
    if (
        isinstance(qp, (list, tuple))
        and len(qp) >= 8
        and len(qp) % 8 == 0
        and all(isinstance(v, (int, float)) for v in qp)
    ):
        for i in range(0, len(qp), 8):
            points = _quad_to_points(page, qp[i : i + 8])
            quads.append((points, _points_bbox(points)))
    if not quads:
        x0, top, x1, bottom = (
            parsed["x0"],
            parsed["top"],
            parsed["x1"],
            parsed["bottom"],
        )
        points = [(x0, top), (x1, top), (x1, bottom), (x0, bottom)]
        quads.append((points, (x0, top, x1, bottom)))
    return quads


def _point_in_polygon(x: T_num, y: T_num, points: Sequence[T_point]) -> bool:
    """Ray-casting point-in-polygon test."""
    inside = False
    j = len(points) - 1
    for i in range(len(points)):
        xi, yi = points[i]
        xj, yj = points[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _extract_anchor_text(chars: T_obj_list, quads: List[T_quad]) -> str:
    """Extract the text beneath a link.

    Each quad usually covers one line of the link, so the result joins
    per-line segments with newlines. Consecutive quads lying on the same
    line (their ranges overlap along the text direction's perpendicular
    axis) are merged first. A character covered by overlapping quads is
    included only once.
    """
    seen: Set[int] = set()
    lines: List[Dict[str, Any]] = []
    for points, bbox in quads:
        bx0, btop, bx1, bbottom = bbox
        horizontal = (bx1 - bx0) >= (bbottom - btop)
        line = None
        if lines and horizontal == lines[-1]["horizontal"]:
            last = lines[-1]
            if horizontal:
                same_line = min(last["bottom"], bbottom) > max(last["top"], btop)
            else:
                same_line = min(last["x1"], bx1) > max(last["x0"], bx0)
            if same_line:
                line = last
        if line is None:
            line = {
                "horizontal": horizontal,
                "x0": bx0,
                "top": btop,
                "x1": bx1,
                "bottom": bbottom,
                "chars": [],
            }
            lines.append(line)
        else:
            line["x0"] = min(line["x0"], bx0)
            line["top"] = min(line["top"], btop)
            line["x1"] = max(line["x1"], bx1)
            line["bottom"] = max(line["bottom"], bbottom)
        for c in chars:
            if id(c) in seen:
                continue
            cx = (c["x0"] + c["x1"]) / 2
            cy = (c["top"] + c["bottom"]) / 2
            if cx < bx0 or cx > bx1 or cy < btop or cy > bbottom:
                continue
            if _point_in_polygon(cx, cy, points):
                seen.add(id(c))
                line["chars"].append(c)
    segments = []
    for line in lines:
        line["chars"].sort(key=lambda c: (c["top"], c["x0"]))
        text = "".join(c["text"] for c in line["chars"])
        if text:
            segments.append(text)
    return "\n".join(segments)


def get_links(page: "Page") -> T_obj_list:
    """Build one link record per ``Link`` annotation on the page."""
    links = []
    for parsed in page.annots:
        annot = parsed["data"]
        subtype = annot.get("Subtype")
        if isinstance(subtype, PSLiteral):
            subtype = subtype.name
        if isinstance(subtype, bytes):
            subtype = decode_text(subtype)
        if subtype != "Link":
            continue
        quads = _get_quads(page, annot, parsed)
        x0 = min(q[1][0] for q in quads)
        top = min(q[1][1] for q in quads)
        x1 = max(q[1][2] for q in quads)
        bottom = max(q[1][3] for q in quads)
        rh = page.root_page.height
        links.append(
            {
                "object_type": "link",
                "page_number": page.page_number,
                "anchor_text": _extract_anchor_text(page.chars, quads),
                "x0": x0,
                "top": top,
                "x1": x1,
                "bottom": bottom,
                "y0": rh - bottom,
                "y1": rh - top,
                "doctop": page.initial_doctop + top,
                "width": x1 - x0,
                "height": bottom - top,
                "quads": [q[1] for q in quads],
                "target": _resolve_target(page.pdf, annot),
            }
        )
    return links
