import abc
from typing import Callable

import equinox as eqx
import jax.numpy as jnp
import jax.tree_util as jtu
from equinox.internal import ω
from jaxtyping import Scalar

from ..least_squares import AbstractLeastSquaresSolver
from ..linear_operator import AbstractLinearOperator, JacobianLinearOperator, linearise
from ..linear_solve import AbstractLinearSolver, AutoLinearSolver, linear_solve
from ..misc import max_norm
from ..solution import RESULTS


def _small(diffsize: Scalar) -> bool:
    # TODO(kidger): make a more careful choice here -- the existence of this
    # function is pretty ad-hoc.
    resolution = 10 ** (2 - jnp.finfo(diffsize.dtype).precision)
    return diffsize < resolution


def _diverged(rate: Scalar) -> bool:
    return jnp.invert(jnp.isfinite(rate)) | (rate > 2)


def _converged(factor: Scalar, tol: Scalar) -> bool:
    return (factor > 0) & (factor < tol)


class _Damped(eqx.Module):
    fn: Callable
    damping: float

    def __call__(self, y, args):
        damping = jnp.sqrt(self.damping)
        f, aux = self.fn(y, args)
        damped = jtu.tree_map(lambda yi: damping * yi, y)
        return (f, damped), aux


class _GaussNewtonLevenbergMarquardtState(eqx.Module):
    step: Scalar
    diffsize: Scalar
    diffsize_prev: Scalar
    result: RESULTS


class _GaussNewtonLevenbergMarquardt(AbstractLeastSquaresSolver):
    rtol: float
    atol: float
    kappa: float = 1e-2
    norm: Callable = max_norm
    # Implies that Levenberg-Marquardt will use a QR solver.
    linear_solver: AbstractLinearSolver = AutoLinearSolver(well_posed=None)
    modify_jac: Callable[[JacobianLinearOperator], AbstractLinearOperator] = linearise

    @property
    @abc.abstractmethod
    def _is_gauss_newton(self) -> bool:
        ...

    def init(self, problem, y, args, options):
        del problem, y, args, options
        return _GaussNewtonLevenbergMarquardtState(
            step=jnp.array(0),
            diffsize=jnp.array(0.0),
            diffsize_prev=jnp.array(0.0),
            result=jnp.array(RESULTS.successful),
        )

    def step(self, problem, y, args, options, state):
        del options
        residuals = problem.fn(y, args)
        if self._is_gauss_newton:
            jac = JacobianLinearOperator(
                problem.fn, y, args, tags=problem.tags, _has_aux=True
            )
        else:
            damping = ...  # TODO(kidger)
            # Note that this doesn't use tags at all.
            jac = JacobianLinearOperator(
                _Damped(problem.fn, damping),
                y,
                args,
                _has_aux=True,
            )
            residuals = (residuals, jtu.tree_map(jnp.zeros_like, y))
        jac = self.modify_jac(jac)
        sol = linear_solve(jac, residuals, self.linear_solver, throw=False)
        # Yep, no `J^T J` here.
        #
        # So Gauss-Newton is often defined as `diff = (J^T J)^{-1} J^T r`.
        # (Similarly for Levenberg-Marquardt.)
        #
        # However `(J^T J)^{-1} J^T` is the pseudoinverse of `J`, i.e. the solution to a
        # linear least squares problem. When folks write down this `J^T J` version, what
        # they're actually doing is just solving this linear least squares problem via
        # the normal equations. (Which reduce linear least squares down to a linear
        # solve.)
        #
        # However this is generally not a great idea:
        # - Calling `J^T J` squares the condition number => bad for numerical stability;
        # - This can take a long time to compile: JAX isn't smart enough to treat each
        #   `J` and `J^T` together, and treats each as a separate autodiff call. (Grr,
        #   the endless problem with XLA inlining everything.)
        #
        # Much better to just "solve" `diff = J^{-1} r` directly: our `linear_solve`
        # routine will return a linear least squares solution in general (if the system
        # is not well-posed: in our case, overdetermined).
        #
        # ...and then, if you wish, take `linear_solver=CG(normal=True)` to solve it via
        # the normal equations in the textbook way! (In practice if you go looking
        # around, you'll see that most sophisticated implementations actually solve this
        # using a QR decomposition.)
        diff = sol.value
        new_y = (y**ω - diff**ω).ω
        scale = (self.atol + self.rtol * ω(new_y).call(jnp.abs)).ω
        diffsize = self.norm((diff**ω / scale**ω).ω)
        new_state = _GaussNewtonLevenbergMarquardtState(
            step=state.step + 1,
            diffsize=diffsize,
            diffsize_prev=state.diffsize,
            result=sol.result,
        )
        return new_y, new_state, jac.aux

    def terminate(self, problem, y, args, options, state):
        del problem, y, args, options
        at_least_two = state.step >= 2
        rate = state.diffsize / state.diffsize_prev
        factor = state.diffsize * rate / (1 - rate)
        small = _small(state.diffsize)
        diverged = _diverged(rate)
        converged = _converged(factor, self.kappa)
        linsolve_fail = state.result != RESULTS.successful
        terminate = linsolve_fail | (at_least_two & (small | diverged | converged))
        result = jnp.where(diverged, RESULTS.nonlinear_divergence, RESULTS.successful)
        result = jnp.where(linsolve_fail, state.result, result)
        return terminate, result


class GaussNewton(_GaussNewtonLevenbergMarquardt):
    _is_gauss_newton = True


class LevenbergMarquardt(_GaussNewtonLevenbergMarquardt):
    _is_gauss_newton = False
