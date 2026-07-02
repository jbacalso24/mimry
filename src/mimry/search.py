from __future__ import annotations

import json

from .storage import load_jsonl

def score(f, q):
    terms=[t.lower() for t in q.replace("_"," ").replace("-"," ").split() if t]; h={"filename":f["filename"].lower(),"path":f["rel_path"].lower(),"content hint":f.get("content_hint","").lower(),"metadata":f.get("metadata_text","").lower()}; s=0; reasons=[]
    for term in terms:
        for label,text in h.items():
            if term in text: s += {"filename":30,"path":20,"content hint":10,"metadata":6}[label]; reasons.append(f"{label} match") if f"{label} match" not in reasons else None
    if s and f["adapter"] != "generic": s += 8; reasons.append(f["adapter"]+" adapter")
    return s, reasons

def find_rows(idx, q, limit=10, graph=False):
    rows=[]; clusters={}
    if graph and (idx/"graph.json").exists(): clusters=json.loads((idx/"graph.json").read_text()).get("clusters", {})
    for f in load_jsonl(idx/"files.jsonl"):
        s, rs=score(f,q)
        if s:
            folder=f["rel_path"].rsplit("/",1)[0] if "/" in f["rel_path"] else "."
            if graph and folder in clusters: s += 5; rs.append("Graphify cluster relationship")
            rows.append({"path":f["rel_path"],"score":s,"reason":", ".join(rs)})
    return sorted(rows, key=lambda r:(-r["score"], r["path"]))[:limit]

def print_rows(title, rows):
    print(title)
    for i,r in enumerate(rows,1): print(f"{i}. {r['path']}\n   Score: {r['score']}\n   Reason: {r['reason']}")
