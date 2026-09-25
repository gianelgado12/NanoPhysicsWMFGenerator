"""Generate continuous WMF hatch paths from one GDSII layer."""

from __future__ import annotations

import argparse
import ctypes
import heapq
import math
import struct
import sys
import tempfile
from pathlib import Path
from typing import Any


def _polygonal_parts(geometry: Any) -> list[Any]:
	if geometry.geom_type == "Polygon":
		return [geometry]
	if hasattr(geometry, "geoms"):
		return [polygon for part in geometry.geoms for polygon in _polygonal_parts(part)]
	return []


def _load_layer_geometry(
	input_path: Path,
	layer: int,
	datatype: int,
	cell_name: str | None,
) -> tuple[Any, float]:
	try:
		import gdstk
		from shapely.geometry import Polygon
		from shapely.ops import unary_union
		from shapely.validation import make_valid
	except ImportError as error:
		raise RuntimeError(
			"Missing dependencies. Install them with: "
			"python -m pip install -r requirements.txt"
		) from error

	library = gdstk.read_gds(str(input_path))
	if cell_name is not None:
		cell = next((item for item in library.cells if item.name == cell_name), None)
		if cell is None:
			names = ", ".join(item.name for item in library.cells)
			raise ValueError(f"Cell '{cell_name}' not found. Available cells: {names}")
		cells = [cell]
	else:
		cells = library.top_level()
		if not cells:
			raise ValueError("The GDS file has no top-level cells.")
		if len(cells) > 1:
			names = ", ".join(cell.name for cell in cells)
			raise ValueError(
				"The GDS file has multiple top-level cells. Choose one with "
				f"--cell. Available cells: {names}"
			)

	polygons = []
	for cell in cells:
		for gds_polygon in cell.get_polygons(layer=layer, datatype=datatype):
			polygon = Polygon(gds_polygon.points)
			if not polygon.is_valid:
				polygon = make_valid(polygon)
			parts = [part for part in _polygonal_parts(polygon) if part.area > 0]
			if not parts:
				raise ValueError(
					f"Could not recover polygon geometry in cell '{cell.name}' "
					f"on layer {layer}, datatype {datatype}."
				)
			polygons.extend(parts)

	if not polygons:
		raise ValueError(
			f"No polygons found on layer {layer}, datatype {datatype}."
		)
	return unary_union(polygons), library.unit


def _line_parts(geometry: Any) -> list[Any]:
	if geometry.is_empty:
		return []
	if geometry.geom_type == "LineString":
		return [geometry] if geometry.length > 0 else []
	if hasattr(geometry, "geoms"):
		return [line for part in geometry.geoms for line in _line_parts(part)]
	return []


def _roundoff_tolerance(geometry: Any) -> float:
	scale = max(1.0, *(abs(value) for value in geometry.bounds))
	return math.ulp(scale) * 256


def _build_visibility_graph(
	polygon: Any,
) -> tuple[list[tuple[float, float]], list[list[tuple[int, float]]]]:
	from shapely.geometry import LineString

	routing_polygon = polygon.buffer(_roundoff_tolerance(polygon))
	vertices = list(dict.fromkeys(
		(x, y)
		for ring in [polygon.exterior, *polygon.interiors]
		for x, y in list(ring.coords)[:-1]
	))
	graph: list[list[tuple[int, float]]] = [[] for _ in vertices]
	for first_index, first in enumerate(vertices):
		for second_index in range(first_index + 1, len(vertices)):
			second = vertices[second_index]
			if routing_polygon.covers(LineString([first, second])):
				distance = math.dist(first, second)
				graph[first_index].append((second_index, distance))
				graph[second_index].append((first_index, distance))
	return vertices, graph


def _shortest_inside_route(
	polygon: Any,
	start: tuple[float, float],
	end: tuple[float, float],
	visibility_graph: tuple[list[tuple[float, float]], list[list[tuple[int, float]]]],
) -> list[tuple[float, float]]:
	from shapely.geometry import LineString

	routing_polygon = polygon.buffer(_roundoff_tolerance(polygon))
	vertices, boundary_graph = visibility_graph
	coordinates = [start, end, *vertices]
	graph: list[list[tuple[int, float]]] = [[] for _ in coordinates]
	for vertex_index, edges in enumerate(boundary_graph):
		for neighbor, distance in edges:
			graph[vertex_index + 2].append((neighbor + 2, distance))

	for endpoint_index, endpoint in enumerate((start, end)):
		for vertex_index, vertex in enumerate(vertices):
			if routing_polygon.covers(LineString([endpoint, vertex])):
				distance = math.dist(endpoint, vertex)
				graph[endpoint_index].append((vertex_index + 2, distance))
				graph[vertex_index + 2].append((endpoint_index, distance))

	distances = [math.inf] * len(coordinates)
	previous = [-1] * len(coordinates)
	distances[0] = 0.0
	queue = [(0.0, 0)]
	while queue:
		distance, current = heapq.heappop(queue)
		if current == 1:
			break
		if distance != distances[current]:
			continue
		for neighbor, edge_length in graph[current]:
			candidate = distance + edge_length
			if candidate < distances[neighbor]:
				distances[neighbor] = candidate
				previous[neighbor] = current
				heapq.heappush(queue, (candidate, neighbor))
	if not math.isfinite(distances[1]):
		raise ValueError("Could not find a continuous connector inside the polygon.")

	route = []
	current = 1
	while current != -1:
		route.append(coordinates[current])
		if current == 0:
			break
		current = previous[current]
	return list(reversed(route))


def _polygon_scan_path(polygon: Any, spacing: float, angle: float) -> Any:
	from shapely.affinity import rotate
	from shapely.geometry import LineString

	origin = (polygon.centroid.x, polygon.centroid.y)
	rotated = rotate(polygon, -angle, origin=origin)
	min_x, min_y, max_x, max_y = rotated.bounds
	coordinates = []
	previous_end = None
	visibility_graph = None
	row = 0
	y = min_y + spacing / 2
	while y < max_y:
		scanline = LineString([(min_x, y), (max_x, y)])
		segments = sorted(
			_line_parts(rotated.intersection(scanline)),
			key=lambda line: line.bounds[0],
		)
		if row % 2:
			segments.reverse()
		for segment in segments:
			segment_coordinates = list(segment.coords)
			if row % 2:
				segment_coordinates.reverse()
			if previous_end is not None:
				straight = LineString([previous_end, segment_coordinates[0]])
				if rotated.covers(straight):
					connector = list(straight.coords)
				else:
					if visibility_graph is None:
						visibility_graph = _build_visibility_graph(rotated)
					connector = _shortest_inside_route(
						rotated, previous_end, segment_coordinates[0], visibility_graph
					)
				coordinates.extend(connector[1:])
			coordinates.extend(segment_coordinates[1:] if coordinates else segment_coordinates)
			previous_end = segment_coordinates[-1]
		row += 1
		y = min_y + spacing / 2 + row * spacing
	if not coordinates:
		raise ValueError(
			"The selected shape is narrower than the scan spacing. "
			"Reduce --spacing-nm."
		)
	return rotate(LineString(coordinates), angle, origin=origin)


def _make_scan_paths(geometry: Any, spacing: float, angle: float) -> list[Any]:
	components = _polygonal_parts(geometry)
	if not components:
		raise ValueError("The selected layer contains no polygonal geometry.")
	return [_polygon_scan_path(polygon, spacing, angle) for polygon in components]


def _path_dimensions_um(paths: list[Any], unit_m: float) -> tuple[float, float]:
	coordinates = [point for path in paths for point in path.coords]
	if not coordinates:
		raise ValueError("No toolpath coordinates were generated.")
	width = max(point[0] for point in coordinates) - min(point[0] for point in coordinates)
	height = max(point[1] for point in coordinates) - min(point[1] for point in coordinates)
	meters_to_micrometers = unit_m * 1e6
	return width * meters_to_micrometers, height * meters_to_micrometers


def _write_dimensions(
	output_path: Path,
	paths: list[Any],
	unit_m: float,
) -> tuple[Path, float, float]:
	width_um, height_um = _path_dimensions_um(paths, unit_m)
	dimensions_path = output_path.with_name(f"{output_path.stem}_dimensions.txt")
	dimensions_path.write_text(
		"Toolpath bounding-box dimensions (source GDS physical scale)\n"
		f"Width: {width_um:.6f} micrometers\n"
		f"Height: {height_um:.6f} micrometers\n\n"
		"The WMF uses normalized logical coordinates; playback software may rescale it.\n",
		encoding="utf-8",
	)
	return dimensions_path, width_um, height_um


def _wmf_coordinates(
	paths: list[Any],
	origin: tuple[float, float] | None,
) -> list[list[tuple[int, int]]]:
	path_coordinates = [list(path.coords) for path in paths]
	all_points = [point for coordinates in path_coordinates for point in coordinates]
	if not all_points:
		raise ValueError("No toolpath coordinates were generated.")
	if origin is None:
		origin_x = min(point[0] for point in all_points)
		origin_y = min(point[1] for point in all_points)
		max_offset = max(
			max(point[0] for point in all_points) - origin_x,
			max(point[1] for point in all_points) - origin_y,
		)
	else:
		origin_x, origin_y = origin
		max_offset = max(
			max(abs(point[0] - origin_x), abs(point[1] - origin_y))
			for point in all_points
		)
	if max_offset <= 0:
		raise ValueError("The toolpath has no representable area in WMF coordinates.")
	scale = 30000 / max_offset

	converted_paths = []
	for coordinates in path_coordinates:
		converted = []
		for x, y in coordinates:
			point = (round((x - origin_x) * scale), round((y - origin_y) * scale))
			if not converted or converted[-1] != point:
				converted.append(point)
		if len(converted) > 1:
			converted_paths.append(converted)
	if not converted_paths:
		raise ValueError("The toolpath is too small to represent in WMF coordinates.")
	return converted_paths


def _placeable_wmf_header(
	paths: list[list[tuple[int, int]]],
) -> bytes:
	points = [point for path in paths for point in path]
	left = min(point[0] for point in points)
	top = min(point[1] for point in points)
	right = max(point[0] for point in points)
	bottom = max(point[1] for point in points)
	if any(value < -32768 or value > 32767 for value in (left, top, right, bottom)):
		raise ValueError("WMF bounds exceed the placeable header coordinate range.")

	header = struct.pack(
		"<IHhhhhHI",
		0x9AC6CDD7,
		0,
		left,
		top,
		right,
		bottom,
		2540,
		0,
	)
	checksum = 0
	for (word,) in struct.iter_unpack("<H", header):
		checksum ^= word
	return header + struct.pack("<H", checksum)


def _write_wmf(
	output_path: Path,
	paths: list[Any],
	origin: tuple[float, float] | None,
) -> None:
	if sys.platform != "win32":
		raise RuntimeError("WMF output currently requires Windows.")

	gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
	handle_type = ctypes.c_void_p
	gdi32.CreateMetaFileW.argtypes = [ctypes.c_wchar_p, handle_type]
	gdi32.CreateMetaFileW.restype = handle_type
	gdi32.MoveToEx.argtypes = [handle_type, ctypes.c_int, ctypes.c_int, handle_type]
	gdi32.MoveToEx.restype = ctypes.c_int
	gdi32.LineTo.argtypes = [handle_type, ctypes.c_int, ctypes.c_int]
	gdi32.LineTo.restype = ctypes.c_int
	gdi32.CloseMetaFile.argtypes = [handle_type]
	gdi32.CloseMetaFile.restype = handle_type
	gdi32.DeleteMetaFile.argtypes = [handle_type]
	gdi32.DeleteMetaFile.restype = ctypes.c_int

	converted_paths = _wmf_coordinates(paths, origin)
	placeable_header = _placeable_wmf_header(converted_paths)
	with tempfile.TemporaryDirectory() as temporary_directory:
		standard_path = Path(temporary_directory) / output_path.name
		device_context = gdi32.CreateMetaFileW(str(standard_path), None)
		if not device_context:
			raise ctypes.WinError(ctypes.get_last_error())

		metafile = None
		try:
			for coordinates in converted_paths:
				start = coordinates[0]
				if not gdi32.MoveToEx(device_context, start[0], start[1], None):
					raise ctypes.WinError(ctypes.get_last_error())
				for x, y in coordinates[1:]:
					if not gdi32.LineTo(device_context, x, y):
						raise ctypes.WinError(ctypes.get_last_error())
		finally:
			metafile = gdi32.CloseMetaFile(device_context)
			if metafile:
				gdi32.DeleteMetaFile(metafile)
		if not metafile:
			raise ctypes.WinError(ctypes.get_last_error())

		output_path.write_bytes(placeable_header + standard_path.read_bytes())


def _build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(
		description="Create a WMF hatch path from polygons in a GDSII layer.",
		epilog=(
			"Example: python WMFGenerator.py chip.gds --layer 3 "
			"--spacing-nm 10 --angle 45"
		),
	)
	parser.add_argument("input", type=Path, help="input GDSII file")
	parser.add_argument(
		"output", nargs="?", type=Path,
		help="output WMF file (defaults to the input name with .wmf extension)",
	)
	parser.add_argument(
		"--layer", dest="layers", action="append", required=True, type=int,
		help="GDS layer number; repeat this option to process multiple layers",
	)
	parser.add_argument(
		"--datatype", type=int, default=0, help="GDS datatype (default: 0)"
	)
	parser.add_argument(
		"--cell", help="cell to export; required when the file has multiple top cells"
	)
	parser.add_argument(
		"--no-recenter",
		action="store_true",
		help="keep the GDS origin instead of placing the extent corner at WMF (0, 0)",
	)
	parser.add_argument(
		"--spacing-nm", type=float, default=10.0,
		help="distance between scan lines in nanometers (default: 10)",
	)
	parser.add_argument(
		"--angle", type=float, default=0.0,
		help="scan-line angle in degrees (default: 0)",
	)
	return parser


def main(argv: list[str] | None = None) -> int:
	parser = _build_parser()
	args = parser.parse_args(argv)
	if not math.isfinite(args.spacing_nm) or args.spacing_nm <= 0:
		parser.error("--spacing-nm must be a finite number greater than zero")
	if not math.isfinite(args.angle):
		parser.error("--angle must be a finite number")

	input_path = args.input.expanduser().resolve()
	base_output_path = (
		args.output.expanduser().resolve()
		if args.output
		else input_path.with_suffix(".wmf")
	)
	if input_path == base_output_path:
		parser.error("input and output paths must be different")
	if not input_path.is_file():
		parser.error(f"input file does not exist: {input_path}")
	layers = list(dict.fromkeys(args.layers))
	output_paths = {
		layer: (
			base_output_path
			if len(layers) == 1
			else base_output_path.with_name(
				f"{base_output_path.stem}_layer_{layer}{base_output_path.suffix}"
			)
		)
		for layer in layers
	}

	try:
		prepared_layers = []
		for layer in layers:
			geometry, unit_m = _load_layer_geometry(
				input_path, layer, args.datatype, args.cell
			)
			spacing = args.spacing_nm / (unit_m * 1e9)
			paths = _make_scan_paths(geometry, spacing, args.angle)
			prepared_layers.append((layer, output_paths[layer], paths, unit_m))

		origin = (0.0, 0.0) if args.no_recenter else None
		for layer, output_path, paths, unit_m in prepared_layers:
			_write_wmf(output_path, paths, origin)
			dimensions_path, width_um, height_um = _write_dimensions(
				output_path, paths, unit_m
			)
			print(f"Layer {layer}: wrote {len(paths)} continuous shape paths to {output_path}")
			print(
				f"Path dimensions: {width_um:.6f} x {height_um:.6f} micrometers "
				f"(details: {dimensions_path})"
			)
	except (OSError, RuntimeError, ValueError) as error:
		parser.error(str(error))

	return 0


if __name__ == "__main__":
	raise SystemExit(main())
