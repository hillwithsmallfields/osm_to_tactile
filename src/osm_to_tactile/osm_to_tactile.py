#!/usr/bin/env python3

"""Program to output laser-cutter data from OSM."""

import argparse
import math
import os

from collections import defaultdict

import shapely
import pyproj
from OSMPythonTools.overpass import Overpass, overpassQueryBuilder
# import ezdxf

from braille_to_geometry.braille_to_geometry import BrailleDotterUKAAF, Diamond, Square, Octagon
import nubs

OSM_DEBUG_FORMAT = "https://www.openstreetmap.org/?mlat=%f&mlon=%f#map=16/%f/%f"
PRETTY_PRINT_SVG = True
LANE_WIDTH = 3

# Some prefixes and suffixes that we remove from street names to make
# the braille labels, in the hope of making it likelier to fit the
# labels in.

NAME_PREFIXES = {
    'ca': ["Carrer de ", "Avinguda de ", "Travessera de "],
    'es': ["Carrer de ", "Avinguda de ", "Travessera de ", "Calle "],
    'fr': ["Rue ", "Chaussée ", "Place ", "Avenue d'", "Avenue des", "Avenue du'", "Avenue "],
    'hr': ["Put "],
    'it': ["Borgo", "Viale ", "Via ", "Vicolo ", "Piazza "],
    'pl': ["Aleja ", "Plac "],
    'pt': ["Rua ", "Calçada da ", "Praça ", "Avenida "],
    'ro': ["Strada ", "Bulevardul ", "Intrarea ", "Calea ", "Splaiul "],
    'sq': ["Rruga i ", "Rruga e ", "Rruga ", "Sheshi", "Shëshitorja ", "Bulevardi "],
    'uk': ["вулиця "],
}

NAME_SUFFIXES = {
    'da': ["gade", "vang", " Vej", " Gade", "varden", " Allé", "pladsen", " Stræde", " Gård", "vej"],
    'de': ["-Weg", "weg", "-Ring", " Winkel", "pfad", "markt", "straße"],
    'en': [" Road", " Street", " Square"],
    'eu': [" kalea"],
    'fi': ["katu"],
    'hr': [" ulica"],
    'hu': [" utca", " tér"],
    'nl': ["straat", "plein", "laan", "steenweg", "steeg", "brug", "gracht"],
    'no': [" allé", " gate", "veien", "gata", "plass", "stredet"],
    'ru': [" переулок", " улица"],
    'se': ["gatan", "vägen", "väg", "stigen"],
    'uk': [" провулок"],
}

def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--bbox", "-b",
        type=float, nargs=4,
        help="""The bounding box of the area to convert, in longitude and latitude.
        Give this as four numbers, in this order: West, South, East, North.
        Use this with --width and --height to specify the area to convert.""")
    parser.add_argument(
        "--osmurl", "-u",
        help="""The OSM URL of the centre of the area to convert.""")
    parser.add_argument(
        "--west", "-w", type=float,
        help="""The longitude of the west edge of the area to convert.""")
    parser.add_argument(
        "--south", "-s", type=float,
        help="""The latitude of the south edge of the area to convert.""")
    parser.add_argument(
        "--east", "-e", type=float,
        help="""The longitude of the east edge of the area to convert.""")
    parser.add_argument(
        "--north", "-n", type=float,
        help="""The latitude of the north edge of the area to convert.""")
    parser.add_argument(
        "--centre", "-c",
        type=float, nargs=2,
        help="""The centre of the area to convert, as longitude and latitude.""")
    parser.add_argument(
        "--width", "-W",
        type=float,
        help="""The width of the resulting map, nominally in millimetres.
        The actual size depends on downstream software and hardware.""")
    parser.add_argument(
        "--height", "-H",
        type=float,
        help="""The heigth of the resulting map, nominally in millimetres.
        The actual size depends on downstream software and hardware.""")
    parser.add_argument(
        "--y-correction",
        type=float, default=1.0,
        help="""A multiplier for the Y coordinates, for if the map looks
        distorted.  I think is something do with the projection, rather
        than working round a bug in the code.""")
    parser.add_argument(
        "--pieces", "-p",
        nargs=2, type=int,
        help="""The number of pieces to cut the map into.
        Jigsaw edges are applied if --jigsaw is also given.""")
    parser.add_argument(
        "--language", "-l",
        help="""The language to use for street names.
        This is in the format used by OSM, typically an ISO 639
        language code, so for example --language nl will select the
        Dutch names which are given as name:nl in the map data.""")
    parser.add_argument(
        "--projection", default="EPSG:3857")
    parser.add_argument(
        "--scale", "-z",
        type=float, default=5000,
        help="""The scale of the map to produce, assuming the output is in millimeters.
        For example, giving 5000 here produces a 1:5000 scale map.""")
    parser.add_argument(
        "--squash", "-q",
        type=float, default=1.0,
        help="""How much to squash roads so they don't take up too
        much space on the map.""")
    parser.add_argument(
        "--label-streets",
        action='store_true',
        help="""Write braille labels alongside streets where there is enough room.""")
    parser.add_argument(
        "--jigsaw", "-j",
        help="""Make interlocking patterns along the edges.
        Not yet implemented.""")
    parser.add_argument(
        "--grid",
        action='store_true',
        help="""Draw a 100m grid over the map.""")
    parser.add_argument(
        "--snap-to-grid",
        action='store_true',
        help="""Snap the bottom left corner of the map to the nearest
        100m grid point in web mercator coordinates.""")
    parser.add_argument(
        "--output", "-o",
        help="""The name of the output file.
        The output format is deduced from the extension; currently only .svg is supported.""")
    parser.add_argument(
        "--verbose", "-v",
        action='store_true')
    return vars(parser.parse_args())

class LinearWay:

    """Common parent class for all linear ways (streets, pavements, crossings)."""

    def __init__(self, geometry=None, width=1, squash=1):
        self.geometry = geometry
        self.width = width
        # make all ways thinner by this factor, because otherwise they
        # can be drawn too thick on large-scale maps:
        self.squash = squash
        self._segments = None
        self._longest_segments = None

    def __iadd__(self, other):
        """Add another way into this one, if they have endpoints in common."""
        my_coords = self.coords()
        their_coords = other.coords()
        new_coords = None
        if hasattr(self, 'type') and hasattr(other, 'type') and self.type != other.type:
            raise ValueError("Type mismatch")
        if my_coords[-1] == their_coords[0]:
            if their_coords[-1] == my_coords[0]:
                raise ValueError("Both endpoints in common")
            new_coords = my_coords + their_coords[1:]
        elif their_coords[-1] == my_coords[0]:
            new_coords = their_coords + my_coords[1:]
        elif my_coords[0] == their_coords[0]:
            new_coords = my_coords + list(reversed(their_coords[1]))
        elif my_coords[-1] == their_coords[-1]:
            new_coords = my_coords + list(reversed(their_coords[:-1]))
        else:
            raise ValueError("No common endpoints")
        self.geometry = shapely.LineString(new_coords)
        for k, v in other.attributes.items():
            if k in self.attributes:
                if v != self.attributes[k]:
                    self.attributes[k] += ("; " + v)
            else:
                 self.attributes[k] = v
        self.width = (self.width + other.width) / 2
        # reset the cache slots
        self._segments = None
        self._longest_segments = None
        return self

    def coords(self):
        """Return the coordinate list for this way."""
        try:
            return list(self.geometry.coords)
        except NotImplementedError as e:
            print("error:", e)
            print("when trying to get coords of", self.geometry)
            print("geometry.geoms is", self.geometry.geoms)
            return None

    def segments(self):
        """Return a list of the straight-line segments of this way,
        in their natural order."""
        if self._segments is None:
            coords = self.coords()
            if coords:
                self._segments = list(zip(coords[:-1], coords[1:]))
        return self._segments

    def longest_segments(self):
        """Return a list of the straight-line segments of this way,
        in descending order of length."""
        if self._longest_segments is None:
            segments = self.segments()
            if segments:
                self._longest_segments = sorted(segments,
                                                key=lambda seg: math.dist(*seg),
                                                reverse=True)
        return self._longest_segments

    def solid(self):
        """Return a 2D solid representing this way.

        A way has no width; the solid form of it has width."""
        return shapely.buffer(self.geometry, self.width / (2 * self.squash))

class Street(LinearWay):

    """A street or road.

    This is a linear feature, and does not include streets which are
    areas.
    """

    def __init__(self,
                 geometry,
                 type=None,
                 attributes=None,
                 name=None,
                 subtype=None,
                 language=None,
                 **kwargs):
        super().__init__(geometry=geometry,
                         width=LANE_WIDTH*int(attributes.get('lanes', '2')),
                         **kwargs)
        self.attributes = attributes
        if name:
            self.name = name
        elif language:
            self.name = (attributes.get('name:' + language)
                         or attributes.get('name', "<anon>"))
        else:
            self.name = attributes.get('name', "<anon>")
        self.language = language
        self._shortened_name = None
        self.subtype = subtype or attributes.get('highway')
        if not attributes:
            self.attributes = {'name': self.name,
                               'subtype': self.subtype}

    def __str__(self):
        return f"<Street {self.subtype} {self.name} {self.geometry}>"

    def __repr__(self):
        return f"<Street {self.subtype} {self.name} {self.geometry}>"

    def shortened_name(self):
        if self._shortened_name is None:
            name = self.name
            if (prefixes := NAME_PREFIXES.get(self.language)):
                for prefix in prefixes:
                    name = name.removeprefix(prefix)
            if (suffixes := NAME_SUFFIXES.get(self.language)):
                for suffix in suffixes:
                    name = name.removesuffix(suffix)
            self._shortened_name = name
        return self._shortened_name

    def json(self):
        return {'type': 'street',
                'subtype': self.subtype,
                'name': self.name,
                'geometry': self.coords()}

class Pavement(LinearWay):

    """A pavement alongside a street (sidewalk in US English)."""

    def __init__(self,
                 geometry,
                 type=None,
                 **kwargs):
        super().__init__(geometry=geometry,
                         width=1,
                         **kwargs)

    def __str__(self):
        return f"<Pavement {self.geometry}>"

    def json(self):
        return {'type': 'pavement',
                'geometry': self.coords()}

class Crossing(LinearWay):

    """A pedestrian crossing such as a zebra crossing."""

    def __init__(self,
                 geometry,
                 type=None,
                 **kwargs):
        super().__init__(geometry=geometry,
                         width=2,
                         **kwargs)

    def __str__(self):
        return f"<Crossing {self.geometry}>"

    def json(self):
        return {'type': 'crossing',
                'geometry': self.coords()}

class Bridge:

    """A bridge carrying a street (or possibly some other kind of way)."""

    def __init__(self, carrying):
        self.carrying = carrying
        self.geometry = shapely.buffer(carrying.geometry, carrying.width*2, cap_style=shapely.BufferCapStyle.flat)

def jigsaw_edge(jigsaw_spec, edge):
    return ((jigsaw_spec == "all")
            or (edge in jigsaw_spec))

def cut_edges(width, height, jigsaw):
    """Return the SVG text for cutting out the shape of the tile."""
    # TODO: use https://www.w3.org/TR/SVG2/paths.html#PathDataCubicBezierCommands
    return ('\n  <path d="M 0 0'
            + (("\n    L %f 0" % width)
               if jigsaw_edge(jigsaw, "south")
               else ("\n    L %f 0" % width))
            + (("\n    L %f %f" % (width, height))
               if jigsaw_edge(jigsaw, "east")
               else ("\n    L %f %f" % (width, height)))
            + (("\n    L 0 %f" % height)
               if jigsaw_edge(jigsaw, "north")
               else ("\n    L 0 %f" % height))
            + (("\n    L 0 0")
               if jigsaw_edge(jigsaw, "west")
               else ("\n    L 0 0"))
            +'\n    z" fill="none" stroke="green" stroke_width="1"/>\n')

def vertical_cut_section(number, section_length, x_position, jigsaw=False):
    """Return the vertical cut section for one square."""
    return (nubs.vertical_with_two_nubs(
        x0=x_position, y0=number*section_length,
        height=section_length,
        first_nub_depth=10, first_nub_breadth=10,
        second_nub_depth=-10, second_nub_breadth=10,
        first_midpoint=20, spacing=60)
            if jigsaw
            else ('   L %f %f\n' % (x_position, number*section_length)))

def vertical_cut(x_position, height, down, jigsaw, colour):
    """Return the SVG for a vertical cut.

    `down` is how many horizontal rows the map is being cut into."""
    section = height/down
    return ('\n  <path d="M %f 0\n' % x_position
            + " ".join(vertical_cut_section(i, section, x_position, jigsaw=jigsaw)
                       for i in range(down+1))
            + '" fill="none"  stroke="%s"/>\n' % colour)

def horizontal_cut_section(number, section_length, y_position, jigsaw=False):
    """Return the horizontal cut section for one square."""
    return (nubs.horizontal_with_two_nubs(
        x0=number*section_length, y0=y_position,
        width=section_length,
        first_nub_depth=10, first_nub_breadth=10,
        second_nub_depth=10, second_nub_breadth=10,
        first_midpoint=20, spacing=60
    )
            if jigsaw
            else ('   L %f %f\n' % (number*section_length, y_position)))

def horizontal_cut(y_position, width, across, jigsaw, colour):
    """Return the SVG for a horizontal cut.

    `across` is how many vertical columns the map is being cut into."""
    section = width/across
    return ('\n  <path d="M 0 %f\n' % y_position
            + " ".join(horizontal_cut_section(i, section, y_position, jigsaw=jigsaw)
                       for i in range(across+1))
            + '"  fill="none"  stroke="%s"/>\n' % colour)

def cut_pieces(width, height,
               across, down,
               jigsaw,
               colour="red"):
    """Return the SVG text for cutting out the shape of the tile.

    The arguments `width` and `height` refer to the overall width and
    height of the map (in output units, typically millimetres).

    `across` and `down` are how many pieces the map is to be cut into
    in each direction.
    """
    return ("\n  ".join([vertical_cut(i*width/across, height, down,
                                      jigsaw, colour)
                       for i in range(1, across)])
            + "\n  ".join([horizontal_cut(i*height/down, width, across,
                                          jigsaw, colour)
                         for i in range(1, down)]))

def grid_100m(x0, y0, width, height, jigsaw=False, colour="orange"):
    return ("\n  ".join(vertical_cut(x0+col*100, height, int(height/100),
                                     jigsaw, colour)
                      for col in range(0, int(width/100)))
            + "\n  ".join(horizontal_cut(y0+row*100, width, int(width/100),
                                         jigsaw, colour)
                          for row in range(0, int(height/100))))

def write_svg(output, bbox, pieces,
              drawable_map, drawable_labels,
              grid=None, jigsaw="", stroke="black", fill="black"):
    """Write the map as SVG."""
    left, bottom, right, top = bbox
    width = right - left
    height = top - bottom
    with open(output, 'w') as outstream:
        outstream.write('<svg width="%f" height="%f">\n' % (width, height))
        shapes = drawable_map.svg(fill_color=fill) + drawable_labels.svg(fill_color='blue')
        if PRETTY_PRINT_SVG:
            shapes = shapes.replace(" L ", "\n    L ").replace(" M ", "\n\n    M ").replace("><", ">\n  <")
        outstream.write(shapes)
        outstream.write(cut_edges(width, height, jigsaw or ""))
        if pieces:
            outstream.write(cut_pieces(width, height, pieces[0], pieces[1], jigsaw))
        if grid:
            outstream.write(grid_100m(grid[0], grid[1], width, height, jigsaw=jigsaw))
        outstream.write("</svg>\n")

# def write_dxf(output, bbox, streets, pavements, crossings):
#     """Write the map as DXF."""
#     pass

def coords(transformer, clip_rect, geometry):
    """Transform all the coordinates in a geometry, using a given transformer.
    This works whether the geometry has a single line or multiple lines.
    The result is clipped to be within the rectangle specified."""

    def transform_xy_list(xy_list):
        """Scale and translate a list of XY coordinate pairs."""
        return [(x-clip_rect[0], y-clip_rect[1])
                for x, y in (transformer.transform(lon, lat)
                             for lon, lat in xy_list)]

    def adjust(shape):
        return shapely.clip_by_rect(shape,
                                    0, 0,
                                    clip_rect[2]-clip_rect[0],
                                    clip_rect[3]-clip_rect[1])

    match geometry['type']:
        case 'LineString':
            return adjust(shapely.LineString(transform_xy_list(geometry['coordinates'])))
        case 'Polygon':
            return adjust(shapely.Polygon([transform_xy_list(shape) for shape in geometry['coordinates']]))
        case _:
            print("Unknown geometry type", _)

def show(streets, pavements, crossings):
    print("Streets:")
    print("========")
    for name in sorted(streets.keys()):
        print("    ", name)
        for feature in streets[name]:
            print("        ", feature)
            for k in sorted(feature.attributes.keys()):
                print("            ", k, feature.attributes[k])
    print("Pavements:")
    print("==========")
    for pavement in pavements:
        print("    ", pavement)
    print("Crossings:")
    print("==========")
    for crossing in crossings:
        print("    ", crossing)

def osm_fetch_streets_in_bbox(input_bbox,
                              transformer,
                              language=None,
                              squash=1.0,
                              verbose=False):
    """Fetch all the streets, pavements and crossings in the given rectangle.

    The output is in the specified projection, which by default is Web Mercator.

    The results, all in the projection coordinates, are:
    - the rectangle bounds
    - the streets
    - the pavements
    - the crossings
    """
    if verbose:
        print("fetching data in", input_bbox[0], input_bbox[1], input_bbox[2], input_bbox[3])
        print("southwest", OSM_DEBUG_FORMAT % (input_bbox[1], input_bbox[0], input_bbox[1], input_bbox[0]))
        print("northwest", OSM_DEBUG_FORMAT % (input_bbox[3], input_bbox[0], input_bbox[3], input_bbox[0]))
        print("southeast", OSM_DEBUG_FORMAT % (input_bbox[1], input_bbox[2], input_bbox[1], input_bbox[2]))
        print("northeast", OSM_DEBUG_FORMAT % (input_bbox[3], input_bbox[2], input_bbox[3], input_bbox[2]))
    overpass = Overpass()
    query = overpassQueryBuilder(bbox=[input_bbox[1], input_bbox[0], input_bbox[3], input_bbox[2]],
                                 elementType='way',
                                 selector='highway',
                                 includeGeometry=True,
                                 out='body')
    osm_data = overpass.query(query)

    output_bbox = transformer.transform_bounds(input_bbox[0], input_bbox[1], input_bbox[2], input_bbox[3])
    print("output_bbox in projection coordinates:", output_bbox)

    ways = osm_data.ways()
    streets = defaultdict(list)
    pavements = []
    crossings = []
    bridges = []
    for way in ways:
        tags = way.tags()
        geometry = way.geometry()
        if geometry['type'] != 'LineString':
            print("Skipping a non-LineString highway", tags.get('name', "anon"), tags.get('highway'), geometry['type'])
            continue
        if not tags.get('tunnel'):
            match tags.get('highway'):
                case 'footway':
                    match tags.get('footway'):
                        case 'sidewalk' | 'steps' | 'pedestrian':
                            pavement = Pavement(coords(transformer, output_bbox, geometry), squash=squash)
                            if tags.get('bridge'):
                                bridges.append(Bridge(pavement))
                            else:
                                pavements.append(pavement)
                        case 'crossing':
                            crossing = Crossing(coords(transformer, output_bbox, geometry), squash=squash)
                            if tags.get('bridge'):
                                bridges.append(Bridge(crossing))
                            else:
                                crossings.append(crossing)
                case _:
                    street = Street(attributes=tags,
                                    geometry=coords(transformer, output_bbox, geometry),
                                    language=language,
                                    squash=squash)
                    if tags.get('bridge'):
                        bridges.append(Bridge(street))
                    else:
                        streets[street.name].append(street)
    return output_bbox, streets, pavements, crossings, bridges

def convert_to_islands(streets, pavements=None, crossings=None, bridges=None):
    """Convert the solid ways to a probably contiguous area, with islands in it,
    where each island is the area between streets (or between a street and its pavements).
    If used for cutting, this will result in a cut sheet which can be stuck to a baseboard."""
    islands = shapely.union_all([s.solid()
                                 for sg in streets.values()
                                 for s in sg]
                                + [p.solid() for p in pavements] if pavements else []
                                + [c.solid() for c in crossings] if pavements else [])
    if bridges:
        for bridge in bridges:
            islands = shapely.difference(islands, bridge.geometry)
        for bridge in bridges:
            islands = shapely.union(islands, bridge.carrying.geometry)
    return islands

def combine_street_segments(street_group):
    """Return a list of streets which have been merged as far as possible.

    Streets may be merged if they have ends in common."""
    if len(street_group) == 1:
        return street_group
    old_len = -1
    while len(street_group) > 1 and len(street_group) != old_len:
        next_stage = []
        old_len = len(street_group)
        base = street_group[0]
        for other in street_group[1:]:
            try:
                base += other
            except ValueError:
                # We couldn't combine other with anything yet, so pass
                # it unchanged into the next stage:
                next_stage.append(other)
            except TypeError as e:
                # Sometimes we get individual coordinates where I was
                # expecting coordinate pairs; not yet sure what is
                # going on there, but we can probably tolerate just
                # not merging them.
                next_stage.append(other)
        # Pass the one we have been accumulating, into the next stage.
        # It goes at the end of the list, so it won't be picked as the
        # accumulator next time round the loop.
        next_stage.append(base)
        street_group = next_stage
    return street_group

def transform_label_geometry(geometry, x, y, label_width, label_height, rotation):
    return shapely.affinity.translate(
        shapely.affinity.rotate(
            shapely.affinity.translate(
                geometry,
                # I have not yet understood this part of the placement, but will keep looking at it
                # xoff=label_width/2,
                # yoff=label_height*2
                xoff=0,
                yoff=0
            ),
            rotation),
        xoff=x, yoff=y)

def prepare_map(streets,
                pavements=None,
                crossings=None,
                bridges=None,
                label_streets=False,
                y_scale_adjust=1.0):
    """Prepare the map for output."""
    merged_streets = {}
    for name, street_group in streets.items():
        merged_streets[name] = combine_street_segments(street_group)
    map_shapes = convert_to_islands(merged_streets, pavements, crossings, bridges)
    labels = []
    dotter = BrailleDotterUKAAF(dot_shape=None,
                                scale=1.25,
                                dot_size=.25,
                                y_scale_adjust=y_scale_adjust)
    if label_streets:
        for name, street_group in merged_streets.items():
            # don't label highly fragmented streets
            if name != "<anon>" and len(street_group) <= 3:
                for street in street_group:
                    # Make the label, and a bounding box for it (for faster clash comparisons):
                    label_text = street.shortened_name()
                    label_bbox = dotter.text_to_bbox(label_text)
                    label_width, label_height = dotter.text_dimensions(label_text)
                    label = dotter.text_to_dots(label_text)
                    possible_label_segments = street.longest_segments()
                    # Try placing the label bbox next to each of the
                    # straight-line segments, starting with the longest,
                    # and check whether it is free from clashes with
                    # anything already drawn (whether the base map, or
                    # previously added labels):
                    if possible_label_segments:
                        taken = None
                        for i, seg in enumerate(possible_label_segments):
                            seg_mid_x = (seg[0][0] + seg[1][0]) / 2
                            seg_mid_y = (seg[0][1] + seg[1][1]) / 2
                            rotation = (math.degrees(math.atan2(seg[1][1] - seg[0][1], seg[1][0] - seg[0][0])) + 180) % 180
                            if shapely.disjoint(map_shapes, transform_label_geometry(label_bbox,
                                                                                     seg_mid_x, seg_mid_y,
                                                                                     label_width, label_height,
                                                                                     rotation)):
                                label = transform_label_geometry(label,
                                                                 seg_mid_x, seg_mid_y,
                                                                 label_width, label_height,
                                                                 rotation)
                                map_shapes = shapely.union(map_shapes, label)
                                labels.append(label)
                                taken = i
                                break;
                        if taken is None:
                            print("Could not find a non-clashing label position for", name)
                        else:
                            print("Took label position choice", taken+1, "for", name)
                    else:
                        print("No possible label placements for", name)
    return map_shapes, shapely.union_all(labels)

def scale_rotate_translate(features, scale, y_correction, height):
    return shapely.affinity.translate(
            shapely.affinity.rotate(
                shapely.affinity.scale(
                    features,
                    xfact=1000/(scale*y_correction), yfact=1000/scale,
                    origin=(0.0, 0.0)),
                angle=-90,
                origin=(0, 0),
            ),
            xoff=0,
            yoff=height
        )

def osm_to_tactile_main(
        bbox=None,
        west=None, south=None, east=None, north=None,
        osmurl=None,
        centre=None,
        width=None, height=None, # output map size in millimetres
        projection="EPSG:3857",
        scale=5000,
        y_correction=1.0,
        pieces=None,
        language=None,
        squash=1.0,
        jigsaw=None,
        grid=False,
        snap_to_grid=False,
        label_streets=False,
        output=None,
        verbose=False):

    """Fetch the streets in a rectangular area, and output laser cutter data for them.
    The rectangle can be specified as a bounding box or as a centre and width and height."""

    transformer = pyproj.Transformer.from_crs("EPSG:4326", projection)

    print("starting conversion; width", width, "height", height)

    if not west and not south and not east and not north:
        if bbox:
            west, south, east, north = bbox
            # TODO: calculate scale
        elif width and height:
            if osmurl:
                latitude, longitude = [float(arg) for arg in osmurl.split("=")[1].split("/")[1:]]
            elif centre:
                latitude, longitude = centre

            # We have latitude and longitude, but we want to calculate
            # the bounding box in web mercator, which is in metres:
            centre_x, centre_y = transformer.transform(longitude, latitude)

            # The 1000 is because our coordinates are in metres, but
            # the output map size is given in millimetres.  And the
            # width and height are swapped, because I got coordinates
            # swapped somewhere else and haven't found time to find
            # where, hence the rotation of the map by 90 degrees.
            map_height_on_ground = width * scale / 1000
            map_width_on_ground = y_correction * height * scale / 1000

            left = centre_x - map_width_on_ground/2
            bottom = centre_y - map_height_on_ground/2
            right = centre_x + map_width_on_ground/2
            top = centre_y + map_height_on_ground/2

            # Get the westmost and southmost grid lines, in web
            # mercator metres.  We need these both for drawing a grid,
            # and for snapping to coordinates to a grid.  We'll
            # calculate them anyway.
            left_most_100m_grid_line = ((left//100)+1)*100
            bottom_most_100m_grid_line = ((bottom//100)+1)*100

            # Get the westmost and southmost 100m grid lines in metres
            # relative to the corner of the map:
            left_grid = (left_most_100m_grid_line - left) * 1000 / scale
            bottom_grid = (bottom_most_100m_grid_line - bottom) * 1000 / scale

            if snap_to_grid:
                left -= left_grid
                bottom -= bottom_grid
                right -= left_grid
                top -= bottom_grid
                left_grid = 0
                bottom_grid = 0

            # We want these back into longitude and latitude for the OSM API to fetch:
            west, south = transformer.transform(left, bottom, direction=pyproj.enums.TransformDirection.INVERSE)
            east, north = transformer.transform(right, top, direction=pyproj.enums.TransformDirection.INVERSE)

            if verbose:
                print("centre (longitude, latitude):", longitude, latitude)
                print("centre (webmercator x, y):", centre_x, centre_y)
                print("resulting map in mm (w, h):", width, height)
                print("map size on ground (w, h):", map_width_on_ground, map_height_on_ground)
                print("webmercator bbox (lbrt):", left, bottom, right, top)
                print("longlat bounding box (wsen):", west, south, east, north)

        else:
            print("Not enough information given to determine rectangle to convert")
            raise ValueError("Not enough information given to determine rectangle to convert")
    bbox, streets, pavements, crossings, bridges = osm_fetch_streets_in_bbox(
        [west, south, east, north],
        transformer=transformer,
        language=language,
        squash=squash,
        verbose=verbose)

    if verbose:
        print("output bbox generated as", bbox)
        show(streets, pavements, crossings)

    if output:
        print("scale is", scale, "so scale factor is", 1000/scale)
        map_shapes, label_shapes = prepare_map(streets,
                                               pavements=pavements,
                                               crossings=crossings,
                                               bridges=bridges,
                                               label_streets=label_streets,
                                               y_scale_adjust=1.0/y_correction)
        drawable_map = scale_rotate_translate(map_shapes, scale, y_correction, height)
        drawable_labels = scale_rotate_translate(label_shapes, scale, y_correction, height)
        match os.path.splitext(output)[1]:
            # case '.dxf':
            #     write_dxf(output, bbox, drawable_map, scale=1000/scale, jigsaw=jigsaw)
            case '.svg':
                print("writing SVG; width", width, "height", height)
                write_svg(output,
                          [0, 0, width, height], # bbox,
                          pieces,
                          drawable_map,
                          drawable_labels,
                          grid=(left_grid, bottom_grid) if grid else None,
                          jigsaw=jigsaw)

if __name__ == "__main__":
    osm_to_tactile_main(**get_args())
