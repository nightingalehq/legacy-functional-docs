---
title: "natural — language guide"
doc_type: register
---

# natural — language guide

Every recognised construct actually in use in this codebase's `natural` source, grouped by keyword with a frequency count and one cited example each. Regenerate with `mfdoc lang-guide --config project.yml --dialect natural` after any source change; do not hand-edit. See `templates/language-guide.md` for the narrative tier that adds connective prose on top of this.

## Structure / declarations

| keyword | count | example |
|---|---|---|
| `A` | 19 | [[MMM0150:8]] `1 #ORDER-NO (A10)` |
| `N` | 9 | [[MMP0100:11]] `1 #RETURN-CODE       (N2)` |
| `D` | 2 | [[MMP0400:25]] `1 #TODAY             (D) INIT <*DATX>` |
| `P` | 1 | [[MMP0100:27]] `1 #AVAIL-TOTAL       (P9.3) INIT <0>` |

## Control flow

| keyword | count | example |
|---|---|---|
| `MOVE` | 16 | [[MMC0100:3]] `MOVE 99 TO #VALIDATION-RC` |
| `IF` | 8 | [[MMC0100:2]] `IF #GRADE-CODE = 'X9'` |
| `ESCAPE ROUTINE` | 4 | [[MMP0100:36]] `ESCAPE ROUTINE` |
| `IF NO RECORDS FOUND` | 3 | [[MMP0100:34]] `IF NO RECORDS FOUND` |
| `WHEN` | 3 | [[MMP0100:53]] `WHEN #AVAIL-TOTAL >= ORDER-VIEW.ORDER-WEIGHT` |
| `COMPRESS` | 2 | [[MMP9560:12]] `COMPRESS 'BATCH'` |
| `ADD` | 1 | [[MMP0100:48]] `ADD STOCK-VIEW.AVAIL-WEIGHT TO #AVAIL-TOTAL` |
| `ASSIGN` | 1 | [[MMP9800:13]] `#FLAG := 1` |
| `DECIDE FOR FIRST CONDITION` | 1 | [[MMP0100:52]] `DECIDE FOR FIRST CONDITION` |
| `ESCAPE BOTTOM` | 1 | [[MMP0100:45]] `ESCAPE BOTTOM` |
| `LOOP` | 1 | [[MMP9600:9]] `LOOP` |
| `ON ERROR` | 1 | [[MMP0200:24]] `ON ERROR` |
| `REJECT IF` | 1 | [[MMP0400:37]] `REJECT IF ORDER-VIEW.ORDER-STATUS = 'HELD'` |

## Data access (DML)

| keyword | count | example |
|---|---|---|
| `FIND` | 6 | [[MMP0100:33]] `FIND ORDER-VIEW WITH ORDER-NO = #ORDER-NO` |
| `UPDATE` | 3 | [[MMP0100:63]] `UPDATE ORDER-VIEW` |
| `STORE` | 2 | [[MMP0100:71]] `STORE AUDIT-VIEW` |
| `DELETE` | 1 | [[MMP9200:16]] `DELETE (X9.)` |
| `READ` | 1 | [[MMP0100:43]] `READ STOCK-VIEW BY GRADE-CODE STARTING FROM ORDER-VIEW.GRADE-CODE` |

### Entity relationships

None recorded.

## Screen interaction

| keyword | count | example |
|---|---|---|
| `MAP_FIELD` | 5 | [[MMM0150:14]] `1 F #ORDER-NO (AD=O) 01/16` |
| `MAP_TEXT` | 5 | [[MMM0150:13]] `1 T 'Order number:' 01/01` |
| `DISPLAY` | 3 | [[MMP0200:19]] `DISPLAY CERT-VIEW.HEAT-NO CERT-VIEW.CAST-DATE` |
| `INPUT` | 3 | [[MMP0200:11]] `INPUT USING MAP 'MMM0200'` |
| `WRITE` | 3 | [[MMP0200:25]] `WRITE 'Unexpected error' *ERROR-NR` |
| `REINPUT` | 2 | [[MMP0200:13]] `REINPUT FULL 'Certificate number is required' MARK 1` |

## Transactions

| keyword | count | example |
|---|---|---|
| `END TRANSACTION` | 3 | [[MMP0100:64]] `END TRANSACTION #ORDER-NO` |

## Calling conventions

| keyword | count | example |
|---|---|---|
| `CALLNAT` | 4 | [[MMP0100:57]] `CALLNAT 'MMN0250' #ORDER-NO #AVAIL-TOTAL #RETURN-CODE` |
| `INCLUDE` | 4 | [[MMP0100:7]] `LOCAL USING MMLDA01` |
| `CALL` | 1 | [[MMP0200:23]] `CALL 'PDFGEN' #CERT-NO` |
| `FETCH RETURN` | 1 | [[MMP0200:22]] `FETCH RETURN #PGM` |
| `PERFORM_INTERNAL` | 1 | [[MMP0100:68]] `PERFORM WRITE-AUDIT` |

## Not yet recognized

Seen in source, not yet matched to a known construct -- ranked by frequency; see `mfdoc calibrate --config project.yml --dialect natural` for the full list and where to add recognition.

| keyword | count | sample |
|---|---|---|
| `DOEND` | 2 | `DOEND` |
| `SETD.` | 1 | `SETD. FROBNICATE #STATUS` |
| `LOOP` | 1 | `LOOP` |

