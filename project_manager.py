# -*- coding: utf-8 -*-
"""
Project Manager
- Shows active projects as flash cards (title, description, contacts, prior notes)
- Prompts for today's notes
- Stores all data in a single JSON file on Google Drive (accessible anywhere)

First-run setup:
  1. Go to https://console.cloud.google.com/
  2. Create a project -> Enable the Google Drive API
  3. Credentials -> Create OAuth 2.0 Client ID (Desktop app)
  4. Download JSON -> save as  project_manager_credentials.json  (same folder as this script)
  5. Run this script -> browser opens once for authorization -> token saved automatically

Usage: python project_manager.py   (or double-click run_project_manager.bat)
"""

import sys
import io
import os
import json
import uuid
from datetime import datetime, timedelta

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaInMemoryUpload
except ImportError:
    print("\n  Missing Google libraries. Run:\n")
    print("    pip install google-api-python-client google-auth-oauthlib google-auth-httplib2\n")
    sys.exit(1)

# ============================================================================
# CONFIG
# ============================================================================
SCOPES          = ["https://www.googleapis.com/auth/drive.file"]
SCRIPT_DIR      = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH      = os.path.join(SCRIPT_DIR, "project_manager_token.json")
CREDS_PATH      = os.path.join(SCRIPT_DIR, "project_manager_credentials.json")
DRIVE_FILE_NAME = "project_manager_data.json"

_drive_file_id = None  # cached across the session


# ============================================================================
# GOOGLE DRIVE HELPERS
# ============================================================================
def _get_drive_service():
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDS_PATH):
                print(f"\n  ERROR: credentials file not found:\n    {CREDS_PATH}")
                print("\n  Setup steps:")
                print("    1. https://console.cloud.google.com/  -> new project")
                print("    2. Enable Google Drive API")
                print("    3. Credentials -> OAuth 2.0 Client ID (Desktop app)")
                print("    4. Download JSON -> rename to  project_manager_credentials.json")
                print("    5. Place it in the same folder as this script, then re-run.\n")
                sys.exit(1)
            flow  = InstalledAppFlow.from_client_secrets_file(CREDS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return build("drive", "v3", credentials=creds)


def _find_or_create_file(service) -> str:
    global _drive_file_id
    if _drive_file_id:
        return _drive_file_id
    results = service.files().list(
        q=f"name='{DRIVE_FILE_NAME}' and trashed=false",
        spaces="drive",
        fields="files(id, name)"
    ).execute()
    files = results.get("files", [])
    if files:
        _drive_file_id = files[0]["id"]
        return _drive_file_id
    # First time — create an empty file
    empty = json.dumps({"projects": []}, indent=2)
    media = MediaInMemoryUpload(empty.encode("utf-8"), mimetype="application/json")
    f = service.files().create(body={"name": DRIVE_FILE_NAME}, media_body=media, fields="id").execute()
    _drive_file_id = f["id"]
    print(f"  Created '{DRIVE_FILE_NAME}' on your Google Drive.")
    return _drive_file_id


def load_projects(service) -> dict:
    file_id = _find_or_create_file(service)
    content = service.files().get_media(fileId=file_id).execute()
    if isinstance(content, bytes):
        content = content.decode("utf-8")
    return json.loads(content) if content.strip() else {"projects": []}


def save_projects(service, data: dict):
    file_id = _find_or_create_file(service)
    content = json.dumps(data, indent=2, ensure_ascii=False)
    media   = MediaInMemoryUpload(content.encode("utf-8"), mimetype="application/json")
    service.files().update(fileId=file_id, media_body=media).execute()


# ============================================================================
# SCORING / QUEUE
# ============================================================================
def _days_since_last_note(project: dict) -> float:
    notes = project.get("notes", [])
    if not notes:
        return 9999
    last_date = sorted(notes, key=lambda n: n.get("date", ""), reverse=True)[0].get("date", "")
    try:
        dt = datetime.fromisoformat(last_date)
        return (datetime.now() - dt).total_seconds() / 86400
    except Exception:
        return 9999


def build_project_queue(projects: list) -> list:
    active = [p for p in projects if p.get("status", "active") == "active"]

    def sort_key(p):
        days           = min(_days_since_last_note(p), 30) / 30   # 0-1, higher = more overdue
        priority       = p.get("priority", 5)
        priority_score = (6 - min(priority, 5)) / 5               # priority 1 -> 1.0, 5 -> 0.2
        return days * 0.6 + priority_score * 0.4

    return sorted(active, key=sort_key, reverse=True)


# ============================================================================
# DISPLAY
# ============================================================================
def print_separator(char="=", width=60):
    print(char * width)


def _wrap(text: str, width: int = 54) -> list:
    words = text.split()
    lines, line = [], ""
    for w in words:
        if len(line) + len(w) + 1 > width:
            lines.append(line)
            line = w
        else:
            line = (line + " " + w).strip()
    if line:
        lines.append(line)
    return lines or [""]


def print_project_card(project: dict, rank: int, total: int):
    print_separator()
    priority_label = {1: "!! CRITICAL", 2: "! HIGH", 3: "NORMAL", 4: "low", 5: "background"}.get(
        project.get("priority", 3), "NORMAL"
    )
    print(f"  #{rank} of {total}   [{priority_label}]")
    print_separator()
    print(f"  Title    : {project['title']}")

    desc = project.get("description", "").strip()
    if desc:
        wrapped = _wrap(desc)
        print(f"  Desc     : {wrapped[0]}")
        for line in wrapped[1:]:
            print(f"             {line}")

    contacts = project.get("contacts", [])
    if contacts:
        print(f"  Contacts : {', '.join(contacts)}")

    days = _days_since_last_note(project)
    days_str = f"{int(days)}d ago" if days < 9999 else "never"
    note_count = len(project.get("notes", []))
    print(f"  Last note: {days_str}  ({note_count} total)")
    print_separator("-", 60)


def print_prior_notes(project: dict, limit: int = 3):
    notes = sorted(project.get("notes", []), key=lambda n: n.get("date", ""), reverse=True)[:limit]
    print("  Prior notes:")
    if not notes:
        print("    (none yet)")
    else:
        for i, note in enumerate(notes, 1):
            try:
                dt   = datetime.fromisoformat(note["date"])
                when = dt.strftime("%Y-%m-%d %I:%M %p")
            except Exception:
                when = note.get("date", "?")
            body    = note.get("body", "").strip() or "(empty)"
            preview = body[:150].replace("\n", " ")
            if len(body) > 150:
                preview += "..."
            print(f"    {i}. [{when}]")
            print(f"       {preview}")
    print_separator("-", 60)


def print_scorecard(projects: list):
    active   = [p for p in projects if p.get("status", "active") == "active"]
    archived = [p for p in projects if p.get("status") == "archived"]
    now      = datetime.now()
    today    = now.date()
    week_ago = now - timedelta(days=7)

    all_note_dts = []
    for p in projects:
        for n in p.get("notes", []):
            try:
                all_note_dts.append((datetime.fromisoformat(n["date"]), p["title"]))
            except Exception:
                pass

    today_notes = [(dt, t) for dt, t in all_note_dts if dt.date() == today]
    week_notes  = [(dt, t) for dt, t in all_note_dts if dt >= week_ago]
    stale       = [p for p in active if _days_since_last_note(p) > 7]

    print()
    print_separator("=", 60)
    print("  PROJECT SCORECARD")
    print_separator("=", 60)
    print(f"  Today              : {now.strftime('%A %b %d, %Y')}")
    print_separator("-", 60)
    print(f"  Active projects    : {len(active)}")
    print(f"  Archived           : {len(archived)}")
    print(f"  Notes today        : {len(today_notes)}")
    print(f"  Notes this week    : {len(week_notes)}")
    print_separator("-", 60)

    if today_notes:
        print("\n  Today's notes:")
        for dt, title in sorted(today_notes, reverse=True):
            print(f"    {dt.strftime('%I:%M %p')}  {title}")

    if stale:
        print(f"\n  Stale (7+ days, no notes) — {len(stale)} project(s):")
        for p in sorted(stale, key=_days_since_last_note, reverse=True):
            d = _days_since_last_note(p)
            print(f"    {int(d):3d}d  {p['title']}")

    print_separator("=", 60)


# ============================================================================
# INTERACTIVE ACTIONS
# ============================================================================
def _prompt_multiline(label: str) -> str:
    print(f"  {label}")
    print("  (Press Enter twice when done.)")
    lines = []
    while True:
        line = input("  | ")
        if line == "" and lines and lines[-1] == "":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def add_note_to_project(project: dict):
    body = _prompt_multiline(f"Notes for '{project['title']}':")
    if not body:
        body = "(no notes entered)"
    project.setdefault("notes", []).append({
        "date": datetime.now().isoformat(),
        "body": body,
    })


def add_new_project(data: dict) -> dict:
    print_separator("=", 60)
    print("  ADD NEW PROJECT")
    print_separator("-", 60)

    title = input("  Title: ").strip()
    if not title:
        print("  (cancelled — no title entered)\n")
        return data

    desc     = input("  Description (one line): ").strip()
    contacts = input("  Contacts (comma-separated, or blank): ").strip()
    priority_raw = input("  Priority 1-5  (1=critical  3=normal  5=background) [3]: ").strip()

    try:
        priority = max(1, min(5, int(priority_raw)))
    except ValueError:
        priority = 3

    data["projects"].append({
        "id":          str(uuid.uuid4()),
        "title":       title,
        "description": desc,
        "contacts":    [c.strip() for c in contacts.split(",") if c.strip()],
        "priority":    priority,
        "status":      "active",
        "created_at":  datetime.now().isoformat(),
        "notes":       [],
    })
    print(f"\n  '{title}' added.\n")
    return data


def edit_project(project: dict):
    print_separator("-", 60)
    print("  EDIT PROJECT  (leave blank to keep current value)")
    print_separator("-", 60)

    t = input(f"  Title [{project['title']}]: ").strip()
    if t:
        project["title"] = t

    d = input(f"  Description [{project.get('description', '')}]: ").strip()
    if d:
        project["description"] = d

    c_str = ", ".join(project.get("contacts", []))
    c = input(f"  Contacts [{c_str}]: ").strip()
    if c:
        project["contacts"] = [x.strip() for x in c.split(",") if x.strip()]

    p = input(f"  Priority 1-5 [{project.get('priority', 3)}]: ").strip()
    if p:
        try:
            project["priority"] = max(1, min(5, int(p)))
        except ValueError:
            pass

    print("  Updated.\n")


# ============================================================================
# MAIN LOOP
# ============================================================================
def run_project_session():
    print_separator("=", 60)
    print("  PROJECT MANAGER")
    print("  n=note  s=skip  e=edit  a=add  ar=archive  sc=scorecard  q=quit")
    print_separator("=", 60)
    print()

    print("  Connecting to Google Drive...")
    try:
        service = _get_drive_service()
        print("  Connected.\n")
    except Exception as ex:
        print(f"  ERROR connecting to Google Drive: {ex}")
        sys.exit(1)

    print("  Loading projects...")
    data     = load_projects(service)
    projects = data.get("projects", [])
    active   = [p for p in projects if p.get("status", "active") == "active"]
    print(f"  {len(active)} active project(s).\n")

    queue = build_project_queue(projects)

    if not queue:
        print("  No active projects yet.")
        ans = input("  Add your first project? (y/n): ").strip().lower()
        if ans == "y":
            data = add_new_project(data)
            save_projects(service, data)
            queue = build_project_queue(data["projects"])
        if not queue:
            return

    idx = 0
    while True:
        if idx >= len(queue):
            print("  You've reviewed all projects in the queue.")
            ans = input("  Start over from the top? (y/n): ").strip().lower()
            if ans == "y":
                queue = build_project_queue(data["projects"])
                idx   = 0
                continue
            else:
                break

        project = queue[idx]
        print_project_card(project, idx + 1, len(queue))
        print("  [n] Note   [s] Skip   [e] Edit   [a] Add new project")
        print("  [ar] Archive   [sc] Scorecard   [q] Quit")
        action = input("  > ").strip().lower()

        if action in ("q", "quit", "exit"):
            break

        elif action in ("sc", "scorecard"):
            print_scorecard(data["projects"])
            continue

        elif action in ("a", "add"):
            data = add_new_project(data)
            save_projects(service, data)
            queue = build_project_queue(data["projects"])
            # don't advance idx — re-show current card

        elif action in ("s", "skip"):
            print(f"  Skipping '{project['title']}'.\n")
            idx += 1

        elif action in ("n", "note", ""):
            print()
            print_prior_notes(project)
            add_note_to_project(project)
            save_projects(service, data)
            print(f"\n  Saved to Google Drive.\n")
            idx += 1

        elif action in ("e", "edit"):
            edit_project(project)
            save_projects(service, data)

        elif action in ("ar", "archive"):
            confirm = input(f"  Archive '{project['title']}'? (y/n): ").strip().lower()
            if confirm == "y":
                project["status"] = "archived"
                save_projects(service, data)
                queue = build_project_queue(data["projects"])
                idx   = min(idx, len(queue) - 1) if queue else 0
                print(f"  Archived. {len(queue)} project(s) remaining.\n")
            else:
                print("  (cancelled)\n")

        else:
            print(f"  Unknown command '{action}'. Use: n / s / e / a / ar / sc / q\n")

    print("\n  Session ended.")
    print_scorecard(data["projects"])


if __name__ == "__main__":
    run_project_session()
