"""Qt 6 interface for the cube_netlabels command-line tool."""
import json
import os
import re
import sys
import subprocess
import tempfile
from pathlib import Path

from PySide6.QtCore import QPointF, QProcess, QProcessEnvironment, QSettings, Qt, QUrl
from PySide6.QtGui import (
    QBrush,
    QColor,
    QDesktopServices,
    QFont,
    QFontDatabase,
    QIntValidator,
    QPainter,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsScene,
    QGraphicsView,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QDoubleSpinBox,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from cube_netlabels.runtime import cli_process_command

try:  # SVG preview needs QtSvgWidgets (included with the normal PySide6 wheel)
    from PySide6.QtSvgWidgets import QGraphicsSvgItem
    HAVE_SVG = True
except ImportError:
    QGraphicsSvgItem = None
    HAVE_SVG = False


class PreviewView(QGraphicsView):
    """Zoomable / pannable viewer: wheel = zoom, drag = pan, double-click = fit."""

    def __init__(self):
        super().__init__()
        self.preview_scene = QGraphicsScene(self)
        self.setScene(self.preview_scene)
        self._fitted = True
        self.setBackgroundBrush(QBrush(QColor("#FFFFFF")))   # paper, in every theme
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setMinimumHeight(380)
        self.setToolTip("Mouse wheel: zoom   |   drag: pan   |   double-click: fit")

    def fit(self):
        self._fitted = True
        rect = self.preview_scene.itemsBoundingRect()
        if not rect.isEmpty():
            self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self._fitted = False
        self.scale(factor, factor)
        event.accept()

    def mouseDoubleClickEvent(self, event):
        self.fit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fitted:
            self.fit()


ALTIUM_STYLE_COLORS = (
    "#000000", "#333333", "#666666", "#999999", "#CCCCCC", "#FFFFFF",
    "#800000", "#FF0000", "#FF6600", "#FFCC00", "#FFFF00", "#CCFF00",
    "#00CC00", "#008000", "#00CCCC", "#00FFFF", "#0066CC", "#0000FF",
    "#6600CC", "#9900FF", "#FF00FF", "#FF99CC", "#980000", "#000080",
)


class NetLabelGui(QMainWindow):
    THEMES = {
        "Dark": {
            "background": "#2B2B2B", "surface": "#383838",
            "surface_alt": "#454545", "border": "#555555",
            "text": "#E6E6E6", "muted": "#B0B0B0",
            "accent": "#C89120", "accent_hover": "#DDAA3B",
            "input": "#303030",
        },
        "Light": {
            "background": "#F3F6FB", "surface": "#FFFFFF",
            "surface_alt": "#EAF0F8", "border": "#D2DBE8",
            "text": "#172033", "muted": "#5D6B82",
            "accent": "#2563EB", "accent_hover": "#1D4ED8",
            "input": "#FFFFFF",
        },
        "Blue": {
            "background": "#071B2E", "surface": "#0B2943",
            "surface_alt": "#103858", "border": "#1B4B6D",
            "text": "#E3F3FF", "muted": "#91B7D0",
            "accent": "#00A6D6", "accent_hover": "#29C1E8",
            "input": "#08233B",
        },
    }

    def __init__(self):
        super().__init__()
        self.setWindowTitle("CubeMX Net Labels for Altium")
        self.resize(900, 760)
        self.setMinimumSize(560, 460)
        self.process = None
        self.designator_process = None
        self._designator_path_loaded = None
        self._designator_path_pending = None
        self._auto_output = None          # last auto-suggested output name
        self.svg_path = Path(tempfile.gettempdir()) / "cube_netlabels_preview.svg"
        self.theme_name = "Dark"
        self.COLORS = self.THEMES[self.theme_name]
        self._configure_style()
        self._build_ui()
        self._build_theme_menu()
        self._restore_settings()

    @staticmethod
    def _arrow_image(color):
        """Draw a small down-arrow PNG (Qt stylesheets can't draw triangles)."""
        path = Path(tempfile.gettempdir()) / f"cube_netlabels_arrow_{color.lstrip('#')}.png"
        pixmap = QPixmap(12, 8)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawPolygon(QPolygonF([QPointF(1, 1.5), QPointF(11, 1.5), QPointF(6, 6.5)]))
        painter.end()
        pixmap.save(str(path), "PNG")
        return path.as_posix()          # forward slashes: required in url() on Windows

    def _configure_style(self):
        c = self.COLORS
        arrow = self._arrow_image(c['muted'])
        arrow_open = self._arrow_image(c['accent'])
        self.setStyleSheet(f"""
            QMenuBar {{ color: {c['text']}; background: {c['surface']}; }}
            QMenuBar::item {{ padding: 7px 12px; background: transparent; }}
            QMenuBar::item:selected {{ background: {c['surface_alt']}; }}
            QMenu {{
                color: {c['text']}; background: {c['surface']};
                border: 1px solid {c['border']}; padding: 5px;
            }}
            QMenu::item {{ padding: 7px 28px 7px 10px; border-radius: 4px; }}
            QMenu::item:selected {{ background: {c['surface_alt']}; }}
            QMainWindow, QWidget#page {{ background: {c['background']}; color: {c['text']}; }}
            QScrollArea {{ border: 0; background: {c['background']}; }}
            QFrame#card {{
                background: {c['surface']}; border: 1px solid {c['border']};
                border-radius: 12px;
            }}
            QFrame#palette {{
                background: {c['surface_alt']}; border: 1px solid {c['border']};
                border-radius: 9px;
            }}
            QLabel {{ color: {c['text']}; background: transparent; border: 0; }}
            QLabel#eyebrow {{ color: {c['accent']}; font-size: 9pt; font-weight: 700; }}
            QLabel#title {{ font-size: 22pt; font-weight: 700; }}
            QLabel#subtitle, QLabel#muted {{ color: {c['muted']}; }}
            QLabel#section {{ font-size: 11pt; font-weight: 700; }}
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
                color: {c['text']}; background: {c['input']};
                border: 1px solid {c['border']}; border-radius: 7px;
                padding: 8px 10px; selection-background-color: {c['accent']};
                min-height: 18px;
            }}
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
                border: 1px solid {c['accent']};
            }}
            QComboBox::drop-down {{
                subcontrol-origin: padding; subcontrol-position: top right;
                width: 26px; border-left: 1px solid {c['border']};
            }}
            QComboBox::down-arrow {{
                image: url("{arrow}"); width: 12px; height: 8px;
            }}
            QComboBox::down-arrow:on {{ image: url("{arrow_open}"); }}
            QComboBox QAbstractItemView {{
                color: {c['text']}; background: {c['input']};
                selection-background-color: {c['accent']};
                border: 1px solid {c['border']};
            }}
            QPushButton {{
                color: {c['text']}; background: {c['surface_alt']};
                border: 1px solid {c['border']}; border-radius: 7px;
                padding: 8px 12px;
            }}
            QPushButton:hover {{ background: {c['border']}; }}
            QPushButton:pressed {{ background: {c['accent']}; }}
            QPushButton#primary {{
                color: white; background: {c['accent']}; border: 0;
                border-radius: 8px; padding: 12px;
                font-size: 11pt; font-weight: 700;
            }}
            QPushButton#primary:hover {{ background: {c['accent_hover']}; }}
            QPushButton#primary:disabled {{
                color: {c['muted']}; background: {c['surface_alt']};
            }}
            QRadioButton, QCheckBox {{ color: {c['text']}; spacing: 8px; }}
            QRadioButton::indicator, QCheckBox::indicator {{
                width: 16px; height: 16px;
            }}
            QProgressBar {{
                background: {c['surface_alt']}; border: 0; border-radius: 4px;
                min-height: 7px; max-height: 7px; text-align: center;
            }}
            QProgressBar::chunk {{ background: {c['accent']}; border-radius: 4px; }}
            QTextEdit {{
                color: {c['text']}; background: {c['input']};
                border: 0; border-radius: 7px;
                selection-background-color: {c['accent']};
                font-family: "Cascadia Mono", "Consolas", monospace;
                font-size: 9pt; padding: 8px;
            }}
            QScrollBar:vertical {{
                background: {c['background']}; width: 10px; margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: {c['border']}; min-height: 30px; border-radius: 5px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
        """)
        self._style_font_buttons()

    def _build_theme_menu(self):
        theme_menu = self.menuBar().addMenu("Theme")
        self.theme_actions = {}
        for name in self.THEMES:
            action = theme_menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(name == self.theme_name)
            action.triggered.connect(
                lambda checked=False, theme=name: self._apply_theme(theme))
            self.theme_actions[name] = action

    def _apply_theme(self, theme_name):
        if theme_name not in self.THEMES:
            raise ValueError(f"Unknown theme: {theme_name}")
        self.theme_name = theme_name
        self.COLORS = self.THEMES[theme_name]
        self._configure_style()
        for name, action in self.theme_actions.items():
            action.setChecked(name == theme_name)
        self._set_font_color(self.color_hex.text())

    def _style_font_buttons(self):
        c = self.COLORS
        buttons = list(getattr(self, "font_style_buttons", {}).values())
        for index, button in enumerate(buttons):
            left_radius = "5px" if index == 0 else "0"
            right_radius = "5px" if index == len(buttons) - 1 else "0"
            button.setStyleSheet(f"""
                QPushButton {{
                    color: {c['muted']}; background: {c['input']};
                    border: 1px solid {c['border']};
                    border-top-left-radius: {left_radius};
                    border-bottom-left-radius: {left_radius};
                    border-top-right-radius: {right_radius};
                    border-bottom-right-radius: {right_radius};
                    padding: 4px 10px; font-weight: 600;
                }}
                QPushButton:hover {{
                    color: {c['text']}; border-color: {c['accent']};
                }}
                QPushButton:checked {{
                    color: #FFFFFF; background: {c['accent']};
                    border-color: {c['accent']};
                }}
            """)
        palette_toggle = getattr(self, "palette_toggle", None)
        if palette_toggle is not None:
            palette_toggle.setStyleSheet(f"""
                QToolButton {{
                    color: {c['muted']}; background: transparent;
                    border: 0; padding: 4px 0; text-align: left;
                }}
                QToolButton:hover {{ color: {c['accent']}; }}
            """)

    def _build_ui(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.setCentralWidget(scroll)

        page = QWidget()
        page.setObjectName("page")
        scroll.setWidget(page)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)

        header = QVBoxLayout()
        eyebrow = QLabel("ALTIUM  /  SCHEMATIC TOOL")
        eyebrow.setObjectName("eyebrow")
        title = QLabel("CubeMX Net Labels")
        title.setObjectName("title")
        subtitle = QLabel("Turn pinout data into clean, connected schematic labels.")
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        header.addWidget(eyebrow)
        header.addWidget(title)
        header.addWidget(subtitle)
        header.setSpacing(3)
        layout.addLayout(header)
        layout.addSpacing(4)

        files = self._card(layout, "01   PROJECT FILES",
                           "Choose your source data and destination.")
        self.csv_path = self._file_row(files, 2, "CubeMX CSV", self._browse_csv)
        self.input_path = self._file_row(
            files, 3, "Input schematic", self._browse_input)
        self.input_path.editingFinished.connect(self._load_designators)
        self.output_path = self._file_row(
            files, 4, "Output schematic", self._browse_output)

        target = self._card(
            layout, "02   TARGET",
            "Select the component and source column for net names.")
        target_grid = target.layout()
        self.designator = QComboBox()
        self.designator.setEditable(True)
        self.designator.addItem("U1")
        self.designator.setFixedWidth(180)
        self.designator.setToolTip(
            "Choose a component designator found in the input schematic.")
        self.column = QComboBox()
        self.column.addItems(("Label", "Signal", "Name"))
        self.column.setFixedWidth(180)
        target_fields = QGridLayout()
        target_fields.setHorizontalSpacing(12)
        target_fields.setVerticalSpacing(8)
        self._field(target_fields, 0, "Select designator", self.designator, 0)
        self._field(target_fields, 0, "Net-name column", self.column, 1)
        target_fields.setColumnStretch(0, 1)
        target_fields.setColumnStretch(1, 1)
        target_grid.addLayout(target_fields, 2, 0, 1, 2)

        routing = self._card(
            layout, "03   ROUTING", "Control grid alignment and wire length (mils).")
        routing_grid = routing.layout()
        self.grid_size = QDoubleSpinBox()
        self.grid_size.setRange(1, 100000)
        self.grid_size.setDecimals(0)
        self.grid_size.setValue(100)
        self.grid_size.setFixedWidth(180)
        self.length_mode = QComboBox()
        self.length_mode.addItems(("Auto fit", "Custom"))
        self.length_mode.setFixedWidth(180)
        self.net_length = QComboBox()
        self.net_length.setEditable(True)
        self.net_length.addItems(("100", "200", "300","400" , "500", "600", "700", "800", "900", "1000"))
        self.net_length.setCurrentText("300")
        self.net_length.lineEdit().setValidator(QIntValidator(1, 100000, self))
        self.net_length.setToolTip("Custom net length in mils")
        self.net_length.setFixedWidth(180)
        routing_fields = QGridLayout()
        routing_fields.setHorizontalSpacing(12)
        routing_fields.setVerticalSpacing(8)
        self._field(routing_fields, 0, "Grid size", self.grid_size, 0)
        length_label = QLabel("Net length")
        length_label.setObjectName("muted")
        routing_fields.addWidget(length_label, 0, 1)
        routing_fields.addWidget(self.length_mode, 1, 1)
        length_value_label = QLabel("Length (mils)")
        length_value_label.setObjectName("muted")
        routing_fields.addWidget(length_value_label, 2, 1)
        length_value_label.setVisible(False)
        self.net_length.setVisible(False)
        routing_fields.addWidget(self.net_length, 3, 1)
        routing_fields.setColumnStretch(0, 1)
        routing_fields.setColumnStretch(1, 1)
        self.length_mode.currentTextChanged.connect(
            lambda mode: (
                length_value_label.setVisible(mode == "Custom"),
                self.net_length.setVisible(mode == "Custom"),
            ))
        routing_grid.addLayout(routing_fields, 2, 0, 1, 2)
        note = QLabel(
            "Label endpoints snap to the grid, which may adjust the final wire length slightly.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        routing_grid.addWidget(note, 3, 0, 1, 2)

        appearance = self._card(
            layout, "04   APPEARANCE", "Choose the label font and color.")
        appearance_grid = appearance.layout()
        families = self._installed_fonts()
        self.font_name = QComboBox()
        self.font_name.addItems(families)
        self.font_name.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.font_name.setMinimumContentsLength(20)
        self.font_name.setMaxVisibleItems(20)
        index = self.font_name.findText("Times New Roman")
        if index >= 0:
            self.font_name.setCurrentIndex(index)
        self.font_name.setFixedWidth(180)
        self.font_size = QComboBox()
        self.font_size.setEditable(True)
        self.font_size.addItems(
            ("6", "8", "9", "10", "11", "12", "14", "16", "18", "20", "24", "28", "36", "48", "72"))
        self.font_size.setCurrentText("10")
        self.font_size.setFixedWidth(180)
        self.font_size.lineEdit().setValidator(QIntValidator(1, 500, self))
        appearance_fields = QGridLayout()
        appearance_fields.setHorizontalSpacing(12)
        appearance_fields.setVerticalSpacing(8)
        self._field(appearance_fields, 0, "Font", self.font_name, 0)
        self._field(appearance_fields, 0, "Size (pt)", self.font_size, 1)
        appearance_fields.setColumnStretch(0, 1)
        appearance_fields.setColumnStretch(1, 1)
        appearance_grid.addLayout(appearance_fields, 2, 0, 1, 2)

        styles = QHBoxLayout()
        styles.setSpacing(1)
        styles.addStretch(1)
        self.font_style_buttons = {}
        for key, label in (
                ("bold", "Bold"),
                ("italic", "Italic"),
                ("underline", "Underline"),
                ("strikeout", "Strikeout")):
            button = QPushButton()
            button.setText(label)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFixedHeight(30)
            button.setToolTip({
                "bold": "Bold",
                "italic": "Italic",
                "underline": "Underline",
                "strikeout": "Cross out / strike through the label text",
            }[key])
            font = QFont(button.font())
            font.setBold(key == "bold")
            font.setItalic(key == "italic")
            font.setUnderline(key == "underline")
            font.setStrikeOut(key == "strikeout")
            button.setFont(font)
            styles.addWidget(button)
            self.font_style_buttons[key] = button
        self._style_font_buttons()
        styles.addStretch(1)
        appearance_grid.addLayout(styles, 3, 0, 1, 2)

        palette = QFrame()
        palette.setObjectName("palette")
        palette_layout = QHBoxLayout(palette)
        palette_layout.setContentsMargins(6, 6, 6, 6)
        palette_layout.setSpacing(3)
        self.font_color = "#980000"
        self.color_preview = QLabel("Aa")
        self.color_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.color_preview.setFixedSize(42, 38)
        self.color_hex = QLineEdit(self.font_color)
        self.color_hex.setFixedWidth(95)
        self.color_hex.textChanged.connect(self._preview_typed_color)
        palette_layout.addWidget(self.color_preview)
        palette_layout.addWidget(self.color_hex)
        palette_layout.addSpacing(3)
        self.color_buttons = []
        swatches = QGridLayout()
        swatches.setHorizontalSpacing(4)
        swatches.setVerticalSpacing(4)
        for index, color in enumerate(ALTIUM_STYLE_COLORS):
            button = QPushButton()
            button.setFixedSize(18, 18)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(color)
            button.clicked.connect(
                lambda _checked=False, value=color: self._set_font_color(value))
            swatches.addWidget(button, index // 8, index % 8)
            self.color_buttons.append((color, button))
        palette_layout.addLayout(swatches)
        self.custom_color_button = QPushButton("Custom…")
        self.custom_color_button.clicked.connect(self._choose_font_color)
        palette_layout.addWidget(self.custom_color_button)
        self.palette_toggle = QToolButton()
        self.palette_toggle.setText("More colors  ▾")
        self.palette_toggle.setCheckable(True)
        self.palette_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.palette_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.palette_toggle.toggled.connect(self._toggle_color_palette)
        self.font_color_button = QToolButton()
        self.font_color_button.setToolTip("Choose label font color")
        self.font_color_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.font_color_button.setFixedSize(30, 28)
        self.font_color_button.clicked.connect(self._choose_font_color)
        palette_controls = QHBoxLayout()
        palette_controls.addWidget(self.palette_toggle)
        palette_controls.addStretch(1)
        palette_controls.addWidget(self.font_color_button)
        appearance_grid.addLayout(palette_controls, 4, 0, 1, 2)
        self.color_palette = palette
        appearance_grid.addWidget(palette, 5, 0, 1, 2)
        palette.hide()
        self._style_font_buttons()
        self._set_font_color(self.font_color)

        checks = self._card(
            layout, "05   PIN CHECKS", "Optionally mark unused pins for ERC.")
        self.add_no_erc = QCheckBox(
            "Add No ERC to unused pins")
        self.add_no_erc.setMinimumWidth(0)
        self.add_no_erc.setChecked(True)
        checks.layout().addWidget(self.add_no_erc, 2, 0, 1, 2)

        actions = QHBoxLayout()
        self.run_button = QPushButton("Generate schematic   →")
        self.run_button.setObjectName("primary")
        self.run_button.clicked.connect(self._run)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        self.open_folder_button = QPushButton("Open output folder")
        self.open_folder_button.setToolTip("Show the output schematic in your file manager")
        self.open_folder_button.clicked.connect(self._open_output_folder)
        actions.addWidget(self.run_button, 1)
        actions.addWidget(self.progress)
        actions.addWidget(self.open_folder_button)
        layout.addLayout(actions)
        self.status = QLabel("Ready")
        self.status.setObjectName("muted")
        self.show_preview = QCheckBox("Show preview")
        self.show_preview.setChecked(True)
        if not HAVE_SVG:
            self.show_preview.setChecked(False)
            self.show_preview.setEnabled(False)
            self.show_preview.setToolTip("QtSvgWidgets is not available in this PySide6 install")
        status_row = QHBoxLayout()
        status_row.addWidget(self.status, 1)
        status_row.addWidget(self.show_preview)
        layout.addLayout(status_row)

        activity = self._card(layout, "ACTIVITY", "Generation output and validation.")
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(120)
        activity.layout().addWidget(self.log, 2, 0, 1, 2)

        self.preview_card = self._card(
            layout, "PREVIEW",
            "Rendered output. Mouse wheel zooms, drag pans, double-click fits the sheet.")
        self.preview_view = PreviewView()
        self.preview_card.layout().addWidget(self.preview_view, 2, 0, 1, 2)
        fit_button = QPushButton("Fit to window")
        fit_button.clicked.connect(lambda _checked=False: self.preview_view.fit())
        self.preview_card.layout().addWidget(fit_button, 3, 0, 1, 2)
        self.preview_card.hide()
        layout.addStretch(1)

    def _card(self, parent_layout, title, subtitle):
        card = QFrame()
        card.setObjectName("card")
        grid = QGridLayout(card)
        grid.setContentsMargins(16, 13, 16, 15)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        section = QLabel(title)
        section.setObjectName("section")
        description = QLabel(subtitle)
        description.setObjectName("muted")
        description.setWordWrap(True)
        grid.addWidget(section, 0, 0, 1, 2)
        grid.addWidget(description, 1, 0, 1, 2)
        if parent_layout is not None:
            parent_layout.addWidget(card)
        return card

    @staticmethod
    def _installed_fonts():
        families = set(QFontDatabase.families())
        if not families and sys.platform == "win32":
            import winreg

            registry_paths = (
                (winreg.HKEY_LOCAL_MACHINE,
                 r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
                (winreg.HKEY_CURRENT_USER,
                 r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
            )
            for hive, key_path in registry_paths:
                try:
                    with winreg.OpenKey(hive, key_path) as key:
                        for index in range(winreg.QueryInfoKey(key)[1]):
                            name, _value, _kind = winreg.EnumValue(key, index)
                            family = re.sub(
                                r"\s+\((?:TrueType|OpenType|All Res)\)$",
                                "", name, flags=re.IGNORECASE).strip()
                            if family:
                                families.add(family)
                except OSError:
                    continue
        return sorted(families, key=str.casefold)

    @staticmethod
    def _field(grid, row, label, widget, column):
        caption = QLabel(label)
        caption.setObjectName("muted")
        grid.addWidget(caption, row, column)
        grid.addWidget(widget, row + 1, column)
        grid.setColumnStretch(column, 1)

    def _file_row(self, card, row, label, callback):
        grid = card.layout()
        caption = QLabel(label)
        caption.setObjectName("muted")
        entry = QLineEdit()
        entry.setClearButtonEnabled(True)
        button = QPushButton("Browse…")
        button.clicked.connect(callback)
        grid.addWidget(caption, row, 0)
        grid.addWidget(entry, row, 1)
        grid.addWidget(button, row, 2)
        grid.setColumnStretch(1, 1)
        return entry

    def _browse_csv(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select CubeMX CSV", "", "CSV files (*.csv);;All files (*)")
        if path:
            self.csv_path.setText(path)

    def _browse_input(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select input schematic", "",
            "Altium schematic (*.SchDoc);;All files (*)")
        if path:
            self.input_path.setText(path)
            source = Path(path)
            suggested = str(source.with_name(
                source.stem + "_labeled" + source.suffix))
            current = self.output_path.text().strip()
            # Fill/refresh the name unless the user typed their own output path
            if not current or current == self._auto_output:
                self.output_path.setText(suggested)
                self._auto_output = suggested
            self._load_designators()

    def _load_designators(self):
        schematic = self.input_path.text().strip()
        if not schematic or not Path(schematic).is_file():
            return
        resolved_path = str(Path(schematic).resolve())
        if self.designator_process is not None:
            if (self.designator_process.state()
                    != QProcess.ProcessState.NotRunning
                    and resolved_path == self._designator_path_pending):
                return
            self.designator_process.kill()
            self.designator_process.deleteLater()
            self.designator_process = None
            self._designator_path_pending = None
        if resolved_path == self._designator_path_loaded:
            return

        self._append_log(f"\nReading component designators from:\n{schematic}\n")
        self.status.setText("Reading component designators…")
        process = QProcess(self)
        program, arguments = cli_process_command([
            "--list-designators", schematic,
        ])
        process.setProgram(program)
        process.setArguments(arguments)
        process.setProcessEnvironment(self._child_environment())
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.finished.connect(
            lambda code, status, current=process, path=resolved_path:
            self._designators_finished(current, path, code, status))
        process.errorOccurred.connect(
            lambda error, current=process:
            self._designators_process_error(current, error))
        self.designator_process = process
        self._designator_path_pending = resolved_path
        process.start()

    def _designators_finished(self, process, path, exit_code, _exit_status):
        stdout = bytes(process.readAllStandardOutput()).decode(
            "utf-8", errors="replace")
        stderr = bytes(process.readAllStandardError()).decode(
            "utf-8", errors="replace")
        process.deleteLater()
        if process is not self.designator_process:
            return
        self.designator_process = None
        self._designator_path_pending = None
        if stderr:
            self._append_log(stderr)
        if exit_code != 0:
            self.status.setText(
                f"Could not read designators (exit code {exit_code})")
            self._append_log("Failed to read component designators.\n")
            return

        result_line = next(
            (line for line in stdout.splitlines()
             if line.startswith("DESIGNATORS:")), None)
        if result_line is None:
            self.status.setText("Could not read component designators")
            self._append_log(
                "The schematic reader returned no designator list.\n" + stdout)
            return
        try:
            designators = json.loads(result_line[len("DESIGNATORS:"):])
        except json.JSONDecodeError as exc:
            self.status.setText("Could not read component designators")
            self._append_log(f"Invalid designator-list output: {exc}\n")
            return
        if (not isinstance(designators, list)
                or any(not isinstance(item, str) for item in designators)):
            self.status.setText("Could not read component designators")
            self._append_log("The schematic reader returned an invalid designator list.\n")
            return

        previous = self.designator.currentText().strip()
        self.designator.clear()
        self.designator.addItems(designators)
        if previous in designators:
            self.designator.setCurrentText(previous)
        elif designators:
            self.designator.setCurrentIndex(0)
        else:
            self.designator.setCurrentText("")
        self._designator_path_loaded = path
        self.status.setText(f"Found {len(designators)} component designator(s)")
        self._append_log(
            "Found component designators: "
            + (", ".join(designators) if designators else "(none)")
            + "\n")

    def _designators_process_error(self, process, error):
        if (process is not self.designator_process
                or error != QProcess.ProcessError.FailedToStart):
            return
        self.designator_process = None
        self._designator_path_pending = None
        self.status.setText("Could not start schematic reader")
        self._append_log(
            "Could not start designator reader: "
            + process.errorString() + "\n")
        process.deleteLater()

    def _browse_output(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save output schematic", "", "Altium schematic (*.SchDoc)")
        if path:
            if not Path(path).suffix:
                path += ".SchDoc"
            self.output_path.setText(path)

    @staticmethod
    def _contrast_color(color):
        rgb = QColor(color)
        if not rgb.isValid():
            return "#FFFFFF"
        luminance = (rgb.red() * 299 + rgb.green() * 587 + rgb.blue() * 114) / 1000
        return "#111827" if luminance > 150 else "#FFFFFF"

    def _set_font_color(self, color):
        color = color.upper()
        self.font_color = color
        self.color_hex.setText(color)
        self.font_color_button.setStyleSheet(
            f"QToolButton {{ background: {color}; border: 1px solid "
            f"{self.COLORS['border']}; border-radius: 4px; }}"
            f"QToolButton:hover {{ border: 2px solid {self.COLORS['accent']}; }}")
        self.color_preview.setStyleSheet(
            f"QLabel {{ background: {color}; color: "
            f"{self._contrast_color(color)}; border: 0; border-radius: 6px; "
            "font-size: 12pt; font-weight: 700; }")
        for swatch, button in self.color_buttons:
            selected = swatch.upper() == color
            button.setStyleSheet(
                f"QPushButton {{ background: {swatch}; border: "
                f"{2 if selected else 1}px solid "
                f"{'#FFFFFF' if selected else self.COLORS['border']}; "
                "border-radius: 5px; }")

    def _preview_typed_color(self, text):
        if re.fullmatch(r"#[0-9a-fA-F]{6}", text):
            self._set_font_color(text)

    def _toggle_color_palette(self, visible):
        self.color_palette.setVisible(visible)
        self.palette_toggle.setText(
            "Fewer colors  ▴" if visible else "More colors  ▾")

    def _choose_font_color(self):
        color = QColorDialog.getColor(QColor(self.font_color), self, "Choose label font color")
        if color.isValid():
            self._set_font_color(color.name())

    def _append_log(self, text):
        if text:
            self.log.moveCursor(self.log.textCursor().MoveOperation.End)
            self.log.insertPlainText(text)
            self.log.ensureCursorVisible()

    def _run(self):
        csv_path = self.csv_path.text().strip()
        input_path = self.input_path.text().strip()
        output_path = self.output_path.text().strip()
        designator = self.designator.currentText().strip()
        if not all((csv_path, input_path, output_path, designator)):
            QMessageBox.critical(
                self, "Missing information",
                "Choose the CSV, input and output schematics, and component designator.")
            return
        if not Path(csv_path).is_file() or not Path(input_path).is_file():
            QMessageBox.critical(
                self, "File not found", "The selected CSV or input schematic does not exist.")
            return
        if Path(input_path).resolve() == Path(output_path).resolve():
            QMessageBox.critical(
                self, "Invalid output",
                "Choose an output path different from the input schematic.")
            return
        if Path(output_path).exists():
            answer = QMessageBox.question(
                self, "Overwrite output?",
                f"Replace the existing file?\n{output_path}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return

        font_color = self.color_hex.text().strip()
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", font_color):
            QMessageBox.critical(
                self, "Invalid font color",
                "Enter a hex color in #RRGGBB format, for example #980000.")
            self.color_hex.setFocus()
            return
        font_size = self.font_size.currentText().strip()
        if not font_size.isdecimal() or not 1 <= int(font_size) <= 500:
            QMessageBox.critical(
                self, "Invalid font size",
                "Enter a font size between 1 and 500 points.")
            self.font_size.setFocus()
            return
        net_length = self.net_length.currentText().strip()
        if (self.length_mode.currentText() == "Custom"
                and (not net_length.isdecimal()
                     or not 1 <= int(net_length) <= 100000)):
            QMessageBox.critical(
                self, "Invalid net length",
                "Enter a custom net length between 1 and 100000 mils.")
            self.net_length.setFocus()
            return
        self._set_font_color(font_color)
        self._save_settings()

        command = [
            csv_path, input_path, output_path,
            "--designator", designator,
            "--column", self.column.currentText(),
            "--grid", str(self.grid_size.value()),
            "--font-name", self.font_name.currentText(),
            "--font-size", str(int(font_size)),
            "--font-color", font_color.upper(),
            "--net-length", net_length
            if self.length_mode.currentText() == "Custom" else "auto",
            "--exact-pins",
        ]
        for style, button in self.font_style_buttons.items():
            if button.isChecked():
                command.append(f"--font-{style}")
        if not self.add_no_erc.isChecked():
            command.append("--leave-unconnected-pins-as-is")
        try:                      # never show a stale preview from an earlier run
            self.svg_path.unlink(missing_ok=True)
        except OSError:
            pass
        if HAVE_SVG and self.show_preview.isChecked():
            command += ["--svg", str(self.svg_path)]

        self.run_button.setEnabled(False)
        self.progress.show()
        self.status.setText("Generating schematic…")
        program, arguments = cli_process_command(command)
        self._append_log("\nRunning:\n" + subprocess.list2cmdline(
            [program, *arguments]) + "\n\n")

        self.process = QProcess(self)
        self.process.setProgram(program)
        self.process.setArguments(arguments)
        self.process.setProcessEnvironment(self._child_environment())
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(self._process_error)
        self.process.start()

    @staticmethod
    def _child_environment():
        """Let `python -m cube_netlabels` find the package even if it is not pip-installed."""
        env = QProcessEnvironment.systemEnvironment()
        root = str(Path(__file__).resolve().parent.parent)
        existing = env.value("PYTHONPATH")
        env.insert("PYTHONPATH", root + (os.pathsep + existing if existing else ""))
        return env

    # ------------------------------------------------------------ settings
    @staticmethod
    def _settings():
        return QSettings("CubeNetLabels", "NetLabelGui")

    def _save_settings(self):
        s = self._settings()
        s.setValue("geometry", self.saveGeometry())
        s.setValue("theme", self.theme_name)
        s.setValue("csv_path", self.csv_path.text().strip())
        s.setValue("input_path", self.input_path.text().strip())
        s.setValue("output_path", self.output_path.text().strip())
        s.setValue("designator", self.designator.currentText().strip())
        s.setValue("column", self.column.currentText())
        s.setValue("grid", self.grid_size.value())
        s.setValue("length_mode", self.length_mode.currentText())
        s.setValue("net_length", self.net_length.currentText().strip())
        s.setValue("font_name", self.font_name.currentText())
        s.setValue("font_size", self.font_size.currentText().strip())
        for key, button in self.font_style_buttons.items():
            s.setValue(f"font_{key}", button.isChecked())
        s.setValue("font_color", self.font_color)
        s.setValue("add_no_erc", self.add_no_erc.isChecked())
        s.setValue("show_preview", self.show_preview.isChecked())

    @staticmethod
    def _set_combo(combo, text):
        index = combo.findText(text)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _restore_settings(self):
        s = self._settings()
        geometry = s.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        theme = s.value("theme", self.theme_name, type=str)
        if theme in self.THEMES and theme != self.theme_name:
            self._apply_theme(theme)

        input_text = s.value("input_path", "", type=str)
        output_text = s.value("output_path", "", type=str)
        self.csv_path.setText(s.value("csv_path", "", type=str))
        self.input_path.setText(input_text)
        self.output_path.setText(output_text)
        if input_text:
            source = Path(input_text)
            auto = str(source.with_name(source.stem + "_labeled" + source.suffix))
            if output_text == auto:
                self._auto_output = auto

        designator = s.value("designator", "", type=str)
        if designator:
            self.designator.setCurrentText(designator)
        self._set_combo(self.column, s.value("column", "", type=str))
        self.grid_size.setValue(s.value("grid", 100, type=float))
        self._set_combo(self.length_mode, s.value("length_mode", "", type=str))
        net_length = s.value("net_length", "", type=str)
        if net_length:
            self.net_length.setCurrentText(net_length)
        self._set_combo(self.font_name, s.value("font_name", "", type=str))
        font_size = s.value("font_size", "", type=str)
        if font_size:
            self.font_size.setCurrentText(font_size)
        for key, button in self.font_style_buttons.items():
            button.setChecked(s.value(f"font_{key}", False, type=bool))
        font_color = s.value("font_color", "", type=str)
        if re.fullmatch(r"#[0-9a-fA-F]{6}", font_color):
            self._set_font_color(font_color)
        self.add_no_erc.setChecked(s.value("add_no_erc", True, type=bool))
        if HAVE_SVG:
            self.show_preview.setChecked(s.value("show_preview", True, type=bool))

        if input_text and Path(input_text).is_file():
            self._load_designators()      # keeps the restored designator if present

    def closeEvent(self, event):
        self._save_settings()
        super().closeEvent(event)

    # ------------------------------------------------------------ output folder
    def _open_output_folder(self):
        text = self.output_path.text().strip()
        if not text:
            QMessageBox.information(
                self, "No output", "Choose an output schematic first.")
            return
        path = Path(text)
        if not path.parent.is_dir():
            QMessageBox.warning(
                self, "Folder not found", f"The folder does not exist:\n{path.parent}")
            return
        try:
            if path.is_file():
                if sys.platform == "win32":          # open Explorer with the file selected
                    subprocess.Popen(f'explorer /select,"{os.path.normpath(path)}"')
                    return
                if sys.platform == "darwin":
                    subprocess.Popen(["open", "-R", str(path)])
                    return
        except OSError:
            pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    # ------------------------------------------------------------ svg preview
    def _load_preview(self):
        if not (HAVE_SVG and self.show_preview.isChecked()):
            return
        if not self.svg_path.is_file():
            self._append_log("Preview: no SVG was produced.\n")
            return
        item = QGraphicsSvgItem(str(self.svg_path))
        if not item.renderer().isValid():
            self._append_log(f"Preview: could not render {self.svg_path}\n")
            return
        scene = self.preview_view.preview_scene
        scene.clear()
        scene.addItem(item)
        scene.setSceneRect(item.boundingRect())
        self.preview_card.show()
        self.preview_view.fit()

    def _read_stdout(self):
        self._append_log(bytes(self.process.readAllStandardOutput()).decode(
            "utf-8", errors="replace"))

    def _read_stderr(self):
        self._append_log(bytes(self.process.readAllStandardError()).decode(
            "utf-8", errors="replace"))

    def _process_finished(self, exit_code, _exit_status):
        self._read_stdout()
        self._read_stderr()
        self.run_button.setEnabled(True)
        self.progress.hide()
        if exit_code == 0:
            self.status.setText("Schematic generated successfully")
            self._load_preview()
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Information)
            box.setWindowTitle("Complete")
            box.setText("Schematic generated successfully.")
            open_button = box.addButton(
                "Open output folder", QMessageBox.ButtonRole.ActionRole)
            box.addButton(QMessageBox.StandardButton.Close)
            box.exec()
            if box.clickedButton() is open_button:
                self._open_output_folder()
        else:
            self.status.setText(f"Generation failed (exit code {exit_code})")
            QMessageBox.critical(
                self, "Generation failed",
                f"The script exited with code {exit_code}. See the activity log.")
        self.process = None

    def _process_error(self, error):
        if self.process and error == QProcess.ProcessError.FailedToStart:
            self.run_button.setEnabled(True)
            self.progress.hide()
            self.status.setText("Could not start the generator")
            QMessageBox.critical(
                self, "Could not start generator",
                self.process.errorString())
            self.process = None


def launch():
    app = QApplication.instance() or QApplication(sys.argv)
    window = NetLabelGui()
    window.show()
    if QApplication.instance() is app:
        app.exec()


if __name__ == "__main__":
    launch()
