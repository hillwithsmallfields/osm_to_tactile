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

from braille_to_geometry.braille_to_geometry import BrailleDotterUKAAF

OSM_DEBUG_FORMAT = "https://www.openstreetmap.org/?mlat=%f&mlon=%f#map=16/%f/%f"
PRETTY_PRINT_SVG = True
LANE_WIDTH = 3

NAME_PREFIXES = {
    'ca': ["Carrer de ", "Avinguda de ", "Travessera de "],
    'es': ["Carrer de ", "Avinguda de ", "Travessera de ", "Calle "],
    'fr': ["Rue ", "Chaussée ", "Place ", "Avenue d'", "Avenue des", "Avenue du'", "Avenue "],
    'pl': ["Aleja ", "Plac "],
    'pt': ["Rua ", "Calçada da ", "Praça ", "Avenida "],
    'sq': ["Rruga i ", "Rruga e ", "Rruga ", "Sheshi", "Shëshitorja ", "Bulevardi "],
    'uk': ["вулиця "],
}

NAME_SUFFIXES = {
    'da': ["gade", "vang", " Vej", " Gade", "varden", " Allé", "pladsen", " Stræde", " Gård", "vej"],
    'de': ["-Weg", "weg", "-Ring", " Winkel", "pfad", "markt", "straße"],
    'en': [" Road", " Street", " Square"],
    'eu': [" kalea"],
    'fi': ["katu"],
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
        "--metres", "--metric", "-m",
        action='store_true',
        help="""Treat size and width as approximate metres""")
    parser.add_argument(
        "--width", "-W",
        type=float)
    parser.add_argument(
        "--height", "-H",
        type=float)
    parser.add_argument(
        "--language", "-l",
        help="""The language to use for street names.
        This is in the format used by OSM, typically an ISO 639
        language code, so for example --language nl will select the
        Dutch names which are given as name:nl in the map data.""")
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
        "--jigsaw", "-j",
        help="""Make interlocking patterns along the edges.
        Not yet implemented.""")
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
        # print("new_coords", new_coords)
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

def jigsaw_edge(jigsaw_spec, edge):
    return ((jigsaw_spec == "all")
            or (edge in jigsaw_spec))

def cut_edges(width, height, jigsaw):
    """Return the SVG text for cutting out the shape of the tile."""
    print("making cut edges for", width, height)
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
            +'\n    z" fill="none" stroke="red" stroke_width="1"/>\n')

def write_svg(output, bbox, drawable, scale=1.0, jigsaw=""):
    """Write the map as SVG."""
    left, bottom, right, top = bbox
    width = (right - left) * scale
    height = (top - bottom) * scale
    with open(output, 'w') as outstream:
        outstream.write('<svg width="%f" height="%f">\n' % (width, height))
        shapes = drawable.svg()
        if PRETTY_PRINT_SVG:
            shapes = shapes.replace(" L ", "\n    L ").replace(" M ", "\n\n    M ").replace("><", ">\n  <")
        outstream.write(shapes)
        outstream.write(cut_edges(width, height, jigsaw or ""))
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
                              projection="EPSG:3857",
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
        print("northeast", OSM_DEBUG_FORMAT % (input_bbox[3], input_bbox[2], input_bbox[3], input_bbox[2]))
    overpass = Overpass()
    query = overpassQueryBuilder(bbox=[input_bbox[1], input_bbox[0], input_bbox[3], input_bbox[2]],
                                 elementType='way',
                                 selector='highway',
                                 includeGeometry=True,
                                 out='body')
    osm_data = overpass.query(query)

    transformer = pyproj.Transformer.from_crs("EPSG:4326", projection)
    output_bbox = transformer.transform_bounds(input_bbox[0], input_bbox[1], input_bbox[2], input_bbox[3])

    ways = osm_data.ways()
    streets = defaultdict(list)
    pavements = []
    crossings = []
    for way in ways:
        tags = way.tags()
        geometry = way.geometry()
        if geometry['type'] != 'LineString':
            print("Skipping a non-LineString highway", tags.get('name', "anon"), geometry['type'])
            continue
        if tags.get('highway') == 'footway':
            match tags.get('footway'):
                case 'sidewalk':
                    pavements.append(Pavement(coords(transformer, output_bbox, geometry), squash=squash))
                case 'crossing':
                    crossings.append(Crossing(coords(transformer, output_bbox, geometry), squash=squash))
        else:
            street = Street(attributes=tags,
                            geometry=coords(transformer, output_bbox, geometry),
                            language=language,
                            squash=squash)
            streets[street.name].append(street)
    return output_bbox, streets, pavements, crossings

def convert_to_islands(streets, pavements=None, crossings=None):
    """Convert the solid ways to a probably contiguous area, with islands in it,
    where each island is the area between streets (or between a street and its pavements).
    If used for cutting, this will result in a cut sheet which can be stuck to a baseboard."""
    return shapely.union_all([s.solid()
                              for sg in streets.values()
                              for s in sg]
                             + [p.solid() for p in pavements] if pavements else []
                             + [c.solid() for c in crossings] if pavements else [])

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

def prepare_map(streets, pavements=None, crossings=None):
    """Prepare the map for output."""
    merged_streets = {}
    for name, street_group in streets.items():
        merged_streets[name] = combine_street_segments(street_group)
    map_shapes = convert_to_islands(merged_streets, pavements, crossings)
    dotter = BrailleDotterUKAAF()
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
                            map_shapes = shapely.union(map_shapes,
                                                       transform_label_geometry(label,
                                                                                seg_mid_x, seg_mid_y,
                                                                                label_width, label_height,
                                                                                rotation))
                            taken = i
                            break;
                    if taken is None:
                        print("Could not find a non-clashing label position for", name)
                    else:
                        print("Took label position choice", taken+1, "for", name)
                else:
                    print("No possible label placements for", name)
    return map_shapes

def osm_to_tactile_main(
        bbox=None,
        osmurl=None,
        west=None, south=None, east=None, north=None,
        centre=None, metres=None, width=None, height=None,
        language=None,
        scale=5000,
        squash=1.0,
        jigsaw=None,
        output=None,
        verbose=False):
    """Fetch the streets in a rectangular area, and output laser cutter data for them.
    The rectangle can be specified as a bounding box or as a centre and width and height."""
    size_scale = 1/111320 if metres else 1
    if bbox:
        west, south, east, north = bbox
    elif centre and width and height:
        west = centre[0] - width/2
        south = centre[1] - height/2
        east = centre[0] + width/2
        north = centre[1] + height/2
    elif osmurl and width and height:
        latitude, longitude = osmurl.split("=")[1].split("/")[1:]
        latitude = float(latitude)
        longitude = float(longitude)
        west = longitude - size_scale * (width/2)
        south = latitude - size_scale * (height/2)
        east = longitude + size_scale * (width/2)
        north = latitude + size_scale * (height/2)
    bbox, streets, pavements, crossings = osm_fetch_streets_in_bbox([west, south, east, north],
                                                                    language=language,
                                                                    squash=squash,
                                                                    verbose=verbose)
    if verbose:
        show(streets, pavements, crossings)
    print("output is", output)
    if output:
        drawable = shapely.affinity.rotate(
            shapely.affinity.scale(
                prepare_map(streets, pavements, crossings),
                xfact=1000/scale, yfact=1000/scale,
                origin=(0.0, 0.0)),
            angle=-90,
        )
        match os.path.splitext(output)[1]:
            # case '.dxf':
            #     write_dxf(output, bbox, drawable, scale=1000/scale, jigsaw=jigsaw)
            case '.svg':
                write_svg(output, bbox, drawable, scale=1000/scale, jigsaw=jigsaw)

if __name__ == "__main__":
    osm_to_tactile_main(**get_args())
