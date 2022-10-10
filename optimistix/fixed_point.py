from typing import Callable, Dict, Optional, TypeVar, Union

import equinox as eqx
import jax
from jaxtyping import Array, PyTree

from .adjoint import AbstractAdjoint, ImplicitAdjoint
from .iterate import iterative_solve
from .results import RESULTS


_SolverState = TypeVar("_SolverState")


class FixedPointProblem(AbstractIterativeProblem):
  fn: Callable


class AbstractFixedPointSolver(AbstractIterativeSolver):
  pass


class FixedPointSolution(eqx.Module):
  fixed_point: Array
  result: RESULTS
  state: _SolverState
  stats: Dict[str, Array]


def _fixed_point(root, _, inputs, __):
  fixed_point_prob, args = inputs
  del inputs
  return fixed_point_prob.fn(root, args) - root


class _ToRootFn(eqx.Module):
  fixed_point_fn: Callable

  def __call__(self, y, args):
    return self.fixed_point_fn(y, args) - y


@eqx.filter_jit
def fixed_point_solve(
    fixed_point_fn: Union[Callable, FixedPointProblem],
    solver: Union[AbstractFixedPointSolver, AbstractRootFindSolver],
    y0: PyTree[Array],
    args: PyTree = None,
    options: Optional[Dict[str, Any]] = None,
    *,
    max_steps: Optional[int] = 16,
    adjoint: AbstractAdjoint = ImplicitAdjoint()
    throw: bool = True,
):
  if isinstance(fixed_point_fn, FixedPointProblem):
    fixed_point_prob = fixed_point_fn
  else:
    fixed_point_prob = FixedPointProblem(fixed_point_fn)
  del fixed_point_fn

  if jax.eval_shape(lambda: y0) != jax.eval_shape(fixed_point_prob.fn, y0, args):
    raise ValueError("The input and output of `fixed_point_fn` must have the same structure")

  if isinstance(solver, AbstractRootFindSolver):
    root_fn = _ToRootFn(fixed_point_prob.fn)
    sol = root_find_solve(root_fn, solver, y0, args, options, max_steps=max_steps, adjoint=adjoint, throw=throw)
    return FixedPointSolution(fixed_point=sol.root, result=sol.result, state=sol.state, stats=sol.stats)
  else:
    fixed_point, result, state, stats = iterative_solve(fixed_point_prob, solver, y0, args, options, rewrite_fn=_fixed_point, max_steps=max_steps, adjoint=adjoint, throw=throw)
    return FixedPointSolution(fixed_point=fixed_point, result=result, state=state, stats=stats)

