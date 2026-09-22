import os
import requests
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-secret-key")
PESAPAL_BASE_URL = os.environ.get("PESAPAL_BASE_URL", "https://pay.pesapal.com/v3")
PESAPAL_CONSUMER_KEY = os.environ.get("PESAPAL_CONSUMER_KEY")
PESAPAL_CONSUMER_SECRET = os.environ.get("PESAPAL_CONSUMER_SECRET")
database_url = os.environ.get("DATABASE_URL", "sqlite:///sales_tracker.db")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    business_name = db.Column(db.String(150), nullable=False)
    phone = db.Column(db.String(30), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=True)
    address = db.Column(db.String(250), nullable=True)
    tagline = db.Column(db.String(250), nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="user")
    subscription_expires = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def subscription_active(self):
        return bool(self.subscription_expires and self.subscription_expires > datetime.utcnow())


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    category = db.Column(db.String(100))
    buying_price = db.Column(db.Float, nullable=False, default=0)
    selling_price = db.Column(db.Float, nullable=False, default=0)
    quantity = db.Column(db.Float, nullable=False, default=0)
    low_stock_level = db.Column(db.Float, nullable=False, default=5)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    phone = db.Column(db.String(40))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Sale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=True)
    total = db.Column(db.Float, nullable=False, default=0)
    amount_paid = db.Column(db.Float, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def balance(self):
        return max(0, self.total - self.amount_paid)


class SaleItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sale.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    unit_price = db.Column(db.Float, nullable=False)
    buying_price = db.Column(db.Float, nullable=False)    
    product = db.relationship("Product")

    @property
    def subtotal(self):
        return self.quantity * self.unit_price

    @property
    def profit(self):
        return self.quantity * (self.unit_price - self.buying_price)


class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


def current_user():
    uid = session.get("user_id")
    return db.session.get(User, uid) if uid else None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def subscription_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            return redirect(url_for("login"))
        if not user.subscription_active() and user.role not in ("owner", "admin"):
            flash("Please activate your subscription to use this module.", "error")
            return redirect(url_for("subscription"))
        return view(*args, **kwargs)
    return wrapped


def owner_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user or user.role not in ("owner", "admin"):
            flash("Owner access required.", "error")
            return redirect(url_for("dashboard"))
        return view(*args, **kwargs)
    return wrapped


@app.context_processor
def inject_globals():
    return {"current_user_obj": current_user()}


@app.route("/")
def home():
    return redirect(url_for("dashboard" if session.get("user_id") else "login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        business_name = request.form.get("business_name", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip() or None
        address = request.form.get("address", "").strip() or None
        tagline = request.form.get("tagline", "").strip() or None
        password = request.form.get("password", "")

        if not name or not business_name or not phone or len(password) < 6:
            flash("Please complete all required fields. Password must be at least 6 characters.", "error")
            return redirect(url_for("register"))
        if User.query.filter_by(phone=phone).first():
            flash("That phone number is already registered.", "error")
            return redirect(url_for("register"))
        if email and User.query.filter_by(email=email).first():
            flash("That email is already registered.", "error")
            return redirect(url_for("register"))

        # The first account becomes the owner. Later accounts are normal users.
        first_account = User.query.count() == 0
        user = User(
    name=name,
    business_name=business_name,
    phone=phone,
    email=email,
    address=address,
    tagline=tagline,
    password_hash=generate_password_hash(password),
    role="owner" if first_account else "user",
    subscription_expires=datetime.utcnow() + timedelta(days=3),
)
        db.session.add(user)
        db.session.commit()
        flash("Account created successfully. Please log in.", "success")
        return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter((User.phone == identifier) | (User.email == identifier)).first()
        if not user or not check_password_hash(user.password_hash, password):
            flash("Invalid login details.", "error")
            return redirect(url_for("login"))
        session.clear()
        session["user_id"] = user.id
        session["role"] = user.role
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_sales = db.session.query(func.coalesce(func.sum(Sale.total), 0)).filter(
        Sale.user_id == user.id, Sale.created_at >= today_start
    ).scalar() or 0
    today_items = SaleItem.query.join(Sale).filter(Sale.user_id == user.id, Sale.created_at >= today_start).all()
    today_profit = sum(i.profit for i in today_items)
    expenses_today = db.session.query(func.coalesce(func.sum(Expense.amount), 0)).filter(
        Expense.user_id == user.id, Expense.created_at >= today_start
    ).scalar() or 0
    owing = sum(s.balance for s in Sale.query.filter_by(user_id=user.id).all())
    products = Product.query.filter_by(user_id=user.id).all()
    stock_value = sum(p.quantity * p.buying_price for p in products)
    low_stock = sum(1 for p in products if p.quantity <= p.low_stock_level)
    return render_template("dashboard.html", user=user, today_sales=today_sales,
                           today_profit=today_profit - expenses_today, owing=owing,
                           stock_value=stock_value, low_stock=low_stock)


@app.route("/subscription")
@login_required
def subscription():
    return render_template(
        "subscription.html",
        user=current_user()
    )
def pesapal_get_token():
    url = f"{PESAPAL_BASE_URL}/api/Auth/RequestToken"

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

    payload = {
        "consumer_key": PESAPAL_CONSUMER_KEY,
        "consumer_secret": PESAPAL_CONSUMER_SECRET
    }

    response = requests.post(
        url,
        json=payload,
        headers=headers,
        timeout=30
    )

    response.raise_for_status()

    data = response.json()
    return data["token"]

@app.route("/pay_subscription", methods=["GET", "POST"])
@login_required
def pay_subscription():
    plan = request.form.get("plan") or request.args.get("plan")
    plans = {
        "daily": 1,
        "weekly": 7,
        "monthly": 30,
        "yearly": 365
    }

    if plan not in plans:
        flash("Invalid subscription plan.", "error")
        return redirect(url_for("subscription"))

    user = current_user()

    user.subscription_expires = (
        datetime.utcnow() + timedelta(days=plans[plan])
    )

    db.session.commit()

    flash(
        f"Subscription activated successfully for {plan}.",
        "success"
    )

    return redirect(url_for("dashboard"))
@app.route("/pesapal/ipn", methods=["GET", "POST"])
def pesapal_ipn():
    return "OK", 200

@app.route("/admin")
@owner_required
def admin():
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template("admin.html", user=current_user(), users=users)


@app.route("/admin/activate-subscription/<int:user_id>", methods=["POST"])
@owner_required
def activate_subscription(user_id):
    target_user = db.session.get(User, user_id)
    if not target_user:
        flash("User not found.", "error")
        return redirect(url_for("admin"))
    base = target_user.subscription_expires if target_user.subscription_active() else datetime.utcnow()
    target_user.subscription_expires = base + timedelta(days=30)
    db.session.commit()
    flash(f"Subscription activated for {target_user.name} for 30 days.", "success")
    return redirect(url_for("admin"))


@app.route("/products", methods=["GET", "POST"])
@subscription_required
def products():
    user = current_user()
    if request.method == "POST":
        try:
            p = Product(user_id=user.id, name=request.form["name"].strip(),
                        category=request.form.get("category", "").strip() or None,
                        buying_price=float(request.form.get("buying_price", 0)),
                        selling_price=float(request.form.get("selling_price", 0)),
                        quantity=float(request.form.get("quantity", 0)),
                        low_stock_level=float(request.form.get("low_stock_level", 5)))
            if not p.name or min(p.buying_price, p.selling_price, p.quantity, p.low_stock_level) < 0:
                raise ValueError
            db.session.add(p)
            db.session.commit()
            flash("Product added successfully.", "success")
        except (ValueError, TypeError):
            flash("Please enter valid product values.", "error")
        return redirect(url_for("products"))
    items = Product.query.filter_by(user_id=user.id).order_by(Product.name.asc()).all()
    return render_template("products.html", products=items, user=user)


@app.route("/products/delete/<int:product_id>", methods=["POST"])
@subscription_required
def delete_product(product_id):
    product = Product.query.filter_by(id=product_id, user_id=current_user().id).first_or_404()
    db.session.delete(product)
    db.session.commit()
    flash("Product deleted.", "success")
    return redirect(url_for("products"))


@app.route("/customers", methods=["GET", "POST"])
@subscription_required
def customers():
    user = current_user()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Customer name is required.", "error")
        else:
            db.session.add(Customer(user_id=user.id, name=name, phone=request.form.get("phone", "").strip()))
            db.session.commit()
            flash("Customer added.", "success")
        return redirect(url_for("customers"))
    items = Customer.query.filter_by(user_id=user.id).order_by(Customer.name.asc()).all()
    balances = {c.id: sum(s.balance for s in Sale.query.filter_by(user_id=user.id, customer_id=c.id).all()) for c in items}
    return render_template("customers.html", customers=items, balances=balances, user=user)


@app.route("/expenses", methods=["GET", "POST"])
@subscription_required
def expenses():
    user = current_user()
    if request.method == "POST":
        try:
            amount = float(request.form.get("amount", 0))
            description = request.form.get("description", "").strip()
            if not description or amount <= 0:
                raise ValueError
            db.session.add(Expense(user_id=user.id, description=description, amount=amount))
            db.session.commit()
            flash("Expense recorded.", "success")
        except ValueError:
            flash("Enter a valid description and amount.", "error")
        return redirect(url_for("expenses"))
    items = Expense.query.filter_by(user_id=user.id).order_by(Expense.created_at.desc()).all()
    return render_template("expenses.html", expenses=items, user=user)


@app.route("/sales", methods=["GET", "POST"])
@subscription_required
def sales():
    user = current_user()
    products = Product.query.filter_by(user_id=user.id).order_by(Product.name.asc()).all()
    customers = Customer.query.filter_by(user_id=user.id).order_by(Customer.name.asc()).all()
    if request.method == "POST":
        try:
            product_id = int(request.form["product_id"])
            quantity = float(request.form["quantity"])
            amount_paid = float(request.form.get("amount_paid", 0))
            customer_id = int(request.form["customer_id"]) if request.form.get("customer_id") else None
            product = Product.query.filter_by(id=product_id, user_id=user.id).first()
            if not product or quantity <= 0 or quantity > product.quantity or amount_paid < 0:
                raise ValueError
            total = quantity * product.selling_price
            if amount_paid > total:
                raise ValueError
            sale = Sale(user_id=user.id, customer_id=customer_id, total=total, amount_paid=amount_paid)
            db.session.add(sale)
            db.session.flush()
            db.session.add(SaleItem(sale_id=sale.id, product_id=product.id, quantity=quantity,
                                    unit_price=product.selling_price, buying_price=product.buying_price))
            product.quantity -= quantity
            db.session.commit()
            flash(f"Sale recorded: UGX {total:,.0f}.", "success")
        except (ValueError, TypeError):
            db.session.rollback()
            flash("Please check the product, quantity and payment details.", "error")
        return redirect(url_for("sales"))
    recent = Sale.query.filter_by(user_id=user.id).order_by(Sale.created_at.desc()).limit(30).all()
    return render_template("sales.html", products=products, customers=customers, sales=recent, user=user)


@app.route("/receipts/<int:sale_id>")
@subscription_required
def receipt(sale_id):
    user = current_user()
    sale = Sale.query.filter_by(id=sale_id, user_id=user.id).first_or_404()
    items = SaleItem.query.filter_by(sale_id=sale.id).all()
    customer = db.session.get(Customer, sale.customer_id) if sale.customer_id else None
    return render_template("receipt.html", sale=sale, items=items, customer=customer, user=user)


@app.route("/reports")
@subscription_required
def reports():
    user = current_user()
    sales = Sale.query.filter_by(user_id=user.id).order_by(Sale.created_at.desc()).all()
    items = SaleItem.query.join(Sale).filter(Sale.user_id == user.id).all()
    total_sales = sum(s.total for s in sales)
    total_profit = sum(i.profit for i in items)
    total_expenses = db.session.query(func.coalesce(func.sum(Expense.amount), 0)).filter(Expense.user_id == user.id).scalar() or 0
    total_owing = sum(s.balance for s in sales)
    return render_template("reports.html", user=user, total_sales=total_sales,
                           total_profit=total_profit, total_expenses=total_expenses,
                           net_profit=total_profit-total_expenses, total_owing=total_owing,
                           sales=sales[:50])


with app.app_context():
    db.create_all()
    # Repair the current prototype: if no owner/admin exists, make the oldest account the owner.
    if User.query.filter(User.role.in_(["owner", "admin"])).count() == 0:
        first = User.query.order_by(User.created_at.asc()).first()
        if first:
            first.role = "owner"
            db.session.commit()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
