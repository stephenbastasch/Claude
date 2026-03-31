# -*- coding: utf-8 -*-
"""
Keap Call Manager
- Suggests contacts to call (prioritized by opportunity value & days since last call)
- Prompts for notes, stores them in Keap
- Scorecard and prior notes pulled live from Keap

Usage: python keap_call_manager.py  (or double-click run_call_manager.bat)
"""

import sys
import io
import os
import json
import time
from datetime import datetime, timedelta

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import keap_refresh_token as krt
import requests

# ============================================================================
# CONFIG
# ============================================================================
KEAP_API_BASE = "https://api.infusionsoft.com/crm/rest/v1"
TOKENS_JSON   = r"O:\keap_tokens\tokens.json"

# In-memory session tracking — contacts shown this session move to end of queue
_shown_this_session = set()

# ============================================================================
# TOKEN HELPERS
# ============================================================================
def _token_expired(tokens: dict) -> bool:
    try:
        expires_at = tokens.get("expires_at")
        if expires_at is not None:
            return time.time() >= (int(expires_at) - 120)
        created    = int(tokens.get("created_at", 0))
        expires_in = int(tokens.get("expires_in", 0))
        return (time.time() - created) >= (expires_in - 120)
    except (TypeError, ValueError):
        return True


def _access_token() -> str:
    tokens = krt.load_tokens(TOKENS_JSON)
    if _token_expired(tokens):
        refreshed = krt.keap_refresh(tokens["refresh_token"], krt.CLIENT_ID, krt.CLIENT_SECRET)
        now = int(time.time())
        refreshed["created_at"] = now
        refreshed["expires_at"] = now + int(refreshed.get("expires_in", 0))
        tokens.update(refreshed)
        krt.save_tokens(TOKENS_JSON, tokens)
    return tokens["access_token"]


def _headers() -> dict:
    return {"Authorization": f"Bearer {_access_token()}", "Content-Type": "application/json"}


def keap_get(path: str, params=None) -> dict:
    url = f"{KEAP_API_BASE}{path}"
    r = requests.get(url, headers=_headers(), params=params or {}, timeout=60)
    if r.status_code == 401:
        tokens = krt.load_tokens(TOKENS_JSON)
        refreshed = krt.keap_refresh(tokens["refresh_token"], krt.CLIENT_ID, krt.CLIENT_SECRET)
        now = int(time.time())
        refreshed["created_at"] = now
        refreshed["expires_at"] = now + int(refreshed.get("expires_in", 0))
        tokens.update(refreshed)
        krt.save_tokens(TOKENS_JSON, tokens)
        r = requests.get(url, headers=_headers(), params=params or {}, timeout=60)
    r.raise_for_status()
    return r.json() if "application/json" in r.headers.get("Content-Type", "") else {}


def keap_post(path: str, data: dict) -> dict:
    url = f"{KEAP_API_BASE}{path}"
    r = requests.post(url, headers=_headers(), json=data, timeout=60)
    if r.status_code == 401:
        tokens = krt.load_tokens(TOKENS_JSON)
        refreshed = krt.keap_refresh(tokens["refresh_token"], krt.CLIENT_ID, krt.CLIENT_SECRET)
        now = int(time.time())
        refreshed["created_at"] = now
        refreshed["expires_at"] = now + int(refreshed.get("expires_in", 0))
        tokens.update(refreshed)
        krt.save_tokens(TOKENS_JSON, tokens)
        r = requests.post(url, headers=_headers(), json=data, timeout=60)
    r.raise_for_status()
    return r.json() if "application/json" in r.headers.get("Content-Type", "") else {}


# ============================================================================
# DATE HELPER
# ============================================================================
def _parse_keap_date(date_str: str):
    """Parse a Keap ISO date string to a naive local datetime."""
    if not date_str:
        return None
    try:
        ds = date_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ds)
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt
    except Exception:
        return None


# ============================================================================
# KEAP NOTES
# ============================================================================
def _is_call_note(n: dict) -> bool:
    """Return True if this note looks like a call note."""
    ntype = (n.get("type") or "").strip().lower()
    title = (n.get("title") or "").strip().lower()
    if ntype == "call":
        return True
    if title.startswith("call") or title.startswith("called"):
        return True
    return False


def get_contact_call_notes(contact_id: int, limit: int = 5) -> list:
    """Get Call-type notes for a contact from Keap, newest first."""
    try:
        data = keap_get("/notes", params={"contact_id": contact_id, "limit": 200})
        notes = data if isinstance(data, list) else data.get("notes", [])
        call_notes = [n for n in notes if _is_call_note(n)]
        call_notes.sort(
            key=lambda n: n.get("last_updated") or n.get("date_created") or "",
            reverse=True
        )
        return call_notes[:limit]
    except Exception:
        return []


def days_since_last_call(contact_id: int) -> float:
    """Days since last Call note in Keap; 9999 if never called."""
    notes = get_contact_call_notes(contact_id, limit=1)
    if not notes:
        return 9999
    date_str = notes[0].get("last_updated") or notes[0].get("date_created", "")
    dt = _parse_keap_date(date_str)
    if dt is None:
        return 9999
    return (datetime.now() - dt).total_seconds() / 86400


def get_all_call_notes_since(since_dt: datetime) -> list:
    """Fetch all Call-type notes from Keap since a given date."""
    since_str = since_dt.strftime("%Y-%m-%dT00:00:00Z")
    try:
        all_notes = []
        offset = 0
        while True:
            data  = keap_get("/notes", params={"since": since_str, "limit": 1000, "offset": offset})
            notes = data if isinstance(data, list) else data.get("notes", [])
            if not notes:
                break
            all_notes.extend([n for n in notes if _is_call_note(n)])
            if len(notes) < 1000:
                break
            offset += 1000
        return all_notes
    except Exception:
        return []


def store_call_note_in_keap(contact_id: int, contact_name: str, notes: str):
    """Store call notes as a Keap Call note on the contact."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    note_data = {
        "contact_id": contact_id,
        "title":      f"Call - {contact_name} - {ts}",
        "body":       notes,
        "type":       "Call",
    }
    try:
        return keap_post("/notes", note_data)
    except Exception as e:
        print(f"  [warn] Could not save note to Keap: {e}")
        return None


# ============================================================================
# SESSION TRACKING (in-memory only)
# ============================================================================
def record_shown(contact_id: int):
    _shown_this_session.add(contact_id)


def was_shown_this_session(contact_id: int) -> bool:
    return contact_id in _shown_this_session


# ============================================================================
# KEAP DATA FETCH
# ============================================================================
def get_active_opportunities(limit: int = 200) -> list:
    """Fetch all active (non-won/lost) opportunities."""
    all_opps = []
    page = 0
    while len(all_opps) < limit:
        data  = keap_get("/opportunities", params={"page": page, "page_size": 200})
        items = data if isinstance(data, list) else data.get("opportunities", [])
        if not items:
            break
        for opp in items:
            stage      = opp.get("stage") or {}
            stage_name = str(stage.get("name") or "").strip().upper() if isinstance(stage, dict) else ""
            if "WON" in stage_name or "LOST" in stage_name:
                continue
            all_opps.append(opp)
        if len(items) < 200:
            break
        page += 1
    return all_opps


def get_contact_detail(contact_id: int) -> dict:
    try:
        return keap_get(f"/contacts/{contact_id}")
    except Exception:
        return {}


# ============================================================================
# SCORING / QUEUE BUILDING
# ============================================================================
def score_opportunity(opp: dict) -> float:
    contact    = opp.get("contact", {})
    contact_id = contact.get("id") if isinstance(contact, dict) else None
    days       = days_since_last_call(contact_id) if contact_id else 9999

    value = 0
    try:
        value = float(opp.get("projected_revenue_high") or opp.get("value") or 0)
    except (TypeError, ValueError):
        pass

    days_score  = min(days, 90) / 90
    value_score = min(value, 100000) / 100000
    return days_score * 0.6 + value_score * 0.4


def build_call_queue() -> list:
    opps          = get_active_opportunities()
    seen_contacts = {}

    for opp in opps:
        contact = opp.get("contact", {})
        cid     = contact.get("id") if isinstance(contact, dict) else None
        if cid is None:
            continue
        score = score_opportunity(opp)
        if cid not in seen_contacts or score > seen_contacts[cid]["score"]:
            seen_contacts[cid] = {"opp": opp, "score": score}

    ranked = sorted(seen_contacts.values(), key=lambda x: x["score"], reverse=True)

    queue = []
    for item in ranked:
        opp          = item["opp"]
        contact_stub = opp.get("contact", {})
        cid          = contact_stub.get("id")
        contact      = get_contact_detail(cid) if cid else {}

        first = contact.get("given_name") or contact_stub.get("first_name", "")
        last  = contact.get("family_name") or contact_stub.get("last_name", "")
        name  = f"{first} {last}".strip() or f"Contact #{cid}"

        phones     = contact.get("phone_numbers") or []
        phone_list = []
        for p in phones:
            if not isinstance(p, dict) or not p.get("number"):
                continue
            field = str(p.get("field", "") or "").replace("PHONE", "Phone").replace("1", "").replace("2", "").strip()
            phone_list.append(f"{p['number']} ({field})" if field else p["number"])
        phone = phone_list if phone_list else ["No phone"]

        emails = contact.get("email_addresses", [])
        email  = next((e["email"] for e in emails if isinstance(e, dict) and e.get("email")), "No email")

        company      = contact.get("company") or {}
        company_name = company.get("company_name", "") if isinstance(company, dict) else ""

        value = 0
        try:
            value = float(opp.get("projected_revenue_high") or opp.get("value") or 0)
        except (TypeError, ValueError):
            pass

        stage      = opp.get("stage") or {}
        stage_name = stage.get("name", "Unknown") if isinstance(stage, dict) else "Unknown"

        days     = days_since_last_call(cid)
        days_str = f"{int(days)}d ago" if days < 9999 else "never"

        queue.append({
            "contact_id":  cid,
            "name":        name,
            "company":     company_name,
            "phone":       phone,
            "email":       email,
            "opp_title":   opp.get("opportunity_title") or opp.get("title") or "Opportunity",
            "opp_value":   value,
            "stage":       stage_name,
            "last_call":   days_str,
            "score":       item["score"],
        })

    # Contacts already shown this session go to the end
    new   = [q for q in queue if not was_shown_this_session(q["contact_id"])]
    shown = [q for q in queue if was_shown_this_session(q["contact_id"])]
    return new + shown


# ============================================================================
# DISPLAY HELPERS
# ============================================================================
def print_separator(char="=", width=60):
    print(char * width)


def print_contact_card(item: dict, rank: int):
    print_separator()
    print(f"  #{rank}  SUGGESTED CALL")
    print_separator()
    print(f"  Name     : {item['name']}")
    if item["company"]:
        print(f"  Company  : {item['company']}")
    for i, ph in enumerate(item["phone"]):
        label = "Phone    :" if i == 0 else "          "
        print(f"  {label} {ph}")
    print(f"  Email    : {item['email']}")
    print(f"  Opp      : {item['opp_title']}")
    print(f"  Stage    : {item['stage']}")
    if item["opp_value"]:
        print(f"  Value    : ${item['opp_value']:,.0f}")
    print(f"  Last call: {item['last_call']}")
    print_separator("-", 60)


def print_prior_notes(item: dict):
    """Show the 2 most recent Call notes from Keap for this contact."""
    notes = get_contact_call_notes(item["contact_id"], limit=2)
    print("  Prior notes:")
    if not notes:
        print("    (none yet)")
        print_separator("-", 60)
        return
    for i, note in enumerate(notes, start=1):
        date_str = note.get("last_updated") or note.get("date_created", "")
        dt       = _parse_keap_date(date_str)
        when     = dt.strftime("%Y-%m-%d %I:%M %p") if dt else date_str
        body     = (note.get("body") or "").strip() or "(no notes)"
        preview  = body[:140].replace("\n", " ")
        if len(body) > 140:
            preview += "..."
        print(f"    {i}. [{when}] {preview}")
    print_separator("-", 60)


def print_scorecard():
    now         = datetime.now()
    year_start  = datetime(now.year, 1, 1)
    week_start  = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    print()
    print("  Fetching call history from Keap...")
    notes = get_all_call_notes_since(year_start)

    def note_dt(n):
        return _parse_keap_date(n.get("last_updated") or n.get("date_created", ""))

    year_notes  = [n for n in notes if note_dt(n)]
    month_notes = [n for n in year_notes if note_dt(n) >= month_start]
    week_notes  = [n for n in year_notes if note_dt(n) >= week_start]
    today_notes = [n for n in year_notes if note_dt(n) and note_dt(n).date() == now.date()]

    print_separator("=", 60)
    print("  CALL SCORECARD")
    print_separator("=", 60)
    print(f"  Today      : {now.strftime('%A %b %d')}  |  Week start: {week_start.strftime('%A %b %d')}")
    print_separator("-", 60)
    print(f"  Today          : {len(today_notes)}")
    print(f"  This week      : {len(week_notes)}")
    print(f"  This month     : {len(month_notes)} ({now.strftime('%b %Y')})")
    print(f"  This year      : {len(year_notes)} ({now.year})")
    print_separator("-", 60)

    if week_notes:
        print("\n  This week's calls:")
        print_separator("-", 60)
        for n in sorted(week_notes, key=lambda x: note_dt(x) or datetime.min, reverse=True):
            dt      = note_dt(n)
            when    = dt.strftime("%a %b %d %I:%M %p") if dt else "?"
            title   = n.get("title", "")
            body    = (n.get("body") or "").strip()
            preview = body[:80].replace("\n", " ")
            if len(body) > 80:
                preview += "..."
            print(f"  {when}  {title}")
            if preview:
                print(f"    Notes: {preview}")
            print()

    print_separator("=", 60)


# ============================================================================
# MAIN LOOP
# ============================================================================
def run_call_session():
    print_separator("=", 60)
    print("  KEAP CALL MANAGER")
    print("  Commands: c (called) | s (skip) | sc (scorecard) | q (quit)")
    print_separator("=", 60)
    print()

    try:
        _access_token()
        print("  Connected to Keap.\n")
    except Exception as e:
        print(f"  ERROR: Cannot connect to Keap: {e}")
        sys.exit(1)

    print("  Building call queue (fetching opportunities)...")
    queue = build_call_queue()

    if not queue:
        print("  No active opportunities found. Nothing to call.")
        return

    print(f"  {len(queue)} contact(s) in queue.\n")

    idx = 0
    while True:
        if idx >= len(queue):
            print("  You have reached the end of the call queue.")
            ans = input("  Restart from top? (y/n): ").strip().lower()
            if ans == "y":
                idx   = 0
                queue = build_call_queue()
            else:
                break

        item   = queue[idx]
        print_contact_card(item, idx + 1)
        print("  [c] Called   [s] Skip   [sc] Scorecard   [q] Quit")
        action = input("  > ").strip().lower()

        if action in ("q", "quit", "exit"):
            break

        elif action in ("sc", "scorecard"):
            print_scorecard()
            continue

        elif action in ("s", "skip", "next"):
            record_shown(item["contact_id"])
            print(f"  Skipping {item['name']}.\n")
            idx += 1

        elif action in ("c", "called", ""):
            print()
            print_prior_notes(item)
            print(f"  Notes for call with {item['name']}:")
            print("  (Enter your notes. Press Enter twice when done.)")
            lines = []
            while True:
                line = input("  | ")
                if line == "" and lines and lines[-1] == "":
                    break
                lines.append(line)
            notes = "\n".join(lines).strip() or "(no notes entered)"

            print(f"\n  Saving note to Keap...", end=" ")
            result = store_call_note_in_keap(item["contact_id"], item["name"], notes)
            print("saved." if result else "failed.")

            record_shown(item["contact_id"])
            print(f"  Call logged for {item['name']}.\n")
            idx += 1

        else:
            print(f"  Unknown command '{action}'. Use: c / s / sc / q")

    print("\n  Session ended.")
    print_scorecard()


if __name__ == "__main__":
    run_call_session()
