"""
BudgetBuddy - Single file full-stack expense tracker
Accounts + EMI + Income + Themes + PDF Reports + Indian Rupees
Uses PyJWT + ReportLab

Run: python main.py  ->  http://localhost:8000
"""
import os
import io
import csv
import random
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, date
from decimal import Decimal
from typing import Optional, List

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field, ConfigDict
from sqlalchemy import (
    create_engine, Column, String, Integer, Numeric, Date, DateTime,
    Boolean, ForeignKey, func
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship
from passlib.context import CryptContext

# PyJWT — safe imports for all versions
import jwt
try:
    from jwt import InvalidTokenError as JWTError
except ImportError:
    try:
        from jwt import PyJWTError as JWTError
    except ImportError:
        JWTError = Exception

# ReportLab for PDF
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors as rl_colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
)
from reportlab.lib.units import mm
import pandas as pd
import numpy as np

# ============ CONFIG ============
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me-in-production-please-32chars")
ALGORITHM = "HS256"
TOKEN_EXPIRE_MIN = 60 * 24 * 7
# Read Turso environment variables
# Read Turso environment variables
TURSO_DATABASE_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN")

if TURSO_DATABASE_URL and TURSO_AUTH_TOKEN:
    # Production: Connect to Turso (libSQL)
    # Strip any existing protocol prefix to avoid double-prefixing
    host = TURSO_DATABASE_URL.replace("libsql://", "").replace("https://", "").replace("http://", "")
    DATABASE_URL = f"sqlite+libsql://{host}?authToken={TURSO_AUTH_TOKEN}&secure=true"
    engine = create_engine(DATABASE_URL)
    print(f"✅ Using Turso database: {host}")
else:
    # Local fallback: Use a local SQLite file
    DATABASE_URL = "sqlite:///./budgetbuddy.db"
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False}
    )
    print("⚠️  Using local SQLite (Turso not configured)")
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

# ============ MODELS ============
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, default="")
    currency = Column(String, default="INR")
    created_at = Column(DateTime, default=datetime.utcnow)

class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    name = Column(String, nullable=False)
    icon = Column(String, default="tag")
    color = Column(String, default="#6366f1")
    is_system = Column(Boolean, default=False)

class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    type = Column(String, default="bank")
    balance = Column(Numeric(14, 2), default=0)
    color = Column(String, default="#3b82f6")
    icon = Column(String, default="card")
    created_at = Column(DateTime, default=datetime.utcnow)

class Expense(Base):
    __tablename__ = "expenses"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    amount = Column(Numeric(12, 2), nullable=False)
    description = Column(String, default="")
    date = Column(Date, nullable=False, default=date.today, index=True)
    payment_method = Column(String, default="upi")
    created_at = Column(DateTime, default=datetime.utcnow)
    category = relationship("Category")

class Budget(Base):
    __tablename__ = "budgets"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    amount = Column(Numeric(12, 2), nullable=False)
    period = Column(String, default="monthly")
    created_at = Column(DateTime, default=datetime.utcnow)
    category = relationship("Category")

class EMI(Base):
    __tablename__ = "emis"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    name = Column(String, nullable=False)
    amount = Column(Numeric(14, 2), nullable=False)
    due_day = Column(Integer, default=5)
    total_months = Column(Integer, nullable=True)
    paid_months = Column(Integer, default=0)
    color = Column(String, default="#ef4444")
    is_active = Column(Boolean, default=True)
    notes = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    account = relationship("Account")

class IncomeSource(Base):
    __tablename__ = "incomes"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    name = Column(String, nullable=False)
    amount = Column(Numeric(14, 2), nullable=False)
    pay_day = Column(Integer, default=1)
    color = Column(String, default="#10b981")
    is_active = Column(Boolean, default=True)
    notes = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    account = relationship("Account")

class RecurringLog(Base):
    __tablename__ = "recurring_logs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    kind = Column(String, nullable=False)
    ref_id = Column(Integer, nullable=False)
    month_key = Column(String, nullable=False)
    amount = Column(Numeric(14, 2), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    processed_at = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

DEFAULT_CATEGORIES = [
    ("Food", "#ef4444"), ("Transport", "#3b82f6"), ("Housing", "#8b5cf6"),
    ("Entertainment", "#ec4899"), ("Health", "#10b981"), ("Shopping", "#f59e0b"),
    ("Utilities", "#06b6d4"), ("EMI", "#ef4444"), ("Other", "#6b7280"),
]

def seed_default_categories(db, user_id):
    for name, color in DEFAULT_CATEGORIES:
        db.add(Category(user_id=user_id, name=name, color=color, is_system=True))
    db.commit()

# ============ SCHEMAS ============
class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
    full_name: str = ""

class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    full_name: str
    currency: str

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

class AccountIn(BaseModel):
    name: str
    type: str = "bank"
    balance: float = 0
    color: str = "#3b82f6"
    icon: str = "card"

class AccountOut(BaseModel):
    id: int
    name: str
    type: str
    balance: float
    color: str
    icon: str

class ExpenseIn(BaseModel):
    amount: float = Field(gt=0)
    description: str = ""
    date: date
    category_id: Optional[int] = None
    account_id: Optional[int] = None
    payment_method: str = "upi"

class BudgetIn(BaseModel):
    amount: float = Field(gt=0)
    category_id: Optional[int] = None
    period: str = "monthly"

class EMIIn(BaseModel):
    name: str
    amount: float = Field(gt=0)
    account_id: int
    due_day: int = Field(ge=1, le=28, default=5)
    total_months: Optional[int] = None
    color: str = "#ef4444"
    notes: str = ""

class IncomeIn(BaseModel):
    name: str
    amount: float = Field(gt=0)
    account_id: int
    pay_day: int = Field(ge=1, le=28, default=1)
    color: str = "#10b981"
    notes: str = ""

# ============ AUTH ============
def hash_pw(p): return pwd_context.hash(p)
def verify_pw(p, h): return pwd_context.verify(p, h)

def create_token(uid):
    return jwt.encode(
        {"sub": str(uid), "exp": datetime.utcnow() + timedelta(minutes=TOKEN_EXPIRE_MIN)},
        SECRET_KEY, algorithm=ALGORITHM
    )

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    exc = HTTPException(401, "Invalid credentials", headers={"WWW-Authenticate": "Bearer"})
    if not token:
        raise exc
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        uid = int(payload.get("sub"))
    except Exception:
        raise exc
    user = db.query(User).filter(User.id == uid).first()
    if not user:
        raise exc
    return user

def _next_day(day):
    today = date.today()
    d = today.replace(day=min(day, 28))
    if d < today:
        if today.month == 12:
            d = d.replace(year=today.year + 1, month=1)
        else:
            d = d.replace(month=today.month + 1)
    return d

# ============ LIFESPAN (startup + shutdown) ============
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---------- STARTUP ----------
    db = SessionLocal()
    try:
        demo = db.query(User).filter(User.email == "demo@budgetbuddy.app").first()
        if not demo:
            demo = User(email="demo@budgetbuddy.app", hashed_password=hash_pw("demo1234"),
                        full_name="Demo User", currency="INR")
            db.add(demo); db.commit(); db.refresh(demo)
            seed_default_categories(db, demo.id)

            cats = db.query(Category).filter(Category.user_id == demo.id).all()
            cm = {c.name: c.id for c in cats}

            sbi = Account(user_id=demo.id, name="SBI Savings", type="bank",
                          balance=Decimal("45000"), color="#3b82f6")
            hdfc = Account(user_id=demo.id, name="HDFC Salary", type="bank",
                           balance=Decimal("85000"), color="#8b5cf6")
            paytm = Account(user_id=demo.id, name="Paytm Wallet", type="wallet",
                            balance=Decimal("1200"), color="#06b6d4")
            cash = Account(user_id=demo.id, name="Cash", type="cash",
                           balance=Decimal("3500"), color="#10b981")
            db.add_all([sbi, hdfc, paytm, cash]); db.commit()
            for a in [sbi, hdfc, paytm, cash]: db.refresh(a)

            db.add_all([
                EMI(user_id=demo.id, account_id=hdfc.id, name="Home Loan",
                    amount=Decimal("18500"), due_day=5, total_months=240,
                    paid_months=18, color="#ef4444"),
                EMI(user_id=demo.id, account_id=hdfc.id, name="Car Loan",
                    amount=Decimal("12500"), due_day=10, total_months=60,
                    paid_months=22, color="#f59e0b"),
                EMI(user_id=demo.id, account_id=sbi.id, name="Credit Card Bill",
                    amount=Decimal("8000"), due_day=15, color="#8b5cf6"),
            ])
            db.add_all([
                IncomeSource(user_id=demo.id, account_id=hdfc.id, name="Monthly Salary",
                             amount=Decimal("85000"), pay_day=1),
                IncomeSource(user_id=demo.id, account_id=sbi.id, name="Freelance",
                             amount=Decimal("12000"), pay_day=20, color="#06b6d4"),
            ])
            db.commit()

            sample = [
                ("Lunch at cafe", "Food", 250), ("Groceries", "Food", 1850),
                ("Swiggy order", "Food", 420), ("Zomato dinner", "Food", 680),
                ("Chai & snacks", "Food", 60), ("Uber ride", "Transport", 180),
                ("Petrol", "Transport", 1200), ("Metro card", "Transport", 500),
                ("Netflix", "Entertainment", 649), ("Movie (PVR)", "Entertainment", 700),
                ("Gym", "Health", 1500), ("Pharmacy", "Health", 350),
                ("Shoes (Myntra)", "Shopping", 2499), ("Amazon order", "Shopping", 1350),
                ("Electricity", "Utilities", 2100), ("Jio Fiber", "Utilities", 999),
            ]
            accs = [sbi, hdfc, paytm, cash]
            payments = ["upi", "card", "cash", "netbanking", "wallet"]
            today = date.today()
            for _ in range(80):
                desc, cat, amt = random.choice(sample)
                d = today - timedelta(days=random.randint(0, 60))
                v = amt * (0.8 + random.random() * 0.6)
                acc = random.choice(accs)
                db.add(Expense(user_id=demo.id, category_id=cm.get(cat), account_id=acc.id,
                               amount=Decimal(str(round(v, 2))), description=desc,
                               date=d, payment_method=random.choice(payments)))

            db.add_all([
                Budget(user_id=demo.id, category_id=cm.get("Food"), amount=Decimal("8000")),
                Budget(user_id=demo.id, category_id=cm.get("Transport"), amount=Decimal("3000")),
                Budget(user_id=demo.id, category_id=cm.get("Entertainment"), amount=Decimal("2000")),
                Budget(user_id=demo.id, category_id=cm.get("Shopping"), amount=Decimal("5000")),
            ])
            db.commit()
            print("✅ Seeded demo user: demo@budgetbuddy.app / demo1234")
    finally:
        db.close()

    yield  # ← app requests handle karta hai

    # ---------- SHUTDOWN ----------
    print("👋 BudgetBuddy shutting down")

# ============ APP ============
app = FastAPI(title="BudgetBuddy", version="2.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

# ---- AUTH ----
@app.post("/api/auth/register", response_model=Token)
def register(data: UserCreate, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == data.email).first():
        raise HTTPException(400, "Email already registered")
    user = User(email=data.email, hashed_password=hash_pw(data.password),
                full_name=data.full_name, currency="INR")
    db.add(user); db.commit(); db.refresh(user)
    seed_default_categories(db, user.id)
    return Token(access_token=create_token(user.id))

@app.post("/api/auth/login", response_model=Token)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form.username).first()
    if not user or not verify_pw(form.password, user.hashed_password):
        raise HTTPException(401, "Incorrect email or password")
    return Token(access_token=create_token(user.id))

@app.get("/api/auth/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user

# ---- CATEGORIES ----
@app.get("/api/categories")
def list_cats(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(Category).filter(
        (Category.user_id == user.id) | (Category.user_id.is_(None))
    ).order_by(Category.name).all()
    return [{"id": c.id, "name": c.name, "color": c.color, "icon": c.icon, "is_system": c.is_system} for c in rows]

# ---- ACCOUNTS ----
@app.get("/api/accounts", response_model=List[AccountOut])
def list_accounts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(Account).filter(Account.user_id == user.id).all()
    return [AccountOut(id=a.id, name=a.name, type=a.type, balance=float(a.balance),
                       color=a.color, icon=a.icon) for a in rows]

@app.post("/api/accounts", response_model=AccountOut)
def create_account(data: AccountIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    a = Account(user_id=user.id, name=data.name, type=data.type,
                balance=Decimal(str(data.balance)), color=data.color, icon=data.icon)
    db.add(a); db.commit(); db.refresh(a)
    return AccountOut(id=a.id, name=a.name, type=a.type, balance=float(a.balance),
                      color=a.color, icon=a.icon)

@app.patch("/api/accounts/{aid}")
def update_account(aid: int, data: AccountIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    a = db.query(Account).filter(Account.id == aid, Account.user_id == user.id).first()
    if not a:
        raise HTTPException(404, "Account not found")
    a.name = data.name
    a.type = data.type
    a.color = data.color
    a.balance = Decimal(str(data.balance))
    db.commit()
    return {"ok": True}

@app.delete("/api/accounts/{aid}")
def delete_account(aid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    a = db.query(Account).filter(Account.id == aid, Account.user_id == user.id).first()
    if not a:
        raise HTTPException(404, "Account not found")
    db.delete(a); db.commit()
    return {"ok": True}

# ---- EXPENSES ----
def _exp_out(e, db):
    acc = db.query(Account).filter(Account.id == e.account_id).first() if e.account_id else None
    return {
        "id": e.id, "amount": float(e.amount), "description": e.description, "date": e.date.isoformat(),
        "category_id": e.category_id, "account_id": e.account_id,
        "account_name": acc.name if acc else None, "account_color": acc.color if acc else None,
        "payment_method": e.payment_method,
        "category_name": e.category.name if e.category else None,
        "category_color": e.category.color if e.category else None,
    }

@app.get("/api/expenses")
def list_expenses(limit: int = Query(200, le=1000),
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(Expense).filter(Expense.user_id == user.id) \
        .order_by(Expense.date.desc(), Expense.id.desc()).limit(limit).all()
    return [_exp_out(e, db) for e in rows]

@app.post("/api/expenses")
def create_expense(data: ExpenseIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    e = Expense(user_id=user.id, amount=Decimal(str(data.amount)),
                description=data.description, date=data.date,
                category_id=data.category_id, account_id=data.account_id,
                payment_method=data.payment_method)
    db.add(e)
    if data.account_id:
        acc = db.query(Account).filter(Account.id == data.account_id, Account.user_id == user.id).first()
        if acc:
            acc.balance = Decimal(str(acc.balance)) - Decimal(str(data.amount))
    db.commit(); db.refresh(e)
    return _exp_out(e, db)

@app.delete("/api/expenses/{eid}")
def delete_expense(eid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    e = db.query(Expense).filter(Expense.id == eid, Expense.user_id == user.id).first()
    if not e:
        raise HTTPException(404, "Expense not found")
    if e.account_id:
        acc = db.query(Account).filter(Account.id == e.account_id, Account.user_id == user.id).first()
        if acc:
            acc.balance = Decimal(str(acc.balance)) + Decimal(str(e.amount))
    db.delete(e); db.commit()
    return {"ok": True}

@app.get("/api/expenses/export/csv")
def export_csv(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(Expense).filter(Expense.user_id == user.id).order_by(Expense.date.desc()).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "Amount (INR)", "Category", "Account", "Description", "Payment"])
    for e in rows:
        acc = db.query(Account).filter(Account.id == e.account_id).first() if e.account_id else None
        w.writerow([e.date.isoformat(), float(e.amount),
                    e.category.name if e.category else "", acc.name if acc else "",
                    e.description, e.payment_method])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=expenses.csv"})

# ---- PDF STATEMENT ----
@app.get("/api/reports/pdf")
def export_pdf(month: Optional[str] = None,
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Generate a PDF statement for the given month (format: YYYY-MM). Defaults to current month."""
    # Determine month range
    if month:
        try:
            y, m = month.split("-")
            y, m = int(y), int(m)
            start = date(y, m, 1)
        except Exception:
            raise HTTPException(400, "Invalid month format, use YYYY-MM")
    else:
        start = date.today().replace(day=1)

    # End of month
    if start.month == 12:
        end = date(start.year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(start.year, start.month + 1, 1) - timedelta(days=1)

    # Fetch data
    expenses = db.query(Expense).filter(
        Expense.user_id == user.id,
        Expense.date >= start, Expense.date <= end
    ).order_by(Expense.date.asc()).all()

    accounts = db.query(Account).filter(Account.user_id == user.id).all()
    total_bal = sum(float(a.balance) for a in accounts)

    # Category totals
    cat_totals = {}
    for e in expenses:
        cat_name = e.category.name if e.category else "Uncategorized"
        cat_totals[cat_name] = cat_totals.get(cat_name, 0) + float(e.amount)

    total_spent = sum(cat_totals.values())

    # EMIs and Incomes
    emis = db.query(EMI).filter(EMI.user_id == user.id, EMI.is_active == True).all()
    emi_total = sum(float(e.amount) for e in emis)

    incomes = db.query(IncomeSource).filter(
        IncomeSource.user_id == user.id, IncomeSource.is_active == True
    ).all()
    inc_total = sum(float(i.amount) for i in incomes)

    # ---- Build PDF ----
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15*mm, rightMargin=15*mm, topMargin=15*mm, bottomMargin=15*mm,
        title=f"BudgetBuddy Statement {start.strftime('%B %Y')}"
    )

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=22,
                        textColor=rl_colors.HexColor("#6366f1"), spaceAfter=4)
    h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=14,
                        textColor=rl_colors.HexColor("#0f172a"), spaceAfter=8, spaceBefore=12)
    sub = ParagraphStyle("SUB", parent=styles["Normal"], fontSize=10,
                         textColor=rl_colors.HexColor("#64748b"), spaceAfter=12)
    cell_style = ParagraphStyle("CELL", parent=styles["Normal"], fontSize=9)

    story = []

    # Header
    story.append(Paragraph("BudgetBuddy", h1))
    story.append(Paragraph(
        f"Statement for <b>{start.strftime('%B %Y')}</b> &nbsp;|&nbsp; {user.email}",
        sub
    ))
    story.append(Spacer(1, 6))

    # Summary box
    net = inc_total - emi_total - total_spent
    summary_data = [
        ["Income", "EMI", "Spent", "Net Saved", "Total Balance"],
        [
            f"Rs. {inc_total:,.2f}",
            f"Rs. {emi_total:,.2f}",
            f"Rs. {total_spent:,.2f}",
            f"Rs. {net:,.2f}",
            f"Rs. {total_bal:,.2f}",
        ]
    ]
    summary_table = Table(summary_data, colWidths=[36*mm]*5)
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), rl_colors.HexColor("#6366f1")),
        ("TEXTCOLOR", (0, 0), (-1, 0), rl_colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("BACKGROUND", (0, 1), (-1, 1), rl_colors.HexColor("#f1f5f9")),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 1), (-1, 1), 11),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOX", (0, 0), (-1, -1), 0.5, rl_colors.HexColor("#cbd5e1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, rl_colors.HexColor("#cbd5e1")),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 12))

    # Category breakdown
    if cat_totals:
        story.append(Paragraph("Spending by Category", h2))
        cat_rows = [["Category", "Amount", "% of Total"]]
        for name, amt in sorted(cat_totals.items(), key=lambda x: -x[1]):
            pct = (amt / total_spent * 100) if total_spent else 0
            cat_rows.append([name, f"Rs. {amt:,.2f}", f"{pct:.1f}%"])
        cat_rows.append(["Total", f"Rs. {total_spent:,.2f}", "100.0%"])
        cat_table = Table(cat_rows, colWidths=[80*mm, 55*mm, 45*mm])
        cat_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), rl_colors.HexColor("#0f172a")),
            ("TEXTCOLOR", (0, 0), (-1, 0), rl_colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -2), [rl_colors.white, rl_colors.HexColor("#f8fafc")]),
            ("BACKGROUND", (0, -1), (-1, -1), rl_colors.HexColor("#e2e8f0")),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("BOX", (0, 0), (-1, -1), 0.5, rl_colors.HexColor("#cbd5e1")),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, rl_colors.HexColor("#cbd5e1")),
        ]))
        story.append(cat_table)
        story.append(Spacer(1, 12))

    # Expense list
    story.append(Paragraph("All Expenses", h2))
    if expenses:
        exp_rows = [["Date", "Description", "Category", "Account", "Amount"]]
        for e in expenses:
            acc = db.query(Account).filter(Account.id == e.account_id).first() if e.account_id else None
            exp_rows.append([
                e.date.strftime("%d %b"),
                Paragraph(e.description or "-", cell_style),
                e.category.name if e.category else "-",
                acc.name if acc else "-",
                f"Rs. {float(e.amount):,.2f}",
            ])
        exp_table = Table(exp_rows, colWidths=[20*mm, 65*mm, 30*mm, 35*mm, 30*mm], repeatRows=1)
        exp_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), rl_colors.HexColor("#0f172a")),
            ("TEXTCOLOR", (0, 0), (-1, 0), rl_colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("ALIGN", (-1, 0), (-1, -1), "RIGHT"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [rl_colors.white, rl_colors.HexColor("#f8fafc")]),
            ("BOX", (0, 0), (-1, -1), 0.5, rl_colors.HexColor("#cbd5e1")),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, rl_colors.HexColor("#cbd5e1")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(exp_table)
    else:
        story.append(Paragraph("No expenses in this month.", cell_style))

    # Footer
    story.append(Spacer(1, 16))
    story.append(Paragraph(
        f"<i>Generated on {datetime.now().strftime('%d %b %Y, %I:%M %p')} by BudgetBuddy</i>",
        ParagraphStyle("F", parent=cell_style, textColor=rl_colors.HexColor("#94a3b8"), fontSize=8)
    ))

    doc.build(story)
    buf.seek(0)

    fname = f"budgetbuddy-{start.strftime('%Y-%m')}.pdf"
    return StreamingResponse(
        buf, media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={fname}"}
    )

# ---- BUDGETS ----
def _spent_for(db, uid, cid):
    today = date.today()
    start = today.replace(day=1)
    q = db.query(func.coalesce(func.sum(Expense.amount), 0)).filter(
        Expense.user_id == uid, Expense.date >= start, Expense.date <= today
    )
    if cid:
        q = q.filter(Expense.category_id == cid)
    return float(q.scalar() or 0)

@app.get("/api/budgets")
def list_budgets(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(Budget).filter(Budget.user_id == user.id).all()
    out = []
    for b in rows:
        spent = _spent_for(db, user.id, b.category_id)
        amt = float(b.amount)
        out.append({
            "id": b.id, "amount": amt, "category_id": b.category_id,
            "category_name": b.category.name if b.category else "Overall",
            "category_color": b.category.color if b.category else "#6366f1",
            "period": b.period, "spent": spent, "remaining": max(0, amt - spent),
            "percent": round((spent / amt * 100) if amt else 0, 1)
        })
    return out

@app.post("/api/budgets")
def create_budget(data: BudgetIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    b = Budget(user_id=user.id, amount=Decimal(str(data.amount)),
               category_id=data.category_id, period=data.period)
    db.add(b); db.commit(); db.refresh(b)
    spent = _spent_for(db, user.id, b.category_id)
    amt = float(b.amount)
    return {
        "id": b.id, "amount": amt, "category_id": b.category_id,
        "category_name": b.category.name if b.category else "Overall",
        "category_color": b.category.color if b.category else "#6366f1",
        "period": b.period, "spent": spent, "remaining": max(0, amt - spent),
        "percent": round((spent / amt * 100) if amt else 0, 1)
    }

@app.delete("/api/budgets/{bid}")
def delete_budget(bid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    b = db.query(Budget).filter(Budget.id == bid, Budget.user_id == user.id).first()
    if not b:
        raise HTTPException(404, "Budget not found")
    db.delete(b); db.commit()
    return {"ok": True}

# ---- EMIs ----
@app.get("/api/emis")
def list_emis(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(EMI).filter(EMI.user_id == user.id, EMI.is_active == True).all()
    mk = date.today().strftime("%Y-%m")
    out = []
    for e in rows:
        remaining = None
        pct = 0
        if e.total_months:
            remaining = max(0, e.total_months - e.paid_months)
            pct = round((e.paid_months / e.total_months) * 100, 1)
        paid = db.query(RecurringLog).filter(
            RecurringLog.user_id == user.id, RecurringLog.kind == "emi",
            RecurringLog.ref_id == e.id, RecurringLog.month_key == mk
        ).first() is not None
        out.append({
            "id": e.id, "name": e.name, "amount": float(e.amount),
            "account_id": e.account_id, "account_name": e.account.name if e.account else "—",
            "due_day": e.due_day, "total_months": e.total_months, "paid_months": e.paid_months,
            "remaining_months": remaining, "progress_pct": pct, "color": e.color,
            "is_active": e.is_active, "notes": e.notes,
            "next_due": _next_day(e.due_day).isoformat(), "paid_this_month": paid
        })
    return out

@app.post("/api/emis")
def create_emi(data: EMIIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    acc = db.query(Account).filter(Account.id == data.account_id, Account.user_id == user.id).first()
    if not acc:
        raise HTTPException(404, "Account not found")
    e = EMI(user_id=user.id, name=data.name, amount=Decimal(str(data.amount)),
            account_id=data.account_id, due_day=data.due_day,
            total_months=data.total_months, color=data.color, notes=data.notes)
    db.add(e); db.commit(); db.refresh(e)
    return {
        "id": e.id, "name": e.name, "amount": float(e.amount),
        "account_id": e.account_id, "account_name": acc.name,
        "due_day": e.due_day, "total_months": e.total_months, "paid_months": 0,
        "remaining_months": e.total_months, "progress_pct": 0,
        "color": e.color, "is_active": True, "notes": e.notes,
        "next_due": _next_day(e.due_day).isoformat(), "paid_this_month": False
    }

@app.delete("/api/emis/{eid}")
def delete_emi(eid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    e = db.query(EMI).filter(EMI.id == eid, EMI.user_id == user.id).first()
    if not e:
        raise HTTPException(404, "EMI not found")
    db.delete(e); db.commit()
    return {"ok": True}

@app.post("/api/emis/{eid}/pay")
def pay_emi(eid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    e = db.query(EMI).filter(EMI.id == eid, EMI.user_id == user.id).first()
    if not e:
        raise HTTPException(404, "EMI not found")
    mk = date.today().strftime("%Y-%m")
    if db.query(RecurringLog).filter(
        RecurringLog.user_id == user.id, RecurringLog.kind == "emi",
        RecurringLog.ref_id == eid, RecurringLog.month_key == mk
    ).first():
        raise HTTPException(400, "EMI already paid this month")

    acc = db.query(Account).filter(Account.id == e.account_id, Account.user_id == user.id).first()
    if not acc:
        raise HTTPException(404, "Account not found")
    acc.balance = Decimal(str(acc.balance)) - Decimal(str(e.amount))

    db.add(RecurringLog(user_id=user.id, kind="emi", ref_id=e.id, month_key=mk,
                        amount=e.amount, account_id=e.account_id))
    e.paid_months = (e.paid_months or 0) + 1

    emi_cat = db.query(Category).filter(
        Category.user_id == user.id, Category.name == "EMI"
    ).first()
    if not emi_cat:
        emi_cat = Category(user_id=user.id, name="EMI", color="#ef4444", is_system=True)
        db.add(emi_cat); db.commit(); db.refresh(emi_cat)
    db.add(Expense(user_id=user.id, category_id=emi_cat.id, account_id=e.account_id,
                   amount=e.amount, description=f"EMI: {e.name}",
                   date=date.today(), payment_method="netbanking"))
    db.commit()
    return {"ok": True, "new_balance": float(acc.balance)}

# ---- INCOME ----
@app.get("/api/incomes")
def list_incomes(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(IncomeSource).filter(
        IncomeSource.user_id == user.id, IncomeSource.is_active == True
    ).all()
    mk = date.today().strftime("%Y-%m")
    out = []
    for i in rows:
        rec = db.query(RecurringLog).filter(
            RecurringLog.user_id == user.id, RecurringLog.kind == "income",
            RecurringLog.ref_id == i.id, RecurringLog.month_key == mk
        ).first() is not None
        out.append({
            "id": i.id, "name": i.name, "amount": float(i.amount),
            "account_id": i.account_id, "account_name": i.account.name if i.account else "—",
            "pay_day": i.pay_day, "color": i.color, "is_active": i.is_active,
            "notes": i.notes, "next_pay": _next_day(i.pay_day).isoformat(),
            "received_this_month": rec
        })
    return out

@app.post("/api/incomes")
def create_income(data: IncomeIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    acc = db.query(Account).filter(Account.id == data.account_id, Account.user_id == user.id).first()
    if not acc:
        raise HTTPException(404, "Account not found")
    i = IncomeSource(user_id=user.id, name=data.name, amount=Decimal(str(data.amount)),
                     account_id=data.account_id, pay_day=data.pay_day,
                     color=data.color, notes=data.notes)
    db.add(i); db.commit(); db.refresh(i)
    return {
        "id": i.id, "name": i.name, "amount": float(i.amount),
        "account_id": i.account_id, "account_name": acc.name,
        "pay_day": i.pay_day, "color": i.color, "is_active": True,
        "notes": i.notes, "next_pay": _next_day(i.pay_day).isoformat(),
        "received_this_month": False
    }

@app.delete("/api/incomes/{iid}")
def delete_income(iid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    i = db.query(IncomeSource).filter(IncomeSource.id == iid, IncomeSource.user_id == user.id).first()
    if not i:
        raise HTTPException(404, "Income not found")
    db.delete(i); db.commit()
    return {"ok": True}

@app.post("/api/incomes/{iid}/receive")
def receive_income(iid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    i = db.query(IncomeSource).filter(IncomeSource.id == iid, IncomeSource.user_id == user.id).first()
    if not i:
        raise HTTPException(404, "Income not found")
    mk = date.today().strftime("%Y-%m")
    if db.query(RecurringLog).filter(
        RecurringLog.user_id == user.id, RecurringLog.kind == "income",
        RecurringLog.ref_id == iid, RecurringLog.month_key == mk
    ).first():
        raise HTTPException(400, "Already received this month")

    acc = db.query(Account).filter(Account.id == i.account_id, Account.user_id == user.id).first()
    if not acc:
        raise HTTPException(404, "Account not found")
    acc.balance = Decimal(str(acc.balance)) + Decimal(str(i.amount))

    db.add(RecurringLog(user_id=user.id, kind="income", ref_id=i.id, month_key=mk,
                        amount=i.amount, account_id=i.account_id))
    db.commit()
    return {"ok": True, "new_balance": float(acc.balance)}

# ---- CASHFLOW ----
@app.get("/api/cashflow")
def cashflow(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    today = date.today()
    start = today.replace(day=1)
    emi_total = float(db.query(func.coalesce(func.sum(EMI.amount), 0))
                      .filter(EMI.user_id == user.id, EMI.is_active == True).scalar() or 0)
    inc_total = float(db.query(func.coalesce(func.sum(IncomeSource.amount), 0))
                      .filter(IncomeSource.user_id == user.id, IncomeSource.is_active == True).scalar() or 0)
    exp_total = float(db.query(func.coalesce(func.sum(Expense.amount), 0))
                      .filter(Expense.user_id == user.id, Expense.date >= start).scalar() or 0)
    net = inc_total - emi_total - exp_total
    rate = round((net / inc_total * 100) if inc_total else 0, 1)
    total_bal = float(db.query(func.coalesce(func.sum(Account.balance), 0))
                      .filter(Account.user_id == user.id).scalar() or 0)
    return {
        "month": today.strftime("%Y-%m"), "total_income": round(inc_total, 2),
        "total_emi": round(emi_total, 2), "total_expenses": round(exp_total, 2),
        "net": round(net, 2), "savings_rate": rate, "accounts_total": round(total_bal, 2)
    }

# ---- ANALYTICS ----
@app.get("/api/analytics/summary")
def summary(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    today = date.today()
    ms = today.replace(day=1)
    ys = today.replace(month=1, day=1)
    pm_end = ms - timedelta(days=1)
    pm_start = pm_end.replace(day=1)

    def total(s, e):
        return float(db.query(func.coalesce(func.sum(Expense.amount), 0)).filter(
            Expense.user_id == user.id, Expense.date >= s, Expense.date <= e).scalar() or 0)

    tm = total(ms, today)
    pm = total(pm_start, pm_end)
    ty = total(ys, today)
    de = (today - ms).days + 1
    avg = tm / de if de else 0
    top = db.query(Category.name, Category.color, func.sum(Expense.amount)) \
        .join(Expense, Expense.category_id == Category.id) \
        .filter(Expense.user_id == user.id, Expense.date >= ms) \
        .group_by(Category.id).order_by(func.sum(Expense.amount).desc()).first()
    return {
        "this_month": round(tm, 2), "prev_month": round(pm, 2), "this_year": round(ty, 2),
        "daily_avg": round(avg, 2), "projected_month": round(avg * 30, 2),
        "change_pct": round(((tm - pm) / pm * 100) if pm else 0, 1),
        "top_category": {"name": top[0], "color": top[1], "total": float(top[2])} if top else None
    }

@app.get("/api/analytics/trend")
def trend(days: int = 30, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    end = date.today()
    start = end - timedelta(days=days - 1)
    rows = db.query(Expense.date, func.sum(Expense.amount)) \
        .filter(Expense.user_id == user.id, Expense.date >= start, Expense.date <= end) \
        .group_by(Expense.date).all()
    by_day = {r[0]: float(r[1]) for r in rows}
    return [{"date": (start + timedelta(days=i)).isoformat(),
             "total": by_day.get(start + timedelta(days=i), 0)} for i in range(days)]

@app.get("/api/analytics/categories")
def by_category(days: int = 30, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    start = date.today() - timedelta(days=days)
    rows = db.query(Category.name, Category.color, func.sum(Expense.amount)) \
        .join(Expense, Expense.category_id == Category.id) \
        .filter(Expense.user_id == user.id, Expense.date >= start) \
        .group_by(Category.id).order_by(func.sum(Expense.amount).desc()).all()
    return [{"name": r[0], "color": r[1], "total": float(r[2])} for r in rows]

@app.get("/api/analytics/insights")
def insights(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    result = []
    today = date.today()
    ms = today.replace(day=1)
    pm_end = ms - timedelta(days=1)
    pm_start = pm_end.replace(day=1)
    de = (today - ms).days + 1
    pm_pe = pm_start + timedelta(days=de - 1)

    cur = dict(db.query(Expense.category_id, func.sum(Expense.amount))
               .filter(Expense.user_id == user.id, Expense.date >= ms)
               .group_by(Expense.category_id).all())
    prev = dict(db.query(Expense.category_id, func.sum(Expense.amount))
                .filter(Expense.user_id == user.id, Expense.date >= pm_start,
                        Expense.date <= pm_pe).group_by(Expense.category_id).all())
    cm = {c.id: c for c in db.query(Category).all()}

    for cid, cur_amt in cur.items():
        prev_amt = float(prev.get(cid, 0) or 0)
        cf = float(cur_amt)
        if prev_amt >= 100 and cf > prev_amt * 1.3:
            name = cm[cid].name if cid in cm else "Unknown"
            pct = round((cf - prev_amt) / prev_amt * 100)
            result.append({"type": "warning", "title": f"{name} up {pct}%",
                           "message": f"₹{cf:,.2f} on {name} vs ₹{prev_amt:,.2f} last month."})

    for b in db.query(Budget).filter(Budget.user_id == user.id).all():
        spent = _spent_for(db, user.id, b.category_id)
        amt = float(b.amount)
        if amt <= 0:
            continue
        pct = spent / amt * 100
        label = b.category.name if b.category else "Overall"
        if pct >= 100:
            result.append({"type": "danger", "title": f"{label} exceeded",
                           "message": f"₹{spent:,.2f} of ₹{amt:,.2f} ({pct:.0f}%)."})
        elif pct >= 80:
            result.append({"type": "warning", "title": f"{label} at {pct:.0f}%",
                           "message": f"₹{amt - spent:,.2f} left."})

    month_total = float(db.query(func.coalesce(func.sum(Expense.amount), 0))
                        .filter(Expense.user_id == user.id, Expense.date >= ms).scalar() or 0)
    if month_total > 0 and de >= 3:
        proj = (month_total / de) * 30
        result.append({"type": "info", "title": "Projected month-end",
                       "message": f"At this pace ~₹{proj:,.2f}."})

    if not result:
        result.append({"type": "success", "title": "You're on track!",
                       "message": "No anomalies detected."})
    return result
# ============ ML / DATA SCIENCE ENDPOINTS ============
# ============ ML / DATA SCIENCE ENDPOINTS (Pure Python) ============

def _load_expenses_df(db: Session, user_id: int, days: int = 180):
    """Load user's expenses into a pandas DataFrame for analysis."""
    start = date.today() - timedelta(days=days)
    rows = db.query(Expense).filter(
        Expense.user_id == user_id,
        Expense.date >= start
    ).order_by(Expense.date.asc()).all()

    if not rows:
        return pd.DataFrame(columns=["date", "amount", "category", "description", "weekday"])

    data = []
    for e in rows:
        data.append({
            "date": pd.to_datetime(e.date),
            "amount": float(e.amount),
            "category": e.category.name if e.category else "Uncategorized",
            "description": e.description or "",
            "weekday": pd.to_datetime(e.date).weekday(),
        })
    return pd.DataFrame(data)


# ---------- Linear Regression (pure numpy) ----------
def _linear_regression(xs, ys):
    """Simple linear regression: returns (slope, intercept, r2)"""
    n = len(xs)
    if n < 2:
        return 0, 0, 0
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    num = sum((xs[i] - x_mean) * (ys[i] - y_mean) for i in range(n))
    den = sum((xs[i] - x_mean) ** 2 for i in range(n))
    if den == 0:
        return 0, y_mean, 0
    slope = num / den
    intercept = y_mean - slope * x_mean
    # R²
    ss_res = sum((ys[i] - (slope * xs[i] + intercept)) ** 2 for i in range(n))
    ss_tot = sum((ys[i] - y_mean) ** 2 for i in range(n))
    r2 = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0
    return slope, intercept, r2


# ---------- K-Means (pure numpy) ----------
def _kmeans(data, k=3, max_iter=100):
    """Simple K-Means. data: numpy array (n, features)."""
    n = len(data)
    if n < k:
        return [0] * n, []

    # Initialize centroids with spread-out points
    idxs = np.linspace(0, n - 1, k).astype(int)
    centroids = data[idxs].copy()

    labels = np.zeros(n, dtype=int)
    for _ in range(max_iter):
        # Assign
        new_labels = np.argmin(
            np.linalg.norm(data[:, None, :] - centroids[None, :, :], axis=2),
            axis=1,
        )
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        # Update
        for i in range(k):
            members = data[labels == i]
            if len(members) > 0:
                centroids[i] = members.mean(axis=0)

    return labels.tolist(), centroids.tolist()


# ---------- 1. Prediction ----------
@app.get("/api/ml/predict")
def ml_predict(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    df = _load_expenses_df(db, user.id, days=180)
    if len(df) < 10:
        return {"status": "insufficient_data",
                "message": "At least 10 expenses needed",
                "predicted_next_month": None, "confidence": None,
                "historical_months": []}

    df["month"] = df["date"].dt.to_period("M")
    monthly = df.groupby("month")["amount"].sum().reset_index()
    monthly["month_index"] = range(len(monthly))

    if len(monthly) < 3:
        return {"status": "insufficient_months",
                "message": "Need 3+ months of data",
                "predicted_next_month": None, "confidence": None,
                "historical_months": monthly.to_dict("records")}

    xs = monthly["month_index"].tolist()
    ys = monthly["amount"].tolist()
    slope, intercept, r2 = _linear_regression(xs, ys)

    predicted = max(0, slope * len(monthly) + intercept)
    confidence = round(min(95, max(30, r2 * 100)), 1)

    historical = [{"month": str(row["month"]), "total": round(float(row["amount"]), 2)}
                  for _, row in monthly.iterrows()]

    return {
        "status": "ok",
        "predicted_next_month": round(predicted, 2),
        "trend": "increasing" if slope > 0 else "decreasing",
        "confidence": confidence,
        "r2_score": round(r2, 3),
        "historical_months": historical,
        "model": "LinearRegression (pure numpy)",
    }


# ---------- 2. Clustering ----------
@app.get("/api/ml/clusters")
def ml_clusters(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    df = _load_expenses_df(db, user.id, days=180)
    if len(df) < 20:
        return {"status": "insufficient_data",
                "message": "At least 20 expenses needed", "clusters": []}

    # Features: normalized amount + weekday
    amounts = df["amount"].values.astype(float)
    weekdays = df["weekday"].values.astype(float)
    a_mean, a_std = amounts.mean(), amounts.std() + 1e-6
    amounts_norm = (amounts - a_mean) / a_std
    weekdays_norm = weekdays / 6.0
    data = np.column_stack([amounts_norm, weekdays_norm])

    labels, _ = _kmeans(data, k=3)
    df["cluster"] = labels

    labels_text = ["Small & frequent", "Medium spends", "Large spends"]
    clusters = []
    for i in range(3):
        subset = df[df["cluster"] == i]
        if len(subset) == 0:
            continue
        top_cats = subset["category"].value_counts().head(3).to_dict()
        clusters.append({
            "cluster_id": i,
            "label": labels_text[i],
            "count": len(subset),
            "avg_amount": round(float(subset["amount"].mean()), 2),
            "total": round(float(subset["amount"].sum()), 2),
            "top_categories": [{"name": k, "count": int(v)} for k, v in top_cats.items()],
        })
    clusters.sort(key=lambda c: c["avg_amount"])
    return {"status": "ok", "clusters": clusters, "algorithm": "KMeans (pure numpy, k=3)"}


# ---------- 3. Anomalies ----------
@app.get("/api/ml/anomalies")
def ml_anomalies(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    df = _load_expenses_df(db, user.id, days=90)
    if len(df) < 15:
        return {"status": "insufficient_data",
                "message": "At least 15 expenses needed", "anomalies": []}

    anomalies = []
    for cat in df["category"].unique():
        cat_df = df[df["category"] == cat].copy()
        if len(cat_df) < 5:
            continue
        mean = cat_df["amount"].mean()
        std = cat_df["amount"].std()
        if std == 0 or pd.isna(std):
            continue
        cat_df["zscore"] = (cat_df["amount"] - mean) / std
        outliers = cat_df[cat_df["zscore"].abs() > 2]
        for _, row in outliers.iterrows():
            anomalies.append({
                "date": row["date"].strftime("%Y-%m-%d"),
                "category": cat,
                "amount": round(float(row["amount"]), 2),
                "category_avg": round(float(mean), 2),
                "zscore": round(float(row["zscore"]), 2),
                "severity": "high" if abs(row["zscore"]) > 3 else "medium",
                "note": f"{abs(row['zscore']):.1f}x std dev from {cat} avg",
            })
    anomalies.sort(key=lambda a: abs(a["zscore"]), reverse=True)
    return {"status": "ok", "anomalies": anomalies[:10],
            "total_checked": len(df), "method": "Z-Score (|z| > 2)"}


# ---------- 4. Auto-categorize (keyword based) ----------
CATEGORY_KEYWORDS = {
    "Food": ["swiggy", "zomato", "lunch", "dinner", "cafe", "restaurant", "chai", "coffee",
             "groceries", "pizza", "burger", "food", "snack", "breakfast"],
    "Transport": ["uber", "ola", "petrol", "diesel", "metro", "bus", "auto", "cab",
                  "fuel", "train", "flight", "toll"],
    "Entertainment": ["netflix", "movie", "pvr", "spotify", "amazon prime", "hotstar",
                      "game", "concert", "youtube"],
    "Health": ["gym", "pharmacy", "doctor", "hospital", "medicine", "apollo",
               "clinic", "medical"],
    "Shopping": ["amazon", "flipkart", "myntra", "shoes", "clothes", "shirt",
                 "dress", "mall", "shopping"],
    "Utilities": ["electricity", "jio", "airtel", "vi", "broadband", "water bill",
                  "gas bill", "recharge", "wifi"],
    "Housing": ["rent", "mortgage", "maintenance", "society", "housing"],
    "EMI": ["emi", "loan", "installment"],
}


@app.post("/api/ml/categorize")
def ml_categorize(payload: dict, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    description = (payload.get("description") or "").strip().lower()
    if not description:
        raise HTTPException(400, "description required")

    # Score each category based on keyword matches
    scores = {}
    for cat, keywords in CATEGORY_KEYWORDS.items():
        score = 0
        for kw in keywords:
            if kw in description:
                score += 1
        if score > 0:
            scores[cat] = score

    if not scores:
        return {
            "status": "ok",
            "predicted_category": "Other",
            "confidence": 30.0,
            "alternatives": [],
            "model": "Keyword matching",
        }

    # Sort by score
    sorted_cats = sorted(scores.items(), key=lambda x: -x[1])
    total = sum(scores.values())
    top_cat, top_score = sorted_cats[0]
    confidence = round((top_score / total) * 100, 1)

    return {
        "status": "ok",
        "predicted_category": top_cat,
        "confidence": confidence,
        "alternatives": [
            {"category": c, "probability": round((s / total) * 100, 1)}
            for c, s in sorted_cats[1:4]
        ],
        "model": "Keyword matching",
    }


# ---------- 5. Budget recommendations ----------
@app.get("/api/ml/budget-recommendation")
def ml_budget_recommendation(user: User = Depends(get_current_user),
                              db: Session = Depends(get_db)):
    df = _load_expenses_df(db, user.id, days=180)
    if len(df) < 20:
        return {"status": "insufficient_data",
                "message": "At least 20 expenses needed",
                "recommendations": []}

    df["month"] = df["date"].dt.to_period("M")
    monthly_cat = df.groupby(["month", "category"])["amount"].sum().reset_index()

    recommendations = []
    for cat in monthly_cat["category"].unique():
        cat_data = monthly_cat[monthly_cat["category"] == cat]["amount"]
        if len(cat_data) < 2:
            continue
        avg = float(cat_data.mean())
        std = float(cat_data.std()) if len(cat_data) > 1 else 0
        recommendations.append({
            "category": cat,
            "avg_monthly": round(avg, 2),
            "std_dev": round(std, 2),
            "recommended_budget": round(avg * 1.1, 2),
            "min_seen": round(float(cat_data.min()), 2),
            "max_seen": round(float(cat_data.max()), 2),
            "months_of_data": len(cat_data),
        })
    recommendations.sort(key=lambda r: -r["recommended_budget"])
    return {"status": "ok", "recommendations": recommendations,
            "method": "Mean + 10% buffer"}


# ---------- 6. Overview ----------
@app.get("/api/ml/overview")
def ml_overview(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    df = _load_expenses_df(db, user.id, days=180)
    if len(df) < 20:
        return {"status": "insufficient_data",
                "message": "Add at least 20 expenses to unlock ML insights",
                "total_records": len(df)}

    total = float(df["amount"].sum())
    avg = float(df["amount"].mean())
    median = float(df["amount"].median())
    std = float(df["amount"].std())

    weekday_avg = df.groupby("weekday")["amount"].mean().to_dict()
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    weekday_data = [{"day": day_names[d], "avg": round(float(weekday_avg.get(d, 0)), 2)}
                    for d in range(7)]

    cat_totals = df.groupby("category")["amount"].sum().sort_values(ascending=False).head(5)
    top_cats = [{"name": k, "total": round(float(v), 2)} for k, v in cat_totals.items()]

    return {
        "status": "ok",
        "total_records": len(df),
        "total_amount": round(total, 2),
        "avg_expense": round(avg, 2),
        "median_expense": round(median, 2),
        "std_dev": round(std, 2),
        "weekday_pattern": weekday_data,
        "top_categories": top_cats,
        "date_range": {
            "from": df["date"].min().strftime("%Y-%m-%d"),
            "to": df["date"].max().strftime("%Y-%m-%d"),
        },
    }
# ============ HTML FRONTEND ============
INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>BudgetBuddy — ₹</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  :root{
    --bg1:#0b0f14;--bg2:#0f172a;
    --panel:#111827;--panel-2:#1f2937;--border:#1f2937;
    --text:#e5e7eb;--muted:#9ca3af;
    --accent:#10b981;--accent-2:#6366f1;
    --danger:#ef4444;--warn:#f59e0b;--success:#10b981;
  }
  body.theme-light{--bg1:#f8fafc;--bg2:#eef2ff;--panel:#ffffff;--panel-2:#f1f5f9;--border:#e2e8f0;--text:#0f172a;--muted:#64748b;--accent:#059669;--accent-2:#4f46e5}
  body.theme-ocean{--bg1:#0c1b2a;--bg2:#0e2f44;--panel:#12314a;--panel-2:#1c425e;--border:#1c425e;--text:#e0f2fe;--muted:#7dd3fc;--accent:#22d3ee;--accent-2:#0891b2}
  body.theme-sunset{--bg1:#1a0b1f;--bg2:#2b1024;--panel:#2a1428;--panel-2:#3d1d3a;--border:#3d1d3a;--text:#fce7f3;--muted:#f9a8d4;--accent:#fb923c;--accent-2:#e11d48}
  body.theme-forest{--bg1:#0a1612;--bg2:#0f1f1a;--panel:#122a22;--panel-2:#1a3d31;--border:#1a3d31;--text:#d1fae5;--muted:#6ee7b7;--accent:#34d399;--accent-2:#059669}
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
       background:linear-gradient(135deg,var(--bg1),var(--bg2));
       background-attachment:fixed;color:var(--text);min-height:100vh;transition:.3s}
  .hidden{display:none!important}
  .container{max-width:1280px;margin:0 auto;padding:24px}
  .card{background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:20px}
  .grid{display:grid;gap:16px}
  .g-4{grid-template-columns:repeat(4,1fr)}
  .g-3{grid-template-columns:repeat(3,1fr)}
  .g-2{grid-template-columns:repeat(2,1fr)}
  @media(max-width:1000px){.g-4{grid-template-columns:repeat(2,1fr)}.g-3{grid-template-columns:1fr}}
  @media(max-width:640px){.g-4,.g-2,.g-3{grid-template-columns:1fr}}
  h2{font-size:1.15rem;margin-bottom:14px;font-weight:600}
  h3{font-size:.78rem;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;font-weight:600}
  .kpi{font-size:1.7rem;font-weight:800;margin-top:6px}
  .kpi-sub{font-size:.8rem;color:var(--muted);margin-top:4px}
  input,select,button,textarea{font-family:inherit;font-size:.95rem}
  input,select,textarea{width:100%;padding:10px 12px;border-radius:8px;
    border:1px solid var(--border);background:var(--panel-2);color:var(--text);outline:none}
  input:focus,select:focus,textarea:focus{border-color:var(--accent-2)}
  button{padding:10px 16px;border-radius:8px;border:none;cursor:pointer;
         background:var(--accent-2);color:#fff;font-weight:600;transition:.15s}
  button:hover{filter:brightness(1.1)}
  button.ghost{background:var(--panel-2);color:var(--text)}
  button.danger{background:var(--danger)}
  button.success{background:var(--accent)}
  button.sm{padding:6px 10px;font-size:.8rem}
  label{display:block;font-size:.78rem;color:var(--muted);margin-bottom:6px;margin-top:12px;text-transform:uppercase;letter-spacing:.05em}
  .row{display:flex;gap:10px;align-items:center}
  table{width:100%;border-collapse:collapse;font-size:.9rem}
  th,td{padding:10px 8px;text-align:left;border-bottom:1px solid var(--border)}
  th{color:var(--muted);font-weight:600;font-size:.72rem;text-transform:uppercase}
  tr:hover td{background:rgba(255,255,255,.02)}
  .pill{display:inline-block;padding:3px 10px;border-radius:20px;font-size:.72rem;font-weight:600}
  .header{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px;flex-wrap:wrap;gap:12px}
  .nav{display:flex;gap:6px;background:var(--panel);padding:6px;border-radius:12px;
       border:1px solid var(--border);flex-wrap:wrap;margin-bottom:24px}
  .nav button{background:transparent;color:var(--muted);padding:8px 16px;font-weight:500}
  .nav button.active{background:var(--panel-2);color:var(--text);font-weight:600}
  .toast{position:fixed;bottom:24px;right:24px;background:var(--panel);
    border:1px solid var(--border);padding:14px 18px;border-radius:10px;
    box-shadow:0 10px 30px rgba(0,0,0,.5);z-index:100;animation:slide .3s}
  @keyframes slide{from{transform:translateY(20px);opacity:0}}
  .toast.error{border-color:var(--danger)}
  .toast.success{border-color:var(--accent)}
  .auth-wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
  .auth-card{width:100%;max-width:420px}
  .progress{height:8px;background:var(--panel-2);border-radius:4px;overflow:hidden;margin-top:8px}
  .progress>div{height:100%;background:var(--accent);transition:width .4s ease}
  .progress.warn>div{background:var(--warn)}
  .progress.danger>div{background:var(--danger)}
  .insight{padding:14px;border-radius:10px;background:var(--panel-2);
    border-left:3px solid var(--accent-2);margin-bottom:10px}
  .insight.warning{border-left-color:var(--warn)}
  .insight.danger{border-left-color:var(--danger)}
  .insight.success{border-left-color:var(--accent)}
  .insight h4{font-size:.95rem;margin-bottom:4px}
  .insight p{font-size:.85rem;color:var(--muted)}
  .brand{display:flex;align-items:center;gap:10px;font-weight:700;font-size:1.2rem}
  .dot{width:10px;height:10px;border-radius:50%;background:var(--accent);display:inline-block;box-shadow:0 0 12px var(--accent)}
  .muted{color:var(--muted);font-size:.85rem}
  canvas{max-height:280px}
  .empty{text-align:center;padding:40px 20px;color:var(--muted)}
  .flex-between{display:flex;justify-content:space-between;align-items:center;gap:10px}
  .tab-content{display:none}
  .tab-content.active{display:block}
  .modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.7);display:flex;align-items:center;
    justify-content:center;z-index:200;padding:20px}
  .modal{background:var(--panel);border-radius:16px;padding:24px;max-width:520px;width:100%;
    max-height:90vh;overflow-y:auto;border:1px solid var(--border)}
  .theme-dot{width:28px;height:28px;border-radius:50%;cursor:pointer;border:2px solid transparent;transition:.2s}
  .theme-dot:hover{transform:scale(1.15)}
  .theme-dot.active{border-color:var(--text);border-width:3px}
  .acc-card{border-radius:16px;padding:20px;color:#fff;min-height:150px;
    display:flex;flex-direction:column;justify-content:space-between;
    position:relative;overflow:hidden;cursor:pointer;transition:.25s}
  .acc-card:hover{transform:translateY(-3px);box-shadow:0 12px 30px rgba(0,0,0,.3)}
  .acc-card::after{content:'';position:absolute;top:-40px;right:-40px;width:120px;height:120px;
    border-radius:50%;background:rgba(255,255,255,.12)}
  .acc-type{font-size:.7rem;letter-spacing:1.5px;font-weight:700;opacity:.85}
  .acc-name{font-size:1rem;font-weight:600;margin-top:12px}
  .acc-bal{font-size:1.6rem;font-weight:800;margin-top:4px}
  .acc-hint{font-size:.65rem;opacity:.55;margin-top:8px}
  .emi-card{padding:16px;border-radius:12px;background:var(--panel);border:1px solid var(--border);margin-bottom:12px}
  .emi-head{display:flex;justify-content:space-between;align-items:flex-start;gap:10px}
  .emi-name{font-weight:700;font-size:1rem}
  .emi-sub{font-size:.78rem;color:var(--muted);margin-top:2px}
  .emi-amt{font-weight:800;font-size:1.15rem}
  .hero{background:linear-gradient(135deg,var(--accent-2),var(--accent));
    border-radius:20px;padding:28px;color:#fff;position:relative;overflow:hidden}
  .hero::before{content:'';position:absolute;top:-60px;right:-60px;width:200px;height:200px;
    border-radius:50%;background:rgba(255,255,255,.15)}
  .hero-label{font-size:.72rem;letter-spacing:2px;opacity:.85;font-weight:700}
  .hero-amt{font-size:2.2rem;font-weight:900;margin-top:8px}
  .hero-sub{font-size:.9rem;opacity:.85;margin-top:4px}
  .hero-row{display:flex;gap:24px;margin-top:20px;flex-wrap:wrap;position:relative;z-index:1}
  .hero-row>div>div:first-child{font-size:.72rem;opacity:.75;text-transform:uppercase;letter-spacing:1px}
  .hero-row>div>div:last-child{font-size:1.05rem;font-weight:700;margin-top:2px}
</style>
</head>
<body class="theme-dark">

<!-- AUTH -->
<div id="authView" class="auth-wrap">
  <div class="card auth-card">
    <div class="brand" style="justify-content:center;margin-bottom:24px">
      <span class="dot"></span> BudgetBuddy <span style="color:var(--accent)">₹</span>
    </div>
    <div class="nav" style="width:100%">
      <button id="tabLogin" class="active" style="flex:1">Sign in</button>
      <button id="tabRegister" style="flex:1">Create account</button>
    </div>
    <form id="loginForm">
      <label>Email</label>
      <input type="email" id="loginEmail" required value="demo@budgetbuddy.app"/>
      <label>Password</label>
      <input type="password" id="loginPassword" required value="demo1234"/>
      <button type="submit" style="width:100%;margin-top:20px">Sign in</button>
      <p class="muted" style="text-align:center;margin-top:14px">Demo: demo@budgetbuddy.app / demo1234</p>
    </form>
    <form id="registerForm" class="hidden">
      <label>Full name</label>
      <input type="text" id="regName" required/>
      <label>Email</label>
      <input type="email" id="regEmail" required/>
      <label>Password (min 6 chars)</label>
      <input type="password" id="regPassword" minlength="6" required/>
      <button type="submit" style="width:100%;margin-top:20px">Create account</button>
    </form>
  </div>
</div>

<!-- APP -->
<div id="appView" class="hidden">
  <div class="container">
    <div class="header">
      <div class="brand"><span class="dot"></span> BudgetBuddy <span style="color:var(--accent)">₹</span></div>
      <div class="row">
        <div class="row" id="themeSwitcher" style="gap:6px;margin-right:8px"></div>
        <span class="muted" id="userEmail"></span>
        <button class="ghost sm" onclick="logout()">Sign out</button>
      </div>
    </div>

    <div class="nav">
      <button class="tab-btn active" data-tab="dashboard">Dashboard</button>
      <button class="tab-btn" data-tab="accounts">Accounts</button>
      <button class="tab-btn" data-tab="plan">Plan (EMI + Income)</button>
      <button class="tab-btn" data-tab="expenses">Expenses</button>
      <button class="tab-btn" data-tab="budgets">Budgets</button>
      <button class="tab-btn" data-tab="insights">Insights</button>
    </div>

    <!-- DASHBOARD -->
    <div id="tab-dashboard" class="tab-content active">
      <div class="hero" style="margin-bottom:20px">
        <div class="hero-label">THIS MONTH · CASH FLOW</div>
        <div class="hero-amt" id="heroNet">₹0.00</div>
        <div class="hero-sub" id="heroNetSub">Loading...</div>
        <div class="hero-row">
          <div><div>Income</div><div id="heroIncome">₹0</div></div>
          <div><div>EMI</div><div id="heroEmi">₹0</div></div>
          <div><div>Spent</div><div id="heroSpent">₹0</div></div>
          <div><div>Balance</div><div id="heroBal">₹0</div></div>
        </div>
      </div>
      <div class="grid g-4" style="margin-bottom:16px">
        <div class="card"><h3>This month</h3><div class="kpi" id="kpiMonth">₹0</div>
          <div class="kpi-sub" id="kpiChange">—</div></div>
        <div class="card"><h3>Projected</h3><div class="kpi" id="kpiProjected">₹0</div>
          <div class="kpi-sub">Based on current pace</div></div>
        <div class="card"><h3>Daily avg</h3><div class="kpi" id="kpiAvg">₹0</div>
          <div class="kpi-sub">This month</div></div>
        <div class="card"><h3>Top category</h3><div class="kpi" id="kpiTop">—</div>
          <div class="kpi-sub" id="kpiTopAmt">—</div></div>
      </div>
      <div class="grid g-2">
        <div class="card"><h2>Last 30 days</h2><canvas id="trendChart"></canvas></div>
        <div class="card"><h2>By category</h2><canvas id="catChart"></canvas></div>
      </div>
    </div>

    <!-- ACCOUNTS -->
    <div id="tab-accounts" class="tab-content">
      <div class="card" style="margin-bottom:16px">
        <div class="flex-between">
          <h2 style="margin:0">Your accounts</h2>
          <button onclick="openAccountModal()">+ Add account</button>
        </div>
        <p class="muted" style="margin-top:8px">Payment karne par balance automatically kam hoga. Click karke edit karo.</p>
      </div>
      <div id="accountsGrid" class="grid g-3"></div>
    </div>

    <!-- PLAN -->
    <div id="tab-plan" class="tab-content">
      <div class="nav" style="margin-bottom:16px">
        <button class="plan-tab active" data-plan="emi">EMIs</button>
        <button class="plan-tab" data-plan="income">Income</button>
      </div>

      <div id="plan-emi">
        <div class="card" style="margin-bottom:16px">
          <div class="flex-between">
            <h2 style="margin:0">Monthly EMIs</h2>
            <button onclick="openEMIModal()">+ Add EMI</button>
          </div>
          <p class="muted" style="margin-top:8px">"Pay this month" dabao — EMI amount account se cut hoga.</p>
        </div>
        <div id="emiList"></div>
      </div>

      <div id="plan-income" class="hidden">
        <div class="card" style="margin-bottom:16px">
          <div class="flex-between">
            <h2 style="margin:0">Income sources</h2>
            <button class="success" onclick="openIncomeModal()">+ Add income</button>
          </div>
          <p class="muted" style="margin-top:8px">"Mark received" dabao — amount account me add hoga.</p>
        </div>
        <div id="incomeList"></div>
      </div>
    </div>

    <!-- EXPENSES -->
    <div id="tab-expenses" class="tab-content">
      <div class="card" style="margin-bottom:16px">
        <h2>Add expense</h2>
        <form id="expenseForm" class="grid g-4">
          <div><label>Amount (₹)</label><input type="number" step="0.01" id="expAmount" required min="0.01"/></div>
          <div><label>Date</label><input type="date" id="expDate" required/></div>
          <div><label>Category</label><select id="expCategory"></select></div>
          <div><label>Account</label><select id="expAccount"></select></div>
          <div><label>Payment method</label>
            <select id="expPayment">
              <option value="upi">UPI</option>
              <option value="card">Card</option>
              <option value="cash">Cash</option>
              <option value="netbanking">Net Banking</option>
              <option value="wallet">Wallet</option>
            </select></div>
          <div style="grid-column:span 3"><label>Description</label>
            <input type="text" id="expDesc" placeholder="e.g. Lunch at cafe"/></div>
          <div style="grid-column:1/-1"><button type="submit">Add expense</button></div>
        </form>
      </div>
      <div class="card">
        <div class="flex-between" style="margin-bottom:14px">
          <h2 style="margin:0">Recent expenses</h2>
          <div class="row">
            <button class="ghost sm" onclick="exportCSV()">Export CSV</button>
            <button class="ghost sm" onclick="exportPDF()">📄 PDF Statement</button>
          </div>
        </div>
        <table>
          <thead><tr><th>Date</th><th>Description</th><th>Category</th><th>Account</th>
            <th style="text-align:right">Amount</th><th></th></tr></thead>
          <tbody id="expenseList"></tbody>
        </table>
        <div id="expenseEmpty" class="empty hidden">No expenses yet.</div>
      </div>
    </div>

    <!-- BUDGETS -->
    <div id="tab-budgets" class="tab-content">
      <div class="card" style="margin-bottom:16px">
        <h2>Create budget</h2>
        <form id="budgetForm" class="grid g-3">
          <div><label>Category</label><select id="budCategory"><option value="">Overall</option></select></div>
          <div><label>Monthly amount (₹)</label><input type="number" step="0.01" id="budAmount" required min="0.01"/></div>
          <div><label>&nbsp;</label><button type="submit" style="width:100%">Create budget</button></div>
        </form>
      </div>
      <div class="card">
        <h2>Budgets</h2>
        <div id="budgetList" class="grid g-2"></div>
        <div id="budgetEmpty" class="empty hidden">No budgets yet.</div>
      </div>
    </div>

    <!-- INSIGHTS -->
    <div id="tab-insights" class="tab-content">
      <div class="card">
        <div class="flex-between" style="margin-bottom:14px">
          <h2 style="margin:0">Smart insights</h2>
          <button class="ghost sm" onclick="exportPDF()">📄 Download Statement</button>
        </div>
        <div id="insightsList"></div>
      </div>
    </div>
  </div>
</div>

<!-- ACCOUNT MODAL -->
<div id="accountModal" class="modal-bg hidden">
  <div class="modal">
    <h2 id="accModalTitle">Add account</h2>
    <label>Name</label>
    <input type="text" id="accName" placeholder="e.g. SBI Savings"/>
    <label>Type</label>
    <select id="accType">
      <option value="bank">Bank</option>
      <option value="cash">Cash</option>
      <option value="wallet">Wallet</option>
      <option value="credit">Credit Card</option>
    </select>
    <label>Balance (₹)</label>
    <input type="number" step="0.01" id="accBalance" value="0"/>
    <label>Color</label>
    <div class="row" id="accColors" style="flex-wrap:wrap;margin-top:6px"></div>
    <div class="row" style="margin-top:20px;justify-content:flex-end">
      <button class="ghost" onclick="closeModal('accountModal')">Cancel</button>
      <button id="accSaveBtn">Create</button>
    </div>
  </div>
</div>

<!-- EMI MODAL -->
<div id="emiModal" class="modal-bg hidden">
  <div class="modal">
    <h2>Add EMI</h2>
    <label>Name</label>
    <input type="text" id="emiName" placeholder="Home Loan"/>
    <label>Monthly amount (₹)</label>
    <input type="number" step="0.01" id="emiAmount" placeholder="18500"/>
    <label>Due day (1-28)</label>
    <input type="number" id="emiDueDay" value="5" min="1" max="28"/>
    <label>Total months (optional)</label>
    <input type="number" id="emiTotalMonths" placeholder="240"/>
    <label>Account (deduct from)</label>
    <select id="emiAccount"></select>
    <div class="row" style="margin-top:20px;justify-content:flex-end">
      <button class="ghost" onclick="closeModal('emiModal')">Cancel</button>
      <button onclick="saveEMI()">Add EMI</button>
    </div>
  </div>
</div>

<!-- INCOME MODAL -->
<div id="incomeModal" class="modal-bg hidden">
  <div class="modal">
    <h2>Add income source</h2>
    <label>Name</label>
    <input type="text" id="incName" placeholder="Monthly Salary"/>
    <label>Amount (₹)</label>
    <input type="number" step="0.01" id="incAmount" placeholder="85000"/>
    <label>Pay day (1-28)</label>
    <input type="number" id="incPayDay" value="1" min="1" max="28"/>
    <label>To account</label>
    <select id="incAccount"></select>
    <div class="row" style="margin-top:20px;justify-content:flex-end">
      <button class="ghost" onclick="closeModal('incomeModal')">Cancel</button>
      <button class="success" onclick="saveIncome()">Add</button>
    </div>
  </div>
</div>

<script>
let token = localStorage.getItem("bb_token");
let categories = [], accounts = [], editingAccountId = null;
let trendChart, catChart;

function fmt(n){ return "₹" + Number(n||0).toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2}); }
function toast(msg, type="success"){
  const el = document.createElement("div");
  el.className = "toast " + type; el.textContent = msg;
  document.body.appendChild(el); setTimeout(()=>el.remove(), 2600);
}
async function api(path, opts={}){
  const headers = {"Content-Type":"application/json", ...(opts.headers||{})};
  if (token) headers["Authorization"] = "Bearer " + token;
  const res = await fetch(path, {...opts, headers});
  if (!res.ok){
    let err; try { err = await res.json(); } catch { err = {detail:"Request failed"}; }
    throw new Error(err.detail || "Request failed");
  }
  const ct = res.headers.get("content-type")||"";
  return ct.includes("application/json") ? res.json() : res;
}
function escapeHtml(s){ return (s||"").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
function closeModal(id){ document.getElementById(id).classList.add("hidden"); }
function openModal(id){ document.getElementById(id).classList.remove("hidden"); }

const THEMES = [
  { name: "dark", color: "#0b0f14", title: "Midnight" },
  { name: "light", color: "#f8fafc", title: "Daylight" },
  { name: "ocean", color: "#0c1b2a", title: "Ocean" },
  { name: "sunset", color: "#1a0b1f", title: "Sunset" },
  { name: "forest", color: "#0a1612", title: "Forest" },
];
function applyTheme(name){
  document.body.className = "theme-" + name;
  localStorage.setItem("bb_theme", name);
  document.querySelectorAll("#themeSwitcher .theme-dot").forEach(d => {
    d.classList.toggle("active", d.dataset.theme === name);
  });
}
function renderThemeSwitcher(){
  const sw = document.getElementById("themeSwitcher"); sw.innerHTML = "";
  const cur = localStorage.getItem("bb_theme") || "dark";
  THEMES.forEach(t => {
    const d = document.createElement("div");
    d.className = "theme-dot"; d.style.background = t.color;
    d.title = t.title; d.dataset.theme = t.name;
    if (t.name === cur) d.classList.add("active");
    d.onclick = () => applyTheme(t.name);
    sw.appendChild(d);
  });
}
applyTheme(localStorage.getItem("bb_theme") || "dark");

document.getElementById("tabLogin").onclick = () => {
  document.getElementById("tabLogin").classList.add("active");
  document.getElementById("tabRegister").classList.remove("active");
  document.getElementById("loginForm").classList.remove("hidden");
  document.getElementById("registerForm").classList.add("hidden");
};
document.getElementById("tabRegister").onclick = () => {
  document.getElementById("tabRegister").classList.add("active");
  document.getElementById("tabLogin").classList.remove("active");
  document.getElementById("registerForm").classList.remove("hidden");
  document.getElementById("loginForm").classList.add("hidden");
};
document.getElementById("loginForm").onsubmit = async (e) => {
  e.preventDefault();
  try {
    const body = new URLSearchParams();
    body.append("username", document.getElementById("loginEmail").value);
    body.append("password", document.getElementById("loginPassword").value);
    const res = await fetch("/api/auth/login", {method:"POST",
      headers:{"Content-Type":"application/x-www-form-urlencoded"}, body});
    if (!res.ok) throw new Error("Invalid email or password");
    const data = await res.json();
    token = data.access_token;
    localStorage.setItem("bb_token", token);
    await enterApp();
  } catch(err){ toast(err.message, "error"); }
};
document.getElementById("registerForm").onsubmit = async (e) => {
  e.preventDefault();
  try {
    const data = await api("/api/auth/register", {method:"POST", body: JSON.stringify({
      email: document.getElementById("regEmail").value,
      password: document.getElementById("regPassword").value,
      full_name: document.getElementById("regName").value,
    })});
    token = data.access_token; localStorage.setItem("bb_token", token);
    toast("Account created!"); await enterApp();
  } catch(err){ toast(err.message, "error"); }
};
function logout(){
  token = null; localStorage.removeItem("bb_token");
  document.getElementById("appView").classList.add("hidden");
  document.getElementById("authView").classList.remove("hidden");
}
async function enterApp(){
  try {
    const me = await api("/api/auth/me");
    document.getElementById("userEmail").textContent = me.email;
    document.getElementById("authView").classList.add("hidden");
    document.getElementById("appView").classList.remove("hidden");
    document.getElementById("expDate").value = new Date().toISOString().slice(0,10);
    renderThemeSwitcher();
    await loadCategories();
    await loadAccounts();
    await refreshAll();
  } catch(err){
    token = null; localStorage.removeItem("bb_token");
    document.getElementById("appView").classList.add("hidden");
    document.getElementById("authView").classList.remove("hidden");
  }
}

document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.onclick = () => {
    document.querySelectorAll(".tab-btn").forEach(b=>b.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach(c=>c.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
    const t = btn.dataset.tab;
    if (t === "dashboard") loadDashboard();
    if (t === "accounts") loadAccounts();
    if (t === "plan") loadPlan();
    if (t === "expenses") loadExpenses();
    if (t === "budgets") loadBudgets();
    if (t === "insights") loadInsights();
  };
});
document.querySelectorAll(".plan-tab").forEach(btn => {
  btn.onclick = () => {
    document.querySelectorAll(".plan-tab").forEach(b=>b.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("plan-emi").classList.toggle("hidden", btn.dataset.plan !== "emi");
    document.getElementById("plan-income").classList.toggle("hidden", btn.dataset.plan !== "income");
  };
});

async function loadCategories(){
  categories = await api("/api/categories");
  const eSel = document.getElementById("expCategory");
  const bSel = document.getElementById("budCategory");
  eSel.innerHTML = '<option value="">Uncategorized</option>';
  bSel.innerHTML = '<option value="">Overall</option>';
  categories.forEach(c => {
    eSel.innerHTML += `<option value="${c.id}">${c.name}</option>`;
    bSel.innerHTML += `<option value="${c.id}">${c.name}</option>`;
  });
}
async function loadAccounts(){
  accounts = await api("/api/accounts");
  const eSel = document.getElementById("expAccount");
  const emiSel = document.getElementById("emiAccount");
  const incSel = document.getElementById("incAccount");
  eSel.innerHTML = '<option value="">— Select —</option>';
  emiSel.innerHTML = ''; incSel.innerHTML = '';
  accounts.forEach(a => {
    eSel.innerHTML += `<option value="${a.id}">${a.name} (${fmt(a.balance)})</option>`;
    emiSel.innerHTML += `<option value="${a.id}">${a.name}</option>`;
    incSel.innerHTML += `<option value="${a.id}">${a.name}</option>`;
  });
  const grid = document.getElementById("accountsGrid");
  grid.innerHTML = "";
  if (!accounts.length){
    grid.innerHTML = '<div class="card empty" style="grid-column:1/-1">No accounts yet. Add one above.</div>';
    return;
  }
  accounts.forEach(a => {
    const div = document.createElement("div");
    div.className = "acc-card";
    div.style.background = `linear-gradient(135deg, ${a.color}, ${shade(a.color, -40)})`;
    div.innerHTML = `
      <div class="flex-between"><span class="acc-type">${a.type.toUpperCase()}</span>
        <span>••••</span></div>
      <div>
        <div class="acc-name">${escapeHtml(a.name)}</div>
        <div class="acc-bal">${fmt(a.balance)}</div>
        <div class="acc-hint">Click to edit</div>
      </div>`;
    div.onclick = () => openAccountModal(a);
    grid.appendChild(div);
  });
}
function shade(hex, p){
  const n = parseInt(hex.replace("#", ""), 16);
  let r = (n >> 16) + p, g = ((n >> 8) & 0xff) + p, b = (n & 0xff) + p;
  r = Math.max(0, Math.min(255, r)); g = Math.max(0, Math.min(255, g)); b = Math.max(0, Math.min(255, b));
  return "#" + ((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1);
}

const ACC_COLORS = ["#3b82f6","#8b5cf6","#10b981","#f59e0b","#ef4444","#06b6d4","#ec4899","#84cc16"];
let selectedColor = ACC_COLORS[0];
function renderAccColors(){
  const box = document.getElementById("accColors"); box.innerHTML = "";
  ACC_COLORS.forEach(c => {
    const d = document.createElement("div");
    d.style.cssText = `width:32px;height:32px;border-radius:16px;cursor:pointer;background:${c};${selectedColor===c?'border:3px solid var(--text)':''}`;
    d.onclick = () => { selectedColor = c; renderAccColors(); };
    box.appendChild(d);
  });
}
function openAccountModal(acc){
  editingAccountId = acc ? acc.id : null;
  document.getElementById("accModalTitle").textContent = acc ? "Edit account" : "Add account";
  document.getElementById("accSaveBtn").textContent = acc ? "Update" : "Create";
  document.getElementById("accName").value = acc ? acc.name : "";
  document.getElementById("accType").value = acc ? acc.type : "bank";
  document.getElementById("accBalance").value = acc ? acc.balance : 0;
  selectedColor = acc ? acc.color : ACC_COLORS[0];
  renderAccColors();
  openModal("accountModal");
}
document.getElementById("accSaveBtn").onclick = async () => {
  const name = document.getElementById("accName").value.trim();
  const type = document.getElementById("accType").value;
  const balance = parseFloat(document.getElementById("accBalance").value) || 0;
  if (!name) return toast("Name required", "error");
  try {
    const d = { name, type, balance, color: selectedColor, icon: "card" };
    if (editingAccountId) await api("/api/accounts/" + editingAccountId, {method:"PATCH", body: JSON.stringify(d)});
    else await api("/api/accounts", {method:"POST", body: JSON.stringify(d)});
    toast(editingAccountId ? "Updated" : "Account created");
    closeModal("accountModal");
    await loadAccounts();
    await refreshAll();
  } catch(e){ toast(e.message, "error"); }
};

async function loadDashboard(){
  try {
    const [flow, s, trend, cats] = await Promise.all([
      api("/api/cashflow"),
      api("/api/analytics/summary"),
      api("/api/analytics/trend?days=30"),
      api("/api/analytics/categories?days=30"),
    ]);
    document.getElementById("heroNet").textContent = fmt(flow.net);
    document.getElementById("heroNetSub").textContent = flow.net >= 0 ? "You'll save this month" : "You'll be short this month";
    document.getElementById("heroIncome").textContent = fmt(flow.total_income);
    document.getElementById("heroEmi").textContent = fmt(flow.total_emi);
    document.getElementById("heroSpent").textContent = fmt(flow.total_expenses);
    document.getElementById("heroBal").textContent = fmt(flow.accounts_total);

    document.getElementById("kpiMonth").textContent = fmt(s.this_month);
    const changeEl = document.getElementById("kpiChange");
    if (s.prev_month > 0){
      const arrow = s.change_pct >= 0 ? "▲" : "▼";
      const color = s.change_pct >= 0 ? "var(--danger)" : "var(--accent)";
      changeEl.innerHTML = `<span style="color:${color}">${arrow} ${Math.abs(s.change_pct)}%</span> vs last month`;
    } else changeEl.textContent = "No prior data";
    document.getElementById("kpiProjected").textContent = fmt(s.projected_month);
    document.getElementById("kpiAvg").textContent = fmt(s.daily_avg);
    document.getElementById("kpiTop").textContent = s.top_category ? s.top_category.name : "—";
    document.getElementById("kpiTopAmt").textContent = s.top_category ? fmt(s.top_category.total) : "No data";

    renderTrend(trend); renderCat(cats);
  } catch(e){ console.log(e); }
}
function renderTrend(data){
  const ctx = document.getElementById("trendChart");
  if (trendChart) trendChart.destroy();
  trendChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: data.map(d => d.date.slice(5)),
      datasets: [{ label: "Spent", data: data.map(d => d.total),
        borderColor: "#10b981", backgroundColor: "rgba(16,185,129,.15)",
        fill: true, tension: .35, pointRadius: 0, borderWidth: 2 }]
    },
    options: { plugins:{legend:{display:false}, tooltip:{callbacks:{label:(c)=>"₹"+c.parsed.y.toLocaleString('en-IN')}}},
      scales:{ x:{ticks:{color:"#9ca3af",maxTicksLimit:8},grid:{color:"rgba(128,128,128,.1)"}},
               y:{ticks:{color:"#9ca3af",callback:(v)=>"₹"+v.toLocaleString('en-IN')},grid:{color:"rgba(128,128,128,.1)"},beginAtZero:true} } }
  });
}
function renderCat(data){
  const ctx = document.getElementById("catChart");
  if (catChart) catChart.destroy();
  if (!data.length){
    catChart = new Chart(ctx, {type:"doughnut",
      data:{labels:["No data"],datasets:[{data:[1],backgroundColor:["#1f2937"]}]},
      options:{plugins:{legend:{labels:{color:"#9ca3af"}}}}});
    return;
  }
  catChart = new Chart(ctx, {
    type: "doughnut",
    data: { labels: data.map(d => d.name),
      datasets: [{ data: data.map(d => d.total), backgroundColor: data.map(d => d.color), borderWidth: 0 }] },
    options: { plugins:{ legend:{position:"bottom",labels:{color:"#9ca3af",padding:12}},
      tooltip:{callbacks:{label:(c)=>c.label+": ₹"+c.parsed.toLocaleString('en-IN')}} } }
  });
}

document.getElementById("expenseForm").onsubmit = async (e) => {
  e.preventDefault();
  try {
    const catId = document.getElementById("expCategory").value;
    const accId = document.getElementById("expAccount").value;
    await api("/api/expenses", {method:"POST", body: JSON.stringify({
      amount: parseFloat(document.getElementById("expAmount").value),
      date: document.getElementById("expDate").value,
      category_id: catId ? parseInt(catId) : null,
      account_id: accId ? parseInt(accId) : null,
      description: document.getElementById("expDesc").value,
      payment_method: document.getElementById("expPayment").value,
    })});
    document.getElementById("expAmount").value = "";
    document.getElementById("expDesc").value = "";
    toast("Expense added");
    await loadExpenses();
    await loadAccounts();
    await loadDashboard();
  } catch(err){ toast(err.message, "error"); }
};
async function loadExpenses(){
  const rows = await api("/api/expenses?limit=200");
  const tbody = document.getElementById("expenseList");
  const empty = document.getElementById("expenseEmpty");
  tbody.innerHTML = "";
  if (!rows.length){ empty.classList.remove("hidden"); return; }
  empty.classList.add("hidden");
  rows.forEach(e => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${e.date}</td>
      <td>${escapeHtml(e.description || "—")}</td>
      <td>${e.category_name ? `<span class="pill" style="background:${e.category_color}22;color:${e.category_color}">${e.category_name}</span>` : '<span class="muted">—</span>'}</td>
      <td>${e.account_name ? `<span class="muted">${escapeHtml(e.account_name)}</span>` : '—'}</td>
      <td style="text-align:right;font-weight:600">${fmt(e.amount)}</td>
      <td style="text-align:right"><button class="danger sm" onclick="deleteExpense(${e.id})">×</button></td>`;
    tbody.appendChild(tr);
  });
}
async function deleteExpense(id){
  if (!confirm("Delete this expense?")) return;
  await api("/api/expenses/" + id, {method:"DELETE"});
  toast("Deleted"); await loadExpenses(); await loadAccounts(); await loadDashboard();
}
function exportCSV(){
  fetch("/api/expenses/export/csv", {headers:{Authorization:"Bearer "+token}})
    .then(r => r.blob()).then(b => {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(b); a.download = "expenses.csv"; a.click();
    });
}
function exportPDF(){
  const d = new Date();
  const month = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`;
  fetch(`/api/reports/pdf?month=${month}`, {
    headers:{Authorization: "Bearer " + token}
  })
  .then(async r => {
    if (!r.ok) throw new Error("PDF generation failed");
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `budgetbuddy-${month}.pdf`;
    a.click();
    URL.revokeObjectURL(a.href);
    toast("PDF downloaded");
  })
  .catch(e => toast(e.message, "error"));
}

async function loadPlan(){ await Promise.all([loadEMIs(), loadIncomes()]); }
async function loadEMIs(){
  const rows = await api("/api/emis");
  const box = document.getElementById("emiList");
  box.innerHTML = "";
  if (!rows.length){ box.innerHTML = '<div class="card empty">No EMIs yet.</div>'; return; }
  rows.forEach(e => {
    const div = document.createElement("div");
    div.className = "emi-card";
    const progressHtml = e.total_months ? `
      <div class="progress"><div style="width:${e.progress_pct}%;background:${e.color}"></div></div>
      <div class="muted" style="margin-top:6px">${e.paid_months}/${e.total_months} paid (${e.progress_pct}%) · ${e.remaining_months} left</div>
    ` : "";
    div.innerHTML = `
      <div class="emi-head">
        <div style="flex:1">
          <div class="emi-name">${escapeHtml(e.name)}</div>
          <div class="emi-sub">Due ${e.due_day}th · from ${escapeHtml(e.account_name)}</div>
        </div>
        <div class="emi-amt" style="color:${e.color}">${fmt(e.amount)}</div>
      </div>
      ${progressHtml}
      <div class="row" style="margin-top:14px">
        ${e.paid_this_month
          ? '<span class="pill" style="background:#10b98133;color:#10b981">✓ Paid this month</span>'
          : `<button class="success sm" onclick="payEMI(${e.id})">Pay this month</button>`}
        <button class="ghost sm" onclick="deleteEMI(${e.id})">Delete</button>
      </div>`;
    box.appendChild(div);
  });
}
async function payEMI(id){
  if (!confirm("Pay this EMI? Amount account se cut hoga.")) return;
  try { await api("/api/emis/"+id+"/pay", {method:"POST"}); toast("✓ EMI paid!"); await loadEMIs(); await loadAccounts(); await loadDashboard(); }
  catch(e){ toast(e.message, "error"); }
}
async function deleteEMI(id){
  if (!confirm("Delete this EMI?")) return;
  await api("/api/emis/"+id, {method:"DELETE"}); toast("Deleted"); await loadEMIs();
}
async function loadIncomes(){
  const rows = await api("/api/incomes");
  const box = document.getElementById("incomeList");
  box.innerHTML = "";
  if (!rows.length){ box.innerHTML = '<div class="card empty">No income sources yet.</div>'; return; }
  rows.forEach(i => {
    const div = document.createElement("div");
    div.className = "emi-card";
    div.innerHTML = `
      <div class="emi-head">
        <div style="flex:1">
          <div class="emi-name">${escapeHtml(i.name)}</div>
          <div class="emi-sub">Day ${i.pay_day} · to ${escapeHtml(i.account_name)}</div>
        </div>
        <div class="emi-amt" style="color:${i.color}">+${fmt(i.amount)}</div>
      </div>
      <div class="row" style="margin-top:14px">
        ${i.received_this_month
          ? '<span class="pill" style="background:#10b98133;color:#10b981">✓ Received this month</span>'
          : `<button class="success sm" onclick="receiveIncome(${i.id})">Mark received</button>`}
        <button class="ghost sm" onclick="deleteIncome(${i.id})">Delete</button>
      </div>`;
    box.appendChild(div);
  });
}
async function receiveIncome(id){
  if (!confirm("Mark received? Amount account me add hoga.")) return;
  try { await api("/api/incomes/"+id+"/receive", {method:"POST"}); toast("✓ Received!"); await loadIncomes(); await loadAccounts(); await loadDashboard(); }
  catch(e){ toast(e.message, "error"); }
}
async function deleteIncome(id){
  if (!confirm("Delete this income source?")) return;
  await api("/api/incomes/"+id, {method:"DELETE"}); toast("Deleted"); await loadIncomes();
}

function openEMIModal(){ openModal("emiModal"); }
async function saveEMI(){
  const name = document.getElementById("emiName").value.trim();
  const amount = parseFloat(document.getElementById("emiAmount").value);
  const due_day = parseInt(document.getElementById("emiDueDay").value) || 5;
  const total = document.getElementById("emiTotalMonths").value;
  const accId = document.getElementById("emiAccount").value;
  if (!name || !amount || !accId) return toast("Fill all fields", "error");
  try {
    await api("/api/emis", {method:"POST", body: JSON.stringify({
      name, amount, account_id: parseInt(accId), due_day,
      total_months: total ? parseInt(total) : null,
    })});
    toast("EMI added"); closeModal("emiModal");
    document.getElementById("emiName").value = "";
    document.getElementById("emiAmount").value = "";
    document.getElementById("emiTotalMonths").value = "";
    await loadEMIs();
  } catch(e){ toast(e.message, "error"); }
}

function openIncomeModal(){ openModal("incomeModal"); }
async function saveIncome(){
  const name = document.getElementById("incName").value.trim();
  const amount = parseFloat(document.getElementById("incAmount").value);
  const pay_day = parseInt(document.getElementById("incPayDay").value) || 1;
  const accId = document.getElementById("incAccount").value;
  if (!name || !amount || !accId) return toast("Fill all fields", "error");
  try {
    await api("/api/incomes", {method:"POST", body: JSON.stringify({
      name, amount, account_id: parseInt(accId), pay_day,
    })});
    toast("Income added"); closeModal("incomeModal");
    document.getElementById("incName").value = "";
    document.getElementById("incAmount").value = "";
    await loadIncomes();
  } catch(e){ toast(e.message, "error"); }
}

document.getElementById("budgetForm").onsubmit = async (e) => {
  e.preventDefault();
  try {
    const catId = document.getElementById("budCategory").value;
    await api("/api/budgets", {method:"POST", body: JSON.stringify({
      amount: parseFloat(document.getElementById("budAmount").value),
      category_id: catId ? parseInt(catId) : null, period: "monthly",
    })});
    document.getElementById("budAmount").value = "";
    toast("Budget created"); loadBudgets();
  } catch(err){ toast(err.message, "error"); }
};
async function loadBudgets(){
  const rows = await api("/api/budgets");
  const list = document.getElementById("budgetList");
  const empty = document.getElementById("budgetEmpty");
  list.innerHTML = "";
  if (!rows.length){ empty.classList.remove("hidden"); return; }
  empty.classList.add("hidden");
  rows.forEach(b => {
    const cls = b.percent >= 100 ? "danger" : b.percent >= 80 ? "warn" : "";
    const div = document.createElement("div");
    div.className = "card";
    div.innerHTML = `
      <div class="flex-between">
        <h3 style="color:${b.category_color};text-transform:none;font-size:1rem">${escapeHtml(b.category_name)}</h3>
        <button class="danger sm" onclick="deleteBudget(${b.id})">×</button>
      </div>
      <div style="margin-top:8px;font-size:1.3rem;font-weight:700">${fmt(b.spent)} <span class="muted" style="font-size:.85rem;font-weight:400">/ ${fmt(b.amount)}</span></div>
      <div class="progress ${cls}"><div style="width:${Math.min(b.percent,100)}%"></div></div>
      <div class="muted" style="margin-top:6px">${b.percent}% used · ${fmt(b.remaining)} remaining</div>`;
    list.appendChild(div);
  });
}
async function deleteBudget(id){
  if (!confirm("Delete this budget?")) return;
  await api("/api/budgets/" + id, {method:"DELETE"});
  toast("Deleted"); loadBudgets();
}

async function loadInsights(){
  const rows = await api("/api/analytics/insights");
  const list = document.getElementById("insightsList");
  list.innerHTML = "";
  rows.forEach(i => {
    const div = document.createElement("div");
    div.className = "insight " + (i.type || "info");
    div.innerHTML = `<h4>${escapeHtml(i.title)}</h4><p>${escapeHtml(i.message)}</p>`;
    list.appendChild(div);
  });
}

async function refreshAll(){
  await loadDashboard();
  await loadExpenses();
  await loadBudgets();
  await loadPlan();
}
if (token) enterApp();
</script>
</body>
</html>"""

@app.get("/", response_class=HTMLResponse)
def index():
    return INDEX_HTML

# ============ MAIN ============
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
