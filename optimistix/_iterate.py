import abc
from typing import Any, Callable, FrozenSet, Generic, Optional, Tuple, TypeVar

import equinox as eqx
import equinox.internal as eqxi
import jax
import jax.numpy as jnp
import jax.tree_util as jtu
from jaxtyping import Array, PyTree

from ._adjoint import AbstractAdjoint
from ._custom_types import sentinel
from ._misc import NoneAux
from ._solution import RESULTS, Solution


class _AuxError:
    def __bool__(self):
        raise ValueError("")


aux_error = _AuxError()

_SolverState = TypeVar("_SolverState")
_Aux = TypeVar("_Aux")


def _is_jaxpr(x):
    return isinstance(x, (jax.core.Jaxpr, jax.core.ClosedJaxpr))


def _is_array_or_jaxpr(x):
    return _is_jaxpr(x) or eqx.is_array(x)


_F = TypeVar("_F")


class AbstractIterativeProblem(eqx.Module, Generic[_F]):
    fn: _F
    has_aux: bool = False


class AbstractIterativeSolver(eqx.Module):
    @abc.abstractmethod
    def init(
        self,
        problem: AbstractIterativeProblem,
        y: PyTree[Array],
        args: PyTree,
        options: dict[str, Any],
        f_struct: PyTree[jax.ShapeDtypeStruct],
        aux_struct: PyTree[jax.ShapeDtypeStruct],
    ) -> _SolverState:
        ...

    @abc.abstractmethod
    def step(
        self,
        problem: AbstractIterativeProblem,
        y: PyTree[Array],
        args: PyTree,
        options: dict[str, Any],
        state: _SolverState,
    ) -> Tuple[PyTree[Array], _SolverState, _Aux]:
        ...

    @abc.abstractmethod
    def terminate(
        self,
        problem: AbstractIterativeProblem,
        y: PyTree[Array],
        args: PyTree,
        options: dict[str, Any],
        state: _SolverState,
    ) -> bool:
        ...

    @abc.abstractmethod
    def buffers(
        self,
        state: _SolverState,
    ) -> Callable:
        ...


def _zero(x):
    if isinstance(x, jax.ShapeDtypeStruct):
        return jnp.zeros(x.shape, dtype=x.dtype)
    else:
        return x


def _iterate(inputs, while_loop):
    problem, args, solver, y0, options, max_steps, f_struct, aux_struct = inputs
    del inputs
    static_leaf = lambda x: isinstance(x, eqxi.Static)
    f_struct = jtu.tree_map(lambda x: x.value, f_struct, is_leaf=static_leaf)
    aux_struct = jtu.tree_map(lambda x: x.value, aux_struct, is_leaf=static_leaf)

    if options is None:
        options = {}

    if not problem.has_aux:
        problem = eqx.tree_at(
            lambda p: (p.fn, p.has_aux), problem, (NoneAux(problem.fn), aux_error)
        )

    if aux_struct is sentinel:
        assert f_struct is sentinel
        f_struct, aux_struct = eqx.filter_eval_shape(problem.fn, y0, args)
    else:
        assert f_struct is not sentinel

    init_aux = jtu.tree_map(_zero, aux_struct)
    init_state = solver.init(problem, y0, args, options, f_struct, aux_struct)
    dynamic_init_state, static_state = eqx.partition(init_state, eqx.is_array)

    init_carry = (y0, 0, dynamic_init_state, init_aux)

    def cond_fun(carry):
        y, _, dynamic_state, _ = carry
        state = eqx.combine(static_state, dynamic_state)
        terminate, _ = solver.terminate(problem, y, args, options, state)
        return jnp.invert(terminate)

    def body_fun(carry):
        y, num_steps, dynamic_state, _ = carry
        state = eqx.combine(static_state, dynamic_state)
        new_y, new_state, aux = solver.step(problem, y, args, options, state)
        new_dynamic_state, new_static_state = eqx.partition(new_state, eqx.is_array)

        new_static_state_no_jaxpr = eqx.filter(
            new_static_state, _is_jaxpr, inverse=True
        )
        static_state_no_jaxpr = eqx.filter(state, _is_array_or_jaxpr, inverse=True)
        assert eqx.tree_equal(static_state_no_jaxpr, new_static_state_no_jaxpr) is True
        return new_y, num_steps + 1, new_dynamic_state, aux

    def buffers(carry):
        _, _, state, _ = carry
        return solver.buffers(state)

    final_carry = while_loop(
        cond_fun, body_fun, init_carry, max_steps=max_steps, buffers=buffers
    )

    final_y, num_steps, final_state, aux = final_carry
    _final_state = eqx.combine(static_state, final_state)
    terminate, result = solver.terminate(problem, final_y, args, options, _final_state)
    result = RESULTS.where(
        (result == RESULTS.successful) & jnp.invert(terminate),
        RESULTS.max_steps_reached,
        result,
    )
    return final_y, (num_steps, result, final_state, aux)


def iterative_solve(
    problem: AbstractIterativeProblem,
    solver: AbstractIterativeSolver,
    y0: PyTree[Array],
    args: PyTree = None,
    options: Optional[dict[str, Any]] = None,
    *,
    rewrite_fn: Callable,
    max_steps: Optional[int],
    adjoint: AbstractAdjoint,
    throw: bool,
    tags: FrozenSet[object],
    f_struct: PyTree[jax.ShapeDtypeStruct] = sentinel,
    aux_struct: PyTree[jax.ShapeDtypeStruct] = sentinel,
) -> Solution:
    f_struct = jtu.tree_map(eqxi.Static, f_struct)
    aux_struct = jtu.tree_map(eqxi.Static, aux_struct)
    inputs = problem, args, solver, y0, options, max_steps, f_struct, aux_struct
    out, (num_steps, result, final_state, aux) = adjoint.apply(
        _iterate, rewrite_fn, inputs, tags
    )
    stats = {"num_steps": num_steps, "max_steps": max_steps}
    sol = Solution(value=out, result=result, state=final_state, aux=aux, stats=stats)
    if throw:
        sol = result.error_if(sol, result != RESULTS.successful)
    return sol
