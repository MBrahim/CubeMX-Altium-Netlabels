import pytest

from cube_netlabels.cli import clean_net, read_cube_csv

CSV = """Position,Name,Type,Signal,Label
1,VBAT,Power,,
14,PA0,I/O,ADC1_IN0,BTN_1
15,PA1,I/O,GPIO_Output,LED RED
16,PA2,I/O,USART2_TX,
17,PA3,I/O,GPIO_Input,
"""


def write(tmp_path, text=CSV):
    path = tmp_path / "pinout.csv"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_label_column_is_default(tmp_path):
    assert read_cube_csv(write(tmp_path)) == {"14": "BTN_1", "15": "LED_RED"}


def test_fallback_signal_fills_unlabelled_pins_but_skips_generic(tmp_path):
    nets = read_cube_csv(write(tmp_path), fallback_signal=True)
    assert nets["16"] == "USART2_TX"
    assert "17" not in nets          # GPIO_Input is too generic to be a net name


def test_fallback_name_uses_port_name(tmp_path):
    nets = read_cube_csv(write(tmp_path), fallback_name=True)
    assert nets["16"] == "PA2"
    assert nets["17"] == "PA3"


def test_signal_column(tmp_path):
    nets = read_cube_csv(write(tmp_path), column="Signal")
    assert nets == {"14": "ADC1_IN0", "16": "USART2_TX"}


def test_semicolon_delimiter(tmp_path):
    nets = read_cube_csv(write(tmp_path, CSV.replace(",", ";")))
    assert nets["14"] == "BTN_1"


def test_duplicate_pin_keeps_first(tmp_path, capsys):
    text = CSV + "14,PA0,I/O,,OTHER\n"
    nets = read_cube_csv(write(tmp_path, text))
    assert nets["14"] == "BTN_1"
    assert "appears twice" in capsys.readouterr().out


def test_same_net_on_two_pins_is_reported(tmp_path, capsys):
    text = CSV + "18,PA4,I/O,,BTN_1\n"
    read_cube_csv(write(tmp_path, text))
    assert "shorted" in capsys.readouterr().out


def test_missing_column_exits(tmp_path):
    with pytest.raises(SystemExit):
        read_cube_csv(write(tmp_path, "Pin,Net\n1,A\n"))


def test_empty_csv_exits(tmp_path):
    with pytest.raises(SystemExit):
        read_cube_csv(write(tmp_path, "Position,Name,Type,Signal,Label\n"))


@pytest.mark.parametrize("raw, expected", [
    ("SW 18", "SW_18"),
    ("  LED1 ", "LED1"),
    ("A/B[1]-x.y+z", "A/B[1]-x.y+z"),
    ("bad$name!", "bad_name_"),
])
def test_clean_net(raw, expected):
    assert clean_net(raw) == expected
