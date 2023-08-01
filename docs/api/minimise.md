# Minimisation

In addition to the following, note that the [Optax]](https://github.com/deepmind/optax) library offers an extensive collection of minimisers via first-order gradient methods -- as are in widespread use for neural networks. If you would like to use these through the Optimistix API then an [`optimistix.OptaxMinimiser`][] wrapper is provided.

::: optimistix.minimise

---

[`optimistix.minimise`][] supports any of the following minimisers.

??? abstract "`optimistix.AbstractMinimiser`"

    ::: optimistix.AbstractMinimiser
        selection:
            members:
                - init
                - step
                - terminate
                - buffers

??? abstract "`optimistix.AbstractGradientDescent`"

    ::: optimistix.AbstractGradientDescent
        selection:
            members:
                false

::: optimistix.GradientDescent
    selection:
        members:
            false

---

::: optimistix.BFGS
    selection:
        members:
            - __init__

---

::: optimistix.OptaxMinimiser
    selection:
        members:
            - __init__

---

::: optimistix.NonlinearCG
    selection:
        members:
            false

[`optimistix.NonlinearCG`][] supports several different methods for computing its β parameter. If you are trying multiple solvers to see which works best on your problem, then you may wish to try all four versions of nonlinear CG. These can each be passed as `NonlinearCG(..., method=...)`.

::: optimistix.polak_ribiere

::: optimistix.fletcher_reeves

::: optimistix.hestenes_stiefel

::: optimistix.dai_yuan