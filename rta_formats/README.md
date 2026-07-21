# RTA request formats — drop folder

Drop the **sample files CAMS / KFintech give you** for their by-folio mailback
requests in here (the blank template, or one you have already sent that worked).
Name them so the RTA is obvious, e.g.:

```
rta_formats/cams_folio_request_sample.txt
rta_formats/kfintech_folio_request_sample.txt
```

Once a sample is here, tell me and I'll match the generator to it exactly.

## What the generator produces today

**MF Folios → "Request Files (500/batch)"** (`/clients/mf/folios/batches/`)
downloads `folio_batches_<date>.zip` containing:

```
CAMS_folios_001.txt      ← up to 500 folio numbers
CAMS_folios_002.txt
KFIN_folios_001.txt
UNKNOWN_folios_001.txt   ← imported before the RTA was recorded; send to both
```

Each file matches `kfintech_folio_request_sample.txt` byte for byte in shape:
one bare folio number
per line, no commas, no header, LF line endings, and **no trailing newline** —
a blank last line reads as an empty folio to the RTA's parser.

Batch size is 500 by default; `?size=250` overrides it, `?rta=CAMS` restricts
to one RTA.

## Confirmed vs assumed

- **KFintech — confirmed** against `kfintech_folio_request_sample.txt`
  (their own sample, 2026-07-21).
- **CAMS — assumed identical.** Not verified. If CAMS wants an AMC/fund code
  column, or the check digit kept (`1234567 / 89`), drop
  `cams_folio_request_sample.txt` here and the generator splits per RTA.

Our folio numbers are stored exactly as the feeds reported them and go out
unchanged, so anything the RTA sent us it will recognise back.
