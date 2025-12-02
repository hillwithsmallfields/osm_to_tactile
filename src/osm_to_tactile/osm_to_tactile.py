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
    parser.add_argument("--load",
                        help="""Load saved data from file instead of using OSM API.""")
    parser.add_argument("--save",
                        help="""Save data to file for re-use.""")
    parser.add_argument("--output", "-o")
    parser.add_argument("--verbose", "-v", action='store_true')
    return vars(parser.parse_args())

OSM_DEBUG_FORMAT = "https://www.openstreetmap.org/?mlat=%f&mlon=%f#map=16/%f/%f"

class Street:

    def __init__(self,
                 geometry,
                 type=None,     # throwaway for calling with **args when loading from json
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

    def __str__(self):
        return f"<Street {self.subtype} {self.name} {self.geometry}>"

    def coords(self):
        return list(self.geometry.coords)

    def json(self):
        return {'type': 'street',
                'subtype': self.subtype,
                'name': self.name,
                'geometry': self.coords()}

class Pavement:

    def __init__(self,
                 geometry,
                 type=None,     # throwaway for calling with **args when loading from json
                 ):
        self.geometry = geometry

    def __str__(self):
        return f"<Pavement {self.geometry}>"

    def coords(self):
        return list(self.geometry.coords)

    def json(self):
        return {'type': 'pavement',
                'geometry': self.coords()}

class Crossing:

    def __init__(self,
                 geometry,
                 type=None,     # throwaway for calling with **args when loading from json
                 ):
        self.geometry = geometry

    def __str__(self):
        return f"<Crossing {self.geometry}>"

    def coords(self):
        return list(self.geometry.coords)

    def json(self):
        return {'type': 'crossing',
                'geometry': self.coords()}

def write_svg(output, streets, pavements, crossings):

    # canvas = svg.SVG(
    #     width=60,
    #     height=60,
    #     elements=[
    #         svg.Circle(
    #             cx=30, cy=30, r=20,
    #             stroke="red",
    #             fill="white",
    #             stroke_width=5,
    #         ),
    #     ],

    pass

def write_dxf(output, streets, pavements, crossings):
    pass

def coords(transformer, base_x, base_y, geometry):
    def transform_xy_list(xy_list):
        return [(x-base_x, y-base_y)
                for x, y in (transformer.transform(lon, lat)
                             for lon, lat in xy_list)]
    match geometry['type']:
        case 'LineString':
            return shapely.LineString(transform_xy_list(geometry['coordinates']))
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
            print("Skipping a non-LineString highway")
            continue
        if tags.get('highway') == 'footway':
            match tags.get('footway'):
                case 'sidewalk':
                    pavements.append(Pavement(coords(transformer, left, bottom, geometry)))
                case 'crossing':
                    crossings.append(Crossing(coords(transformer, left, bottom, geometry)))
        else:
            street = Street(attributes=tags, geometry=coords(transformer, left, bottom, geometry))
            streets[street.name].append(street)
    return streets, pavements, crossings

def osm_to_tactile_main(
        bbox=None,
        osmurl=None,
        west=None, south=None, east=None, north=None,
        centre=None, metres=None, width=None, height=None,
        load=None, save=None,
        output=None,
        verbose=False):
    """Fetch the streets in a rectangular area, and output laser cutter data for them.
    The rectangle can be specified as a bounding box or as a centre and width and height."""
    size_scale = 1/111320 if metres else 1
    if load:
        data = storage.load(load)
        streets = {name: [Street(**s) for s in sg] for name, sg in data['streets'].items()}
        pavements = [Pavement(**p) for p in data['pavements']]
        crossings = [Crossing(**c) for c in data['crossings']]
    else:
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
        streets, pavements, crossings = osm_fetch_streets_in_bbox(west, south, east, north)
    if verbose:
        show(streets, pavements, crossings)
    if save:
        storage.save(save,
                     {'streets': {sn: [s.json()
                                       for s in sg]
                                  for sn, sg in streets.items()},
                      'pavements': [p.json() for p in pavements],
                      'crossings': [c.json() for c in crossings]})
    if output:
        match os.path.splitext(output)[0]:
            case '.dxf':
                write_dxf(output, streets, pavements, crossings)
            case '.svg':
                write_svg(output, streets, pavements, crossings)

if __name__ == "__main__":
    osm_to_tactile_main(**get_args())
