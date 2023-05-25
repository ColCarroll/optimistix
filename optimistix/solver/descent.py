from typing import Any, Optional

import equinox as eqx
import jax.numpy as jnp
from equinox.internal import ω
from jaxtyping import Array, PyTree, Scalar

from ..iterate import AbstractIterativeProblem
from ..line_search import AbstractDescent
from ..linear_operator import AbstractLinearOperator
from ..linear_solve import AutoLinearSolver, linear_solve
from ..misc import tree_inner_prod, two_norm
from ..solution import RESULTS
from .misc import quadratic_predicted_reduction


class GradientState(eqx.Module):
    vector: PyTree[Array]


class UnnormalisedGradient(AbstractDescent[GradientState]):
    def init_state(
        self,
        problem: AbstractIterativeProblem,
        y: PyTree[Array],
        vector: PyTree[Array],
        operator: Optional[AbstractLinearOperator] = None,
        operator_inv: Optional[AbstractLinearOperator] = None,
        args: Optional[Any] = None,
        options: Optional[dict[str, Any]] = None,
    ):
        return GradientState(vector)

    def update_state(
        self,
        descent_state: GradientState,
        diff_prev: PyTree[Array],
        vector: PyTree[Array],
        operator: Optional[AbstractLinearOperator] = None,
        operator_inv: Optional[AbstractLinearOperator] = None,
        options: Optional[dict[str, Any]] = None,
    ):
        return GradientState(vector)

    def __call__(
        self,
        delta: Scalar,
        descent_state: GradientState,
        args: Any,
        options: dict[str, Any],
    ):
        diff = (-delta * ω(descent_state.vector)).ω
        return diff, jnp.array(RESULTS.successful)

    def predicted_reduction(
        self,
        diff: PyTree[Array],
        descent_state: GradientState,
        args: PyTree,
        options: Optional[dict[str, Any]],
    ):
        return tree_inner_prod(descent_state.vector, diff)


class NormalisedGradient(AbstractDescent[GradientState]):
    def init_state(
        self,
        problem: AbstractIterativeProblem,
        y: PyTree[Array],
        vector: PyTree[Array],
        operator: Optional[AbstractLinearOperator] = None,
        operator_inv: Optional[AbstractLinearOperator] = None,
        args: Optional[Any] = None,
        options: Optional[dict[str, Any]] = None,
    ):
        return GradientState(vector)

    def update_state(
        self,
        descent_state: GradientState,
        diff_prev: PyTree[Array],
        vector: PyTree[Array],
        operator: Optional[AbstractLinearOperator] = None,
        operator_inv: Optional[AbstractLinearOperator] = None,
        options: Optional[dict[str, Any]] = None,
    ):
        return GradientState(vector)

    def __call__(
        self,
        delta: Scalar,
        descent_state: GradientState,
        args: Any,
        options: dict[str, Any],
    ):
        diff = ((-delta * descent_state.vector**ω) / two_norm(descent_state.vector)).ω
        return diff, jnp.array(RESULTS.successful)

    def predicted_reduction(
        self,
        diff: PyTree[Array],
        descent_state: GradientState,
        args: PyTree,
        options: Optional[dict[str, Any]],
    ):
        return tree_inner_prod(descent_state.vector, diff)


class NewtonState(eqx.Module):
    vector: PyTree[Array]
    operator: Optional[AbstractLinearOperator]
    operator_inv: Optional[AbstractLinearOperator]


#
# NOTE: we handle both the Gauss-Newton and quasi-Newton case identically
# in the `__call__` method. This is because the newton step is
# `-B^† g` where`B^†` is the Moore-Penrose pseudoinverse of the quasi-Newton
# matrix `B` and `g` the gradient of the function to minimise.
# In the Gauss-Newton setting the newton step is `-J^† r` where
# `J^†` is the pseudoinverse of the Jacobian and `r` is the residual vector.
# Throughout, we have abstracted `J` and `B` into `operator` and
# `g` and `r` into `vector`, so the solves are identical.
#
# However, note that the gauss_newton flag is still necessary for the
# predicted reduction, whos computation does change depending on
# whether `operator` and `vector` contains the quasi-Newton matrix and
# vector or the Jacobian and residual. In line-searches which do not use
# this, there is no difference between `gauss_newton=True` and `gauss_newton=False`.
#
class UnnormalisedNewton(AbstractDescent[NewtonState]):
    gauss_newton: bool = False

    def init_state(
        self,
        problem: AbstractIterativeProblem,
        y: PyTree[Array],
        vector: PyTree[Array],
        operator: Optional[AbstractLinearOperator],
        operator_inv: Optional[AbstractLinearOperator],
        args: Optional[Any] = None,
        options: Optional[dict[str, Any]] = None,
    ):
        return NewtonState(vector, operator, operator, operator_inv)

    def update_state(
        self,
        descent_state: NewtonState,
        diff_prev: PyTree[Array],
        vector: PyTree[Array],
        operator: Optional[AbstractLinearOperator] = None,
        operator_inv: Optional[AbstractLinearOperator] = None,
        options: Optional[dict[str, Any]] = None,
    ):
        return NewtonState(vector, operator, operator_inv)

    def __call__(
        self,
        delta: Scalar,
        descent_state: NewtonState,
        args: Any,
        options: dict[str, Any],
    ):
        if descent_state.operator_inv is not None:
            newton = descent_state.operator_inv.mv(descent_state.vector)
            result = RESULTS.successful
        elif descent_state.operator is not None:
            out = linear_solve(
                descent_state.operator,
                descent_state.vector,
                AutoLinearSolver(well_posed=False),
            )
            newton = out.value
            result = out.result
        else:
            raise ValueError(
                "At least one of `operator` or `operator_inv` must be "
                "passed to the UnnormalisedNewton descent."
            )

        diff = (-delta * newton**ω).ω
        return diff, result

    def predicted_reduction(
        self,
        diff: PyTree[Array],
        descent_state: NewtonState,
        args: PyTree,
        options: Optional[dict[str, Any]],
    ):
        return quadratic_predicted_reduction(
            self.gauss_newton, diff, descent_state, args, options
        )


class NormalisedNewton(AbstractDescent[NewtonState]):
    gauss_newton = False

    def init_state(
        self,
        problem: AbstractIterativeProblem,
        y: PyTree[Array],
        vector: PyTree[Array],
        operator: Optional[AbstractLinearOperator],
        operator_inv: Optional[AbstractLinearOperator],
        args: Optional[Any] = None,
        options: Optional[dict[str, Any]] = None,
    ):
        return NewtonState(vector, operator)

    def update_state(
        self,
        descent_state: NewtonState,
        diff_prev: PyTree[Array],
        vector: PyTree[Array],
        operator: Optional[AbstractLinearOperator],
        operator_inv: Optional[AbstractLinearOperator],
        options: Optional[dict[str, Any]] = None,
    ):
        return NewtonState(vector, operator)

    def __call__(
        self,
        delta: Scalar,
        descent_state: NewtonState,
        args: Any,
        options: dict[str, Any],
    ):
        if descent_state.operator_inv is not None:
            newton = descent_state.operator_inv.mv(descent_state.vector)
            result = RESULTS.successful
        elif descent_state.operator is not None:
            out = linear_solve(
                descent_state.operator,
                descent_state.vector,
                AutoLinearSolver(well_posed=False),
            )
            newton = out.value
            result = out.result
        else:
            raise ValueError(
                "At least one of `operator` or `operator_inv` must be "
                "passed to the UnnormalisedNewton descent."
            )

        diff = ((-delta * newton**ω) / two_norm(newton)).ω
        return diff, result

    def predicted_reduction(
        self,
        diff: PyTree[Array],
        descent_state: NewtonState,
        args: PyTree,
        options: Optional[dict[str, Any]],
    ):
        return quadratic_predicted_reduction(
            self.gauss_newton, diff, descent_state, args, options
        )
