"""Live smoke test: drive the full Sourcewell loop against a running API + Mailpit.

Prereqs: `make up` and `make dev` running. Then: `python3 scripts/smoke.py`.
Drives auto mode end to end and asserts an email reached Mailpit.
"""

import json
import random
import urllib.request

API = "http://localhost:8901"
MAILPIT = "http://localhost:8904"


def call(method, url, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def main():
    slug = f"qa{random.randint(1000, 999999)}"
    uid = call(
        "POST",
        f"{API}/organizations",
        {
            "org_name": "QA Org",
            "slug": slug,
            "admin_email": f"admin@{slug}.com",
            "admin_name": "QA",
        },
    )["admin_user_id"]
    print("user        =", uid)

    h = {"X-User-Id": uid}
    ws = call("POST", f"{API}/workspaces", {"name": "QA Team", "kind": "team"}, h)["id"]
    h["X-Workspace-Id"] = ws
    print("workspace   =", ws)

    print(
        "contacts    =",
        call("POST", f"{API}/contacts/sample", {"count": 5}, h)["created"],
    )

    cid = call(
        "POST",
        f"{API}/campaigns",
        {
            "name": "QA Backend hire",
            "criteria": {"skills": ["python"], "titles": ["engineer"]},
            "sequence": [
                {
                    "channel": "email",
                    "delay_days": 0,
                    "subject": "Hi {first_name}",
                    "body": "Saw your work at {company} - open to a chat about a {title} role?",
                }
            ],
            "autonomy_mode": "auto",
            "from_email": "recruiter@qa.sourcewell.dev",
        },
        h,
    )["id"]
    print("campaign    =", cid)

    ranked = call("POST", f"{API}/campaigns/{cid}/rank", {}, h)
    print(
        "proposed    =",
        ranked["proposed"],
        "| top score =",
        ranked["enrollments"][0]["score"],
    )
    eid = ranked["enrollments"][0]["id"]

    print(
        "approve     =",
        call("POST", f"{API}/enrollments/{eid}/approve", {}, h)["state"],
    )
    print("run-due #1  =", call("POST", f"{API}/admin/run-due", {}, h))
    print("run-due #2  =", call("POST", f"{API}/admin/run-due", {}, h))

    thread = call("GET", f"{API}/enrollments/{eid}/messages", None, h)
    print("thread      =", [(m["status"], m["subject"]) for m in thread])

    mp = call("GET", f"{MAILPIT}/api/v1/messages", None)
    print("mailpit total =", mp["total"])
    for m in mp["messages"]:
        print("   ->", m["To"][0]["Address"], "|", m["Subject"])

    assert any(m["status"] == "sent" for m in thread), "no sent message"
    assert mp["total"] >= 1, "no email reached mailpit"
    print("\nSMOKE OK")


if __name__ == "__main__":
    main()
