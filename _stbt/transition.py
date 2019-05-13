# coding: utf-8

"""Detection & frame-accurate measurement of animations and transitions.

For example a selection that moves from one menu item to another or loading a
new screen such as a Guide and waiting for it to populate fully.

Because we want these measurements to be frame-accurate, we don't do expensive
image processing, relying instead on diffs between successive frames.

Copyright 2017-2018 Stb-tester.com Ltd.
License: LGPL v2.1 or (at your option) any later version (see
https://github.com/stb-tester/stb-tester/blob/master/LICENSE for details).
"""
from __future__ import unicode_literals
from __future__ import print_function
from __future__ import division
from __future__ import absolute_import
from future import standard_library
standard_library.install_aliases()
from builtins import next
from builtins import *  # pylint:disable=redefined-builtin,unused-wildcard-import,wildcard-import,wrong-import-order
from builtins import object  # pylint:disable=wrong-import-order

import enum

import cv2
import numpy

from .core import load_image
from .logging import debug
from .types import Region


def press_and_wait(
        key, region=Region.ALL, mask=None, timeout_secs=10, stable_secs=1,
        _dut=None):

    """Press a key, then wait for the screen to change, then wait for it to stop
    changing.

    This can be used to wait for a menu selection to finish moving before
    attempting to OCR at the selection's new position; or to measure the
    duration of animations; or to measure how long it takes for a screen (such
    as an EPG) to finish populating.

    :param str key: The name of the key to press (passed to `stbt.press`).

    :param stbt.Region region: Only look at the specified region of the video
        frame.

    :param str mask: The filename of a black & white image that specifies which
        part of the video frame to look at. White pixels select the area to
        analyse; black pixels select the area to ignore. You can't specify
        ``region`` and ``mask`` at the same time.

    :param timeout_secs: A timeout in seconds. This function will return a
        falsey value if the transition didn't complete within this number of
        seconds from the key-press.
    :type timeout_secs: int or float

    :param stable_secs: A duration in seconds. The screen must stay unchanged
        (within the specified region or mask) for this long, for the transition
        to be considered "complete".

    :returns:
        An object that will evaluate to true if the transition completed, false
        otherwise. It has the following attributes:

        * **frame** (`stbt.Frame`) – If successful, the first video frame when
          the transition completed; if timed out, the last frame seen.
        * **status** (`TransitionStatus`) – Either ``START_TIMEOUT``,
          ``STABLE_TIMEOUT``, or ``COMPLETE``. If it's ``COMPLETE``, the whole
          object will evaluate as true.
        * **press_time** (*float*) – When the key-press completed.
        * **animation_start_time** (*float*) – When animation started after the
          key-press (or ``None`` if timed out).
        * **end_time** (*float*) – When animation completed (or ``None`` if
          timed out).
        * **duration** (*float*) – Time from ``press_time`` to ``end_time`` (or
          ``None`` if timed out).
        * **animation_duration** (*float*) – Time from ``animation_start_time``
          to ``end_time`` (or ``None`` if timed out).

        All times are measured in seconds since 1970-01-01T00:00Z; the
        timestamps can be compared with system time (the output of
        ``time.time()``).
    """
    if _dut is None:
        import stbt
        _dut = stbt

    t = _Transition(region, mask, timeout_secs, stable_secs, _dut)
    press_result = _dut.press(key)
    debug("transition: %.3f: Pressed %s" % (press_result.end_time, key))
    result = t.wait(press_result)
    debug("press_and_wait(%r) -> %s" % (key, result))
    return result


def wait_for_transition_to_end(
        initial_frame=None, region=Region.ALL, mask=None, timeout_secs=10,
        stable_secs=1, _dut=None):

    """Wait for the screen to stop changing.

    In most cases you should use `press_and_wait` to measure a complete
    transition, but if you need to measure several points during a single
    transition you can use `wait_for_transition_to_end` as the last
    measurement. For example::

        stbt.press("KEY_OK")  # Launch my app
        press_time = time.time()
        m = stbt.wait_for_match("my-app-home-screen.png")
        time_to_first_frame = m.time - press_time
        end = wait_for_transition_to_end(m.frame)
        time_to_fully_populated = end.end_time - press_time

    :param stbt.Frame initial_frame: The frame of video when the transition
        started. If `None`, we'll pull a new frame from the device under test.

    :param region: See `press_and_wait`.
    :param mask: See `press_and_wait`.
    :param timeout_secs: See `press_and_wait`.
    :param stable_secs: See `press_and_wait`.

    :returns: See `press_and_wait`.
    """
    if _dut is None:
        import stbt
        _dut = stbt

    t = _Transition(region, mask, timeout_secs, stable_secs, _dut)
    result = t.wait_for_transition_to_end(initial_frame)
    debug("wait_for_transition_to_end() -> %s" % (result,))
    return result


class _Transition(object):
    def __init__(self, region=Region.ALL, mask=None, timeout_secs=10,
                 stable_secs=1, dut=None):
        if dut is None:
            import stbt
            dut = stbt

        if region is not Region.ALL and mask is not None:
            raise ValueError(
                "You can't specify region and mask at the same time")

        self.region = region
        self.mask_image = None
        if isinstance(mask, numpy.ndarray):
            self.mask_image = mask
        elif mask:
            self.mask_image = load_image(mask)

        self.timeout_secs = timeout_secs
        self.stable_secs = stable_secs
        self.dut = dut

        self.frames = self.dut.frames()
        self.diff = strict_diff
        self.expiry_time = None

    def wait(self, press_result):
        self.expiry_time = press_result.end_time + self.timeout_secs

        # Wait for animation to start
        for f in self.frames:
            if f.time < press_result.end_time:
                # Discard frame to work around latency in video-capture pipeline
                continue
            if self.diff(press_result.frame_before, f, self.region,
                         self.mask_image):
                _debug("Animation started", f)
                animation_start_time = f.time
                break
            _debug("No change", f)
            if f.time >= self.expiry_time:
                _debug(
                    "Transition didn't start within %s seconds of pressing %s",
                    f, self.timeout_secs, press_result.key)
                return _TransitionResult(
                    f, TransitionStatus.START_TIMEOUT,
                    press_result.end_time, None, None)

        end_result = self.wait_for_transition_to_end(f)  # pylint:disable=undefined-loop-variable
        return _TransitionResult(
            end_result.frame, end_result.status,
            press_result.end_time, animation_start_time, end_result.end_time)

    def wait_for_transition_to_end(self, initial_frame):
        if initial_frame is None:
            initial_frame = next(self.frames)
        if self.expiry_time is None:
            self.expiry_time = initial_frame.time + self.timeout_secs

        f = first_stable_frame = initial_frame
        while True:
            prev = f
            f = next(self.frames)
            if self.diff(prev, f, self.region, self.mask_image):
                _debug("Animation in progress", f)
                first_stable_frame = f
            else:
                _debug("No change since previous frame", f)
            if f.time - first_stable_frame.time >= self.stable_secs:
                _debug("Transition complete (stable for %ss since %.3f).",
                       first_stable_frame, self.stable_secs,
                       first_stable_frame.time)
                return _TransitionResult(
                    first_stable_frame, TransitionStatus.COMPLETE,
                    None, initial_frame.time, first_stable_frame.time)
            if f.time >= self.expiry_time:
                _debug("Transition didn't end within %s seconds",
                       f, self.timeout_secs)
                return _TransitionResult(
                    f, TransitionStatus.STABLE_TIMEOUT,
                    None, initial_frame.time, None)


def _debug(s, f, *args):
    debug(("transition: %.3f: " + s) % ((f.time,) + args))


def strict_diff(f1, f2, region, mask_image):
    if region is not None:
        full_frame = Region(0, 0, f1.shape[1], f1.shape[0])
        region = Region.intersect(full_frame, region)
        f1 = f1[region.y:region.bottom, region.x:region.right]
        f2 = f2[region.y:region.bottom, region.x:region.right]

    absdiff = cv2.absdiff(f1, f2)
    if mask_image is not None:
        absdiff = cv2.bitwise_and(absdiff, mask_image, absdiff)

    return numpy.count_nonzero(absdiff) > 50 or (absdiff > 20).any()


class _TransitionResult(object):
    def __init__(
            self, frame, status, press_time, animation_start_time, end_time):
        self.frame = frame
        self.status = status
        self.press_time = press_time
        self.animation_start_time = animation_start_time
        self.end_time = end_time

    def __repr__(self):
        return (
            "_TransitionResult(frame=<Frame>, status=%s, press_time=%s, "
            "animation_start_time=%s, end_time=%s)" % (
                self.status,
                self.press_time,
                self.animation_start_time,
                self.end_time))

    def __str__(self):
        # Also lists the properties -- it's useful to see them in the logs.
        return (
            "_TransitionResult(frame=<Frame>, status=%s, press_time=%s, "
            "animation_start_time=%s, end_time=%s, duration=%s, "
            "animation_duration=%s)" % (
                self.status,
                self.press_time,
                self.animation_start_time,
                self.end_time,
                self.duration,
                self.animation_duration))

    def __bool__(self):
        return self.status == TransitionStatus.COMPLETE

    @property
    def duration(self):
        if self.end_time is None or self.press_time is None:
            return None
        return self.end_time - self.press_time

    @property
    def animation_duration(self):
        if self.end_time is None or self.animation_start_time is None:
            return None
        return self.end_time - self.animation_start_time


class TransitionStatus(enum.Enum):
    START_TIMEOUT = 0
    STABLE_TIMEOUT = 1
    COMPLETE = 2
