from types import SimpleNamespace as NS

from cube_netlabels.cli import (comp_designator, install_pin_patch,
                                connected_pin_numbers, outward_angles,
                                part_pins, text_of)


def test_outward_angles_follow_the_symbol_side():
    hot = {"L": (0, 150), "R": (1000, 150), "B": (500, 0), "T": (500, 300)}
    assert outward_angles(hot) == {"L": 180, "R": 0, "B": 270, "T": 90}


def test_connected_pin_numbers_uses_wire_segments_and_net_labels():
    pins = [
        NS(component_designator="U1", designator="1",
           pin=NS(get_hot_spot=lambda: NS(x_mils=1000, y_mils=1000))),
        NS(component_designator="U1", designator="2",
           pin=NS(get_hot_spot=lambda: NS(x_mils=2000, y_mils=2000))),
        NS(component_designator="U2", designator="3",
           pin=NS(get_hot_spot=lambda: NS(x_mils=1000, y_mils=1000))),
    ]
    wire = NS(points_mils=[NS(x_mils=900, y_mils=1000),
                           NS(x_mils=1100, y_mils=1000)])
    label = NS(record=NS(location_mils=NS(x_mils=2000, y_mils=2000)))
    schdoc = NS(
        get_all_pins=lambda: pins,
        get_wires=lambda: [wire],
        get_net_labels=lambda: [label],
        get_power_ports=lambda: [],
        get_cross_sheet_connectors=lambda: [],
        get_ports=lambda: [],
    )

    assert connected_pin_numbers(schdoc, "U1") == {"1", "2"}


def test_part_pins_only_returns_pins_of_the_placed_part():
    pins = [NS(designator="1", owner_part_id=1),
            NS(designator="2", owner_part_id=2),
            NS(designator="3", owner_part_id=-1)]       # -1 = shared by all parts
    comp = NS(current_part_id=2, pins=pins)
    assert [p.designator for p in part_pins(comp)] == ["2", "3"]


def test_text_of_accepts_strings_and_text_objects():
    assert text_of("U1") == "U1"
    assert text_of(NS(text="U2")) == "U2"
    assert text_of(42) is None


def test_comp_designator_variants():
    assert comp_designator(NS(designator="U1 ")) == "U1"
    assert comp_designator(NS(designator=NS(text="U2"))) == "U2"

    class SchDesignator:                     # child object, as altium_monkey stores it
        text = "U3"

    assert comp_designator(NS(children=[SchDesignator()])) == "U3"
    assert comp_designator(NS(children=[], parameters=[])) is None


def test_pin_patch_restores_fields_and_removes_added_defaults():
    class Pin:
        def __init__(self, uid, record):
            self.unique_id, self._record = uid, record

        def serialize_to_record(self):
            return dict(self._record)

    fixups = {
        "A": ({"PinDefinedFunctionsCount": "1", "PinDefinedFunction1": "ADC123_IN10"},
              {"ELECTRICAL", "COLOR"}),
    }
    install_pin_patch(Pin, fixups)

    patched = Pin("A", {"RECORD": "2", "Name": "PC0", "ELECTRICAL": "0", "COLOR": "0"})
    assert patched.serialize_to_record() == {
        "RECORD": "2", "Name": "PC0",
        "PinDefinedFunctionsCount": "1", "PinDefinedFunction1": "ADC123_IN10"}

    untouched = Pin("other", {"RECORD": "2", "ELECTRICAL": "7"})
    assert untouched.serialize_to_record() == {"RECORD": "2", "ELECTRICAL": "7"}
