"""Create (or remove) demo closings so the Trends tab has something to show.

    python seed_demo_data.py             add 60 days of demo closings
    python seed_demo_data.py --remove    delete them again

Use it on development database only.
"""
import random
import sys
from datetime import datetime, time, timedelta

from app import app
from models import (db, Bar, CashCount, Category, ClosingNote, Count, Employee,
                    InventorySession, Item, Role, TaskCompletion)

DEMO_NAME = "Demo Employee"   # every demo closing belongs to this archived employee
DAYS = 60


def remove_demo():
    employee = Employee.query.filter_by(name=DEMO_NAME).first()
    if not employee:
        print("No demo data found.")
        return
    for closing in InventorySession.query.filter_by(employee_id=employee.id).all():
        Count.query.filter_by(session_id=closing.id).delete()
        TaskCompletion.query.filter_by(session_id=closing.id).delete()
        ClosingNote.query.filter_by(session_id=closing.id).delete()
        for cash in CashCount.query.filter_by(session_id=closing.id).all():
            db.session.delete(cash)
        db.session.delete(closing)
    db.session.delete(employee)
    db.session.commit()
    print("Demo data removed.")


def add_demo():
    if Employee.query.filter_by(name=DEMO_NAME).first():
        print("Demo data already exists. Run with --remove first.")
        return
    bar = Bar.query.first()
    items = (Item.query.join(Category)
             .filter(Category.bar_id == bar.id, Item.archived_at.is_(None)).all())
    if not items:
        print("Add some stock items in the Stock tab first.")
        return

    employee = Employee(name=DEMO_NAME, bar_id=bar.id, archived_at=datetime.now(),
                        role_id=Role.query.filter_by(name="employee").first().id)
    db.session.add(employee)
    db.session.flush()

    random.seed(42)   # same demo data every run
    levels = {item.id: random.randint(8, 24) for item in items}
    today = datetime.now().date()
    for offset in range(DAYS, 0, -1):
        day = today - timedelta(days=offset)
        closing = InventorySession(
            bar_id=bar.id, employee_id=employee.id, status="completed",
            started_at=datetime.combine(day, time(22, 45)),
            completed_at=datetime.combine(day, time(23, 30)))
        db.session.add(closing)
        db.session.flush()
        for item in items:
            used = random.choice([0, 1, 1, 2, 2, 3, 4]) + (3 if day.weekday() >= 4 else 0)
            levels[item.id] -= used
            if levels[item.id] < 4:
                levels[item.id] += random.choice([12, 18, 24])   # a delivery arrives
            db.session.add(Count(session_id=closing.id, item_id=item.id,
                                 quantity=max(levels[item.id], 0)))
    db.session.commit()
    print(f"Added {DAYS} demo closings for {len(items)} items.")


if __name__ == "__main__":
    with app.app_context():
        remove_demo() if "--remove" in sys.argv else add_demo()