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

import abc
from typing import Any, Generic

import equinox as eqx
from jaxtyping import PyTree, Scalar

from ._custom_types import Y
from ._solution import RESULTS


class AbstractDescent(eqx.Module, Generic[Y]):
    """The abstract base class for descents. A descent is a method which returns the
    `diff` to take at point `y` such that `y + diff` is the next iterate in a
    nonlinear optimisation problem.

    This generalises the concept of line search and trust region to anything which
    takes a step-size and returns the step to take given that step-size.
    """

    @abc.abstractmethod
    def __call__(
        self,
        step_size: Scalar,
        args: PyTree,
        options: dict[str, Any],
    ) -> tuple[Y, RESULTS]:
        ...
