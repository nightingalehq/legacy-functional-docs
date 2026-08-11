---
title: "System-wide test-plan register"
doc_type: register
---

# System-wide test-plan register

Every scenario `mfdoc test-plan` derived from the fact store, keyed by the same `MEMBER:BR-nnn` id its source rule carries in the module doc and rules register. Regenerate with `mfdoc test-plan` after any source change; do not hand-edit. `status` defaults to `characterization` until a human promotes an entry via `test-overlay.yml`.

| scenario | member | kind | status | construct | condition | citation |
|---|---|---|---|---|---|---|
| `MMC0100:BR-001` | `MMC0100` | unit | characterization | `IF` | `#GRADE-CODE = 'X9'` | [[MMC0100:2]] |
| `MMP0100:BR-001` | `MMP0100` | unit | characterization | `IF NO RECORDS FOUND` | `no records found for preceding database loop` | [[MMP0100:34]] |
| `MMP0100:BR-004` | `MMP0100` | unit | characterization | `IF` | `ORDER-VIEW.ORDER-STATUS NE 'CONF'` | [[MMP0100:38]] |
| `MMP0100:BR-007` | `MMP0100` | unit | characterization | `IF` | `STOCK-VIEW.GRADE-CODE NE ORDER-VIEW.GRADE-CODE` | [[MMP0100:44]] |
| `MMP0100:BR-009` | `MMP0100` | unit | characterization | `IF` | `STOCK-VIEW.PLANT-CODE = #PLANT` | [[MMP0100:47]] |
| `MMP0100:BR-011` | `MMP0100` | unit | characterization | `WHEN` | `#AVAIL-TOTAL >= ORDER-VIEW.ORDER-WEIGHT` | [[MMP0100:53]] |
| `MMP0100:BR-013` | `MMP0100` | unit | characterization | `WHEN` | `#AVAIL-TOTAL >= ORDER-VIEW.ORDER-WEIGHT * (1 - #TOLERANCE-PCT / 100)` | [[MMP0100:55]] |
| `MMP0100:BR-015` | `MMP0100` | unit | characterization | `WHEN` | `NONE` | [[MMP0100:58]] |
| `MMP0200:BR-001` | `MMP0200` | unit | characterization | `IF` | `#CERT-NO = ' '` | [[MMP0200:12]] |
| `MMP0200:BR-002` | `MMP0200` | unit | characterization | `IF NO RECORDS FOUND` | `no records found for preceding database loop` | [[MMP0200:16]] |
| `MMP0200:BR-004` | `MMP0200` | unit | characterization | `ON ERROR` | `` | [[MMP0200:24]] |
| `MMP9000:BR-001` | `MMP9000` | unit | characterization | `IF` | `ORDER-VIEW.ORDER-STATUS = 'CONF' AND ORDER-VIEW.CUSTOMER-NO = 'C00123'` | [[MMP9000:14]] |
| `MMP9300:BR-001` | `MMP9300` | unit | characterization | `IF` | `#STATUS = 'A'` | [[MMP9300:12]] |
| `MMP9400:BR-002` | `MMP9400` | unit | characterization | `IF` | `#STATUS = 'CONF'` | [[MMP9400:11]] |
| `ORDENQ:BR-001` | `ORDENQ` | unit | characterization | `IF` | `ORDER_NO = " "` | [[ORDENQ:11]] |
| `ORDENQ:BR-002` | `ORDENQ` | unit | characterization | `IF` | `STATUS <> 0` | [[ORDENQ:16]] |
| `ORDENQ:BR-004` | `ORDENQ` | unit | characterization | `CASE` | `ORDVIEW.STATUS` | [[ORDENQ:25]] |
| `ORDENQ:BR-005` | `ORDENQ` | unit | characterization | `WHEN` | `"CONF"` | [[ORDENQ:26]] |
| `ORDENQ:BR-006` | `ORDENQ` | unit | characterization | `WHEN` | `"HELD"` | [[ORDENQ:28]] |


