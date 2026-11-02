# $description: Generate WMF hatch path from the active cell
# $show-in-menu
# $menu-path: tools_menu.end

"""KLayout macro that runs WMFGenerator.py on the layers of the active cell.

KLayout's embedded Python lacks gdstk and shapely, so the layout is saved to a
temporary GDS and the command-line tool runs in a separate Python interpreter.
"""

import json
import os
import shutil
import subprocess
import tempfile

import pya

_TITLE = "WMF hatch path"
_SETTINGS_FILE = "wmf_generator_settings.json"


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


def _run(command):
	# KLayout exports PYTHONHOME for its own Python, which breaks any other interpreter.
	environment = {
		key: value for key, value in os.environ.items()
		if key.upper() not in ("PYTHONHOME", "PYTHONPATH")
	}
	return subprocess.run(
		command,
		capture_output=True,
		text=True,
		errors="replace",
		env=environment,
		creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
	)


def interpreter_ready(python_path):
	"""Return True if the interpreter can import the tool's dependencies."""
	if not python_path or not os.path.isfile(python_path):
		return False
	try:
		return _run([python_path, "-c", "import gdstk, shapely"]).returncode == 0
	except OSError:
		return False


def build_command(
	python_path, script_path, gds_path, output_path, cell_name, layers, datatype,
	pitch_nm, pattern, angle=None, spiral=None, recenter=True,
):
	"""Build the WMFGenerator.py command line for the given options."""
	command = [
		python_path, script_path, gds_path, output_path,
		"--cell", cell_name,
		"--datatype", str(datatype),
		"--spacing-nm", str(pitch_nm),
		"--pattern", pattern,
	]
	for layer in layers:
		command += ["--layer", str(layer)]
	if pattern == "raster" and angle is not None:
		command += ["--angle", str(angle)]
	if pattern == "contour" and spiral is not None:
		command += ["--spiral", spiral]
	if not recenter:
		command.append("--no-recenter")
	return command


def run_generation(layout, cell_name, python_path, script_path, output_path, **options):
	"""Save the layout to a temporary GDS and run the generator on it."""
	with tempfile.TemporaryDirectory() as folder:
		gds_path = os.path.join(folder, "export.gds")
		layout.write(gds_path)
		command = build_command(
			python_path, script_path, gds_path, output_path, cell_name, **options
		)
		return _run(command)


def _layers_in_cell(layout, cell):
	found = []
	for index in layout.layer_indexes():
		if not cell.bbox_per_layer(index).empty():
			info = layout.get_info(index)
			found.append((info.layer, info.datatype))
	return sorted(found)


def _text(value):
	"""Unwrap a KLayout dialog result, which is a StringValue or None."""
	if value is None:
		return None
	return value.to_s() if hasattr(value, "to_s") else str(value)


def _read(getter):
	"""Read a Qt value that KLayout exposes as either a property or a method."""
	return getter() if callable(getter) else getter


def _ask_file(title, current, file_filter):
	return _text(pya.FileDialog.ask_open_file_name(
		title, os.path.dirname(current) if current else "", file_filter
	))


def _resolve_tools(settings):
	"""Find or ask for the interpreter and WMFGenerator.py; return (python, script)."""
	python_path = (
		settings.get("python")
		or os.environ.get("WMF_GENERATOR_PYTHON")
		or shutil.which("python")
	)
	while not interpreter_ready(python_path):
		pya.MessageBox.info(
			_TITLE,
			"Select a Python interpreter that has gdstk and shapely installed "
			"(see requirements.txt in the project folder).",
			pya.MessageBox.Ok,
		)
		python_path = _ask_file("Select python.exe", python_path, "Python (python.exe)")
		if not python_path:
			return None, None

	script_path = settings.get("script")
	macro_file = globals().get("__file__")
	if not (script_path and os.path.isfile(script_path)) and macro_file:
		guess = os.path.join(os.path.dirname(os.path.dirname(macro_file)), "WMFGenerator.py")
		script_path = guess if os.path.isfile(guess) else None
	if not script_path:
		script_path = _ask_file("Select WMFGenerator.py", "", "WMFGenerator.py (WMFGenerator.py)")
		if not script_path:
			return None, None
	return python_path, script_path


def _selected_layer(view):
	try:
		node = view.current_layer
		if node is not None and node.source_layer >= 0:
			return node.source_layer, max(node.source_datatype, 0)
	except Exception:
		pass
	return None


_PATTERNS = ("Raster", "Contour - inward", "Contour - outward")


class _OptionsDialog:
	"""One form that collects every generation option."""

	def __init__(self, parent, available, checked, settings, default_output):
		self.result = None
		self.dialog = pya.QDialog(parent)
		self.dialog.setWindowTitle(_TITLE)
		self.dialog.setMinimumWidth(560)
		main = pya.QVBoxLayout(self.dialog)

		layers_box = pya.QGroupBox("Layers (layer/datatype)", self.dialog)
		grid = pya.QGridLayout(layers_box)
		self.layer_boxes = []
		for position, key in enumerate(available):
			box = pya.QCheckBox(f"{key[0]}/{key[1]}", layers_box)
			box.setChecked(key in checked)
			grid.addWidget(box, position // 4, position % 4)
			self.layer_boxes.append((key, box))
		main.addWidget(layers_box)

		form = pya.QFormLayout()
		self.pitch = pya.QDoubleSpinBox(self.dialog)
		self.pitch.setDecimals(3)
		self.pitch.setRange(0.001, 1e6)
		self.pitch.setValue(settings.get("pitch_nm", 10.0))
		form.addRow("Pitch between scan lines (nm)", self.pitch)

		self.pattern = pya.QComboBox(self.dialog)
		self.pattern.addItems(list(_PATTERNS))
		self.pattern.setCurrentIndex(min(settings.get("pattern_index", 0), len(_PATTERNS) - 1))
		form.addRow("Fill pattern", self.pattern)

		self.angle = pya.QDoubleSpinBox(self.dialog)
		self.angle.setDecimals(3)
		self.angle.setRange(-360, 360)
		self.angle.setValue(settings.get("angle", 0.0))
		form.addRow("Raster scan angle (degrees)", self.angle)

		self.recenter = pya.QCheckBox("Place the path's lower-left corner at WMF (0, 0)", self.dialog)
		self.recenter.setChecked(settings.get("recenter", True))
		form.addRow(self.recenter)

		self.output = pya.QLineEdit(self.dialog)
		self.output.setText(default_output)
		self.browse_button = pya.QPushButton("Browse...", self.dialog)
		output_row = pya.QHBoxLayout()
		output_row.addWidget(self.output)
		output_row.addWidget(self.browse_button)
		form.addRow("Output file", output_row)
		main.addLayout(form)
		main.addWidget(pya.QLabel("A _layer_N suffix is added to the name when several layers are selected.", self.dialog))

		button_row = pya.QHBoxLayout()
		button_row.addStretch(1)
		self.generate_button = pya.QPushButton("Generate", self.dialog)
		self.generate_button.setDefault(True)
		self.cancel_button = pya.QPushButton("Cancel", self.dialog)
		button_row.addWidget(self.generate_button)
		button_row.addWidget(self.cancel_button)
		main.addLayout(button_row)
		self._layouts = (grid, form, output_row, button_row)

		self.pattern.currentIndexChanged += self._pattern_changed
		self.browse_button.clicked += self._browse
		self.generate_button.clicked += self._accept
		self.cancel_button.clicked += lambda *_: self.dialog.reject()
		self._pattern_changed()

	def _pattern_changed(self, *_):
		self.angle.setEnabled(_read(self.pattern.currentIndex) == 0)

	def _browse(self, *_):
		chosen = _text(pya.FileDialog.ask_save_file_name(
			"Save WMF", _read(self.output.text), "WMF files (*.wmf)"
		))
		if chosen:
			self.output.setText(chosen)

	def options(self):
		"""Return the chosen options, raising ValueError if the form is invalid."""
		selected = [key for key, box in self.layer_boxes if _read(box.isChecked)]
		if not selected:
			raise ValueError("Select at least one layer.")
		if len({datatype for _, datatype in selected}) > 1:
			raise ValueError("All selected layers must use the same datatype.")
		output_path = _read(self.output.text).strip()
		if not output_path:
			raise ValueError("Choose an output file.")
		index = _read(self.pattern.currentIndex)
		return {
			"layers": [layer for layer, _ in selected],
			"datatype": selected[0][1],
			"pitch_nm": _read(self.pitch.value),
			"pattern": "raster" if index == 0 else "contour",
			"spiral": None if index == 0 else ("inward" if index == 1 else "outward"),
			"angle": _read(self.angle.value) if index == 0 else None,
			"recenter": _read(self.recenter.isChecked),
			"output_path": output_path,
			"pattern_index": index,
		}

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

	available = _layers_in_cell(layout, cell)
	if not available:
		pya.MessageBox.warning(_TITLE, f"Cell '{cell.name}' has no shapes.", pya.MessageBox.Ok)
		return

	settings = _load_settings()
	python_path, script_path = _resolve_tools(settings)
	if not python_path:
		return

	remembered = {
		tuple(int(part) for part in item.split("/")) for item in settings.get("layers", [])
	}
	selected = _selected_layer(view)
	checked = (
		{selected} if selected in available
		else remembered & set(available) or {available[0]}
	)
	layout_file = cellview.filename()
	default_output = os.path.splitext(layout_file)[0] + ".wmf" if layout_file else cell.name + ".wmf"

	dialog = _OptionsDialog(
		pya.Application.instance().main_window(), available, checked, settings, default_output
	)
	if not dialog.exec() or dialog.result is None:
		return
	options = dict(dialog.result)
	output_path = options.pop("output_path")
	pattern_index = options.pop("pattern_index")

	result = run_generation(layout, cell.name, python_path, script_path, output_path, **options)

	settings.update(
		python=python_path, script=script_path, pitch_nm=options["pitch_nm"],
		pattern_index=pattern_index, recenter=options["recenter"],
		angle=options["angle"] if options["angle"] is not None else settings.get("angle", 0.0),
		layers=[f"{layer}/{options['datatype']}" for layer in options["layers"]],
	)
	_save_settings(settings)

	if result.returncode == 0:
		pya.MessageBox.info(_TITLE, result.stdout.strip(), pya.MessageBox.Ok)
	else:
		lines = [line for line in result.stderr.splitlines() if line.strip()]
		errors = [line for line in lines if "error:" in line]
		pya.MessageBox.critical(_TITLE, "\n".join(errors or lines[-5:]), pya.MessageBox.Ok)


if pya.Application.instance().main_window() is not None:
	main()
