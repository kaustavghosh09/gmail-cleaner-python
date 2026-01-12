# gmail_cleaner.py
import os
import re
import time
import random
from collections import defaultdict
from typing import Dict, List, Tuple, Optional, Set

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from categories import (
    SYSTEM_LABEL_PROCESSED,
    CATEGORIES,
    print_menu,
    get_category,
    CategorySpec,
)

# ---------------- CONFIG ----------------
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

BATCH_SIZE_FETCH = 50
BATCH_SIZE_ACTIONS = 25
BATCH_SIZE_BACKFILL = 50

MAX_EMAILS_PER_RUN = 500
SLEEP_BETWEEN_BATCHES_SEC = 1.5

MAX_RETRIES = 6


# ---------------- AUTH ----------------
def authenticate():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists("credentials.json"):
                raise FileNotFoundError("credentials.json not found in this folder.")
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)

        with open("token.json", "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


# ---------------- LABEL CACHE ----------------
def get_existing_labels(service) -> Dict[str, str]:
    resp = service.users().labels().list(userId="me").execute()
    return {l["name"].lower(): l["id"] for l in resp.get("labels", [])}


def ensure_label_cached(service, cache: Dict[str, str], name: str) -> str:
    key = name.lower()
    if key in cache:
        return cache[key]

    created = service.users().labels().create(
        userId="me",
        body={
            "name": name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        },
    ).execute()

    cache[key] = created["id"]
    return created["id"]


def build_label_cache(service, cache: Dict[str, str]) -> Dict[str, str]:
    label_ids: Dict[str, str] = {}
    for c in CATEGORIES.values():
        for lab in c.add_labels:
            label_ids[lab] = ensure_label_cached(service, cache, lab)
    return label_ids


# ---------------- HELPERS ----------------
def extract_sender_email(headers) -> str:
    for h in headers:
        if h.get("name", "").lower() == "from":
            val = h.get("value", "")
            m = re.search(r"<([^>]+)>", val)
            return (m.group(1) if m else val).strip().lower()
    return ""


def list_message_ids_by_query(service, query: str, cap: Optional[int] = None) -> List[str]:
    ids: List[str] = []
    token = None
    while True:
        max_results = 500
        if cap is not None:
            remaining = cap - len(ids)
            if remaining <= 0:
                break
            max_results = min(500, remaining)

        resp = service.users().messages().list(
            userId="me", q=query, maxResults=max_results, pageToken=token
        ).execute()

        ids.extend([m["id"] for m in resp.get("messages", [])])

        token = resp.get("nextPageToken")
        if not token:
            break

    return ids


def is_rate_limit_error(e: HttpError) -> bool:
    status = getattr(e.resp, "status", None)
    return status in (403, 429)


def execute_batch_with_retries(batch, action_desc: str):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            batch.execute()
            return
        except HttpError as e:
            status = getattr(e.resp, "status", "unknown")
            print(f"\nGmail API error during {action_desc} (HTTP {status}).")
            print("Details:", e)

            if not is_rate_limit_error(e):
                raise

            sleep_s = min(60, (2 ** attempt) + random.uniform(0.2, 1.2))
            print(f"Rate limit hit. Sleeping {sleep_s:.1f}s then retrying ({attempt}/{MAX_RETRIES})...")
            time.sleep(sleep_s)

    raise RuntimeError(f"Failed after {MAX_RETRIES} retries due to rate limits during {action_desc}.")


def parse_sender_selection(text: str, max_index: int) -> List[int]:
    """
    Accepts:
      "2,5,9"
      "1 4 7"
      "3-6"
      "2,5-7,12"
    Returns sorted unique 0-based indices.
    """
    raw = text.strip().replace(" ", ",")
    parts = [p for p in raw.split(",") if p]
    selected: Set[int] = set()

    for p in parts:
        if "-" in p:
            a, b = p.split("-", 1)
            if not a.isdigit() or not b.isdigit():
                continue
            start, end = int(a), int(b)
            if start > end:
                start, end = end, start
            for n in range(start, end + 1):
                if 1 <= n <= max_index:
                    selected.add(n - 1)
        else:
            if p.isdigit():
                n = int(p)
                if 1 <= n <= max_index:
                    selected.add(n - 1)

    return sorted(selected)


def print_post_action_summary(
    category: CategorySpec,
    chosen_pairs: List[Tuple[str, List[str]]],
    total_emails: int,
):
    # This summary prints AFTER the action succeeds
    archived = "YES" if category.archive else "NO"
    trashed = "YES" if category.trash else "NO"
    spammed = "YES" if category.spam else "NO"

    print("\n=== POST-ACTION SUMMARY ===")
    print(f"Category applied:          {category.name}")
    print(f"Senders selected:          {len(chosen_pairs)}")
    print(f"Total emails affected:     {total_emails}")
    print(f"Labels added:              {SYSTEM_LABEL_PROCESSED} + {', '.join(category.add_labels)}")
    print(f"Archived (remove INBOX):   {archived}")
    print(f"Moved to Trash:            {trashed}")
    print(f"Marked as Spam:            {spammed}")
    print("\nSenders included:")
    for sender, mids in chosen_pairs:
        print(f" - {sender} ({len(mids)} emails)")
    print("==========================\n")


# ---------------- CORE LOGIC ----------------
def list_unprocessed_message_ids(service) -> List[str]:
    return list_message_ids_by_query(service, f"-label:{SYSTEM_LABEL_PROCESSED}")


def group_unprocessed_by_sender(service, message_ids: List[str]) -> List[Tuple[str, List[str]]]:
    sender_map = defaultdict(list)

    def cb(req_id, resp, exc):
        if resp:
            sender = extract_sender_email(resp.get("payload", {}).get("headers", []))
            if sender:
                sender_map[sender].append(req_id)

    total = len(message_ids)
    for i in range(0, total, BATCH_SIZE_FETCH):
        batch = service.new_batch_http_request(callback=cb)
        chunk = message_ids[i:i + BATCH_SIZE_FETCH]

        for mid in chunk:
            batch.add(
                service.users().messages().get(
                    userId="me",
                    id=mid,
                    format="metadata",
                    metadataHeaders=["From"],
                ),
                request_id=mid,
            )

        execute_batch_with_retries(batch, action_desc="fetch sender metadata")
        done = min(i + BATCH_SIZE_FETCH, total)
        print(f"Fetched sender metadata: {done}/{total}")
        time.sleep(SLEEP_BETWEEN_BATCHES_SEC)

    grouped = list(sender_map.items())
    grouped.sort(key=lambda x: len(x[1]), reverse=True)
    return grouped


def apply_category_to_messages(
    service,
    msg_ids: List[str],
    category: CategorySpec,
    label_ids: Dict[str, str],
    processed_label_id: str,
):
    total = len(msg_ids)
    for i in range(0, total, BATCH_SIZE_ACTIONS):
        chunk = msg_ids[i:i + BATCH_SIZE_ACTIONS]
        batch = service.new_batch_http_request()

        for mid in chunk:
            add_ids = [processed_label_id] + [
                label_ids[l] for l in category.add_labels if l in label_ids
            ]

            remove_ids = ["INBOX"] if category.archive else []

            if category.spam:
                add_ids = ["SPAM"] + add_ids

            batch.add(
                service.users().messages().modify(
                    userId="me",
                    id=mid,
                    body={"addLabelIds": add_ids, "removeLabelIds": remove_ids},
                )
            )

            if category.trash:
                batch.add(service.users().messages().trash(userId="me", id=mid))

        execute_batch_with_retries(batch, action_desc=f"apply category {category.name}")
        done = min(i + BATCH_SIZE_ACTIONS, total)
        print(f"Applied {category.name}: {done}/{total}")
        time.sleep(SLEEP_BETWEEN_BATCHES_SEC)


def backfill_processed(service, processed_label_id: str, label: str):
    query = f"label:{label} -label:{SYSTEM_LABEL_PROCESSED}"
    ids = list_message_ids_by_query(service, query, cap=5000)

    print(f"Backfill found: {len(ids)} emails for '{label}' missing {SYSTEM_LABEL_PROCESSED}")
    if not ids:
        return

    confirm = input(f"Add {SYSTEM_LABEL_PROCESSED} to these {len(ids)} emails? (y/N): ").strip().lower()
    if confirm != "y":
        return

    total = len(ids)
    for i in range(0, total, BATCH_SIZE_BACKFILL):
        chunk = ids[i:i + BATCH_SIZE_BACKFILL]
        batch = service.new_batch_http_request()

        for mid in chunk:
            batch.add(
                service.users().messages().modify(
                    userId="me",
                    id=mid,
                    body={"addLabelIds": [processed_label_id], "removeLabelIds": []},
                )
            )

        execute_batch_with_retries(batch, action_desc=f"backfill from {label}")
        done = min(i + BATCH_SIZE_BACKFILL, total)
        print(f"Backfilled processed label: {done}/{total}")
        time.sleep(SLEEP_BETWEEN_BATCHES_SEC)


# ---------------- MAIN ----------------
def main():
    service = authenticate()
    label_cache = get_existing_labels(service)

    processed_label_id = ensure_label_cached(service, label_cache, SYSTEM_LABEL_PROCESSED)
    label_ids = build_label_cache(service, label_cache)

    # Run totals (optional, but useful)
    run_total_actions = 0
    run_total_emails_affected = 0
    run_category_counts = defaultdict(int)

    print("\n=== Gmail Cleaner ===")
    print("1) Process new/unreviewed emails (missing PROCESSED_BY_CLEANER)")
    print("2) Backfill PROCESSED_BY_CLEANER for an existing label (one-time cleanup)")
    print("3) Exit")

    choice = input("Choose option: ").strip()

    if choice == "3":
        return

    if choice == "2":
        lab = input("Enter label name to backfill from (example: BLOCKED): ").strip()
        if lab:
            backfill_processed(service, processed_label_id, lab)
        return

    ids = list_unprocessed_message_ids(service)
    if not ids:
        print("No unprocessed emails found.")
        return

    print(f"Unprocessed emails found: {len(ids)}")
    if len(ids) > MAX_EMAILS_PER_RUN:
        print(f"Safety limit: processing only first {MAX_EMAILS_PER_RUN} emails this run.")
        ids = ids[:MAX_EMAILS_PER_RUN]

    grouped = group_unprocessed_by_sender(service, ids)

    print("\n=== SENDERS (THIS RUN) ===")
    for i, (sender, mids) in enumerate(grouped, 1):
        print(f"{i:>3}. {sender:<45} {len(mids)} emails")

    while grouped:
        print_menu()

        raw = input("Sender number(s) (e.g., 2,5-7) or q: ").strip().lower()
        if raw == "q":
            break

        selected = parse_sender_selection(raw, max_index=len(grouped))
        if not selected:
            print("No valid sender numbers selected.")
            continue

        ckey = input("Category number (applies to ALL selected senders): ").strip()
        cat = get_category(ckey)
        if not cat:
            print("Invalid category.")
            continue

        chosen_pairs = [(grouped[i][0], grouped[i][1]) for i in selected]
        total_emails = sum(len(mids) for _, mids in chosen_pairs)

        flat_ids: List[str] = []
        for _, mids in chosen_pairs:
            flat_ids.extend(mids)

        print("\n=== CONFIRMATION ===")
        print(f"Category: {cat.name}")
        print(f"Selected senders: {len(chosen_pairs)}")
        print(f"TOTAL emails affected (this action): {total_emails}")

        confirm = input("Proceed? (y/N): ").strip().lower()
        if confirm != "y":
            print("Cancelled.")
            continue

        # Apply
        apply_category_to_messages(service, flat_ids, cat, label_ids, processed_label_id)

        # Post-action summary (this is what you asked for)
        print_post_action_summary(cat, chosen_pairs, total_emails)

        # Update run totals
        run_total_actions += 1
        run_total_emails_affected += total_emails
        run_category_counts[cat.name] += total_emails

        # Remove processed groups from list
        for i in sorted(selected, reverse=True):
            grouped.pop(i)

    # Optional end-of-run totals (helpful)
    if run_total_actions > 0:
        print("\n=== RUN TOTAL SUMMARY ===")
        print(f"Actions taken this run:       {run_total_actions}")
        print(f"Total emails affected:        {run_total_emails_affected}")
        print("By category (emails affected):")
        for k, v in sorted(run_category_counts.items(), key=lambda x: x[1], reverse=True):
            print(f" - {k}: {v}")
        print("=========================\n")

    print("Done. Run again to continue with the next chunk.")


if __name__ == "__main__":
    try:
        main()
    except HttpError as e:
        status = getattr(e.resp, "status", "unknown")
        print(f"\nGmail API HttpError (HTTP {status}):", e)
        print("If this is a rate limit (403/429), wait 60–120 seconds and rerun.")
    except Exception as e:
        print("\nError:", e)
