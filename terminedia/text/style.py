"""Tokenizer and tree-structure for style-applying in text.

Allows one to encode in a single string style changes
instead of having to chunk pieces of text
to change the context for color, effects and transform changes


Also, [future] enable the parsing of more than one
markup style - for example, allowing terminedia
to extract color and movement information from
ANSI text streams generated by other apps.


TMMarkup example:


here comes some text [color:blue] with apples [background:red] infinite [/color /background effect:blink]in joy and blink[/effect]
[direction:up]happy[effect:bold]new year[/effect][direction:left]there we go[/direction]up again[direction: right] the end.

Markup description:
    any text outside of a [...] block is treated as plain text
    double use of square brackets - [[...]] escape on single bracket pair(wip)
    the tag name inside squares can be any of:
        - "color": sets the text foreground color. The color can be spelled as
            a CSS color name ('red', 'yellow', etc...) or using a numeric notation
            with a numeric triplet inside parenthesis (this will be parsed as if
            it were a Pythn tuple. Besides that the special color names "transparent" and
            "default" can also be used. The first ignores color and uses the correspondent
            color already set in the underlying character cell.
        - "foreground": the same as "color"
        - "background": Sets the text foreground color
        - "effect": any effect name from those listed in the "terminedia.effects" enum.
            more than one effect can be activated in the same markup - separate the
            effect names with a "|". Example: "[effect: blink|underline]".
            Some of th provided effects rely on terminal capabilities, such as underline,
            while others depend on an actual character replacing for unicode characters
            providing the visual effect named. The later are not meant to be cumullative,
            as characters can only be replaced once; ex.: "[effect: encircled]". Besides
            all existing effets, the special value "transparent" is also affected, and should
            preserve the effects active in the cell the character will be rendered to.
        - "effects": an alias for "effect"
        - "font" - the font to be used to render the text. Only works for multi-block sized text,
                and for the embedded UNSCII fonts: "fantasy","mcr" and "thin". Ex:
                "[font; thin]", "[font]"
        - "char": replaces all characters inside this tag with the givern one.
        - "direction": One of the 4 directions for text flow: up, down, right and left
            instead of "[direction: left]" , the direction names can be used as tag names.,
            so these are valid: "[left]abcd[up]efgh[right]ijklm"
        - "transformers": one of the Transformer instances listed in "terminedia.transformers.library" (wip)
        - tag name starting with an "/": pops the last corresponding tag and drops its modifications to
        the text flow. ex. "[/color]" (wip)
        - Two comma separated numbers: "teleports" the text the text for that coordinate in
               - the target rendering area. ex. "Hello[5, 3]World", prints 'world" at the 5,3 coordinates.
               - Using the "+" and "-" characers as a numeric prefix will use those numbrs as relative positions
               ex.: "[0, +1]" will move the beginning of text the next line.

        If tags are not closed, styles are not "popped", but this is no problem (no memory laks or such)
        the closing styles feature  is just a matter of convenience to return to previous values
        of the same attribute. Also,
        unlike XML, there is no problem crossing tags; This is valid input:
        "[color: blue] hello [background: #ddd] world [/color] for you [/background]!!"



"""
from collections.abc import Sequence, MutableMapping
from copy import copy
import re
import typing as T
import threading

from terminedia.contexts import Context
from terminedia.utils import V2, Rect, get_current_tick
from terminedia.values import WIDTH_INDEX, HEIGHT_INDEX, RelativeMarkIndex, Directions


RETAIN_POS = object()


class StyledSequence:
    def __init__(
        self, text, mark_sequence, text_plane=None, context=None, starting_point=None
    ):
        """
        Args:
          text (Sequence): the stream of characters to be rendered  - it can be a string or a list of 1-grapheme strings.
          mark_sequence (Mapping): A mappign with Mark objects. The keys either represent index positions on the text
            where the mark will be processed, or they can be at the special index "config" denoting marks
            that are to have their indexes processed according to other enviroment circunstances
            (like the current "tick" - and possibly 'current position')
            The value at each item can contain a single Mark or a of Markers.
          text_plane (terminedia.text.planes.TextPlane): area where the output is to be rendered
            on iterating. The Text object will be searched for aditional "Mark" objects that
            will compose the syle and position when encountered (they are less
            prioritary than the Marks passed in mark_sequence)
            If no Text object is given, the instance may still be iterated to retrieve
            a sequence of char, context and position - for example, when generating
            output directly to a tty.
          context (terminedia.Context): parent context. By default the context
          attached to the given text_plane is used
          starting_point: first position to be yielded when iteration starts (from which
            rules apply according to context.direction and others given by the matched
            "Mark" objects. Defaults to (0, 0)



        Helper class to render text that will both hold embedded style information,
        conveyed in "Mark" objects (with information like "at position 10, push foreground color 'red'"),
        and respect Mark objects embedded in the "text_plane" associanted rendering space.

        Style changes are all on top of a given "parent context"
        if any (otherwise, the text_plane context is used, or None)

        The rendering part include yielding the proper position of each
        rendering character,as contexts convey also
        text printing direction and marks can not only
        push a new printing direction, but also "teleport" the
        rendering point for the next character altogether.

        """
        self.text = text
        self.mark_sequence = mark_sequence
        self.parent_context = context
        self._last_index_processed = None
        self.context = Context()
        self.text_plane = text_plane
        self.starting_point = V2(starting_point) if starting_point else V2(0, 0)
        self.current_position = self.starting_point
        self._sanity_counter = 0
        self.locals = threading.local()

    def _process_to(self, index):

        if self._last_index_processed is None and index == 0:
            self.current_position = self.starting_point
        elif (
            self._last_index_processed is None
            or index != self._last_index_processed + 1
        ):
            return self._reprocess_from_start(index)

        for mark_here, mark_origin in self.marks.get_full(index, self.current_position):
            mark_here.context = self.context
            mark_here.pos = self.current_position
            if mark_here.attributes or mark_here.pop_attributes:
                self._context_push(mark_here.attributes, mark_here.pop_attributes, mark_origin, index)
            if mark_here.moveto:
                mtx = mark_here.moveto[0]
                mty = mark_here.moveto[1]
                mtx = mtx if mtx is not RETAIN_POS else self.current_position.x
                mty = mty if mty is not RETAIN_POS else self.current_position.y
                self.current_position = V2(mtx, mty)
            if mark_here.rmoveto:
                self.current_position += V2(mark_here.rmoveto)
        self._last_index_processed = index
        return self.context

    def _reprocess_from_start(self, index):
        self._sanity_counter += 1
        if self._sanity_counter > 1:
            raise RuntimeError(
                "Something resetting marked text internal state in infinite loop"
            )
        self._reset_context()
        self._last_index_processed = None
        for i in range(0, index + 1):
            self._process_to(i)

        self._sanity_counter -= 1
        return self.context


    def _enter_iteration(self):
        cm = self.locals.context_map = {}
        for key, value in self.context:
            cm[key] = [(value, "original")]
        marks = self.text_plane.marks if self.text_plane else MarkMap()
        self.marks = marks.prepare(
            self.mark_sequence,
            self.text_plane.ticks if self.text_plane else get_current_tick(),
            self.text,
            self.context,
        )
        self._active_transformers = []


    def _context_push(self, attributes, pop_attributes, mark_origin, index):
        seq_attrs = {"transformer": "transformers", "pretransformer": "pretransformers"}
        cm = self.locals.context_map
        changed = set()
        attributes = attributes or {}
        pop_attributes = pop_attributes or {}
        for key in pop_attributes:
            key = seq_attrs.get(key, key)
            stack = cm.setdefault(key, [])
            if not stack:
                continue
            changed.add(key)
            for i, (snapshot_attribute, snapshot_origin) in enumerate(reversed(stack)):
                if snapshot_origin == mark_origin:
                    stack.pop(-(i + 1))
                    break

        for key, value in attributes.items():
            if key in seq_attrs:
                key = seq_attrs[key]
                new_value = copy(getattr(self.context, key))
                spam = len(self.text) - index
                if isinstance(value, str):
                    if " " in value:
                        value, spam = value.split()
                        spam = int(spam)
                    value = self.text_plane.transformers_map.get(value)
                else:
                    spam = getattr(value, "sequence_len", spam)
                value = copy(value)
                # Inject values to be available for transformer methods:
                value.sequence_len = spam
                value.sequence = self.text[index: index + spam]
                value.sequence_absolute_start = index
                self._active_transformers.append(value)
                new_value.append(value)
                value = new_value
            stack = cm.setdefault(key, [])
            stack.append((value, mark_origin))

            changed.add(key)

        for attr in changed:
            if cm[attr]:
                setattr(self.context, attr, cm[attr][-1][0])

    def _remove_transformers(self, tr):
        # Remove active transformers from the 3 places they are present:
        # self._active_transformers, self.context and self.locals.context_map

        # (TransformersContainer class feature a "safe_remove")
        self._active_transformers.remove(tr)
        for key in ("transformers", "pretransformers"):
            getattr(self.context, key).remove(tr)
            for container, origin in self.locals.context_map.get(key):
                container.remove(tr)

    def _get_position_at(self, char, index):
        if self._last_index_processed != index:
            self._process_to(index)
        position = self.current_position
        self.current_position += self.context.direction
        return position

    def __iter__(self):
        self._enter_iteration()
        with self.context():
            for index, char in enumerate(self.text):
                values = char, self._process_to(index), self._get_position_at(
                    char, index
                )
                if self._active_transformers:
                    # transformers have to be updated after made active...
                    to_remove = set()
                    for tr in self._active_transformers:
                        tr.sequence_index = index - tr.sequence_absolute_start
                        if tr.sequence_index >= tr.sequence_len:
                            to_remove.add(tr)
                    for tr in to_remove:
                        self._remove_transformers(tr)

                yield values
        if hasattr(self, "marks"):
            del self.marks
        # self._unwind()

    def _reset_context(self):
        for key, value in self._parent_context_data.items():
            if key in ("transformers", "pretransformers"):
                value = copy(value)
            setattr(self.context, key, value)

    def _prepare_context(self):
        self.context = Context()
        source = self.text_plane.owner.context
        self._parent_context_data = {key:value for key, value in source}
        self._reset_context()

    def render(self):
        if not self.text_plane:
            return
        # FIXME: if self.parent_context is not self.text_plane.owner.context, combine parent and current context
        # otherwise combination is already in place at the render_lock
        self._prepare_context()
        render_lock = self.text_plane._render_styled_lock(self.context)
        try:
            char_fn = next(render_lock)

            for char, context, position in self:
                char_fn(char, position)
                # handle double-width characters
                if getattr(self.context, "text_lastchar_was_double", False):
                    if self.context.direction == Directions.RIGHT:
                        self.current_position += self.context.direction
        finally:
            next(render_lock, None)

### Helper functions used exclusively by MarkMap
# (Up to class MarkMap iself)


def _force_iter(item):
    if isinstance(item, Sequence):
        yield from item
    else:
        yield item


def _merge_as_lists(*args):
    result = []
    for item in args:
        if item is None: continue
        if not isinstance(item, list):
            result.append(item)
        else:
            result.extend(item)
    if len(result) == 1:
        return result[0]
    return result


def index_is_relative(index):
     return index[0] is None or index[1] is None or isinstance(index[0], RelativeMarkIndex) or index[0] < 0 or isinstance(index[1], RelativeMarkIndex) or index[1] < 0

def _normalize_component(comp, name):
    if isinstance(comp, RelativeMarkIndex):
        return True, comp
    elif comp is None or comp < 0:
        return True, (RelativeMarkIndex(name) + comp)
    return False, comp

def normalize_relative_index(index):
    r1, x = _normalize_component(index[0], "WIDTH")
    r2, y = _normalize_component(index[1], "HEIGHT")
    return (r1 | r2), V2(x, y)

def get_relative_variants(pos, size=None):
    """Given a position and  a size, yields all possible ways
    of 'spelling' the given position expresing  Vector with relative-to-the-end indexes
    """
    pos = list(pos)
    if pos[0] is None:
        pos[0] = WIDTH_INDEX
    if isinstance(pos[0], RelativeMarkIndex):
        pos[0] = pos[0].evaluate(size)
    elif pos[0] < 0:
        pos[0] = size[0] + pos[0]
    if pos[1] is None:
        pos[1] = WIDTH_INDEX
    if isinstance(pos[1], RelativeMarkIndex):
        pos[1] = pos[1].evaluate(size)
    elif pos[1] < 0:
        pos[1] = size[1] + pos[1]
    pos = tuple(pos)

    px = WIDTH_INDEX - (size[0] - pos[0])
    py = HEIGHT_INDEX - (size[1] - pos[1])

    yield pos
    yield px, pos[1]
    yield pos[0], py
    yield px, py


class MarkMap(MutableMapping):
    """Mapping attached to each text plane -

    TL;DR: this is a mapping used to control
    rich text rendering and flow. An instance is attached
    to each text_plane and can be reached at shape.text[size].marks
    This instance can be directly used by Text object users
    to place marks that will change the behavior of printed
    text at that point and beyond.


    It contains Mark objects that
    are "virtually" hidden in the plane and can change the attributes or
    position of a text sequence in the point one character will (or would)
    be printed were they are located. The attribute change takes effect
    for the rich-text stream been rendered from that point on.

    In plain code, that means doing:
    ```
    myshape.text[1].marks[3,0] = TM.Mark(attributes{"color": "red"})
    myshape.text[1] = "123456"
    ```
    will render '123' in the current context color, and '456' in red.

    The positional Marks can also be "virtual" in a sense one can set
    a rectangle of special marks in a single call: this is used
    to setup the "teleporter" marks at text-plane boundaries
    that enable text to continue on the next line, when printing
    left-to-right.

    A third Mark category can be added, consisting of Marks which index
    will change overtime: the "special" index can receive "SpecialMark" instances:
    those are Mark objects that have an "index" method - this method
    receives two parameters  a "tick" number and the length of the sequence being rendered,
    and returns a 1D index - which is used to place the mark
    inside the sequence of text being rendered, or a 2D index, that is
    used as a location on the grid.

    The instances of MarkMap are consumed by StyledSequence objects when rendering,
    and those will set a 1D positional-mark mapping (this creates a shallow copy
    of a MarkMap instance). The StyledSequence then consumes marks when iterating
    itself for rendering, retrieving both marks in the text stream (1D positional
    marking), Marks fixed on the text plane, and special marks with time-variant
    position. When retrieving the Marks at a given position, the location on the
    2D plane, and tick number are available to be consumed by callables on
    special Mark objects

    A caracteristic of the contents of MarkMap cells is that
    a cell may conten eiter a Mark object, or a list of MarkObjects
     - Lists can be created freely and marks can be created
     freely in instances of MarkMap. Marks that are placed
     in absolute cell addresses are merged with ones stored
     in relative cell address upon reading (addressing from the left or from the
     bottom of a text plane). The mechanism for that is too complicated
     to be something to be proud off - but seems to work when a text-area changes
     size in a nice way.


    """
    def __init__(self, parent=None):
        self.data = {}
        self.relative_data = {}
        self.tick = 0
        self.seq_data = {}
        self.special = set()
        self._concrete_special = {}
        self.text_plane = parent
        self.is_rendering_copy = False

    def prepare(self, seq_data, tick=0, parsed_text="", context=None):
        instance = copy(self)
        instance.tick = tick
        instance.seq_data = seq_data
        instance.context = context
        instance.parsed_text = parsed_text
        instance.special = self.special.copy()
        instance.data = self.data.copy()
        if "special" in seq_data:
            instance.special.update(seq_data["special"])
        instance.concretize_special_marks()
        instance.concretize_relative_marks()
        instance.is_rendering_copy = True

        #  self.relative_data are the same object on purpose  -
        return instance

    def concretize_special_marks(self):
        self._concrete_special = {}
        for mark in self.special:
            # TODO: inject parameters to compute index according to its signature
            # currently hardcoded to 2 parameters: tick and length of target text
            index = mark.index(self.tick, len(self.parsed_text))
            self._concrete_special.setdefault(index, []).append(mark)

    def concretize_relative_marks(self):
        # Compute numeric index of marks stored relative to width and height of the text_plane
        if not self.text_plane:
            return
        size = self.text_plane.size
        for index, mark in self.relative_data.items():
            concrete_index, *_ = get_relative_variants(index, size)
            new_mark = self.data.get(concrete_index, [])
            new_mark = _merge_as_lists(new_mark, mark)
            self.data[concrete_index] = new_mark


    def get_full(self, sequence_index, pos):

        self.sequence_index = sequence_index
        self.pos = pos

        mark_seq = [(item, "plane") for item in self._concrete_special.get(pos, [])]
        mark_seq += [(item, "sequence") for item in self._concrete_special.get(sequence_index, [])]
        mark_seq += [(item, "plane") for item in _force_iter(self.get(pos, []))]
        mark_seq += [(item, "sequence") for item in _force_iter(self.seq_data.get(sequence_index, []))]

        return mark_seq

    def __setitem__(self, index, value):
        if index == "special":
            self.special.add(value)
            return
        if isinstance(index, Rect):
            for pos in index.iter_cells():
                self[pos] = value
            return
        is_relative, index = normalize_relative_index(index)
        if is_relative:
            self.relative_data[index] = value
        else:
            self.data[index] = value

    def __getitem__(self, index):
        # TODO retrieve MagicMarks and virtual marks
        # is_relative, index, absolute_index, relative_index = self._convert_to_relative(index)
        is_relative = index_is_relative(index)
        if self.is_rendering_copy and is_relative:
            index, *_ = get_relative_variants(index, self.text_plane.size)

        if self.is_rendering_copy or is_relative and not self.text_plane:
            return self.data[index]
        all_marks = []
        for i, r_index in enumerate(get_relative_variants(index, self.text_plane.size)):
            if i == 0:
                # first index is normalizes with positive integer coordinates
                all_marks.append(self.data.get(r_index))
            all_marks.append(self.relative_data.get(r_index))
        result = _merge_as_lists(*all_marks)

        if not result:
            raise KeyError(index)
        return result


    def __delitem__(self, index):
        found = False
        for i, r_index in enumerate(get_relative_variants(index, self.text_plane.size)):
            if i == 0:
                found |= bool(self.data.pop(r_index, False))
            found |= bool(self.relative_data.pop(r_index, False))
        if not found:
            raise KeyError(index)

    def __len__(self):
        return len(self.data) + len(self.relative_data)

    def __iter__(self):
        if self.text_plane:
            if not self.relative_data:
                return iter(self.data)
            def gen():
                yield from iter(self.data)
                yield from iter(self.relative_data)
            return gen()
        else:
            from itertools import chain
            return chain(self.data, self.relative_data)
    def clear(self):
        self.__init__(parent=self.text_plane)

    def __repr__(self):
        return "MarkMap < >"


class Mark:
    """Control object to be added to a text_plane or StyledStream

    The object indicate which context attributes or text position
    enter in effect at that point in the stream.

    Instances of this are to be automatically created on parsing markup strings or
    or other input - but can be hand-crafted for special effects.


    """

    # This is supposed to evolve to be programable
    # and depend on injected parameters like position, ticks -
    # like transformers.Transformer

    # For the time being, subclass and use 'property'.
    # 'context' and 'pos' attributes are set on the instance
    # prior to reading the other property values.

    __slots__ = "attributes pop_attributes moveto rmoveto context pos".split()
    attributes: T.Mapping
    pop_attributes: T.Mapping
    moveto: V2
    rmoveto: V2

    def __init__(self, attributes=None, pop_attributes=None, moveto=None, rmoveto=None):
        self.attributes = attributes
        self.pop_attributes = pop_attributes
        self.moveto = moveto
        self.rmoveto = rmoveto

    @classmethod
    def merge(cls, m1, m2):
        if not isinstance(m1, list):
            m1 = [m1]
        m1.append(m2)
        return m1

        # The following code is nice, and might still be used to
        # consolidate moveto + rmoveto -
        # However, it would not preserve the order of popping attibutes
        # so, we'd better allow Sequences with a single Key in the "mark_sequence" dictionary.
        #attributes = m1.attributes or {}
        #attributes.update(m2.attributes or {})
        #pop_attributes = m1.pop_attributes or {}
        #pop_attributes.update(m2.pop_attributes or {})
        #moveto = m2.moveto or m1.moveto
        #if m1.rmoveto and m2.rmoveto:
            #rmoveto = m1.rmoveto + m2.rmoveto
        #else:
            #rmoveto = m1.rmoveto or m2.rmoveto
        #return cls(
            #attributes=attributes,
            #pop_attributes=pop_attributes,
            #moveto=moveto,
            #rmoveto=rmoveto,
        #)

    def __repr__(self):
        return f"{self.__class__.__name__}({('attributes=%r, ' % self.attributes) if self.attributes else ''}{('pop_attributes=%r, ' % self.pop_attributes) if self.pop_attributes else ''}{('moveto={!r}, '.format(self.moveto)) if self.moveto else ''}{('rmoveto={!r}'.format(self.rmoveto)) if self.rmoveto else ''})"


EmptyMark = Mark()

class SpecialMark(Mark):
    __slots__=["index"]
    def __init__(self, index, *args, **kwargs):
        self.index = index
        super().__init__(*args, **kwargs)



class Tokenizer:
    # TODO: when a second tokenizer is created, code that can be refactored currently in
    # MLTokenizer will be moved here.
    pass


class MLTokenizer(Tokenizer):
    _parser = re.compile(r"(?<!\[)\[[^\[].*?\]")

    def __init__(self, initial=""):
        """Parses a string with special Markup and prepare for rendering

        After instantiating, keep calling '.update' to add more text,
        at any point call ".render()" to create a StyledSequence instance
        and render it to a text plane.
        """
        self.raw_text = ""
        self.update(initial)

    def update(self, text):
        # Imitates Python's hashlib interface
        self.raw_text += text

    def parse(self):
        """Parses the raw_text in  the instance, and sets
        setting a stripped "parsed_text" attribute along a ".mark_sequence" attribute
        containing the described marks embedded in the text as Mark instances.
        """
        raw_tokens = []
        offset = 0

        def annotate_and_strip_tokens(match):
            nonlocal offset
            token = match.group()
            raw_tokens.append((match.start() - offset, token.strip("[]")))
            offset += match.end() - match.start()
            return ""

        self.parsed_text = self._parser.sub(annotate_and_strip_tokens, self.raw_text)
        self._tokens_to_marks(raw_tokens)

    def _tokens_to_marks(self, raw_tokens):
        from terminedia.transformers import library as transformers_library
        from terminedia import Effects, Color, Directions, DEFAULT_BG, DEFAULT_FG, TRANSPARENT

        self.mark_sequence = {}
        # Separate stack to anottate the length of the affected string inside each Transformer
        transformer_stack = []
        last_offset = -1
        offset_repeat_counter = -1
        for offset, token in raw_tokens:
            if offset == last_offset:
                offset_repeat_counter += 1
            else:
                offset_repeat_counter = 0
            last_offset = offset
            attributes = None
            pop_attributes = None
            rmoveto = None
            moveto = None

            if ":" in token:
                action, value = [v.strip() for v in token.split(":")]
                action = action.lower()
                if action == "effect":
                    action = "effects"
                if action not in ("transformer", "font", "char"):
                    value = value.lower()
            else:
                action = token.strip()
                value = None
                if action in {"left", "right", "up", "down"}:
                    value = action
                    action = "direction"
            if action.startswith("/"):
                starting_tag= False
                action = action[1:]
            else:
                starting_tag = True

            # Allow for special color values:
            if action in ("color", "foreground") and value == "default":
                value = DEFAULT_FG
            if action == "background" and value == "default":
                value = DEFAULT_BG
            if value == "transparent" and action in {"effects", "color", "foreground", "background"}:
                value = TRANSPARENT
            if value and value.startswith("(") and action in {"color", "foreground", "background"}:
                value = ast.literal_eval(value)
            if action == "transformer":
                action = "pretransformer"
                if starting_tag:
                    transformer_stack.append((value, offset, offset_repeat_counter))
                else:
                    closing_transformer, oppening_offset, oppening_repeat = transformer_stack.pop()
                    if not " " in closing_transformer:
                        # if there is a space, assume the spam of the transformer is given
                        # on the opening tag and do nothing.
                        spam = offset - oppening_offset
                        closing_mark = self.mark_sequence[oppening_offset]
                        if isinstance(closing_mark, list):
                            closing_mark = closing_mark[oppening_repeat]
                        closing_mark.attributes["pretransformer"] += f" {spam}"

            attribute_names = {"effects", "color", "foreground", "background", "direction", "pretransformer", "char", "font", }
            if action in attribute_names:
                if starting_tag:
                    attributes = {
                        action: (
                            Color(value) if action in ("color", "foreground", "background") else
                            sum(Effects.__members__.get(v.strip(), 0) for v in value.split("|"))
                                if action == "effects" else
                            getattr(Directions, value.upper()) if action == "direction" else
                            # getattr(transformers_library, value) if action == "pretransformer" else
                            value
                        )
                    }
                else:
                    pop_attributes = {action: None}

            if "," in action and attributes is None and pop_attributes is None:
                nx, ny = [v.strip() for v in action.split(",")]
                nnx, nny = int(nx), int(ny)
                if nx[0] in ("+", "-") and ny[0] in ("+", "-"):
                    rmoveto = nnx, nny
                elif nx[0] in ("+", "-") and ny[0] not in ("+", "-"):
                    moveto = RETAIN_POS, nny
                    rmoveto = nnx, 0
                elif nx[0] not in ("+", "-") and ny[0] in ("+", "-"):
                    moveto = nnx, RETAIN_POS
                    rmoveto = 0, nny
                else:
                    moveto = nnx, nny
            # Unknown token action - simply drop for now"
            mark = Mark(attributes, pop_attributes, moveto, rmoveto)
            if offset in self.mark_sequence:
                mark = Mark.merge(self.mark_sequence[offset], mark)
            self.mark_sequence[offset] = mark

    def __call__(self, text_plane=None, context=None, starting_point=(0, 0)):
        self.parse()
        self.styled_sequence = StyledSequence(
            self.parsed_text,
            self.mark_sequence,
            text_plane=text_plane,
            context=context,
            starting_point=starting_point,
        )
        return self.styled_sequence


class ANSITokenizer(Tokenizer):
    # TODO....
    pass
