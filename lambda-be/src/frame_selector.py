"""Frame selector using HOG + PCA + KMeans clustering (numpy-only, no scikit-learn).

Selects visually diverse frames by:
1. Computing HOG feature vectors for each candidate frame
2. Reducing dimensionality via eigendecomposition (PCA)
3. Clustering with Lloyd's KMeans
4. Picking the frame closest to each cluster centroid
"""
import logging
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HOG feature extraction (simplified, OpenCV-based)
# ---------------------------------------------------------------------------

def _compute_hog(image_path: str, resize: Tuple[int, int] = (128, 128)) -> Optional[np.ndarray]:
    """Compute a compact HOG descriptor for a frame image.

    Args:
        image_path: Path to the frame image.
        resize: Target (width, height) before HOG computation.

    Returns:
        1-D numpy feature vector, or None on failure.
    """
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        logger.warning("Could not read image for HOG: %s", image_path)
        return None
    img = cv2.resize(img, resize)

    win_size = resize
    block_size = (16, 16)
    block_stride = (8, 8)
    cell_size = (8, 8)
    nbins = 9

    hog = cv2.HOGDescriptor(win_size, block_size, block_stride, cell_size, nbins)
    descriptor = hog.compute(img)
    if descriptor is None:
        return None
    return descriptor.flatten().astype(np.float32)


# ---------------------------------------------------------------------------
# PCA via eigendecomposition (numpy only)
# ---------------------------------------------------------------------------

def _pca_transform(X: np.ndarray, n_components: int) -> np.ndarray:
    """Reduce dimensionality using PCA (eigendecomposition of covariance matrix).

    Args:
        X: (n_samples, n_features) feature matrix.
        n_components: Number of principal components to keep.

    Returns:
        (n_samples, n_components) projected matrix.
    """
    n_components = min(n_components, X.shape[0], X.shape[1])
    mean = X.mean(axis=0)
    X_centered = X - mean

    if X.shape[0] < X.shape[1]:
        # Gram matrix trick for efficiency when n_samples < n_features
        gram = X_centered @ X_centered.T
        eigenvalues, eigenvectors_small = np.linalg.eigh(gram)
        idx = np.argsort(eigenvalues)[::-1][:n_components]
        eigenvectors_small = eigenvectors_small[:, idx]
        components = X_centered.T @ eigenvectors_small
        norms = np.linalg.norm(components, axis=0, keepdims=True)
        norms[norms == 0] = 1.0
        components = components / norms
    else:
        cov = (X_centered.T @ X_centered) / max(X.shape[0] - 1, 1)
        eigenvalues, eigenvectors = np.linalg.eigh(cov)
        idx = np.argsort(eigenvalues)[::-1][:n_components]
        components = eigenvectors[:, idx]

    return X_centered @ components


# ---------------------------------------------------------------------------
# KMeans (Lloyd's algorithm, numpy only)
# ---------------------------------------------------------------------------

def _kmeans(X: np.ndarray, k: int, max_iter: int = 50, seed: int = 42) -> Tuple[np.ndarray, np.ndarray]:
    """K-Means clustering via Lloyd's algorithm.

    Args:
        X: (n_samples, n_features) data matrix.
        k: Number of clusters.
        max_iter: Maximum iterations.
        seed: Random seed for reproducibility.

    Returns:
        (labels, centroids) — assignments and final centroids.
    """
    rng = np.random.RandomState(seed)
    n = X.shape[0]
    k = min(k, n)

    # KMeans++ initialisation
    centroids = np.empty((k, X.shape[1]), dtype=X.dtype)
    centroids[0] = X[rng.randint(n)]
    for c in range(1, k):
        dists = np.min(
            np.linalg.norm(X[:, None, :] - centroids[None, :c, :], axis=2), axis=1
        )
        probs = dists ** 2
        probs_sum = probs.sum()
        if probs_sum == 0:
            centroids[c] = X[rng.randint(n)]
        else:
            probs /= probs_sum
            centroids[c] = X[rng.choice(n, p=probs)]

    labels = np.zeros(n, dtype=np.int32)
    for _ in range(max_iter):
        dists = np.linalg.norm(X[:, None, :] - centroids[None, :, :], axis=2)
        new_labels = np.argmin(dists, axis=1).astype(np.int32)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for c in range(k):
            members = X[labels == c]
            if len(members) > 0:
                centroids[c] = members.mean(axis=0)

    return labels, centroids


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def select_frames_by_clustering(
    frame_images: Dict[int, str],
    n_select: int,
    pca_components: int = 16,
) -> List[int]:
    """Select visually diverse frames using HOG + PCA + KMeans.

    Args:
        frame_images: Dict mapping frame_idx → image file path.
        n_select: Number of frames to select.
        pca_components: Number of PCA components.

    Returns:
        Sorted list of selected frame indices.
    """
    indices = sorted(frame_images.keys())
    if len(indices) <= n_select:
        logger.info("Clustering: fewer candidates (%d) than requested (%d) — using all",
                     len(indices), n_select)
        return indices

    # 1. Compute HOG features
    features = []
    valid_indices = []
    for idx in indices:
        hog_vec = _compute_hog(frame_images[idx])
        if hog_vec is not None:
            features.append(hog_vec)
            valid_indices.append(idx)

    if len(valid_indices) <= n_select:
        logger.info("Clustering: too few valid HOG vectors (%d) — returning all",
                     len(valid_indices))
        return sorted(valid_indices)

    X = np.vstack(features)
    logger.info("HOG matrix: %s (frames=%d, features=%d)", X.shape, len(valid_indices), X.shape[1])

    # 2. PCA
    X_pca = _pca_transform(X, n_components=pca_components)
    logger.info("PCA reduced to %d components", X_pca.shape[1])

    # 3. KMeans
    labels, centroids = _kmeans(X_pca, k=n_select)
    logger.info("KMeans cluster sizes: %s",
                [int((labels == c).sum()) for c in range(n_select)])

    # 4. Pick frame closest to each centroid
    selected = []
    for c in range(n_select):
        member_mask = labels == c
        if not member_mask.any():
            continue
        member_indices = np.where(member_mask)[0]
        member_features = X_pca[member_mask]
        dists = np.linalg.norm(member_features - centroids[c], axis=1)
        best_local = np.argmin(dists)
        best_global = member_indices[best_local]
        selected.append(valid_indices[best_global])

    # If some clusters were empty, pad from remaining frames
    if len(selected) < n_select:
        remaining = [i for i in valid_indices if i not in selected]
        selected.extend(remaining[: n_select - len(selected)])

    selected.sort()
    logger.info("Clustering selected %d frames: %s", len(selected), selected)
    return selected
