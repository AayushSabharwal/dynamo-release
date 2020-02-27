import numpy as np
from scipy.sparse import issparse
from .utils import vector_field_function, integrate_vf


def Fate(adata, init_cells, init_states=None, basis='pca', t_end=None, direction='both', average=False, VecFld_true=None, **kwargs):
    """Predict the historical and future cell transcriptomic states over arbitrary time scales by integrating vector field
    functions from one or a set of initial cell state(s).

    Parameters
    ----------
        adata: :class:`~anndata.AnnData`
            AnnData object that contains the reconstructed vector field function in the `uns` attribute.
        init_cells: `list` (default: None)
            Cell name or indices of the initial cell states for the historical or future cell state prediction with numerical integration.
            If the names in init_cells are not find in the adata.obs_name, it will be treated as cell indices and must be integers.
        init_states: `numpy.ndarray` or None (default: None)
            Initial cell states for the historical or future cell state prediction with numerical integration.
        basis: `str` (default: 'pca')
            The embedding data to use.
        query_cell_str: `str` or `List` (default: `root`)
            a string that will be used as arugments for the query method of the pandas data frame (obs.query(query_cell_str)).
        t_end: `float` (default None)
            The length of the time period from which to predict cell state forward or backward over time. This is used
            by the odeint function.
        direction: `string` (default: both)
            The direction to predict the cell fate. One of the `forward`, `backward` or `both` string.
        average: `bool` (default: False)
            A boolean flag to determine whether to smooth the trajectory by calculating the average cell state at each time
            step.
        VecFld_true: `function`
            The true ODE function, useful when the data is generated through simulation. Replace VecFld arugment when this has been set.
        kwargs:
            Additional parameters that will be passed into the fate function.

    Returns
    -------
        adata: :class:`~anndata.AnnData`
            AnnData object that is updated with the dictionary Fate (includes `t` and `prediction` keys) in uns attribute.
    """
    if init_cells is not None:
        intersect_cell_names = list(set(init_cells).intersection(adata.obs_names))
        if basis is 'X' and 'use_for_dynamo' in adata.var_keys():
            _init_states = init_states[:, adata.var.use_for_dynamo]
        else:
            _init_states = adata.obsm['X_' + basis][init_cells, :] if len(intersect_cell_names) == 0 else \
                adata[intersect_cell_names].obsm['X_' + basis].copy()

    if init_states is None: init_states = _init_states

    VecFld = adata.uns['VecFld']["VecFld"] if basis is 'X' else adata.uns['VecFld_' + basis]["VecFld"]

    if t_end is None:
        xmin, xmax = adata.obsm['X_pca'].min(0), adata.obsm['X_pca'].max(0)
        t_end = max(xmax - xmin) / np.min(np.abs(VecFld['V']))

    if issparse(init_states):
        init_states = init_states.A

    VecFld = adata.uns['VecFld']["VecFld"] if basis is 'X' else adata.uns['VecFld_' + basis]["VecFld"]
    t, prediction, avg = fate(VecFld, init_states, VecFld_true=VecFld_true, direction=direction, t_end=t_end, average=average, **kwargs)

    if VecFld_true is None:
        fate_key = 'Fate' if basis is 'X' else 'Fate_' + basis
        adata.uns[fate_key] = {'t': t, 'prediction': prediction}
    else:
        adata.uns["Fate_true"] = {'t': t, 'prediction': prediction}


def fate(VecFld, init_states, VecFld_true = None, t_end=1, step_size=None, direction='both', average=False):
    """Predict the historical and future cell transcriptomic states over arbitrary time scales by integrating vector field
    functions from one or a set of initial cell state(s).

    Arguments
    ---------
        VecFld: `function`
            Functional form of the vector field reconstructed from sparse single cell samples. It is applicable to the entire
            transcriptomic space.
        init_states: `numpy.ndarray`
            Initial cell states for the historical or future cell state prediction with numerical integration.
        VecFld_true: `function`
            The true ODE function, useful when the data is generated through simulation. Replace VecFld arugment when this has been set.
        t_end: `float` (default 1)
            The length of the time period from which to predict cell state forward or backward over time. This is used
            by the odeint function.
        step_size: `float` or None (default None)
            Step size for integrating the future or history cell state, used by the odeint function. By default it is None,
            and the step_size will be automatically calculated to ensure 250 total integration time-steps will be used.
        direction: `string` (default: both)
            The direction to predict the cell fate. One of the `forward`, `backward`or `both` string.
        average: `bool` (default: False)
            A boolean flag to determine whether to smooth the trajectory by calculating the average cell state at each time
            step.

    Returns
    -------
    t: `numpy.ndarray`
        The time at which the cell state are predicted.
    prediction: `numpy.ndarray`
        Predicted cells states at different time points. Row order corresponds to the element order in t. If init_states
        corresponds to multiple cells, the expression dynamics over time for each cell is concatenated by rows. That is,
        the final dimension of prediction is (len(t) * n_cells, n_features). n_cells: number of cells; n_features: number
        of genes or number of low dimensional embeddings. Of note, if the average is set to be True, the average cell state
        at each time point is calculated for all cells.
    """

    V_func = lambda x: vector_field_function(x=x, t=None, VecFld=VecFld) if VecFld_true is None else VecFld_true

    if step_size is None:
        t_linspace = np.linspace(0, t_end, 10**(max(int(np.log10(t_end)), 6)))
    else:
        t_linspace = np.arange(0, t_end + step_size, step_size)

    n_cell, n_feature, n_steps = init_states.shape[0], init_states.shape[1], len(t_linspace)

    if direction == 'both':
        prediction = np.zeros((n_cell * n_steps * 2, n_feature))

        avg = np.zeros((n_steps * 2, n_feature))

        for i in range(n_cell):
            t, y = integrate_vf(init_states[i, :], t_linspace, (), direction, V_func)
            prediction[(n_steps * i * 2):(n_steps * (i + 1) * 2), :] = y

            for j in range(len(t)):
                avg[j, :] = np.mean(prediction[np.array(range(n_cell)) * n_steps + i, :], 0)

    elif direction in ['forward', 'backward']:
        prediction = np.zeros((n_cell * n_steps, n_feature))
        avg = np.zeros((n_steps, n_feature))

        for i in range(n_cell):
            t, y = integrate_vf(init_states[i, :], t_linspace, {}, direction, V_func)
            prediction[(n_steps * i):(n_steps * (i + 1)), :] = y

            for j in range(len(t)):
                avg[j, :] = np.mean(prediction[np.array(range(n_cell)) * n_steps + i, :], 0)
    else:
        raise Exception('both, forward, backward are the only valid direction argument strings')

    if average: prediction = avg

    return t, prediction, avg

# def fate_(adata, time, direction = 'forward'):
#     from .moments import *
#     gene_exprs = adata.X
#     cell_num, gene_num = gene_exprs.shape
#
#
#     for i in range(gene_num):
#         params = {'a': adata.uns['dynamo'][i, "a"], \
#                   'b': adata.uns['dynamo'][i, "b"], \
#                   'la': adata.uns['dynamo'][i, "la"], \
#                   'alpha_a': adata.uns['dynamo'][i, "alpha_a"], \
#                   'alpha_i': adata.uns['dynamo'][i, "alpha_i"], \
#                   'sigma': adata.uns['dynamo'][i, "sigma"], \
#                   'beta': adata.uns['dynamo'][i, "beta"], \
#                   'gamma': adata.uns['dynamo'][i, "gamma"]}
#         mom = moments_simple(**params)
#         for j in range(cell_num):
#             x0 = gene_exprs[i, j]
#             mom.set_initial_condition(*x0)
#             if direction == "forward":
#                 gene_exprs[i, j] = mom.solve([0, time])
#             elif direction == "backward":
#                 gene_exprs[i, j] = mom.solve([0, - time])
#
#     adata.uns['prediction'] = gene_exprs
#     return adata
