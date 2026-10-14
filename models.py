from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class Bar(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)

    categories = db.relationship("Category", backref="bar", lazy=True)
    employees = db.relationship("Employee", backref="bar", lazy=True)


class Role(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(30), unique=True, nullable=False)  # "admin", "employee"


class Employee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    pin_hash = db.Column(db.String(200))  # null for roles that don't need a PIN
    bar_id = db.Column(db.Integer, db.ForeignKey("bar.id"), nullable=False)
    role_id = db.Column(db.Integer, db.ForeignKey("role.id"), nullable=False)
    failed_attempts = db.Column(db.Integer, default=0, nullable=False)
    locked_until = db.Column(db.DateTime)
    must_change_pin = db.Column(db.Boolean, default=False, nullable=False)
    code_hash = db.Column(db.String(64), unique=True)  # keyed hash of the closing code; None = no code
    archived_at = db.Column(db.DateTime)               # set when removed; closings keep pointing at them

    role = db.relationship("Role")

    def set_pin(self, raw_pin):
        self.pin_hash = generate_password_hash(raw_pin)

    def check_pin(self, raw_pin):
        if not self.pin_hash:
            return False
        return check_password_hash(self.pin_hash, raw_pin)


class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    bar_id = db.Column(db.Integer, db.ForeignKey("bar.id"), nullable=False)

    items = db.relationship("Item", backref="category", lazy=True)
    archived_at = db.Column(db.DateTime)   


class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("category.id"), nullable=False)
    archived_at = db.Column(db.DateTime)


class InventorySession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bar_id = db.Column(db.Integer, db.ForeignKey("bar.id"), nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=False)
    started_at = db.Column(db.DateTime, default=datetime.now)
    completed_at = db.Column(db.DateTime)
    status = db.Column(db.String(20), default="in_progress")  # in_progress / completed

    employee = db.relationship("Employee")
    counts = db.relationship("Count", backref="session", lazy=True)
    notes = db.relationship("ClosingNote", backref="session", lazy=True,
                            order_by="ClosingNote.created_at")


class Count(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("inventory_session.id"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)

    item = db.relationship("Item")


class ClosingTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bar_id = db.Column(db.Integer, db.ForeignKey("bar.id"), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    archived_at = db.Column(db.DateTime)


class CashCount(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("inventory_session.id"), nullable=False)
    total = db.Column(db.Numeric(10, 2), nullable=False)
    tip_pool_total = db.Column(db.Numeric(10, 2))

    lines = db.relationship("CashCountLine", backref="cash_count",
                            cascade="all, delete-orphan", lazy=True)


class CashCountLine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cash_count_id = db.Column(db.Integer, db.ForeignKey("cash_count.id"), nullable=False)
    denomination_cents = db.Column(db.Integer, nullable=False)  # $20 bill = 2000
    quantity = db.Column(db.Integer, nullable=False)


class TaskCompletion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("inventory_session.id"), nullable=False)
    task_id = db.Column(db.Integer, db.ForeignKey("closing_task.id"), nullable=False)
    done = db.Column(db.Boolean, nullable=False, default=True)  


class ClosingNote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("inventory_session.id"), nullable=False)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)