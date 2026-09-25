# NanoPhysics WMF Generator

This command-line tool reads polygon geometry from a selected GDSII layer and writes a continuous raster-style toolpath as a Windows Metafile (`.wmf`). Scan lines are connected inside each shape, including around holes where possible.

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

Specify a different output path or adjust the scan settings:

```powershell
python WMFGenerator.py design.gds output.wmf --layer 3 --datatype 0 --spacing-nm 10 --angle 45
```

Run `python WMFGenerator.py --help` to see all options.

## Options

| Option | Description |
| --- | --- |
| `input` | Input GDSII file. |
| `output` | Optional WMF output path. Defaults to the input name with a `.wmf` extension. |
| `--layer` | Required GDS layer number. |
| `--datatype` | GDS datatype filter. Defaults to `0`. |
| `--cell` | Cell to process. Required when the GDS contains multiple top-level cells. |
| `--spacing-nm` | Distance between scan lines in nanometers. Defaults to `10`. The value is converted using the GDS library unit. |
| `--angle` | Scan-line angle in degrees. Defaults to `0`. |
| `--no-recenter` | Disable default recentering. By default, the selected layer's merged polygon geometry is centered on its area centroid; this option keeps the GDS origin at the WMF origin. |

## Outputs

For `output.wmf`, the tool writes `output_dimensions.txt` with the generated path's bounding-box width and height in micrometers. These dimensions are calculated from the GDS physical unit and path coordinates before WMF coordinate normalization.

The WMF coordinates are normalized to fit the format's logical-coordinate range. Consequently, the text report describes the path's intended physical dimensions, but software that opens or imports the WMF may rescale it.

## Geometry and Path Behavior

- Geometry is filtered by layer and datatype, flattened from the selected cell's references, and overlapping polygons are merged.
- Each connected polygon region gets one continuous back-and-forth path. Separate regions get separate paths.
- Invalid self-touching GDS boundaries are passed through Shapely's geometry repair so hole boundaries can be recovered. Connectors are routed through visible edges inside the polygon to avoid cutting across holes.
- Rectangles are treated as polygons. Non-polygonal annotations such as labels are not toolpath geometry.

## Limitations

- WMF generation is Windows-only.
- Touching or overlapping polygons are merged and treated as one connected region, rather than traced independently.
- Visibility-graph routing checks pairs of polygon boundary vertices. Highly detailed polygons with many vertices can take longer to process, especially when routing around holes.
- Scan lines are spaced at the requested pitch; features narrower than the pitch may not receive a scan line. If a component produces no scan line, generation stops with an error. Reduce `--spacing-nm` to process finer features.
- A WMF file does not guarantee a fixed physical playback size. Use the dimensions text file as the intended GDS-scaled path bounds and verify scaling in the receiving application.
