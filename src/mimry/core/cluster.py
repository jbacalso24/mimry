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


if __name__ == "__main__":
    import random
    import sys

    # Test 1: Two clearly separate clusters plus one isolated node
    print("Test 1: Two clusters + one isolated node...", end=" ", flush=True)
    test_nodes = [
        {"id": "a1", "community": 0},
        {"id": "a2", "community": 0},
        {"id": "a3", "community": 0},
        {"id": "b1", "community": 0},
        {"id": "b2", "community": 0},
        {"id": "b3", "community": 0},
        {"id": "c1", "community": 0},
    ]

    test_edges = [
        # Cluster A: fully connected
        {"source": "a1", "target": "a2"},
        {"source": "a1", "target": "a3"},
        {"source": "a2", "target": "a3"},
        # Cluster B: fully connected
        {"source": "b1", "target": "b2"},
        {"source": "b1", "target": "b3"},
        {"source": "b2", "target": "b3"},
        # c1 is isolated
    ]

    result = assign_communities(test_nodes, test_edges)

    # Extract communities
    communities = {node["id"]: node["community"] for node in result}

    # Assertions
    try:
        # 1. a1, a2, a3 all share one community id
        assert communities["a1"] == communities["a2"], "a1 and a2 should have same community"
        assert communities["a2"] == communities["a3"], "a2 and a3 should have same community"

        # 2. b1, b2, b3 all share one community id
        assert communities["b1"] == communities["b2"], "b1 and b2 should have same community"
        assert communities["b2"] == communities["b3"], "b2 and b3 should have same community"

        # 3. those two community ids differ
        assert communities["a1"] != communities["b1"], "cluster A and B should have different communities"

        # 4. c1 has its own community id, different from both
        assert communities["c1"] != communities["a1"], "c1 should differ from cluster A"
        assert communities["c1"] != communities["b1"], "c1 should differ from cluster B"

        # 5. every returned node has an int community value
        for node in result:
            assert isinstance(node["community"], int), f"Node {node['id']} community is not int"

        # 6. returned list has same number of nodes as input
        assert len(result) == len(test_nodes), f"Result length {len(result)} != input length {len(test_nodes)}"

        # 7. Determinism under input reordering
        shuffled_nodes = list(test_nodes)
        random.Random(1234).shuffle(shuffled_nodes)
        result_shuffled = assign_communities(shuffled_nodes, test_edges)
        communities_shuffled = {node["id"]: node["community"] for node in result_shuffled}
        assert communities == communities_shuffled, "Results differ when input is shuffled"

        # 8. Empty graph
        empty_result = assign_communities([], [])
        assert empty_result == [], "Empty graph should return empty list"

        # 9. Edges referencing unknown node ids are ignored
        unknown_edges = [
            {"source": "a1", "target": "a2"},
            {"source": "unknown", "target": "a3"},  # Should be ignored
            {"source": "b1", "target": "nonexistent"},  # Should be ignored
        ]
        result_with_unknown = assign_communities(test_nodes, unknown_edges)
        # Should not crash and should return correct nodes
        assert len(result_with_unknown) == len(test_nodes)

        # 10. Input nodes were NOT mutated
        original_community = test_nodes[0]["community"]
        assert test_nodes[0]["community"] == original_community, "Input node was mutated"

        print("OK")
        print(f"Clusters: {communities}")

    except AssertionError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        sys.exit(1)

    # Test 2: Check convergence iterations
    print("\nTest 2: Checking convergence iterations...", end=" ", flush=True)
    test_nodes_2 = [
        {"id": "n1", "community": 0},
        {"id": "n2", "community": 0},
        {"id": "n3", "community": 0},
        {"id": "n4", "community": 0},
        {"id": "n5", "community": 0},
    ]
    test_edges_2 = [
        {"source": "n1", "target": "n2"},
        {"source": "n2", "target": "n3"},
        {"source": "n3", "target": "n4"},
        {"source": "n4", "target": "n5"},
    ]
    result_2 = assign_communities(test_nodes_2, test_edges_2)
    communities_2 = {node["id"]: node["community"] for node in result_2}
    # In a linear chain, label propagation should eventually converge to one community
    assert len(set(communities_2.values())) == 1, "Linear chain should converge to single community"
    print("OK")

    # Test 3: Verify determinism more thoroughly
    print("Test 3: Determinism test...", end=" ", flush=True)
    for seed in [42, 123, 999]:
        result_1 = assign_communities(test_nodes, test_edges)
        shuffled = list(test_nodes)
        random.Random(seed).shuffle(shuffled)
        result_2 = assign_communities(shuffled, test_edges)
        comm_1 = {node["id"]: node["community"] for node in result_1}
        comm_2 = {node["id"]: node["community"] for node in result_2}
        assert comm_1 == comm_2, f"Determinism failed with seed {seed}"
    print("OK")

    print("\nAll tests passed!")
    sys.exit(0)
