import matplotlib.pyplot as plt
import shiny.experimental as x
from htmltools import div
from pathlib import Path
from shiny import App, reactive, render, ui

from .utils import filter_fig
from ..plot import streamline_plot
from ..prediction import perturbation


css_path = Path(__file__).parent / "styles.css"


def perturbation_web_app(input_adata):
    app_ui = x.ui.page_sidebar(
        x.ui.sidebar(
            ui.include_css(css_path),
            x.ui.accordion(
                x.ui.accordion_panel(
                    div("Perturbation", class_="bold-subtitle"),
                    ui.input_text("selected_genes", "Genes to perform perturbation: ", placeholder="e.g. GATA1"),
                    ui.input_text("emb_basis", "Basis from which the vector field is reconstructed: ", value="umap"),
                    ui.input_text(
                        "expression", "Expression value to encode the genetic perturbation: ", placeholder="e.g. -100"
                    ),
                    ui.input_action_button(
                        "activate_purterbation", "Run perturbation", class_="btn-primary"
                    ),
                    value="Perturbation",
                ),
                x.ui.accordion_panel(
                    div("Streamline Plot", class_="bold-subtitle"),
                    ui.input_text("color", "The key to color the cells: ", value="cell_type"),
                    ui.input_text(
                        "streamline_basis", "The perturbation output as the basis of plot: ", value="umap_perturbation"
                    ),
                    ui.input_action_button(
                        "activate_streamline_plot", "Streamline plot", class_="btn-primary"
                    ),
                    value="Streamline Plot",
                ),
                open=False,
            ),
            width=500,
        ),
        ui.div(
            div("Streamline Plot", class_="bold-subtitle"),
            x.ui.output_plot("perturbation_plot"),
        ),
    )

    def server(input, output, session):
        adata = input_adata.copy()

        @reactive.Effect
        @reactive.event(input.activate_purterbation)
        def run_purterbation():
            selected_genes = input.selected_genes().split(",")
            expression = [int(txt) for txt in input.expression().split(",")]

            perturbation(adata, selected_genes, expression, emb_basis=input.emb_basis())

        @output
        @render.plot()
        @reactive.event(input.activate_streamline_plot)
        def perturbation_plot():
            color = input.color().split(",")

            axes_list = streamline_plot(adata, color=color, basis=input.streamline_basis(), save_show_or_return="return")

            return filter_fig(plt.gcf())

    app = App(app_ui, server, debug=True)
    app.run()