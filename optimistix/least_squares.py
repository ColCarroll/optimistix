from typing import Any, Callable, Dict, FrozenSet, Optional, TypeVar, Union

import equinox as eqx
import jax
import jax.numpy as jnp
from jaxtyping import Array, PyTree

from .adjoint import AbstractAdjoint, ImplicitAdjoint
from .iterate import AbstractIterativeProblem, AbstractIterativeSolver, iterative_solve
from .minimise import AbstractMinimiser, minimise, MinimiseProblem
from .solution import Solution


_SolverState = TypeVar("_SolverState")


class LeastSquaresProblem(AbstractIterativeProblem):
    tags: FrozenSet[object] = frozenset()


class AbstractLeastSquaresSolver(AbstractIterativeSolver):
    pass


def _residual(optimum, _, inputs, __):
    residual_prob, args = inputs
    del inputs

    def objective(_optimum):
        return jnp.sum(residual_prob.fn(_optimum, args) ** 2)

    return jax.grad(objective)(optimum)


class _ToMinimiseFn(eqx.Module):
    residual_fn: Callable
    has_aux: bool

    def __call__(self, y, args):
        out = self.residual_fn(y, args)
        if self.has_aux:
            out, aux = out
            return jnp.sum(out**2), aux
        else:
            return jnp.sum(out**2)


@eqx.filter_jit
def least_squares(
    problem: LeastSquaresProblem,
    solver: Union[AbstractLeastSquaresSolver, AbstractMinimiser],
    y0: PyTree[Array],
    args: PyTree = None,
    options: Optional[Dict[str, Any]] = None,
    *,
    max_steps: Optional[int] = 256,
    adjoint: AbstractAdjoint = ImplicitAdjoint(),
    throw: bool = True,
) -> Solution:
    if isinstance(solver, AbstractMinimiser):
        minimise_fn = _ToMinimiseFn(problem.fn, problem.has_aux)
        minimise_problem = MinimiseProblem(fn=minimise_fn, has_aux=problem.has_aux)
        return minimise(
            minimise_problem,
            solver,
            y0,
            args,
            options,
            max_steps=max_steps,
            adjoint=adjoint,
            throw=throw,
        )

    else:
        return iterative_solve(
            problem,
            solver,
            y0,
            args,
            options,
            rewrite_fn=_residual,
            max_steps=max_steps,
            adjoint=adjoint,
            throw=throw,
            tags=problem.tags,
        )
