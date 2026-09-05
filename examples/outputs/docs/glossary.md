---
title: "Glossary"
doc_type: register
---

# Glossary

Every known entity (Adabas file, DDM, table, dataset, ...) and its fields, deduplicated across the whole system. A name shared by more than one kind (e.g. a DDM and its underlying Adabas file) gets one heading per kind, labelled accordingly. Regenerate with `mfdoc glossary` after any source change; do not hand-edit.

### GRADE_MASTER

- kind: `sql_table`

| field | format | length | remark |
|---|---|---|---|
| `DESCRIPTION` | VARCHAR | 40 |  |
| `GRADE_CODE` | CHAR | 6 | NOT NULL |

### MILL-CERT

- kind: `ddm`

### MILL-ORDER (adabas_file)

- kind: `adabas_file`

| field | format | length | remark |
|---|---|---|---|
| `AA` | A | 10 |  |
| `AB` | A | 8 |  |
| `AC` |  |  |  |
| `AD` | A | 6 |  |
| `AE` | A | 4 |  |
| `AF` | P | 5 |  |
| `AG` | A | 6 |  |
| `AH` | A | 12 |  |
| `AI` |  |  |  |
| `AJ` | A | 6 |  |
| `AK` | P | 4 |  |
| `S1` |  |  |  |

### MILL-ORDER (ddm)

- kind: `ddm`
- notes: default sequence: AA

| field | format | length | remark |
|---|---|---|---|
| `CUSTOMER-NO` | A | 8 | N D |
| `DEL-DATE` | D | 6 |  |
| `DEL-QTY` | P | 7.3 |  |
| `DELIVERY` |  |  |  |
| `DUE-DATE` | D | 6 | D |
| `GRADE-CODE` | A | 6 | D |
| `ORDER-CUST-KEY` |  |  |  |
| `ORDER-DETAIL` |  |  |  |
| `ORDER-NO` | A | 10 | D primary order key |
| `ORDER-STATUS` | A | 4 | N |
| `ORDER-WEIGHT` | P | 9.3 |  |
| `ROUTE-STEP` | A | 12 | N |

### MILL_CERT

- kind: `sql_table`

| field | format | length | remark |
|---|---|---|---|
| `CAST_DATE` | DATE |  |  |
| `CERT_NO` | CHAR | 12 | NOT NULL |
| `GRADE_CODE` | CHAR | 6 | NOT NULL |
| `HEAT_NO` | CHAR | 10 | NOT NULL |
| `XCERT1` |  |  |  |
| `YIELD_MPA` | DECIMAL | 7,2 |  |

### ORDER-AUDIT

- kind: `ddm`

### ORDERMST

- kind: `supra_master`

| field | format | length | remark |
|---|---|---|---|
| `CUST-NO` | A | 8 |  |
| `ORDER-NO` | A | 10 | control key from directory |
| `ORDER-STAT` | A | 4 |  |
| `ORDER-WT` | P | 9.3 |  |

### ORDLINE

- kind: `supra_ved`

| field | format | length | remark |
|---|---|---|---|
| `GRADE-CODE` | A | 6 |  |
| `LINE-NO` | N | 3 |  |
| `LINE-WT` | P | 9.3 |  |
| `ORDER-NO` | A | 10 | control key from directory |

### STEEL.PROD.MILLORD

- kind: `vsam`
- notes: CICS FILE MILLORD

### STEEL.PROD.ORDER.EXTRACT

- kind: `vsam`

### STOCK-BALANCE

- kind: `ddm`

### TEST-COUPLE (adabas_file)

- kind: `adabas_file`

| field | format | length | remark |
|---|---|---|---|
| `AA` | A | 10 |  |
| `AB` | A | 8 |  |
| `AC` | A | 8 |  |

### TEST-COUPLE (ddm)

- kind: `ddm`
- notes: default sequence: AA

| field | format | length | remark |
|---|---|---|---|
| `AMBIGUOUS-NOTE` | A | 8 | N   coupling used here, no target given |
| `COUPLE-KEY` | A | 10 | D primary key |
| `CROSS-REF` | A | 8 | N   coupled to file 045 |

