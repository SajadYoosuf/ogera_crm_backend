"""
Move contacts that are assigned to an admin over to other employees.
Each one goes to a random active employee who is not an admin, manager or support
(the same pool as auto-assignment on POST /contacts).
    python scripts/reassign_admin_leads.py           # dry run
    python scripts/reassign_admin_leads.py --commit
"""

import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import SessionLocal, engine
import models  # noqa: F401
from models import Contact, Employee

NON_ASSIGNABLE_ROLES = ("admin", "manager", "support")


def main():
    engine.echo = False
    db = SessionLocal()
    try:
        pool = (
            db.query(Employee)
            .filter(
                Employee.deleted_at.is_(None),
                Employee.is_active == 1,
                Employee.role.notin_(NON_ASSIGNABLE_ROLES),
            )
            .all()
        )
        if not pool:
            print("No eligible employees to assign to; nothing changed.")
            return
        contacts = (
            db.query(Contact)
            .join(Employee, Employee.id == Contact.assigned_to)
            .filter(Employee.role == "admin", Contact.deleted_at.is_(None))
            .all()
        )
        moved = Counter()
        for contact in contacts:
            employee = random.choice(pool)
            contact.assigned_to = employee.id
            moved[employee.name] += 1
        print(f"{len(contacts)} contact(s) assigned to an admin.")
        for name, count in moved.most_common():
            print(f"  -> {name}: {count}")
        if "--commit" in sys.argv:
            db.commit()
            print("Committed.")
        else:
            db.rollback()
            print("Dry run - pass --commit to write.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
