"""진동 특징 추출 (노트북 pdm_ims_autoencoder.ipynb 의 함수와 동일한 계산).
서버 점수가 Colab 결과와 일치하려면 이 파일의 수식을 노트북과 다르게 바꾸면 안 된다."""
import numpy as np
from scipy import signal, stats
from scipy.fft import rfft, rfftfreq


def bearing_freqs(shaft_hz, n, d, D, theta_deg=0.0):
    r = d / D * np.cos(np.radians(theta_deg))
    return {'BPFO': n / 2 * shaft_hz * (1 - r), 'BPFI': n / 2 * shaft_hz * (1 + r),
            'BSF': D / (2 * d) * shaft_hz * (1 - r ** 2), 'FTF': shaft_hz / 2 * (1 - r)}


def time_features(x):
    rms = np.sqrt(np.mean(x ** 2)); peak = np.max(np.abs(x)); mabs = np.mean(np.abs(x))
    return {'mean': x.mean(), 'std': x.std(), 'rms': rms, 'peak': peak, 'p2p': x.max() - x.min(),
            'kurtosis': stats.kurtosis(x), 'skew': stats.skew(x),
            'crest': peak / rms, 'shape': rms / mabs, 'impulse': peak / mabs}


def amp_spectrum(x, fs):
    w = signal.windows.hann(len(x))                  # Hanning 윈도우
    X = np.abs(rfft((x - x.mean()) * w)) * 2 / w.sum()
    return rfftfreq(len(x), 1 / fs), X


def band_peak(f, X, f0, tol_hz):
    m = (f >= f0 - tol_hz) & (f <= f0 + tol_hz)
    return float(X[m].max()) if m.any() else 0.0


def freq_features(x, fs, shaft_hz, fault, prefix='', n_harm=2, tol_ratio=0.03):
    f, X = amp_spectrum(x, fs)
    df = f[1] - f[0]
    P = X ** 2; Pn = P / (P.sum() + 1e-18)
    out = {f'{prefix}spec_entropy': float(-(Pn * np.log(Pn + 1e-18)).sum()),
           f'{prefix}spec_centroid': float((f * Pn).sum()),
           f'{prefix}spec_peak_hz': float(f[np.argmax(X)])}
    targets = {'1X': shaft_hz, '2X': 2 * shaft_hz, '3X': 3 * shaft_hz}
    for name in ['BPFO', 'BPFI', 'BSF']:
        for h in range(1, n_harm + 1):
            targets[f'{name}_h{h}'] = fault[name] * h
    for k, f0 in targets.items():
        out[f'{prefix}{k}'] = band_peak(f, X, f0, max(f0 * tol_ratio, 2 * df))
    return out


def envelope_spectrum(x, fs, band=(2000, 5000)):
    """고주파 공진대역 대역통과 → Hilbert 포락선 → 진폭 스펙트럼."""
    nyq = fs / 2
    hi = min(band[1], nyq * 0.95)
    sos = signal.butter(4, [band[0] / nyq, hi / nyq], btype='band', output='sos')
    env = np.abs(signal.hilbert(signal.sosfiltfilt(sos, x)))
    return amp_spectrum(env, fs)


def envelope_features(x, fs, fault, band=(2000, 5000), prefix='env_', n_harm=2, tol_ratio=0.03, spec=None):
    f, X = spec if spec is not None else envelope_spectrum(x, fs, band)
    df = f[1] - f[0]
    out = {}
    for name in ['BPFO', 'BPFI', 'BSF']:
        for h in range(1, n_harm + 1):
            f0 = fault[name] * h
            out[f'{prefix}{name}_h{h}'] = band_peak(f, X, f0, max(f0 * tol_ratio, 2 * df))
    return out


def extract_all(x, fs, shaft_hz, fault, env_band, return_env_spec=False):
    x = np.asarray(x, dtype=float)
    feats = time_features(x)
    feats.update(freq_features(x, fs, shaft_hz, fault))
    spec = envelope_spectrum(x, fs, env_band)
    feats.update(envelope_features(x, fs, fault, band=env_band, spec=spec))
    return (feats, spec) if return_env_spec else feats
