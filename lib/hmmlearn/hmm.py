# Hidden Markov Models
#
# Author: Ron Weiss <ronweiss@gmail.com>
#         Shiqiao Du <lucidfrontier.45@gmail.com>
# API changes: Jaques Grobler <jaquesgrobler@gmail.com>
# Modifications to create of the HMMLearn module: Gael Varoquaux
# More API changes: Sergei Lebedev <superbobry@gmail.com>
# Addition of PoissonHMM: Caleb Kemere <caleb.kemere@rice.edu>
# Modifications to PoissonHMM: Etienne Ackermann <era3@rice.edu>
# Addition of GammaHMM: Caleb Kemere and Joshua Chu <jpc6@rice.edu>

"""
The :mod:`hmmlearn.hmm` module implements hidden Markov models.
"""

import numpy as np
import sys
from scipy.special import logsumexp, digamma, polygamma
from sklearn import cluster
from sklearn.utils import check_random_state
from scipy.stats import multivariate_normal

from . import _utils
from .stats import (log_multivariate_normal_density,
                    log_multivariate_poisson_density,
                    log_marked_poisson_density,
                    mp_log_marked_poisson_density)
from .base import _BaseHMM
from .utils import iter_from_X_lengths, normalize, fill_covars

__all__ = ["GMMHMM",
           "GaussianHMM",
           "MultinomialHMM",
           "PoissonHMM",
           "MarkedPoissonHMM",
           "MultiprobeMarkedPoissonHMM"]

COVARIANCE_TYPES = frozenset(("spherical", "diag", "full", "tied"))
MIN_LIKELIHOOD = 1e-300
MIN_LOGLIKELIHOOD = -700

class GaussianHMM(_BaseHMM):
    """Hidden Markov Model with Gaussian emissions.

    Parameters
    ----------
    n_components : int
        Number of states.

    covariance_type : string, optional
        String describing the type of covariance parameters to
        use.  Must be one of

        * "spherical" --- each state uses a single variance value that
          applies to all features.
        * "diag" --- each state uses a diagonal covariance matrix.
        * "full" --- each state uses a full (i.e. unrestricted)
          covariance matrix.
        * "tied" --- all states use **the same** full covariance matrix.

        Defaults to "diag".

    min_covar : float, optional
        Floor on the diagonal of the covariance matrix to prevent
        overfitting. Defaults to 1e-3.

    startprob_prior : array, shape (n_components, ), optional
        Parameters of the Dirichlet prior distribution for
        :attr:`startprob_`.

    transmat_prior : array, shape (n_components, n_components), optional
        Parameters of the Dirichlet prior distribution for each row
        of the transition probabilities :attr:`transmat_`.

    means_prior, means_weight : array, shape (n_components, ), optional
        Mean and precision of the Normal prior distribtion for
        :attr:`means_`.

    covars_prior, covars_weight : array, shape (n_components, ), optional
        Parameters of the prior distribution for the covariance matrix
        :attr:`covars_`.

        If :attr:`covariance_type` is "spherical" or "diag" the prior is
        the inverse gamma distribution, otherwise --- the inverse Wishart
        distribution.

    algorithm : string, optional
        Decoder algorithm. Must be one of "viterbi" or`"map".
        Defaults to "viterbi".

    random_state: RandomState or an int seed, optional
        A random number generator instance.

    n_iter : int, optional
        Maximum number of iterations to perform.

    tol : float, optional
        Convergence threshold. EM will stop if the gain in log-likelihood
        is below this value.

    verbose : bool, optional
        When ``True`` per-iteration convergence reports are printed
        to :data:`sys.stderr`. You can diagnose convergence via the
        :attr:`monitor_` attribute.

    params : string, optional
        Controls which parameters are updated in the training
        process.  Can contain any combination of 's' for startprob,
        't' for transmat, 'm' for means and 'c' for covars. Defaults
        to all parameters.

    init_params : string, optional
        Controls which parameters are initialized prior to
        training.  Can contain any combination of 's' for
        startprob, 't' for transmat, 'm' for means and 'c' for covars.
        Defaults to all parameters.

    Attributes
    ----------
    n_features : int
        Dimensionality of the Gaussian emissions.

    monitor\_ : ConvergenceMonitor
        Monitor object used to check the convergence of EM.

    transmat\_ : array, shape (n_components, n_components)
        Matrix of transition probabilities between states.

    startprob\_ : array, shape (n_components, )
        Initial state occupation distribution.

    means\_ : array, shape (n_components, n_features)
        Mean parameters for each state.

    covars\_ : array
        Covariance parameters for each state.

        The shape depends on :attr:`covariance_type`::

            (n_components, )                        if "spherical",
            (n_features, n_features)                if "tied",
            (n_components, n_features)              if "diag",
            (n_components, n_features, n_features)  if "full"

    Examples
    --------
    >>> from hmmlearn.hmm import GaussianHMM
    >>> GaussianHMM(n_components=2)
    ...                             #doctest: +ELLIPSIS +NORMALIZE_WHITESPACE
    GaussianHMM(algorithm='viterbi',...
    """
    def __init__(self, n_components=1, covariance_type='diag',
                 min_covar=1e-3,
                 startprob_prior=1.0, transmat_prior=1.0,
                 means_prior=0, means_weight=0,
                 covars_prior=1e-2, covars_weight=1,
                 algorithm="viterbi", random_state=None,
                 n_iter=10, tol=1e-2, verbose=False,
                 params="stmc", init_params="stmc"):
        _BaseHMM.__init__(self, n_components,
                          startprob_prior=startprob_prior,
                          transmat_prior=transmat_prior, algorithm=algorithm,
                          random_state=random_state, n_iter=n_iter,
                          tol=tol, params=params, verbose=verbose,
                          init_params=init_params)

        self.covariance_type = covariance_type
        self.min_covar = min_covar
        self.means_prior = means_prior
        self.means_weight = means_weight
        self.covars_prior = covars_prior
        self.covars_weight = covars_weight

    @property
    def covars_(self):
        """Return covars as a full matrix."""
        return fill_covars(self._covars_, self.covariance_type,
                           self.n_components, self.n_features)

    @covars_.setter
    def covars_(self, covars):
        self._covars_ = np.asarray(covars).copy()

    def _check(self):
        super(GaussianHMM, self)._check()

        self.means_ = np.asarray(self.means_)
        self.n_features = self.means_.shape[1]

        if self.covariance_type not in COVARIANCE_TYPES:
            raise ValueError('covariance_type must be one of {}'
                             .format(COVARIANCE_TYPES))

        _utils._validate_covars(self._covars_, self.covariance_type,
                                self.n_components)

    def _init(self, X, lengths=None):
        super(GaussianHMM, self)._init(X, lengths=lengths)

        _, n_features = X.shape
        if hasattr(self, 'n_features') and self.n_features != n_features:
            raise ValueError('Unexpected number of dimensions, got %s but '
                             'expected %s' % (n_features, self.n_features))

        self.n_features = n_features
        if 'm' in self.init_params or not hasattr(self, "means_"):
            kmeans = cluster.KMeans(n_clusters=self.n_components,
                                    random_state=self.random_state)
            kmeans.fit(X)
            self.means_ = kmeans.cluster_centers_
        if 'c' in self.init_params or not hasattr(self, "covars_"):
            cv = np.cov(X.T) + self.min_covar * np.eye(X.shape[1])
            if not cv.shape:
                cv.shape = (1, 1)
            self._covars_ = \
                _utils.distribute_covar_matrix_to_match_covariance_type(
                    cv, self.covariance_type, self.n_components).copy()

    def _compute_log_likelihood(self, X):
        return log_multivariate_normal_density(
            X, self.means_, self._covars_, self.covariance_type)

    def _generate_sample_from_state(self, state, random_state=None):
        random_state = check_random_state(random_state)
        return random_state.multivariate_normal(
            self.means_[state], self.covars_[state]
        )

    def _initialize_sufficient_statistics(self):
        stats = super(GaussianHMM, self)._initialize_sufficient_statistics()
        stats['post'] = np.zeros(self.n_components)
        stats['obs'] = np.zeros((self.n_components, self.n_features))
        stats['obs**2'] = np.zeros((self.n_components, self.n_features))
        if self.covariance_type in ('tied', 'full'):
            stats['obs*obs.T'] = np.zeros((self.n_components, self.n_features,
                                           self.n_features))
        return stats

    def _accumulate_sufficient_statistics(self, stats, obs, framelogprob,
                                          posteriors, fwdlattice, bwdlattice):
        super(GaussianHMM, self)._accumulate_sufficient_statistics(
            stats, obs, framelogprob, posteriors, fwdlattice, bwdlattice)

        if 'm' in self.params or 'c' in self.params:
            stats['post'] += posteriors.sum(axis=0)
            stats['obs'] += np.dot(posteriors.T, obs)

        if 'c' in self.params:
            if self.covariance_type in ('spherical', 'diag'):
                stats['obs**2'] += np.dot(posteriors.T, obs ** 2)
            elif self.covariance_type in ('tied', 'full'):
                # posteriors: (nt, nc); obs: (nt, nf); obs: (nt, nf)
                # -> (nc, nf, nf)
                stats['obs*obs.T'] += np.einsum(
                    'ij,ik,il->jkl', posteriors, obs, obs)

    def _do_mstep(self, stats):
        super(GaussianHMM, self)._do_mstep(stats)

        means_prior = self.means_prior
        means_weight = self.means_weight

        # TODO: find a proper reference for estimates for different
        #       covariance models.
        # Based on Huang, Acero, Hon, "Spoken Language Processing",
        # p. 443 - 445
        denom = stats['post'][:, np.newaxis]
        if 'm' in self.params:
            self.means_ = ((means_weight * means_prior + stats['obs'])
                           / (means_weight + denom))

        if 'c' in self.params:
            covars_prior = self.covars_prior
            covars_weight = self.covars_weight
            meandiff = self.means_ - means_prior

            if self.covariance_type in ('spherical', 'diag'):
                cv_num = (means_weight * meandiff**2
                          + stats['obs**2']
                          - 2 * self.means_ * stats['obs']
                          + self.means_**2 * denom)
                cv_den = max(covars_weight - 1, 0) + denom
                self._covars_ = \
                    (covars_prior + cv_num) / np.maximum(cv_den, 1e-5)
                if self.covariance_type == 'spherical':
                    self._covars_ = np.tile(
                        self._covars_.mean(1)[:, np.newaxis],
                        (1, self._covars_.shape[1]))
            elif self.covariance_type in ('tied', 'full'):
                cv_num = np.empty((self.n_components, self.n_features,
                                  self.n_features))
                for c in range(self.n_components):
                    obsmean = np.outer(stats['obs'][c], self.means_[c])

                    cv_num[c] = (means_weight * np.outer(meandiff[c],
                                                         meandiff[c])
                                 + stats['obs*obs.T'][c]
                                 - obsmean - obsmean.T
                                 + np.outer(self.means_[c], self.means_[c])
                                 * stats['post'][c])
                cvweight = max(covars_weight - self.n_features, 0)
                if self.covariance_type == 'tied':
                    self._covars_ = ((covars_prior + cv_num.sum(axis=0)) /
                                     (cvweight + stats['post'].sum()))
                elif self.covariance_type == 'full':
                    self._covars_ = ((covars_prior + cv_num) /
                                     (cvweight + stats['post'][:, None, None]))


class MultinomialHMM(_BaseHMM):
    r"""Hidden Markov Model with multinomial (discrete) emissions

    Parameters
    ----------

    n_components : int
        Number of states.

    startprob_prior : array, shape (n_components, ), optional
        Parameters of the Dirichlet prior distribution for
        :attr:`startprob_`.

    transmat_prior : array, shape (n_components, n_components), optional
        Parameters of the Dirichlet prior distribution for each row
        of the transition probabilities :attr:`transmat_`.

    algorithm : string, optional
        Decoder algorithm. Must be one of "viterbi" or "map".
        Defaults to "viterbi".

    random_state: RandomState or an int seed, optional
        A random number generator instance.

    n_iter : int, optional
        Maximum number of iterations to perform.

    tol : float, optional
        Convergence threshold. EM will stop if the gain in log-likelihood
        is below this value.

    verbose : bool, optional
        When ``True`` per-iteration convergence reports are printed
        to :data:`sys.stderr`. You can diagnose convergence via the
        :attr:`monitor_` attribute.

    params : string, optional
        Controls which parameters are updated in the training
        process.  Can contain any combination of 's' for startprob,
        't' for transmat, 'e' for emissionprob.
        Defaults to all parameters.

    init_params : string, optional
        Controls which parameters are initialized prior to
        training.  Can contain any combination of 's' for
        startprob, 't' for transmat, 'e' for emissionprob.
        Defaults to all parameters.

    Attributes
    ----------
    n_features : int
        Number of possible symbols emitted by the model (in the samples).

    monitor\_ : ConvergenceMonitor
        Monitor object used to check the convergence of EM.

    transmat\_ : array, shape (n_components, n_components)
        Matrix of transition probabilities between states.

    startprob\_ : array, shape (n_components, )
        Initial state occupation distribution.

    emissionprob\_ : array, shape (n_components, n_features)
        Probability of emitting a given symbol when in each state.

    Examples
    --------
    >>> from hmmlearn.hmm import MultinomialHMM
    >>> MultinomialHMM(n_components=2)
                                 #doctest: +ELLIPSIS +NORMALIZE_WHITESPACE
    MultinomialHMM(algorithm='viterbi',...)
    """
    # TODO: accept the prior on emissionprob_ for consistency.
    def __init__(self, n_components=1,
                 startprob_prior=1.0, transmat_prior=1.0,
                 algorithm="viterbi", random_state=None,
                 n_iter=10, tol=1e-2, verbose=False,
                 params="ste", init_params="ste"):
        _BaseHMM.__init__(self, n_components,
                          startprob_prior=startprob_prior,
                          transmat_prior=transmat_prior,
                          algorithm=algorithm,
                          random_state=random_state,
                          n_iter=n_iter, tol=tol, verbose=verbose,
                          params=params, init_params=init_params)

    def _init(self, X, lengths=None):
        if not self._check_input_symbols(X):
            raise ValueError("expected a sample from "
                             "a Multinomial distribution.")

        super(MultinomialHMM, self)._init(X, lengths=lengths)
        self.random_state = check_random_state(self.random_state)

        if 'e' in self.init_params:
            if not hasattr(self, "n_features"):
                symbols = set()
                for i, j in iter_from_X_lengths(X, lengths):
                    symbols |= set(X[i:j].flatten())
                self.n_features = len(symbols)
            self.emissionprob_ = self.random_state \
                .rand(self.n_components, self.n_features)
            normalize(self.emissionprob_, axis=1)

    def _check(self):
        super(MultinomialHMM, self)._check()

        self.emissionprob_ = np.atleast_2d(self.emissionprob_)
        n_features = getattr(self, "n_features", self.emissionprob_.shape[1])
        if self.emissionprob_.shape != (self.n_components, n_features):
            raise ValueError(
                "emissionprob_ must have shape (n_components, n_features)")
        else:
            self.n_features = n_features

    def _compute_log_likelihood(self, X):
        return np.log(self.emissionprob_)[:, np.concatenate(X)].T

    def _generate_sample_from_state(self, state, random_state=None):
        cdf = np.cumsum(self.emissionprob_[state, :])
        random_state = check_random_state(random_state)
        return [(cdf > random_state.rand()).argmax()]

    def _initialize_sufficient_statistics(self):
        stats = super(MultinomialHMM, self)._initialize_sufficient_statistics()
        stats['obs'] = np.zeros((self.n_components, self.n_features))
        return stats

    def _accumulate_sufficient_statistics(self, stats, X, framelogprob,
                                          posteriors, fwdlattice, bwdlattice):
        super(MultinomialHMM, self)._accumulate_sufficient_statistics(
            stats, X, framelogprob, posteriors, fwdlattice, bwdlattice)
        if 'e' in self.params:
            for t, symbol in enumerate(np.concatenate(X)):
                stats['obs'][:, symbol] += posteriors[t]

    def _do_mstep(self, stats):
        super(MultinomialHMM, self)._do_mstep(stats)
        if 'e' in self.params:
            self.emissionprob_ = (stats['obs']
                                  / stats['obs'].sum(axis=1)[:, np.newaxis])

    def _check_input_symbols(self, X):
        """Check if ``X`` is a sample from a Multinomial distribution.

        That is ``X`` should be an array of non-negative integers from
        range ``[min(X), max(X)]``, such that each integer from the range
        occurs in ``X`` at least once.

        For example ``[0, 0, 2, 1, 3, 1, 1]`` is a valid sample from a
        Multinomial distribution, while ``[0, 0, 3, 5, 10]`` is not.
        """
        symbols = np.concatenate(X)
        if (len(symbols) == 1                                # not enough data
            or not np.issubdtype(symbols.dtype, np.integer)  # not an integer
            or (symbols < 0).any()):                         # not positive
            return False
        u = np.unique(symbols)
        return u[0] == 0 and u[-1] == len(u) - 1


class GMMHMM(_BaseHMM):
    r"""Hidden Markov Model with Gaussian mixture emissions.

    Parameters
    ----------
    n_components : int
        Number of states in the model.

    n_mix : int
        Number of states in the GMM.

    covariance_type : string, optional
        String describing the type of covariance parameters to
        use.  Must be one of

        * "spherical" --- each state uses a single variance value that
          applies to all features.
        * "diag" --- each state uses a diagonal covariance matrix.
        * "full" --- each state uses a full (i.e. unrestricted)
          covariance matrix.
        * "tied" --- all states use **the same** full covariance matrix.

        Defaults to "diag".

    min_covar : float, optional
        Floor on the diagonal of the covariance matrix to prevent
        overfitting. Defaults to 1e-3.

    startprob_prior : array, shape (n_components, ), optional
        Parameters of the Dirichlet prior distribution for
        :attr:`startprob_`.

    transmat_prior : array, shape (n_components, n_components), optional
        Parameters of the Dirichlet prior distribution for each row
        of the transition probabilities :attr:`transmat_`.

    weights_prior : array, shape (n_mix, ), optional
        Parameters of the Dirichlet prior distribution for
        :attr:`weights_`.

    means_prior, means_weight : array, shape (n_mix, ), optional
        Mean and precision of the Normal prior distribtion for
        :attr:`means_`.

    covars_prior, covars_weight : array, shape (n_mix, ), optional
        Parameters of the prior distribution for the covariance matrix
        :attr:`covars_`.

        If :attr:`covariance_type` is "spherical" or "diag" the prior is
        the inverse gamma distribution, otherwise --- the inverse Wishart
        distribution.

    algorithm : string, optional
        Decoder algorithm. Must be one of "viterbi" or "map".
        Defaults to "viterbi".

    random_state: RandomState or an int seed, optional
        A random number generator instance.

    n_iter : int, optional
        Maximum number of iterations to perform.

    tol : float, optional
        Convergence threshold. EM will stop if the gain in log-likelihood
        is below this value.

    verbose : bool, optional
        When ``True`` per-iteration convergence reports are printed
        to :data:`sys.stderr`. You can diagnose convergence via the
        :attr:`monitor_` attribute.

    init_params : string, optional
        Controls which parameters are initialized prior to training. Can
        contain any combination of 's' for startprob, 't' for transmat, 'm'
        for means, 'c' for covars, and 'w' for GMM mixing weights.
        Defaults to all parameters.

    params : string, optional
        Controls which parameters are updated in the training process.  Can
        contain any combination of 's' for startprob, 't' for transmat, 'm' for
        means, and 'c' for covars, and 'w' for GMM mixing weights.
        Defaults to all parameters.

    Attributes
    ----------
    monitor\_ : ConvergenceMonitor
        Monitor object used to check the convergence of EM.

    startprob\_ : array, shape (n_components, )
        Initial state occupation distribution.

    transmat\_ : array, shape (n_components, n_components)
        Matrix of transition probabilities between states.

    weights\_ : array, shape (n_components, n_mix)
        Mixture weights for each state.

    means\_ : array, shape (n_components, n_mix)
        Mean parameters for each mixture component in each state.

    covars\_ : array
        Covariance parameters for each mixture components in each state.

        The shape depends on :attr:`covariance_type`::

            (n_components, n_mix)                          if "spherical",
            (n_components, n_features, n_features)         if "tied",
            (n_components, n_mix, n_features)              if "diag",
            (n_components, n_mix, n_features, n_features)  if "full"
    """

    def __init__(self, n_components=1, n_mix=1,
                 min_covar=1e-3, startprob_prior=1.0, transmat_prior=1.0,
                 weights_prior=1.0, means_prior=0.0, means_weight=0.0,
                 covars_prior=None, covars_weight=None,
                 algorithm="viterbi", covariance_type="diag",
                 random_state=None, n_iter=10, tol=1e-2,
                 verbose=False, params="stmcw",
                 init_params="stmcw"):
        _BaseHMM.__init__(self, n_components,
                          startprob_prior=startprob_prior,
                          transmat_prior=transmat_prior,
                          algorithm=algorithm, random_state=random_state,
                          n_iter=n_iter, tol=tol, verbose=verbose,
                          params=params, init_params=init_params)
        self.covariance_type = covariance_type
        self.min_covar = min_covar
        self.n_mix = n_mix
        self.weights_prior = weights_prior
        self.means_prior = means_prior
        self.means_weight = means_weight
        self.covars_prior = covars_prior
        self.covars_weight = covars_weight

    def _init(self, X, lengths=None):
        super(GMMHMM, self)._init(X, lengths=lengths)

        _n_samples, self.n_features = X.shape

        # Default values for covariance prior parameters
        self._init_covar_priors()
        self._fix_priors_shape()

        main_kmeans = cluster.KMeans(n_clusters=self.n_components,
                                     random_state=self.random_state)
        labels = main_kmeans.fit_predict(X)
        kmeanses = []
        for label in range(self.n_components):
            kmeans = cluster.KMeans(n_clusters=self.n_mix,
                                    random_state=self.random_state)
            kmeans.fit(X[np.where(labels == label)])
            kmeanses.append(kmeans)

        if 'w' in self.init_params or not hasattr(self, "weights_"):
            self.weights_ = (np.ones((self.n_components, self.n_mix)) /
                             (np.ones((self.n_components, 1)) * self.n_mix))

        if 'm' in self.init_params or not hasattr(self, "means_"):
            self.means_ = np.zeros((self.n_components, self.n_mix,
                                    self.n_features))
            for i, kmeans in enumerate(kmeanses):
                self.means_[i] = kmeans.cluster_centers_

        if 'c' in self.init_params or not hasattr(self, "covars_"):
            cv = np.cov(X.T) + self.min_covar * np.eye(self.n_features)
            if not cv.shape:
                cv.shape = (1, 1)

            if self.covariance_type == 'tied':
                self.covars_ = np.zeros((self.n_components,
                                         self.n_features, self.n_features))
                self.covars_[:] = cv
            elif self.covariance_type == 'full':
                self.covars_ = np.zeros((self.n_components, self.n_mix,
                                         self.n_features, self.n_features))
                self.covars_[:] = cv
            elif self.covariance_type == 'diag':
                self.covars_ = np.zeros((self.n_components, self.n_mix,
                                         self.n_features))
                self.covars_[:] = np.diag(cv)
            elif self.covariance_type == 'spherical':
                self.covars_ = np.zeros((self.n_components, self.n_mix))
                self.covars_[:] = cv.mean()

    def _init_covar_priors(self):
        if self.covariance_type == "full":
            if self.covars_prior is None:
                self.covars_prior = 0.0
            if self.covars_weight is None:
                self.covars_weight = -(1.0 + self.n_features + 1.0)
        elif self.covariance_type == "tied":
            if self.covars_prior is None:
                self.covars_prior = 0.0
            if self.covars_weight is None:
                self.covars_weight = -(self.n_mix + self.n_features + 1.0)
        elif self.covariance_type == "diag":
            if self.covars_prior is None:
                self.covars_prior = -1.5
            if self.covars_weight is None:
                self.covars_weight = 0.0
        elif self.covariance_type == "spherical":
            if self.covars_prior is None:
                self.covars_prior = -(self.n_mix + 2.0) / 2.0
            if self.covars_weight is None:
                self.covars_weight = 0.0

    def _fix_priors_shape(self):
        # If priors are numbers, this function will make them into a
        # matrix of proper shape
        self.weights_prior = np.broadcast_to(
            self.weights_prior, (self.n_components, self.n_mix)).copy()
        self.means_prior = np.broadcast_to(
            self.means_prior,
            (self.n_components, self.n_mix, self.n_features)).copy()
        self.means_weight = np.broadcast_to(
            self.means_weight,
            (self.n_components, self.n_mix)).copy()

        if self.covariance_type == "full":
            self.covars_prior = np.broadcast_to(
                self.covars_prior,
                (self.n_components, self.n_mix,
                 self.n_features, self.n_features)).copy()
            self.covars_weight = np.broadcast_to(
                self.covars_weight, (self.n_components, self.n_mix)).copy()
        elif self.covariance_type == "tied":
            self.covars_prior = np.broadcast_to(
                self.covars_prior,
                (self.n_components, self.n_features, self.n_features)).copy()
            self.covars_weight = np.broadcast_to(
                self.covars_weight, self.n_components).copy()
        elif self.covariance_type == "diag":
            self.covars_prior = np.broadcast_to(
                self.covars_prior,
                (self.n_components, self.n_mix, self.n_features)).copy()
            self.covars_weight = np.broadcast_to(
                self.covars_weight,
                (self.n_components, self.n_mix, self.n_features)).copy()
        elif self.covariance_type == "spherical":
            self.covars_prior = np.broadcast_to(
                self.covars_prior, (self.n_components, self.n_mix)).copy()
            self.covars_weight = np.broadcast_to(
                self.covars_weight, (self.n_components, self.n_mix)).copy()

    def _check(self):
        super(GMMHMM, self)._check()

        if not hasattr(self, "n_features"):
            self.n_features = self.means_.shape[2]

        self._init_covar_priors()
        self._fix_priors_shape()

        # Checking covariance type
        if self.covariance_type not in COVARIANCE_TYPES:
            raise ValueError("covariance_type must be one of {}"
                             .format(COVARIANCE_TYPES))

        self.weights_ = np.array(self.weights_)
        # Checking mixture weights' shape
        if self.weights_.shape != (self.n_components, self.n_mix):
            raise ValueError("mixture weights must have shape "
                             "(n_components, n_mix), actual shape: {}"
                             .format(self.weights_.shape))

        # Checking mixture weights' mathematical correctness
        if not np.allclose(np.sum(self.weights_, axis=1),
                           np.ones(self.n_components)):
            raise ValueError("mixture weights must sum up to 1")

        # Checking means' shape
        self.means_ = np.array(self.means_)
        if self.means_.shape != (self.n_components, self.n_mix,
                                 self.n_features):
            raise ValueError("mixture means must have shape "
                             "(n_components, n_mix, n_features), "
                             "actual shape: {}".format(self.means_.shape))

        # Checking covariances' shape
        self.covars_ = np.array(self.covars_)
        covars_shape = self.covars_.shape
        needed_shapes = {
            "spherical": (self.n_components, self.n_mix),
            "tied": (self.n_components, self.n_features, self.n_features),
            "diag": (self.n_components, self.n_mix, self.n_features),
            "full": (self.n_components, self.n_mix,
                     self.n_features, self.n_features)
        }
        needed_shape = needed_shapes[self.covariance_type]
        if covars_shape != needed_shape:
            raise ValueError("{!r} mixture covars must have shape {}, "
                             "actual shape: {}"
                             .format(self.covariance_type,
                                     needed_shape, covars_shape))

        # Checking covariances' mathematical correctness
        from scipy import linalg

        if (self.covariance_type == "spherical" or
                self.covariance_type == "diag"):
            if np.any(self.covars_ <= 0):
                raise ValueError("{!r} mixture covars must be non-negative"
                                 .format(self.covariance_type))
        elif self.covariance_type == "tied":
            for i, covar in enumerate(self.covars_):
                if (not np.allclose(covar, covar.T) or
                        np.any(linalg.eigvalsh(covar) <= 0)):
                    raise ValueError("'tied' mixture covars must be "
                                     "symmetric, positive-definite")
        elif self.covariance_type == "full":
            for i, mix_covars in enumerate(self.covars_):
                for j, covar in enumerate(mix_covars):
                    if (not np.allclose(covar, covar.T) or
                            np.any(linalg.eigvalsh(covar) <= 0)):
                        raise ValueError(
                            "'full' covariance matrix of mixture {} of "
                            "component {} must be symmetric, positive-definite"
                            .format(j, i))

    def _generate_sample_from_state(self, state, random_state=None):
        if random_state is None:
            random_state = self.random_state
        random_state = check_random_state(random_state)

        cur_weights = self.weights_[state]
        i_gauss = random_state.choice(self.n_mix, p=cur_weights)
        if self.covariance_type == 'tied':
            # self.covars_.shape == (n_components, n_features, n_features)
            # shouldn't that be (n_mix, ...)?
            covs = self.covars_
        else:
            covs = self.covars_[:, i_gauss]
            covs = fill_covars(covs, self.covariance_type,
                               self.n_components, self.n_features)
        return random_state.multivariate_normal(
            self.means_[state, i_gauss], covs[state]
        )

    def _compute_log_weighted_gaussian_densities(self, X, i_comp):
        cur_means = self.means_[i_comp]
        cur_covs = self.covars_[i_comp]
        if self.covariance_type == 'spherical':
            cur_covs = cur_covs[:, np.newaxis]
        log_cur_weights = np.log(self.weights_[i_comp])

        return log_multivariate_normal_density(
            X, cur_means, cur_covs, self.covariance_type
        ) + log_cur_weights

    def _compute_log_likelihood(self, X):
        n_samples, _ = X.shape
        res = np.zeros((n_samples, self.n_components))

        for i in range(self.n_components):
            log_denses = self._compute_log_weighted_gaussian_densities(X, i)
            with np.errstate(under="ignore"):
                res[:, i] = logsumexp(log_denses, axis=1)

        return res

    def _initialize_sufficient_statistics(self):
        stats = super(GMMHMM, self)._initialize_sufficient_statistics()
        stats['n_samples'] = 0
        stats['post_comp_mix'] = None
        stats['post_mix_sum'] = np.zeros((self.n_components, self.n_mix))
        stats['post_sum'] = np.zeros(self.n_components)
        stats['samples'] = None
        stats['centered'] = None
        return stats

    def _accumulate_sufficient_statistics(self, stats, X, framelogprob,
                                          post_comp, fwdlattice, bwdlattice):

        # TODO: support multiple frames

        super(GMMHMM, self)._accumulate_sufficient_statistics(
            stats, X, framelogprob, post_comp, fwdlattice, bwdlattice
        )

        n_samples, _ = X.shape

        stats['n_samples'] = n_samples
        stats['samples'] = X

        prob_mix = np.zeros((n_samples, self.n_components, self.n_mix))
        for p in range(self.n_components):
            log_denses = self._compute_log_weighted_gaussian_densities(X, p)
            with np.errstate(under="ignore"):
                prob_mix[:, p, :] = np.exp(log_denses) + np.finfo(np.float).eps

        prob_mix_sum = np.sum(prob_mix, axis=2)
        post_mix = prob_mix / prob_mix_sum[:, :, np.newaxis]
        post_comp_mix = post_comp[:, :, np.newaxis] * post_mix
        stats['post_comp_mix'] = post_comp_mix

        stats['post_mix_sum'] = np.sum(post_comp_mix, axis=0)
        stats['post_sum'] = np.sum(post_comp, axis=0)

        stats['centered'] = X[:, np.newaxis, np.newaxis, :] - self.means_

    def _do_mstep(self, stats):
        super(GMMHMM, self)._do_mstep(stats)

        n_samples = stats['n_samples']
        n_features = self.n_features

        # Maximizing weights
        alphas_minus_one = self.weights_prior - 1
        new_weights_numer = stats['post_mix_sum'] + alphas_minus_one
        new_weights_denom = (
            stats['post_sum'] + np.sum(alphas_minus_one, axis=1)
        )[:, np.newaxis]
        new_weights = new_weights_numer / new_weights_denom

        # Maximizing means
        lambdas, mus = self.means_weight, self.means_prior
        new_means_numer = np.einsum(
            'ijk,il->jkl',
            stats['post_comp_mix'], stats['samples']
        ) + lambdas[:, :, np.newaxis] * mus
        new_means_denom = (stats['post_mix_sum'] + lambdas)[:, :, np.newaxis]
        new_means = new_means_numer / new_means_denom

        # Maximizing covariances
        centered_means = self.means_ - mus

        if self.covariance_type == 'full':
            centered = stats['centered'].reshape((
                n_samples, self.n_components, self.n_mix, self.n_features, 1
            ))
            centered_t = stats['centered'].reshape((
                n_samples, self.n_components, self.n_mix, 1, self.n_features
            ))
            centered_dots = centered * centered_t

            psis_t = np.transpose(self.covars_prior, axes=(0, 1, 3, 2))
            nus = self.covars_weight

            centr_means_resh = centered_means.reshape((
                self.n_components, self.n_mix, self.n_features, 1
            ))
            centr_means_resh_t = centered_means.reshape((
                self.n_components, self.n_mix, 1, self.n_features
            ))
            centered_means_dots = centr_means_resh * centr_means_resh_t

            new_cov_numer = np.einsum(
                'ijk,ijklm->jklm',
                stats['post_comp_mix'], centered_dots
            ) + psis_t + (lambdas[:, :, np.newaxis, np.newaxis] *
                          centered_means_dots)
            new_cov_denom = (
                stats['post_mix_sum'] + 1 + nus + self.n_features + 1
            )[:, :, np.newaxis, np.newaxis]

            new_cov = new_cov_numer / new_cov_denom
        elif self.covariance_type == 'diag':
            centered2 = stats['centered'] ** 2
            centered_means2 = centered_means ** 2

            alphas = self.covars_prior
            betas = self.covars_weight

            new_cov_numer = np.einsum(
                'ijk,ijkl->jkl',
                stats['post_comp_mix'], centered2
            ) + lambdas[:, :, np.newaxis] * centered_means2 + 2 * betas
            new_cov_denom = (
                stats['post_mix_sum'][:, :, np.newaxis] + 1 + 2 * (alphas + 1)
            )

            new_cov = new_cov_numer / new_cov_denom
        elif self.covariance_type == 'spherical':
            centered_norm2 = np.sum(stats['centered'] ** 2, axis=-1)

            alphas = self.covars_prior
            betas = self.covars_weight

            centered_means_norm2 = np.sum(centered_means ** 2, axis=-1)

            new_cov_numer = np.einsum(
                'ijk,ijk->jk',
                stats['post_comp_mix'], centered_norm2
            ) + lambdas * centered_means_norm2 + 2 * betas
            new_cov_denom = (
                n_features * stats['post_mix_sum'] + n_features +
                2 * (alphas + 1)
            )

            new_cov = new_cov_numer / new_cov_denom
        elif self.covariance_type == 'tied':
            centered = stats['centered'].reshape((
                n_samples, self.n_components, self.n_mix, self.n_features, 1
            ))
            centered_t = stats['centered'].reshape((
                n_samples, self.n_components, self.n_mix, 1, self.n_features
            ))
            centered_dots = centered * centered_t

            psis_t = np.transpose(self.covars_prior, axes=(0, 2, 1))
            nus = self.covars_weight

            centr_means_resh = centered_means.reshape((
                self.n_components, self.n_mix, self.n_features, 1
            ))
            centr_means_resh_t = centered_means.reshape((
                self.n_components, self.n_mix, 1, self.n_features
            ))
            centered_means_dots = centr_means_resh * centr_means_resh_t

            lambdas_cmdots_prod_sum = np.einsum(
                'ij,ijkl->ikl',
                lambdas, centered_means_dots
            )

            new_cov_numer = np.einsum(
                'ijk,ijklm->jlm',
                stats['post_comp_mix'], centered_dots
            ) + lambdas_cmdots_prod_sum + psis_t
            new_cov_denom = (
                stats['post_sum'] + self.n_mix + nus + self.n_features + 1
            )[:, np.newaxis, np.newaxis]

            new_cov = new_cov_numer / new_cov_denom

        # Assigning new values to class members
        self.weights_ = new_weights
        self.means_ = new_means
        self.covars_ = new_cov


class PoissonHMM(_BaseHMM):
    """Hidden Markov Model with independent Poisson emissions.

    Parameters
    ----------
    n_components : int
        Number of states.
    startprob_prior : array, shape (n_components, )
        Initial state occupation prior distribution.
    transmat_prior : array, shape (n_components, n_components)
        Matrix of prior transition probabilities between states.
    algorithm : string, one of the :data:`base.DECODER_ALGORITHMS`
        Decoder algorithm.
    random_state: RandomState or an int seed (0 by default)
        A random number generator instance.
    n_iter : int, optional
        Maximum number of iterations to perform.
    tol : float, optional
        Convergence threshold. EM will stop if the gain in log-likelihood
        is below this value.
    verbose : bool, optional
        When ``True`` per-iteration convergence reports are printed
        to :data:`sys.stderr`. You can diagnose convergence via the
        :attr:`monitor_` attribute.
    params : string, optional
        Controls which parameters are updated in the training
        process.  Can contain any combination of 's' for startprob,
        't' for transmat, 'm' for means and 'c' for covars. Defaults
        to all parameters.
    init_params : string, optional
        Controls which parameters are initialized prior to
        training.  Can contain any combination of 's' for
        startprob, 't' for transmat, and 'm' for means.
        Defaults to all parameters.

    Attributes
    ----------
    n_components : int
        Number of states.
    n_features : int
        Dimensionality of the (independent) Poisson emissions.
    monitor_ : ConvergenceMonitor
        Monitor object used to check the convergence of EM.
    transmat_ : array, shape (n_components, n_components)
        Matrix of transition probabilities between states.
    startprob_ : array, shape (n_components, )
        Initial state occupation distribution.
    means_ : array, shape (n_components, n_features)
        Mean parameters for each state.
    Examples
    --------
    >>> from hmmlearn.hmm import PoissonHMM
    >>> PoissonHMM(n_components=2)
    ...                             #doctest: +ELLIPSIS +NORMALIZE_WHITESPACE
    PoissonHMM(algorithm='viterbi',...)
    """

    def __init__(self, n_components=1,
                 startprob_prior=1.0, transmat_prior=1.0,
                 means_prior=0, means_weight=0,
                 algorithm="viterbi", random_state=None,
                 n_iter=10, tol=1e-2, verbose=False,
                 params="stm", init_params="stm"):
        _BaseHMM.__init__(self, n_components,
                          startprob_prior=startprob_prior,
                          transmat_prior=transmat_prior, algorithm=algorithm,
                          random_state=random_state, n_iter=n_iter,
                          tol=tol, params=params, verbose=verbose,
                          init_params=init_params)

        self.means_prior = means_prior
        self.means_weight = means_weight

    def _check(self):
        super(PoissonHMM, self)._check()

        self.means_ = np.asarray(self.means_)
        self.n_features = self.means_.shape[1]

    def _compute_log_likelihood(self, obs):
        return log_multivariate_poisson_density(obs, self.means_)

    def _generate_sample_from_state(self, state, random_state=None):
        rng = check_random_state(random_state)
        return rng.poisson(self.means_[state])

    def _init(self, X, lengths=None):
        super(PoissonHMM, self)._init(X, lengths=lengths)

        _, n_features = X.shape
        if hasattr(self, 'n_features') and self.n_features != n_features:
            raise ValueError('Unexpected number of dimensions, got %s but '
                             'expected %s' % (n_features, self.n_features))

        self.n_features = n_features
        if 'm' in self.init_params or not hasattr(self, "means_"):
            kmeans = cluster.KMeans(n_clusters=self.n_components,
                                    random_state=self.random_state)
            kmeans.fit(X)
            self.means_ = kmeans.cluster_centers_

    def _initialize_sufficient_statistics(self):
        stats = super(PoissonHMM, self)._initialize_sufficient_statistics()
        stats['post'] = np.zeros(self.n_components)
        stats['obs'] = np.zeros((self.n_components, self.n_features))
        return stats

    def _accumulate_sufficient_statistics(self, stats, obs, framelogprob,
                                          posteriors, fwdlattice, bwdlattice):
        super(PoissonHMM, self)._accumulate_sufficient_statistics(
            stats, obs, framelogprob, posteriors, fwdlattice, bwdlattice)

        if 'm' in self.params:
            stats['post'] += posteriors.sum(axis=0)
            stats['obs'] += np.dot(posteriors.T, obs)

    def _do_mstep(self, stats):
        super(PoissonHMM, self)._do_mstep(stats)

        means_prior = self.means_prior
        means_weight = self.means_weight

        denom = stats['post'][:, np.newaxis]
        if 'm' in self.params:
            self.means_ = ((means_weight * means_prior + stats['obs'])
                           / (means_weight + denom))
            self.means_ = np.where(self.means_ > 1e-3, self.means_, 1e-3)

class MarkedPoissonHMM(_BaseHMM):
    """Hidden Markov Model with independent Poisson emissions, where only marks
       are observed, and not the identities of the clusters.

    Parameters
    ----------
    n_components : int
        Number of states.
    n_clusters : int
        Dimensionality of the latent (independent) Poisson emissions.
    mu : array, shape (n_clusters, n_cluster_dims)
        Multivariate Gaussian cluster means.
    Sigma : array, shape (n_clusters, n_cluster_dims, n_cluster_dims)
        Multivariate Gaussian cluster covariances.
    startprob_prior : array, shape (n_components, )
        Initial state occupation prior distribution.
    transmat_prior : array, shape (n_components, n_components)
        Matrix of prior transition probabilities between states.
    COMPLETE ME
    stype : str, optional
        One of ['unbiased', 'biased', 'no-ml'].
        'biased' samples in proportion to mark and rate probabilities.
        'no-ml' does not sample, but only returns the maximum likely
        IKR, based on the cluster params. Default is 'unbiased'.


    Attributes
    ----------
    n_components : int
        Number of states.
    n_clusters : int
        Dimensionality of the latent (independent) Poisson emissions.
    monitor_ : ConvergenceMonitor
        Monitor object used to check the convergence of EM.
    transmat_ : array, shape (n_components, n_components)
        Matrix of transition probabilities between states.
    startprob_ : array, shape (n_components, )
        Initial state occupation distribution.
    rates_ : array, shape (n_components, n_clusters)
        Relative rate parameters for each state. Relative rates
        over n_clusters, so that each row sums to one.


    Notes
    -----
    Observations are expected to have shape (n_samples, ) with each
    element being an array with shape (n_marks, mark_dims). It is
    therefore a ragged array. Ragged arrays can be created be e.g.
    by marks = np.array(n_samples*[None]), and then setting each
    element accordingly.

    LIMITATIONS
    -----------
    In its current form, this class only supports marks from a single probe.
    Neurons/units/clusters from different probes are assumed to be independent,
    so that the evaluation could almost be independent as well, but the
    underlying states are shared, so this class needs to be reworked/extended.

    Examples
    --------
    """

    def __init__(self, n_components=1, n_clusters=1,
                 cluster_means=None, cluster_covars=None, covariance_type='diag',
                 min_rate=0, rate_mode='absolute',
                 startprob_prior=1.0, transmat_prior=1.0,
                 rate_prior=0, rate_weight=0,
                 algorithm="viterbi", random_state=None,
                 n_iter=10, n_samples=1e6, tol=1e-2, verbose=False,
                 params="str", init_params="strc", stype='unbiased', reorder=False):
        _BaseHMM.__init__(self, n_components,
                          startprob_prior=startprob_prior,
                          transmat_prior=transmat_prior, algorithm=algorithm,
                          random_state=random_state, n_iter=n_iter,
                          tol=tol, params=params, verbose=verbose,
                          init_params=init_params)

        self._BaseHMM__is_clusterless = True

        self.rate_mode = rate_mode
        self.min_rate = min_rate
        self.rate_prior = rate_prior
        self.rate_weight = rate_weight
        self.n_samples = int(n_samples)
        self.n_clusters = n_clusters
        self.cluster_means = cluster_means
        self.cluster_covars = cluster_covars
        self.covariance_type = covariance_type
        self.stype = stype
        self.reorder = reorder
        self._already_initialized = False

    def plot_marks(self, obs, *, figsize=None, n_cols=None,  **kwargs):
        """obs has shape (n_samples, )-->(n_marks, n_dim)."""
        from itertools import combinations
        from seaborn import despine
        import matplotlib.pyplot as plt

        def flatten_obs(obs):
            flattened = []
            for sample in obs:
                for mark in sample:
                    flattened.append(mark)
            flattened = np.array(flattened)

            return flattened

        def no_xticklabels(*axes):
            """Remove the tick labels on the x-axis (but leave the tick marks).

            Parameters
            ----------
            ax : axis object (default=pyplot.gca())

            """
            if len(axes) == 0:
                axes = [plt.gca()]
            for ax in axes:
                ax.set_xticklabels([])

        def no_yticklabels(*axes):
            """Remove the tick labels on the y-axis (but leave the tick marks).

            Parameters
            ----------
            ax : axis object (default=pyplot.gca())

            """
            if len(axes) == 0:
                axes = [plt.gca()]
            for ax in axes:
                ax.set_yticklabels([])

        X = flatten_obs(obs)
        n_spikes, n_dims = X.shape

        pairs = []
        for val in combinations(range(n_dims), r=2):
            pairs.append(val)

        if n_cols is None:
            n_cols = 3

        n_pairs = len(pairs)
        n_rows = int(np.ceil(n_pairs / n_cols))

        if figsize is None:
            figsize = (12, 3*n_rows)

        f, axes = plt.subplots(n_rows, n_cols, sharex=True, sharey=True, figsize=figsize)
        axes = np.ravel(axes)

        used_axes = np.zeros(len(axes))
        for pp, pair in enumerate(pairs):
            axes[pp].set_xlabel('feature {}'.format(pair[0]))
            axes[pp].set_ylabel('feature {}'.format(pair[1]))
            used_axes[pp] = 1
            no_xticklabels()
            no_yticklabels()
            despine()

            axes[pp].plot(X[:,pair[0]], X[:,pair[1]], '.', **kwargs)

        for pp, ax in enumerate(axes):
            if not used_axes[pp]:
                ax.axis('off')

        return f

    def plot_clusters(self, obs, *, figsize=None, n_cols=None,  **kwargs):
        """obs has shape (n_samples, )-->(n_marks, n_dim)."""
        from itertools import combinations
        from seaborn import despine
        import matplotlib.pyplot as plt

        def flatten_obs(obs):
            flattened = []
            for sample in obs:
                for mark in sample:
                    flattened.append(mark)
            flattened = np.array(flattened)

            return flattened

        def no_xticklabels(*axes):
            """Remove the tick labels on the x-axis (but leave the tick marks).

            Parameters
            ----------
            ax : axis object (default=pyplot.gca())

            """
            if len(axes) == 0:
                axes = [plt.gca()]
            for ax in axes:
                ax.set_xticklabels([])

        def no_yticklabels(*axes):
            """Remove the tick labels on the y-axis (but leave the tick marks).

            Parameters
            ----------
            ax : axis object (default=pyplot.gca())

            """
            if len(axes) == 0:
                axes = [plt.gca()]
            for ax in axes:
                ax.set_yticklabels([])

        X = flatten_obs(obs)
        n_spikes, n_dims = X.shape

        mark_ids = self._gmm.predict(X)
        n_clusters = np.max(mark_ids)

        pairs = []
        for val in combinations(range(n_dims), r=2):
            pairs.append(val)

        if n_cols is None:
            n_cols = 3

        n_pairs = len(pairs)
        n_rows = int(np.ceil(n_pairs / n_cols))

        if figsize is None:
            figsize = (12, 3*n_rows)

        f, axes = plt.subplots(n_rows, n_cols, sharex=True, sharey=True, figsize=figsize)
        axes = np.ravel(axes)

        used_axes = np.zeros(len(axes))
        for pp, pair in enumerate(pairs):
            axes[pp].set_xlabel('feature {}'.format(pair[0]))
            axes[pp].set_ylabel('feature {}'.format(pair[1]))
            used_axes[pp] = 1
            no_xticklabels()
            no_yticklabels()
            despine()
            for mi in range(n_clusters+1):
                if not np.any(mark_ids == mi):
                    continue
                axes[pp].plot(X[mark_ids==mi,pair[0]], X[mark_ids==mi,pair[1]], '.', **kwargs)

        for pp, ax in enumerate(axes):
            if not used_axes[pp]:
                ax.axis('off')

        return f

    @property
    def covars_(self):
        """Return covars as a full matrix."""
        return fill_covars(self.cluster_covars, self.covariance_type,
                           self.n_clusters, self.cluster_dim)

    @covars_.setter
    def covars_(self, covars):
        self.cluster_covars = np.asarray(covars).copy()

    def _check(self):
        super(MarkedPoissonHMM, self)._check()

        self.rate_ = np.asarray(self.rate_)
        assert self.n_clusters == self.rate_.shape[1]

        if self.covariance_type not in COVARIANCE_TYPES:
            raise ValueError('covariance_type must be one of {}'
                             .format(COVARIANCE_TYPES))

        _utils._validate_covars(self.cluster_covars, self.covariance_type,
                                self.n_clusters)

        assert self.cluster_means.shape[0] == self.n_clusters
        self.cluster_dim = self.cluster_means.shape[1]

        assert self.covars_.shape[0] == self.n_clusters
        assert self.covars_.shape[1] == self.cluster_dim
        assert self.covars_.shape[2] == self.cluster_dim

    def _compute_log_likelihood(self, obs):
        return log_marked_poisson_density(obs,
                                          self.rate_,
                                          self.cluster_means,
                                          self.cluster_covars,
                                          self.n_samples,
                                          self.stype,
                                          self.random_state,
                                          self.reorder)

    def _generate_sample_from_state(self, state, random_state=None):
        raise NotImplementedError

    def _init(self, X, lengths=None):
        if not self._already_initialized:
            super(MarkedPoissonHMM, self)._init(X, lengths=lengths)

            if 'c' in self.init_params:
                # do GMM here to estimate cluster means and covariances.

                from sklearn import mixture

                if self.verbose:
                    message = "Initializing cluster parameters with a Gaussian mixture model"
                    print(message, file=sys.stderr)

                flattened = []
                for mm in X:
                    for mmm in mm:
                        flattened.append(mmm)
                flattened = np.array(flattened)

                gmm = mixture.GaussianMixture(n_components=self.n_clusters,
                                    covariance_type=self.covariance_type,
                                    verbose=self.verbose, random_state=self.random_state)
                gmm.fit(flattened)
                self._gmm = gmm

                self.cluster_means = gmm.means_
                self.cluster_covars = gmm.covariances_

            if 'r' in self.init_params or not hasattr(self, "rate_"):
                # maybe do gamma-sampled normalized rates?
                if self.verbose:
                    message = "Initializing cluster rates with a Gamma prior"
                    print(message, file=sys.stderr)

                rng = check_random_state(self.random_state)
                r = rng.gamma(1,1, size=(self.n_components, self.n_clusters))
                r = (r.T/np.sum(r, axis=1)).T
                self.rate_ = r

            if self.verbose:
                message = "Done\n"
                print(message, file=sys.stderr)

            self._already_initialized = True

    def _initialize_sufficient_statistics(self):
        stats = super(MarkedPoissonHMM, self)._initialize_sufficient_statistics()
        stats['post'] = np.zeros(self.n_components)
        stats['numerator'] = np.zeros((self.n_components, self.n_clusters))
        return stats

    def _accumulate_sufficient_statistics(self, stats, obs, framelogprob,
                                          posteriors, fwdlattice, bwdlattice):
        super(MarkedPoissonHMM, self)._accumulate_sufficient_statistics(
            stats, obs, framelogprob, posteriors, fwdlattice, bwdlattice)

        # stats['post'] contains (n_samples, n_components) posteriors over states (gammas)
        # stats['numerator'] contains (n_components, n_clusters) rate updates

        if 'r' in self.params:
            stats['post'] += posteriors.sum(axis=0)

            # expected rates (n_samples, n_clusters), where n_clusters is the
            # latent number of neurons.

            n_samples = len(obs)
            N = self.n_clusters
            Z = self.n_components
            cluster_means = self.cluster_means
            covars = self.covars_

            numerator = np.zeros((Z, N))

            R = self.rate_

            for zz in range(Z):
                r = R[zz,:].squeeze()
                expected_rates = np.zeros((n_samples, N))

                for mm, marks in enumerate(obs):
                    K = len(marks)

                    if K > 0:
                        logF = np.zeros((N, K))
                        for nn in range(N):
                            mvn = multivariate_normal(mean=cluster_means[nn], cov=covars[nn])
                            f = np.atleast_1d(mvn.logpdf(marks))
                            f[f < MIN_LOGLIKELIHOOD] = MIN_LOGLIKELIHOOD
                            logF[nn,:] = f

                        den = logsumexp((logF.T + np.log(r)).T , axis=0)

                        logmnp = np.zeros((N,K))
                        for nn in range(N):
                            for kk in range(K):
                                logmnp[nn,kk] = logF[nn,kk] + np.log(r[nn]) - den[kk]

                        if self.rate_mode == 'relative':
                            expected_rates[mm,:] = np.exp(logsumexp(logmnp, axis=1) - np.log(K))
                        elif self.rate_mode == 'absolute':
                            expected_rates[mm,:] = np.exp(logsumexp(logmnp, axis=1))
                    else:
                        expected_rates[mm,:] = np.ones(N) * self.min_rate

                numerator[zz,:] = np.dot(posteriors[:,zz].T, expected_rates)

            stats['numerator'] += numerator

    def _do_mstep(self, stats):
        super(MarkedPoissonHMM, self)._do_mstep(stats)

        rate_prior = self.rate_prior
        rate_weight = self.rate_weight

        denom = stats['post'][:, np.newaxis]
        if 'r' in self.params:
            self.rate_ = ((rate_weight * rate_prior + stats['numerator'])
                           / (rate_weight + denom))
            self.rate_ = np.where(self.rate_ > 1e-3, self.rate_, 1e-3)
            if self.rate_mode == 'relative':
                self.rate_ = (self.rate_.T/np.sum(self.rate_, axis=1)).T

class MultiprobeMarkedPoissonHMM(_BaseHMM):
    """Hidden Markov Model with independent Poisson emissions,
       where only marks are observed, and not the identities of the clusters.

    Parameters
    ----------

    Attributes
    ----------

    Notes
    -----
    Observations are expected to have shape (n_probes, ) with each
    element having shape (n_samples, ) with each element being an
    array with shape (n_marks, mark_dims). It is therefore a ragged
    array. Ragged arrays can be created be e.g. by
        >>> marks = np.array(n_samples*[None]), and then setting
    each element accordingly.

    UPDATE
    ------
    hmmlearn doesn't work with (n_probes, ) data. It requires (n_samples, ) type
    data. We can get around this restriction during fit(), but not in decode(),
    so we have to re-work this class again.

    We now require (n_samples, n_probes, )-->(n_marks*, n_dims) data, which is
    ugly, but I don't think we have much of a choice here. *variable number.

    """

    def __init__(self, n_components=1, n_clusters=1,
                 cluster_means=None, cluster_covars=None, covariance_type='diag',
                 min_rate=0, rate_mode='absolute',
                 startprob_prior=1.0, transmat_prior=1.0,
                 rate_prior=0, rate_weight=0,
                 algorithm="viterbi", random_state=None,
                 n_iter=10, n_samples=1e6, tol=1e-2, verbose=False,
                 params="str", init_params="strc", stype='unbiased', reorder=False):
        _BaseHMM.__init__(self, n_components,
                          startprob_prior=startprob_prior,
                          transmat_prior=transmat_prior, algorithm=algorithm,
                          random_state=random_state, n_iter=n_iter,
                          tol=tol, params=params, verbose=verbose,
                          init_params=init_params)

        self._BaseHMM__is_clusterless = True

        self.rate_mode = rate_mode
        self.min_rate = min_rate
        self.rate_prior = rate_prior
        self.rate_weight = rate_weight
        self.n_samples = int(n_samples)
        self.n_clusters = n_clusters           # per-probe
        self.cluster_means = cluster_means     # per-probe
        self.cluster_covars = cluster_covars   # per-probe
        self.covariance_type = covariance_type
        self.stype = stype
        self.reorder = reorder
        self.n_probes = len(n_clusters)
        self._already_initialized = False

    def plot_marks(self, obs, probe, *, figsize=None, n_cols=None,  **kwargs):
        """obs has shape (n_samples, )-->(n_marks, n_dim)."""
        from itertools import combinations
        from seaborn import despine
        import matplotlib.pyplot as plt

        n_samples, n_probes = obs.shape
        data = obs[:,probe]

        def flatten_obs(obs):
            flattened = []
            for sample in data:
                if np.any(sample):
                    for mark in sample:
                        flattened.append(mark)
            flattened = np.array(flattened)
            return flattened

        def no_xticklabels(*axes):
            """Remove the tick labels on the x-axis (but leave the tick marks).

            Parameters
            ----------
            ax : axis object (default=pyplot.gca())

            """
            if len(axes) == 0:
                axes = [plt.gca()]
            for ax in axes:
                ax.set_xticklabels([])

        def no_yticklabels(*axes):
            """Remove the tick labels on the y-axis (but leave the tick marks).

            Parameters
            ----------
            ax : axis object (default=pyplot.gca())

            """
            if len(axes) == 0:
                axes = [plt.gca()]
            for ax in axes:
                ax.set_yticklabels([])

        X = flatten_obs(data)
        n_spikes, n_dims = X.shape

        pairs = []
        for val in combinations(range(n_dims), r=2):
            pairs.append(val)

        if n_cols is None:
            n_cols = 3

        n_pairs = len(pairs)
        n_rows = int(np.ceil(n_pairs / n_cols))

        if figsize is None:
            figsize = (12, 3*n_rows)

        f, axes = plt.subplots(n_rows, n_cols, sharex=True, sharey=True, figsize=figsize)
        axes = np.ravel(axes)

        used_axes = np.zeros(len(axes))
        for pp, pair in enumerate(pairs):
            axes[pp].set_xlabel('feature {}'.format(pair[0]))
            axes[pp].set_ylabel('feature {}'.format(pair[1]))
            used_axes[pp] = 1
            no_xticklabels()
            no_yticklabels()
            despine()

            axes[pp].plot(X[:,pair[0]], X[:,pair[1]], '.', **kwargs)

        for pp, ax in enumerate(axes):
            if not used_axes[pp]:
                ax.axis('off')

        return f

    def plot_clusters(self, obs, probe, *, figsize=None, n_cols=None,  **kwargs):
        """obs has shape (n_samples, )-->(n_marks, n_dim)."""
        from itertools import combinations
        from seaborn import despine
        import matplotlib.pyplot as plt

        def flatten_obs(obs):
            flattened = []
            for sample in obs:
                for mark in sample:
                    flattened.append(mark)
            flattened = np.array(flattened)

            return flattened

        def no_xticklabels(*axes):
            """Remove the tick labels on the x-axis (but leave the tick marks).

            Parameters
            ----------
            ax : axis object (default=pyplot.gca())

            """
            if len(axes) == 0:
                axes = [plt.gca()]
            for ax in axes:
                ax.set_xticklabels([])

        def no_yticklabels(*axes):
            """Remove the tick labels on the y-axis (but leave the tick marks).

            Parameters
            ----------
            ax : axis object (default=pyplot.gca())

            """
            if len(axes) == 0:
                axes = [plt.gca()]
            for ax in axes:
                ax.set_yticklabels([])

        n_samples, n_probes = obs.shape
        data = obs[:,probe]

        X = flatten_obs(data)
        n_spikes, n_dims = X.shape

        mark_ids = self._gmm[probe].predict(X)
        n_clusters = np.max(mark_ids)

        pairs = []
        for val in combinations(range(n_dims), r=2):
            pairs.append(val)

        if n_cols is None:
            n_cols = 3

        n_pairs = len(pairs)
        n_rows = int(np.ceil(n_pairs / n_cols))

        if figsize is None:
            figsize = (12, 3*n_rows)

        f, axes = plt.subplots(n_rows, n_cols, sharex=True, sharey=True, figsize=figsize)
        axes = np.ravel(axes)

        used_axes = np.zeros(len(axes))
        for pp, pair in enumerate(pairs):
            axes[pp].set_xlabel('feature {}'.format(pair[0]))
            axes[pp].set_ylabel('feature {}'.format(pair[1]))
            used_axes[pp] = 1
            no_xticklabels()
            no_yticklabels()
            despine()
            for mi in range(n_clusters+1):
                if not np.any(mark_ids == mi):
                    continue
                axes[pp].plot(X[mark_ids==mi,pair[0]], X[mark_ids==mi,pair[1]], '.', **kwargs)

        for pp, ax in enumerate(axes):
            if not used_axes[pp]:
                ax.axis('off')

        return f

    @property
    def covars_(self):
        """Return covars as full matrices."""
        covars = np.array(self.n_probes*[None])
        for probe in range(self.n_probes):
            covars[probe] = fill_covars(self.cluster_covars[probe],
                                        self.covariance_type,
                                        self.n_clusters[probe],
                                        self.cluster_dim)
        return covars

    @covars_.setter
    def covars_(self, covars):
        self.cluster_covars = np.asarray(covars).copy()

    def _check(self):
        super(MultiprobeMarkedPoissonHMM, self)._check()

        self.rate_ = np.asarray(self.rate_)
        assert np.sum(self.n_clusters) == self.rate_.shape[1]

        if self.covariance_type not in COVARIANCE_TYPES:
            raise ValueError('covariance_type must be one of {}'
                             .format(COVARIANCE_TYPES))

        for probe in range(self.n_probes):
            _utils._validate_covars(self.cluster_covars[probe], self.covariance_type,
                                    self.n_clusters[probe])

            assert self.cluster_means[probe].shape[0] == self.n_clusters[probe]
        self.cluster_dim = self.cluster_means[0].shape[1]

        for probe in range(self.n_probes):
            assert self.covars_[probe].shape[0] == self.n_clusters[probe]
            assert self.covars_[probe].shape[1] == self.cluster_dim
            assert self.covars_[probe].shape[2] == self.cluster_dim

    def _compute_log_likelihood(self, obs):
        """obs has shape (n_samples, n_probes)-->(n_marks*, n_dims)"""
        return mp_log_marked_poisson_density(obs,
                                          self.rate_,
                                          self.cluster_ids,
                                          self.cluster_means,
                                          self.cluster_covars,
                                          self.n_samples,
                                          self.stype,
                                          self.random_state,
                                          self.reorder)

    def _generate_sample_from_state(self, state, random_state=None):
        raise NotImplementedError

    def _init(self, X, lengths=None):
        if not self._already_initialized:
            super(MultiprobeMarkedPoissonHMM, self)._init(X, lengths=lengths)

            n_samples, n_probes = X.shape
            assert self.n_probes == n_probes

            if 'c' in self.init_params:
                # do GMM here to estimate cluster means and covariances.

                from sklearn import mixture

                if self.verbose:
                    message = "Initializing cluster parameters with a Gaussian mixture model"
                    print(message, file=sys.stderr)

                self.cluster_means = np.array(self.n_probes*[None])
                self.cluster_covars = np.array(self.n_probes*[None])

                self._gmm = np.array(self.n_probes*[None])
                for probe in range(self.n_probes):
                    data = X[:,probe]
                    flattened = []
                    for sample in data:
                        if np.any(sample):
                            for mark in sample:
                                flattened.append(mark)
                    flattened = np.array(flattened)

                    gmm = mixture.GaussianMixture(n_components=self.n_clusters[probe],
                                        covariance_type=self.covariance_type,
                                        verbose=self.verbose, random_state=self.random_state)
                    gmm.fit(flattened)
                    self._gmm[probe] = gmm

                    self.cluster_means[probe] = gmm.means_
                    self.cluster_covars[probe] = gmm.covariances_

            if 'r' in self.init_params or not hasattr(self, "rate_"):
                # maybe do gamma-sampled normalized rates?
                if self.verbose:
                    message = "Initializing cluster rates with a Gamma prior"
                    print(message, file=sys.stderr)

                rng = check_random_state(self.random_state)
                r = rng.gamma(1,1, size=(self.n_components, np.sum(self.n_clusters)))
                r = (r.T/np.sum(r, axis=1)).T
                self.rate_ = r

            if self.verbose:
                message = "Done\n"
                print(message, file=sys.stderr)

            self._already_initialized = True

    def _initialize_sufficient_statistics(self):
        stats = super(MultiprobeMarkedPoissonHMM, self)._initialize_sufficient_statistics()
        stats['post'] = np.zeros(self.n_components)
        stats['numerator'] = np.zeros((self.n_components, np.sum(self.n_clusters)))
        return stats

    @property
    def cluster_ids(self):
        cluster_ids = np.array(self.n_probes*[None])
        count = 0
        for probe in range(self.n_probes):
            n_clusters = self.n_clusters[probe]
            cluster_ids[probe] = list(range(count, count+n_clusters))
            count += n_clusters
        return cluster_ids

    def _accumulate_sufficient_statistics(self, stats, obs, framelogprob,
                                          posteriors, fwdlattice, bwdlattice):
        super(MultiprobeMarkedPoissonHMM, self)._accumulate_sufficient_statistics(
            stats, obs, framelogprob, posteriors, fwdlattice, bwdlattice)

        # stats['post'] contains (n_samples, n_components) posteriors over states (gammas)
        # stats['numerator'] contains (n_components, n_clusters) rate updates

        if 'r' in self.params:
            stats['post'] += posteriors.sum(axis=0)

            # expected rates (n_samples, n_clusters), where n_clusters is the
            # latent number of neurons.

            n_samples = len(obs)
            N_total = np.sum(self.n_clusters)
            Z = self.n_components
            cluster_means = self.cluster_means
            covars = self.covars_

            numerator = np.zeros((Z, N_total))

            R = self.rate_

            for zz in range(Z):
                expected_rates = np.zeros((n_samples, N_total))

                for probe in range(self.n_probes):
                    r = R[zz, self.cluster_ids[probe]].squeeze()
                    X = obs[:,probe]
                    N = len(self.cluster_ids[probe])
                    for mm, marks in enumerate(X):
                        K = len(marks)

                        if K > 0:
                            logF = np.zeros((N, K))
                            for nn in range(N):
                                mvn = multivariate_normal(mean=cluster_means[probe][nn], cov=covars[probe][nn])
                                f = np.atleast_1d(mvn.logpdf(marks))
                                f[f < MIN_LOGLIKELIHOOD] = MIN_LOGLIKELIHOOD
                                logF[nn,:] = f

                            den = logsumexp((logF.T + np.log(r)).T , axis=0)

                            logmnp = np.zeros((N,K))
                            for nn in range(N):
                                for kk in range(K):
                                    logmnp[nn,kk] = logF[nn,kk] + np.log(r[nn]) - den[kk]

                            if self.rate_mode == 'relative':
                                expected_rates[mm,self.cluster_ids[probe]] = np.exp(logsumexp(logmnp, axis=1) - np.log(K))
                            elif self.rate_mode == 'absolute':
                                expected_rates[mm,self.cluster_ids[probe]] = np.exp(logsumexp(logmnp, axis=1))
                        else:
                            expected_rates[mm,self.cluster_ids[probe]] = np.ones(N) * self.min_rate

                numerator[zz,:] = np.dot(posteriors[:,zz].T, expected_rates)

            stats['numerator'] += numerator

    def _do_mstep(self, stats):
        super(MultiprobeMarkedPoissonHMM, self)._do_mstep(stats)

        rate_prior = self.rate_prior
        rate_weight = self.rate_weight

        denom = stats['post'][:, np.newaxis]
        if 'r' in self.params:
            self.rate_ = ((rate_weight * rate_prior + stats['numerator'])
                           / (rate_weight + denom))
            self.rate_ = np.where(self.rate_ > 1e-3, self.rate_, 1e-3)
            if self.rate_mode == 'relative':
                self.rate_ = (self.rate_.T/np.sum(self.rate_, axis=1)).T