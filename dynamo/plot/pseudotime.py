from typing import Any, Dict, Optional, Tuple, Union

try:
    from typing import Literal
except ImportError:
    from typing_extensions import Literal

import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
from anndata import AnnData
from scipy.sparse import csr_matrix

from ..tools.utils import update_dict
from .utils import save_fig


def plot_dim_reduced_direct_graph(
    adata: AnnData,
    graph: Optional[Union[csr_matrix, np.ndarray]] = None,
    cell_proj_closest_vertex: Optional[np.ndarray] = None,
    save_show_or_return: Literal["save", "show", "return"] = "show",
    save_kwargs: Dict[str, Any] = {},
) -> Optional[plt.Axes]:

    if graph is None:
        graph = adata.uns["directed_velocity_tree"]

    if cell_proj_closest_vertex is None:
        cell_proj_closest_vertex = adata.uns["cell_order"]["pr_graph_cell_proj_closest_vertex"]

    radius = 0.1
    cmap = plt.cm.viridis

    cells_percentage = [[0.5, 1, 0.5] for _ in range(graph.shape[0])]

    maxes = np.max(np.array(cells_percentage), axis=0)

    colors = {}

    for i in range(graph.shape[0]):
        colors[i] = list(np.array(cells_percentage[i]) / maxes)

    G = nx.from_numpy_array(graph)
    pos = nx.spring_layout(G)

    g = nx.draw_networkx_edges(G, pos=pos)

    for node in G.nodes:
        attributes = cells_percentage[node]

        plt.pie(
            [1] * len(attributes),  # s.t. all wedges have equal size
            center=pos[node],
            colors=[cmap(a) for a in colors[node]],
            radius=radius)

    if save_show_or_return in ["save", "both", "all"]:
        s_kwargs = {
            "path": None,
            "prefix": "plot_dim_reduced_direct_graph",
            "dpi": None,
            "ext": "pdf",
            "transparent": True,
            "close": True,
            "verbose": True,
        }
        s_kwargs = update_dict(s_kwargs, save_kwargs)

        if save_show_or_return in ["both", "all"]:
            s_kwargs["close"] = False

        save_fig(**s_kwargs)
    if save_show_or_return in ["show", "both", "all"]:
        plt.tight_layout()
        plt.show()
    if save_show_or_return in ["return", "all"]:
        return g


def plot_direct_graph(
    adata: AnnData,
    layout: None = None,
    figsize: Tuple[float, float] = (6, 4),
    save_show_or_return: Literal["save", "show", "return"] = "show",
    save_kwargs: Dict[str, Any] = {},
) -> None:
    """Not implemented."""

    df_mat = adata.uns["df_mat"]

    import matplotlib.pyplot as plt
    import networkx as nx

    edge_color = "gray"

    G = nx.from_pandas_edgelist(
        df_mat,
        source="source",
        target="target",
        edge_attr="weight",
        create_using=nx.DiGraph(),
    )
    G.nodes()
    W = []
    for n, nbrs in G.adj.items():
        for nbr, eattr in nbrs.items():
            W.append(eattr["weight"])

    options = {
        "width": 300,
        "arrowstyle": "-|>",
        "arrowsize": 1000,
    }

    plt.figure(figsize=figsize)
    if layout is None:
        #     pos : dictionary, optional
        #        A dictionary with nodes as keys and positions as values.
        #        If not specified a spring layout positioning will be computed.
        #        See :py:mod:`networkx.drawing.layout` for functions that
        #        compute node positions.

        g = nx.draw(
            G,
            with_labels=True,
            node_color="skyblue",
            node_size=100,
            edge_color=edge_color,
            width=W / np.max(W) * 5,
            edge_cmap=plt.cm.Blues,
            options=options,
        )
    else:
        raise Exception("layout", layout, " is not supported.")

    if save_show_or_return in ["save", "both", "all"]:
        s_kwargs = {
            "path": None,
            "prefix": "plot_direct_graph",
            "dpi": None,
            "ext": "pdf",
            "transparent": True,
            "close": True,
            "verbose": True,
        }
        s_kwargs = update_dict(s_kwargs, save_kwargs)

        if save_show_or_return in ["both", "all"]:
            s_kwargs["close"] = False

        save_fig(**s_kwargs)
    if save_show_or_return in ["show", "both", "all"]:
        plt.tight_layout()
        plt.show()
    if save_show_or_return in ["return", "all"]:
        return g
