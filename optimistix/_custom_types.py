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
from typing import Any, TYPE_CHECKING, TypeVar, Union
from typing_extensions import TypeAlias

import equinox as eqx
import equinox.internal as eqxi
from jaxtyping import Scalar


if TYPE_CHECKING:
    from typing import ClassVar as AbstractVar
else:
    from equinox import AbstractVar


class AbstractLineSearchState(eqx.Module):
    next_init: AbstractVar[Scalar]


Args: TypeAlias = Any
Aux = TypeVar("Aux")
Out = TypeVar("Out")
SolverState = TypeVar("SolverState")
LineSearchState = TypeVar("LineSearchState", bound=AbstractLineSearchState)
Y = TypeVar("Y")

Fn: TypeAlias = Callable[[Y, Args], tuple[Out, Aux]]
MaybeAuxFn: TypeAlias = Union[
    Callable[[Y, Args], tuple[Out, Aux]], Callable[[Y, Args], Out]
]

sentinel: Any = eqxi.doc_repr(object(), "sentinel")
