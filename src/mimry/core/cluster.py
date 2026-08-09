from __future__ import annotations

from collections import Counter, defaultdict


def assign_communities(nodes: list[dict], edges: list[dict]) -> list[dict]:
    """Return nodes with a stable integer 'community' assigned to each.

    Uses label propagation over the undirected edge list. Nodes with no edges
    each keep their own singleton community.
    """
    if not nodes:
        return []

    # Build set of valid node ids for validation
    node_ids = {node["id"] for node in nodes}

    # Build undirected adjacency map
    adjacency = defaultdict(set)
    for edge in edges:
        source = edge.get("source")
        target = edge.get("target")
        # Skip edges with missing or invalid endpoints
        if source not in node_ids or target not in node_ids:
            continue
        # Add both directions for undirected graph
        adjacency[source].add(target)
        adjacency[target].add(source)

    # Initialize: each node's label is its own id
    labels = {node["id"]: node["id"] for node in nodes}

    # Label propagation with convergence check or max 20 iterations
    for _ in range(20):
        new_labels = {}
        changed = False

        # Process nodes in sorted order for determinism
        for node_id in sorted(labels.keys()):
            neighbors = adjacency[node_id]

            if not neighbors:
                # Isolated node keeps its own label
                new_labels[node_id] = labels[node_id]
            else:
                # Collect labels of neighbors
                neighbor_labels = [labels[neighbor] for neighbor in neighbors]
                # Add node's own label to the pool for tie-breaking stability
                neighbor_labels.append(labels[node_id])

                # Count label frequencies
                label_counts = Counter(neighbor_labels)

                # Find the maximum frequency
                max_count = max(label_counts.values())

                # Get all labels with maximum frequency
                candidates = [label for label, count in label_counts.items() if count == max_count]

                # Choose lexicographically smallest among ties
                best_label = min(candidates)
                new_labels[node_id] = best_label

                # Track if anything changed
                if best_label != labels[node_id]:
                    changed = True

        labels = new_labels

        # Stop if no changes (converged)
        if not changed:
            break

    # Map final label strings to small integers in lexicographic order
    unique_labels = sorted(set(labels.values()))
    label_to_community = {label: idx for idx, label in enumerate(unique_labels)}

    # Build result: new list of new dicts with community assigned
    result = []
    for node in nodes:
        new_node = dict(node)  # Create a new dict, don't mutate input
        new_node["community"] = label_to_community[labels[node["id"]]]
        result.append(new_node)

    return result
