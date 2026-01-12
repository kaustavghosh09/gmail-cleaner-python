# Gmail Cleaner (Python)

A safe, incremental Gmail automation tool built using Python and Gmail API.

## What it does
- Scans entire mailbox
- Groups emails by sender
- Applies labels instead of blind deletes
- Supports re-runs without reprocessing
- Handles Gmail API quotas safely

## Labels used
- NEED_REVIEW
- STAYS
- BANKING
- NEWSLETTERS
- UNSUBSCRIBE
- DELETE
- DELETE_AND_SPAM
- BLOCK
- PROCESSED_BY_CLEANER

## Design principles
- No destructive action without labels
- Incremental processing
- Resume-safe execution
- Ops-style thinking over shortcuts

## Tech
- Python
- Gmail API
- OAuth 2.0
