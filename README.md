# NanoPhysics WMF Generator

This command-line tool reads polygon geometry from a selected GDSII layer and writes a continuous raster-style toolpath as a Windows Metafile (`.wmf`). Scan lines are connected inside each shape, including around holes where possible. Each shape's path is stored as a single polyline record, so software that treats each drawing record as one stroke (for example, a Bruker NanoMan AFM lithography import) sees one continuous line per shape.

## Requirements

- Windows, because WMF output is written through the Windows GDI API
- Python 3.10 or newer
- The packages in `requirements.txt` (`gdstk` and `shapely`)

Install the Python packages from the project directory:

```powershell
python -m pip install -r requirements.txt
```

## Basic Use

```powershell
python WMFGenerator.py design.gds --layer 1
```

This uses datatype `0`, a 10 nm scan-line pitch, and a horizontal scan angle. The default output is `design.wmf`; a dimensions report named `design_dimensions.txt` is written beside it.

To trace a border or other thin shape by following its edges instead of scanning in straight lines, use the contour pattern:

```powershell
python WMFGenerator.py design.gds --layer 1 --pattern contour --spiral inward
```

This draws concentric loops spaced at the pitch, joined into one continuous path. `--spiral inward` (the default) starts at the outer edge and works inward; `--spiral outward` starts at the innermost loop and works out. For a border around a hole, inward moves from the outer edge toward the region of interest.

Specify a different output path or adjust the scan settings:

```powershell
python WMFGenerator.py design.gds output.wmf --layer 3 --datatype 0 --spacing-nm 10 --angle 45
```

Process multiple layers in one run by repeating `--layer`:

```powershell
python WMFGenerator.py design.gds --layer 1 --layer 3 --layer 5
```

This writes `design_layer_1.wmf`, `design_layer_3.wmf`, and `design_layer_5.wmf`, each with a matching `_dimensions.txt` report. If an output base path is supplied, the layer suffix is added to that base name. A single `--layer` keeps the normal output filename. All layers are processed before any file is written, so an error on one layer does not leave a partial set of outputs.

Run `python WMFGenerator.py --help` to see all options.

## KLayout Macro

[klayout/generate_wmf.py](klayout/generate_wmf.py) adds **Tools → Generate WMF hatch path from the active cell** to KLayout. The command-line tool is unchanged; the macro saves the layout to a temporary GDS and runs `WMFGenerator.py` in a separate Python interpreter, because KLayout's built-in Python does not include `gdstk` and `shapely`.

To install, copy the macro into KLayout's `pymacros` folder (for example `C:\Users\<you>\KLayout\pymacros`) and restart KLayout. The first run asks for the Python interpreter that has the packages from `requirements.txt` installed and for the location of `WMFGenerator.py`; both are remembered in `wmf_generator_settings.json` in your KLayout folder, along with the last pitch and pattern. Setting the `WMF_GENERATOR_PYTHON` environment variable also selects the interpreter.

The macro opens a single form with checkboxes for the layers in the active cell (`layer/datatype`; all selected layers must share one datatype), the pitch, the pattern (raster, contour inward or contour outward), the scan angle (raster only), whether to place the path's corner at WMF `(0, 0)`, and the output file. The previous choices are remembered. Errors from the tool are shown in a message box.

### Border macro

[klayout/create_border.py](klayout/create_border.py) adds **Tools → Create a border polygon around a region**. It only draws geometry and does not generate a WMF. Install it the same way, then trace the result with the hatch path tool using the contour pattern.

The form asks for the target layer and datatype, the region of interest (center X and Y, width and height in microns), the border width, and where the border sits: outside the region, inside it, or centered on its edge. The region defaults to the bounding box of the active cell, or 50 × 50 µm for an empty cell. The border is added to the layer as one rectangular ring polygon, inside an undo step, and any shapes already on that layer are left in place, so use an empty layer to trace only the border.

## Options

| Option | Description |
| --- | --- |
| `input` | Input GDSII file. |
| `output` | Optional WMF output path. Defaults to the input name with a `.wmf` extension. |
| `--layer` | Required GDS layer number. Repeat the option to process multiple layers in one run. |
| `--datatype` | GDS datatype filter. Defaults to `0`. |
| `--cell` | Cell to process. Required when the GDS contains multiple top-level cells. |
| `--spacing-nm` | Distance between scan lines in nanometers. Defaults to `10`. The value is converted using the GDS library unit. |
| `--angle` | Raster scan-line angle in degrees. Defaults to `0`. Not allowed with `--pattern contour`, because the contour path follows the shape's own edges; rotate the shape in the GDS instead. |
| `--pattern` | `raster` (default) for back-and-forth scan lines, or `contour` for concentric loops that follow the shape's edges. |
| `--spiral` | Contour direction: `inward` (default, outer edge toward the center or hole) or `outward`. Only valid with `--pattern contour`. |
| `--no-recenter` | Disable default extent recentering. By default, the minimum corner of the generated path's bounding box maps to WMF `(0, 0)` and all coordinates are nonnegative; this option keeps the GDS origin at the WMF origin. |

## Outputs

For `output.wmf`, the tool writes `output_dimensions.txt` with the generated path's bounding-box width and height in micrometers. These dimensions are calculated from the GDS physical unit and path coordinates before WMF coordinate normalization.

The WMF is written with an Aldus placeable header, with bounds derived from the generated path coordinates and `2540` logical units per inch. Each connected shape is written as one polyline record containing all of its vertices, rather than as separate move and line records. Coordinates are normalized so the longest extent spans about 30,000 logical units. Consequently, the text report describes the path's intended physical dimensions, but software that opens or imports the WMF may rescale it.

## Geometry and Path Behavior

- Geometry is filtered by layer and datatype, flattened from the selected cell's references, and overlapping polygons are merged.
- Each connected polygon region gets one continuous path. Raster paths alternate scan direction row by row; contour paths run counter-clockwise around concentric inset loops, each joined to the next by a short step inside the shape. Separate regions get separate paths, and no connector joins them.
- Contour loops use sharp (mitred) corners. If the shape thickness is not a multiple of the pitch, an extra centerline loop covers the leftover strip.
- Invalid self-touching GDS boundaries are passed through Shapely's geometry repair so hole boundaries can be recovered. Connectors are routed through visible edges inside the polygon to avoid cutting across holes.
- Rectangles are treated as polygons. Non-polygonal annotations such as labels are not toolpath geometry.

## Limitations

- WMF generation is Windows-only.
- The raster path changes direction at the end of every scan row, so it has many vertices; the vertex count grows as the pitch shrinks.
- Contour fill works best for rectangles and rings. When the inset of a complex shape splits into separate pieces, the loops are still joined into one path, but the connecting steps can be long and retrace material already written.
- Touching or overlapping polygons are merged and treated as one connected region, rather than traced independently.
- Visibility-graph routing checks pairs of polygon boundary vertices. Highly detailed polygons with many vertices can take longer to process, especially when routing around holes.
- Scan lines are spaced at the requested pitch; features narrower than the pitch may not receive a scan line. If a component produces no scan line, generation stops with an error. Reduce `--spacing-nm` to process finer features.
- A WMF file does not guarantee a fixed physical playback size. Use the dimensions text file as the intended GDS-scaled path bounds and verify scaling in the receiving application.
