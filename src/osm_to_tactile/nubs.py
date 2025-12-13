"""
Create SVG fragments for jigsaw-style "nub" cutting.

Intended for laser-cut tactile map tiles.

Parameterizing the nubs means we can make the tile shapes unique within an area, so they will fit together only one way.
"""

def vertical_with_nub(x0, y0, height, nub_depth, nub_breadth, midpoint):
    x1 = x0 + nub_depth
    y1 = y0 + height
    return f'''\nM {x0} {y0}
                 L {x0} {midpoint-nub_breadth}
                 C {x0} {midpoint+nub_breadth}    {x1} {midpoint-2*nub_breadth}  {x1} {midpoint}
                 C {x1} {midpoint+2*nub_breadth}  {x0} {midpoint-nub_breadth}    {x0} {midpoint+nub_breadth}
                 L {x0} {y1}\n'''

def horizontal_with_nub(x0, y0, height, nub_depth, nub_breadth, midpoint):
    x1 = x0 + height
    y1 = y0 + nub_depth
    return f'''\nM {x0}                     {y0}
                 L {midpoint-nub_breadth}   {y0}
                 C {midpoint+nub_breadth}   {y0}    {midpoint-2*nub_breadth} {y1}     {midpoint}             {y1}
                 C {midpoint+2*nub_breadth} {y1}    {midpoint-nub_breadth}   {y0}     {midpoint+nub_breadth} {y0}
                 L {x1}                     {y0}\n'''

def vertical_with_two_nubs(x0, y0, height,
                           first_nub_depth, first_nub_breadth,
                           second_nub_depth, second_nub_breadth,
                           first_midpoint, spacing):
    return (vertical_with_nub(x0, y0,
                              height/2,
                              first_nub_depth, first_nub_breadth, first_midpoint)
            + vertical_with_nub(x0, y0+height/2,
                                height/2,
                                second_nub_depth, second_nub_breadth,
                                first_midpoint+spacing))

def horizontal_with_two_nubs(x0, y0, width,
                             first_nub_depth, first_nub_breadth,
                             second_nub_depth, second_nub_breadth,
                             first_midpoint, spacing):
    return (horizontal_with_nub(x0, y0,
                                width/2,
                                first_nub_depth, first_nub_breadth, first_midpoint)
            + horizontal_with_nub(x0, y0+width/2,
                                  width/2,
                                  second_nub_depth, second_nub_breadth,
                                  first_midpoint+spacing))
