import equinox as eqx
from diffrax.misc import rms_norm

from ..linear_operator import JacobianLinearOperator
from ..linear_solve import AbstractLinearSolver, linear_solve, AutoLinearSolver
from ..results import RESULTS
from ..root_finding import AbstractRootFindSolver


def _small(diffsize: Scalar) -> bool:
  # TODO(kidger): make a more careful choice here -- the existence of this
  # function is pretty ad-hoc.
  resolution = 10 ** (2 - jnp.finfo(diffsize.dtype).precision)
  return diffsize < resolution


def _diverged(rate: Scalar) -> bool:
  return ~jnp.isfinite(rate) | (rate > 2)


def _converged(factor: Scalar, tol: Scalar) -> bool:
  return (factor > 0) & (factor < tol)


class _NewtonChordState(eqx.Module):
  linear_state: Optional[PyTree]
  step: Scalar
  diffsize: Scalar
  diffsize_prev: Scalar


class _NewtonChord(AbstractRootFindSolver):
  rtol: float
  atol: float
  kappa: float = 1e-2
  norm: Callable = rms_norm
  linear_solver: AbstractLinearSolver = AutoLinearSolver()

  @property
  @abc.abstractmethod
  def _is_newton(self) -> bool:
    ...

  def init(self, root_prob, y, args, options):
    if self._is_newton:
      linear_state = None
    else:
      jac = JacobianLinearOperator(root_prob.fn, y, args, patterns=root_prob.patterns)
      linear_state = (jac, self.linear_solver.init(jac))
    return _NewtonChordState(linear_state=linear_state, step=jnp.array(0), diffsize=jnp.array(0.0), diffsize_prev=jnp.array(0.0))

  def step(self, root_prob, y, args, options, state):
    fx = root_prob.fn(y, args)
    if self._is_newton:
      jac = JacobianLinearOperator(root_prob.fn, y, args, patterns=root_prob.patterns)
      diff = linear_solve(jac, fx, self.linear_solver).solution
    else:
      jac, state = state.linear_state
      diff = linear_solve(jac, fx, self.linear_solver, state=state).solution
    scale = (self.atol + self.rtol * y**ω).ω
    diffsize = self.norm((diff**ω / scale**ω).ω)
    new_y = (y**ω - diff**ω).ω
    new_state = _NewtonChordState(linear_state=state.linear_state, step=state.step + 1, diffsize=diffsize, diffsize_prev=state.diffsize)
    return new_y, new_state

  def terminate(self, root_prob, y, args, options, state):
    del root_prob, y, args, options
    at_least_two = state.step >= 2
    rate = state.diffsize / state.diffsize_prev
    factor = state.diffsize * rate / (1  - rate)
    small = _small(state.diffsize)
    diverged = _diverged(rate)
    converged = _converged(factor, self.kappa)
    terminate = at_least_two & (small | diverged | converged)
    result = jnp.where(converged, RESULTS.successful, RESULTS.nonconvergence)
    result = jnp.where(diverged, RESULTS.divergence, result)
    result = jnp.where(small, RESULTS.successful, result)
    return terminate, result


class Newton(_NewtonChord):
  _is_newton = True


class Chord(_NewtonChord):
  _is_newton = False
