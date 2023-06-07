from typing import (
    Any,
    Callable,
    cast,
    Optional,
)

import equinox as eqx
import jax
import jax.lax as lax
import jax.numpy as jnp
import jax.tree_util as jtu
import lineax as lx
from equinox.internal import ω
from jaxtyping import Array, Bool, Float, Int, PyTree, Scalar

from .._custom_types import Aux, Fn, Out, sentinel, Y
from .._line_search import AbstractDescent
from .._misc import tree_where, two_norm
from .._root_find import AbstractRootFinder, root_find
from .._solution import RESULTS
from .misc import quadratic_predicted_reduction


#
# NOTE: This method is usually called Levenberg-Marquardt. However,
# Levenberg-Marquard often refers specifically to the case where this approach
# is applied in the Gauss-Newton setting. For this reason, we refer to the approach
# by the more generic name "iterative dual."
#
# Iterative dual is a method of solving for the descent direction given a
# trust region radius. It does this by solving the dual problem
# `(B + lambda I) p = r` for `p`, where `B` is the quasi-Newton matrix,
# lambda is the dual parameter (the dual parameterisation of the
# trust region radius), `I` is the identity, and `r` is the vector of residuals.
#
# Iterative dual is approached in one of two ways:
# 1. set the trust region radius and find the Levenberg-Marquadt parameter
# `lambda` which will give approximately solve the trust region problem. ie.
# solve the dual of the trust region problem.
#
# 2. set the Levenberg-Marquadt parameter `lambda` directly.
#
# Respectively, this is the indirect and direct approach to iterative dual.
# The direct approach is common in practice, and is often interpreted as
# interpolating between quasi-Newton and gradient based approaches.
#
# The indirect approach is very interpretable in the classical trust region sense.
# Note however, if `B` is the quasi-Newton Hessian approximation and `g` the
# gradient, that `||p(lambda)||` is dependent upon `B`. Specifically, decompose
# `B = QLQ^T` via the spectral decomposition with eigenvectors $q_i$ and corresponding
# eigenvalues $l_i$. Then
# ```
# ||p(lambda)||^2 = sum(((q_j^T g)^2)/(l_1 + lambda)^2)
# ```
# The consequence of this is that the relationship between lambda and the trust region
# radius changes each iteration as `B` is updated. For this reason, many researchers
# prefer the more interpretable indirect approach. This is what Moré used in their
# classical implementation of the algorithm as well (see Moré, "The Levenberg-Marquardt
# Algorithm: Implementation and Theory.")
#


class _IndirectDualState(eqx.Module):
    delta: Scalar
    y: Array
    y_prev: Array
    lower_bound: Scalar
    upper_bound: Scalar
    step: Int[Array, ""]


class _IndirectDualRootFind(
    AbstractRootFinder[_IndirectDualState, Scalar, Scalar, Aux]
):
    gauss_newton: bool
    converged_tol: float

    def init(
        self,
        fn: Fn[Scalar, Scalar, Aux],
        y: Scalar,
        args: Any,
        options: dict[str, Any],
        f_struct: PyTree[jax.ShapeDtypeStruct],
        aux_struct: PyTree[jax.ShapeDtypeStruct],
        tags: frozenset[object],
    ) -> _IndirectDualState:
        del f_struct, aux_struct
        try:
            delta = options["delta"]
        except KeyError:
            raise ValueError(
                "The indirect iterative dual root find needs delta "
                "(trust region radius) passed via `options['delta']`"
            )

        if self.gauss_newton:
            try:
                vector = options["vector"]
                operator = options["operator"]
            except KeyError:
                raise ValueError(
                    "The indirect iterative dual root find with "
                    "`gauss_newton=True` needs the operator and vector passed via "
                    "`options['operator']` and `options['vector']`."
                )
            grad = operator.transpose().mv(vector)
        else:
            try:
                grad = options["vector"]
            except KeyError:
                raise ValueError(
                    "The indirect iterative dual root find with "
                    "`gauss_newton=False` needs the vector passed via "
                    "`options['vector']`."
                )
        delta_nonzero = delta > jnp.finfo(delta.dtype).eps
        safe_delta = jnp.where(delta_nonzero, delta, 1)
        upper_bound = jnp.where(delta_nonzero, two_norm(grad) / safe_delta, jnp.inf)
        return _IndirectDualState(
            delta=delta,
            y=y,
            y_prev=y,
            lower_bound=jnp.array(0.0),
            upper_bound=upper_bound,
            step=jnp.array(0),
        )

    def step(
        self,
        fn: Fn[Scalar, Scalar, Aux],
        y: Scalar,
        args: Any,
        options: dict[str, Any],
        state: _IndirectDualState,
        tags: frozenset[object],
    ) -> tuple[Scalar, _IndirectDualState, Aux]:
        # avoid an extra compilation of problem.fn in the init.
        y_or_zero = jnp.where(state.step == 0, 0, y)
        lambda_in_bounds = (state.lower_bound < y) & (y < state.upper_bound)
        new_y = jnp.where(
            lambda_in_bounds,
            y_or_zero,
            jnp.maximum(
                1e-3 * state.upper_bound,
                jnp.sqrt(state.upper_bound * state.lower_bound),
            ),
        )
        # TODO(raderj): track down a link to reference for this.
        f_val, aux = fn(y_or_zero, args)
        f_grad, _ = jax.grad(fn, has_aux=True)(y_or_zero, args)
        f_grad = cast(Array, f_grad)
        grad_nonzero = f_grad < jnp.finfo(f_grad.dtype).eps
        safe_grad = jnp.where(grad_nonzero, f_grad, 1)
        factor = jnp.where(grad_nonzero, f_val / safe_grad, jnp.array(jnp.inf))
        upper_bound = jnp.where(f_val < 0, y, state.upper_bound)
        lower_bound = jnp.maximum(state.lower_bound, y - factor)
        diff = -((f_val - state.delta) / state.delta) * factor
        new_y = y + diff
        new_y = jnp.where(state.step == 0, y, new_y)
        upper_bound = jnp.where(state.step == 0, state.upper_bound, upper_bound)
        lower_bound = jnp.where((state.step == 0) & grad_nonzero, -factor, lower_bound)
        new_state = _IndirectDualState(
            delta=state.delta,
            y=new_y,
            y_prev=y,
            lower_bound=lower_bound,
            upper_bound=upper_bound,
            step=state.step + 1,
        )
        return jnp.clip(new_y, a_min=0), new_state, aux

    def terminate(
        self,
        fn: Fn[Scalar, Scalar, Aux],
        y: Scalar,
        args: Any,
        options: dict[str, Any],
        state: _IndirectDualState,
        tags: frozenset[object],
    ) -> tuple[Bool[Array, ""], RESULTS]:
        at_least_two = state.step >= 2
        interval_size = jnp.abs(state.upper_bound - state.lower_bound)
        interval_converged = interval_size < self.converged_tol
        y_converged = jnp.abs(state.y - state.y_prev) < self.converged_tol
        converged = interval_converged | y_converged
        terminate = converged & at_least_two
        return terminate, RESULTS.successful

    def buffers(self, state: _IndirectDualState) -> tuple[()]:
        return ()


class _IterativeDualState(eqx.Module):
    y: PyTree[Array]
    fn: Fn[Scalar, Scalar, Any]
    vector: PyTree[Array]
    operator: lx.AbstractLinearOperator


class _Damped(eqx.Module):
    fn: Callable
    damping: Float[Array, " "]

    def __call__(self, y: PyTree[Array], args: Any):
        damping = jnp.sqrt(self.damping)
        f, aux = self.fn(y, args)
        damped = jtu.tree_map(lambda yi: damping * yi, y)
        return (f, damped), aux


class _DirectIterativeDual(AbstractDescent[_IterativeDualState]):
    gauss_newton: bool
    modify_jac: Callable[
        [lx.JacobianLinearOperator], lx.AbstractLinearOperator
    ] = lx.linearise

    def init_state(
        self,
        fn: Fn[Y, Out, Aux],
        y: PyTree[Array],
        vector: PyTree[Array],
        operator: Optional[lx.AbstractLinearOperator],
        operator_inv: Optional[lx.AbstractLinearOperator],
        args: Any,
        options: dict[str, Any],
    ) -> _IterativeDualState:
        if operator is None:
            assert False
        return _IterativeDualState(y, fn, vector, operator)

    def update_state(
        self,
        descent_state: _IterativeDualState,
        diff_prev: PyTree[Array],
        vector: PyTree[Array],
        operator: Optional[lx.AbstractLinearOperator],
        operator_inv: Optional[lx.AbstractLinearOperator],
        options: dict[str, Any],
    ) -> _IterativeDualState:
        return _IterativeDualState(descent_state.y, descent_state.fn, vector, operator)

    def __call__(
        self,
        delta: Scalar,
        descent_state: _IterativeDualState,
        args: Any,
        options: dict[str, Any],
    ) -> tuple[PyTree[Array], RESULTS]:
        if self.gauss_newton:
            vector = (descent_state.vector, ω(descent_state.y).call(jnp.zeros_like).ω)
            operator = lx.JacobianLinearOperator(
                _Damped(descent_state.fn, delta),
                descent_state.y,
                args,
                _has_aux=True,
            )
        else:
            vector = descent_state.vector
            operator = descent_state.operator + delta * lx.IdentityLinearOperator(
                descent_state.operator.in_structure()
            )
        operator = self.modify_jac(operator)
        linear_soln = lx.linear_solve(operator, vector, lx.QR(), throw=False)
        diff = (-linear_soln.value**ω).ω
        return diff, RESULTS.promote(linear_soln.result)

    def predicted_reduction(
        self,
        diff: PyTree[Array],
        descent_state: _IterativeDualState,
        args: Any,
        options: dict[str, Any],
    ) -> Scalar:
        return quadratic_predicted_reduction(
            self.gauss_newton, diff, descent_state, args, options
        )


class DirectIterativeDual(_DirectIterativeDual):
    def __call__(
        self,
        delta: Scalar,
        descent_state: _IterativeDualState,
        args: Any,
        options: dict[str, Any],
    ) -> tuple[PyTree[Array], RESULTS]:
        if descent_state.operator is None:
            raise ValueError(
                "`operator` must be passed to `DirectIterativeDual`. "
                "Note that `operator_inv` is not currently supported for this descent."
            )

        delta_nonzero = delta > jnp.finfo(delta.dtype).eps
        if self.gauss_newton:
            vector = (descent_state.vector, ω(descent_state.y).call(jnp.zeros_like).ω)
            operator = lx.JacobianLinearOperator(
                _Damped(
                    descent_state.fn,
                    jnp.where(delta_nonzero, 1 / delta, jnp.inf),
                ),
                descent_state.y,
                args,
                _has_aux=True,
            )
        else:
            vector = descent_state.vector
            operator = descent_state.operator + jnp.where(
                delta_nonzero, 1 / delta, jnp.inf
            ) * lx.IdentityLinearOperator(descent_state.operator.in_structure())
        operator = self.modify_jac(operator)
        linear_soln = lx.linear_solve(operator, vector, lx.QR(), throw=False)
        no_diff = jtu.tree_map(jnp.zeros_like, linear_soln.value)
        diff = tree_where(delta_nonzero, (-linear_soln.value**ω).ω, no_diff)
        return diff, RESULTS.promote(linear_soln.result)


class IndirectIterativeDual(AbstractDescent[_IterativeDualState]):

    #
    # Indirect iterative dual finds the `λ` to match the
    # trust region radius by applying Newton's root finding method to
    # `φ(p) = ||p(λ)|| - δ`
    # where `δ` is the trust region radius.
    #
    # Moré found a clever way to compute `dφ/dλ = -||q||^2/||p(λ)||` where `q` is
    # defined as: `q = R^(-1) p`, for `R` as in the QR decomposition of
    # `(B + λ I)`.
    #
    # TODO(raderj): write a solver in root_finder which specifically assumes iterative
    # dual so we can use the trick (or at least see if it's worth doing.)
    # TODO(raderj): Use Householder + Givens method.
    #
    gauss_newton: bool
    lambda_0: Float[Array, ""]
    root_finder: AbstractRootFinder
    solver: lx.AbstractLinearSolver
    tr_reg: Optional[lx.PyTreeLinearOperator]
    norm: Callable
    modify_jac: Callable[[lx.JacobianLinearOperator], lx.AbstractLinearOperator]

    def __init__(
        self,
        gauss_newton: bool,
        lambda_0: float,
        root_finder: AbstractRootFinder = sentinel,
        solver: lx.AbstractLinearSolver = lx.AutoLinearSolver(well_posed=False),
        tr_reg: Optional[lx.PyTreeLinearOperator] = None,
        norm: Callable = two_norm,
        modify_jac: Callable[
            [lx.JacobianLinearOperator], lx.AbstractLinearOperator
        ] = lx.linearise,
    ):
        self.gauss_newton = gauss_newton
        self.lambda_0 = jnp.array(lambda_0)
        if root_finder is sentinel:
            self.root_finder = _IndirectDualRootFind(self.gauss_newton, 1e-3)
        else:
            self.root_finder = root_finder
        self.solver = solver
        self.tr_reg = tr_reg
        self.norm = norm
        self.modify_jac = modify_jac

    def init_state(
        self,
        fn: Fn[Y, Out, Aux],
        y: PyTree[Array],
        vector: PyTree[Array],
        operator: Optional[lx.AbstractLinearOperator],
        operator_inv: Optional[lx.AbstractLinearOperator],
        args: Any,
        options: dict[str, Any],
    ) -> _IterativeDualState:
        if operator is None:
            assert False
        return _IterativeDualState(y, fn, vector, operator)

    def update_state(
        self,
        descent_state: _IterativeDualState,
        diff_prev: PyTree[Array],
        vector: PyTree[Array],
        operator: Optional[lx.AbstractLinearOperator],
        operator_inv: Optional[lx.AbstractLinearOperator],
        options: dict[str, Any],
    ):
        return _IterativeDualState(descent_state.y, descent_state.fn, vector, operator)

    def __call__(
        self,
        delta: Scalar,
        descent_state: _IterativeDualState,
        args: Any,
        options: dict[str, Any],
    ) -> tuple[PyTree[Array], RESULTS]:
        if descent_state.operator is None:
            raise ValueError(
                "`operator` must be passed to "
                " `IndirectDirectIterativeDual`. Note that `operator_inv` is "
                "not currently supported for this descent."
            )

        direct_dual = eqx.Partial(
            _DirectIterativeDual(self.gauss_newton, self.modify_jac),
            descent_state=descent_state,
            args=args,
            options=options,
        )
        newton_soln = lx.linear_solve(
            descent_state.operator,
            (-descent_state.vector**ω).ω,
            self.solver,
            throw=False,
        )
        # NOTE: try delta = delta * self.norm(newton_step).
        # this scales the trust and sets the natural bound `delta = 1`.
        newton_step = (-ω(newton_soln.value)).ω
        newton_result = RESULTS.promote(newton_soln.result)
        tr_reg = self.tr_reg

        if tr_reg is None:
            tr_reg = lx.IdentityLinearOperator(jax.eval_shape(lambda: newton_step))

        def comparison_fn(
            lambda_i: Scalar,
            args: Any,
        ):
            (step, _) = direct_dual(lambda_i)
            step_norm = self.norm(step)
            return step_norm - delta

        def accept_newton():
            return newton_step, newton_result

        def reject_newton():
            root_find_options = {
                "vector": descent_state.vector,
                "operator": descent_state.operator,
                "delta": delta,
            }
            lambda_out = root_find(
                fn=comparison_fn,
                has_aux=False,
                solver=self.root_finder,
                y0=self.lambda_0,
                args=args,
                options=root_find_options,
                max_steps=32,
                throw=False,
            ).value
            return direct_dual(lambda_out)

        newton_norm = self.norm(newton_step)
        return lax.cond(newton_norm < delta, accept_newton, reject_newton)

    def predicted_reduction(
        self,
        diff: PyTree[Array],
        descent_state: _IterativeDualState,
        args: Any,
        options: dict[str, Any],
    ) -> Scalar:
        return quadratic_predicted_reduction(
            self.gauss_newton, diff, descent_state, args, options
        )
