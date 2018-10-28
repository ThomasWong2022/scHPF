#!/usr/bin/env python

import numpy as np
from scipy.sparse import coo_matrix
from scipy.misc import logsumexp
from scipy.special import digamma, gammaln, psi
from sklearn.base import BaseEstimator

# TODO warn if can't import, and allow computation with slow
from schpf.hpf_numba import *


class HPF_Gamma(object):
    """Gamma variational distributions

    Parameters
    ----------
    vi_shape: np.ndarray
        Gamma shape parameter for the variational Gamma distributions.
        Ndarray.shape[0] must match `vi_rate`
    vi_rate: np.ndarray
        Gamma rate parameter for the variational Gamma distributions.
        Ndarray.shape[0] must match `vi_shape`
    """

    @staticmethod
    def random_gamma_factory(dims, shape_prior, rate_prior):
        """Factory method to randomly initialize variational distributions

        Parameters
        ----------
        dims: list-like
            Numpy-style shape of the matrix of Gammas.
        shape_prior: float
            Prior for variational Gammas' shapes.  Must be greater than 0.
        rate_prior: float
            Prior for variational Gammas' rates.  Must be greater than 0.

        Returns
        -------
            A randomly initialized HPF_Gamma instance
        """
        vi_shape = np.random.uniform(0.5 * shape_prior, 1.5 * shape_prior,
                                     dims).astype('float64')
        vi_rate  = np.random.uniform(0.5 * rate_prior, 1.5 * rate_prior,
                                     dims).astype('float64')
        return HPF_Gamma(vi_shape,vi_rate)


    def __init__(self, vi_shape, vi_rate):
        """Initializes HPF_Gamma with variational shape and rates"""
        assert(vi_shape.shape == vi_rate.shape)
        assert(np.all(vi_shape > 0))
        assert(np.all(vi_rate > 0))
        self.vi_shape = vi_shape
        self.vi_rate = vi_rate
        self.dims = vi_shape.shape


    @property
    def e_x(self):
        """Expected value of the random variable(s) given variational
        distribution(s)
        """
        return self.vi_shape / self.vi_rate


    @property
    def e_logx(self):
        """Expectation of the log of random variable given variational
        distribution(s)"""
        return digamma(self.vi_shape) - np.log(self.vi_rate)


    @property
    def entropy(self):
        """Entropy of variational Gammas"""
        return  self.vi_shape - np.log(self.vi_rate) \
                + gammaln(self.vi_shape) \
                + (1 - self.vi_shape) * digamma(self.vi_shape)


    def sample(self, nsamples=1):
        """Sample from variational distributions

        Parameters
        ----------
        nsamples: int (optional, default 1)
            Number of samples to take.

        Returns
        -------
        X_rep : np.ndarray
            An ndarray of samples from the variational distributions, where
            the last dimension is the number of samples `nsamples`
        """
        samples = []
        for i in range(nsamples):
            samples.append(np.random.gamma(self.vi_shape, 1/self.vi_rate).T)
        return np.stack(samples).T


class scHPF(BaseEstimator):
    """HPF as described in ____

    Parameters
    ----------
    nfactors: int
        Number of factors (K)
    a: float, (optional, default 0.3)
        Hyperparameter a
    ap: float (optional, default 1.0)
        Hyperparameter a'
    bp: float (optional, default None)
        Hyperparameter b'. Set empirically from observed data if not
        given.
    c: float, (optional, default 0.3)
        Hyperparameter c
    cp: float (optional, default 1.0)
        Hyperparameter c'
    dp: float (optional, default None)
        Hyperparameter d'. Set empirically from observed data if not
        given.
    min_iter: int (optional, default 30):
        Minimum number of interations for training.
    max_iter: int (optional, default 1000):
        Maximum number of interations for training.
    check_freq: int (optional, default 10)
        Number of training iterations between calculating loss.
    epsilon: float (optional, default 0.001)
        Percent change of loss for convergence.
    better_than_n_ago: int (optional, default 5)
        Stop condition if loss is getting worse.  Stops training if loss
        is worse than `better_than_n_ago`*`check_freq` training steps
        ago and getting worse.
    xi: HPF_Gamma (optional, default None)
        Variational distributions for xi
    theta: HPF_Gamma (optional, default None)
        Variational distributions for theta
    eta: HPF_Gamma (optional, default None)
        Variational distributions for eta
    beta: HPF_Gamma (optional, default None)
        Variational distributions for beta
    """
    def __init__(
            self,
            nfactors,
            a=0.3,
            ap=1,
            bp=None,
            c=0.3,
            cp=1,
            dp=None,
            min_iter=30,
            max_iter=1000,
            check_freq=10,
            epsilon=0.001,
            better_than_n_ago=5,
            xi=None,
            theta=None,
            eta=None,
            beta=None,
            loss=None
            ):
        """Initialize HPF instance"""
        self.nfactors = nfactors
        self.a = a
        self.ap = ap
        self.bp = bp
        self.c = c
        self.cp = cp
        self.dp = dp
        self.min_iter = min_iter
        self.max_iter = max_iter
        self.check_freq = check_freq
        self.epsilon = epsilon
        self.better_than_n_ago = better_than_n_ago

        self.xi = None
        self.eta = None
        self.theta = None
        self.beta = None

        self.loss = []


    def cell_score(self, xi=None, theta=None):
        """Get cell score from xi and theta

        Properties
        ----------
        xi : HPF_Gamma, (optional, default self.xi)
            varitional distributions for xi
        theta : HPF_Gamma, (optional, default self.theta)
            varitional distributions for theta

        Returns
        -------
        cell_score : ndarray
            ncell x nfactor array of cell scores
        """
        xi = self.xi if xi is None else xi
        theta = self.theta if theta is None else theta
        return self._score(xi, theta)


    def gene_score(self, eta=None, beta=None):
        """Get cell score from eta and beta

        Parameters
        ----------
        eta : HPF_Gamma, (optional, default self.eta)
            varitional distributions for eta
        beta : HPF_Gamma, (optional, default self.beta)
            varitional distributions for beta

        Returns
        -------
        gene_score : ndarray
            ngene x nfactor array of cell scores
        """
        eta = self.eta if eta is None else eta
        beta = self.beta if beta is None else beta
        return self._score(eta, beta)


    def pois_llh_pointwise(self, X, theta=None, beta=None):
        """Poisson log-likelihood (for each nonzero data)

        Attempt to use numba/cffi/gsl, use numpy otherwise

        Parameters
        ----------
        X: coo_matrix
            Data to compute Poisson log likelihood of. Assumed to be nonzero.

        Returns
        -------
        llh: ndarray
        """
        theta = self.theta if theta is None else theta
        beta = self.beta if beta is None else beta
        try:
            llh = compute_pois_llh(X.data, X.row, X.col,
                                   theta.vi_shape, theta.vi_rate,
                                   beta.vi_shape, beta.vi_rate)
        except NameError:
            e_rate = (theta.e_x[X.row] *  beta.e_x[X.col]).sum(axis=1)
            llh = X.data * np.log(e_rate) - e_rate - gammaln(X.data + 1)
        return llh


    def pois_llh(self, X, **params):
        """Convenience method for total llh """
        return np.sum(self.pois_llh_pointwise(X, **params))


    def fit(self, X, validation_data=None, **params):
        """Fit an scHPF model

        Parameters
        ----------
        X: coo_matrix
            Data to fit
        validation_data: coo_matrix, (optional, default None)
            validation data, train data used if not given
        """
        (bp, dp, xi, eta, theta, beta) = self._fit(X,
                validation_data=validation_data, **params)
        self.bp = bp
        self.dp = dp
        self.xi = xi
        self.eta = eta
        self.theta = theta
        self.beta = beta
        return self


    # TODO copy self but with new values for new data
    def project(self, X, validation_data=None, min_iter=10):
        """Get bp,xi and theta for new data while fixing gene scores"""
        (bp, _, xi, _, theta, _) = self._fit(X,
                validation_data=validation_data, freeze_genes=True)
        return bp, xi, theta


    def _score(self, capacity, loading):
        return loading.e_x * capacity.e_x[:,None]


    def _fit(self, X, validation_data=None, freeze_genes=False, reinit=True,
            verbose=True, min_iter=None, message_function=None):
        """Combined internal fit/transform function

        Parameters
        ----------
        X: coo_matrix
            Data to fit
        validation_data: coo_matrix, (optional, default None)
            validation data, train data used if not given
        freeze_genes: bool, (optional, default False)
            Should we update gene variational distributions eta and beta
        reinit: bool, (optional, default True)
            Randomly initialize variational distributions even if they
            already exist. Superseded by freeze_genes.
        verbose: bool (optional, default True)
            Print messages at each check_freq
        min_iter: int (optional, default None)
            replaces self.min_iter if given
        message_function : function  (optional, default None)
            A function that takes arguments theta, beta, and t and, if
            given, is called at check_interval. Intended use is
            to check additional stats during training, potentially with
            hardcoded data, but is unrestricted.  Use at own risk.

        Returns
        -------
        bp: float
            Empirically set value for bp
        dp: float
            Empirically set value for dp. Unchanged if freeze_genes.
        xi: HPF_Gamma
            Learned variational distributions for xi
        eta: HPF_Gamma
            Learned variational distributions for eta. Unchanged if
            freeze_genes.
        theta: HPF_Gamma
            Learned variational distributions for theta
        beta: HPF_Gamma
            Learned variational distributions for beta. Unchanged if
            freeze_genes.
        """
        # local (convenience) vars for model
        nfactors, (ncells, ngenes) = self.nfactors, X.shape
        (a, ap, c, cp) = (self.a, self.ap, self.c, self.cp)

        # get empirically set hyperparameters and variational distributions
        (bp, dp, xi, eta, theta, beta) = self._setup(X, freeze_genes, reinit)

        # Make first updates for hierarchical prior
        # (vi_shape is constant, but want to update full distribution)
        xi.vi_shape[:] = ap + nfactors * a
        xi.vi_rate = bp + theta.e_x.sum(1)
        if not freeze_genes:
            eta.vi_shape[:] = cp + nfactors * c
            eta.vi_rate = dp + beta.e_x.sum(1)

        pct_change = []
        min_iter = self.min_iter if min_iter is None else min_iter
        for t in range(self.max_iter):
            if t==0 and reinit: #randomize phi for first iteration
                random_phi = np.random.dirichlet( 0.25*np.ones(nfactors),
                        X.data.shape[0])
                Xphi_data = X.data[:,None] * random_phi
            else:
                Xphi_data = compute_Xphi_data(X.data, X.row, X.col,
                                            theta.vi_shape, theta.vi_rate,
                                            beta.vi_shape, beta.vi_rate)

            # gene updates (if not frozen)
            if not freeze_genes:
                beta.vi_shape = compute_loading_shape_update(Xphi_data, X.col,
                        ngenes, c)
                beta.vi_rate = compute_loading_rate_update(eta.vi_shape,
                        eta.vi_rate, theta.vi_shape, theta.vi_rate)
                eta.vi_rate = dp + beta.e_x.sum(1)

            # cell updates
            theta.vi_shape = compute_loading_shape_update(Xphi_data, X.row,
                                                          ncells, a)
            theta.vi_rate = compute_loading_rate_update(xi.vi_shape, xi.vi_rate,
                    beta.vi_shape, beta.vi_rate)
            xi.vi_rate = bp + theta.e_x.sum(1)


            # record llh/percent change and check for convergence
            if t % self.check_freq == 0:
                # chech llh
                vX = validation_data if validation_data is not None else X
                curr = -self.pois_llh(vX, theta=theta, beta=beta)
                curr /= vX.data.shape[0]
                self.loss.append(curr)

                # calculate percent change
                try:
                    prev = self.loss[-2]
                    pct_change.append(100 * (curr - prev) / np.abs(prev))
                except IndexError:
                    pct_change.append(100)
                if verbose:
                    msg = '[Iter. {0: >4}]  loss:{1:.6f}  pct:{2:.9f}'.format(
                            t, curr, pct_change[-1])
                    print(msg)
                if message_function is not None:
                    message_function(theta, beta, t)


                # check convergence
                if len(self.loss) > 3 and t >= min_iter:
                    # convergence conditions (all must be met)
                    current_small = np.abs(pct_change[-1]) < self.epsilon
                    prev_small = np.abs(pct_change[-2]) < self.epsilon
                    not_inflection = not (
                            (np.abs(self.loss[-3]) < np.abs(prev)) \
                            and (np.abs(prev) > np.abs(curr)))
                    converged = current_small and prev_small and not_inflection
                    if converged:
                        print('converged')
                        break

                    # getting worse, and has been for better_than_n_ago checks
                    # (don't waste time on a bad run)
                    if len(self.loss) > self.better_than_n_ago \
                            and self.better_than_n_ago:
                        nprev = self.loss[-self.better_than_n_ago]
                        worse_than_n_ago = np.abs(nprev) < np.abs(curr)
                        getting_worse = np.abs(prev) < np.abs(curr)
                        if worse_than_n_ago and getting_worse:
                            print('getting worse break')
                            break


            # TODO message or warning or something
            if t >= self.max_iter:
                break

        return (bp, dp, xi, eta, theta, beta)


    def _setup(self, X, freeze_genes=False, reinit=True, clip=True):
        """Setup variational distributions"""
        # locals for convenience
        nfactors, (ncells, ngenes) = self.nfactors, X.shape
        a, ap, c, cp = self.a, self.ap, self.c, self.cp
        bp, dp = self.bp, self.dp

        xi, eta, theta, beta = (self.xi, self.eta, self.theta, self.beta)

        # empirically set bp and dp
        def mean_var_ratio(X, axis):
            axis_sum = X.sum(axis=axis)
            return np.mean(axis_sum) / np.var(axis_sum)
        if bp is None:
            bp = ap * mean_var_ratio(X, axis=1)
        if dp is None: # dp first in case of error
            if freeze_genes:
                msg = 'dp is None and cannot  dp when freeze_genes is True.'
                raise ValueError(msg)
            else:
                dp = cp *  mean_var_ratio(X, axis=0)
                if clip and bp > 1000 * dp:
                    old_val = dp
                    dp = bp / 1000
                    print('Clipping dp: was {} now {}'.format(old_val, dp))

        if reinit or (xi is None):
            xi = HPF_Gamma.random_gamma_factory((ncells,), ap, bp)
        if reinit or (theta is None):
            theta = HPF_Gamma.random_gamma_factory((ncells,nfactors), a, bp)

        # Check if variational distributions for genes exist, create if not
        # Error if freeze_genes and eta and beta don't exists
        if freeze_genes:
            if eta is None or beta is None:
                msg = 'To fit with frozen gene variational distributions ' \
                    + '(`freeze_genes`==True), eta and beta must be set to ' \
                    + 'valid HPF_Gamma instances.'
                raise ValueError(msg)
        else:
            if reinit or (eta is None):
                eta = HPF_Gamma.random_gamma_factory((ngenes,), cp, dp)
            if reinit or (beta is None):
                beta = HPF_Gamma.random_gamma_factory((ngenes,nfactors),
                        c, dp)

        return (bp, dp, xi, eta, theta, beta)


    def _initialize(self, X, freeze_genes=False):
        """Shortcut to setup random distributions without fitting"""
        (bp, dp, xi, eta, theta, beta) = self._setup(X, freeze_genes,
                reinit=True)
        self.bp = bp
        self.dp = dp
        self.xi = xi
        self.eta = eta
        self.theta = theta
        self.beta = beta
