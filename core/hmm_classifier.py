"""
Gaussian HMM market regime classifier — pure numpy/scipy implementation.

Mengapa tidak pakai hmmlearn:
  hmmlearn membutuhkan Cython extension yang belum tersedia untuk Python 3.14.
  Implementasi ini menggunakan Baum-Welch (EM) untuk training dan forward
  algorithm untuk prediksi, identik secara matematis.

State prediction (no look-ahead):
  Menggunakan forward algorithm murni. Pada timestep terakhir T,
  smoothed = filtered karena β_T = 1 (tidak ada data future setelah T).
  Hasilnya identik dengan predict_proba()[-1] dari hmmlearn.
"""

import numpy as np
from scipy.stats import multivariate_normal
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

STATE_LABELS  = ["CRASH", "BEAR", "NEUTRAL", "BULL", "EUPHORIA"]
N_STATES      = 5
N_FEATURES    = 3
RETRAIN_HOURS = 24
MIN_ROWS      = 30   # minimum candles setelah feature extraction
N_ITER        = 150  # iterasi Baum-Welch
TOL           = 1e-5


# ── Numerically stable log-sum-exp ───────────────────────────────────────────

def _logsumexp(a: np.ndarray) -> float:
    m = np.max(a)
    return m + np.log(np.sum(np.exp(a - m)))


def _log_normalize(a: np.ndarray) -> np.ndarray:
    """Normalize log-probability vector to sum to 1 (log domain)."""
    lse = _logsumexp(a)
    return a - lse


# ── Gaussian HMM (Baum-Welch + forward algorithm) ────────────────────────────

class _GaussianHMM:
    """
    Minimal Gaussian HMM trained with Baum-Welch (EM).
    All computations in log-space for numerical stability.
    """

    def __init__(self, n_states: int, n_iter: int = N_ITER, tol: float = TOL,
                 random_state: int = 42):
        self.n_states   = n_states
        self.n_iter     = n_iter
        self.tol        = tol
        self.rng        = np.random.default_rng(random_state)

        # Parameters (set by fit)
        self.log_pi: np.ndarray | None = None   # (K,) log initial probs
        self.log_A:  np.ndarray | None = None   # (K, K) log transition matrix
        self.means:  np.ndarray | None = None   # (K, D)
        self.covars: np.ndarray | None = None   # (K, D, D)

    # ── Init params via K-Means ──────────────────────────────────────────────

    def _init_params(self, X: np.ndarray) -> None:
        K, D = self.n_states, X.shape[1]

        km = KMeans(n_clusters=K, random_state=int(self.rng.integers(1e6)),
                    n_init=10).fit(X)
        labels = km.labels_

        self.means  = np.zeros((K, D))
        self.covars = np.zeros((K, D, D))
        for k in range(K):
            mask = labels == k
            pts  = X[mask] if mask.sum() > 1 else X
            self.means[k]  = pts.mean(axis=0)
            self.covars[k] = np.cov(pts, rowvar=False).reshape(D, D) + np.eye(D) * 1e-4

        # Uniform init + small noise
        log_pi = np.full(K, -np.log(K))
        self.log_pi = log_pi

        A = np.full((K, K), 1.0 / K) + self.rng.uniform(0, 0.01, (K, K))
        A /= A.sum(axis=1, keepdims=True)
        self.log_A = np.log(A + 1e-12)

    # ── Emission log-probabilities ────────────────────────────────────────────

    def _log_emit(self, X: np.ndarray) -> np.ndarray:
        """Return (T, K) matrix of log P(x_t | state k)."""
        T, K = len(X), self.n_states
        log_b = np.zeros((T, K))
        for k in range(K):
            log_b[:, k] = multivariate_normal.logpdf(
                X, mean=self.means[k], cov=self.covars[k], allow_singular=True
            )
        return log_b

    # ── Forward pass (log-space) ──────────────────────────────────────────────

    def _forward(self, log_b: np.ndarray) -> tuple[np.ndarray, float]:
        """
        Returns:
            log_alpha: (T, K) forward log-probabilities
            log_likelihood: scalar
        """
        T, K = log_b.shape
        log_alpha = np.zeros((T, K))
        log_alpha[0] = self.log_pi + log_b[0]

        for t in range(1, T):
            for k in range(K):
                log_alpha[t, k] = (
                    _logsumexp(log_alpha[t - 1] + self.log_A[:, k])
                    + log_b[t, k]
                )

        log_likelihood = _logsumexp(log_alpha[-1])
        return log_alpha, log_likelihood

    # ── Backward pass (log-space) ─────────────────────────────────────────────

    def _backward(self, log_b: np.ndarray) -> np.ndarray:
        """Returns log_beta: (T, K) backward log-probabilities."""
        T, K = log_b.shape
        log_beta = np.zeros((T, K))   # β_T = 1 → log β_T = 0

        for t in range(T - 2, -1, -1):
            for k in range(K):
                log_beta[t, k] = _logsumexp(
                    self.log_A[k] + log_b[t + 1] + log_beta[t + 1]
                )

        return log_beta

    # ── Baum-Welch EM ────────────────────────────────────────────────────────

    def fit(self, X: np.ndarray) -> "_ GaussianHMM":
        self._init_params(X)
        T, D, K = len(X), X.shape[1], self.n_states
        prev_ll = -np.inf

        for iteration in range(self.n_iter):
            log_b     = self._log_emit(X)
            log_alpha, log_ll = self._forward(log_b)
            log_beta  = self._backward(log_b)

            # Convergence check
            if abs(log_ll - prev_ll) < self.tol:
                logger.debug(f"HMM converged at iteration {iteration}, logL={log_ll:.4f}")
                break
            prev_ll = log_ll

            # ── E-step: compute γ and ξ ──────────────────────────────────────
            # γ_t(k) = P(z_t=k | X)
            log_gamma = log_alpha + log_beta
            for t in range(T):
                log_gamma[t] = _log_normalize(log_gamma[t])
            gamma = np.exp(log_gamma)   # (T, K)

            # ξ_t(i,j) = P(z_t=i, z_{t+1}=j | X)
            log_xi = np.zeros((T - 1, K, K))
            for t in range(T - 1):
                for i in range(K):
                    for j in range(K):
                        log_xi[t, i, j] = (
                            log_alpha[t, i]
                            + self.log_A[i, j]
                            + log_b[t + 1, j]
                            + log_beta[t + 1, j]
                        )
                log_xi[t] -= _logsumexp(log_xi[t].ravel())
            xi = np.exp(log_xi)   # (T-1, K, K)

            # ── M-step: update parameters ────────────────────────────────────
            # Initial state probabilities
            self.log_pi = _log_normalize(np.log(gamma[0] + 1e-12))

            # Transition matrix
            xi_sum   = xi.sum(axis=0)               # (K, K)
            A_new    = xi_sum / (xi_sum.sum(axis=1, keepdims=True) + 1e-12)
            self.log_A = np.log(A_new + 1e-12)

            # Emission means and covariances
            gamma_sum = gamma.sum(axis=0) + 1e-12   # (K,)
            self.means = (gamma.T @ X) / gamma_sum[:, None]   # (K, D)

            for k in range(K):
                diff = X - self.means[k]             # (T, D)
                w    = gamma[:, k]                   # (T,)
                cov  = (w[:, None] * diff).T @ diff / gamma_sum[k]
                self.covars[k] = cov + np.eye(D) * 1e-4   # regularize

        return self

    # ── Prediction ────────────────────────────────────────────────────────────

    def predict_proba_last(self, X: np.ndarray) -> np.ndarray:
        """
        Return P(z_T | X_{1:T}) using forward algorithm only.

        At t=T: β_T(k) = 1 for all k, so γ_T(k) ∝ α_T(k).
        This is strictly causal — no future observations are used.
        """
        log_b        = self._log_emit(X)
        log_alpha, _ = self._forward(log_b)
        log_probs    = _log_normalize(log_alpha[-1])
        return np.exp(log_probs)

    def forward_sequence(self, X: np.ndarray) -> np.ndarray:
        """
        Run the forward algorithm ONCE and return P(z_t | x_{1:t}) for every t.

        Returns (T, K) array — row t is the causal filtered state distribution.
        O(T·K²) total vs O(T²·K²) if calling predict_proba_last in a loop.
        Used by the backtester to pre-compute all HMM states in one pass.
        """
        log_b        = self._log_emit(X)
        log_alpha, _ = self._forward(log_b)
        T            = len(X)
        result       = np.zeros((T, self.n_states))
        for t in range(T):
            result[t] = np.exp(_log_normalize(log_alpha[t]))
        return result


# ── Public classifier ─────────────────────────────────────────────────────────

class HMMClassifier:
    """
    5-state market regime classifier built on top of _GaussianHMM.

    States (sorted by mean log-return, low → high):
        CRASH | BEAR | NEUTRAL | BULL | EUPHORIA
    """

    def __init__(self, n_states: int = N_STATES, random_state: int = 42):
        self.n_states      = n_states
        self.random_state  = random_state
        self.scaler        = StandardScaler()
        self._hmm: _GaussianHMM | None = None
        self._state_map: dict[int, str] = {}
        self._last_trained: datetime | None = None
        self.is_fitted     = False

    # ── Features ─────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_features(df: "pd.DataFrame") -> "pd.DataFrame":
        import pandas as pd
        out = pd.DataFrame(index=df.index)
        out["log_return"]    = np.log(df["close"] / df["close"].shift(1))
        out["volatility"]    = out["log_return"].rolling(window=10).std()
        safe_vol             = df["volume"].clip(lower=1e-8)
        out["volume_change"] = np.log(safe_vol / safe_vol.shift(1))
        return out.dropna()

    # ── Training ──────────────────────────────────────────────────────────────

    def fit(self, df: "pd.DataFrame") -> None:
        features = self._extract_features(df)

        if len(features) < MIN_ROWS:
            raise ValueError(
                f"Data tidak cukup untuk HMM training: "
                f"{len(features)} baris (minimum {MIN_ROWS})"
            )

        X = self.scaler.fit_transform(features.values)

        self._hmm = _GaussianHMM(
            n_states=self.n_states,
            n_iter=N_ITER,
            tol=TOL,
            random_state=self.random_state,
        ).fit(X)

        # Map HMM indices → semantic labels via mean log_return (feature 0)
        # Relative order of means is preserved after StandardScaler
        mean_returns   = self._hmm.means[:, 0]
        sorted_indices = np.argsort(mean_returns)
        self._state_map = {int(idx): STATE_LABELS[rank]
                           for rank, idx in enumerate(sorted_indices)}

        self._last_trained = datetime.now()
        self.is_fitted     = True

        state_info = {self._state_map[int(i)]: f"{mean_returns[i]:.4f}"
                      for i in range(self.n_states)}
        logger.info(f"HMM trained on {len(features)} candles. "
                    f"Mean log_returns (scaled) per state: {state_info}")

        # Sanity check: if BULL/EUPHORIA have negative mean returns,
        # their labels are unreliable — remap them to NEUTRAL to avoid false BUY signals.
        for idx, label in list(self._state_map.items()):
            if label in ("BULL", "EUPHORIA") and mean_returns[idx] < 0:
                logger.warning(
                    f"HMM state '{label}' has negative mean return "
                    f"({mean_returns[idx]:.4f}) — remapped to NEUTRAL"
                )
                self._state_map[idx] = "NEUTRAL"
            if label in ("BEAR", "CRASH") and mean_returns[idx] > 0:
                logger.warning(
                    f"HMM state '{label}' has positive mean return "
                    f"({mean_returns[idx]:.4f}) — remapped to NEUTRAL"
                )
                self._state_map[idx] = "NEUTRAL"

    def needs_retrain(self) -> bool:
        if not self.is_fitted or self._last_trained is None:
            return True
        return (datetime.now() - self._last_trained) > timedelta(hours=RETRAIN_HOURS)

    def predict_sequence(self, df: "pd.DataFrame") -> list[tuple[str, float]]:
        """
        Predict state for every row using a single O(T·K²) forward pass.

        Rows that fall in the feature-extraction warmup window (NaN features)
        are returned as ("NEUTRAL", 0.0).  All other rows use strictly causal
        forward probabilities — at time t only data up to t is considered.

        Returns:
            List of (state_label, confidence) aligned with df.index.
        """
        import pandas as pd
        if not self.is_fitted or self._hmm is None:
            return [("NEUTRAL", 0.0)] * len(df)

        features   = self._extract_features(df)
        n_warmup   = len(df) - len(features)
        X          = self.scaler.transform(features.values)
        all_probs  = self._hmm.forward_sequence(X)       # (T_valid, K)

        results: list[tuple[str, float]] = [("NEUTRAL", 0.0)] * n_warmup
        for row_probs in all_probs:
            best_idx   = int(np.argmax(row_probs))
            confidence = float(row_probs[best_idx])
            label      = self._state_map.get(best_idx, "NEUTRAL")
            results.append((label, confidence))

        return results

    # ── Prediction ────────────────────────────────────────────────────────────

    def predict(self, df: "pd.DataFrame") -> tuple[str, float]:
        """
        Predict current market regime.

        Returns:
            (label, confidence)   e.g. ("BULL", 0.83)
        """
        if not self.is_fitted or self._hmm is None:
            raise RuntimeError("HMMClassifier belum ditraining. Panggil fit() terlebih dahulu.")

        features = self._extract_features(df)
        if features.empty:
            raise ValueError("Tidak ada fitur yang bisa diekstrak dari data.")

        X = self.scaler.transform(features.values)
        probs = self._hmm.predict_proba_last(X)

        best_idx   = int(np.argmax(probs))
        confidence = float(probs[best_idx])
        label      = self._state_map.get(best_idx, "NEUTRAL")

        return label, confidence

    def fit_and_predict_multi(self, tf_dict: dict) -> dict:
        """
        Train HMM on the 1h timeframe (most data) if needed, then predict
        the current regime for each timeframe in tf_dict.

        Returns {"15m": state, "1h": state, "4h": state}.
        """
        _candidate = tf_dict.get("1h")
        train_df = _candidate if _candidate is not None else next(iter(tf_dict.values()))

        if self.needs_retrain():
            try:
                self.fit(train_df)
            except Exception as e:
                logger.warning(f"HMM fit_and_predict_multi: training gagal: {e}")

        results: dict[str, str] = {}
        for tf, df in tf_dict.items():
            if not self.is_fitted:
                results[tf] = "NEUTRAL"
                continue
            try:
                state, _ = self.predict(df)
                results[tf] = state
            except Exception as e:
                logger.warning(f"HMM predict untuk {tf} gagal: {e}")
                results[tf] = "NEUTRAL"

        return results

    # ── Descriptions ─────────────────────────────────────────────────────────

    @staticmethod
    def get_state_description(state: str) -> str:
        return {
            "CRASH":    "Kondisi crash — penurunan tajam, volatilitas ekstrem. Hindari posisi baru.",
            "BEAR":     "Pasar bearish — tren turun, momentum negatif. Posisi defensif disarankan.",
            "NEUTRAL":  "Pasar sideways — tidak ada tren dominan. Tunggu konfirmasi arah.",
            "BULL":     "Pasar bullish — tren naik, momentum positif. Kondisi mendukung long.",
            "EUPHORIA": "Euforia — kenaikan berlebihan, risiko reversal tinggi.",
        }.get(state, "Kondisi tidak diketahui.")

    @staticmethod
    def get_trading_bias(state: str) -> str:
        return {
            "CRASH":    "JANGAN trading — fokus capital preservation.",
            "BEAR":     "Bias SELL/HOLD — hindari BUY baru.",
            "NEUTRAL":  "Netral — andalkan sinyal teknikal lain.",
            "BULL":     "Bias BUY — gunakan position sizing konservatif.",
            "EUPHORIA": "Hati-hati — potensi profit tinggi, risiko reversal ekstrem.",
        }.get(state, "Tidak ada bias yang jelas.")
