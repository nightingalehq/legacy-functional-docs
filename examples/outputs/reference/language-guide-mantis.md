---
title: "mantis — language guide"
doc_type: register
---

# mantis — language guide

Every recognised construct actually in use in this codebase's `mantis` source, grouped by keyword with a frequency count and one cited example each. Regenerate with `mfdoc lang-guide --config project.yml --dialect mantis` after any source change; do not hand-edit. See `templates/language-guide.md` for the narrative tier that adds connective prose on top of this.

## Structure / declarations

| keyword | count | example |
|---|---|---|
| `TEXT` | 10 | [[ORDENQ:3]] `TEXT ORDER_NO(10)` |
| `NUMERIC` | 3 | [[ORDENQ:5]] `NUMERIC ORDER_WT(9.3)` |

## Control flow

| keyword | count | example |
|---|---|---|
| `ASSIGN` | 7 | [[ORDENQ:14]] `MSG = "Order number required"` |
| `IF` | 5 | [[ORDENQ:13]] `IF ORDER_NO = " "` |
| `WHEN` | 2 | [[ORDENQ:28]] `WHEN "CONF"` |
| `CASE` | 1 | [[ORDENQ:27]] `CASE ORDVIEW.STATUS` |
| `WHILE` | 1 | [[ORDENQ:23]] `WHILE STATUS = 0` |

## Data access (DML)

| keyword | count | example |
|---|---|---|
| `ADD-M` | 1 | [[PRODSCHED:15]] `ADD-M(SCHEDVIEW)` |
| `OBTAIN` | 1 | [[ORDENQ:22]] `OBTAIN ORDVIEW WHERE ORDER_NO = ORDER_NO` |
| `RDNXT` | 1 | [[ORDENQ:25]] `RDNXT(ORDLINE, ORDER_NO)` |
| `READM` | 1 | [[ORDENQ:17]] `READM(ORDERMST, ORDER_NO)` |
| `WRITM` | 1 | [[ORDENQ:33]] `WRITM(ORDERMST, ORDER_NO)` |

### Entity relationships

None recorded.

## Screen interaction

| keyword | count | example |
|---|---|---|
| `SHOW` | 4 | [[ORDENQ:15]] `SHOW ORDSCR1` |
| `CONVERSE` | 3 | [[ORDENQ:12]] `CONVERSE ORDSCR1` |

## Transactions

| keyword | count | example |
|---|---|---|
| `ENDTR` | 2 | [[ORDENQ:34]] `ENDTR` |

## Calling conventions

| keyword | count | example |
|---|---|---|
| `INCLUDE` | 7 | [[ORDENQ:12]] `CONVERSE ORDSCR1` |
| `CALL` | 3 | [[ORDENQ:10]] `EXTERNAL "STEELLIB","PRICECALC"` |

## Not yet recognized

Seen in source, not yet matched to a known construct -- ranked by frequency; see `mfdoc calibrate --config project.yml --dialect mantis` for the full list and where to add recognition.

None recorded.

