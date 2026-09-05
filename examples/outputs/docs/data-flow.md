---
title: "Data-flow diagram"
doc_type: register
---

# Data-flow diagram

Module-to-entity read/write edges, derived from every recorded data-access statement. Regenerate with `mfdoc data-flow` after any source change; do not hand-edit.

```mermaid
graph LR
    n_MMP0100_8cf9a3["MMP0100"]
    n_MILL_ORDER_ddc95d[("MILL-ORDER")]
    n_MMP0100_8cf9a3 -->|R,U| n_MILL_ORDER_ddc95d
    n_ORDER_AUDIT_2ce887[("ORDER-AUDIT")]
    n_MMP0100_8cf9a3 -->|C| n_ORDER_AUDIT_2ce887
    n_STOCK_BALANCE_2d1f3c[("STOCK-BALANCE")]
    n_MMP0100_8cf9a3 -->|R| n_STOCK_BALANCE_2d1f3c
    n_MMP0200_1f64a3["MMP0200"]
    n_MILL_CERT_e845a2[("MILL-CERT")]
    n_MMP0200_1f64a3 -->|R| n_MILL_CERT_e845a2
    n_MMP9200_73eb78["MMP9200"]
    n_MMP9200_73eb78 -->|R,U| n_MILL_ORDER_ddc95d
    n_MMP9600_41a009["MMP9600"]
    n_MMP9600_41a009 -->|R| n_MILL_ORDER_ddc95d
    n_MMP9700_196c79["MMP9700"]
    n_MMP9700_196c79 -->|R| n_MILL_ORDER_ddc95d
    n_ORDENQ_64c39a["ORDENQ"]
    n_ORDERMST_bbae13[("ORDERMST")]
    n_ORDENQ_64c39a -->|R,U| n_ORDERMST_bbae13
    n_ORDLINE_3e21f6[("ORDLINE")]
    n_ORDENQ_64c39a -->|R| n_ORDLINE_3e21f6
```

