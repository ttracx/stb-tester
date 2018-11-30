# coding: utf-8

import argparse
import itertools
import os
import sys
from collections import OrderedDict
from contextlib import contextmanager
from textwrap import dedent

from .config import get_config
from .utils import mkdir_p

_debug_level = None


def debug(msg):
    """Print the given string to stderr if stbt run `--verbose` was given."""
    if get_debug_level() > 0:
        sys.stderr.write(
            "%s: %s\n" % (os.path.basename(sys.argv[0]), msg))


def ddebug(s):
    """Extra verbose debug for stbt developers, not end users"""
    if get_debug_level() > 1:
        sys.stderr.write("%s: %s\n" % (os.path.basename(sys.argv[0]), s))


def warn(s):
    sys.stderr.write("%s: warning: %s\n" % (
        os.path.basename(sys.argv[0]), s))


def get_debug_level():
    global _debug_level
    if _debug_level is None:
        _debug_level = get_config('global', 'verbose', type_=int)
    return _debug_level


@contextmanager
def scoped_debug_level(level):
    global _debug_level
    oldlevel = _debug_level
    _debug_level = level
    try:
        yield
    finally:
        _debug_level = oldlevel


def argparser_add_verbose_argument(argparser):
    class IncreaseDebugLevel(argparse.Action):
        num_calls = 0

        def __call__(self, parser, namespace, values, option_string=None):
            global _debug_level
            self.num_calls += 1
            _debug_level = self.num_calls
            setattr(namespace, self.dest, _debug_level)

    argparser.add_argument(
        '-v', '--verbose', action=IncreaseDebugLevel, nargs=0,
        default=get_debug_level(),  # for stbt-run arguments dump
        help='Enable debug output (specify twice to enable GStreamer element '
             'dumps to ./stbt-debug directory)')


class ImageLogger(object):
    """Log intermediate images used in image processing (such as `match`).

    Create a new ImageLogger instance for each frame of video.
    """
    _frame_number = itertools.count(1)

    def __init__(self, name, **kwargs):
        self.enabled = get_debug_level() > 1
        if not self.enabled:
            return

        self.name = name
        self.frame_number = ImageLogger._frame_number.next()

        try:
            outdir = os.path.join("stbt-debug", "%05d" % self.frame_number)
            mkdir_p(outdir)
            self.outdir = outdir
        except OSError:
            warn("Failed to create directory '%s'; won't save debug images."
                 % outdir)
            self.enabled = False
            return

        self.images = OrderedDict()
        self.pyramid_levels = set()
        self.data = {}
        for k, v in kwargs.iteritems():
            self.data[k] = v

    def set(self, **kwargs):
        if not self.enabled:
            return
        for k, v in kwargs.iteritems():
            self.data[k] = v

    def append(self, **kwargs):
        if not self.enabled:
            return
        for k, v in kwargs.iteritems():
            if k not in self.data:
                self.data[k] = []
            self.data[k].append(v)

    def imwrite(self, name, image, regions=None, colours=None):
        import cv2
        import numpy
        if not self.enabled:
            return
        if name in self.images:
            raise ValueError("Image for name '%s' already logged" % name)
        if image.dtype == numpy.float32:
            # Scale `cv2.matchTemplate` heatmap output in range
            # [0.0, 1.0] to visible grayscale range [0, 255].
            image = cv2.convertScaleAbs(image, alpha=255)
        else:
            image = image.copy()
        self.images[name] = image
        if regions is None:
            regions = []
        elif not isinstance(regions, list):
            regions = [regions]
        if colours is None:
            colours = []
        elif not isinstance(colours, list):
            colours = [colours]
        for region, colour in zip(regions, colours):
            cv2.rectangle(
                image, (region.x, region.y), (region.right, region.bottom),
                colour, thickness=1)

        cv2.imwrite(os.path.join(self.outdir, name + ".png"), image)

    def html(self, template, **kwargs):
        if not self.enabled:
            return

        try:
            import jinja2
        except ImportError:
            warn(
                "Not generating html view of the image-processing debug images "
                "because python 'jinja2' module is not installed.")
            return

        with open(os.path.join(self.outdir, "index.html"), "w") as f:
            f.write(jinja2.Template(_INDEX_HTML_HEADER)
                    .render(frame_number=self.frame_number)
                    .encode("utf-8"))
            f.write(jinja2.Template(dedent(template.lstrip("\n")))
                    .render(**kwargs)
                    .encode("utf-8"))
            f.write(jinja2.Template(_INDEX_HTML_FOOTER)
                    .render()
                    .encode("utf-8"))


_INDEX_HTML_HEADER = dedent(u"""\
    <!DOCTYPE html>
    <html lang='en'>
    <head>
    <meta charset="utf-8"/>
    <link href="http://netdna.bootstrapcdn.com/twitter-bootstrap/2.3.2/css/bootstrap-combined.min.css" rel="stylesheet">
    <style>
        a.nav { margin: 10px; }
        a.nav[href*="/00000/"] { visibility: hidden; }
        a.nav.pull-left { margin-left: 0; }
        a.nav.pull-right { margin-right: 0; }
        h5 { margin-top: 40px; }

        /* match */
        .table th { font-weight: normal; background-color: #eee; }
        img.thumb {
            vertical-align: middle; max-width: 150px; max-height: 36px;
            padding: 1px; border: 1px solid #ccc; }
        p { line-height: 40px; }
        .table td { vertical-align: middle; }

        /* ocr */
        .annotated_image { position: relative; }
        .ocr_region { position: absolute; outline: 2px solid #8080ff; }
    </style>
    </head>
    <body>
    <div class="container">
    <a href="../{{ "%05d" % (frame_number - 1) }}/index.html"
       class="nav pull-left">«prev</a>
    <a href="../{{ "%05d" % (frame_number + 1) }}/index.html"
       class="nav pull-right">next»</a>

    """)

_INDEX_HTML_FOOTER = dedent(u"""\

    </div>
    </body>
    </html>
""")


def test_that_debug_can_write_unicode_strings():
    def test(level):
        with scoped_debug_level(level):
            warn(u'Prüfungs Debug-Unicode')
            debug(u'Prüfungs Debug-Unicode')
            ddebug(u'Prüfungs Debug-Unicode')
    for level in [0, 1, 2]:
        yield (test, level)


def draw_on(frame, *args, **kwargs):
    draw_sink_ref = getattr(frame, '_draw_sink', None)
    if not draw_sink_ref:
        return
    draw_sink = draw_sink_ref()
    if not draw_sink:
        return
    draw_sink.draw(*args, **kwargs)
