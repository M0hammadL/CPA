# Copyright (c) Facebook, Inc. and its affiliates. All Rights Reserved

import json
import os
import pprint
import re
import sys
import time
from os import listdir, walk
from os.path import isfile, join
from pathlib import Path

import cpa.plotting as pl
import numpy as np
import pandas as pd
import scanpy as sc
import torch
from cpa.api import *
from cpa.data import Dataset, load_dataset_splits
from cpa.model import CPA
from cpa.plotting import *
from cpa.train import evaluate, prepare_cpa


class DatasetSpecs:
    """
    Specification for pretty plotting of the datasets used in the study.
    """

    def __init__(self, model_name):
        self.model_name = model_name
        self.plot_ref = True
        if "GSM" in model_name:
            self.perts_palette = {
                "BMS": "#999999",
                "SAHA": "#4daf4a",
                "Dex": "#377eb8",
                "Nutlin": "#e41a1c",
                "Vehicle": "#000000",
            }
            self.perturbations_pair = ["Nutlin", "BMS"]
            self.target_genes = ["MDM2"]
            self.selected_drugs = ["Nutlin", "BMS", "Dex", "SAHA"]
            self.selected_cov = "A549"

        if "pachter" in model_name:
            self.perts_palette = None
            self.perturbations_pair = ["EGF", "RA"]
            self.selected_cov = "unknown"
            self.plot_ref = False

        if "cross" in model_name:
            self.perts_palette = None
            self.perturbations_pair = None
            self.selected_cov = "mouse"
            self.target_genes = ["Car13", "Chac1", "Ncf1", "Nfkbiz", "Phlda1", "Rel"]
            self.plot_ref = False


def get_best_plots(model_name, path="./results/plots"):
    print("Start plotting for:", model_name)
    specs = DatasetSpecs(model_name)

    folder = f"{path}/{model_name.split('/')[-2]}/"
    Path(folder).mkdir(parents=True, exist_ok=True)
    plots_prefix = f"{folder}/{model_name.split('/')[-2]}_{model_name.split('/')[-1]}"
    print("Plots are saved to: ", plots_prefix + "_*")

    # load model weights
    state, args, history = torch.load(model_name, map_location=torch.device("cpu"))

    # Plot training history
    pretty_history = CPAHistory(history, fileprefix=plots_prefix)
    pretty_history.print_time()
    pretty_history.plot_losses()
    pretty_history.plot_metrics(epoch_min=100)

    # Load the dataset and model pre-trained weights
    autoencoder, datasets = prepare_cpa(args, state_dict=state)

    # Setting a variable for the API
    cpa_api = API(datasets, autoencoder)

    # Setting up a variabel for automatic plotting. The plots also could be
    # used separately.
    cpa_plots = CompertVisuals(
        cpa_api, fileprefix=plots_prefix, perts_palette=specs.perts_palette
    )

    # Plot latent space
    perts_anndata = cpa_api.get_drug_embeddings()
    covars_anndata = cpa_api.get_covars_embeddings()
    cpa_plots.plot_latent_embeddings(
        cpa_api.emb_perts, kind="perturbations", show_text=True
    )
    cpa_plots.plot_latent_embeddings(cpa_api.emb_covars, kind="covars")

    # Plot latent dose response
    latent_response = cpa_api.latent_dose_response(perturbations=None)
    cpa_plots.plot_contvar_response(
        latent_response,
        postfix="latent",
        var_name=cpa_api.perturbation_key,
        title_name="Latent dose response",
    )

    # Plot latent dose response 2D
    if not (specs.perturbations_pair is None):
        latent_dose_2D = cpa_api.latent_dose_response2D(
            specs.perturbations_pair, n_points=100
        )
        cpa_plots.plot_contvar_response2D(
            latent_dose_2D, postfix="latent2D", title_name="Latent dose-response"
        )

        reconstructed_response2D = cpa_api.get_response2D(
            datasets, specs.perturbations_pair, cpa_api.unique_сovars[0]
        )
        cpa_plots.plot_contvar_response2D(
            reconstructed_response2D,
            title_name="Reconstructed dose-response  2D",
            logdose=False,
            postfix="reconstructed-dose-response2D",
            # xlims=(-3, 0), ylims=(-3, 0)
        )
        cpa_plots.plot_contvar_response2D(
            reconstructed_response2D,
            title_name="Reconstructed dose-response 2D",
            logdose=True,
            postfix="log10-reconstructed-dose-response2D",
            xlims=(-3, 0),
            ylims=(-3, 0),
        )

        df_pred = pl.plot_uncertainty_comb_dose(
            cpa_api=cpa_api,
            cov=specs.selected_cov,
            pert=f"{specs.perturbations_pair[0]}+{specs.perturbations_pair[1]}",
            N=51,
            cond_key="treatment",
            filename=f"{cpa_plots.fileprefix}_uncertainty_{specs.perturbations_pair[0]}_{specs.perturbations_pair[1]}.png",
            metric="cosine",
        )
    uncert_list = []
    for i, drug in enumerate(specs.selected_drugs):
        uncert_list.append(
            pl.plot_uncertainty_dose(
                cpa_api,
                cov=specs.selected_cov,
                pert=drug,
                N=51,
                measured_points=cpa_api.measured_points["all"],
                cond_key="condition",
                log=True,
                metric="cosine",
                filename=f"{cpa_plots.fileprefix}_uncertainty_{drug}.png",
            )
        )
    df_uncert = pd.concat(uncert_list)

    selected_drug = specs.selected_drugs[0]
    logscale_labels = cpa_api.measured_points["all"][specs.selected_cov][
        selected_drug
    ]

    df_ref = get_reference_from_combo([selected_drug], datasets)
    df_ref["uncertainty_cosine"] = 0
    df_ref["uncertainty_eucl"] = 0
    df_ref["condition"] = selected_drug
    df_ref["log10-dose"] = [np.log10(float(d)) for d in df_ref[selected_drug].values]

    df_uncert["log10-dose"] = [np.log10(float(d)) for d in df_uncert["dose_val"].values]
    for unc in ["uncertainty_cosine", "uncertainty_eucl"]:
        pl.plot_dose_response(
            df_uncert,
            "log10-dose",
            "condition",
            xlabelname="log10-dose",
            df_ref=df_ref,
            response_name=unc,
            title_name="",
            use_ref_response=True,
            col_dict=cpa_plots.perts_palette,
            plot_vertical=False,
            f1=4,
            f2=3.3,
            logscale=logscale_labels,
            fname=f"{plots_prefix}_{unc}",
            bbox=(1.6, 1.0),
            fontsize=13,
            format="png",
        )

    # # Plot reconstructed dose response
    if specs.plot_ref:
        df_reference = cpa_api.get_response_reference(datasets)
        reconstructed_response = cpa_api.get_response(datasets)
        # df_reference = df_reference.replace('training_treated', 'train')
        for gene in specs.target_genes:
            cpa_plots.plot_contvar_response(
                reconstructed_response,
                df_ref=df_reference,
                postfix="reconstructed-dose-response",
                figsize=(4, 3.3),
                bbox=(1.6, 1.0),
                response_name=gene,
                xlabelname="dose",
                logdose=False,
                palette=cpa_plots.perts_palette,
                title_name="",
            )
            cpa_plots.plot_contvar_response(
                reconstructed_response,
                postfix="log10-reconstructed-dose-response",
                df_ref=df_reference,
                figsize=(4, 3.3),
                bbox=(1.6, 1.0),
                response_name=gene,
                xlabelname="log10-dose",
                logdose=True,
                palette=cpa_plots.perts_palette,
                measured_points=logscale_labels,
                title_name="",
            )


if __name__ == "__main__":
    mypath = "pretrained_models/"
    _, folders, _ = next(walk(mypath))
    for fold in folders:
        for f in listdir(join(mypath, fold)):
            model_name = join(mypath, fold, f)
            if (sys.argv[1] in model_name) and ("sweep" in model_name):
                get_best_plots(model_name)
