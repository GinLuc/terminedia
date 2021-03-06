import inspect
import math
from functools import partial

from collections.abc import Mapping

from .collections import (
    mirror_dict,
    FrozenDict,
    HookList,
    TaggedDict,
    LazyDict,
)
from .descriptors import LazyBindProperty, ObservableProperty
from .vector import V2, NamedV2
from .rect import Rect
from .colors import css_colors, Color, SpecialColor
from .gradient import Gradient


root_context = None


def size_in_blocks(size, resolution=""):
    """Given a shape size using a specific resolution, returns the size in blocks needed to acommodate that"""
    size_factor = (
        (2, 4)
        if resolution == "braille"
        else (2, 2)
        if resolution == "high"
        else (1, 2)
        if resolution == "square"
        else (1, 1)
    )
    return V2(math.ceil(size.x / size_factor[0]), math.ceil(size.y / size_factor[1]))


def size_in_pixels(size, resolution=""):
    """Given a number of blocks return the available pixels in a specific resolution"""
    size_factor = (
        (2, 4)
        if resolution == "braille"
        else (2, 2)
        if resolution == "high"
        else (1, 2)
        if resolution == "square"
        else (1, 1)
    )
    return V2(math.ceil(size.x * size_factor[0]), math.ceil(size.y * size_factor[1]))


def get_current_tick():
    """use a counter global to Screen module, increased on
    calls to screen.update()
    """
    global root_context
    if not root_context:
        from terminedia import context as root_context
    return root_context.ticks if hasattr(root_context, "ticks") else 0


def tick_forward():
    global root_context
    if not root_context:
        from terminedia import context as root_context
    root_context.ticks = get_current_tick() + 1


def combine_signatures(func, wrapper=None, include=None):
    """Adds keyword-only parameters from wrapper to signature

    Args:
      - func: The 'user' func that is being decorated and replaced by 'wrapper'
      - wrapper: The 'traditional' decorator which keyword-only parametrs should be added to the
            wrapped-function ('func')'s signature
      - include: optional list of keyword parameters that even not being present
            on the wrappers signature, will be included on the final signature.
            (if passed, these named arguments will be part of the kwargs)

    Use this in place of `functools.wraps`
    It works by creating a dummy function with the attrs of func, but with
    extra, KEYWORD_ONLY parameters from 'wrapper'.
    To be used in decorators that add new keyword parameters as
    the "__wrapped__"

    Usage:

    def decorator(func):
        @combine_signatures(func)
        def wrapper(*args, new_parameter=None, **kwargs):
            ...
            return func(*args, **kwargs)
    """
    # TODO: move this into 'extradeco' independent package
    from functools import partial, wraps
    from inspect import signature, _empty as insp_empty, _ParameterKind as ParKind
    from itertools import groupby

    if wrapper is None:
        return partial(combine_signatures, func, include=include)

    sig_func = signature(func)
    sig_wrapper = signature(wrapper)
    pars_func = {
        group: list(params)
        for group, params in groupby(sig_func.parameters.values(), key=lambda p: p.kind)
    }
    pars_wrapper = {
        group: list(params)
        for group, params in groupby(
            sig_wrapper.parameters.values(), key=lambda p: p.kind
        )
    }

    def render_annotation(p):
        return f"{':' + (repr(p.annotation) if not isinstance(p.annotation, type) else repr(p.annotation.__name__)) if p.annotation != insp_empty else ''}"

    def render_params(p):
        return f"{'=' + repr(p.default) if p.default != insp_empty else ''}"

    def render_by_kind(groups, key):
        parameters = groups.get(key, [])
        return [f"{p.name}{render_annotation(p)}{render_params(p)}" for p in parameters]

    pos_only = render_by_kind(pars_func, ParKind.POSITIONAL_ONLY)
    pos_or_keyword = render_by_kind(pars_func, ParKind.POSITIONAL_OR_KEYWORD)
    var_positional = [p for p in pars_func.get(ParKind.VAR_POSITIONAL, [])]
    keyword_only = render_by_kind(pars_func, ParKind.KEYWORD_ONLY)
    var_keyword = [p for p in pars_func.get(ParKind.VAR_KEYWORD, [])]

    extra_parameters = render_by_kind(pars_wrapper, ParKind.KEYWORD_ONLY)
    if include:
        if isinstance(include[0], Mapping):
            include = [
                f"{param['name']}{':' + param['annotation'] if 'annotation' in param else ''}{'=' + param['default'] if 'default' in param else ''}"
                for param in include
            ]
        else:
            include = [f"{name}=None" for name in include]

    def opt(seq, value=None):
        return ([value] if value else [", ".join(seq)]) if seq else []

    annotations = func.__annotations__.copy()
    for parameter in pars_wrapper.get(ParKind.KEYWORD_ONLY):
        annotations[parameter.name] = parameter.annotation

    param_spec = ", ".join(
        [
            *opt(pos_only),
            *opt(pos_only, "/"),
            *opt(pos_or_keyword),
            *opt(
                keyword_only or extra_parameters,
                ("*" if not var_positional else f"*{var_positional[0].name}"),
            ),
            *opt(keyword_only),
            *opt(extra_parameters),
            *opt(include),
            *opt(var_keyword, f"**{var_keyword[0].name}" if var_keyword else ""),
        ]
    )
    declaration = f"def {func.__name__}({param_spec}): pass"

    f_globals = func.__globals__
    f_locals = {}

    exec(declaration, f_globals, f_locals)

    result = f_locals[func.__name__]
    result.__qualname__ = func.__qualname__
    result.__doc__ = func.__doc__
    result.__annotations__ = annotations

    return wraps(result)(wrapper)


def contextkwords(func=None, context_path=None, text_attrs=False):
    if func is None:
        return partial(contextkwords, context_path=context_path, text_attrs=text_attrs)
    sig = inspect.signature(func)

    @combine_signatures(func, include=["font", "direction"] if text_attrs else None)
    def wrapper(
        *args,
        char=None,
        color=None,
        foreground=None,
        background=None,
        effects=None,
        # write_transformers=None,
        fill=None,
        context=None,
        **kwargs,
    ):
        """
        Decorator to pass decorated function an updated, stacked context
        with all options passed in the call already set.

        If an explicit
        'transformers' if passed will be used to draw the pixels, if it makes sense
        (i.e. the pixels are t    Add a "clear" draw method to empty-up a target.o
        be transformed on write, rather than on reading)

        Existing transformers on the current context will be ignored
        """
        global root_context
        if not root_context:
            from terminedia import context as root_context
        if text_attrs:
            font = kwargs.pop("font", None)
            direction = kwargs.pop("direction", None)
        else:
            font = direction = None
        if all(
            attr is not None
            for attr in (
                char,
                color,
                foreground,
                background,
                effects,  # write_transformers,
                fill,
                *([font, direction] if text_attrs else []),
                context,
            )
        ):
            return func(*args, **kwargs)

        self = args[0] if args else None
        if self and not context_path:
            self_context = getattr(self, "context", None)
        else:
            self_context = self
            for comp in context_path.split("."):
                self_context = getattr(self_context, comp)

        color = color or foreground

        parameters = locals().copy()
        context_kw = {
            attr: parameters[attr]
            for attr in (
                "char",
                "color",
                "background",
                "effects",  #'write_transformers',
                "fill",
                "font",
                "direction",
                "context",
            )
            if parameters[attr] is not None
        }

        work_context = self_context or root_context

        if "context" in sig.parameters:
            kwargs["context"] = work_context

        with work_context(**context_kw):
            return func(*args, **kwargs)

    return wrapper
