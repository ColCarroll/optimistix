import abc
from typing import Dict, TypeVar

import equinox as eqx
from jaxtyping import PyTree, Array

from .adjoint import AbstractAdjoint, ImplicitAdjoint
from .results import RESULTS


_SolverState = TypeVar("_SolverState")


class AbstractRootFindSolver(eqx.Module):
  @abc.abstractmethod
  def init(self, root_fn: Callable, y: PyTree[Array], args: PyTree, options: Dict[str, Any]) -> _SolverState:
    ...

  @abc.abstractmethod
  def step(self, root_fn: Callable, y: PyTree[Array], args: PyTree, state: _SolverState) -> Tuple[PyTree[Array], _SolverState]:
    ...

  @abc.abstractmethod
  def terminate(self, root_fn: Callable, y: PyTree[Array], args: PyTree, state: _SolverState) -> bool:
    ...


class RootFindSolution(eqx.Module):
  root: Array
  result: RESULTS
  state: _SolverState
  stats: Dict[str, Array]


def _solve(inputs, closure, reverse_autodiffable):
  fn, y0, args = inputs
  solver, options, max_steps = closure
  del inputs, closure

  init_state = solver.init(fn, y0, args, options)
  init_val = (y0, 0, init_state)

  def cond_fun(carry):
    y, _, state = carry
    terminate, _ = solver.terminate(y, args, state)
    return jnp.invert(terminate)

  def body_fun(carry, _):
    y, num_steps, state = carry
    new_y, new_state = solver.step(y, args, state)
    return new_y, num_steps + 1, new_state

  if reverse_autodiffable:
    final_val = bounded_while_loop(cond_fun, body_fun, init_val, max_steps, base=4)
  else:
    if max_steps is None:
      _cond_fun = cond_fun
    else:
      def _cond_fun(carry):
        _, num_steps, _ = carry
        return cond_fun(carry) & (num_steps < max_steps)
    final_val = bounded_while_loop(cond_fun, body_fun, init_val, max_steps=None)

  out, num_steps, final_state = final_val
  terminate, result = solver.terminate(final_y, args, final_state)
  result = jnp.where(result == RESULTS.successful, jnp.where(terminate, RESULTS.successful, RESULTS.max_steps_reached), result)
  return out, (num_steps, result)


def _root(root, _, inputs, __):
  root_fn, _, args = inputs
  del inputs
  return root_fn(root, args)


def root_find_solve(
    root_fn: Callable
    solver: AbstractRootFindSolver,
    y0: PyTree[Array],
    args: PyTree = None,
    options: Optional[Dict[str, Any]] = None,
    *,
    max_steps: Optional[int] = 16,
    adjoint: AbstractAdjoint = ImplicitAdjoint()
    throw: bool = True,
):

  inputs = root_fn, y0, args
  closure = solver, options, max_steps
  root, (num_steps, residual) = adjoint.apply(_solve, _root, inputs, closure)

  error_index = unvmap_max(result)
  branched_error_if(
    throw & (results != RESULTS.successful),
    error_index,
    RESULTS.reverse_lookup
  )
  stats = {"num_steps": num_steps, "max_steps": max_steps}
  return RootFindSolution(root=root, result=result, state=final_state, stats=stats)

