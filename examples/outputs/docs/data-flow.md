---
title: "Data-flow diagram"
doc_type: register
---

# Data-flow diagram

Module-to-entity read/write edges, derived from every recorded data-access statement. Regenerate with `mfdoc data-flow` after any source change; do not hand-edit.

```mermaid
graph LR
    n_member_4["MMP0100"]
    n_MILL_ORDER_ddc95d[("MILL-ORDER")]
    n_member_4 -->|R,U| n_MILL_ORDER_ddc95d
    n_ORDER_AUDIT_2ce887[("ORDER-AUDIT")]
    n_member_4 -->|C| n_ORDER_AUDIT_2ce887
    n_STOCK_BALANCE_2d1f3c[("STOCK-BALANCE")]
    n_member_4 -->|R| n_STOCK_BALANCE_2d1f3c
    n_member_5["MMP0200"]
    n_MILL_CERT_e845a2[("MILL-CERT")]
    n_member_5 -->|R| n_MILL_CERT_e845a2
    n_member_6["MMP0400"]
    n_member_6 -->|R,U| n_MILL_ORDER_ddc95d
    n_QUALITY_HOLD_d666dd[("QUALITY-HOLD")]
    n_member_6 -->|C| n_QUALITY_HOLD_d666dd
    n_member_9["MMP9200"]
    n_member_9 -->|R,U| n_MILL_ORDER_ddc95d
    n_member_13["MMP9600"]
    n_member_13 -->|R| n_MILL_ORDER_ddc95d
    n_member_14["MMP9700"]
    n_member_14 -->|R| n_MILL_ORDER_ddc95d
    n_member_22["ORDENQ"]
    n_ORDERMST_bbae13[("ORDERMST")]
    n_member_22 -->|R,U| n_ORDERMST_bbae13
    n_ORDLINE_3e21f6[("ORDLINE")]
    n_member_22 -->|R| n_ORDLINE_3e21f6
    n_member_23["PRODSCHED"]
    n_SCHEDVIEW_9edf00[("SCHEDVIEW")]
    n_member_23 -->|C| n_SCHEDVIEW_9edf00
```

