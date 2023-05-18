import functools as ft
from typing import Any, Callable

import equinox as eqx
import jax
import jax.numpy as jnp
import jax.tree_util as jtu
from equinox.internal import ω
from jax import lax
from jaxtyping import Array, ArrayLike, Bool, PyTree

from ..custom_types import Scalar
from ..line_search import AbstractDescent, AbstractProxyDescent, OneDimensionalFunction
from ..linear_operator import AbstractLinearOperator, IdentityLinearOperator
from ..minimise import AbstractMinimiser, minimise, MinimiseProblem
from ..misc import max_norm
from ..solution import RESULTS
from .nonlinear_cg_descent import hestenes_stiefel, NonlinearCGDescent


def _small(diffsize: Scalar) -> Bool[ArrayLike, " "]:
    # TODO(kidger): make a more careful choice here -- the existence of this
    # function is pretty ad-hoc.
    resolution = 10 ** (2 - jnp.finfo(diffsize.dtype).precision)
    return diffsize < resolution


def _diverged(rate: Scalar) -> Bool[ArrayLike, " "]:
    return jnp.invert(jnp.isfinite(rate))


def _converged(factor: Scalar, tol: Scalar) -> Bool[ArrayLike, " "]:
    return (factor > 0) & (factor < tol)


class GradOnlyState(eqx.Module):
    descent_state: PyTree
    vector: PyTree[Array]
    operator: AbstractLinearOperator
    diff: PyTree[Array]
    diffsize: Scalar
    diffsize_prev: Scalar
    result: RESULTS
    f_val: PyTree[Array]
    aux: Any
    step: Scalar


# note that this is GradOnly and not VecOnly. It doesn't make sense to use this
# in the least squares setting where `vector` is the residual vector.
class AbstractGradOnly(AbstractMinimiser):
    atol: float
    rtol: float
    line_search: AbstractMinimiser
    descent: AbstractDescent
    norm: Callable
    converged_tol: float

    def __init__(
        self,
        atol: float,
        rtol: float,
        line_search: AbstractMinimiser,
        descent: AbstractDescent,
        norm: Callable = max_norm,
        converged_tol: float = 1e-2,
    ):
        self.atol = atol
        self.rtol = rtol
        self.line_search = line_search
        self.descent = descent
        self.norm = norm
        self.converged_tol = converged_tol

    def init(
        self,
        problem: MinimiseProblem,
        y: PyTree[Array],
        args: Any,
        options: dict[str, Any],
    ):
        f0, aux_shape = jtu.tree_map(
            lambda x: jnp.full(x.shape, jnp.inf), jax.eval_shape(problem.fn, y, args)
        )
        y_shaped_empty = ω(y).call(jnp.zeros_like).ω
        operator = IdentityLinearOperator(jax.eval_shape(lambda: y))
        descent_state = self.descent.init_state(problem, y, y_shaped_empty, operator)
        return GradOnlyState(
            descent_state=descent_state,
            vector=y_shaped_empty,
            operator=operator,
            diff=jtu.tree_map(lambda x: jnp.full(x.shape, jnp.inf), y),
            diffsize=jnp.array(0.0),
            diffsize_prev=jnp.array(0.0),
            result=jnp.array(RESULTS.successful),
            f_val=f0,
            aux=aux_shape,
            step=jnp.array(0),
        )

    def step(
        self,
        problem: MinimiseProblem,
        y: PyTree[Array],
        args: Any,
        options: dict[str, Any],
        state: GradOnlyState,
    ):
        def main_pass(y, state):
            descent = eqx.Partial(
                self.descent,
                descent_state=state.descent_state,
                args=args,
                options=options,
            )
            problem_1d = MinimiseProblem(
                OneDimensionalFunction(problem, descent, y), has_aux=True
            )
            line_search_options = {
                "f0": state.f_val,
                "compute_f0": (state.step == 0),
                "vector": state.vector,
                "operator": state.operator,
            }

            if isinstance(self.descent, AbstractProxyDescent):
                line_search_options["predicted_reduction"] = ft.partial(
                    self.descent.predicted_reduction,
                    descent_state=state.descent_state,
                    args=args,
                    options={},
                )

            line_sol = minimise(
                problem_1d,
                self.line_search,
                jnp.array(2.0),
                args=args,
                options=line_search_options,
                max_steps=100,
            )
            (f_val, diff, new_aux, _) = line_sol.aux
            return f_val, diff, new_aux, line_sol.result

        def first_pass(y, state):
            return jnp.inf, ω(y).call(jnp.zeros_like).ω, state.aux, RESULTS.successful

        # this lax.cond allows us to avoid an extra compilation of f(y) in the init.
        f_val, diff, new_aux, result = lax.cond(
            state.step == 0, first_pass, main_pass, y, state
        )
        new_y = (ω(y) + ω(diff)).ω
        new_grad, _ = jax.jacrev(problem.fn, has_aux=problem.has_aux)(new_y, args)
        scale = (self.atol + self.rtol * ω(new_y).call(jnp.abs)).ω
        diffsize = self.norm((ω(diff) / ω(scale)).ω)
        descent_state = self.descent.update_state(
            state.descent_state, diff, new_grad, state.operator, {}
        )
        result = jnp.where(
            result == RESULTS.max_steps_reached, RESULTS.successful, result
        )
        new_state = GradOnlyState(
            descent_state=descent_state,
            vector=new_grad,
            operator=state.operator,
            diff=diff,
            diffsize=diffsize,
            diffsize_prev=state.diffsize,
            result=result,
            f_val=f_val,
            aux=new_aux,
            step=state.step + 1,
        )
        return new_y, new_state, new_aux

    def terminate(
        self,
        problem: MinimiseProblem,
        y: PyTree[Array],
        args: Any,
        options: dict[str, Any],
        state: GradOnlyState,
    ):
        at_least_two = state.step > 2
        rate = state.diffsize / state.diffsize_prev
        factor = state.diffsize * rate / (1 - rate)
        small = _small(state.diffsize)
        diverged = _diverged(rate)
        converged = _converged(factor, self.converged_tol)
        linsolve_fail = state.result != RESULTS.successful
        terminate = linsolve_fail | (at_least_two & (small | diverged | converged))
        result = jnp.where(diverged, RESULTS.nonlinear_divergence, RESULTS.successful)
        result = jnp.where(linsolve_fail, state.result, result)
        return terminate, result

    def buffers(self, state: GradOnlyState):
        return ()


class GradOnly(AbstractGradOnly):
    ...


class NonlinearCG(GradOnly):
    def __init__(
        self,
        atol: float,
        rtol: float,
        line_search: AbstractMinimiser,
        norm: Callable = max_norm,
        converged_tol: float = 1e-2,
        method: Callable = hestenes_stiefel,
    ):
        self.atol = atol
        self.rtol = rtol
        self.line_search = line_search
        self.norm = norm
        self.converged_tol = converged_tol
        self.descent = NonlinearCGDescent(method)
