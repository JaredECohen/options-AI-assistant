"""Plotly rendering of a fitted IV surface."""

from __future__ import annotations

import plotly.graph_objects as go

from optionscoach.surface.iv_surface import BilinearIVSurface


def plot_surface_3d(
    surface: BilinearIVSurface, *, title: str = "Implied Volatility Surface"
) -> go.Figure:
    """Render a fitted :class:`BilinearIVSurface` as a 3D Plotly figure.

    Axes: x = log-moneyness ``ln(K/S)``, y = time-to-expiry (years), z = IV.
    """
    mesh_lm, mesh_t, iv = surface.grid()
    figure = go.Figure(
        data=[
            go.Surface(
                x=mesh_lm,
                y=mesh_t,
                z=iv,
                colorscale="Viridis",
                colorbar={"title": "IV"},
            )
        ]
    )
    figure.update_layout(
        title=title,
        scene={
            "xaxis_title": "log-moneyness ln(K/S)",
            "yaxis_title": "time to expiry (years)",
            "zaxis_title": "implied volatility",
        },
        margin={"l": 0, "r": 0, "t": 40, "b": 0},
    )
    return figure
