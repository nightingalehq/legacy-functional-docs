---
title: "Call graph"
doc_type: register
---

# Call graph

```mermaid
graph TD
    n_MMB0100_e0da8b["MMB0100"]
    n_NATBATCH_1ec14e(["NATBATCH (unresolved)"])
    n_MMB0100_e0da8b -.->|unresolved| n_NATBATCH_1ec14e
    n_MMP0100_8cf9a3["MMP0100"]
    n_MMB0100_e0da8b --> n_MMP0100_8cf9a3
    n_IDCAMS_143221(["IDCAMS (unresolved)"])
    n_MMB0100_e0da8b -.->|unresolved| n_IDCAMS_143221
    n_MMU0300_5a0cc4(["MMU0300 (unresolved)"])
    n_MMB0100_e0da8b -.->|unresolved| n_MMU0300_5a0cc4
    n_MMLDA01_0d625b(["MMLDA01 (unresolved)"])
    n_MMP0100_8cf9a3 -.->|unresolved| n_MMLDA01_0d625b
    n_MMN0250_191285(["MMN0250 (unresolved)"])
    n_MMP0100_8cf9a3 -.->|unresolved| n_MMN0250_191285
    n_MMN0900_91c85d(["MMN0900 (unresolved)"])
    n_MMP0100_8cf9a3 -.->|unresolved| n_MMN0900_91c85d
    n_WRITE_AUDIT_1d9441["WRITE-AUDIT"]
    n_MMP0100_8cf9a3 --> n_WRITE_AUDIT_1d9441
    n_MMP0200_1f64a3["MMP0200"]
    n_MMM0200_d74c33(["MMM0200 (unresolved)"])
    n_MMP0200_1f64a3 -.->|unresolved| n_MMM0200_d74c33
    n__PGM_3c367c(["#PGM (unresolved)"])
    n_MMP0200_1f64a3 -.->|unresolved| n__PGM_3c367c
    n_PDFGEN_d9cc91(["PDFGEN (unresolved)"])
    n_MMP0200_1f64a3 -.->|unresolved| n_PDFGEN_d9cc91
    n_MMP9100_de93b7["MMP9100"]
    n_MMC0100_3c03f8["MMC0100"]
    n_MMP9100_de93b7 --> n_MMC0100_3c03f8
    n_MMP9400_a303f1["MMP9400"]
    n_PROGA_5dc263(["PROGA (unresolved)"])
    n_MMP9400_a303f1 -.->|unresolved| n_PROGA_5dc263
    n_ORDENQ_64c39a["ORDENQ"]
    n_PRICECALC_388507(["PRICECALC (unresolved)"])
    n_ORDENQ_64c39a -.->|unresolved| n_PRICECALC_388507
    n_ORDSCR1_79daf7(["ORDSCR1 (unresolved)"])
    n_ORDENQ_64c39a -.->|unresolved| n_ORDSCR1_79daf7
    n_ORDSCR2_a3c2bc(["ORDSCR2 (unresolved)"])
    n_ORDENQ_64c39a -.->|unresolved| n_ORDSCR2_a3c2bc
    n_SCRNENT_fb658a["SCRNENT"]
    n_MAP_5af1a3(["MAP (unresolved)"])
    n_SCRNENT_fb658a -.->|unresolved| n_MAP_5af1a3
    n_STEEL_2762ee["STEEL"]
    n_NATCICS_1a3061(["NATCICS (unresolved)"])
    n_STEEL_2762ee -.->|unresolved| n_NATCICS_1a3061
    n_STEEL_2762ee --> n_MMP0200_1f64a3
```
