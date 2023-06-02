from typing import Any, FrozenSet, Optional, TypeVar

import equinox as eqx
import jax
import jax.numpy as jnp
import jax.tree_util as jtu
from jaxtyping import Array, PyTree

from ._adjoint import AbstractAdjoint, ImplicitAdjoint
from ._iterate import AbstractIterativeProblem, AbstractIterativeSolver, iterative_solve
from ._solution import Solution


_SolverState = TypeVar("_SolverState")

MinimiseFunction = TypeVar("MinimiseFunction")


class MinimiseProblem(AbstractIterativeProblem[MinimiseFunction]):
    tags: FrozenSet[object] = frozenset()


class AbstractMinimiser(AbstractIterativeSolver):
    pass


def _minimum(optimum, _, inputs):
    minimise_prob, args, *_ = inputs
    del inputs

    def min_no_aux(x):
        if minimise_prob.has_aux:
            out, _ = minimise_prob.fn(x, args)
        else:
            out = minimise_prob.fn(x, args)
        return out

    return jax.grad(min_no_aux)(optimum)


@eqx.filter_jit
def minimise(
    problem: MinimiseProblem,
    solver: AbstractMinimiser,
    y0: PyTree[Array],
    args: PyTree = None,
    options: Optional[dict[str, Any]] = None,
    *,
    max_steps: Optional[int] = 256,
    adjoint: AbstractAdjoint = ImplicitAdjoint(),
    throw: bool = True,
) -> Solution:
    y0 = jtu.tree_map(jnp.asarray, y0)
    struct = jax.eval_shape(lambda: problem.fn(y0, args))
    if problem.has_aux:
        struct, aux_struct = struct
    else:
        aux_struct = None

    if not (isinstance(struct, jax.ShapeDtypeStruct) and struct.shape == ()):
        raise ValueError("minimisation function must output a single scalar.")

    return iterative_solve(
        problem,
        solver,
        y0,
        args,
        options,
        rewrite_fn=_minimum,
        max_steps=max_steps,
        adjoint=adjoint,
        throw=throw,
        tags=problem.tags,
        aux_struct=aux_struct,
        f_struct=struct,
    )
