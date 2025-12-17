# osm_to_tactile: make 2.5D data from openstreetmap

This software fetches street data from openstreetmap.org and converts
it to a format that can be sent to a laser cutter (possibly via some
other tools).

The only output format currently supported is SVG (scalable vector
graphics); if your laser cutter does not support this directly, you
should be able to import it into a tool that will convert it to the
required format (InkScape may well be suitable).

Engraving the output onto acrylic works well, but you could try other
materials, and, depending on the scale and the strength of the
material, it might work to cut the street shapes out and stick them
down on a backing plate.

The software can output cut lines to cut the result into tiles, and it
can also snap your input coordinates to a 100m grid (in web mercator
coordinates) which means you should be able to fit together map pieces
from separate runs.

There is the beginning of an option to put jigsaw-style "nubs" onto
the cut tiles, with each 100m position having a different arrangement
of nubs based on its coordinates (so tiles will only fit together with
their correct neighbours) but that isn't ready yet.

There are currently a large number of command line input options; some
or all of these may get moved to a config file in a later version.

## Simple usage instructions

You will need to set the output size using the options `--width` and
`--height`, in whatever units your laser cutter system consumes.  I've
been treating them as millimetres, but it's possible they'll get
rescaled downstream.

There are several ways to select the area to convert.  The one I've
settled on for practical use is to give it an OSM URL, with the option
`--osmurl`, to specify the centre of the area, and to set the scale
with `--scale`; it will then work out the boundaries of the rectangle
to convert from the scale and from the width and height of the output.
If `--snap-to-grid` is given, the coordinates will be adjusted so that
the west and south edges are on exact multiples of 100 metres in the
web mercator projection.

The map may be scaled unequally in X and Y; I think this is connected
with the map projection used.  You can compensate for it with
`--y-correction`.  I've been using 0.65 for this (in Belgium); I think
it might be nearer to 1.0 nearer the equator.

To cut the map into pieces (tiles), specify `--pieces` with two
numbers, which are how many pieces to cut it into horizontally and
vertically.  For example, `--pieces 3 2` will make 3 columns and 2
rows of tiles.

Specify the output file with `--output`.  The output format is
selected automatically from the filename; currently only `.svg` is
supported, but I hope to add `.dxf` sometime.

Streets, pavements (sidewalks), and crossings are drawn.  They have
preset widths; multi-lane roads are drawn wider.  The thickness are
roughly in proportion to the real widths; you can narrow them with
`--squash` and a number to divide the width by.

With the option `--label-streets`, braille labels are drawn where
there is room for them; currently the placement is inaccurate in some
circumstances but it may work for yours.  In multilingual countries,
you can select which language to use for the names by giving
`--language` followed by a two-letter country code.

The option `--topology` with a filename ending in `.json` or `.yaml`
will produce a data file describing the streets mapped.  This is
reasonably readable to technically-minded people, but is intended for
processing by a separate program into a more human-readable form.
