from tqdm import tqdm
import numpy as np
import pandas as pd
import scipy
from scipy import interpolate
from scipy.sparse import issparse, csr_matrix
from scipy.integrate import odeint, solve_ivp
import warnings


# ---------------------------------------------------------------------------------------------------
# others
def get_mapper():
    mapper = {
        "X_spliced": "M_s",
        "X_unspliced": "M_u",
        "X_new": "M_n",
        "X_old": "M_o",
        "X_total": "M_t",
        "X_uu": "M_uu",
        "X_ul": "M_ul",
        "X_su": "M_su",
        "X_sl": "M_sl",
        "X_protein": "M_p",
        "X": "X",
    }
    return mapper


def get_mapper_inverse():
    mapper = get_mapper()

    return dict([(v, k) for k, v in mapper.items()])


def get_finite_inds(X, ax=0):
    finite_inds = np.isfinite(X.sum(ax).A1) if issparse(X) else np.isfinite(X.sum(ax))

    return finite_inds


def update_dict(dict1, dict2):
    dict1.update((k, dict2[k]) for k in dict1.keys() & dict2.keys())

    return dict1


def closest_cell(coord, cells):
    cells = np.asarray(cells)
    dist_2 = np.sum((cells - coord) ** 2, axis=1)

    return np.argmin(dist_2)


def elem_prod(X, Y):
    if issparse(X):
        return X.multiply(Y)
    elif issparse(Y):
        return Y.multiply(X)
    else:
        return np.multiply(X, Y)


def norm_vector(x):
    """calculate euclidean norm for a row vector"""

    return np.sqrt(np.einsum('i, i -> ', x, x))


def norm_row(X):
    """calculate euclidean norm for each row of a matrix"""

    return np.sqrt(X.multiply(X).sum(1).A1 if issparse(X) else np.einsum('ij, ij -> i', X, X) if X.ndim > 1 else np.einsum('i, i -> ', X, X))


def einsum_correlation(X, Y_i, type="pearson"):
    """calculate pearson or cosine correlation between X (genes/pcs/embeddings x cells) and the velocity vectors Y_i for gene i"""

    if type == "pearson":
        X -= X.mean(axis=1)[:, None]
        Y_i -= np.nanmean(Y_i)
    elif type == "cosine":
        X, Y_i = X, Y_i

    X_norm, Y_norm = norm_row(X),  norm_vector(Y_i)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if Y_norm == 0:
            corr = np.zeros(X_norm.shape[0])
        else:
            corr = np.einsum('ij, j', X, Y_i) / (X_norm * Y_norm)[None, :]

    return corr

  
def form_triu_matrix(arr):
    '''
        Construct upper triangle matrix from an 1d array.
    '''
    n = int(np.ceil((np.sqrt(1 + 8 * len(arr)) - 1) * 0.5))
    M = np.zeros((n, n))
    c = 0
    for i in range(n):
        for j in range(n):
            if j >= i:
                if c < len(arr):
                    M[i, j] = arr[c]
                    c += 1
                else:
                    break
    return M

  
def moms2var(m1, m2):
    var = m2 - elem_prod(m1, m1)

    return var


def var2m2(var, m1):
    m2 = var + elem_prod(m1, m1)

    return m2

# ---------------------------------------------------------------------------------------------------
# dynamics related:
def one_shot_gamma_alpha(k, t, l):
    gamma = -np.log(1 - k) / t
    alpha = l * (gamma / k)[0]

    return gamma, alpha


def one_shot_k(gamma, t):
    k = 1 - np.exp(-gamma * t)
    return k

def one_shot_gamma_alpha_matrix(k, t, U):
    """Assume U is a sparse matrix and only tested on one-shot experiment"""
    Kc = np.clip(k, 0, 1 - 1e-3)
    gamma = -(np.log(1 - Kc) / t)
    alpha = U.multiply((gamma / k)[:, None])

    return gamma, alpha

def _one_shot_gamma_alpha_matrix(K, tau, N, R):
    """original code from Yan"""
    N, R = N.A.T, R.A.T
    K = np.array(K)
    tau = tau[0]
    Kc = np.clip(K, 0, 1-1e-3)
    if np.isscalar(tau):
        B = -np.log(1-Kc)/tau
    else:
        B = -(np.log(1-Kc)[None, :].T/tau).T
    return B, (elem_prod(B, N)/K).T - elem_prod(B, R).T


def compute_velocity_labeling_B(B, alpha, R):
    return (alpha - elem_prod(B, R.T).T)
# ---------------------------------------------------------------------------------------------------
# dynamics related:
def get_valid_inds(adata, filter_gene_mode):
    if filter_gene_mode == "final":
        valid_ind = adata.var.use_for_dynamo.values
    elif filter_gene_mode == "basic":
        valid_ind = adata.var.pass_basic_filter.values
    elif filter_gene_mode == "no":
        valid_ind = np.repeat([True], adata.shape[1])

    return valid_ind


def log_unnormalized_data(raw, log_unnormalized):
    if issparse(raw):
        raw.data = np.log(raw.data + 1) if log_unnormalized else raw.data
    else:
        raw = np.log(raw + 1) if log_unnormalized else raw

    return raw


def get_data_for_kin_params_estimation(
    subset_adata,
    model,
    use_moments,
    tkey,
    protein_names,
    log_unnormalized,
    NTR_vel,
):
    U, Ul, S, Sl, P, US, U2, S2, = (
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )  # U: unlabeled unspliced; S: unlabel spliced
    normalized, has_splicing, has_labeling, has_protein, assumption_mRNA = (
        False,
        False,
        False,
        False,
        None,
    )

    mapper = get_mapper()

    # labeling plus splicing
    if (
        np.all(
            ([i in subset_adata.layers.keys() for i in ["X_ul", "X_sl", "X_su"]])
        ) or np.all(
            ([mapper[i] in subset_adata.layers.keys() for i in ["X_ul", "X_sl", "X_su"]])
        )
    ):  # only uu, ul, su, sl provided
        has_splicing, has_labeling, normalized, assumption_mRNA = (
            True,
            True,
            True,
            "ss" if NTR_vel else 'kinetic',
        )
        U = subset_adata.layers[mapper["X_uu"]].T if use_moments \
            else subset_adata.layers["X_uu"].T # unlabel unspliced: U

        Ul = subset_adata.layers[mapper["X_ul"]].T if use_moments \
            else subset_adata.layers["X_ul"].T

        Sl = subset_adata.layers[mapper["X_sl"]].T if use_moments \
            else subset_adata.layers["X_sl"].T

        S = subset_adata.layers[mapper["X_su"]].T if use_moments \
            else subset_adata.layers["X_su"].T # unlabel spliced: S

    elif np.all(
            ([i in subset_adata.layers.keys() for i in ["uu", "ul", "sl", "su"]])
    ):
        has_splicing, has_labeling, normalized, assumption_mRNA = (
            True,
            True,
            False,
            "ss" if NTR_vel else 'kinetic',
        )
        raw, raw_uu = subset_adata.layers["uu"].T, subset_adata.layers["uu"].T
        U = log_unnormalized_data(raw, log_unnormalized)

        raw, raw_ul = subset_adata.layers["ul"].T, subset_adata.layers["ul"].T
        Ul = log_unnormalized_data(raw, log_unnormalized)

        raw, raw_sl = subset_adata.layers["sl"].T, subset_adata.layers["sl"].T
        Sl = log_unnormalized_data(raw, log_unnormalized)

        raw, raw_su = subset_adata.layers["su"].T, subset_adata.layers["su"].T
        S = log_unnormalized_data(raw, log_unnormalized)

    # labeling without splicing
    if (
        ("X_new" in subset_adata.layers.keys() and not use_moments)
        or (mapper["X_new"] in subset_adata.layers.keys() and use_moments)
    ):  # run new / total ratio (NTR)
        has_labeling, normalized, assumption_mRNA = (
            True,
            True,
            "ss" if NTR_vel else 'kinetic',
        )
        U = (
            subset_adata.layers[mapper["X_total"]].T
            - subset_adata.layers[mapper["X_new"]].T
            if use_moments
            else subset_adata.layers["X_total"].T - subset_adata.layers["X_new"].T
        )
        Ul = (
            subset_adata.layers[mapper["X_new"]].T
            if use_moments
            else subset_adata.layers["X_new"].T
        )
    elif "new" in subset_adata.layers.keys():
        has_labeling, assumption_mRNA = (
            True,
            "ss" if NTR_vel else 'kinetic',
        )
        raw, raw_new, old = (
            subset_adata.layers["new"].T,
            subset_adata.layers["new"].T,
            subset_adata.layers["total"].T - subset_adata.layers["new"].T,
        )
        if issparse(raw):
            raw.data = np.log(raw.data + 1) if log_unnormalized else raw.data
            old.data = np.log(old.data + 1) if log_unnormalized else old.data
        else:
            raw = np.log(raw + 1) if log_unnormalized else raw
            old = np.log(old + 1) if log_unnormalized else old
        U = old
        Ul = raw

    # splicing data
    if (
        ("X_unspliced" in subset_adata.layers.keys() and not use_moments)
        or (mapper["X_unspliced"] in subset_adata.layers.keys() and use_moments)
    ):
        has_splicing, normalized, assumption_mRNA = True, True, "kinetic" \
            if tkey in subset_adata.obs.columns else 'ss'
        U = (
            subset_adata.layers[mapper["X_unspliced"]].T
            if use_moments
            else subset_adata.layers["X_unspliced"].T
        )
    elif "unspliced" in subset_adata.layers.keys():
        has_splicing, assumption_mRNA = True, "kinetic" \
            if tkey in subset_adata.obs.columns else 'ss'
        raw, raw_unspliced = (
            subset_adata.layers["unspliced"].T,
            subset_adata.layers["unspliced"].T,
        )
        if issparse(raw):
            raw.data = np.log(raw.data + 1) if log_unnormalized else raw.data
        else:
            raw = np.log(raw + 1) if log_unnormalized else raw
        U = raw
    if (
        ("X_spliced" in subset_adata.layers.keys() and not use_moments)
        or (mapper["X_spliced"] in subset_adata.layers.keys() and use_moments)
    ):
        S = (
            subset_adata.layers[mapper["X_spliced"]].T
            if use_moments
            else subset_adata.layers["X_spliced"].T
        )
    elif "spliced" in subset_adata.layers.keys():
        raw, raw_spliced = (
            subset_adata.layers["spliced"].T,
            subset_adata.layers["spliced"].T,
        )
        if issparse(raw):
            raw.data = np.log(raw.data + 1) if log_unnormalized else raw.data
        else:
            raw = np.log(raw + 1) if log_unnormalized else raw
        S = raw

    ind_for_proteins = None
    if (
        ("X_protein" in subset_adata.obsm.keys() and not use_moments)
        or (mapper["X_protein"] in subset_adata.obsm.keys() and use_moments)
    ):
        P = (
            subset_adata.obsm[mapper["X_protein"]].T
            if use_moments
            else subset_adata.obsm["X_protein"].T
        )
    elif "protein" in subset_adata.obsm.keys():
        P = subset_adata.obsm["protein"].T
    if P is not None:
        has_protein = True
        if protein_names is None:
            warnings.warn(
                "protein layer exists but protein_names is not provided. No estimation will be performed for protein data."
            )
        else:
            protein_names = list(
                set(subset_adata.var.index).intersection(protein_names)
            )
            ind_for_proteins = [
                np.where(subset_adata.var.index == i)[0][0] for i in protein_names
            ]
            subset_adata.var["is_protein_velocity_genes"] = False
            subset_adata.var.loc[ind_for_proteins, "is_protein_velocity_genes"] = True

    experiment_type = "conventional"

    if has_labeling:
        if tkey is None:
            warnings.warn(
                "dynamo finds that your data has labeling, but you didn't provide a `tkey` for"
                "metabolic labeling experiments, so experiment_type is set to be `one-shot`."
            )
            experiment_type = "one-shot"
            t = np.ones_like(subset_adata.n_obs)
        elif tkey in subset_adata.obs.columns:
            t = np.array(subset_adata.obs[tkey], dtype="float")
            if len(np.unique(t)) == 1:
                experiment_type = "one-shot"
            else:
                labeled_sum = U.sum(0) if Ul is None else Ul.sum(0)
                xx, yy = labeled_sum.A1 if issparse(U) else labeled_sum, t
                xm, ym = np.mean(xx), np.mean(yy)
                cov = np.mean(xx * yy) - xm * ym
                var_x = np.mean(xx * xx) - xm * xm

                k = cov / var_x

                # total labeled RNA amount will increase (decrease) in kinetic (degradation) experiments over time.
                experiment_type = "kin" if k > 0 else "deg"
        else:
            raise Exception(
                "the tkey ", tkey, " provided is not a valid column name in .obs."
            )
        if model == "stochastic" and all(
            [x in subset_adata.layers.keys() for x in ["M_tn", "M_nn", "M_tt"]]
        ):
            US, U2, S2 = (
                subset_adata.layers["M_tn"].T,
                subset_adata.layers["M_nn"].T if not has_splicing else None,
                subset_adata.layers["M_tt"].T if not has_splicing else None,
            )
    else:
        t = None
        if model == "stochastic":
            US, U2, S2 = subset_adata.layers["M_us"].T, subset_adata.layers["M_uu"].T, subset_adata.layers["M_ss"].T

    return (
        U,
        Ul,
        S,
        Sl,
        P,
        US,
        U2,
        S2,
        t,
        normalized,
        has_splicing,
        has_labeling,
        has_protein,
        ind_for_proteins,
        assumption_mRNA,
        experiment_type,
    )

def set_velocity(
    adata,
    vel_U,
    vel_S,
    vel_P,
    _group,
    cur_grp,
    cur_cells_bools,
    valid_ind,
    ind_for_proteins,
):
    if type(vel_U) is not float:
        if cur_grp == _group[0]:
            adata.layers["velocity_U"] = csr_matrix((adata.shape))
        tmp = csr_matrix((np.sum(cur_cells_bools), adata.shape[1]))
        tmp[:, valid_ind] = vel_U.T.tocsr() if issparse(vel_U) else csr_matrix(vel_U.T)
        adata.layers["velocity_U"][
            cur_cells_bools, :
        ] = tmp  # np.where(valid_ind)[0] required for sparse matrix
    if type(vel_S) is not float:
        if cur_grp == _group[0]:
            adata.layers["velocity_S"] = csr_matrix((adata.shape))
        tmp = csr_matrix((np.sum(cur_cells_bools), adata.shape[1]))
        tmp[:, valid_ind] = vel_S.T.tocsr() if issparse(vel_S) else csr_matrix(vel_S.T)
        adata.layers["velocity_S"][
            cur_cells_bools, :
        ] = tmp  # np.where(valid_ind)[0] required for sparse matrix
    if type(vel_P) is not float:
        if cur_grp == _group[0]:
            adata.obsm["velocity_P"] = csr_matrix(
                (adata.obsm["P"].shape[0], len(ind_for_proteins))
            )
        adata.obsm["velocity_P"][cur_cells_bools, :] = (
            vel_P.T.tocsr() if issparse(vel_P) else csr_matrix(vel_P.T)
        )

    return adata


def set_param_ss(
    adata,
    est,
    alpha,
    beta,
    gamma,
    eta,
    delta,
    experiment_type,
    _group,
    cur_grp,
    kin_param_pre,
    valid_ind,
    ind_for_proteins,
):
    if experiment_type is "mix_std_stm":
        if alpha is not None:
            if cur_grp == _group[0]:
                adata.varm[kin_param_pre + "alpha"] = np.zeros(
                    (adata.shape[1], alpha[1].shape[1])
                )
            adata.varm[kin_param_pre + "alpha"][valid_ind, :] = alpha[1]
            (
                adata.var[kin_param_pre + "alpha"],
                adata.var[kin_param_pre + "alpha_std"],
            ) = (None, None)
            (
                adata.var.loc[valid_ind, kin_param_pre + "alpha"],
                adata.var.loc[valid_ind, kin_param_pre + "alpha_std"],
            ) = (alpha[1][:, -1], alpha[0])

        if cur_grp == _group[0]:
            (
                adata.var[kin_param_pre + "beta"],
                adata.var[kin_param_pre + "gamma"],
                adata.var[kin_param_pre + "half_life"],
            ) = (None, None, None)

        adata.var.loc[valid_ind, kin_param_pre + "beta"] = beta
        adata.var.loc[valid_ind, kin_param_pre + "gamma"] = gamma
        adata.var.loc[valid_ind, kin_param_pre + "half_life"] = np.log(2) / gamma
    else:
        if alpha is not None:
            if len(alpha.shape) > 1:  # for each cell
                if cur_grp == _group[0]:
                    adata.varm[kin_param_pre + "alpha"] = (
                        csr_matrix(np.zeros(adata.shape[::-1]))
                        if issparse(alpha)
                        else np.zeros(adata.shape[::-1])
                    )  #
                adata.varm[kin_param_pre + "alpha"][valid_ind, :] = alpha  #
                adata.var.loc[valid_ind, kin_param_pre + "alpha"] = alpha.mean(1)
            elif len(alpha.shape) is 1:
                if cur_grp == _group[0]:
                    adata.var[kin_param_pre + "alpha"] = None
                adata.var.loc[valid_ind, kin_param_pre + "alpha"] = alpha

        if cur_grp == _group[0]:
            (
                adata.var[kin_param_pre + "beta"],
                adata.var[kin_param_pre + "gamma"],
                adata.var[kin_param_pre + "half_life"],
            ) = (None, None, None)
        adata.var.loc[valid_ind, kin_param_pre + "beta"] = beta
        adata.var.loc[valid_ind, kin_param_pre + "gamma"] = gamma
        adata.var.loc[valid_ind, kin_param_pre + "half_life"] = None if gamma is None else np.log(2) / gamma

        (
            alpha_intercept,
            alpha_r2,
            beta_k,
            gamma_k,
            gamma_intercept,
            gamma_r2,
            gamma_logLL,
            delta_intercept,
            delta_r2,
            uu0,
            ul0,
            su0,
            sl0,
            U0,
            S0,
            total0,
        ) = est.aux_param.values()
        if alpha_r2 is not None:
            alpha_r2[~np.isfinite(alpha_r2)] = 0
        if cur_grp == _group[0]:
            (
                adata.var[kin_param_pre + "alpha_b"],
                adata.var[kin_param_pre + "alpha_r2"],
                adata.var[kin_param_pre + "gamma_b"],
                adata.var[kin_param_pre + "gamma_r2"],
                adata.var[kin_param_pre + "gamma_logLL"],
                adata.var[kin_param_pre + "delta_b"],
                adata.var[kin_param_pre + "delta_r2"],
                adata.var[kin_param_pre + "uu0"],
                adata.var[kin_param_pre + "ul0"],
                adata.var[kin_param_pre + "su0"],
                adata.var[kin_param_pre + "sl0"],
                adata.var[kin_param_pre + "U0"],
                adata.var[kin_param_pre + "S0"],
                adata.var[kin_param_pre + "total0"],
            ) = (
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            )

        adata.var.loc[valid_ind, kin_param_pre + "alpha_b"] = alpha_intercept
        adata.var.loc[valid_ind, kin_param_pre + "alpha_r2"] = alpha_r2

        if gamma_r2 is not None:
            gamma_r2[~np.isfinite(gamma_r2)] = 0
        adata.var.loc[valid_ind, kin_param_pre + "gamma_b"] = gamma_intercept
        adata.var.loc[valid_ind, kin_param_pre + "gamma_r2"] = gamma_r2
        adata.var.loc[valid_ind, kin_param_pre + "gamma_logLL"] = gamma_logLL

        adata.var.loc[valid_ind, kin_param_pre + "uu0"] = uu0
        adata.var.loc[valid_ind, kin_param_pre + "ul0"] = ul0
        adata.var.loc[valid_ind, kin_param_pre + "su0"] = su0
        adata.var.loc[valid_ind, kin_param_pre + "sl0"] = sl0
        adata.var.loc[valid_ind, kin_param_pre + "U0"] = U0
        adata.var.loc[valid_ind, kin_param_pre + "S0"] = S0
        adata.var.loc[valid_ind, kin_param_pre + "total0"] = total0

        if experiment_type == 'one-shot':
            adata.var[kin_param_pre + "beta_k"] = None
            adata.var[kin_param_pre + "gamma_k"] = None
            adata.var.loc[valid_ind, kin_param_pre + "beta_k"] = beta_k
            adata.var.loc[valid_ind, kin_param_pre + "gamma_k"] = gamma_k

        if ind_for_proteins is not None:
            delta_r2[~np.isfinite(delta_r2)] = 0
            if cur_grp == _group[0]:
                (
                    adata.var[kin_param_pre + "eta"],
                    adata.var[kin_param_pre + "delta"],
                    adata.var[kin_param_pre + "delta_b"],
                    adata.var[kin_param_pre + "delta_r2"],
                    adata.var[kin_param_pre + "p_half_life"],
                ) = (None, None, None, None, None)
            adata.var.loc[valid_ind, kin_param_pre + "eta"][ind_for_proteins] = eta
            adata.var.loc[valid_ind, kin_param_pre + "delta"][ind_for_proteins] = delta
            adata.var.loc[valid_ind, kin_param_pre + "delta_b"][
                ind_for_proteins
            ] = delta_intercept
            adata.var.loc[valid_ind, kin_param_pre + "delta_r2"][
                ind_for_proteins
            ] = delta_r2
            adata.var.loc[valid_ind, kin_param_pre + "p_half_life"][
                ind_for_proteins
            ] = (np.log(2) / delta)

    return adata


def set_param_kinetic(
    adata,
    alpha,
    a,
    b,
    alpha_a,
    alpha_i,
    beta,
    gamma,
    cost,
    logLL,
    kin_param_pre,
    extra_params,
    _group,
    cur_grp,
    valid_ind,
):
    if cur_grp == _group[0]:
        (
            adata.var[kin_param_pre + "alpha"],
            adata.var[kin_param_pre + "a"],
            adata.var[kin_param_pre + "b"],
            adata.var[kin_param_pre + "alpha_a"],
            adata.var[kin_param_pre + "alpha_i"],
            adata.var[kin_param_pre + "beta"],
            adata.var[kin_param_pre + "p_half_life"],
            adata.var[kin_param_pre + "gamma"],
            adata.var[kin_param_pre + "half_life"],
            adata.var[kin_param_pre + "cost"],
            adata.var[kin_param_pre + "logLL"],
        ) = (None, None, None, None, None, None, None, None, None, None, None)

    adata.var.loc[valid_ind, kin_param_pre + "alpha"] = alpha
    adata.var.loc[valid_ind, kin_param_pre + "a"] = a
    adata.var.loc[valid_ind, kin_param_pre + "b"] = b
    adata.var.loc[valid_ind, kin_param_pre + "alpha_a"] = alpha_a
    adata.var.loc[valid_ind, kin_param_pre + "alpha_i"] = alpha_i
    adata.var.loc[valid_ind, kin_param_pre + "beta"] = beta
    adata.var.loc[valid_ind, kin_param_pre + "gamma"] = gamma
    adata.var.loc[valid_ind, kin_param_pre + "half_life"] = np.log(2) / gamma
    adata.var.loc[valid_ind, kin_param_pre + "cost"] = cost
    adata.var.loc[valid_ind, kin_param_pre + "logLL"] = logLL
    # add extra parameters (u0, uu0, etc.)
    extra_params.columns = [kin_param_pre + i for i in extra_params.columns]
    extra_params = extra_params.set_index(adata.var.index[valid_ind])
    var = pd.concat((adata.var, extra_params), axis=1, sort=False)
    adata.var = var

    return adata


def get_U_S_for_velocity_estimation(
    subset_adata, use_moments, has_splicing, has_labeling, log_unnormalized, NTR
):
    mapper = get_mapper()

    if has_splicing:
        if has_labeling:
            if "X_uu" in subset_adata.layers.keys():  # unlabel spliced: S
                if use_moments:
                    uu, ul, su, sl = (
                        subset_adata.layers[mapper["X_uu"]].T,
                        subset_adata.layers[mapper["X_ul"]].T,
                        subset_adata.layers[mapper["X_su"]].T,
                        subset_adata.layers[mapper["X_sl"]].T,
                    )
                else:
                    uu, ul, su, sl = (
                        subset_adata.layers["X_uu"].T,
                        subset_adata.layers["X_ul"].T,
                        subset_adata.layers["X_su"].T,
                        subset_adata.layers["X_sl"].T,
                    )
            else:
                uu, ul, su, sl = (
                    subset_adata.layers["uu"].T,
                    subset_adata.layers["ul"].T,
                    subset_adata.layers["su"].T,
                    subset_adata.layers["sl"].T,
                )
                if issparse(uu):
                    uu.data = np.log(uu.data + 1) if log_unnormalized else uu.data
                    ul.data = np.log(ul.data + 1) if log_unnormalized else ul.data
                    su.data = np.log(su.data + 1) if log_unnormalized else su.data
                    sl.data = np.log(sl.data + 1) if log_unnormalized else sl.data
                else:
                    uu = np.log(uu + 1) if log_unnormalized else uu
                    ul = np.log(ul + 1) if log_unnormalized else ul
                    su = np.log(su + 1) if log_unnormalized else su
                    sl = np.log(sl + 1) if log_unnormalized else sl
            U, S = (ul + sl, uu + ul + su + sl) if NTR else (uu + ul, su + sl)
            # U, S = (ul + sl, uu + ul + su + sl) if NTR else (ul, sl)
        else:
            if ("X_unspliced" in subset_adata.layers.keys()) or (
                mapper["X_unspliced"] in subset_adata.layers.keys()
            ):  # unlabel spliced: S
                if use_moments:
                    U, S = (
                        subset_adata.layers[mapper["X_unspliced"]].T,
                        subset_adata.layers[mapper["X_spliced"]].T,
                    )
                else:
                    U, S = (
                        subset_adata.layers["X_unspliced"].T,
                        subset_adata.layers["X_spliced"].T,
                    )
            else:
                U, S = (
                    subset_adata.layers["unspliced"].T,
                    subset_adata.layers["spliced"].T,
                )
                if issparse(U):
                    U.data = np.log(U.data + 1) if log_unnormalized else U.data
                    S.data = np.log(S.data + 1) if log_unnormalized else S.data
                else:
                    U = np.log(U + 1) if log_unnormalized else U
                    S = np.log(S + 1) if log_unnormalized else S
    else:
        if ("X_new" in subset_adata.layers.keys()) or (
            mapper["X_new"] in subset_adata.layers.keys()
        ):  # run new / total ratio (NTR)
            if use_moments:
                U = subset_adata.layers[mapper["X_new"]].T
                S = (
                    subset_adata.layers[mapper["X_total"]].T
                    if NTR
                    else subset_adata.layers[mapper["X_total"]].T
                    - subset_adata.layers[mapper["X_new"]].T
                )
            else:
                U = subset_adata.layers["X_new"].T
                S = (
                    subset_adata.layers["X_total"].T
                    if NTR
                    else subset_adata.layers["X_total"].T
                    - subset_adata.layers["X_new"].T
                )
        elif "new" in subset_adata.layers.keys():
            U = subset_adata.layers["new"].T
            S = (
                subset_adata.layers["total"].T
                if NTR
                else subset_adata.layers["total"].T - subset_adata.layers["new"].T
            )
            if issparse(U):
                U.data = np.log(U.data + 1) if log_unnormalized else U.data
                S.data = np.log(S.data + 1) if log_unnormalized else S.data
            else:
                U = np.log(U + 1) if log_unnormalized else U
                S = np.log(S + 1) if log_unnormalized else S

    return U, S

# ---------------------------------------------------------------------------------------------------
# estimation related
def lhsclassic(n_samples, n_dim):
    # From PyDOE
    # Generate the intervals
    cut = np.linspace(0, 1, n_samples + 1)

    # Fill points uniformly in each interval
    u = np.random.rand(n_samples, n_dim)
    a = cut[:n_samples]
    b = cut[1 : n_samples + 1]
    rdpoints = np.zeros(u.shape)
    for j in range(n_dim):
        rdpoints[:, j] = u[:, j] * (b - a) + a

    # Make the random pairings
    H = np.zeros(rdpoints.shape)
    for j in range(n_dim):
        order = np.random.permutation(range(n_samples))
        H[:, j] = rdpoints[order, j]

    return H

def calc_R2(X, Y, k, f=lambda X, k: np.einsum('ij,i -> ij', X, k)):
    """calculate R-square. X, Y: n_species (mu, sigma) x n_obs"""
    if X.ndim == 1:
        X = X[None]
    if Y.ndim == 1:
        Y = Y[None]
    if np.isscalar(k):
        k = np.array([k])
    
    Y_bar = np.mean(Y, 1)
    d = Y.T - Y_bar
    SS_tot = np.sum(np.einsum('ij,ij -> i', d, d))

    F = f(X, k)
    d = F - Y
    SS_res = np.sum(np.einsum('ij,ij -> j', d, d))

    return 1 - SS_res/SS_tot


def norm_loglikelihood(x, mu, sig):
    """Calculate log-likelihood for the data.
    """
    err = (x - mu) / sig
    ll = -len(err)/2*np.log(2*np.pi) - np.sum(np.log(sig)) - 0.5*err.dot(err.T)
    return np.sum(ll, 0)


def calc_norm_loglikelihood(X, Y, k, f=lambda X, k: np.einsum('ij,i -> ij', X, k)):
    """calculate log likelihood based on normal distribution. X, Y: n_species (mu, sigma) x n_obs"""
    if X.ndim == 1:
        X = X[None]
    if Y.ndim == 1:
        Y = Y[None]
    if np.isscalar(k):
        k = np.array([k])

    n = X.shape[0]
    F = f(X, k)

    d = F - Y
    sig = np.einsum('ij,ij -> i', d, d)

    LogLL = 0
    for i in range(Y.shape[0]):
        LogLL += norm_loglikelihood(Y[i], F[i], np.sqrt(sig[i] / n))

    return LogLL
# ---------------------------------------------------------------------------------------------------
# velocity related


def find_extreme(s, u, normalize=True, perc_left=None, perc_right=None):
    if normalize:
        su = s / np.clip(np.max(s), 1e-3, None)
        su += u / np.clip(np.max(u), 1e-3, None)
    else:
        su = s + u

    if perc_left is None:
        mask = su >= np.percentile(su, 100 - perc_right, axis=0)
    elif perc_right is None:
        mask = np.ones_like(su, dtype=bool)
    else:
        left, right = np.percentile(su, [perc_left, 100 - perc_right], axis=0)
        mask = (su <= left) | (su >= right)

    return mask


def set_velocity_genes(
    adata,
    vkey="velocity_S",
    min_r2=0.01,
    min_alpha=0,
    min_gamma=0,
    min_delta=0,
    use_for_dynamo=True,
):
    layer = vkey.split("_")[1]

    if layer is "U":
        if 'alpha_r2' not in adata.var.columns: adata.var['alpha_r2'] = None
        if np.all(adata.var.alpha_r2.values == None):
            adata.var.alpha_r2 = 1
        adata.var["use_for_velocity"] = (
            (adata.var.alpha > min_alpha)
            & (adata.var.alpha_r2 > min_r2)
            & adata.var.use_for_dynamo
            if use_for_dynamo
            else (adata.var.alpha > min_alpha) & (adata.var.alpha_r2 > min_r2)
        )
    elif layer is "S":
        if 'gamma_r2' not in adata.var.columns: adata.var['gamma_r2'] = None
        if np.all(adata.var.gamma_r2.values == None): adata.var.gamma_r2 = 1
        adata.var["use_for_velocity"] = (
            (adata.var.gamma > min_gamma)
            & (adata.var.gamma_r2 > min_r2)
            & adata.var.use_for_dynamo
            if use_for_dynamo
            else (adata.var.gamma > min_gamma) & (adata.var.gamma_r2 > min_r2)
        )
    elif layer is "P":
        if 'delta_r2' not in adata.var.columns: adata.var['delta_r2'] = None
        if np.all(adata.var.delta_r2.values == None):
            adata.var.delta_r2 = 1
        adata.var["use_for_velocity"] = (
            (adata.var.delta > min_delta)
            & (adata.var.delta_r2 > min_r2)
            & adata.var.use_for_dynamo
            if use_for_dynamo
            else (adata.var.delta > min_delta) & (adata.var.delta_r2 > min_r2)
        )

    return adata


def get_ekey_vkey_from_adata(adata):
    dynamics_key = [i for i in adata.uns.keys() if i.endswith("dynamics")][0]
    experiment_type, use_smoothed = (
        adata.uns[dynamics_key]["experiment_type"],
        adata.uns[dynamics_key]["use_smoothed"],
    )
    has_splicing, has_labeling = (
        adata.uns[dynamics_key]["has_splicing"],
        adata.uns[dynamics_key]["has_labeling"],
    )
    NTR = adata.uns[dynamics_key]["NTR_vel"]

    mapper = get_mapper()
    layer = []

    if has_splicing:
        if has_labeling:
            if "X_uu" in adata.layers.keys():  # unlabel spliced: S
                if use_smoothed:
                    uu, ul, su, sl = (
                        adata.layers[mapper["X_uu"]],
                        adata.layers[mapper["X_ul"]],
                        adata.layers[mapper["X_su"]],
                        adata.layers[mapper["X_sl"]],
                    )
                    ul, sl = (ul + sl, uu + ul + su + sl) if NTR else (ul + uu, sl + su)
                    adata.layers["M_U"], adata.layers["M_S"] = ul, sl
                else:
                    uu, ul, su, sl = (
                        adata.layers["X_uu"],
                        adata.layers["X_ul"],
                        adata.layers["X_su"],
                        adata.layers["X_sl"],
                    )
                    ul, sl = (ul + sl, uu + ul + su + sl) if NTR else (ul + uu, sl + su)
                    adata.layers["X_U"], adata.layers["X_S"] = ul, sl
            else:
                raise Exception(
                    "The input data you have is not normalized/log trnasformed or smoothed and normalized/log trnasformed!"
                )

            if experiment_type == "kin":
                ekey, vkey, layer = (
                    ("M_U", "velocity_U", "X_U")
                    if use_smoothed
                    else ("X_U", "velocity_U", "X_U")
                )
            elif experiment_type == "deg":
                ekey, vkey, layer = (
                    ("M_S", "velocity_S", "X_S")
                    if use_smoothed
                    else ("X_S", "velocity_S", "X_S")
                )
            elif experiment_type == "one_shot":
                ekey, vkey, layer = (
                    ("M_U", "velocity_U", "X_U")
                    if use_smoothed
                    else ("X_U", "velocity_U", "X_U")
                )
            elif experiment_type == "mix_std_stm":
                ekey, vkey, layer = (
                    ("M_U", "velocity_U", "X_U")
                    if use_smoothed
                    else ("X_U", "velocity_U", "X_U")
                )
        else:
            if ("X_unspliced" in adata.layers.keys()) or (
                mapper["X_unspliced"] in adata.layers.keys()
            ):  # unlabel spliced: S
                if use_smoothed:
                    ul, sl = mapper["X_unspliced"], mapper["X_spliced"]
                else:
                    ul, sl = "X_unspliced", "X_spliced"
            else:
                raise Exception(
                    "The input data you have is not normalized/log trnasformed or smoothed and normalized/log trnasformed!"
                )
            ekey, vkey, layer = (
                ("M_s", "velocity_S", "X_spliced")
                if use_smoothed
                else ("X_spliced", "velocity_S", "X_spliced")
            )
    else:
        # use_smoothed: False
        if ("X_new" in adata.layers.keys()) or (
            mapper["X_new"] in adata.layers.keys
        ):  # run new / total ratio (NTR)
            # we may also create M_U, M_S layers?
            if experiment_type == "kin":
                ekey, vkey, layer = (
                    (mapper["X_new"], "velocity_U", "X_new")
                    if use_smoothed
                    else ("X_new", "velocity_U", "X_new")
                )
            elif experiment_type == "deg":
                ekey, vkey, layer = (
                    (mapper["X_total"], "velocity_S", "X_total")
                    if use_smoothed
                    else ("X_total", "velocity_S", "X_total")
                )
            elif experiment_type == "one-shot" or experiment_type == "one_shot":
                ekey, vkey, layer = (
                    (mapper["X_total"], "velocity_S", "X_total")
                    if use_smoothed
                    else ("X_total", "velocity_S", "X_total")
                )
            elif experiment_type == "mix_std_stm":
                ekey, vkey, layer = (
                    (mapper["X_new"], "velocity_U", "X_new")
                    if use_smoothed
                    else ("X_new", "velocity_U", "X_new")
                )

        elif "new" in adata.layers.keys():
            raise Exception(
                "The input data you have is not normalized/log trnasformed or smoothed and normalized/log trnasformed!"
            )

    return ekey, vkey, layer

# ---------------------------------------------------------------------------------------------------
# cell velocities related
def get_iterative_indices(indices, index, n_recurse_neighbors=2, max_neighs=None):
    # These codes are borrowed from scvelo. Need to be rewritten later.
    def iterate_indices(indices, index, n_recurse_neighbors):
        if n_recurse_neighbors > 1:
            index = iterate_indices(indices, index, n_recurse_neighbors - 1)
        ix = np.append(index, indices[index])
        if np.isnan(ix).any():
            ix = ix[~np.isnan(ix)]
        return ix.astype(int)

    indices = np.unique(iterate_indices(indices, index, n_recurse_neighbors))
    if max_neighs is not None and len(indices) > max_neighs:
        indices = np.random.choice(indices, max_neighs, replace=False)
    return indices


def append_iterative_neighbor_indices(indices, n_recurse_neighbors=2, max_neighs=None):
    indices_rec = []
    for i in range(indices.shape[0]):
        neig = get_iterative_indices(indices, i, n_recurse_neighbors, max_neighs)
        indices_rec.append(neig)
    return indices_rec

def split_velocity_graph(G, neg_cells_trick=True):
    """split velocity graph (built either with correlation or with cosine kernel
     into one positive graph and one negative graph"""

    if not issparse(G): G = csr_matrix(G)
    if neg_cells_trick: G_ = G.copy()
    G.data[G.data < 0] = 0
    G.eliminate_zeros()

    if neg_cells_trick:
        G_.data[G_.data > 0] = 0
        G_.eliminate_zeros()

        return (G, G_)
    else:
        return G

# ---------------------------------------------------------------------------------------------------
# vector field related
def integrate_vf(
    init_states, t, args, integration_direction, f, interpolation_num=None, average=True
):
    """integrating along vector field function"""

    n_cell, n_feature, n_steps = (
        init_states.shape[0],
        init_states.shape[1],
        len(t) if interpolation_num is None else interpolation_num,
    )

    if n_cell > 1:
        if integration_direction == "both":
            if average:
                avg = np.zeros((n_steps * 2, n_feature))
        else:
            if average:
                avg = np.zeros((n_steps, n_feature))

    Y = None
    if interpolation_num is not None:
        valid_ids = None
    for i in tqdm(range(n_cell), desc="integrating vector field"):
        y0 = init_states[i, :]
        if integration_direction == "forward":
            y = scipy.integrate.odeint(lambda x, t: f(x), y0, t, args=args)
            t_trans = t
        elif integration_direction == "backward":
            y = scipy.integrate.odeint(lambda x, t: f(x), y0, -t, args=args)
            t_trans = -t
        elif integration_direction == "both":
            y_f = scipy.integrate.odeint(lambda x, t: f(x), y0, t, args=args)
            y_b = scipy.integrate.odeint(lambda x, t: f(x), y0, -t, args=args)
            y = np.hstack((y_b[::-1, :], y_f))
            t_trans = np.hstack((-t[::-1], t))

            if interpolation_num is not None:
                interpolation_num = interpolation_num * 2
        else:
            raise Exception(
                "both, forward, backward are the only valid direction argument strings"
            )

        if interpolation_num is not None:
            vids = np.where((np.diff(y.T) < 1e-3).sum(0) < y.shape[1])[0]
            valid_ids = vids if valid_ids is None else list(set(valid_ids).union(vids))

        Y = y if Y is None else np.vstack((Y, y))

    if interpolation_num is not None:
        valid_t_trans = t_trans[valid_ids]

        _t, _Y = None, None
        for i in range(n_cell):
            cur_Y = Y[i : (i + 1) * len(t_trans), :][valid_ids, :]
            t_linspace = np.linspace(
                valid_t_trans[0], valid_t_trans[-1], interpolation_num
            )
            f = interpolate.interp1d(valid_t_trans, cur_Y.T)
            _Y = f(t_linspace) if _Y is None else np.hstack((_Y, f(t_linspace)))
            _t = t_linspace if _t is None else np.hstack((_t, t_linspace))

        t, Y = _t, _Y.T

    if n_cell > 1 and average:
        t_len = int(len(t) / n_cell)
        for i in range(t_len):
            avg[i, :] = np.mean(Y[np.arange(n_cell) * t_len + i, :], 0)
        Y = avg

    return t, Y


def integrate_vf_ivp(
    init_states, t, args, integration_direction, f, interpolation_num=None, average=True
):
    """integrating along vector field function using the initial value problem solver from scipy.integrate"""

    n_cell, n_feature = init_states.shape
    max_step = np.abs(t[-1] - t[0]) / 2500

    T, Y, SOL = [], [], []

    for i in tqdm(range(n_cell), desc="integration with ivp solver"):
        y0 = init_states[i, :]
        ivp_f, ivp_f_event = (
            lambda t, x: f(x),
            lambda t, x: np.sum(np.linalg.norm(f(x)) < 1e-5) - 1,
        )
        ivp_f_event.terminal = True

        print("\nintegrating cell ", i, "; Expression: ", init_states[i, :])
        if integration_direction == "forward":
            y_ivp = solve_ivp(
                ivp_f,
                [t[0], t[-1]],
                y0,
                events=ivp_f_event,
                args=args,
                max_step=max_step,
                dense_output=True,
            )
            y, t_trans, sol = y_ivp.y, y_ivp.t, y_ivp.sol
        elif integration_direction == "backward":
            y_ivp = solve_ivp(
                ivp_f,
                [-t[0], -t[-1]],
                y0,
                events=ivp_f_event,
                args=args,
                max_step=max_step,
                dense_output=True,
            )
            y, t_trans, sol = y_ivp.y, y_ivp.t, y_ivp.sol
        elif integration_direction == "both":
            y_ivp_f = solve_ivp(
                ivp_f,
                [t[0], t[-1]],
                y0,
                events=ivp_f_event,
                args=args,
                max_step=max_step,
                dense_output=True,
            )
            y_ivp_b = solve_ivp(
                ivp_f,
                [-t[0], -t[-1]],
                y0,
                events=ivp_f_event,
                args=args,
                max_step=max_step,
                dense_output=True,
            )
            y, t_trans = (
                np.hstack((y_ivp_b.y[::-1, :], y_ivp_f.y)),
                np.hstack((y_ivp_b.t[::-1], y_ivp_f.t)),
            )
            sol = [y_ivp_b.sol, y_ivp_f.sol]

            if interpolation_num is not None:
                interpolation_num = interpolation_num * 2
        else:
            raise Exception(
                "both, forward, backward are the only valid direction argument strings"
            )

        T.extend(t_trans)
        Y.append(y)
        SOL.append(sol)

        print("\nintegration time: ", len(t_trans))

    valid_t_trans = np.unique(T)

    _Y = None
    if integration_direction == "both":
        neg_t_len = sum(valid_t_trans < 0)
    for i in range(n_cell):
        cur_Y = (
            SOL[i](valid_t_trans)
            if integration_direction != "both"
            else np.hstack(
                (
                    SOL[i][0](valid_t_trans[:neg_t_len]),
                    SOL[i][1](valid_t_trans[neg_t_len:]),
                )
            )
        )
        _Y = cur_Y if _Y is None else np.hstack((_Y, cur_Y))

    t, Y = valid_t_trans, _Y

    if n_cell > 1 and average:
        t_len = int(len(t) / n_cell)
        avg = np.zeros((n_feature, t_len))

        for i in range(t_len):
            avg[:, i] = np.mean(Y[:, np.arange(n_cell) * t_len + i], 1)
        Y = avg

    return t, Y.T


def integrate_streamline(
    X, Y, U, V, integration_direction, init_states, interpolation_num=250, average=True
):
    """use streamline's integrator to alleviate stacking of the solve_ivp. Need to update with the correct time."""
    import matplotlib.pyplot as plt

    n_cell = init_states.shape[0]

    res = np.zeros((n_cell * interpolation_num, 2))
    j = -1  # this index will become 0 when the first trajectory found

    for i in tqdm(range(n_cell), "integration with streamline"):
        strm = plt.streamplot(
            X,
            Y,
            U,
            V,
            start_points=init_states[i, None],
            integration_direction=integration_direction,
            density=100,
        )
        strm_res = np.array(strm.lines.get_segments()).reshape((-1, 2))

        if len(strm_res) == 0:
            continue
        else:
            j += 1
        t = np.arange(strm_res.shape[0])
        t_linspace = np.linspace(t[0], t[-1], interpolation_num)
        f = interpolate.interp1d(t, strm_res.T)

        cur_rng = np.arange(j * interpolation_num, (j + 1) * interpolation_num)
        res[cur_rng, :] = f(t_linspace).T

    res = res[: cur_rng[-1], :]  # remove all empty trajectories
    n_cell = int(res.shape[0] / interpolation_num)

    if n_cell > 1 and average:
        t_len = len(t_linspace)
        avg = np.zeros((t_len, 2))

        for i in range(t_len):
            cur_rng = np.arange(n_cell) * t_len + i
            avg[i, :] = np.mean(res[cur_rng, :], 0)

        res = avg

    plt.close()

    return t_linspace, res


# ---------------------------------------------------------------------------------------------------
# fate related
def fetch_exprs(adata, basis, layer, genes, time, mode, project_back_to_high_dim):
    import pandas as pd

    if basis is not None:
        fate_key = "fate_" + basis
    else:
        fate_key = "fate" if layer == "X" else "fate_" + layer

    time = (
        adata.obs[time].values
        if mode is not "vector_field"
        else adata.uns[fate_key]["t"]
    )

    if mode is not "vector_field":
        valid_genes = list(set(genes).intersection(adata.var.index))

        if layer is "X":
            exprs = adata[np.isfinite(time), :][:, valid_genes].X
        elif layer in adata.layers.keys():
            exprs = adata[np.isfinite(time), :][:, valid_genes].layers[layer]
        elif layer is "protein":  # update subset here
            exprs = adata[np.isfinite(time), :][:, valid_genes].obsm[layer]
        else:
            raise Exception(
                f"The {layer} you passed in is not existed in the adata object."
            )
    else:
        fate_genes = adata.uns[fate_key]["genes"]
        valid_genes = list(set(genes).intersection(fate_genes))

        if basis is not None:
            if project_back_to_high_dim:
                exprs = adata.uns[fate_key]["high_prediction"]
                exprs = exprs[np.isfinite(time), pd.Series(fate_genes).isin(valid_genes)]
            else:
                exprs = adata.uns[fate_key]["prediction"][np.isfinite(time), :]
                valid_genes = [basis + "_" + str(i) for i in np.arange(exprs.shape[1])]
        else:
            exprs = adata.uns[fate_key]["prediction"][np.isfinite(time), pd.Series(fate_genes).isin(valid_genes)]

    time = time[np.isfinite(time)]

    return exprs, valid_genes, time


def fetch_states(adata, init_states, init_cells, basis, layer, average, t_end):
    if init_states is None and init_cells is None:
        raise Exception("Either init_state or init_cells should be provided.")
    elif init_states is None and init_cells is not None:
        if type(init_cells) == str:
            init_cells = [init_cells]
        intersect_cell_names = sorted(
            set(init_cells).intersection(adata.obs_names),
            key=lambda x: list(init_cells).index(x),
        )
        _cell_names = (
            init_cells if len(intersect_cell_names) == 0 else intersect_cell_names
        )

        if basis is not None:
            init_states = adata[_cell_names].obsm["X_" + basis].copy()
            if len(_cell_names) == 1:
                init_states = init_states.reshape((1, -1))
            VecFld = adata.uns["VecFld_" + basis]["VecFld"]
            X = adata.obsm["X_" + basis]

            valid_genes = [
                basis + "_" + str(i) for i in np.arange(init_states.shape[1])
            ]
        else:
            # valid_genes = list(set(genes).intersection(adata.var_names[adata.var.use_for_velocity]) if genes is not None \
            #     else adata.var_names[adata.var.use_for_velocity]
            # ----------- enable the function to only only a subset genes -----------

            vf_key = "VecFld" if layer == "X" else "VecFld_" + layer
            valid_genes = adata.uns[vf_key]["genes"]
            init_states = (
                adata[_cell_names, :][:, valid_genes].X
                if layer == "X"
                else adata[_cell_names, :][:, valid_genes].layers[layer]
            )
            if issparse(init_states):
                init_states = init_states.A
            if len(_cell_names) == 1:
                init_states = init_states.reshape((1, -1))

            if layer == "X":
                VecFld = adata.uns["VecFld"]["VecFld"]
                X = adata[:, valid_genes].X
            else:
                VecFld = adata.uns["VecFld_" + layer]["VecFld"]
                X = adata[:, valid_genes].layers[layer]

    if init_states.shape[0] > 1 and average in ["origin", True]:
        init_states = init_states.mean(0).reshape((1, -1))

    if t_end is None:
        xmin, xmax = X.min(0), X.max(0)
        t_end = np.max(xmax - xmin) / np.min(np.abs(VecFld["V"]))

    if issparse(init_states):
        init_states = init_states.A

    return init_states, VecFld, t_end, valid_genes


# ---------------------------------------------------------------------------------------------------
# arc curve related
def remove_redundant_points_trajectory(X, tol=1e-4, output_discard=False):
    """remove consecutive data points that are too close to each other."""
    X = np.atleast_2d(X)
    discard = np.zeros(len(X), dtype=bool)
    if X.shape[0] > 1:
        for i in range(len(X) - 1):
            dist = np.linalg.norm(X[i + 1] - X[i])
            if dist < tol:
                discard[i + 1] = True
        X = X[~discard]

    arclength = 0

    x0 = X[0]
    for i in range(1, len(X)):
        tangent = X[i] - x0 if i == 1 else X[i] - X[i - 1]
        d = np.linalg.norm(tangent)

        arclength += d

    if output_discard:
        return X, arclength, discard
    else:
        return X, arclength


def arclength_sampling(X, step_length, t=None):
    """uniformly sample data points on an arc curve that generated from vector field predictions."""
    Y = []
    x0 = X[0]
    T = [] if t is not None else None
    t0 = t[0] if t is not None else None
    i = 1
    terminate = False
    arclength = 0

    while i < len(X) - 1 and not terminate:
        l = 0
        for j in range(i, len(X) - 1):
            tangent = X[j] - x0 if j == i else X[j] - X[j - 1]
            d = np.linalg.norm(tangent)
            if l + d >= step_length:
                x = x0 if j == i else X[j - 1]
                y = x + (step_length - l) * tangent / d
                if t is not None:
                    tau = t0 if j == i else t[j - 1]
                    tau += (step_length - l) / d * (t[j] - tau)
                    T.append(tau)
                    t0 = tau
                Y.append(y)
                x0 = y
                i = j
                break
            else:
                l += d
        arclength += step_length
        if l + d < step_length:
            terminate = True

    if T is not None:
        return np.array(Y), arclength, T
    else:
        return np.array(Y), arclength
