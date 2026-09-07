---
title: "Complexity/risk heatmap"
doc_type: register
---

# Complexity/risk heatmap

risk_score = (rule_count + max_depth) * (in_degree + out_degree + 1), normalized 0-100 across this run's members. A simple v1 proxy for where to focus review, not a certified complexity metric.

| member | rule_count | max_depth | in_degree | out_degree | risk_score |
|---|---|---|---|---|---|
| `ORDENQ` | 12 | 1 | 0 | 8 | 100.0 |
| `MMP0100` | 17 | 1 | 1 | 4 | 92.3 |
| `MMP0400` | 6 | 1 | 0 | 2 | 17.9 |
| `MMP0200` | 4 | 0 | 1 | 3 | 17.1 |
| `PRODSCHED` | 3 | 1 | 1 | 0 | 6.8 |
| `MMC0100` | 2 | 1 | 1 | 0 | 5.1 |
| `MMP9400` | 2 | 0 | 0 | 1 | 3.4 |
| `MMP9000` | 2 | 1 | 0 | 0 | 2.6 |
| `SCRNENT` | 1 | 0 | 0 | 2 | 2.6 |
| `MMP9800` | 2 | 0 | 0 | 0 | 1.7 |
| `MMP9300` | 1 | 0 | 0 | 0 | 0.9 |
| `MMP9600` | 1 | 0 | 0 | 0 | 0.9 |

