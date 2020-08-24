#
# Copyright (c), 2016-2020, SISSA (International School for Advanced Studies).
# All rights reserved.
# This file is distributed under the terms of the MIT License.
# See the file 'LICENSE' in the root directory of the present
# distribution, or http://opensource.org/licenses/MIT.
#
# @author Davide Brunato <brunato@sissa.it>
#
"""
This module defines Unicode code points helper functions.
"""
from sys import maxunicode

CHARACTER_CLASS_ESCAPED = {ord(c) for c in r'-|.^?*+{}()[]\\'}
"""Code Points of escaped chars in a character class."""


def code_point_order(cp):
    """Ordering function for code points."""
    return cp if isinstance(cp, int) else cp[0]


def code_point_reverse_order(cp):
    """Reverse ordering function for code points."""
    return cp if isinstance(cp, int) else cp[1] - 1


def iter_code_points(code_points, reverse=False):
    """
    Iterates a code points sequence. Three ore more consecutive
    code points are merged in a range.

    :param code_points: an iterable with code points and code point ranges.
    :param reverse: if `True` reverses the order of the sequence.
    :return: yields code points or code point ranges.
    """
    start_cp = end_cp = None
    if reverse:
        code_points = sorted(code_points, key=code_point_reverse_order, reverse=True)
    else:
        code_points = sorted(code_points, key=code_point_order)

    for cp in code_points:
        if isinstance(cp, int):
            cp = cp, cp + 1

        if start_cp is None:
            start_cp, end_cp = cp
            continue
        elif reverse:
            if start_cp <= cp[1]:
                start_cp = min(start_cp, cp[0])
                continue
        elif end_cp >= cp[0]:
            end_cp = max(end_cp, cp[1])
            continue

        if end_cp > start_cp + 1:
            yield start_cp, end_cp
        else:
            yield start_cp
        start_cp, end_cp = cp
    else:
        if start_cp is not None:
            if end_cp > start_cp + 1:
                yield start_cp, end_cp
            else:
                yield start_cp


def check_code_point(cp):
    """
    Checks a code point or code point range.

    :return: a valid code point range.
    """
    if isinstance(cp, int):
        if not (0 <= cp <= maxunicode):
            raise ValueError("not a Unicode code point: %r" % cp)
        return cp, cp + 1
    else:
        if not (0 <= cp[0] < cp[1] <= maxunicode + 1) \
                or not isinstance(cp[0], int) or not isinstance(cp[1], int):
            raise ValueError("not a Unicode code point range: %r" % cp)
        return cp


def code_point_repr(cp):
    """
    Returns the string representation of a code point.

    :param cp: an integer or a tuple with at least two integers. \
    Values must be in interval [0, sys.maxunicode].
    """
    if isinstance(cp, int):
        if cp in CHARACTER_CLASS_ESCAPED:
            return r'\%s' % chr(cp)
        return chr(cp)

    if cp[0] in CHARACTER_CLASS_ESCAPED:
        start_char = r'\%s' % chr(cp[0])
    else:
        start_char = chr(cp[0])

    end_cp = cp[1] - 1  # Character ranges include the right bound
    if end_cp in CHARACTER_CLASS_ESCAPED:
        end_char = r'\%s' % chr(end_cp)
    else:
        end_char = chr(end_cp)

    if end_cp > cp[0] + 1:
        return '%s-%s' % (start_char, end_char)
    else:
        return start_char + end_char