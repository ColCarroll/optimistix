from typing import Any, Dict, Union

import equinox as eqx
import equinox.internal as eqxi
from jaxtyping import Array, PyTree


_linear_singular_msg = """
The linear solver returned NaN output. This usually means either:

(a) the operator was singular (`jnp.linalg.det(matrix) == 0`), and the solver requires
    nonsingular matrices; or

(b) the operator had a high condition number (`jnp.linalg.cond(matrix)` is large), and
    the solver can only handle low condition numbers; or

(c) the operator was not positive definite, and the solver requires positive define
    matrices.

In each case consider changing your solver to one that supports the structure of the
operator. (`optimistix.QR()` is moderately expensive but will work for all problems.)

Alternatively, you may have a bug in the definition of your operator. (If you were
expecting this solver to work for it.)
""".strip()


class RESULTS(metaclass=eqxi.ContainerMeta):
    successful = ""
    max_steps_reached = (
        "The maximum number of solver steps was reached. Try increasing `max_steps`."
    )
    linear_singular = _linear_singular_msg
    nonlinear_divergence = "Nonlinear solve diverged."


class Solution(eqx.Module):
    value: PyTree[Array]
    result: RESULTS
    state: PyTree[Any]
    aux: PyTree[Array]
    stats: Dict[str, PyTree[Union[Array, int]]]
