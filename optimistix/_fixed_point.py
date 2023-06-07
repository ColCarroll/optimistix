from typing import Any, Callable, Optional, Union

import equinox as eqx
import jax
from equinox.internal import ω
from jaxtyping import PyTree

from ._adjoint import AbstractAdjoint, ImplicitAdjoint
from ._custom_types import Aux, Fn, SolverState, Y
from ._iterate import AbstractIterativeSolver, iterative_solve
from ._misc import NoneAux
from ._root_find import AbstractRootFinder
from ._solution import Solution


class AbstractFixedPointSolver(AbstractIterativeSolver[SolverState, Y, Y, Aux]):
    pass


def _fixed_point(root, _, inputs):
    fixed_point_fn, args, *_ = inputs
    del inputs
    f_val, _ = fixed_point_fn(root, args)
    return (f_val**ω - root**ω).ω


def _root(root, _, inputs):
    root_fn, args, *_ = inputs
    del inputs
    out, _ = root_fn(root, args)
    return out


class _ToRootFn(eqx.Module):
    fixed_point_fn: Callable

    def __call__(self, y, args):
        out, aux = self.fixed_point_fn(y, args)
        return out - y, aux


@eqx.filter_jit
def fixed_point(
    fn: Fn[Y, Y, Aux],
    solver: Union[AbstractFixedPointSolver, AbstractRootFinder],
    y0: Y,
    args: PyTree = None,
    options: Optional[dict[str, Any]] = None,
    *,
    has_aux: bool = False,
    max_steps: Optional[int] = 256,
    adjoint: AbstractAdjoint = ImplicitAdjoint(),
    throw: bool = True,
    tags: frozenset[object] = frozenset()
) -> Solution:

    if not has_aux:
        fn = NoneAux(fn)

    f_struct, aux_struct = jax.eval_shape(lambda: fn(y0, args))

    if jax.eval_shape(lambda: y0) != f_struct:
        raise ValueError(
            "The input and output of `fixed_point_fn` must have the same structure"
        )

    if isinstance(solver, AbstractRootFinder):
        root_fn = _ToRootFn(fn)
        return iterative_solve(
            root_fn,
            solver,
            y0,
            args,
            options,
            rewrite_fn=_root,
            max_steps=max_steps,
            adjoint=adjoint,
            throw=throw,
            tags=tags,
            aux_struct=aux_struct,
            f_struct=f_struct,
        )
    else:
        return iterative_solve(
            fn,
            solver,
            y0,
            args,
            options,
            rewrite_fn=_fixed_point,
            max_steps=max_steps,
            adjoint=adjoint,
            throw=throw,
            tags=tags,
            aux_struct=aux_struct,
            f_struct=f_struct,
        )
