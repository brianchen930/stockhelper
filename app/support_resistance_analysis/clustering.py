"""Deterministic one-dimensional Lloyd K-Means; no sklearn dependency."""
import numpy as np
from .models import Candidate


def cluster_prices(points, config):
    points = [p for p in points if np.isfinite(p.price) and p.price > 0]
    unique = np.unique([p.price for p in points])
    if len(unique) < config.kmeans_min_points:
        return []
    values = np.array([p.price for p in points])
    weights = np.array([max(p.weight, config.kmeans_convergence) for p in points])
    k = min(config.kmeans_max_clusters, max(1, int(np.sqrt(len(unique)))))
    centers = np.quantile(unique, np.linspace(0, 1, k))
    for _ in range(config.kmeans_iterations):
        labels = abs(values[:, None]-centers).argmin(axis=1)
        updated = np.array([np.average(values[labels == i], weights=weights[labels == i])
                            if (labels == i).any() else centers[i] for i in range(k)])
        if np.allclose(updated, centers, atol=config.kmeans_convergence, rtol=0):
            centers = updated
            break
        centers = updated
    labels = abs(values[:, None]-centers).argmin(axis=1)
    result = []
    for i, center in enumerate(centers):
        members = [p for p, label in zip(points, labels) if label == i]
        if members:
            result.append(Candidate(float(center), 'kmeans', 'kmeans', max(p.position for p in members),
                                    max(p.confirmed_position for p in members)))
    return result
