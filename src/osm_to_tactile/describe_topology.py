#!/usr/bin/env python3

import argparse
import json
import sys

def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", "-o",
        help="""The name of the output file.""")
    parser.add_argument(
        "inputfile")
    return vars(parser.parse_args())

def describe_topology_main(inputfile, output):
    with open(inputfile, encoding='utf-8') as instream:
        topology = json.load(instream)
    with (open(output, 'w', encoding='utf-8') if output else sys.stdout) as o:
        for name in sorted(topology.keys()):
            street = topology[name]
            split = len(street) > 1
            o.write(("%s (%d sections):\n" % (name, len(street)))
                    if split
                    else ("%s:\n" % name))
            for i, section in enumerate(street, start=1):
                previous_junction = None
                repeat_count = 1
                if split:
                    o.write("  Section %d of %s:\n" % (i, name))
                for junction in section:
                    if junction != previous_junction:
                        o.write("    " + ", and ".join(junction))
                        if repeat_count > 1:
                            o.write(" %d times" % repeat_count)
                        o.write("\n")
                        repeat_count = 1
                        previous_junction = junction
                    else:
                        repeat_count += 1

if __name__ == "__main__":
    describe_topology_main(**get_args())
