"""Detect source-bearing raster area without double-counting overlapping tiles."""
from __future__ import annotations

from itertools import pairwise
from typing import Any

import pymupdf


def raster_union_coverage(page: Any) -> float:
    """Fraction of the page covered by the union of clipped image rectangles."""
    rectangles = [rect & page.rect for image in page.get_images()
                  for rect in page.get_image_rects(image[0])]
    rectangles = [rect for rect in rectangles if not rect.is_empty]
    boundaries = sorted({x for rect in rectangles for x in (rect.x0, rect.x1)})
    area = 0.0
    for left, right in pairwise(boundaries):
        intervals = sorted((rect.y0, rect.y1) for rect in rectangles
                           if rect.x0 < right and rect.x1 > left)
        end = float('-inf')
        height = 0.0
        for bottom, top in intervals:
            height += max(0.0, top - max(bottom, end))
            end = max(end, top)
        area += (right - left) * height
    return area / max(1.0, page.rect.get_area())


def raster_source_gap(page: Any, native: str, *, minimum_text: int) -> bool:
    """A majority scan with only labels/header/footer is not canonical text."""
    if raster_union_coverage(page) < .5:
        return False
    body = pymupdf.Rect(page.rect.x0, page.rect.y0 + page.rect.height * .08,
                        page.rect.x1, page.rect.y1 - page.rect.height * .08)
    body_chars = sum(len(block[4].strip()) for block in page.get_text('blocks')
                     if len(block) >= 7 and block[6] == 0
                     and (pymupdf.Rect(block[:4]) & body).get_area() > 0)
    return len(native.strip()) < minimum_text or body_chars < minimum_text
