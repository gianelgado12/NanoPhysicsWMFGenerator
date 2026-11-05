# $description: Create a border polygon around a region
# $show-in-menu
# $menu-path: tools_menu.end

"""KLayout macro that draws a rectangular border polygon on a chosen layer.

It only creates geometry; trace it afterwards with the WMF hatch path macro
(or WMFGenerator.py) using the contour pattern.
"""

import json
import os

import pya

_TITLE = "Create border"
_SETTINGS_FILE = "wmf_generator_settings.json"
_SIDES = ("Outside the region", "Inside the region", "Centered on the region edge")


def _settings_path():
	try:
		folder = pya.Application.instance().klayout_path()[0]
	except Exception:
		folder = os.path.expanduser("~")
	return os.path.join(folder, _SETTINGS_FILE)


def _load_settings():
	try:
		with open(_settings_path(), encoding="utf-8") as handle:
			return json.load(handle)
	except (OSError, ValueError):
		return {}


def _save_settings(settings):
	try:
		with open(_settings_path(), "w", encoding="utf-8") as handle:
			json.dump(settings, handle, indent=2)
	except OSError:
		pass


def _read(getter):
	"""Read a Qt value that KLayout exposes as either a property or a method."""
	return getter() if callable(getter) else getter


def border_boxes(center_x, center_y, width, height, border, side_index):
	"""Return the (outer, inner) rectangles in microns as (left, bottom, right, top)."""
	if width <= 0 or height <= 0:
		raise ValueError("The region width and height must be greater than zero.")
	if border <= 0:
		raise ValueError("The border width must be greater than zero.")
	outer_grow, inner_grow = (
		(border, 0.0), (0.0, -border), (border / 2, -border / 2)
	)[side_index]

	def box(grow):
		return (
			center_x - width / 2 - grow, center_y - height / 2 - grow,
			center_x + width / 2 + grow, center_y + height / 2 + grow,
		)

	inner = box(inner_grow)
	if inner[2] - inner[0] <= 0 or inner[3] - inner[1] <= 0:
		raise ValueError("The border is too wide for the region.")
	return box(outer_grow), inner


def create_border(layout, cell, layer, datatype, center_x, center_y, width, height, border, side_index):
	"""Insert the border ring into the cell and return it as a Region."""
	outer, inner = border_boxes(center_x, center_y, width, height, border, side_index)
	dbu = layout.dbu

	def region(rectangle):
		return pya.Region(pya.Box(*(round(value / dbu) for value in rectangle)))

	ring = region(outer) - region(inner)
	cell.shapes(layout.layer(layer, datatype)).insert(ring)
	return ring


def _selected_layer(view):
	try:
		node = view.current_layer
		if node is not None and node.source_layer >= 0:
			return node.source_layer, max(node.source_datatype, 0)
	except Exception:
		pass
	return None


class _BorderDialog:
	"""One form that collects the border parameters."""

	def __init__(self, parent, layer, datatype, region, settings):
		self.result = None
		self.dialog = pya.QDialog(parent)
		self.dialog.setWindowTitle(_TITLE)
		self.dialog.setMinimumWidth(420)
		main = pya.QVBoxLayout(self.dialog)
		form = pya.QFormLayout()

		def spin(value, low, high, decimals=3):
			box = pya.QDoubleSpinBox(self.dialog)
			box.setDecimals(decimals)
			box.setRange(low, high)
			box.setValue(value)
			return box

		def whole(value):
			box = pya.QSpinBox(self.dialog)
			box.setRange(0, 65535)
			box.setValue(value)
			return box

		self.layer = whole(layer)
		self.datatype = whole(datatype)
		form.addRow("Target layer", self.layer)
		form.addRow("Target datatype", self.datatype)

		center_x, center_y, width, height = region
		self.center_x = spin(center_x, -1e6, 1e6)
		self.center_y = spin(center_y, -1e6, 1e6)
		self.width = spin(width, 0.001, 1e6)
		self.height = spin(height, 0.001, 1e6)
		form.addRow("Region center X (um)", self.center_x)
		form.addRow("Region center Y (um)", self.center_y)
		form.addRow("Region width (um)", self.width)
		form.addRow("Region height (um)", self.height)

		self.border = spin(settings.get("border_um", 3.0), 0.001, 1e6)
		form.addRow("Border width (um)", self.border)
		self.side = pya.QComboBox(self.dialog)
		self.side.addItems(list(_SIDES))
		self.side.setCurrentIndex(min(settings.get("border_side", 0), len(_SIDES) - 1))
		form.addRow("Border position", self.side)
		main.addLayout(form)

		buttons = pya.QHBoxLayout()
		buttons.addStretch(1)
		self.create_button = pya.QPushButton("Create", self.dialog)
		self.create_button.setDefault(True)
		self.cancel_button = pya.QPushButton("Cancel", self.dialog)
		buttons.addWidget(self.create_button)
		buttons.addWidget(self.cancel_button)
		main.addLayout(buttons)
		self._layouts = (form, buttons)

		self.create_button.clicked += self._accept
		self.cancel_button.clicked += lambda *_: self.dialog.reject()

	def options(self):
		"""Return the chosen parameters, raising ValueError if the border is invalid."""
		values = {
			"layer": _read(self.layer.value),
			"datatype": _read(self.datatype.value),
			"center_x": _read(self.center_x.value),
			"center_y": _read(self.center_y.value),
			"width": _read(self.width.value),
			"height": _read(self.height.value),
			"border": _read(self.border.value),
			"side_index": _read(self.side.currentIndex),
		}
		border_boxes(
			values["center_x"], values["center_y"], values["width"],
			values["height"], values["border"], values["side_index"],
		)
		return values

	def _accept(self, *_):
		try:
			self.result = self.options()
		except ValueError as error:
			pya.MessageBox.warning(_TITLE, str(error), pya.MessageBox.Ok)
			return
		self.dialog.accept()

	def exec(self):
		return bool(self.dialog.exec_())


def main():
	view = pya.Application.instance().main_window().current_view()
	cellview = view.active_cellview() if view else None
	if cellview is None or not cellview.is_valid():
		pya.MessageBox.warning(_TITLE, "Open a layout and select a cell first.", pya.MessageBox.Ok)
		return
	layout, cell = cellview.layout(), cellview.cell

	settings = _load_settings()
	selected = _selected_layer(view) or (settings.get("border_layer", 1), settings.get("border_datatype", 0))
	bounds = cell.dbbox()
	if bounds.empty():
		region = (0.0, 0.0, 50.0, 50.0)
	else:
		region = (bounds.center().x, bounds.center().y, bounds.width(), bounds.height())

	dialog = _BorderDialog(
		pya.Application.instance().main_window(), selected[0], selected[1], region, settings
	)
	if not dialog.exec() or dialog.result is None:
		return
	options = dialog.result

	view.transaction("Create border")
	try:
		create_border(layout, cell, **options)
	finally:
		view.commit()
	view.add_missing_layers()

	settings.update(
		border_um=options["border"], border_side=options["side_index"],
		border_layer=options["layer"], border_datatype=options["datatype"],
	)
	_save_settings(settings)
	outer, _ = border_boxes(
		options["center_x"], options["center_y"], options["width"],
		options["height"], options["border"], options["side_index"],
	)
	pya.MessageBox.info(
		_TITLE,
		f"Created a {outer[2] - outer[0]:.3f} x {outer[3] - outer[1]:.3f} um border "
		f"on layer {options['layer']}/{options['datatype']} of '{cell.name}'.\n"
		"Trace it with the WMF hatch path tool using the contour pattern.",
		pya.MessageBox.Ok,
	)


if pya.Application.instance().main_window() is not None:
	main()
