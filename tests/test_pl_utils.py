from utils import *
import networkx as nx
import dynamo as dyn
import matplotlib.pyplot as plt
import copy

logger = LoggerManager.get_main_logger()


def test_scatter_contour(adata):
    dyn.pl.scatters(adata, layer="curvature", save_show_or_return="show", contour=True)
    dyn.pl.scatters(adata, layer="curvature", save_show_or_return="show", contour=True, calpha=1)


def test_circosPlot(adata):
    # genes from top acceleration rank
    selected_genes = ["hmgn2", "hmgb2a", "si:ch211-222l21.1", "mbpb", "h2afvb"]
    edges_list = dyn.vf.build_network_per_cluster(
        adata,
        cluster="Cell_type",
        cluster_names=None,
        genes=selected_genes,
        n_top_genes=1000,
        abs=True,
    )

    print(edges_list["Unknown"])
    network = nx.from_pandas_edgelist(
        edges_list["Unknown"].drop_duplicates().query("weight > 1e-5"),
        "regulator",
        "target",
        edge_attr="weight",
        create_using=nx.DiGraph(),
    )
    _network = copy.deepcopy(network)
    dyn.pl.circosPlot(
        adata,
        cluster="Cell_type",
        cluster_name="Unknown",
        edges_list=None,
        network=network,
        color="M_s",
        save_show_or_return="return",
    )

    for e in network.edges():
        assert network.edges[e]["weight"] == _network.edges[e]["weight"]
    dyn.pl.circosPlot(
        adata,
        cluster="Cell_type",
        cluster_name="Unknown",
        edges_list=None,
        network=network,
        color="M_s",
        save_show_or_return="return",
    )
    pass


def test_scatter_group_gamma():
    dyn.pl.scatters(
        viral_adata,
        basis=viral_adata.var_names.intersection(gene_list_df.index)[:5],
        x="M_s",
        y="M_u",
        color="coarse_cluster",
        group="coarse_cluster",
        add_group_gamma_fit=True,
    )


if __name__ == "__main__":
    # generate data if needed
    adata = gen_or_read_zebrafish_data()

    # To-do: use a fixture in future
    # test_space_simple1(adata)
    # test_scatter_contour(adata)
    print("adata shape:", adata.shape)
    test_circosPlot(adata)
