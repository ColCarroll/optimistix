# Copyright 2023 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from collections.abc import Callable
from typing import Any, Generic

import equinox as eqx
import jax
import jax.numpy as jnp
from equinox.internal import ω
from jaxtyping import Array, Bool, PyTree, Scalar

from .._custom_types import AbstractLineSearchState, Aux, Fn, Y
from .._descent import AbstractDescent
from .._minimise import AbstractMinimiser, minimise
from .._misc import max_norm, two_norm
from .._solution import RESULTS
from .learning_rate import LearningRate
from .misc import cauchy_termination


class Gradient(AbstractDescent[Y]):
    normalise: bool = False

    def __call__(
        self,
        step_size: Scalar,
        args: PyTree,
        options: dict[str, Any],
    ) -> tuple[Y, RESULTS]:
        vector = options["vector"]
        if self.normalise:
            diff = (vector**ω / two_norm(vector)).ω
        else:
            diff = vector
        return (-step_size * diff**ω).ω, RESULTS.successful


class _GradientDescentState(eqx.Module, Generic[Y, Aux]):
    step_size: Scalar
    y_prev: Y
    f_val: Scalar
    f_prev: Scalar
    result: RESULTS


class AbstractGradientDescent(AbstractMinimiser[_GradientDescentState[Y, Aux], Y, Aux]):
    rtol: float
    atol: float
    norm: Callable
    line_search: AbstractMinimiser[AbstractLineSearchState, Y, Aux]

    def init(
        self,
        fn: Fn[Y, Scalar, Aux],
        y: Y,
        args: PyTree,
        options: dict[str, Any],
        f_struct: jax.ShapeDtypeStruct,
        aux_struct: PyTree[jax.ShapeDtypeStruct],
        tags: frozenset[object],
    ) -> _GradientDescentState[Y, Aux]:
        del fn, aux_struct
        return _GradientDescentState(
            step_size=jnp.array(1.0),
            y_prev=y,
            f_val=jnp.array(jnp.inf, dtype=f_struct.dtype),
            f_prev=jnp.array(jnp.inf, dtype=f_struct.dtype),
            result=RESULTS.successful,
        )

    def step(
        self,
        fn: Fn[Y, Scalar, Aux],
        y: Y,
        args: PyTree,
        options: dict[str, Any],
        state: _GradientDescentState[Y, Aux],
        tags: frozenset[object],
    ) -> tuple[Y, _GradientDescentState, Aux]:
        (f_val, aux), new_grad = jax.value_and_grad(fn, has_aux=True)(y, args)
        line_search_options = {
            "init_step_size": state.step_size,
            "vector": new_grad,
            "f0": f_val,
        }
        line_sol = minimise(
            fn,
            self.line_search,
            y,
            args,
            line_search_options,
            has_aux=True,
            throw=False,
        )
        result = RESULTS.where(
            line_sol.result == RESULTS.max_steps_reached,
            RESULTS.successful,
            line_sol.result,
        )
        new_state = _GradientDescentState(
            step_size=line_sol.state.next_init,
            y_prev=y,
            f_val=f_val,
            f_prev=state.f_val,
            result=result,
        )
        return line_sol.value, new_state, aux

    def terminate(
        self,
        fn: Fn[Y, Scalar, Aux],
        y: Y,
        args: PyTree,
        options: dict[str, Any],
        state: _GradientDescentState[Y, Aux],
        tags: frozenset[object],
    ) -> tuple[Bool[Array, ""], RESULTS]:
        return cauchy_termination(
            self.rtol,
            self.atol,
            self.norm,
            y,
            (y**ω - state.y_prev**ω).ω,
            state.f_val,
            state.f_prev,
            state.result,
        )

    def buffers(self, state: _GradientDescentState[Y, Aux]) -> tuple[()]:
        return ()


class GradientDescent(AbstractGradientDescent):
    """Classic gradient descent with a learning rate `learning_rate`.

    `GradientDescent` can also use any `line_search`/`descent` which only uses gradient
    information. Right now the only `descent` which can be used with `GradientMethod`
    is `Gradient`. `GradientMethod` can use any of `BacktrackingLineSearch`,
    `LinearTrustRegion`, or `LearningRate`.
    """

    def __init__(
        self,
        rtol: float,
        atol: float,
        norm: Callable = max_norm,
        *,
        learning_rate: float,
    ):
        self.rtol = rtol
        self.atol = atol
        self.norm = norm
        self.line_search = LearningRate(Gradient(), learning_rate=learning_rate)
