# -*- coding: utf-8 -*-
"""
Keap Call Manager
- Suggests contacts to call (prioritized by opportunity value & days since last call)
- Prompts for notes, stores them in Keap, logs the call locally
- Provides a weekly scorecard of calls made

Usage: python "Keap CRM/keap_call_manager.py"
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

sys.path.append(r"C:\Users\steve\Documents\python\Keap CRM")
sys.path.append(r"C:\Users\steve\Documents\python")
sys.path.append(r"C:\Users\steve\Downloads")

import keap_refresh_token as krt
import requests

# ============================================================================
# CONFIG
# ============================================================================
KEAP_API_BASE   = "https://api.infusionsoft.com/crm/rest/v1"
TOKENS_JSON     = r"O:\keap_tokens\tokens.json"
CALL_LOG_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "call_log.json")
QUEUE_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "queue_state.json")

# ============================================================================
# TOKEN HELPERS (same pattern as keap_agent.py)
# ============================================================================
def _token_expired(tokens: dict) -> bool:
    """Support both created_at/expires_in and fetched_at/expires_at token formats."""
    try:
        # Prefer expires_at if present (from keap_refresh_token standalone)
        expires_at = tokens.get("expires_at")
        if expires_at is not None:
            return time.time() >= (int(expires_at) - 120)
        # Fallback: created_at + expires_in
        created = int(tokens.get("created_at", 0))
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
        # Force refresh once
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
        # Force refresh and retry (same as keap_get)
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
# LOCAL CALL LOG
# ============================================================================
def load_call_log() -> list:
    if not os.path.exists(CALL_LOG_FILE):
        return []
    try:
        with open(CALL_LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_call_log(log: list):
    log_dir = os.path.dirname(CALL_LOG_FILE)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    with open(CALL_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2)


def log_call(contact_id: int, contact_name: str, opp_title: str, notes: str):
    log = load_call_log()
    entry = {
        "timestamp": datetime.now().isoformat(),
        "contact_id": contact_id,
        "contact_name": contact_name,
        "opportunity": opp_title,
        "notes": notes,
    }
    log.append(entry)
    save_call_log(log)
    return entry


def last_call_date(contact_id: int, log: list):
    """Return datetime of most recent call for this contact, or None."""
    if contact_id is None:
        return None
    try:
        cid = int(contact_id)
    except (TypeError, ValueError):
        return None
    calls = [e for e in log if e.get("contact_id") is not None and int(e["contact_id"]) == cid]
    if not calls:
        return None
    latest = max(calls, key=lambda e: e["timestamp"])
    return datetime.fromisoformat(latest["timestamp"])


def days_since_last_call(contact_id: int, log: list) -> float:
    """Days since last call; 9999 if never called."""
    dt = last_call_date(contact_id, log)
    if dt is None:
        return 9999
    return (datetime.now() - dt).total_seconds() / 86400


def prior_notes_for_contact(contact_id: int, log: list, limit: int = 2) -> list:
    """Return most recent local call-log notes for this contact."""
    if contact_id is None:
        return []
    try:
        cid = int(contact_id)
    except (TypeError, ValueError):
        return []

    calls = []
    for entry in log:
        try:
            if entry.get("contact_id") is None or int(entry.get("contact_id")) != cid:
                continue
        except (TypeError, ValueError):
            continue
        calls.append(entry)

    calls.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return calls[:max(0, int(limit))]


# ============================================================================
# QUEUE STATE (track who we've shown so "new" people appear first tomorrow)
# ============================================================================
def load_queue_state() -> dict:
    """Returns {contact_id: last_shown_iso_timestamp}"""
    if not os.path.exists(QUEUE_STATE_FILE):
        return {}
    try:
        with open(QUEUE_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("shown", {}) if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_queue_state(shown: dict):
    log_dir = os.path.dirname(QUEUE_STATE_FILE)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    with open(QUEUE_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"shown": shown, "updated": datetime.now().isoformat()}, f, indent=2)


def record_shown(contact_id: int):
    """Record that we showed this contact today (so they go to end of queue tomorrow)."""
    state = load_queue_state()
    state[str(contact_id)] = datetime.now().isoformat()
    save_queue_state(state)


def was_shown_today(contact_id: int, state: dict) -> bool:
    """True if we displayed this contact earlier today."""
    ts = state.get(str(contact_id))
    if not ts:
        return False
    try:
        return datetime.fromisoformat(ts).date() == datetime.now().date()
    except Exception:
        return False


# ============================================================================
# KEAP DATA FETCH
# ============================================================================
def get_active_opportunities(limit: int = 200) -> list:
    """Fetch all active (non-won/lost/closed) opportunities."""
    all_opps = []
    page = 0
    while len(all_opps) < limit:
        data = keap_get("/opportunities", params={"page": page, "page_size": 200})
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("opportunities", [])
        else:
            items = []
        if not items:
            break
        for opp in items:
            stage = opp.get("stage") or {}
            stage_name = ""
            if isinstance(stage, dict):
                stage_name = str(stage.get("name") or "").strip().upper()

            # Ignore opportunities based only on stage name.
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
# CONTACT SCORING / SUGGESTION
# ============================================================================
def score_opportunity(opp: dict, log: list) -> float:
    """
    Higher score = should call sooner.
    Factors:
      - Days since last call (more days = higher priority)
      - Opportunity value (higher = higher priority)
    """
    contact = opp.get("contact", {})
    contact_id = contact.get("id") if isinstance(contact, dict) else None
    days = days_since_last_call(contact_id, log) if contact_id else 9999

    value = 0
    try:
        value = float(opp.get("projected_revenue_high") or opp.get("value") or 0)
    except (TypeError, ValueError):
        value = 0

    # Normalize: cap days at 90 for scoring, value capped at $100k
    days_score  = min(days, 90) / 90        # 0.0 – 1.0
    value_score = min(value, 100000) / 100000  # 0.0 – 1.0

    return days_score * 0.6 + value_score * 0.4


def build_call_queue(log: list) -> list:
    """
    Returns a list of dicts ready for presentation, sorted by priority (highest first).
    Deduplicates by contact — keeps only the top opp per contact.
    """
    opps = get_active_opportunities()
    seen_contacts = {}

    for opp in opps:
        contact = opp.get("contact", {})
        cid = contact.get("id") if isinstance(contact, dict) else None
        if cid is None:
            continue
        score = score_opportunity(opp, log)
        if cid not in seen_contacts or score > seen_contacts[cid]["score"]:
            seen_contacts[cid] = {"opp": opp, "score": score}

    ranked = sorted(seen_contacts.values(), key=lambda x: x["score"], reverse=True)

    queue = []
    for item in ranked:
        opp = item["opp"]
        contact_stub = opp.get("contact", {})
        cid = contact_stub.get("id")

        # Pull full contact detail
        contact = get_contact_detail(cid) if cid else {}

        first = contact.get("given_name") or contact_stub.get("first_name", "")
        last  = contact.get("family_name") or contact_stub.get("last_name", "")
        name  = f"{first} {last}".strip() or f"Contact #{cid}"

        phones = contact.get("phone_numbers") or []
        phone_list = []
        for p in phones:
            if not isinstance(p, dict) or not p.get("number"):
                continue
            field = str(p.get("field", "") or "").replace("PHONE", "Phone").replace("1", "").replace("2", "").strip()
            phone_list.append(f"{p['number']} ({field})" if field else p["number"])
        phone = phone_list if phone_list else ["No phone"]

        emails = contact.get("email_addresses", [])
        email  = next((e["email"] for e in emails if isinstance(e, dict) and e.get("email")), "No email")

        company = contact.get("company") or {}
        company_name = company.get("company_name", "") if isinstance(company, dict) else ""

        value = 0
        try:
            value = float(opp.get("projected_revenue_high") or opp.get("value") or 0)
        except (TypeError, ValueError):
            pass

        stage = opp.get("stage") or {}
        stage_name = stage.get("name", "Unknown") if isinstance(stage, dict) else "Unknown"

        days = days_since_last_call(cid, log)
        days_str = f"{int(days)}d ago" if days < 9999 else "never"

        queue.append({
            "contact_id":   cid,
            "name":         name,
            "company":      company_name,
            "phone":        phone,
            "email":        email,
            "opp_title":    opp.get("opportunity_title") or opp.get("title") or "Opportunity",
            "opp_value":    value,
            "stage":        stage_name,
            "last_call":    days_str,
            "score":        item["score"],
        })

    # Move contacts we've already shown today to the end, so "new" people appear first
    shown_state = load_queue_state()
    new_today = [q for q in queue if not was_shown_today(q["contact_id"], shown_state)]
    shown_today = [q for q in queue if was_shown_today(q["contact_id"], shown_state)]
    return new_today + shown_today


# ============================================================================
# KEAP NOTE / CALL LOGGING
# ============================================================================
def store_call_note_in_keap(contact_id: int, notes: str):
    """Store call notes as a Keap note on the contact."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    note_data = {
        "contact_id": contact_id,
        "title": f"Call Note - {ts}",
        "body": notes,
        "type": "Call",
    }
    try:
        result = keap_post("/notes", note_data)
        return result
    except Exception as e:
        print(f"  [warn] Could not save note to Keap: {e}")
        return None


# ============================================================================
# DISPLAY HELPERS
# ============================================================================
def print_separator(char="=", width=60):
    print(char * width)


def print_contact_card(item: dict, rank: int):
    print_separator()
    print(f"  #{rank}  SUGGESTED CALL")
    print_separator()
    print(f"  Name    : {item['name']}")
    if item["company"]:
        print(f"  Company : {item['company']}")
    for i, ph in enumerate(item["phone"]):
        label = "Phone   :" if i == 0 else "         "
        print(f"  {label} {ph}")
    print(f"  Email   : {item['email']}")
    print(f"  Opp     : {item['opp_title']}")
    print(f"  Stage   : {item['stage']}")
    if item["opp_value"]:
        print(f"  Value   : ${item['opp_value']:,.0f}")
    print(f"  Last call: {item['last_call']}")
    print_separator("-", 60)


def print_prior_notes(item: dict, log: list, limit: int = 2):
    """Show the previous local notes for this contact before logging a new call."""
    prior = prior_notes_for_contact(item.get("contact_id"), log, limit=limit)
    print("  Prior notes:")
    if not prior:
        print("    (none yet)")
        print_separator("-", 60)
        return

    for i, entry in enumerate(prior, start=1):
        ts = entry.get("timestamp", "")
        when = ts
        try:
            when = datetime.fromisoformat(ts).strftime("%Y-%m-%d %I:%M %p")
        except Exception:
            pass
        note_text = (entry.get("notes") or "").strip() or "(no notes)"
        preview = note_text[:140].replace("\n", " ")
        if len(note_text) > 140:
            preview += "..."
        print(f"    {i}. [{when}] {preview}")
    print_separator("-", 60)


def print_scorecard():
    log = load_call_log()
    if not log:
        print("\n  No calls logged yet.")
        return

    now = datetime.now()
    week_start = now - timedelta(days=now.weekday())  # Monday
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)

    week_calls = [e for e in log if datetime.fromisoformat(e["timestamp"]) >= week_start]
    today_calls = [e for e in log if datetime.fromisoformat(e["timestamp"]).date() == now.date()]
    month_calls = [
        e for e in log
        if (dt := datetime.fromisoformat(e["timestamp"])).year == now.year and dt.month == now.month
    ]
    year_calls = [e for e in log if datetime.fromisoformat(e["timestamp"]).year == now.year]

    day_total = len(today_calls)
    week_total = len(week_calls)
    month_total = len(month_calls)
    year_total = len(year_calls)
    all_time_total = len(log)

    print()
    print_separator("=", 60)
    print("  WEEKLY CALL SCORECARD")
    print_separator("=", 60)
    print(f"  Today      : {now.strftime('%A %b %d')}  |  Week start: {week_start.strftime('%A %b %d')}")
    print_separator("-", 60)
    print(f"  Day total      : {day_total}")
    print(f"  Weekly total   : {week_total}")
    print(f"  Monthly total  : {month_total} ({now.strftime('%b %Y')})")
    print(f"  Year total     : {year_total} ({now.year})")
    print(f"  Total (all)    : {all_time_total}")
    print_separator("-", 60)
    print()

    if week_calls:
        print("  This week's calls:")
        print_separator("-", 60)
        for e in sorted(week_calls, key=lambda x: x["timestamp"], reverse=True):
            dt = datetime.fromisoformat(e["timestamp"])
            print(f"  {dt.strftime('%a %b %d %I:%M %p')}  {e['contact_name']}")
            if e.get("opportunity"):
                print(f"    Opp: {e['opportunity']}")
            if e.get("notes"):
                preview = e["notes"][:80].replace("\n", " ")
                if len(e["notes"]) > 80:
                    preview += "..."
                print(f"    Notes: {preview}")
            print()

    # Top contacts called
    from collections import Counter
    top = Counter(e["contact_name"] for e in log).most_common(5)
    if top:
        print("\n  Most called contacts:")
        for name, count in top:
            print(f"    {name}: {count} call(s)")
    print_separator("=", 60)


# ============================================================================
# MAIN LOOP
# ============================================================================
def run_call_session():
    print_separator("=", 60)
    print("  KEAP CALL MANAGER")
    print("  Commands: next | skip | scorecard | quit")
    print_separator("=", 60)
    print()

    # Verify connection
    try:
        _access_token()
        print("  Connected to Keap.\n")
    except Exception as e:
        print(f"  ERROR: Cannot connect to Keap: {e}")
        sys.exit(1)

    log = load_call_log()
    print("  Building call queue (fetching opportunities)...")
    queue = build_call_queue(log)

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
                idx = 0
                log = load_call_log()  # reload to re-score
                queue = build_call_queue(log)
            else:
                break

        item = queue[idx]
        print_contact_card(item, idx + 1)

        print("  [c] Called   [s] Skip   [sc] Scorecard   [q] Quit")
        action = input("  > ").strip().lower()

        if action in ("q", "quit", "exit"):
            break

        elif action in ("sc", "scorecard"):
            print_scorecard()
            # Don't advance
            continue

        elif action in ("s", "skip", "next"):
            record_shown(item["contact_id"])
            print(f"  Skipping {item['name']}.\n")
            idx += 1

        elif action in ("c", "called", ""):
            print()
            print_prior_notes(item, log, limit=2)
            print(f"  Notes for call with {item['name']}:")
            print("  (Enter your notes. Press Enter twice when done.)")
            lines = []
            while True:
                line = input("  | ")
                if line == "" and lines and lines[-1] == "":
                    break
                lines.append(line)
            notes = "\n".join(lines).strip()

            if not notes:
                notes = "(no notes entered)"

            # Save to Keap
            print(f"\n  Saving note to Keap...", end=" ")
            result = store_call_note_in_keap(item["contact_id"], notes)
            if result:
                print("saved.")
            else:
                print("failed (check warning above). Notes saved locally.")

            # Save to local log
            log_call(item["contact_id"], item["name"], item["opp_title"], notes)
            record_shown(item["contact_id"])
            log = load_call_log()  # refresh

            print(f"  Call logged for {item['name']}.")
            print()

            # Show next suggestion
            idx += 1

        else:
            print(f"  Unknown command '{action}'. Use: c / s / sc / q")

    print("\n  Session ended.")
    print_scorecard()


if __name__ == "__main__":
    run_call_session()
