from __future__ import annotations

from collections import defaultdict


class GraphifyCore:
    engine_name = "mimry-graphify-core"

    def build_graph(self, files, symbols, edges):
        nodes = []
        graph_edges = []
        for f in files:
            nodes.append({"id": f"file:{f['file_id']}", "type": "file", "label": f['rel_path'], "path": f['rel_path']})
        for s in symbols:
            nodes.append({"id": f"symbol:{s['symbol_id']}", "type": "symbol", "label": s['name'], "kind": s['kind']})
        for e in edges:
            graph_edges.append({"from": f"{e['source_type']}:{e['source_id']}", "to": f"{e['target_type']}:{e['target_id']}", "type": e['edge_type'], "confidence": e['confidence']})
        return {"engine": self.engine_name, "nodes": nodes, "edges": graph_edges, "clusters": self.cluster_by_folder(files)}

    def cluster_by_folder(self, files):
        clusters = defaultdict(list)
        for f in files:
            folder = f['rel_path'].rsplit('/', 1)[0] if '/' in f['rel_path'] else '.'
            clusters[folder].append(f['rel_path'])
        return dict(sorted(clusters.items()))
