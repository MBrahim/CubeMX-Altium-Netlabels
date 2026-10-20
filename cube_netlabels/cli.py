#!/usr/bin/env python3
"""
Place net labels on an MCU symbol in an Altium .SchDoc from an STM32CubeMX pinout CSV.

For every CSV row that has a net name:
    pin number (CSV "Position")  ->  pin of the component (--designator)
    draws a short wire stub out of the pin, puts a Net Label at the stub end.

Also places a generic No ERC directive on each unconnected, non-power pin of
the selected designator.

Usage
-----
  pip install -r requirements-gui.txt  # PySide6 / Qt 6 GUI
  python -m cube_netlabels --gui
  pip install altium-monkey
  python -m cube_netlabels pinout.csv board.SchDoc out.SchDoc --designator U1 --dry-run
  python -m cube_netlabels pinout.csv board.SchDoc out.SchDoc --designator U1 --svg check.svg
  python -m cube_netlabels --list-designators board.SchDoc
  python -m cube_netlabels --inspect board.SchDoc --designator U1     # dump API names

Always writes to a NEW file; your original is never touched.
"""
import argparse
import csv
import json
import math
import re
import sys

# --------------------------------------------------------------------------
# 1. CSV  (pure python, no altium_monkey needed)
# --------------------------------------------------------------------------
# CubeMX "Pinout" CSV columns: Position, Name, Type, Signal, Label, (Mode)
#   Position = package pin number/ball (what Altium calls the pin Designator)
#   Name     = port pin (PA9)        Signal = peripheral signal (USART1_TX)
#   Label    = user label you typed in CubeMX
# Generic signals that make poor net names:
GENERIC = re.compile(r"^(GPIO_(Input|Output|Analog)|n/?a|-)$", re.I)


def clean_net(name: str) -> str:
    name = name.strip().replace(" ", "_")
    return re.sub(r"[^\w\-\.\+\\/\[\]]", "_", name)


def read_cube_csv(path, column="Label", fallback_signal=False, fallback_name=False):
    with open(path, newline="", encoding="utf-8-sig") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.DictReader(f, dialect=dialect))
    if not rows:
        sys.exit("CSV is empty")
    cols = {k.strip().lower(): k for k in rows[0].keys() if k}
    for needed in ("position", column.lower()):
        if needed not in cols:
            sys.exit(f"CSV has no '{needed}' column. Found: {list(cols.values())}")

    out, seen = {}, {}
    for r in rows:
        pin = (r[cols["position"]] or "").strip()
        net = (r[cols[column.lower()]] or "").strip()
        if not net and fallback_signal and "signal" in cols:
            s = (r[cols["signal"]] or "").strip()
            net = "" if GENERIC.match(s) else s
        if not net and fallback_name and "name" in cols:
            net = (r[cols["name"]] or "").strip()
        if not pin or not net or GENERIC.match(net):
            continue
        net = clean_net(net)
        if pin in out:
            print(f"WARN: pin {pin} appears twice, keeping first ({out[pin]})")
            continue
        out[pin] = net
        seen.setdefault(net, []).append(pin)
    for net, pins in seen.items():
        if len(pins) > 1:
            print(f"NOTE: net '{net}' is on several pins {pins} (they will be shorted!)")
    return out


# --------------------------------------------------------------------------
# 2. altium_monkey adapter -- the ONLY part that touches the library.
# --------------------------------------------------------------------------
import inspect as _inspect


def public(o):
    return [a for a in dir(o) if not a.startswith("_")]


def text_of(v):
    """designator objects may be str or an object with .text"""
    for n in ("text", "value", "name"):
        if hasattr(v, n) and isinstance(getattr(v, n), str):
            return getattr(v, n)
    return v if isinstance(v, str) else None


def comp_designator(c):
    for n in ("designator", "designator_text", "designator_value"):
        if hasattr(c, n):
            t = text_of(getattr(c, n))
            if t:
                return t.strip()
    for ch in list(getattr(c, "children", []) or []):
        if "esignator" in type(ch).__name__:
            t = text_of(ch)
            if t:
                return t.strip()
    for p in list(getattr(c, "parameters", []) or []):
        nm = text_of(getattr(p, "name", ""))
        if nm and nm.lower() == "designator":
            return (text_of(getattr(p, "text", getattr(p, "value", ""))) or "").strip()
    return None


def find_component(schdoc, designator):
    comps = list(schdoc.components)
    found = []
    for c in comps:
        d = comp_designator(c)
        found.append(d)
        if d and d.upper() == designator.upper():
            return c
    print(f"Component '{designator}' not found. Designators seen: {found}")
    if comps and not any(found):
        c = comps[0]
        print("Could not read any designator. First component children types:",
              [type(x).__name__ for x in (c.children or [])])
        print("parameters:", [(type(p).__name__, public(p)[:12]) for p in list(c.parameters)[:5]])
    sys.exit(1)


def find_components(schdoc, designator):
    """All placed parts sharing a designator (U1A, U1B... are separate instances)."""
    parts = [c for c in schdoc.components
             if (comp_designator(c) or "").upper() == designator.upper()]
    if not parts:
        find_component(schdoc, designator)   # prints diagnostics and exits
    return parts


def list_designators(schdoc):
    """Return the unique, readable designators of components placed in a schematic."""
    return sorted(
        {designator for component in schdoc.components
         if (designator := comp_designator(component))},
        key=str.casefold,
    )


def part_pins(comp):
    """Pins that belong to THIS placed part (owner_part_id == current part,
    or -1 = shared by all parts)."""
    cur = getattr(comp, "current_part_id", None)
    out = []
    for p in comp.pins:
        pid = getattr(p, "owner_part_id", None)
        if pid is None or cur is None or pid == cur or pid == -1:
            out.append(p)
    return out


def pin_number(p):
    for n in ("designator", "pin_designator", "number", "designator_text"):
        if hasattr(p, n):
            t = text_of(getattr(p, n))
            if t:
                return t.strip()
    sys.exit(f"Cannot read pin number. Pin attrs: {public(p)}")


def xy(v):
    if hasattr(v, "x") and hasattr(v, "y"):
        return float(v.x), float(v.y)
    if hasattr(v, "x_mils"):
        return float(v.x_mils), float(v.y_mils)
    return float(v[0]), float(v[1])


def xy_mils(v):
    if hasattr(v, "x_mils"):
        return float(v.x_mils), float(v.y_mils)
    return float(v[0]), float(v[1])


def hotspot(comp, pin):
    try:
        return xy(comp.get_pin_hotspot(pin_number(pin) if not isinstance(pin, str) else pin))
    except Exception as e:
        sys.exit(f"get_pin_hotspot failed: {e!r}\nsignature: "
                 f"{_inspect.signature(comp.get_pin_hotspot)}\n"
                 f"doc: {comp.get_pin_hotspot.__doc__}")


def outward_angles(hot):
    """hot: {pin_number: (x,y)}. A pin belongs to the side of the hot-end
    bounding box it is closest to -> stub goes that way. No rotation maths."""
    xs = [p[0] for p in hot.values()]; ys = [p[1] for p in hot.values()]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    res = {}
    for n, (x, y) in hot.items():
        d = {180: x - x0, 0: x1 - x, 270: y - y0, 90: y1 - y}
        res[n] = min(d, key=d.get)
    return res


def pt(x, y):
    """Build an altium_monkey SchPointMils from mils."""
    import altium_monkey as am
    P = getattr(am, "SchPointMils", None)
    if P is None:
        sys.exit("altium_monkey has no SchPointMils; names: "
                 f"{[n for n in dir(am) if 'Point' in n]}")
    for build in (lambda: P(x, y), lambda: P(x=x, y=y),
                  lambda: P.from_mils(x, y), lambda: P(x_mils=x, y_mils=y)):
        try:
            return build()
        except Exception:
            pass
    sys.exit(f"Cannot construct SchPointMils. Signature: {_inspect.signature(P)}\n"
             f"Doc: {P.__doc__}")


def _call(factory_name, attempts):
    import altium_monkey as am
    f = getattr(am, factory_name, None)
    if f is None:
        sys.exit(f"altium_monkey has no '{factory_name}'. Related names: "
                 f"{[n for n in dir(am) if re.search('wire|label|net', n, re.I)]}")
    errors = []
    for kw in attempts:
        try:
            return f(**kw)
        except Exception as e:
            errors.append(f"{list(kw)} -> {type(e).__name__}: {e}")
    sys.exit(f"{factory_name} failed for every guess:\n  " + "\n  ".join(errors) +
             f"\nReal signature: {_inspect.signature(f)}\nDoc: {f.__doc__}")


def make_wire(p1, p2):
    import altium_monkey as am

    return _call("make_sch_wire", [
        dict(points_mils=[pt(*p1), pt(*p2)],
             color=am.ColorValue.from_hex("#000080"))])


def make_label(text, xy_, angle, font_name="Times New Roman", font_size=10,
               font_color="#980000", font_bold=False, font_italic=False,
               font_underline=False, font_strikeout=False):
    import altium_monkey as am

    p = pt(*xy_)
    font = am.SchFontSpec(
        name=font_name, size=font_size, bold=font_bold, italic=font_italic,
        underline=font_underline, strikeout=font_strikeout)
    color = am.ColorValue.from_hex(font_color)
    justification = (am.TextJustification.BOTTOM_RIGHT if angle == 0
                     else am.TextJustification.BOTTOM_LEFT)
    return _call("make_sch_net_label", [
        dict(text=text, location_mils=p, orientation=angle,
             font=font, color=color, justification=justification),
        dict(text=text, location_mils=p, font=font,
             color=color, justification=justification),
        dict(name=text, location_mils=p, orientation=angle,
             font=font, color=color, justification=justification),
        dict(net_name=text, location_mils=p, orientation=angle,
             font=font, color=color, justification=justification),
        dict(text=text, location_mils=xy_, orientation=angle,
             font=font, color=color, justification=justification),
        dict(text=text, location_mils=xy_, font=font, color=color,
             justification=justification)])


def _point_on_segment(point, start, end, tolerance=0.1):
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_squared = dx * dx + dy * dy
    if not length_squared:
        return math.hypot(point[0] - start[0], point[1] - start[1]) <= tolerance

    position = ((point[0] - start[0]) * dx
                + (point[1] - start[1]) * dy) / length_squared
    position = max(0.0, min(1.0, position))
    nearest = (start[0] + position * dx, start[1] + position * dy)
    return math.hypot(point[0] - nearest[0], point[1] - nearest[1]) <= tolerance


def connected_pin_numbers(schdoc, designator):
    """Return component pins whose connection points touch schematic geometry."""
    pin_infos = [
        info for info in schdoc.get_all_pins()
        if info.component_designator.casefold() == designator.casefold()
    ]
    wire_segments = []
    for wire in schdoc.get_wires():
        points = [xy_mils(point) for point in wire.points_mils]
        wire_segments.extend(zip(points, points[1:]))

    connector_points = []
    for accessor in (schdoc.get_net_labels, schdoc.get_power_ports,
                     schdoc.get_cross_sheet_connectors):
        connector_points.extend(
            xy_mils(info.record.location_mils) for info in accessor())
    for port in schdoc.get_ports():
        connector_points.extend((x * 10, y * 10)
                                for x, y in port.connection_points)

    pin_points = [
        (info, xy_mils(info.pin.get_hot_spot())) for info in pin_infos]
    point_counts = {}
    for _info, point in pin_points:
        point_counts[point] = point_counts.get(point, 0) + 1

    connected = set()
    for info, point in pin_points:
        if point_counts[point] > 1 or any(
                math.hypot(point[0] - connector[0], point[1] - connector[1]) <= 0.1
                for connector in connector_points):
            connected.add(info.designator)
            continue
        if any(_point_on_segment(point, start, end)
               for start, end in wire_segments):
            connected.add(info.designator)
    return connected


def add_no_erc_to_unconnected_pins(schdoc, parts, designator):
    """Mark unconnected, non-power pins on the selected placed component(s)."""
    import altium_monkey as am

    connected = connected_pin_numbers(schdoc, designator)
    existing = {
        (round(marker.location_mils.x_mils), round(marker.location_mils.y_mils))
        for marker in schdoc.no_ercs
    }
    unconnected = []
    added = 0
    for comp in parts:
        for pin in part_pins(comp):
            if pin.electrical == am.PinElectrical.POWER:
                continue
            if pin_number(pin) in connected:
                continue

            location = tuple(round(value) for value in hotspot(comp, pin))
            unconnected.append(pin_number(pin))
            if location in existing:
                continue
            schdoc.add_object(
                am.make_sch_no_erc(
                    location_mils=am.SchPointMils.from_mils(*location)))
            existing.add(location)
            added += 1
    return unconnected, added


def label_stub_length(text, minimum_stub, font_name="Times New Roman",
                      font_size=10):
    from altium_monkey.altium_text_metrics import measure_text_width
    from altium_monkey.altium_ttf_metrics import pt_to_px

    width_px = measure_text_width(text, pt_to_px(font_size, font_name), font_name)
    width_mils = width_px * 1000 / 96
    required_length = max(minimum_stub, math.ceil(width_mils))
    return math.ceil(required_length / 100) * 100


def inspect_doc(schdoc, designator, pin_no="8"):
    import altium_monkey as am
    print("module names:", [n for n in dir(am) if re.search("wire|label|net|port", n, re.I)])
    for q in find_components(schdoc, designator):
        print("part", getattr(q, "current_part_id", "?"), "pins:", sorted(pin_number(p) for p in part_pins(q)))
    c = find_components(schdoc, designator)[0]
    pins = list(c.pins)
    print(f"{designator}: {len(pins)} pins; pin attrs:", public(pins[0]))
    for p in pins[:4]:
        print("  pin", pin_number(p), "hotspot", hotspot(c, p))


    # ---- detail dump of one pin (to find where Altium "Functions" live)
    target = None
    for q in find_components(schdoc, designator):
        for p in part_pins(q):
            if pin_number(p) == str(pin_no):
                target, owner = p, q
    if target is None:
        print("pin", pin_no, "not found"); return
    print(f"\n=== pin {pin_no}: {target!r}")
    for n in public(target):
        try:
            v = getattr(target, n)
        except Exception as e:
            v = f"<err {e}>"
        if callable(v):
            continue
        print(f"  {n} = {v!r}"[:300])
    print("  methods:", [n for n in public(target) if callable(getattr(target, n, None))])
    for fn in ("serialize_to_record", "to_record", "raw_record"):
        if hasattr(target, fn):
            try:
                print(f"  {fn}() =", getattr(target, fn)())
            except Exception as e:
                print(f"  {fn}() failed: {e}")
    print("  pin-related names on component:",
          [n for n in public(owner) if re.search("pin|func|alt", n, re.I)])


# --------------------------------------------------------------------------
# Lossless save: altium_monkey drops pin fields it doesn't model (notably the
# multi-function pin data PINDEFINEDFUNCTIONSCOUNT / PINDEFINEDFUNCTIONn).
# We read them from the ORIGINAL file and re-inject them while saving.
# --------------------------------------------------------------------------
def raw_pin_records(path):
    """Raw RECORD=2 (pin) records of a .SchDoc as {key: value}, original key case."""
    try:
        import olefile
    except ImportError:
        sys.exit("pip install olefile   (needed to keep pin functions intact)")
    ole = olefile.OleFileIO(path)
    data = ole.openstream("FileHeader").read()
    i, out = 0, []
    while i + 4 <= len(data):
        n = int.from_bytes(data[i:i + 3], "little")
        flag = data[i + 3]
        payload = data[i + 4:i + 4 + n]
        i += 4 + n
        if flag != 0:
            continue
        txt = payload.split(b"\x00")[0].decode("cp1252", "replace")
        d = {}
        for part in txt.split("|"):
            if "=" in part:
                k, v = part.split("=", 1)
                d[k] = v
        if d.get("RECORD") == "2":
            out.append(d)
    return out


# Fields altium_monkey writes explicitly even when the original file omitted them.
# Altium treats "omitted" as its own default, which is NOT always what the library
# assumes (e.g. missing ELECTRICAL is not the same as ELECTRICAL=0 for these pins).
ADDED_BY_LIB = {"ELECTRICAL", "COLOR", "ORIENTATION", "SHOWNAME", "SHOWDESIGNATOR", "ISHIDDEN"}
RESTORE_PREFIXES = ("PINDEFINEDFUNCTION", "PINPROPAGATIONDELAY")


def pin_fixups(path, exact=False):
    """{UniqueID: (fields_to_add, keys_to_remove)} derived from the ORIGINAL file."""
    fix = {}
    for r in raw_pin_records(path):
        uid = r.get("UNIQUEID") or r.get("UniqueID")
        if not uid:
            continue
        present = {k.upper() for k in r}
        add = {k: v for k, v in r.items() if k.upper().startswith(RESTORE_PREFIXES)}
        fix[uid] = (add, (ADDED_BY_LIB - present) if exact else set())
    return fix


def install_pin_patch(pin_cls, fixups):
    """Wrap pin_cls.serialize_to_record so each pin is written like the original."""
    orig = pin_cls.serialize_to_record

    def patched(self, *args, **kw):
        rec = orig(self, *args, **kw)
        fx = fixups.get(getattr(self, "unique_id", None))
        if fx and isinstance(rec, dict):
            add, remove = fx
            for k in [k for k in rec if k.upper() in remove]:
                del rec[k]
            rec.update(add)
        return rec

    pin_cls.serialize_to_record = patched


def verify_lossless(orig_path, new_path, exact=False):
    def sig(path):
        d = {}
        for r in raw_pin_records(path):
            uid = r.get("UNIQUEID") or r.get("UniqueID")
            up = {k.upper(): v for k, v in r.items()}
            fn = tuple(sorted((k, v) for k, v in up.items() if k.startswith("PINDEFINEDFUNCTION")))
            el = up.get("ELECTRICAL")
            if not exact and el is None:
                el = "0"            # missing == Input; only exact mode cares about presence
            d[uid] = (fn, el, frozenset(up) & ADDED_BY_LIB if exact else frozenset())
        return d
    A, B = sig(orig_path), sig(new_path)
    miss = (((), None, frozenset()))
    bad_fn = [u for u in A if A[u][0] != B.get(u, miss)[0]]
    bad_el = [u for u in A if A[u][1] != B.get(u, miss)[1]]
    bad_ex = [u for u in A if A[u][2] != B.get(u, miss)[2]]
    print(f"check: {len(A)} pin records in original, {len(B)} in output")
    checks = [("pin functions      ", bad_fn), ("pin electrical type", bad_el)]
    if exact:
        checks.append(("pin default fields", bad_ex))
    for label, bad in checks:
        print(f"check: {label} preserved ->", "PASS" if not bad else f"FAIL ({len(bad)} pins)")


# --------------------------------------------------------------------------
# 3. main
# --------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="cube-netlabels",
        description="Place net labels from an STM32CubeMX pinout CSV onto an "
                    "Altium .SchDoc. Always writes a new output file.")
    ap.add_argument("csv", nargs="?")
    ap.add_argument("schdoc_in", nargs="?")
    ap.add_argument("schdoc_out", nargs="?")
    ap.add_argument("--designator")
    ap.add_argument("--list-designators", metavar="SCHDOC",
                    help="list component designators found in a schematic and exit")
    ap.add_argument("--column", default="Label", help="CSV column used as net name (Label|Signal|Name)")
    ap.add_argument("--fallback-signal", action="store_true", help="if Label empty use Signal")
    ap.add_argument("--fallback-name", action="store_true", help="if still empty use port name (PA9)")
    ap.add_argument("--stub", type=float, default=300, help="minimum wire stub length, mils (multiple of 100); longer labels extend the stub as needed")
    ap.add_argument("--net-length", default="auto", help="wire stub length in mils, or 'auto' to size it for the label; endpoints are snapped to --grid")
    ap.add_argument("--grid", type=float, default=100, help="snap generated label endpoints to this grid size in mils (default: 100)")
    ap.add_argument("--font-name", default="Times New Roman", help="net label font name")
    ap.add_argument("--font-size", type=int, default=10, help="net label font size in points")
    ap.add_argument("--font-color", default="#980000", help="net label font color in #RRGGBB format")
    ap.add_argument("--font-bold", action="store_true", help="make net labels bold")
    ap.add_argument("--font-italic", action="store_true", help="italicize net labels")
    ap.add_argument("--font-underline", action="store_true", help="underline net labels")
    ap.add_argument("--font-strikeout", action="store_true", help="cross out net labels")
    ap.add_argument("--leave-unconnected-pins-as-is", action="store_true",
                    help="do not add generic No ERC markers to unconnected non-power pins")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--inspect", action="store_true")
    ap.add_argument("--exact-pins", action="store_true", help="also strip pin fields the library adds (ELECTRICAL=0, COLOR=0...) so pin records match the original byte-for-meaning; off by default")
    ap.add_argument("--pin", default="8", help="with --inspect: pin number to dump in detail")
    ap.add_argument("--svg", help="also render result to this SVG for a visual check")
    ap.add_argument("--gui", action="store_true",
                    help="launch the graphical interface (needs PySide6)")
    a = ap.parse_args(argv)

    if a.gui:
        from cube_netlabels.gui import launch
        launch()
        return

    if a.list_designators:
        from altium_monkey import AltiumSchDoc
        print("DESIGNATORS:" + json.dumps(
            list_designators(AltiumSchDoc(a.list_designators))))
        return

    if not a.designator:
        ap.error("--designator is required")

    # allow:  --inspect board.SchDoc
    if a.inspect:
        schematic = a.csv or a.schdoc_in
        if not schematic:
            ap.error("the schematic input path is required")
        from altium_monkey import AltiumSchDoc
        inspect_doc(AltiumSchDoc(schematic), a.designator, a.pin)
        return

    if not a.schdoc_in:
        ap.error("the schematic input path is required")

    nets = read_cube_csv(a.csv, a.column, a.fallback_signal, a.fallback_name)
    print(f"{len(nets)} labelled pins in CSV")
    if a.dry_run:
        for p, n in nets.items():
            print(f"  pin {p:>4} -> {n}")
        return
    if not a.schdoc_out:
        sys.exit("need output .SchDoc path")
    if a.grid <= 0:
        sys.exit("--grid must be greater than zero")
    if a.font_size <= 0:
        sys.exit("--font-size must be greater than zero")
    if a.net_length.lower() == "auto":
        net_length = None
    else:
        try:
            net_length = float(a.net_length)
        except ValueError:
            sys.exit("--net-length must be a positive number or 'auto'")
        if net_length <= 0:
            sys.exit("--net-length must be greater than zero")

    from altium_monkey import AltiumSchDoc
    doc = AltiumSchDoc(a.schdoc_in)
    parts = find_components(doc, a.designator)
    extras = pin_fixups(a.schdoc_in, a.exact_pins)
    print(f"restoring original pin records for {len(extras)} pins on save")
    pin_cls = type(next(iter(part_pins(parts[0]))))
    install_pin_patch(pin_cls, extras)
    print(f"{a.designator}: {len(parts)} placed part(s)")

    placed, done = 0, set()
    for comp in parts:
        pins = part_pins(comp)
        by_num = {pin_number(p): p for p in pins}
        print(f"  part id {getattr(comp, 'current_part_id', '?')}: {len(by_num)} pins")
        if not by_num:
            continue
        hot = {n: hotspot(comp, n) for n in by_num}
        ang_of = outward_angles(hot)       # computed per part, not across parts
        for num, net in nets.items():
            if num not in by_num or num in done:
                continue
            x, y = hot[num]
            ang = ang_of[num]
            stub = (label_stub_length(net, a.stub, a.font_name, a.font_size)
                    if net_length is None else net_length)
            ex = round((x + stub * math.cos(math.radians(ang))) / a.grid) * a.grid
            ey = round((y + stub * math.sin(math.radians(ang))) / a.grid) * a.grid
            doc.add_object(make_wire((round(x), round(y)), (ex, ey)))
            label = make_label(
                net, (ex, ey), ang, a.font_name, a.font_size, a.font_color,
                a.font_bold, a.font_italic, a.font_underline, a.font_strikeout)
            doc.add_object(label)
            label.font_id = doc.font_manager.get_or_create_font(
                font_name=a.font_name,
                font_size=a.font_size,
                bold=a.font_bold,
                italic=a.font_italic,
                rotation=0,
                underline=a.font_underline,
                strikeout=a.font_strikeout)
            done.add(num)
            placed += 1
    missing = [n for n in nets if n not in done]

    if not a.leave_unconnected_pins_as_is:
        unconnected, no_erc_added = add_no_erc_to_unconnected_pins(
            doc, parts, a.designator)
        print(f"placed {no_erc_added} No ERC marker(s) on "
              f"{len(unconnected)} unconnected non-power pin(s) of {a.designator}")

    doc.save(a.schdoc_out)
    print(f"placed {placed} labels -> {a.schdoc_out}")
    verify_lossless(a.schdoc_in, a.schdoc_out, a.exact_pins)
    if missing:
        print("CSV pins not found on symbol:", ", ".join(missing))
    if a.svg:
        with open(a.svg, "w", encoding="utf-8") as f:
            f.write(doc.to_svg())
        print("SVG written:", a.svg)


if __name__ == "__main__":
    main()
