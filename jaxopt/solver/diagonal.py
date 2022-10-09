class Diagonal(AbstractLinearSolver):
  maybe_singular: bool = True
  rcond: Optional[float] = None

  def is_maybe_singular(self):
    return self.maybe_singular

  def init(self, operator, options):
    del options
    if operator.in_size() != operator.out_size():
      raise ValueError("`Diagonal` may only be used for linear solves with square matrices")
    if "diagonal" not in operator.pattern:
      raise ValueError("`Diagonal` may only be used for linear solves with diagonal matrices")
    return operator

  def compute(self, state, vector, options):
    operator = state
    del state, options
    if "unit_diagonal" in operator.pattern:
      solution = vector
    else:
      # TODO(kidger): do diagonal solves more efficiently than this.
      vector, unflatten = jfu.ravel_pytree(vector)
      diag = jnp.diag(operator.as_matrix())
      rcond = resolve_rcond(self.rcond, diag.size, diag.size, diag.dtype)
      diag = jnp.where(diag >= rcond * jnp.max(diag), diag, jnp.inf)
      solution = unflatten(vector / diag)
    return solution, RESULTS.successful, {}

  def transpose(self, state, options):
    # Matrix is symmetric
    return state, options
