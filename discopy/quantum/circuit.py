# -*- coding: utf-8 -*-

"""
Implements classical-quantum circuits.

Objects are :class:`BitsAndQubits` generated by two basic types
:code:`bit` and :code:`qubit`.

Arrows are diagrams generated by :class:`QuantumGate`, :class:`ClassicalGate`,
:class:`Discard`, :class:`Measure` and :class:`Encode`.

>>> from discopy.quantum.gates import Ket, CX, H, X, sqrt
>>> from discopy.grammar.pregroup import Word
>>> from discopy.rigid import Ty, Cup, Id
>>> s, n = Ty('s'), Ty('n')
>>> Alice = Word('Alice', n)
>>> loves = Word('loves', n.r @ s @ n.l)
>>> Bob = Word('Bob', n)
>>> grammar = Cup(n, n.r) @ Id(s) @ Cup(n.l, n)
>>> sentence = grammar << Alice @ loves @ Bob
>>> ob = {s: 0, n: 1}
>>> ar = {Alice: Ket(0),
...       loves: CX << sqrt(2) @ H @ X << Ket(0, 0),
...       Bob: Ket(1)}
>>> F = CircuitFunctor(ob, ar)
>>> assert abs(F(sentence).eval().array) ** 2
>>> from discopy import drawing
>>> drawing.equation(
...     sentence, F(sentence), symbol='$\\\\mapsto$', figsize=(6, 3),
...     path='docs/_static/imgs/quantum/circuit-example.png')

.. image:: ../_static/imgs/quantum/circuit-example.png
    :align: center
"""

import random
from itertools import takewhile
from collections.abc import Mapping

from discopy import messages, monoidal, rigid, tensor
from discopy.cat import AxiomError
from discopy.rigid import Ob, Ty, Diagram
from discopy.tensor import np, Dim, Tensor
from math import pi
from functools import reduce, partial


def index2bitstring(i, length):
    """ Turns an index into a bitstring of a given length. """
    if i >= 2 ** length:
        raise ValueError("Index should be less than 2 ** length.")
    if not i and not length:
        return ()
    return tuple(map(int, '{{:0{}b}}'.format(length).format(i)))


def bitstring2index(bitstring):
    """ Turns a bitstring into an index. """
    return sum(value * 2 ** i for i, value in enumerate(bitstring[::-1]))


class BitsAndQubits(Ty):
    """
    Implements the objects of :class:`Circuit`, the free monoid on two objects
    :code:`bit` and :code:`qubit`.

    Examples
    --------
    >>> assert bit == BitsAndQubits("bit")
    >>> assert qubit == BitsAndQubits("qubit")
    >>> assert bit @ qubit != qubit @ bit

    You can construct :code:`n` qubits by taking powers of :code:`qubit`:

    >>> bit @ bit @ qubit @ qubit @ qubit
    bit ** 2 @ qubit ** 3
    """
    @staticmethod
    def upgrade(old):
        if not set(old.objects) <= {Ob('bit'), Ob('qubit')}:
            raise TypeError(messages.type_err(BitsAndQubits, old))
        return BitsAndQubits(*old.objects)

    def __repr__(self):
        if not self:
            return "qubit ** 0"
        n_bits = len(list(takewhile(lambda x: x.name == "bit", self.objects)))
        n_qubits = len(list(takewhile(
            lambda x: x.name == "qubit", self.objects[n_bits:])))
        remainder = self.objects[n_bits + n_qubits:]
        left = "" if not n_bits else "bit{}".format(
            " ** {}".format(n_bits) if n_bits > 1 else "")
        middle = "" if not n_qubits else "qubit{}".format(
            " ** {}".format(n_qubits) if n_qubits > 1 else "")
        right = "" if not remainder else repr(BitsAndQubits(*remainder))
        return " @ ".join(s for s in [left, middle, right] if s)

    def __str__(self):
        return repr(self)

    @property
    def l(self):
        return BitsAndQubits(*self.objects[::-1])

    r = l


bit, qubit = BitsAndQubits("bit"), BitsAndQubits("qubit")


@monoidal.Diagram.subclass
class Circuit(tensor.Diagram):
    """ Classical-quantum circuits. """
    def __repr__(self):
        return super().__repr__().replace('Diagram', 'Circuit')

    @property
    def is_mixed(self):
        """
        Whether the circuit is mixed, i.e. it contains both bits and qubits
        or it discards qubits. Mixed circuits can be evaluated only by a
        :class:`CQMapFunctor` not a :class:`discopy.tensor.Functor`.
        """
        both_bits_and_qubits = self.dom.count(bit) and self.dom.count(qubit)\
            or any(layer.cod.count(bit) and layer.cod.count(qubit)
                   for layer in self.layers)
        return both_bits_and_qubits or any(box.is_mixed for box in self.boxes)

    def init_and_discard(self):
        """ Returns a circuit with empty domain and only bits as codomain. """
        from discopy.quantum.gates import Bits, Ket
        circuit = self
        if circuit.dom:
            init = Id(0).tensor(*(
                Bits(0) if x.name == "bit" else Ket(0) for x in circuit.dom))
            circuit = init >> circuit
        if circuit.cod != bit ** len(circuit.cod):
            discards = Id(0).tensor(*(
                Discard() if x.name == "qubit"
                else Id(bit) for x in circuit.cod))
            circuit = circuit >> discards
        return circuit

    def eval(self, *others, backend=None, mixed=False, **params):
        """
        Evaluate a circuit on a backend, or simulate it with numpy.

        Parameters
        ----------
        others : :class:`discopy.quantum.circuit.Circuit`
            Other circuits to process in batch.
        backend : pytket.Backend, optional
            Backend on which to run the circuit, if none then we apply
            :class:`discopy.tensor.Functor` or :class:`CQMapFunctor` instead.
        mixed : bool, optional
            Whether to apply :class:`discopy.tensor.Functor`
            or :class:`CQMapFunctor`.
        params : kwargs, optional
            Get passed to Circuit.get_counts.

        Returns
        -------
        tensor : :class:`discopy.tensor.Tensor`
            If :code:`backend is not None` or :code:`mixed=False`.
        cqmap : :class:`CQMap`
            Otherwise.

        Examples
        --------
        We can evaluate a pure circuit (i.e. with :code:`not circuit.is_mixed`)
        as a unitary :class:`discopy.tensor.Tensor` or as a :class:`CQMap`:

        >>> from discopy.quantum import *

        >>> H.eval().round(2)
        Tensor(dom=Dim(2), cod=Dim(2), array=[0.71, 0.71, 0.71, -0.71])
        >>> H.eval(mixed=True).round(1)  # doctest: +ELLIPSIS
        CQMap(dom=Q(Dim(2)), cod=Q(Dim(2)), array=[0.5, ..., 0.5])

        We can evaluate a mixed circuit as a :class:`CQMap`:

        >>> assert Measure().eval()\\
        ...     == CQMap(dom=Q(Dim(2)), cod=C(Dim(2)),
        ...              array=[1, 0, 0, 0, 0, 0, 0, 1])
        >>> circuit = Bits(1, 0) @ Ket(0) >> Discard(bit ** 2 @ qubit)
        >>> assert circuit.eval() == CQMap(dom=CQ(), cod=CQ(), array=[1])

        We can execute any circuit on a `pytket.Backend`:

        >>> circuit = Ket(0, 0) >> sqrt(2) @ H @ X >> CX >> Measure() @ Bra(0)
        >>> from discopy.quantum.tk import mockBackend
        >>> backend = mockBackend({(0, 1): 512, (1, 0): 512})
        >>> assert circuit.eval(backend, n_shots=2**10).round()\\
        ...     == Tensor(dom=Dim(1), cod=Dim(2), array=[0., 1.])
        """
        from discopy import cqmap
        from discopy.quantum.gates import Bits, scalar, ClassicalGate
        if len(others) == 1 and not isinstance(others[0], Circuit):
            # This allows the syntax :code:`circuit.eval(backend)`
            return self.eval(backend=others[0], mixed=mixed, **params)
        if backend is None:
            if others:
                return [circuit.eval(mixed=mixed, **params)
                        for circuit in (self, ) + others]
            post_processes = Id(self.cod)
            for left, box, right in self.layers:
                if isinstance(box, ClassicalGate) and not box.is_linear:
                    if left.count(bit) or right.count(bit):
                        raise AxiomError("You can't tensor non-linear gates.")
                    post_processes = (post_processes or Id(box.dom)) >> box
                elif post_processes and not isinstance(box, ClassicalGate):
                    raise AxiomError("You can't do anything quantum "
                                     "after non-linear gates.")
            circuit = self[:-len(post_processes) or len(self)]
            functor = cqmap.Functor() if mixed or self.is_mixed\
                else tensor.Functor(lambda x: 2, lambda f: f.array)
            result = functor(circuit)
            for process in post_processes.boxes:
                result = process(result)
            return result
        circuits = [circuit.to_tk() for circuit in (self, ) + others]
        results, counts = [], circuits[0].get_counts(
            *circuits[1:], backend=backend, **params)
        for i, circuit in enumerate(circuits):
            n_bits = len(circuit.post_processing.dom)
            result = Tensor.zeros(Dim(1), Dim(*(n_bits * (2, ))))
            for bitstring, count in counts[i].items():
                result += (scalar(count) @ Bits(*bitstring)).eval()
            for process in circuit.post_processing.boxes:
                result = process(result)
            results.append(result)
        return results if len(results) > 1 else results[0]

    def get_counts(self, *others, backend=None, **params):
        """
        Get counts from a backend, or simulate them with numpy.

        Parameters
        ----------
        others : :class:`discopy.quantum.circuit.Circuit`
            Other circuits to process in batch.
        backend : pytket.Backend, optional
            Backend on which to run the circuit, if none then `numpy`.
        n_shots : int, optional
            Number of shots, default is :code:`2**10`.
        measure_all : bool, optional
            Whether to measure all qubits, default is :code:`False`.
        normalize : bool, optional
            Whether to normalize the counts, default is :code:`True`.
        post_select : bool, optional
            Whether to perform post-selection, default is :code:`True`.
        scale : bool, optional
            Whether to scale the output, default is :code:`True`.
        seed : int, optional
            Seed to feed the backend, default is :code:`None`.
        compilation : callable, optional
            Compilation function to apply before getting counts.

        Returns
        -------
        counts : dict
            From bitstrings to counts.

        Examples
        --------
        >>> from discopy.quantum import *
        >>> circuit = H @ X >> CX >> Measure(2)
        >>> from discopy.quantum.tk import mockBackend
        >>> backend = mockBackend({(0, 1): 512, (1, 0): 512})
        >>> circuit.get_counts(backend, n_shots=2**10)
        {(0, 1): 0.5, (1, 0): 0.5}
        """
        if len(others) == 1 and not isinstance(others[0], Circuit):
            # This allows the syntax :code:`circuit.get_counts(backend)`
            return self.get_counts(backend=others[0], **params)
        if backend is None:
            if others:
                return [circuit.get_counts(**params)
                        for circuit in (self, ) + others]
            utensor, counts = self.init_and_discard().eval(), dict()
            for i in range(2**len(utensor.cod)):
                bits = index2bitstring(i, len(utensor.cod))
                if utensor.array[bits]:
                    counts[bits] = utensor.array[bits].real
            return counts
        counts = self.to_tk().get_counts(
            *(other.to_tk() for other in others), backend=backend, **params)
        return counts if len(counts) > 1 else counts[0]

    def measure(self, mixed=False):
        """
        Measures a circuit on the computational basis using :code:`numpy`.

        Parameters
        ----------
        mixed : bool, optional
            Whether to apply :class:`tensor.Functor` or :class:`CQMapFunctor`.

        Returns
        -------
        array : numpy.ndarray
        """
        from discopy.quantum.gates import Bra, Ket
        if mixed or self.is_mixed:
            return self.init_and_discard().eval(mixed=True).array.real
        state = (Ket(*(len(self.dom) * [0])) >> self).eval()
        effects = [Bra(*index2bitstring(j, len(self.cod))).eval()
                   for j in range(2 ** len(self.cod))]
        array = np.zeros(len(self.cod) * (2, ) or (1, ))
        for effect in effects:
            array += effect.array * np.absolute((state >> effect).array) ** 2
        return array

    def to_tk(self):
        """
        Export to t|ket>.

        Returns
        -------
        tk_circuit : pytket.Circuit
            A :class:`pytket.Circuit`.

        Note
        ----
        * No measurements are performed.
        * SWAP gates are treated as logical swaps.
        * If the circuit contains scalars or a :class:`Bra`,
          then :code:`tk_circuit` will hold attributes
          :code:`post_selection` and :code:`scalar`.

        Examples
        --------
        >>> from discopy.quantum import *

        >>> circuit0 = sqrt(2) @ H @ Rx(0.5) >> CX >> Measure() @ Discard()
        >>> circuit0.to_tk()
        tk.Circuit(2, 1).H(0).Rx(1.0, 1).CX(0, 1).Measure(0, 0).scale(2)

        >>> circuit1 = Ket(1, 0) >> CX >> Id(1) @ Ket(0) @ Id(1)
        >>> circuit1.to_tk()
        tk.Circuit(3).X(0).CX(0, 2)

        >>> circuit2 = X @ Id(2) >> Id(1) @ SWAP >> CX @ Id(1) >> Id(1) @ SWAP
        >>> circuit2.to_tk()
        tk.Circuit(3).X(0).CX(0, 2)

        >>> circuit3 = Ket(0, 0)\\
        ...     >> H @ Id(1)\\
        ...     >> Id(1) @ X\\
        ...     >> CX\\
        ...     >> Id(1) @ Bra(0)
        >>> print(repr(circuit3.to_tk()))
        tk.Circuit(2, 1).H(0).X(1).CX(0, 1).Measure(1, 0).post_select({0: 0})
        """
        # pylint: disable=import-outside-toplevel
        from discopy.quantum.tk import to_tk
        return to_tk(self)

    @staticmethod
    def from_tk(*tk_circuits):
        """
        Translates a :class:`pytket.Circuit` into a :class:`Circuit`, or
        a list of :class:`pytket` circuits into a :class:`Sum`.

        Parameters
        ----------
        tk_circuits : pytket.Circuit
            potentially with :code:`scalar` and
            :code:`post_selection` attributes.

        Returns
        -------
        circuit : :class:`Circuit`
            Such that :code:`Circuit.from_tk(circuit.to_tk()) == circuit`.

        Note
        ----
        * :meth:`Circuit.init_and_discard` is applied beforehand.
        * SWAP gates are introduced when applying gates to non-adjacent qubits.

        Examples
        --------
        >>> from discopy.quantum import *
        >>> import pytket as tk

        >>> c = Rz(0.5) @ Id(1) >> Id(1) @ Rx(0.25) >> CX
        >>> assert Circuit.from_tk(c.to_tk()) == c.init_and_discard()

        >>> tk_GHZ = tk.Circuit(3).H(1).CX(1, 2).CX(1, 0)
        >>> pprint = lambda c: print(str(c).replace(' >>', '\\n  >>'))
        >>> pprint(Circuit.from_tk(tk_GHZ))
        Ket(0)
          >> Id(1) @ Ket(0)
          >> Id(2) @ Ket(0)
          >> Id(1) @ H @ Id(1)
          >> Id(1) @ CX
          >> SWAP @ Id(1)
          >> CX @ Id(1)
          >> SWAP @ Id(1)
          >> Discard(qubit) @ Id(2)
          >> Discard(qubit) @ Id(1)
          >> Discard(qubit)
        >>> circuit = Ket(1, 0) >> CX >> Id(1) @ Ket(0) @ Id(1)
        >>> print(Circuit.from_tk(circuit.to_tk())[3:-3])
        X @ Id(2) >> Id(1) @ SWAP >> CX @ Id(1) >> Id(1) @ SWAP

        >>> bell_state = Circuit.caps(qubit, qubit)
        >>> bell_effect = bell_state[::-1]
        >>> circuit = bell_state @ Id(1) >> Id(1) @ bell_effect >> Bra(0)
        >>> pprint(Circuit.from_tk(circuit.to_tk())[3:])
        H @ Id(2)
          >> CX @ Id(1)
          >> Id(1) @ CX
          >> Id(1) @ H @ Id(1)
          >> Bra(0) @ Id(2)
          >> Bra(0) @ Id(1)
          >> Bra(0)
          >> scalar(4+0j)
        """
        # pylint: disable=import-outside-toplevel
        from discopy.quantum.tk import from_tk
        if not tk_circuits:
            return Sum([], qubit ** 0, qubit ** 0)
        if len(tk_circuits) == 1:
            return from_tk(tk_circuits[0])
        return sum(Circuit.from_tk(c) for c in tk_circuits)

    def grad(self, var):
        """
        Gradient with respect to `var`.

        Parameters
        ----------
        var : sympy.Symbol
            Differentiated variable.

        Returns
        -------
        circuits : `discopy.quantum.circuit.Sum`

        Examples
        --------
        >>> from sympy.abc import phi
        >>> from discopy.quantum import *
        >>> circuit = Rz(phi / 2) @ Rz(phi + 1) >> CX
        >>> assert circuit.grad(phi)\\
        ...     == (Rz(phi / 2) @ scalar(pi) @ Rz(phi + 1.5) >> CX)\\
        ...     + (scalar(pi/2) @ Rz(phi/2 + .5) @ Rz(phi + 1) >> CX)
        """
        return super().grad(var)

    def draw(self, **params):
        """ We draw the labels of a circuit whenever it's mixed. """
        draw_type_labels = params.get('draw_type_labels') or self.is_mixed
        return super().draw(**dict(params, draw_type_labels=draw_type_labels))

    @staticmethod
    def swap(left, right):
        return monoidal.Diagram.swap(
            left, right, ar_factory=Circuit, swap_factory=Swap)

    @staticmethod
    def permutation(perm, dom=None):
        if dom is None:
            dom = qubit ** len(perm)
        return monoidal.Diagram.permutation(perm, dom, ar_factory=Circuit)

    @staticmethod
    def cups(left, right):
        from discopy.quantum.gates import CX, H, sqrt, Bra, Match

        def cup_factory(left, right):
            if left == right == qubit:
                return CX >> H @ sqrt(2) @ Id(1) >> Bra(0, 0)
            if left == right == bit:
                return Match() >> Discard(bit)
            raise ValueError
        return rigid.cups(
            left, right, ar_factory=Circuit, cup_factory=cup_factory)

    @staticmethod
    def caps(left, right):
        return Circuit.cups(left, right).dagger()


class Id(rigid.Id, Circuit):
    """ Identity circuit. """
    def __init__(self, dom):
        if isinstance(dom, int):
            dom = qubit ** dom
        self._qubit_only = all(x.name == "qubit" for x in dom)
        rigid.Id.__init__(self, dom)
        Circuit.__init__(self, dom, dom, [], [])

    def __repr__(self):
        return "Id({})".format(len(self.dom) if self._qubit_only else self.dom)

    def __str__(self):
        return repr(self)


Circuit.id = Id


class Box(rigid.Box, Circuit):
    """
    Boxes in a circuit diagram.

    Parameters
    ----------
    name : any
    dom : BitsAndQubits
    cod : BitsAndQubits
    is_mixed : bool, optional
        Whether the box is mixed, default is :code:`True`.
    _dagger : bool, optional
        If set to :code:`None` then the box is self-adjoint.
    """
    def __init__(self, name, dom, cod,
                 is_mixed=True, data=None, _dagger=False):
        if dom and not isinstance(dom, BitsAndQubits):
            raise TypeError(messages.type_err(BitsAndQubits, dom))
        if cod and not isinstance(cod, BitsAndQubits):
            raise TypeError(messages.type_err(BitsAndQubits, cod))
        rigid.Box.__init__(self, name, dom, cod, data=data, _dagger=_dagger)
        Circuit.__init__(self, dom, cod, [self], [0])
        if not is_mixed:
            if all(x.name == "bit" for x in dom @ cod):
                self.classical = True
            elif all(x.name == "qubit" for x in dom @ cod):
                self.classical = False
            else:
                raise ValueError(
                    "dom and cod should be bits only or qubits only.")
        self._mixed = is_mixed

    def grad(self, var):
        if var not in self.free_symbols:
            return Sum([], self.dom, self.cod)
        raise NotImplementedError

    @property
    def is_mixed(self):
        return self._mixed

    def __repr__(self):
        return self.name


class Sum(monoidal.Sum, Box):
    """ Sums of circuits. """
    @staticmethod
    def upgrade(old):
        return Sum(old.terms, old.dom, old.cod)

    @property
    def is_mixed(self):
        return any(circuit.is_mixed for circuit in self.terms)

    def get_counts(self, backend=None, **params):
        if not self.terms:
            return {}
        if len(self.terms) == 1:
            return self.terms[0].get_counts(backend=backend, **params)
        counts = Circuit.get_counts(*self.terms, backend=backend, **params)
        result = {}
        for circuit_counts in counts:
            for bitstring, count in circuit_counts.items():
                result[bitstring] = result.get(bitstring, 0) + count
        return result

    def eval(self, backend=None, mixed=False, **params):
        if not self.terms:
            return 0
        if len(self.terms) == 1:
            return self.terms[0].eval(backend=backend, mixed=mixed, **params)
        return sum(
            Circuit.eval(*self.terms, backend=backend, mixed=mixed, **params))

    def grad(self, var):
        return sum(circuit.grad(var) for circuit in self.terms)

    def to_tk(self):
        return [circuit.to_tk() for circuit in self.terms]


Circuit.sum = Sum


class Swap(rigid.Swap, Box):
    """ Implements swaps of circuit wires. """
    def __init__(self, left, right):
        rigid.Swap.__init__(self, left, right)
        Box.__init__(
            self, self.name, self.dom, self.cod, is_mixed=left != right)

    def dagger(self):
        return Swap(self.right, self.left)

    def __repr__(self):
        return "SWAP"\
            if self.left == self.right == qubit else super().__repr__()

    def __str__(self):
        return repr(self)


class Discard(Box):
    """ Discard n qubits. If :code:`dom == bit` then marginal distribution. """
    def __init__(self, dom=1):
        if isinstance(dom, int):
            dom = qubit ** dom
        super().__init__(
            "Discard({})".format(dom), dom, qubit ** 0, is_mixed=True)
        self.drawing_name = "Discard"
        if dom == bit:
            self.drawing_name = ""
            self.draw_as_spider, self.color = True, "black"

    def dagger(self):
        return MixedState(self.dom)


class MixedState(Box):
    """
    Maximally-mixed state on n qubits.
    If :code:`cod == bit` then uniform distribution.
    """
    def __init__(self, cod=1):
        if isinstance(cod, int):
            cod = qubit ** cod
        super().__init__(
            "MixedState({})".format(cod), qubit ** 0, cod, is_mixed=True)
        self.drawing_name = "MixedState"
        if cod == bit:
            self.drawing_name = ""
            self.draw_as_spider, self.color = True, "black"

    def dagger(self):
        return Discard(self.cod)


class Measure(Box):
    """
    Measure n qubits into n bits.

    Parameters
    ----------
    n_qubits : int
        Number of qubits to measure.
    destructive : bool, optional
        Whether to do a non-destructive measurement instead.
    override_bits : bool, optional
        Whether to override input bits, this is the standard behaviour of tket.
    """
    def __init__(self, n_qubits=1, destructive=True, override_bits=False):
        dom, cod = qubit ** n_qubits, bit ** n_qubits
        name = "Measure({})".format("" if n_qubits == 1 else n_qubits)
        if not destructive:
            cod = qubit ** n_qubits @ cod
            name = name\
                .replace("()", "(1)").replace(')', ", destructive=False)")
        if override_bits:
            dom = dom @ bit ** n_qubits
            name = name\
                .replace("()", "(1)").replace(')', ", override_bits=True)")
        super().__init__(name, dom, cod, is_mixed=True)
        self.destructive, self.override_bits = destructive, override_bits
        self.n_qubits = n_qubits
        self.drawing_name = "Measure"

    def dagger(self):
        return Encode(self.n_qubits,
                      constructive=self.destructive,
                      reset_bits=self.override_bits)


class Encode(Box):
    """
    Controlled preparation, i.e. encode n bits into n qubits.

    Parameters
    ----------
    n_bits : int
        Number of bits to encode.
    constructive : bool, optional
        Whether to do a classically-controlled correction instead.
    reset_bits : bool, optional
        Whether to reset the bits to the uniform distribution.
    """
    def __init__(self, n_bits=1, constructive=True, reset_bits=False):
        dom, cod = bit ** n_bits, qubit ** n_bits
        name = Measure(n_bits, constructive, reset_bits).name\
            .replace("Measure", "Encode")\
            .replace("destructive", "constructive")\
            .replace("override_bits", "reset_bits")
        super().__init__(name, dom, cod, is_mixed=True)
        self.constructive, self.reset_bits = constructive, reset_bits
        self.n_bits = n_bits

    def dagger(self):
        return Measure(self.n_bits,
                       destructive=self.constructive,
                       override_bits=self.reset_bits)


class CircuitFunctor(rigid.Functor):
    """ Functors into :class:`Circuit`. """
    def __init__(self, ob, ar):
        if isinstance(ob, Mapping):
            ob = {x: qubit ** y if isinstance(y, int) else y
                  for x, y in ob.items()}
        super().__init__(ob, ar, ob_factory=BitsAndQubits, ar_factory=Circuit)

    def __repr__(self):
        return super().__repr__().replace("Functor", "CircuitFunctor")


class IQPansatz(Circuit):
    """
    Builds an IQP ansatz on n qubits, if n = 1 returns an Euler decomposition

    >>> pprint = lambda c: print(str(c).replace(' >>', '\\n  >>'))
    >>> pprint(IQPansatz(3, [[0.1, 0.2], [0.3, 0.4]]))
    H @ Id(2)
      >> Id(1) @ H @ Id(1)
      >> Id(2) @ H
      >> CRz(0.1) @ Id(1)
      >> Id(1) @ CRz(0.2)
      >> H @ Id(2)
      >> Id(1) @ H @ Id(1)
      >> Id(2) @ H
      >> CRz(0.3) @ Id(1)
      >> Id(1) @ CRz(0.4)
    >>> print(IQPansatz(1, [0.3, 0.8, 0.4]))
    Rx(0.3) >> Rz(0.8) >> Rx(0.4)
    """
    def __init__(self, n_qubits, params):
        from discopy.quantum.gates import H, Rx, Rz, CRz

        def layer(thetas):
            hadamards = Id(0).tensor(*(n_qubits * [H]))
            rotations = Id(n_qubits).then(*(
                Id(i) @ CRz(thetas[i]) @ Id(n_qubits - 2 - i)
                for i in range(n_qubits - 1)))
            return hadamards >> rotations
        if n_qubits == 1:
            circuit = Rx(params[0]) >> Rz(params[1]) >> Rx(params[2])
        elif len(np.shape(params)) != 2 or np.shape(params)[1] != n_qubits - 1:
            raise ValueError(
                "Expected params of shape (depth, {})".format(n_qubits - 1))
        else:
            depth = np.shape(params)[0]
            circuit = Id(n_qubits).then(*(
                layer(params[i]) for i in range(depth)))
        super().__init__(
            circuit.dom, circuit.cod, circuit.boxes, circuit.offsets)


def real_amp_ansatz(params: np.ndarray, *, entanglement='linear'): # TODO default full
    """
    The real-amplitudes 2-local circuit. The shape of the params determines the number of
    layers and the number of qubits respectively (layers, qubit).
    This heuristic generates orthogonal operators so the imaginary part of the correponding
    matrix is always the zero matrix.
    """
    # TODO Reverse shape, columns qubits, rows, layers
    from discopy.quantum.gates import CX, Ry, rewire
    ext_cx = partial(rewire, CX)
    assert entanglement in ('linear', 'circular')   # TODO full
    params = np.asarray(params)
    assert params.ndim == 2
    dom = qubit**params.shape[1]

    def layer(v, is_last=False):
        n = len(dom)
        rys = Id(0).tensor(*(Ry(v[k]) for k in range(n)))
        if is_last:
            return rys
        cxs = [ext_cx(k, k+1, dom=dom) for k in range(n-1)]
        cxs = reduce(lambda a, b: a >> b, cxs)
        if entanglement == 'circular':
            # TODO Note that if len(dom) == 2 => this can be simplified...
            cxs = ext_cx(n-1, 0, dom=dom) >> cxs
        return rys >> cxs

    circuit = [layer(v, is_last=idx==(len(params) - 1)) for idx, v in enumerate(params)]
    circuit = reduce(lambda a, b: a >> b, circuit)
    return circuit


def random_tiling(n_qubits, depth=3, gateset=None, seed=None):
    """ Returns a random Euler decomposition if n_qubits == 1,
    otherwise returns a random tiling with the given depth and gateset.

    >>> from discopy.quantum.gates import CX, H, T, Rx, Rz
    >>> c = random_tiling(1, seed=420)
    >>> print(c)
    Rx(0.0263) >> Rz(0.781) >> Rx(0.273)
    >>> print(random_tiling(2, 2, gateset=[CX, H, T], seed=420))
    CX >> T @ Id(1) >> Id(1) @ T
    >>> print(random_tiling(3, 2, gateset=[CX, H, T], seed=420))
    CX @ Id(1) >> Id(2) @ T >> H @ Id(2) >> Id(1) @ H @ Id(1) >> Id(2) @ H
    >>> print(random_tiling(2, 1, gateset=[Rz, Rx], seed=420))
    Rz(0.673) @ Id(1) >> Id(1) @ Rx(0.273)
    """
    from discopy.quantum.gates import H, CX, Rx, Rz, Parametrized
    gateset = gateset or [H, Rx, CX]
    if seed is not None:
        random.seed(seed)
    if n_qubits == 1:
        phases = [random.random() for _ in range(3)]
        return Rx(phases[0]) >> Rz(phases[1]) >> Rx(phases[2])
    result = Id(n_qubits)
    for _ in range(depth):
        line, n_affected = Id(0), 0
        while n_affected < n_qubits:
            gate = random.choice(
                gateset if n_qubits - n_affected > 1 else [
                    g for g in gateset
                    if g is Rx or g is Rz or len(g.dom) == 1])
            if isinstance(gate, type) and issubclass(gate, Parametrized):
                gate = gate(random.random())
            line = line @ gate
            n_affected += len(gate.dom)
        result = result >> line
    return result
