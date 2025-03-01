# -*- coding: utf-8 -*-

"""
The free hypergraph category with cospans of labeled hypergraphs as arrows.

Summary
-------

.. autosummary::
    :template: class.rst
    :nosignatures:
    :toctree:

    Hypergraph

.. admonition:: Functions

    .. autosummary::
        :template: function.rst
        :nosignatures:
        :toctree:

        pushout
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from inspect import isclass

import random
from typing import Any, Iterable, Union, TYPE_CHECKING

import matplotlib.pyplot as plt

from networkx import (
    DiGraph as Graph,
    spring_layout,
    draw_networkx,
    dag_longest_path_length,
    weisfeiler_lehman_graph_hash,
)
from networkx.algorithms.isomorphism import is_isomorphic

from discopy import drawing, messages
from discopy.cat import Category
from discopy.drawing import Node
from discopy.utils import (
    factory,
    factory_name,
    assert_isinstance,
    pushout,
    unbiased,
    NamedGeneric,
    AxiomError,
    Composable,
    Whiskerable,
    assert_isatomic,
    assert_istraceable,
    tuplify,
    untuplify,
)
if TYPE_CHECKING:
    from discopy.cat import Ty, Box, Diagram


class Hypergraph(Composable, Whiskerable, NamedGeneric['category', 'functor']):
    """
    A hypergraph is given by a domain, a codomain, a list of boxes, a list of
    spider types and a list of wires from :meth:`ports` to spiders.

    Parameters:
        dom (frobenius.Ty) : The domain of the diagram, i.e. its input.
        cod (frobenius.Ty) : The codomain of the diagram, i.e. its output.
        boxes (tuple[frobenius.Box, ...]) : The boxes inside the diagram.
        wires (tuple[Any, ...]) : List of wires from ports to spiders.
        spider_types : Mapping[Any, frobenius.Ty]
            Mapping from spiders to atomic types, if :code:`None` then this is
            computed from the types of ports.
        offsets : tuple[int | None, ...]
            Number of wires to the left of each box used by :meth:`to_diagram`.

    Note
    ----
    Hypergraphs are parameterised by a ``category``, i.e. ``dom`` and ``cod``
    are of type ``category.ob`` and each box is of type ``category.ar``.

    >>> from discopy.frobenius import Hypergraph as H
    >>> from discopy.frobenius import Ty, Diagram
    >>> assert H.category.ob == Ty and H.category.ar == Diagram

    They are also parameterised by a ``functor`` called by :meth:`to_diagram`.

    >>> from discopy.frobenius import Functor
    >>> assert H.functor == Functor

    Note
    ----
    The wires go from ports to spiders, they are given as a list of length::

        len(dom) + sum(len(box.dom) + len(box.cod) for box in boxes) + len(cod)

    The values themselves don't matter, they are simply labels for the spiders.
    We must have :code:`len(types) >= len(set(wires))`, the spiders that don't
    appear as the target of a wire are scalar spiders, i.e. with zero legs.

    Abstractly, a hypergraph diagram can be seen as a cospan for the boundary::

        range(len(dom)) -> range(n_spiders) <- range(len(cod))

    together with a cospan for each box in boxes::

        range(len(box.dom)) -> range(n_spiders) <- range(len(box.cod))

    Composition of two hypergraph diagram is given by the pushout of the span::

        range(self.n_spiders) <- range(len(self.cod)) -> range(other.n_spiders)

    Examples
    --------
    >>> x, y, z = map(Ty, "xyz")

    >>> assert H.id(x @ y @ z).n_spiders == 3
    >>> assert H.id(x @ y @ z).wires == (0, 1, 2, 0, 1, 2)

    >>> assert H.swap(x, y).n_spiders == 2
    >>> assert H.swap(x, y).wires == (0, 1, 1, 0)

    >>> assert H.spiders(1, 2, x @ y).n_spiders == 2
    >>> assert H.spiders(1, 2, x @ y).wires == (0, 1, 0, 1, 0, 1)
    >>> assert H.spiders(0, 0, x @ y @ z).n_spiders == 3
    >>> assert H.spiders(0, 0, x @ y @ z).wires == ()

    >>> from discopy.frobenius import Box
    >>> f, g = Box('f', x, y).to_hypergraph(), Box('g', y, z).to_hypergraph()

    >>> assert f.n_spiders == g.n_spiders == 2
    >>> assert f.wires == g.wires == (0, 0, 1, 1)

    >>> assert (f >> g).n_spiders == 3
    >>> assert (f >> g).wires == (0, 0, 1, 1, 2, 2)

    >>> assert (f @ g).n_spiders == 4
    >>> assert (f @ g).wires == (0, 1, 0, 2, 1, 3, 2, 3)
    """
    category = None
    functor = None

    def __init__(
            self, dom: Ty, cod: Ty, boxes: tuple[Box, ...],
            wires: tuple[Any, ...],
            spider_types: Union[Mapping[Any, Ty], Iterable[Ty]] = None,
            offsets: tuple[int | None, ...] = None):
        assert_isinstance(dom, self.category.ob)
        assert_isinstance(cod, self.category.ob)
        for box in boxes:
            assert_isinstance(box, self.category.ar)
        assert_isinstance(wires, tuple)
        self.dom, self.cod, self.boxes = dom, cod, boxes
        if len(wires) != len(self.ports):
            raise ValueError
        if spider_types is None:
            spider_types = {
                spider: port.obj for spider, port in zip(wires, self.ports)}
        if not isinstance(spider_types, Mapping):
            spider_types = dict(enumerate(spider_types))
        relabeling = sorted(set(wires), key=wires.index)
        relabeling += sorted(set(spider_types) - set(relabeling))
        self.wires = tuple(relabeling.index(s) for s in wires)
        self.spider_types = tuple(map(
            lambda typ: typ.r if getattr(typ, "z", 0) else typ,
            [spider_types[s] for s in relabeling]))
        for obj in self.spider_types:
            assert_isatomic(obj, self.category.ob)
        for obj, wires in zip(self.spider_types, self.spider_wires):
            adjoint = getattr(obj, "r", obj)
            for i in set.union(*wires):
                if self.ports[i].obj not in [obj, adjoint]:
                    raise AxiomError(messages.TYPE_ERROR.format(
                        obj, self.ports[i].obj))
        self.offsets = offsets or tuple(len(boxes) * [None])

    @property
    def box_wires(self) -> list[tuple[tuple[int, ...], tuple[int, ...]]]:
        """
        The wires connecting the boxes of a hypergraph.

        Returns a list of length :code:`len(self.boxes)` such that::

            dom_wires, cod_wires = self.box_wires[i]
            len(dom_wires) == len(box.dom) and len(cod_wires) == len(box.cod)

        for :code:`box = self.boxes[i]`.

        Example
        -------
        >>> from discopy.frobenius import Ty, Box, Hypergraph as H

        >>> x, y, z = map(Ty, "xyz")
        >>> f = Box('f', x, y @ y).to_hypergraph()
        >>> g = Box('g', y @ y, z).to_hypergraph()
        >>> for wires in (f >> g).box_wires: print(wires)
        ((0,), (1, 2))
        ((1, 2), (3,))
        """
        result, i = [], len(self.dom)
        for box in self.boxes:
            dom_wires = self.wires[i:i + len(box.dom)]
            cod_wires = self.wires[i + len(box.dom):i + len(box.dom @ box.cod)]
            result.append((dom_wires, cod_wires))
            i += len(box.dom @ box.cod)
        return result

    @property
    def spider_wires(self) -> list[tuple[set[int], set[int]]]:
        """
        The input and output wires for each spider of a hypergraph.

        Example
        -------
        >>> from discopy.frobenius import Ty, Box, Hypergraph as H

        >>> x, y, z = map(Ty, "xyz")
        >>> f = Box('f', x, y).to_hypergraph()
        >>> for wires in (f >> H.spiders(1, 2, y)).spider_wires: print(wires)
        ({0}, {1})
        ({2}, {3, 4})
        """
        result = [(set(), set()) for _ in range(self.n_spiders)]
        for port, spider in enumerate(self.wires[:len(self.dom)]):
            result[spider][0].add(port)
        n_ports = len(self.dom)
        for dom_wires, cod_wires in self.box_wires:
            for port, spider in enumerate(dom_wires):
                result[spider][1].add(port + n_ports)
            for port, spider in enumerate(cod_wires):
                result[spider][0].add(port + n_ports + len(dom_wires))
            n_ports += len(dom_wires + cod_wires)
        output_wires = self.wires[len(self.wires) - len(self.cod):]
        for port, spider in enumerate(output_wires):
            result[spider][1].add(port + n_ports)
        return result

    @property
    def ports(self):
        """
        The ports in a diagram.

        Examples
        --------
        >>> from discopy.frobenius import Ty, Box, Hypergraph as H

        >>> x, y, z = map(Ty, "xyz")
        >>> f = Box('f', x, y @ y).to_hypergraph()
        >>> g = Box('g', y @ y, z).to_hypergraph()
        >>> for port in (f >> g).ports: print(port)
        Node('input', i=0, obj=frobenius.Ty(frobenius.Ob('x')))
        Node('dom', depth=0, i=0, obj=frobenius.Ty(frobenius.Ob('x')))
        Node('cod', depth=0, i=0, obj=frobenius.Ty(frobenius.Ob('y')))
        Node('cod', depth=0, i=1, obj=frobenius.Ty(frobenius.Ob('y')))
        Node('dom', depth=1, i=0, obj=frobenius.Ty(frobenius.Ob('y')))
        Node('dom', depth=1, i=1, obj=frobenius.Ty(frobenius.Ob('y')))
        Node('cod', depth=1, i=0, obj=frobenius.Ty(frobenius.Ob('z')))
        Node('output', i=0, obj=frobenius.Ty(frobenius.Ob('z')))
        """
        inputs = [Node("input", i=i, obj=obj)
                  for i, obj in enumerate(self.dom)]
        doms_and_cods = sum([[
            Node(kind, depth=depth, i=i, obj=obj)
            for i, obj in enumerate(typ)]
            for depth, box in enumerate(self.boxes)
            for kind, typ in [("dom", box.dom), ("cod", box.cod)]], [])
        outputs = [Node("output", i=i, obj=obj)
                   for i, obj in enumerate(self.cod)]
        return inputs + doms_and_cods + outputs

    @property
    def n_spiders(self):
        """ The number of spiders in a hypergraph diagram. """
        return len(self.spider_types)

    @property
    def scalar_spiders(self):
        """ The zero-legged spiders in a hypergraph diagram. """
        return [i for i in range(self.n_spiders) if not self.wires.count(i)]

    @classmethod
    def id(cls, dom=None) -> Hypergraph:
        dom = cls.category.ob() if dom is None else dom
        return cls(dom, dom, (), 2 * tuple(range(len(dom))))

    twist = id

    @unbiased
    def then(self, other: Hypergraph):
        """
        Composition of two hypergraph diagrams, i.e. their :func:`pushout`.
        """
        if not self.cod == other.dom:
            raise AxiomError
        dom, cod = self.dom, other.cod
        boxes, offsets = self.boxes + other.boxes, self.offsets + other.offsets
        self_boundary = self.wires[len(self.wires) - len(self.cod):]
        other_boundary = other.wires[:len(other.dom)]
        left, right = pushout(
            self.n_spiders, other.n_spiders, self_boundary, other_boundary)
        wires = [left[i] for i in self.wires[:len(self.wires) - len(self.cod)]]
        wires += [right[i] for i in other.wires[len(other.dom):]]
        spider_types = {
            left[i]: t for i, t in enumerate(self.spider_types)}
        spider_types.update({
            right[i]: t for i, t in enumerate(other.spider_types)})
        return type(self)(dom, cod, boxes, tuple(wires), spider_types, offsets)

    @unbiased
    def tensor(self, other: Hypergraph):
        """ Tensor of two hypergraph diagrams, i.e. their disjoint union. """
        dom, cod = self.dom @ other.dom, self.cod @ other.cod
        boxes, offsets = self.boxes + other.boxes, self.offsets + other.offsets
        dom_wires = self.wires[:len(self.dom)] + tuple(
            self.n_spiders + i for i in other.wires[:len(other.dom)])
        box_wires = self.wires[len(self.dom):-len(self.cod) or len(self.wires)]
        box_wires += tuple(self.n_spiders + i for i in other.wires[
            len(other.dom):-len(other.cod) or len(other.wires)])
        cod_wires = self.wires[len(self.wires) - len(self.cod):] + tuple(
            self.n_spiders + i
            for i in other.wires[len(other.wires) - len(other.cod):])
        wires = dom_wires + box_wires + cod_wires
        spiders = self.spider_types + other.spider_types
        return type(self)(dom, cod, boxes, wires, spiders, offsets)

    def dagger(self):
        """
        Dagger of a hypergraph diagram, called with :code:`[::-1]`.

        Examples
        --------
        >>> from discopy.frobenius import Ty, Box, Hypergraph as H
        >>> x, y, z = map(Ty, "xyz")
        >>> f = Box('f', x, y).to_hypergraph()
        >>> g = Box('g', y, z).to_hypergraph()
        >>> assert (f >> g)[::-1] == g[::-1] >> f[::-1]
        >>> assert H.spiders(1, 2, x @ y)[::-1] == H.spiders(2, 1, x @ y)
        """
        dom, cod = self.cod, self.dom
        boxes = tuple(box.dagger() for box in self.boxes[::-1])
        dom_wires = self.wires[len(self.wires) - len(self.cod):]
        box_wires = sum([
            cod_wires + dom_wires
            for dom_wires, cod_wires in self.box_wires[::-1]], ())
        cod_wires = self.wires[:len(self.dom)]
        wires = dom_wires + box_wires + cod_wires
        return type(self)(
            dom, cod, boxes, wires, self.spider_types, self.offsets[::-1])

    @classmethod
    def swap(cls, left, right):
        dom, cod = left @ right, right @ left
        boxes, wires = (), tuple(range(len(dom)))\
            + tuple(range(len(left), len(dom))) + tuple(range(len(left)))
        return cls(dom, cod, boxes, wires)

    braid = swap

    @classmethod
    def spiders(cls, n_legs_in, n_legs_out, typ):
        dom, cod = typ ** n_legs_in, typ ** n_legs_out
        boxes, spider_types = (), tuple(typ)
        wires = (n_legs_in + n_legs_out) * tuple(range(len(typ)))
        return cls(dom, cod, boxes, wires, spider_types)

    @classmethod
    def copy(cls, typ, n=2) -> Hypergraph:
        return cls.spiders(1, n, typ)

    @classmethod
    def merge(cls, typ, n=2) -> Hypergraph:
        return cls.spiders(n, 1, typ)

    cup_factory = classmethod(lambda cls, left, right: cls.from_box(
        cls.category.ar.cup_factory(left, right)))
    cap_factory = classmethod(lambda cls, left, right: cls.from_box(
        cls.category.ar.cap_factory(left, right)))

    @classmethod
    def cups(cls, left, right):
        if not getattr(left, 'r', left[::-1]) == right:
            raise AxiomError
        wires = tuple(range(len(left))) + tuple(reversed(range(len(left))))
        return cls(left @ right, cls.category.ob(), (), wires)

    @classmethod
    def caps(cls, left, right):
        if not getattr(left, 'r', left[::-1]) == right:
            raise AxiomError
        wires = tuple(range(len(left))) + tuple(reversed(range(len(left))))
        return cls(cls.category.ob(), left @ right, (), wires)

    def transpose(self, left=False):
        """ The transpose of a hypergraph diagram. """
        return self.category.ar.transpose(self, left)

    def rotate(self, left=False):
        """
        The half-turn rotation of a hypergraph, called with ``.l`` and ``.r``.
        """
        dom, cod = (x.l if left else x.r for x in (self.cod, self.dom))
        boxes = tuple(box.l if left else box.r for box in self.boxes[::-1])
        dom_wires = self.wires[len(self.wires) - len(self.cod):][::-1]
        box_wires = sum([
            cod_wires[::-1] + dom_wires[::-1]
            for dom_wires, cod_wires in self.box_wires[::-1]], ())
        cod_wires = self.wires[:len(self.dom)][::-1]
        wires = dom_wires + box_wires + cod_wires
        return type(self)(
            dom, cod, boxes, wires, self.spider_types, self.offsets[::-1])

    l = property(lambda self: self.rotate(left=True))
    r = property(lambda self: self.rotate(left=False))

    def explicit_trace(self, left=False):
        """
        The trace of a hypergraph with explicit boxes (trace, cup or cap).

        Parameters:
            left : Whether to trace on the left or right.

        Note
        ----
        When ``category.ar.trace_factory`` is a subclass of ``category.ar``,
        e.g. for symmetric diagrams, then the result is just one big trace box
        wrapped up as a hypergraph.

        Otherwise, we assume that the trace factory is a class method, e.g.
        for compact diagrams, in which case we use this method to introduce
        cup and cap boxes.
        """
        factory = self.category.ar.trace_factory
        if isclass(factory) and issubclass(factory, self.category.ar):
            return self.from_box(factory(self.to_diagram(), left))
        return factory.__func__(type(self), self, left)

    def trace(self, n=1, left=False):
        """
        The trace of a hypergraph is its pre- and post-composition with
        cups and caps to form a feedback loop.

        Parameters:
            n : The number of wires to trace.
            left : Whether to trace on the left or right.
        """
        assert_istraceable(self, n, left)
        dom, cod = (self.dom[n:], self.cod[n:]) if left\
            else (self.dom[:-n], self.cod[:-n])
        traced_wires = self.dom[:n] if left else self.dom[len(self.dom) - n:]
        traced_wires_r = getattr(traced_wires, "r", traced_wires[::-1])
        return self.caps(traced_wires_r, traced_wires) @ dom\
            >> traced_wires_r @ self\
            >> self.cups(traced_wires_r, traced_wires) @ cod if left\
            else dom @ self.caps(traced_wires, traced_wires_r)\
            >> self @ traced_wires_r\
            >> cod @ self.cups(traced_wires, traced_wires_r)

    def interchange(self, i: int, j: int) -> Hypergraph:
        """
        Interchange boxes at indices ``i`` and ``j``.

        Parameters:
            i : The index of the first box.
            j : The index of the second box.

        Example
        -------
        >>> from discopy.frobenius import Ty, Box, Hypergraph as H
        >>> x = Ty('x')
        >>> f = Box('f', Ty(), x).to_hypergraph()
        >>> g = Box('g', x, Ty()).to_hypergraph()
        >>> print((f >> g).interchange(0, 1))
        Cap(x, x) >> g @ x >> f @ x >> Cup(x, x)
        """
        boxes, offsets = list(self.boxes), list(self.offsets)
        boxes[i], boxes[j] = boxes[j], boxes[i]
        offsets[i], offsets[j] = offsets[j], offsets[i]
        boxes, offsets = tuple(boxes), tuple(offsets)
        box_wires = list(self.box_wires)
        box_wires[i], box_wires[j] = box_wires[j], box_wires[i]
        box_wires = sum([c + d for c, d in box_wires], ())
        dom_wires = self.wires[:len(self.dom)]
        cod_wires = self.wires[len(self.wires) - len(self.cod):]
        wires = dom_wires + box_wires + cod_wires
        return type(self)(
            self.dom, self.cod, boxes, wires, self.spider_types, offsets)

    def simplify(self) -> Hypergraph:
        """
        Simplify by applying interchangers eagerly until the length of the
        diagram is minimal, takes quadratic time.

        Example
        -------
        >>> from discopy.frobenius import Ty, Box, Hypergraph as H
        >>> x = Ty('x')
        >>> f = Box('f', Ty(), x).to_hypergraph()
        >>> g = Box('g', x, Ty()).to_hypergraph()
        >>> assert (f >> g).interchange(0, 1).simplify() == f >> g
        """
        for i in range(len(self.boxes)):
            for j in range(len(self.boxes)):
                result = self.interchange(i, j)
                if len(result.to_diagram()) < len(self.to_diagram()):
                    return result.simplify()
        return self

    def __getitem__(self, key):
        if key == slice(None, None, -1):
            return self.dagger()
        raise NotImplementedError

    def __eq__(self, other: Any):
        if not isinstance(other, Hypergraph):
            return False
        return self.is_parallel(other) and is_isomorphic(
            self.to_graph(), other.to_graph(), lambda x, y: x == y)

    def __hash__(self):
        return hash((self.dom, self.cod, weisfeiler_lehman_graph_hash(
            self.to_graph(), node_attr="box")))

    def __repr__(self):
        spider_types = f", spider_types={self.spider_types}"\
            if self.scalar_spiders else ""
        return factory_name(type(self))\
            + f"(dom={repr(self.dom)}, cod={repr(self.cod)}, " \
              f"boxes={repr(self.boxes)}, " \
              f"wires={repr(self.wires)}{spider_types})"

    def __str__(self):
        return str(self.to_diagram())

    @property
    def bijection(self):
        """
        Bijection between ports.

        Raises
        ------
            ValueError : If the hypergraph is not bijective.

        Examples
        --------
        >>> from discopy.frobenius import Ty, Box, Hypergraph as H
        >>> x, y = map(Ty, "xy")
        >>> f = Box('f', x, y).to_hypergraph()
        >>> for i, port in enumerate(f.ports): print(i, port)
        0 Node('input', i=0, obj=frobenius.Ty(frobenius.Ob('x')))
        1 Node('dom', depth=0, i=0, obj=frobenius.Ty(frobenius.Ob('x')))
        2 Node('cod', depth=0, i=0, obj=frobenius.Ty(frobenius.Ob('y')))
        3 Node('output', i=0, obj=frobenius.Ty(frobenius.Ob('y')))
        >>> for i, j in enumerate(f.bijection): print(f"{i} -> {j}")
        0 -> 1
        1 -> 0
        2 -> 3
        3 -> 2
        """
        if not self.is_bijective:
            raise ValueError
        result = {}
        for source, spider in enumerate(self.wires):
            if spider in self.wires[source + 1:]:
                target = self.wires[source + 1:].index(spider) + source + 1
                result[source], result[target] = target, source
        return [result[source] for source in sorted(result)]

    @property
    def is_bijective(self) -> bool:
        """
        Checks bijectivity, i.e. each spider is connected to two or zero ports.
        In that case, the diagram actually lives in a compact-closed category,
        i.e. it can be drawn using only swaps, cups and caps.

        Examples
        --------
        >>> from discopy.frobenius import Ty, Box, Hypergraph as H
        >>> x, y = map(Ty, "xy")
        >>> f = Box('f', x, y).to_hypergraph()
        >>> assert f.is_bijective and f.transpose().is_bijective
        >>> assert H.cups(x, x).is_bijective and H.caps(x, x).is_bijective
        >>> assert H.spiders(0, 0, x).is_bijective
        >>> assert not H.spiders(1, 2, x).is_bijective
        """
        return all(
            self.wires.count(i) in [0, 2] for i in range(self.n_spiders))

    @property
    def is_monogamous(self) -> bool:
        """
        Checks monogamy, i.e. each input connects to exactly one output,
        formally whether :code:`self.wires` induces a bijection::

            len(self.dom) + sum(len(box.dom) for box in boxes)
            == self.n_spiders - len(self.scalar_spiders)
            == sum(len(box.cod) for box in boxes) + len(self.dom)

        In that case, the diagram actually lives in a traced category.

        Examples
        --------
        >>> from discopy.frobenius import Ty, Box, Hypergraph as H

        >>> x, y = map(Ty, "xy")
        >>> f = Box('f', x, y).to_hypergraph()

        >>> assert f.is_monogamous
        >>> assert (f >> f[::-1]).is_monogamous

        >>> assert H.spiders(0, 0, x).is_monogamous

        >>> cycle = H.caps(x, x) >> x @ (f >> f[::-1]) >> H.cups(x, x)
        >>> assert cycle.is_monogamous

        >>> assert not f.transpose().is_monogamous
        >>> assert not H.cups(x, x).is_monogamous
        >>> assert not H.spiders(1, 2, x).is_monogamous
        >>> assert not H.spiders(2, 3, x).is_monogamous
        """
        for input_wires, output_wires in self.spider_wires:
            if len(input_wires) != len(output_wires):
                return False
            if len(input_wires) + len(output_wires) not in [0, 2]:
                return False
        return True

    @property
    def is_polygynous(self) -> bool:
        """
        Checks polygyny, i.e. if each non-scalar spider is connected to exactly
        one output port.
        """
        return all(len(x) == 1 for x, y in self.spider_wires if x.union(y))

    @property
    def is_causal(self) -> bool:
        """
        Checks causality, i.e. if each spider is connected to exactly one
        output port and to zero or more input ports all with higher indices.

        If the diagram is causal then it lives in a symmetric monoidal
        category with a supply of commutative comonoids.

        If the diagram is causal and monogamous then it actually lives in
        a symmetric monoidal category, i.e. it can be drawn using only swaps.

        Examples
        --------
        >>> from discopy.frobenius import Ty, Box, Hypergraph as H
        >>> x, y = map(Ty, "xy")
        >>> f = Box('f', x, y).to_hypergraph()
        >>> assert f.is_causal
        >>> assert (f >> H.spiders(1, 0, y)).is_causal
        >>> assert (H.spiders(1, 2, x) >> f @ f).is_causal


        >>> cycle = H.caps(x, x) >> H.cups(x, x)
        >>> assert not cycle.is_causal

        >>> assert not H.cups(x, x).is_causal
        """
        return all(len(input_wires) == 1 and all(
            u < v for u in input_wires for v in output_wires)
            for input_wires, output_wires in self.spider_wires)

    def make_bijective(self) -> Hypergraph:
        """
        Introduces spider boxes to make self bijective.

        Example
        -------
        >>> from discopy.frobenius import Ty, Spider, Hypergraph as H

        >>> spider = H.spiders(3, 2, Ty('x')).make_bijective()
        >>> assert spider.boxes == (Spider(3, 2, Ty('x')), )
        >>> assert spider.wires == (0, 1, 2) + (0, 1, 2) + (3, 4) + (3, 4)

        >>> copy = H.spiders(1, 2, Ty('x', 'y')).make_bijective()
        >>> assert copy.boxes == (Spider(1, 2, Ty('x')), Spider(1, 2, Ty('y')))

        >>> unit = H.spiders(0, 1, Ty('x', 'y')).make_bijective()
        >>> assert unit.boxes == (Spider(0, 1, Ty('y')), Spider(0, 1, Ty('x')))
        """
        boxes = list(self.boxes)
        wires = list(self.wires)
        spider_types = list(self.spider_types)
        for spider, (typ, (input_wires, output_wires)) in reversed(list(
                enumerate(zip(spider_types, self.spider_wires)))):
            n_legs = len(input_wires) + len(output_wires)
            if n_legs in [0, 2]:
                continue
            if input_wires:
                node = self.ports[max(input_wires)]
                depth = 0 if node.kind == "input" else node.depth + 1
            else:
                node = self.ports[min(output_wires)]
                depth = len(boxes) if node.kind == "output" else node.depth
            boxes = boxes[:depth] + [self.category.ar.spider_factory(
                len(input_wires), len(output_wires), typ)] + boxes[depth:]
            offsets = self.offsets[:depth] + (None, ) + self.offsets[depth:]
            for j, port in enumerate(input_wires.union(output_wires)):
                wires[port] = len(spider_types) + j
            i = len(self.dom) + len(
                sum([sum(wires, ()) for wires in self.box_wires[:depth]], ()))
            wires = wires[:i] + list(range(
                len(spider_types), len(spider_types) + n_legs)) + wires[i:]
            spider_types += n_legs * [typ]
            del spider_types[spider]
            wires = [w - 1 if w > spider else w for w in wires]
            return type(self)(
                self.dom, self.cod, tuple(boxes),
                tuple(wires), spider_types, offsets).make_bijective()
        return self

    def make_monogamous(self) -> Hypergraph:
        """
        Introduce :class:`Cup` and :class:`Cap` boxes to make self monogamous.

        Example
        -------
        >>> from discopy.frobenius import Ty, Box, Cup, Cap, Spider
        >>> x = Ty('x')
        >>> h = Box('f', x, x).transpose().to_hypergraph().make_monogamous()
        >>> assert h.boxes == (Cap(x, x), Box('f', x, x), Cup(x, x))
        >>> assert h.wires == (0, ) + (1, 2) + (1, 3) + (0, 3) + (2, )
        """
        if not self.is_bijective:
            return self.make_bijective().make_monogamous()
        for kinds, cups_or_caps in [
                (["input", "cod"], "cups"),
                (["dom", "output"], "caps")]:
            for source, spider in [
                    (source, spider) for source, (spider, port)
                    in enumerate(zip(self.wires, self.ports))
                    if port.kind in kinds]:
                if spider not in self.wires[source + 1:]:
                    continue
                target = self.wires[source + 1:].index(spider) + source + 1
                if self.ports[target].kind in kinds:
                    spider_types = dict(enumerate(self.spider_types))
                    typ = spider_types[spider]
                    left, right = len(spider_types), len(spider_types) + 1
                    wires = list(self.wires)
                    wires[source], wires[target] = left, right
                    if cups_or_caps == "cups":
                        boxes = self.boxes + (
                            self.category.ar.cup_factory(typ, typ), )
                        offsets = self.offsets + (None, )
                        wires = wires[:len(wires) - len(self.cod)] + [
                            left, right] + wires[len(wires) - len(self.cod):]
                    else:
                        boxes = (self.category.ar.cap_factory(typ, typ),
                                 ) + self.boxes
                        offsets = (None, ) + self.offsets
                        wires = wires[:len(self.dom)] + [
                            left, right] + wires[len(self.dom):]
                    wires = tuple(wires)
                    spider_types[left] = spider_types[right] = typ
                    del spider_types[spider]
                    return type(self)(
                        self.dom, self.cod, boxes, wires, spider_types, offsets
                    ).make_monogamous()
        return self

    def make_polygynous(self) -> Hypergraph:
        """
        Introduce spider boxes to make self polygynous.

        Example
        -------
        >>> from discopy.frobenius import Ty, Box, Hypergraph as H, Spider
        >>> h = H.spiders(2, 3, Ty('x')).make_polygynous()
        >>> assert h.boxes == (Spider(2, 1, Ty('x')), )
        >>> assert h.wires == (0, 1) + (0, 1, 2) + (2, 2, 2)
        """
        for spider, (typ, (input_wires, output_wires)) in enumerate(
                zip(self.spider_types, self.spider_wires)):
            n_legs = len(input_wires) + len(output_wires)
            if len(input_wires) == 1:
                continue
            depth = getattr(self.ports[max(input_wires)], "depth", -1) + 1\
                if input_wires else 0
            boxes = self.boxes[:depth] + (self.category.ar.spider_factory(
                len(input_wires), 1, typ), ) + self.boxes[depth:]
            offsets = self.offsets[:depth] + (None, ) + self.offsets[depth:]
            wires = list(self.wires)
            for j, port in enumerate(input_wires):
                wires[port] = self.n_spiders + j
            i = len(self.dom) + len(
                sum([sum(wires, []) for wires in self.box_wires[:depth]], []))
            wires = wires[:i] + list(range(
                self.n_spiders, self.n_spiders + len(input_wires))
            ) + [spider] + wires[i:]
            spider_types = self.spider_types + len(input_wires) * (typ, )
            return type(self)(
                self.dom, self.cod, boxes, tuple(wires), spider_types, offsets
            ).make_polygynous()
        return self

    def make_causal(self) -> Hypergraph:
        """
        Introduce trace boxes to make self causal.

        Example
        -------
        >>> from discopy.frobenius import Ty, Box, Cup, Cap
        >>> x = Ty('x')
        >>> f = Box('f', x @ x, x @ x).to_hypergraph()
        >>> assert f.trace().make_causal().boxes\\
        ...     == (Cap(x, x), f.boxes[0], Cup(x, x))

        >>> from discopy.frobenius import Hypergraph as H, Spider
        >>> assert H.spiders(2, 1, x).make_causal().boxes\\
        ...     == (Spider(2, 1, x),)
        """
        if not self.is_polygynous:
            return self.make_polygynous().make_causal()
        for input_spider, (typ, (input_wires, output_wires)) in enumerate(
                zip(self.spider_types, self.spider_wires)):
            if not input_wires:
                assert not output_wires
                dom, cod = self.dom @ typ, self.cod @ typ
                wires = list(self.wires)
                wires = wires[:len(self.dom)] + [input_spider]\
                    + wires[len(self.dom):] + [input_spider]
                boxes, wires = self.boxes, tuple(wires)
                arg = type(self)(
                    dom, cod, boxes, wires, self.spider_types, self.offsets)
                return arg.make_causal().explicit_trace()
            input_wire, = input_wires
            for output_wire in output_wires:
                if output_wire < input_wire:
                    dom, cod = self.dom @ typ, self.cod @ typ
                    spider_types = self.spider_types + (typ, )
                    output_spider = len(spider_types) - 1
                    wires = list(self.wires)
                    wires[output_wire] = output_spider
                    wires = wires[:len(self.dom)] + [output_spider]\
                        + wires[len(self.dom):] + [input_spider]
                    boxes, wires = self.boxes, tuple(wires)
                    arg = type(self)(
                        dom, cod, boxes, wires, spider_types, self.offsets)
                    return arg.make_causal().explicit_trace()
        return self

    @classmethod
    def from_box(cls, box: Box) -> Hypergraph:
        """
        Turn a box into a hypergraph with binary spiders for each wire.

        Parameters:
            box : The box to turn into a hypergraph.

        Example
        -------
        >>> from discopy.frobenius import Ty, Box, Hypergraph as H
        >>> x, y, z = map(Ty, "xyz")
        >>> for p in Box('f', x, y @ z).to_hypergraph().ports: print(p)
        Node('input', i=0, obj=frobenius.Ty(frobenius.Ob('x')))
        Node('dom', depth=0, i=0, obj=frobenius.Ty(frobenius.Ob('x')))
        Node('cod', depth=0, i=0, obj=frobenius.Ty(frobenius.Ob('y')))
        Node('cod', depth=0, i=1, obj=frobenius.Ty(frobenius.Ob('z')))
        Node('output', i=0, obj=frobenius.Ty(frobenius.Ob('y')))
        Node('output', i=1, obj=frobenius.Ty(frobenius.Ob('z')))
        """
        spider_types = tuple(box.dom @ box.cod)
        wires = 2 * tuple(range(len(box.dom)))\
            + 2 * tuple(range(len(box.dom), len(box.dom @ box.cod)))
        return cls(box.dom, box.cod, (box, ), wires, spider_types)

    @classmethod
    def from_diagram(cls, old: Diagram) -> Hypergraph:
        """
        Turn a :class:`Diagram` into a :class:`Hypergraph`.

        Parameters:
            old : The planar diagram to encode as hypergraph.

        Example
        -------
        >>> from discopy.frobenius import Ty, Hypergraph as H
        >>> x, y = map(Ty, "xy")
        >>> back_n_forth = lambda d: H.from_diagram(d.to_diagram())
        >>> for d in [H.spiders(0, 0, x),
        ...           H.spiders(2, 3, x),
        ...           H.spiders(1, 2, x @ y)]:
        ...     assert back_n_forth(d) == d
        """
        return cls.functor(
            ob=lambda typ: typ, ar=cls.from_box,
            dom=Category(old.ty_factory, type(old)),
            cod=Category(old.ty_factory, cls))(old)

    def to_diagram(self, make_causal_first: bool = False) -> Diagram:
        """
        Downgrade to :class:`Diagram`, called by :code:`print`.

        Parameters:
            make_causal_first : The order in which we downgrade.

        Note
        ----
        Hypergraphs can be translated to planar diagrams in two different ways:

        * either we first :meth:`make_bijective` (introducing spiders) then
          :meth:`make_monogamous` (introducing cups and caps) and finally
          :meth:`make_causal` (introducing traces)
        * or we first :meth:`make_polygynous` (introducing merges) then
          :meth:`make_causal` (introducing traces) and finally
          :meth:`make_bijective` (introducing copies).

        Examples
        --------
        >>> from discopy.frobenius import Ty, Box, Hypergraph as H
        >>> x = Ty('x')
        >>> v = Box('v', Ty(), x @ x).to_hypergraph()
        >>> print(v >> H.swap(x, x) >> v[::-1])
        v >> Swap(x, x) >> v[::-1]
        >>> print(x @ H.swap(x, x) >> v[::-1] @ x)
        x @ Swap(x, x) >> v[::-1] @ x
        """
        if not self.is_causal or not self.is_monogamous:
            if make_causal_first:
                return self.make_causal().make_bijective().to_diagram()
            else:
                return self.make_monogamous().make_causal().to_diagram()
        diagram = self.category.ar.id(self.dom)
        scan, n_ports = self.wires[:len(self.dom)], len(self.dom)
        for depth, (box, offset) in enumerate(zip(self.boxes, self.offsets)):
            for i, obj in enumerate(box.dom):
                j = scan.index(self.wires[n_ports + i])
                if i == 0 and offset is None:
                    offset = j
                elif j > offset + i:
                    diagram >>= diagram.cod[:offset + i] @ diagram.swap(
                        diagram.cod[offset + i:j], diagram.cod[j]
                    ) @ diagram.cod[j + 1:]
                    scan = (scan[:offset + i] + scan[j:j + 1]) + (
                        scan[offset + i:j] + scan[j + 1:])
                elif j < offset + i:
                    diagram >>= diagram.cod[:j] @ diagram.swap(
                        diagram.cod[j], diagram.cod[j + 1:offset + i]
                    ) @ diagram.cod[offset + i:]
                    scan = (scan[:j] + scan[j + 1:offset + i]) + (
                        scan[j:j + 1] + scan[offset + i:])
                    offset -= 1
                assert len(scan) == len(diagram.cod)
            offset = 0 if offset is None else offset
            scan = scan[:offset] + self.wires[
                n_ports + len(box.dom):n_ports + len(box.dom @ box.cod)
            ] + scan[offset + len(box.dom):]
            diagram >>= diagram.cod[:offset] @ box @ diagram.cod[
                offset + len(box.dom):]
            n_ports += len(box.dom @ box.cod)
        for i, _ in enumerate(self.cod):
            j = scan.index(self.wires[n_ports + i])
            if i < j:
                diagram >>= diagram.cod[:i] @ diagram.swap(
                    diagram.cod[i:j], diagram.cod[j:j + 1]
                ) @ diagram.cod[j + 1:]
                scan = scan[:i] + scan[j:j + 1] + scan[i:j] + scan[j + 1:]
        return diagram

    @classmethod
    def from_callable(cls, dom: Ty, cod: Ty) -> Callable[Callable, Hypergraph]:
        """
        Turns an arbitrary Python function into a causal hypergraph.

        Parameters:
            dom : The domain of the hypergraph.
            cod : The codomain of the hypergraph.
        """
        def decorator(func):
            graph, box_nodes, spider_nodes = Graph(), [], []

            def apply(box, *inputs, offset=None):
                for node in inputs:
                    assert_isinstance(node, Node)
                if len(inputs) != len(box.dom):
                    raise AxiomError(f"Expected {len(box.dom)} inputs, "
                                     f"got {len(inputs)} instead.")
                depth = len(box_nodes)
                box_node = Node("box", box=box, depth=depth, offset=offset)
                box_nodes.append(box_node)
                graph.add_node(box_node)
                for i, obj in enumerate(box.dom):
                    if inputs[i].obj != obj:
                        raise AxiomError(f"Expected {obj} as input, "
                                         f"got {inputs[i].obj} instead.")
                    dom_node = Node("dom", obj=obj, i=i, depth=depth)
                    graph.add_edge(inputs[i], dom_node)
                    graph.add_edge(dom_node, box_node)
                outputs = []
                for i, obj in enumerate(box.cod):
                    cod_node = Node("cod", obj=obj, i=i, depth=depth)
                    spider = Node("spider", obj=obj, i=len(spider_nodes))
                    graph.add_edge(box_node, cod_node)
                    graph.add_edge(cod_node, spider)
                    spider_nodes.append(spider)
                    outputs.append(spider)
                return untuplify(outputs)

            cls.category.ar.__call__ = apply
            for i, obj in enumerate(dom):
                input_node = Node("input", obj=obj, i=i)
                input_spider = Node("spider", obj=obj, i=i)
                spider_nodes.append(input_spider)
                graph.add_edge(input_node, input_spider)
            for i, spider in enumerate(tuplify(func(*spider_nodes))):
                assert_isinstance(spider, Node)
                node = Node("output", obj=spider.obj, i=i)
                graph.add_edge(spider, node)
            del cls.category.ar.__call__
            result = cls.from_graph(graph)
            if result.cod != cod:
                raise AxiomError(f"Expected diagram.cod == {cod}, "
                                 f"got {result.cod} instead.")
            return result

        return decorator

    @classmethod
    def from_graph(cls, graph: Graph) -> Hypergraph:
        """ The inverse of :meth:`to_graph`. """
        def predecessor(node):
            result, = graph.predecessors(node)
            return result

        def successor(node):
            result, = graph.successors(node)
            return result

        inputs, outputs, box_nodes, spider_nodes = [], [], [], []
        for node in graph.nodes:
            for kind, nodelist in zip(
                    ["input", "output", "box", "spider"],
                    [inputs, outputs, box_nodes, spider_nodes]):
                if node.kind == kind:
                    nodelist.append(node)
        dom = sum([n.obj for n in inputs], cls.category.ob())
        cod = sum([n.obj for n in outputs], cls.category.ob())
        boxes = tuple(n.box for n in box_nodes)
        offsets = tuple(n.offset for n in box_nodes)
        spider_types = {n: n.obj for n in spider_nodes}
        wires = tuple(map(successor, sorted(inputs, key=lambda node: node.i)))
        for box_node in box_nodes:
            wires += tuple(map(predecessor, sorted(
                graph.predecessors(box_node), key=lambda node: node.i)))
            wires += tuple(map(successor, sorted(
                graph.successors(box_node), key=lambda node: node.i)))
        wires += tuple(map(
            predecessor, sorted(outputs, key=lambda node: node.i)))
        return cls(dom, cod, boxes, wires, spider_types, offsets)

    def to_graph(self) -> Graph:
        """
        Translate a hypergraph into a labeled graph with nodes for inputs,
        outputs, boxes, domain, codomain and spiders.
        """
        graph = Graph()
        graph.add_nodes_from(
            Node("spider", i=i, obj=obj)
            for i, obj in enumerate(self.spider_types))
        graph.add_nodes_from(
            (Node("input", i=i, obj=obj), dict(i=i))
            for i, obj in enumerate(self.dom))
        graph.add_edges_from(
            (Node("input", i=i, obj=obj), Node("spider", i=j, obj=obj))
            for i, (j, obj) in enumerate(
                zip(self.wires[:len(self.dom)], self.dom)))
        for i, (box, (dom_wires, cod_wires)) in enumerate(
                zip(self.boxes, self.box_wires)):
            box_node = Node("box", box=box, i=i)
            graph.add_node(box_node, box=box)
            for case, wires in [("dom", dom_wires), ("cod", cod_wires)]:
                for j, spider in enumerate(wires):
                    obj = self.spider_types[spider]
                    spider_node = Node("spider", i=spider, obj=obj)
                    port_node = Node(case, i=i, j=j)
                    graph.add_node(port_node, j=j, box=None)
                    if case == "dom":
                        graph.add_edge(spider_node, port_node)
                        graph.add_edge(port_node, box_node)
                    else:
                        graph.add_edge(box_node, port_node)
                        graph.add_edge(port_node, spider_node)
        graph.add_nodes_from(
            (Node("output", i=i, obj=obj), dict(i=i))
            for i, obj in enumerate(self.cod))
        graph.add_edges_from(
            (Node("spider", i=j, obj=obj), Node("output", i=i, obj=obj))
            for i, (j, obj) in enumerate(
                zip(self.wires[len(self.wires) - len(self.cod):], self.cod)))
        return graph

    def depth(self) -> int:
        """ The depth of a causal hypergraph. """
        return dag_longest_path_length(self.make_causal().to_graph()) // 4

    def spring_layout(self, seed=None, k=None):
        """ Computes a layout using a force-directed algorithm. """
        if seed is not None:
            random.seed(seed)
        graph, pos = self.to_graph().to_undirected(), {}
        height = len(self.boxes) + self.n_spiders
        width = max(len(self.dom), len(self.cod))
        for i, obj in enumerate(self.dom):
            pos[Node("input", i=i, obj=obj)] = (i, height)
        for i, (dom_wires, cod_wires) in enumerate(self.box_wires):
            box_node = Node("box", i=i, box=self.boxes[i])
            pos[box_node] = (
                random.uniform(-width / 2, width / 2),
                random.uniform(0, height))
            for kind, wires in [("dom", dom_wires), ("cod", cod_wires)]:
                for j, spider in enumerate(wires):
                    pos[Node(kind, i=i, j=j)] = pos[box_node]
        for i, obj in enumerate(self.spider_types):
            pos[Node("spider", i=i, obj=obj)] = (
                random.uniform(-width / 2, width / 2),
                random.uniform(0, height))
        for i, obj in enumerate(self.cod):
            pos[Node("output", i=i, obj=obj)] = (i, 0)
        fixed = self.ports[:len(self.dom)] + self.ports[
            len(self.wires) - len(self.cod):] or None
        pos = spring_layout(graph, pos=pos, fixed=fixed, k=k, seed=seed)
        return graph, pos

    def draw(self, seed=None, k=.25, path=None):
        """
        Draw a hypegraph using a force-based layout algorithm.

        Examples
        --------
        >>> from discopy.frobenius import Ty, Box, Hypergraph as H
        >>> x, y, z = map(Ty, "xyz")
        >>> f = Box('f', x, y @ z).to_hypergraph()
        >>> f.draw(
        ...     path='docs/_static/hypergraph/box.png', seed=42)

        .. image:: /_static/hypergraph/box.png
            :align: center

        >>> (H.spiders(2, 2, x) >> f @ x).draw(
        ...     path='docs/_static/hypergraph/diagram.png', seed=42)

        .. image:: /_static/hypergraph/diagram.png
            :align: center
        """
        graph, pos = self.spring_layout(seed=seed, k=k)
        for i, (box, (dom_wires, cod_wires)) in enumerate(
                zip(self.boxes, self.box_wires)):
            box_node = Node("box", i=i, box=box)
            for kind, wires in [("dom", dom_wires), ("cod", cod_wires)]:
                for j, spider in enumerate(wires):
                    port_node = Node(kind, i=i, j=j)
                    x, y = pos[box_node]
                    if not getattr(box, "draw_as_spider", False):
                        y += .25 if kind == "dom" else -.25
                        x -= .25 * (len(wires[:-1]) / 2 - j)
                    pos[port_node] = x, y
        labels = {
            node: self.spider_types[node.i] if node.kind == "spider"
            else self.boxes[node.i].name if node.kind == "box" else ""
            for node in graph.nodes}
        nodelist = list(graph.nodes)
        node_size = [
            300 if node.kind in ["spider", "box"] else 0 for node in nodelist]
        draw_networkx(
            graph, pos=pos, labels=labels,
            nodelist=nodelist, node_size=node_size,
            node_color="white", edgecolors="black")
        if path is not None:
            plt.savefig(path)
            plt.close()
        plt.show()
