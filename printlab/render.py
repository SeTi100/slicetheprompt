"""Vier Ansichten eines (mehrfarbigen) Netzes als PNG (matplotlib, ohne Bildschirm)."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import to_rgb  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from mpl_toolkits.mplot3d.art3d import Poly3DCollection  # noqa: E402

from .stlio import face_normals  # noqa: E402

VIEWS = [
    ("Isometrie", 25, -55),
    ("Vorne (−Y)", 0, -90),
    ("Rechts (+X)", 0, 0),
    ("Oben (+Z)", 90, -90),
]


def _shade(tris: np.ndarray, base) -> np.ndarray:
    n = face_normals(tris)
    light = np.array([0.4, -0.6, 0.7])
    light /= np.linalg.norm(light)
    k = 0.45 + 0.55 * np.clip(n @ light, 0, 1)
    base = np.array(base)
    return np.clip(base[None] * k[:, None] + 0.04, 0, 1)


def render(parts, out: str | Path, title: str = "", bed: dict | None = None, max_tris: int = 120_000) -> Path:
    """parts: Dreiecks-Array ODER Liste von (Dreiecke, Farbe, Name)."""
    if isinstance(parts, np.ndarray):
        parts = [(parts, "#4682B4", None)]
    total = sum(len(p[0]) for p in parts)
    tris_all, cols_all = [], []
    for tris, color, _ in parts:
        if total > max_tris:
            k = max(1, int(len(tris) * max_tris / total))
            tris = tris[np.random.default_rng(0).choice(len(tris), k, replace=False)]
        rgb = to_rgb(color)
        # sehr helle Farben (weiss) etwas abdunkeln, damit sie vor dem Hintergrund sichtbar sind
        if min(rgb) > 0.85:
            rgb = tuple(c * 0.88 for c in rgb)
        tris_all.append(tris)
        cols_all.append(_shade(tris, rgb))
    tris = np.concatenate(tris_all)
    colors = np.concatenate(cols_all)
    pts = tris.reshape(-1, 3)
    mn, mx = pts.min(0), pts.max(0)
    size = mx - mn
    centre = (mn + mx) / 2
    half = max(size.max() / 2, 1.0) * 1.05

    fig = plt.figure(figsize=(10, 9), dpi=110)
    for i, (name, elev, azim) in enumerate(VIEWS):
        ax = fig.add_subplot(2, 2, i + 1, projection="3d")
        edge = (0, 0, 0, 0.08) if len(tris) < 20_000 else "none"
        coll = Poly3DCollection(tris, facecolors=colors, edgecolors=edge, linewidths=0.2)
        ax.add_collection3d(coll)
        ax.set_xlim(centre[0] - half, centre[0] + half)
        ax.set_ylim(centre[1] - half, centre[1] + half)
        ax.set_zlim(centre[2] - half, centre[2] + half)
        ax.set_box_aspect((1, 1, 1))
        ax.view_init(elev=elev, azim=azim)
        if i > 0:
            ax.set_proj_type("ortho")
        ax.set_title(name, fontsize=10)
        ax.set_xlabel("X", fontsize=8)
        ax.set_ylabel("Y", fontsize=8)
        ax.set_zlabel("Z", fontsize=8)
        ax.tick_params(labelsize=7)
        if bed is not None and abs(mn[2]) < 1e-6:
            xs = [centre[0] - half, centre[0] + half]
            ys = [centre[1] - half, centre[1] + half]
            X, Y = np.meshgrid(xs, ys)
            ax.plot_surface(X, Y, np.zeros_like(X), color=(0.85, 0.85, 0.8), alpha=0.25, shade=False)
    dims = "{:.2f} × {:.2f} × {:.2f} mm".format(*size)
    fig.suptitle(f"{title}   {dims}".strip(), fontsize=12)
    labels = [(n, c) for _, c, n in parts if n]
    if labels:
        fig.legend(handles=[Patch(facecolor=c, edgecolor="k", label=n) for n, c in labels],
                   loc="lower center", ncol=min(len(labels), 6), fontsize=9)
    fig.tight_layout(rect=(0, 0.04 if labels else 0, 1, 1))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    return out
