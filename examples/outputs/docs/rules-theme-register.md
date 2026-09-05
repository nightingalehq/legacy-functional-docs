---
title: "Rules register — by theme"
doc_type: register
---

# Rules register — by theme

Every candidate business rule, grouped by business theme instead of by module. Same `MEMBER:BR-nnn` IDs as the per-module docs and `mfdoc rules-register`. Regenerate with `mfdoc rules-theme-register` after any source or taxonomy change; do not hand-edit.

## MILLPROD

31 rule(s) (31 structural)

| BR-ID | member | line | depth | construct | condition | literals |
|---|---|---|---|---|---|---|
| **MMC0100:BR-001** | `MMC0100` | [[MMC0100:2]] | 0 | `IF` | `#GRADE-CODE = 'X9'` | `X9` |
| **MMC0100:BR-002** | `MMC0100` | [[MMC0100:3]] | 1 | `MOVE` | `MOVE 99 TO #VALIDATION-RC` | `99` |
| **MMP0100:BR-001** | `MMP0100` | [[MMP0100:34]] | 0 | `IF NO RECORDS FOUND` | `no records found for preceding database loop` | `` |
| **MMP0100:BR-002** | `MMP0100` | [[MMP0100:35]] | 1 | `MOVE` | `MOVE 10 TO #RETURN-CODE` | `10` |
| **MMP0100:BR-003** | `MMP0100` | [[MMP0100:36]] | 1 | `ESCAPE ROUTINE` | `` | `` |
| **MMP0100:BR-004** | `MMP0100` | [[MMP0100:38]] | 0 | `IF` | `ORDER-VIEW.ORDER-STATUS NE 'CONF'` | `CONF` |
| **MMP0100:BR-005** | `MMP0100` | [[MMP0100:39]] | 1 | `MOVE` | `MOVE 20 TO #RETURN-CODE` | `20` |
| **MMP0100:BR-006** | `MMP0100` | [[MMP0100:40]] | 1 | `ESCAPE ROUTINE` | `` | `` |
| **MMP0100:BR-007** | `MMP0100` | [[MMP0100:44]] | 0 | `IF` | `STOCK-VIEW.GRADE-CODE NE ORDER-VIEW.GRADE-CODE` | `` |
| **MMP0100:BR-008** | `MMP0100` | [[MMP0100:45]] | 1 | `ESCAPE BOTTOM` | `` | `` |
| **MMP0100:BR-009** | `MMP0100` | [[MMP0100:47]] | 0 | `IF` | `STOCK-VIEW.PLANT-CODE = #PLANT` | `` |
| **MMP0100:BR-010** | `MMP0100` | [[MMP0100:52]] | 0 | `DECIDE FOR FIRST CONDITION` | `` | `` |
| **MMP0100:BR-011** | `MMP0100` | [[MMP0100:53]] | 1 | `WHEN` | `#AVAIL-TOTAL >= ORDER-VIEW.ORDER-WEIGHT` | `` |
| **MMP0100:BR-012** | `MMP0100` | [[MMP0100:54]] | 1 | `MOVE` | `MOVE 'RLSD' TO ORDER-VIEW.ORDER-STATUS` | `RLSD` |
| **MMP0100:BR-013** | `MMP0100` | [[MMP0100:55]] | 1 | `WHEN` | `#AVAIL-TOTAL >= ORDER-VIEW.ORDER-WEIGHT * (1 - #TOLERANCE-PCT / 100)` | `1,100` |
| **MMP0100:BR-014** | `MMP0100` | [[MMP0100:56]] | 1 | `MOVE` | `MOVE 'PART' TO ORDER-VIEW.ORDER-STATUS` | `PART` |
| **MMP0100:BR-015** | `MMP0100` | [[MMP0100:58]] | 1 | `WHEN` | `NONE` | `` |
| **MMP0100:BR-016** | `MMP0100` | [[MMP0100:59]] | 1 | `MOVE` | `MOVE 30 TO #RETURN-CODE` | `30` |
| **MMP0100:BR-017** | `MMP0100` | [[MMP0100:60]] | 1 | `ESCAPE ROUTINE` | `` | `` |
| **MMP0200:BR-001** | `MMP0200` | [[MMP0200:12]] | 0 | `IF` | `#CERT-NO = ' '` | ` ` |
| **MMP0200:BR-002** | `MMP0200` | [[MMP0200:16]] | 0 | `IF NO RECORDS FOUND` | `no records found for preceding database loop` | `` |
| **MMP0200:BR-003** | `MMP0200` | [[MMP0200:21]] | 0 | `MOVE` | `MOVE 'MMP0300' TO #PGM` | `MMP0300` |
| **MMP0200:BR-004** | `MMP0200` | [[MMP0200:24]] | 0 | `ON ERROR` | `` | `` |
| **MMP9000:BR-001** | `MMP9000` | [[MMP9000:14]] | 0 | `IF` | `ORDER-VIEW.ORDER-STATUS = 'CONF' AND ORDER-VIEW.CUSTOMER-NO = 'C00123'` | `CONF,C00123` |
| **MMP9000:BR-002** | `MMP9000` | [[MMP9000:16]] | 1 | `MOVE` | `MOVE 1 TO #FLAG` | `1` |
| **MMP9300:BR-001** | `MMP9300` | [[MMP9300:12]] | 0 | `IF` | `#STATUS = 'A'` | `A` |
| **MMP9400:BR-001** | `MMP9400` | [[MMP9400:9]] | 0 | `MOVE` | `MOVE 'CONF' TO #STATUS` | `CONF` |
| **MMP9400:BR-002** | `MMP9400` | [[MMP9400:11]] | 0 | `IF` | `#STATUS = 'CONF'` | `CONF` |
| **MMP9600:BR-001** | `MMP9600` | [[MMP9600:9]] | 0 | `LOOP` | `` | `` |
| **MMP9800:BR-001** | `MMP9800` | [[MMP9800:13]] | 0 | `ASSIGN` | `#FLAG := 1` | `1` |
| **MMP9800:BR-002** | `MMP9800` | [[MMP9800:18]] | 0 | `COMPRESS` | `COMPRESS 'A' 'B' INTO #MESSAGE` | `A,B` |

## STEELLIB

13 rule(s) (13 structural)

| BR-ID | member | line | depth | construct | condition | literals |
|---|---|---|---|---|---|---|
| **ORDENQ:BR-001** | `ORDENQ` | [[ORDENQ:11]] | 0 | `IF` | `ORDER_NO = " "` | ` ` |
| **ORDENQ:BR-002** | `ORDENQ` | [[ORDENQ:12]] | 1 | `ASSIGN` | `MSG = "Order number required"` | `Order number required` |
| **ORDENQ:BR-003** | `ORDENQ` | [[ORDENQ:16]] | 0 | `IF` | `STATUS <> 0` | `0` |
| **ORDENQ:BR-004** | `ORDENQ` | [[ORDENQ:17]] | 1 | `ASSIGN` | `MSG = "Order not found"` | `Order not found` |
| **ORDENQ:BR-005** | `ORDENQ` | [[ORDENQ:21]] | 0 | `WHILE` | `STATUS = 0` | `0` |
| **ORDENQ:BR-006** | `ORDENQ` | [[ORDENQ:22]] | 1 | `ASSIGN` | `ORDER_WT = ORDER_WT + ORDVIEW.LINE_WT` | `` |
| **ORDENQ:BR-007** | `ORDENQ` | [[ORDENQ:25]] | 0 | `CASE` | `ORDVIEW.STATUS` | `` |
| **ORDENQ:BR-008** | `ORDENQ` | [[ORDENQ:26]] | 1 | `WHEN` | `"CONF"` | `CONF` |
| **ORDENQ:BR-009** | `ORDENQ` | [[ORDENQ:28]] | 1 | `WHEN` | `"HELD"` | `HELD` |
| **ORDENQ:BR-010** | `ORDENQ` | [[ORDENQ:29]] | 1 | `ASSIGN` | `MSG = "Order is on credit hold"` | `Order is on credit hold` |
| **ORDENQ:BR-011** | `ORDENQ` | [[ORDENQ:37]] | 0 | `IF` | `ORDER_WT > 500 OR CUST_NO = " "` | ` ,500` |
| **ORDENQ:BR-012** | `ORDENQ` | [[ORDENQ:39]] | 1 | `ASSIGN` | `MSG = "Credit check required"` | `Credit check required` |
| **SCRNENT:BR-001** | `SCRNENT` | [[SCRNENT:7]] | 0 | `IF` | `CH_UNIT = " "` | ` ` |

Total: 44 rule candidate(s) across 2 theme(s).

