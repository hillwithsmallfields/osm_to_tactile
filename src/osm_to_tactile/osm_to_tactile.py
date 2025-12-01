#!/usr/bin/env python3

"""Program to output laser-cutter data from OSM."""

import argparse
from collections import defaultdict
import dobishem.storage as storage

# import OSMPythonTools
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

    def json(self):
        return {'type': 'street',
                'subtype': self.subtype,
                'name': self.name,
                'geometry': self.geometry}

class Pavement:

    def __init__(self,
                 geometry,
                 type=None,     # throwaway for calling with **args when loading from json
                 ):
        self.geometry = geometry

    def __str__(self):
        return f"<Pavement {self.geometry}>"

    def json(self):
        return {'type': 'pavement',
                'geometry': self.geometry}

class Crossing:

    def __init__(self,
                 geometry,
                 type=None,     # throwaway for calling with **args when loading from json
                 ):
        self.geometry = geometry

    def __str__(self):
        return f"<Crossing {self.geometry}>"

    def json(self):
        return {'type': 'crossing',
                'geometry': self.geometry}

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
    streets = defaultdict(list)
    pavements = []
    crossings = []
    for way in osm_data.ways():
        tags = way.tags()
        geometry = way.geometry()
        if tags.get('highway') == 'footway':
            match tags.get('footway'):
                case 'sidewalk':
                    pavements.append(Pavement(geometry['coordinates']))
                case 'crossing':
                    crossings.append(Crossing(geometry['coordinates']))
        else:
            street = Street(attributes=tags, geometry=geometry['coordinates'])
            streets[street.name].append(street)
    return streets, pavements, crossings

def osm_to_tactile_main(
        bbox=None,
        osmurl=None,
        west=None, south=None, east=None, north=None,
        centre=None, metres=None, width=None, height=None,
        load=None, save=None,
        output=None):
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
    if save:
        storage.save(save,
                     {'streets': {sn: [s.json()
                                       for s in sg]
                                  for sn, sg in streets.items()},
                      'pavements': [p.json() for p in pavements],
                      'crossings': [c.json() for c in crossings]})

if __name__ == "__main__":
    osm_to_tactile_main(**get_args())
