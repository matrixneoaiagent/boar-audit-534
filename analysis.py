#!/usr/bin/env python3
"""
poidh #534 / on-chain 534 -- audit addendum:
the spec-required retrieval decomposition (U-ref / U-noref) and the
perfect-retrieval bound for DEVICE-FINAL.

Inputs (public):
  boar-research  rnd/research/coordinator/arms/native-rc/work/v04/bench/questions.jsonl
  boar-traces    rnd/research/coordinator/arms/device-final/traces/dev-s4{2,3}.traces.jsonl
                 rnd/research/coordinator/arms/device-final/judge/dev-s4{2,3}.{opus,codex}.judgments.jsonl
  boar-bench     bench/v0.4/refs/v2/{claude,codex}.jsonl

Reproduces the published U-total first, then computes:
  C = items whose trace ruler.kept is true (retrieval kept >=1 source)
  N = items whose trace ruler.kept is false
  U_C, U_N
  U_bound = (sum_C w*u + sum_N w*r) / sum_H w*r
            (the 7 N items scored exactly like their reference: an
             unattainable upper bound on what fixing retrieval could buy)
  dimension split on C: (app-1)/(ref-1) for correctness/completeness/usefulness

No model calls; stdlib only.
"""
import json, os, random, statistics, collections, sys

ROOT = os.environ.get("BOAR_ROOT", "/tmp/opencode/boar")
RES = os.path.join(ROOT, "res")
BEN = os.path.join(ROOT, "bench")
DF = os.path.join(RES, "rnd/research/coordinator/arms/device-final")
NR = os.path.join(RES, "rnd/research/coordinator/arms/native-rc")

rd = lambda p: [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]

Q = {r["id"]: r for r in rd(os.path.join(NR, "work/v04/bench/questions.jsonl"))}
IDS = list(Q)
H = [i for i in IDS if not Q[i]["anchor"]]
W = {i: Q[i]["weight"] for i in IDS}
FAMS = ("opus", "codex")


def load(fam, seed):
    tf = os.path.join(DF, f"traces/dev-s{seed}.traces.jsonl")
    jf = os.path.join(DF, f"judge/dev-s{seed}.{fam}.judgments.jsonl")
    T = {t["itemId"]: t for t in rd(tf)}
    per = collections.defaultdict(dict)
    for r in rd(jf):
        if r.get("ok"):
            per[r["queryId"]][r["order"]] = r["mapped"]
    out = {}
    for i in IDS:
        if len(per.get(i, {})) < 2:
            continue
        orders = []
        for m in per[i].values():
            orders.append(dict(
                ma=statistics.mean(m["boar"][k] for k in ("correctness", "completeness", "usefulness")),
                mr=statistics.mean(m["ref"][k] for k in ("correctness", "completeness", "usefulness")),
                ba={k: m["boar"][k] for k in ("correctness", "completeness", "usefulness")},
                ra={k: m["ref"][k] for k in ("correctness", "completeness", "usefulness")},
            ))
        out[i] = dict(z=bool(T[i]["ruler"].get("refusedZ")), orders=orders,
                      kept=bool(T[i]["ruler"].get("kept")))
    return out


def points(G):
    P = {}
    for i, e in G.items():
        o = e["orders"]
        P[i] = dict(
            u=0.0 if e["z"] else statistics.mean(x["ma"] for x in o) - 1,
            r=statistics.mean(x["mr"] for x in o) - 1,
            z=e["z"], kept=e["kept"],
            dims={k: statistics.mean(x["ba"][k] for x in o) - 1 for k in ("correctness", "completeness", "usefulness")},
            refd={k: statistics.mean(x["ra"][k] for x in o) - 1 for k in ("correctness", "completeness", "usefulness")},
        )
    return P


def U(P, S, w=None):
    w = w or W
    S = [i for i in S if i in P]
    d = sum(w[i] * P[i]["r"] for i in S)
    return sum(w[i] * P[i]["u"] for i in S) / d if d else None


def boot(fn, n=2000, seed=11):
    rnd = random.Random(seed)
    vals = []
    for _ in range(n):
        its = [rnd.choice(IDS) for _ in IDS]
        v = fn(its)
        if v is not None:
            vals.append(v)
    vals.sort()
    return [round(vals[int(.025 * len(vals))], 3), round(vals[int(.975 * len(vals)) - 1], 3)]


def analyse(P):
    C = [i for i in H if P[i]["kept"]]
    N = [i for i in H if not P[i]["kept"]]
    sc = sum(W[i] * P[i]["u"] for i in C)
    sn = sum(W[i] * P[i]["u"] for i in N)
    rc = sum(W[i] * P[i]["r"] for i in C)
    rn = sum(W[i] * P[i]["r"] for i in N)
    out = dict(n_H=len(H), n_C=len(C), n_N=len(N),
               U_H=(sc + sn) / (rc + rn), U_C=sc / rc, U_N=sn / rn,
               U_bound=(sc + rn) / (rc + rn),
               C=C, N=N,
               dims={k: (sum(W[i] * P[i]["dims"][k] for i in C) / sum(W[i] for i in C),
                         sum(W[i] * P[i]["refd"][k] for i in C) / sum(W[i] for i in C))
                     for k in ("correctness", "completeness", "usefulness")})
    out["U_C_ci"] = boot(lambda its: U(P, [i for i in C if i in set(its)]))
    out["U_bound_ci"] = boot(lambda its: (
        (sum(W[i] * P[i]["u"] for i in C if i in set(its)) + sum(W[i] * P[i]["r"] for i in N if i in set(its)))
        / max(1e-9, sum(W[i] * P[i]["r"] for i in H if i in set(its)))))
    return out


if __name__ == "__main__":
    res = {}
    for fam in FAMS:
        for seed in ("42", "43"):
            P = points(load(fam, seed))
            res[f"{fam}_s{seed}"] = analyse(P)
        # pooled: concatenate both seeds' orders per item (same rule as m1_agg.pool)
        G1, G2 = load(fam, "42"), load(fam, "43")
        PO = {}
        for i in IDS:
            es = [g[i] for g in (G1, G2) if i in g]
            if es:
                PO[i] = dict(z=any(e["z"] for e in es) and all(e["z"] for e in es),
                             orders=[o for e in es for o in e["orders"]],
                             kept=all(e["kept"] for e in es))
        res[f"{fam}_pooled"] = analyse(points(PO))
    json.dump(res, open(os.path.join(ROOT, "uc_un.json"), "w"), indent=1, default=str)

    print(f"{'family':7} {'seed':7} {'U_H':>6} {'U_C':>6} {'U_N':>6} {'U_bound':>7} {'nC':>3} {'nN':>3}")
    for k, v in res.items():
        fam, seed = k.rsplit("_", 1)
        print(f"{fam:7} {seed:7} {v['U_H']:.4f} {v['U_C']:.4f} {v['U_N']:.4f} {v['U_bound']:.4f} {v['n_C']:3d} {v['n_N']:3d}")
    print("\nCIs (pooled):")
    for fam in FAMS:
        v = res[f"{fam}_pooled"]
        print(fam, "U_C", round(v['U_C'], 4), v['U_C_ci'], "U_bound", round(v['U_bound'], 4), v['U_bound_ci'])
    print("\ndimension split on C (pooled): app-1 / ref-1 / ratio")
    for fam in FAMS:
        v = res[f"{fam}_pooled"]
        print(fam)
        for k, (a, r) in v["dims"].items():
            print(f"  {k:13} {a:.3f} / {r:.3f} / {a/r:.3f}")
