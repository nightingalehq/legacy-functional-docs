---
title: "Testability advisory"
doc_type: register
---

# Testability advisory

Classification of every batchable member for test generation, derived from data_access/call_edge/transaction_scopes facts. Regenerate with `mfdoc test-advisory`; do not hand-edit. Seam suggestions are advisory prose only -- nothing here changes source.

## Pure — unit-testable directly, no mocks needed

### `MMC0100`

### `MMP9000`

### `MMP9100`

### `MMP9300`

### `MMP9500`

### `MMP9800`

## Needs mocks — unit-testable with named seams

### `MMP9200`
- entities to mock: MILL-ORDER
- callees to mock: -
- seam: Extract the `MILL-ORDER` access (first seen at [[MMP9200:12]], `FIND`) behind a seam (a lookup/repository call this unit takes as a parameter or can have substituted) so a unit test can supply fixture data for `MILL-ORDER` instead of a live database call.

### `MMP9600`
- entities to mock: MILL-ORDER
- callees to mock: -
- seam: Extract the `MILL-ORDER` access (first seen at [[MMP9600:8]], `FIND`) behind a seam (a lookup/repository call this unit takes as a parameter or can have substituted) so a unit test can supply fixture data for `MILL-ORDER` instead of a live database call.

### `MMP9700`
- entities to mock: MILL-ORDER
- callees to mock: -
- seam: Extract the `MILL-ORDER` access (first seen at [[MMP9700:7]], `FIND`) behind a seam (a lookup/repository call this unit takes as a parameter or can have substituted) so a unit test can supply fixture data for `MILL-ORDER` instead of a live database call.

## Blocked — dynamic/unresolved call, confirm before testing

### `MMP0100`
- entities to mock: MILL-ORDER, ORDER-AUDIT, STOCK-BALANCE
- callees to mock: MMN0250, MMN0900
- gap: [[MMP0100:57]] `CALLNAT` target `MMN0250` has no source in the ingested set -- its behaviour can't be characterized, so this call can only be stubbed opaquely (assert it was invoked with X), not verified against real logic.
- gap: [[MMP0100:67]] `CALLNAT` target `MMN0900` has no source in the ingested set -- its behaviour can't be characterized, so this call can only be stubbed opaquely (assert it was invoked with X), not verified against real logic.

### `MMP0200`
- entities to mock: MILL-CERT
- callees to mock: PDFGEN
- gap: [[MMP0200:22]] `FETCH RETURN` target is dynamic (a variable, not a literal) -- the callee set is unknown, so no fixed seam/mock can be named; a test can only be written once the possible targets are confirmed with an SME.
- gap: [[MMP0200:23]] `CALL` target `PDFGEN` has no source in the ingested set -- its behaviour can't be characterized, so this call can only be stubbed opaquely (assert it was invoked with X), not verified against real logic.

### `MMP9400`
- entities to mock: -
- callees to mock: PROGA
- gap: [[MMP9400:10]] `CALLNAT` target `PROGA` has no source in the ingested set -- its behaviour can't be characterized, so this call can only be stubbed opaquely (assert it was invoked with X), not verified against real logic.

### `ORDENQ`
- entities to mock: ORDERMST, ORDLINE
- callees to mock: PRICECALC
- gap: [[ORDENQ:8]] `CALL` target `PRICECALC` has no source in the ingested set -- its behaviour can't be characterized, so this call can only be stubbed opaquely (assert it was invoked with X), not verified against real logic.
- gap: [[ORDENQ:27]] `CALL` target `PRICECALC` has no source in the ingested set -- its behaviour can't be characterized, so this call can only be stubbed opaquely (assert it was invoked with X), not verified against real logic.

