#!/usr/bin/env python3

"""Program to output laser-cutter data from OSM."""

import argparse

# import OSMPythonTools
from OSMPythonTools.overpass import Overpass
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
    parser.add_argument("--metres", "-m", action='store_true',
                        help="""Treat size and width as approximate metres""")
    parser.add_argument("--width", "-W", type=float)
    parser.add_argument("--height", "-H", type=float)
    parser.add_argument("--output", "-o")
    return vars(parser.parse_args())

OSM_DEBUG_FORMAT = "https://www.openstreetmap.org/?mlat=%f&mlon=%f#map=16/%f/%f"

def osm_fetch_streets_in_bbox(west, south, east, north):
    print("fetching data in", west, south, east, north)
    print("southwest", OSM_DEBUG_FORMAT % (south, west, south, west))
    print("northeast", OSM_DEBUG_FORMAT % (north, east, north, east))
    overpass = Overpass()
    osm_data = overpass.query(
         """[bbox:%f, %f, %f, %f];
         ( way; >; ); out body;""" % (south, west, north, east))
    print("osm data is", osm_data)

def osm_to_tactile_main(
        bbox,
        osmurl,
        west, south, east, north,
        centre, metres, width, height,
        output):
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
    streets = osm_fetch_streets_in_bbox(west, south, east, north)

if __name__ == "__main__":
    osm_to_tactile_main(**get_args())
