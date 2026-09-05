---
title: "Complexity/risk heatmap"
doc_type: register
---

# Complexity/risk heatmap

risk_score = (rule_count + max_depth) * (in_degree + out_degree + 1), normalized 0-100 across this run's members. A simple v1 proxy for where to focus review, not a certified complexity metric.

| member | rule_count | max_depth | in_degree | out_degree | risk_score |
|---|---|---|---|---|---|
| `MMP0100` | 17 | 1 | 1 | 4 | 100.0 |
| `ORDENQ` | 12 | 1 | 0 | 7 | 96.3 |
| `MMP0200` | 4 | 0 | 1 | 3 | 18.5 |
| `MMC0100` | 2 | 1 | 1 | 0 | 5.6 |
| `MMP9400` | 2 | 0 | 0 | 1 | 3.7 |
| `MMP9000` | 2 | 1 | 0 | 0 | 2.8 |
| `SCRNENT` | 1 | 0 | 0 | 2 | 2.8 |
| `MMP9800` | 2 | 0 | 0 | 0 | 1.9 |
| `MMP9300` | 1 | 0 | 0 | 0 | 0.9 |
| `MMP9600` | 1 | 0 | 0 | 0 | 0.9 |

