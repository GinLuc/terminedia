from collections.abc import Sequence

from terminedia.transformers import TransformersContainer
from terminedia.utils import  HookList, Rect, V2, get_current_tick
from terminedia.values import EMPTY, TRANSPARENT


tags = dict()


class Sprite:
    def __init__(self, shapes=None, pos=(0,0), active=False, tick_cycle=1, anchor="topleft", alpha=True):
        from terminedia.image import Shape
        self.shapes = shapes if isinstance(shapes, Sequence) and not isinstance(shapes, Shape) else [shapes]
        self.pos = pos
        self.active = active
        self.tick_cycle = tick_cycle
        self.anchor = anchor
        self._check_and_promote()
        self.transformers = TransformersContainer()
        self.dirty_previous_rect = self.rect
        if alpha:
            for shape in self.shapes:
                shape.spaces_to_transparency()

    def _check_and_promote(self):
        """called at initialization to try to promote any object that is not a Shape
        to a Shape.

        """
        from terminedia.image import shape, Shape, FullShape
        for index, item in enumerate(self.shapes):
            if not isinstance(item, Shape):
                item = shape(item)
            if not isinstance(item, FullShape):
                item = FullShape.promote(item)
                self.shapes[index] = shape(item)

    @property
    def pos(self):
        return self._pos

    @pos.setter
    def pos(self, value):
        self._pos = V2(value)

    @property
    def shape(self):
        tick = get_current_tick()
        return self.shapes[(tick // self.tick_cycle) % len(self.shapes)]

    @property
    def rect(self):
        r = Rect(self.shape.size)
        if self.anchor == "topleft":
            r.left = self.pos.x
            r.top = self.pos.y
        elif self.anchor == "center":
            r.center = self.pos
        return r

    @property
    def dirty_rects(self):
        changed_rect = self.rect != self.dirty_previous_rect
        transformer_using_tick = any("tick" in transformer.signatures for transformer in self.transformers)
        if changed_rect or transformer_using_tick:
            dirty = {(self.rect - self.rect.c1).as_tuple}
        else:
            dirty = self.shape.dirty_rects

        self.dirty_previous_rect = self.rect
        return dirty

    def owner_coords(self, rect, where=None):
        if not isinstance(rect, Rect):
            rect = Rect(rect)
        if not where:
            where = self.rect
        return Rect(where.c1 + rect.c1, width=rect.width, height=rect.height)

    def get_at(self, pos=None, container_pos=None, pixel=None):
        # TODO: pixel to be used when there are combination modes/translucency
        if container_pos:
            if self.anchor == "topleft":
                pos = container_pos - self.pos
            else:
                pos = container_pos - self.rect.c1
        pixel = self.shape[pos]
        if self.transformers:
            pixel = self.transformers.process(self.shape, pos, pixel)
        return pixel


class SpriteContainer(HookList):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def insert_hook(self, item):
        if not isinstance(item, Sprite):
            item = Sprite(item)
        item.owner = self.owner
        return item

    def get_at(self, pos, pixel=None):
        pcls = type(pixel)
        for sprite in reversed(self.data):
            if not sprite.active:
                continue
            if pos in sprite.rect:
                new_pixel = sprite.get_at(container_pos=pos, pixel=pixel)
                if any(comp is TRANSPARENT for comp in new_pixel):
                    pixel = [c_orig if c_new is TRANSPARENT else c_new for c_orig, c_new in zip(pixel, new_pixel)]
                else:
                    pixel = new_pixel
        return pixel if isinstance(pixel, pcls) else pcls(*pixel)

    def add(self, item, pos=(0,0), active=True, tick_cycle=1, anchor="topleft", alpha=True):
        if not isinstance(item, Sprite):
            item = Sprite(item, pos, active, tick_cycle, anchor, alpha=alpha)
        self.append(item)
        return item
