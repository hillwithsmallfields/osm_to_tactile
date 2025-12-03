#!/usr/bin/env python3

"""Program to output laser-cutter data from OSM."""

import argparse
import os

from collections import defaultdict

import svg
import dobishem.storage as storage
import shapely
import pyproj
from OSMPythonTools.overpass import Overpass, overpassQueryBuilder
import ezdxf

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
    parser.add_argument("--output", "-o")
    parser.add_argument("--verbose", "-v", action='store_true')
    return vars(parser.parse_args())

OSM_DEBUG_FORMAT = "https://www.openstreetmap.org/?mlat=%f&mlon=%f#map=16/%f/%f"

class LinearWay:

    def coords(self):
        try:
            return list(self.geometry.coords)
        except NotImplementedError as e:
            print("error:", e)
            print("when trying to get coords of", self.geometry)
            print("geometry.geoms is", self.geometry.geoms)

    def solid(self):
        return shapely.buffer(self.geometry, self.width / 2)

class Street(LinearWay):

    def __init__(self,
                 geometry,
                 type=None,
                 attributes=None,
                 name=None,
                 subtype=None,
                 ):
        self.attributes = attributes
        self.name = name or attributes.get('name', "<anon>")
        self.subtype = subtype or attributes.get('highway')
        if not attributes:
            self.attributes = {'name': self.name,
                               'subtype': self.subtype}
        self.geometry = geometry
        self.width = 4          # TODO: set according to street data

    def __str__(self):
        return f"<Street {self.subtype} {self.name} {self.geometry}>"

    def json(self):
        return {'type': 'street',
                'subtype': self.subtype,
                'name': self.name,
                'geometry': self.coords()}

class Pavement(LinearWay):

    def __init__(self,
                 geometry,
                 type=None,
                 ):
        self.geometry = geometry
        self.width = 1

    def __str__(self):
        return f"<Pavement {self.geometry}>"

    def json(self):
        return {'type': 'pavement',
                'geometry': self.coords()}

class Crossing(LinearWay):

    def __init__(self,
                 geometry,
                 type=None,
                 ):
        self.geometry = geometry
        self.width = 2

    def __str__(self):
        return f"<Crossing {self.geometry}>"

    def json(self):
        return {'type': 'crossing',
                'geometry': self.coords()}

PRETTY_PRINT = True

def write_svg(output, bbox, streets, pavements, crossings):
    # TODO: flip rotate coordinates
    print("writing output to", output)
    left, bottom, right, top = bbox
    width = right - left
    height = top - bottom
    islands = convert_to_islands(streets, pavements, crossings)
    with open(output, 'w') as outstream:
        outstream.write('<svg width="%f" height="%f">\n' % (width, height))
        outstream.write("<g>\n")
        cuts = islands.svg()
        if PRETTY_PRINT:
            cuts = cuts.replace(" L ", "\n    L ").replace(" M ", "\n\n    M ").replace("><", ">\n  <")
        outstream.write(cuts)
        outstream.write("</g>\n")
        outstream.write("</svg>\n")

def write_dxf(output, bbox, streets, pavements, crossings):
    pass

def coords(transformer, base_x, base_y, limit_x, limit_y, geometry):
    def transform_xy_list(xy_list):
        return [(x-base_x, y-base_y)
                for x, y in (transformer.transform(lon, lat)
                             for lon, lat in xy_list)]
    match geometry['type']:
        case 'LineString':
            unclipped = shapely.LineString(transform_xy_list(geometry['coordinates']))
            result = shapely.clip_by_rect(unclipped,
                                          0, 0,
                                          limit_x-base_x, limit_y-base_y,
                                          )
            return result
        case 'Polygon':
            return shapely.Polygon([transform_xy_list(shape)
                                    for shape in geometry['coordinates']])
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

def osm_fetch_streets_in_bbox(west, south, east, north, verbose=False):
    if verbose:
        print("fetching data in", west, south, east, north)
        print("southwest", OSM_DEBUG_FORMAT % (south, west, south, west))
        print("northeast", OSM_DEBUG_FORMAT % (north, east, north, east))
    overpass = Overpass()
    query = overpassQueryBuilder(bbox=[south, west, north, east],
                                 elementType='way',
                                 selector='highway',
                                 includeGeometry=True,
                                 out='body')
    osm_data = overpass.query(query)

    transformer = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857")
    left, bottom, right, top = transformer.transform_bounds(west, south, east, north)

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
                    pavements.append(Pavement(coords(transformer, left, bottom, right, top, geometry)))
                case 'crossing':
                    crossings.append(Crossing(coords(transformer, left, bottom, right, top, geometry)))
        else:
            street = Street(attributes=tags, geometry=coords(transformer, left, bottom, right, top, geometry))
            streets[street.name].append(street)
    return [left, bottom, right, top], streets, pavements, crossings

def convert_to_islands(streets, pavements, crossings):
    return shapely.union_all([s.solid()
                              for sg in streets.values()
                              for s in sg]
                             + [p.solid() for p in pavements]
                             + [c.solid() for c in crossings])

def osm_to_tactile_main(
        bbox=None,
        osmurl=None,
        west=None, south=None, east=None, north=None,
        centre=None, metres=None, width=None, height=None,
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
    bbox, streets, pavements, crossings = osm_fetch_streets_in_bbox(west, south, east, north)
    if verbose:
        show(streets, pavements, crossings)
    print("output is", output)
    if output:
        match os.path.splitext(output)[1]:
            case '.dxf':
                write_dxf(output, bbox, streets, pavements, crossings)
            case '.svg':
                write_svg(output, bbox, streets, pavements, crossings)

if __name__ == "__main__":
    osm_to_tactile_main(**get_args())
