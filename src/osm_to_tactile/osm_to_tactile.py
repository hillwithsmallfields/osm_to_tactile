#!/usr/bin/env python3

"""Program to output laser-cutter data from OSM."""

import argparse
import os

from collections import defaultdict

import shapely
import pyproj
from OSMPythonTools.overpass import Overpass, overpassQueryBuilder
# import ezdxf

OSM_DEBUG_FORMAT = "https://www.openstreetmap.org/?mlat=%f&mlon=%f#map=16/%f/%f"
PRETTY_PRINT_SVG = True
LANE_WIDTH = 3

def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bbox", "-b", type=float, nargs=4)
    parser.add_argument("--osmurl", "-u")
    parser.add_argument("--west", "-w", type=float)
    parser.add_argument("--south", "-s", type=float)
    parser.add_argument("--east", "-e", type=float)
    parser.add_argument("--north", "-n", type=float)
    parser.add_argument("--centre", "-c", type=float, nargs=2)
    parser.add_argument("--metres", "--metric", "-m", action='store_true',
                        help="""Treat size and width as approximate metres""")
    parser.add_argument("--width", "-W", type=float)
    parser.add_argument("--height", "-H", type=float)
    parser.add_argument("--scale", "-z", type=float, default=5000)
    parser.add_argument("--squash", "-q", type=float, default=1.0)
    parser.add_argument("--output", "-o")
    parser.add_argument("--verbose", "-v", action='store_true')
    return vars(parser.parse_args())

class LinearWay:

    """Common parent class for all linear ways (streets, pavements, crossings)."""

    def __init__(self, geometry=None, width=1, squash=1):
        self.geometry = geometry
        self.width = width
        # make all ways thinner by this factor, because otherwise they
        # can be drawn too thick on large-scale maps:
        self.squash = squash

    def coords(self):
        """Return the coordinate list for this way."""
        try:
            return list(self.geometry.coords)
        except NotImplementedError as e:
            print("error:", e)
            print("when trying to get coords of", self.geometry)
            print("geometry.geoms is", self.geometry.geoms)
            return None

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
                 **kwargs):
        super().__init__(geometry=geometry,
                         width=LANE_WIDTH*int(attributes.get('lanes', '2')),
                         **kwargs)
        self.attributes = attributes
        self.name = name or attributes.get('name', "<anon>")
        self.subtype = subtype or attributes.get('highway')
        if not attributes:
            self.attributes = {'name': self.name,
                               'subtype': self.subtype}

    def __str__(self):
        return f"<Street {self.subtype} {self.name} {self.geometry}>"

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

def write_svg(output, bbox, streets, pavements, crossings, scale=1.0):
    """Write the map as SVG."""
    # TODO: flip rotate coordinates
    left, bottom, right, top = bbox
    width = (right - left) * scale
    height = (top - bottom) * scale
    islands = shapely.affinity.rotate(
        shapely.affinity.scale(
            convert_to_islands(streets, pavements, crossings),
            xfact=scale, yfact=scale,
            origin=(0.0, 0.0)),
        angle=-90,
    )
    with open(output, 'w') as outstream:
        outstream.write('<svg width="%f" height="%f">\n' % (width, height))
        outstream.write("<g>\n")
        cuts = islands.svg()
        if PRETTY_PRINT_SVG:
            cuts = cuts.replace(" L ", "\n    L ").replace(" M ", "\n\n    M ").replace("><", ">\n  <")
        outstream.write(cuts)
        outstream.write("</g>\n")
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
            street = Street(attributes=tags, geometry=coords(transformer, output_bbox, geometry), squash=squash)
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

def osm_to_tactile_main(
        bbox=None,
        osmurl=None,
        west=None, south=None, east=None, north=None,
        centre=None, metres=None, width=None, height=None,
        scale=5000,
        squash=1.0,
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
                                                                    squash=squash,
                                                                    verbose=verbose)
    if verbose:
        show(streets, pavements, crossings)
    print("output is", output)
    if output:
        match os.path.splitext(output)[1]:
            # case '.dxf':
            #     write_dxf(output, bbox, streets, pavements, crossings)
            case '.svg':
                write_svg(output, bbox, streets, pavements, crossings, scale=1000/scale)

if __name__ == "__main__":
    osm_to_tactile_main(**get_args())
