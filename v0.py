"""GDC Bank - management console with dashboard (tkinter + MySQL).

The database schema is NOT modified: this file only reads/writes the
customers, accounts and transactions tables you already created.
Requires:  pip install mysql-connector-python
"""
import csv
import math
import os
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import mysql.connector
from mysql.connector import Error

# ----------------------------------------------------------------- settings
DB_CONFIG = dict(
    host="localhost",
    user="root",
    password="",
    database="gdc_bank",
    port=3306,
)

# logo files live in the "assets" folder next to this script
ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

# colour palette
LOGO_BG = "#17111a"  # background colour of the GDC logo, so it blends in
SIDEBAR = LOGO_BG
SIDEBAR_HOVER = "#2a2230"
SIDEBAR_ACTIVE = "#2563eb"
BG = "#f1f5f9"
CARD = "#ffffff"
BORDER = "#e2e8f0"
TEXT = "#0f172a"
MUTED = "#64748b"
BLUE = "#2563eb"
GREEN = "#16a34a"
RED = "#dc2626"
ORANGE = "#f59e0b"
PURPLE = "#7c3aed"
FONT = "Segoe UI"  # Tk silently falls back to a default font if missing

TYPE_COLORS = {"deposit": GREEN, "withdrawal": RED, "transf_in": BLUE, "transf_out": ORANGE}
TYPE_LABELS = {
    "deposit": "Deposit",
    "withdrawal": "Withdrawal",
    "transf_in": "Transfer in",
    "transf_out": "Transfer out",
}
LABEL_TO_TYPE = {v: k for k, v in TYPE_LABELS.items()}


# ----------------------------------------------------------------- database
def get_connection():
    return mysql.connector.connect(**DB_CONFIG)


def run_query(sql, params=(), fetch=False):
    """Run ONE statement on its own connection.
    fetch=True returns the rows, otherwise it commits and returns lastrowid."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        if fetch:
            return cur.fetchall()
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def load_image(name):
    """Load assets/<name> as a Tk image, or None if the file is missing.
    (Keep a reference to the result, otherwise Tk garbage-collects it.)"""
    try:
        return tk.PhotoImage(file=os.path.join(ASSETS_DIR, name))
    except (tk.TclError, OSError):
        return None


def spaced(text):
    """'GESTION DES COMPTES' -> 'G E S T I O N   D E S ...' (wide letter-spacing like the logo)."""
    return "   ".join(" ".join(word) for word in text.split(" "))


def money(value):
    return f"{Decimal(value or 0):,.2f}"


def parse_amount(text):
    try:
        value = Decimal(text.strip().replace(",", "."))
    except InvalidOperation:
        raise ValueError("Amount must be a number.")
    if value <= 0:
        raise ValueError("Amount must be greater than 0.")
    return value.quantize(Decimal("0.01"))


def parse_date(text):
    text = text.strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("Dates must use the format YYYY-MM-DD (e.g. 2026-10-27).")


# ---------------------------------------------------------------- customers
class customers():
    def __init__(self, name, email, id=None):
        self.id = id
        self.name = name
        self.email = email

    def validate(self):
        if len(self.name.strip()) < 2:
            raise ValueError("Name must contain at least 2 characters.")
        if "@" not in self.email or "." not in self.email.split("@")[-1]:
            raise ValueError("Please enter a valid email address.")

    def add(self):
        self.validate()
        self.id = run_query(
            "INSERT INTO customers (full_name, email, created_at) VALUES (%s, %s, %s)",
            (self.name.strip(), self.email.strip(), date.today()),
        )
        return self.id

    def update_customers(self, new_name, new_email):
        self.name = new_name
        self.email = new_email
        self.validate()
        if self.id is not None:
            run_query(
                "UPDATE customers SET full_name=%s, email=%s WHERE id=%s",
                (self.name.strip(), self.email.strip(), self.id),
            )

    def delete(self):
        run_query("DELETE FROM customers WHERE id=%s", (self.id,))

    @staticmethod
    def all(search=""):
        like = f"%{search.strip()}%"
        return run_query(
            """SELECT c.id, c.full_name, c.email, c.created_at,
                      COUNT(a.id), COALESCE(SUM(a.balance), 0)
               FROM customers c LEFT JOIN accounts a ON a.customers_id = c.id
               WHERE c.full_name LIKE %s OR c.email LIKE %s
               GROUP BY c.id, c.full_name, c.email, c.created_at
               ORDER BY c.id""",
            (like, like),
            fetch=True,
        )

    @staticmethod
    def names():
        return run_query("SELECT id, full_name FROM customers ORDER BY full_name", fetch=True)


# ----------------------------------------------------------------- accounts
class accounts():
    @staticmethod
    def open(customer_id, initial=Decimal("0.00")):
        conn = get_connection()
        try:
            cur = conn.cursor()
            conn.start_transaction()
            cur.execute(
                "INSERT INTO accounts (customers_id, balance, created_at) VALUES (%s, %s, %s)",
                (customer_id, initial, date.today()),
            )
            acc_id = cur.lastrowid
            if initial > 0:
                cur.execute(
                    "INSERT INTO transactions (account_id, type, amount) VALUES (%s, 'deposit', %s)",
                    (acc_id, initial),
                )
            conn.commit()
            return acc_id
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def all(search=""):
        like = f"%{search.strip()}%"
        return run_query(
            """SELECT a.id, c.full_name, a.balance, a.created_at
               FROM accounts a JOIN customers c ON c.id = a.customers_id
               WHERE c.full_name LIKE %s OR CAST(a.id AS CHAR) LIKE %s
               ORDER BY a.id""",
            (like, like),
            fetch=True,
        )

    @staticmethod
    def delete(account_id):
        rows = run_query("SELECT balance FROM accounts WHERE id=%s", (account_id,), fetch=True)
        if not rows:
            raise ValueError("Account not found.")
        if rows[0][0] > 0:
            raise ValueError("Only accounts with a zero balance can be deleted.")
        run_query("DELETE FROM accounts WHERE id=%s", (account_id,))


# ------------------------------------------------------------- transactions
class transactions():
    @staticmethod
    def _move(account_id, kind, amount):
        conn = get_connection()
        try:
            cur = conn.cursor()
            conn.start_transaction()
            cur.execute("SELECT balance FROM accounts WHERE id=%s FOR UPDATE", (account_id,))
            row = cur.fetchone()
            if row is None:
                raise ValueError("Account not found.")
            if kind == "withdrawal":
                if row[0] < amount:
                    raise ValueError("Insufficient funds.")
                cur.execute("UPDATE accounts SET balance = balance - %s WHERE id=%s", (amount, account_id))
            else:
                cur.execute("UPDATE accounts SET balance = balance + %s WHERE id=%s", (amount, account_id))
            cur.execute(
                "INSERT INTO transactions (account_id, type, amount) VALUES (%s, %s, %s)",
                (account_id, kind, amount),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def deposit(account_id, amount):
        transactions._move(account_id, "deposit", amount)

    @staticmethod
    def withdraw(account_id, amount):
        transactions._move(account_id, "withdrawal", amount)

    @staticmethod
    def transfer(src, dst, amount):
        if src == dst:
            raise ValueError("Source and destination accounts must differ.")
        conn = get_connection()
        try:
            cur = conn.cursor()
            conn.start_transaction()
            cur.execute(
                "SELECT id, balance FROM accounts WHERE id IN (%s, %s) ORDER BY id FOR UPDATE",
                (src, dst),
            )
            balances = dict(cur.fetchall())
            if src not in balances or dst not in balances:
                raise ValueError("Account not found.")
            if balances[src] < amount:
                raise ValueError("Insufficient funds.")
            cur.execute("UPDATE accounts SET balance = balance - %s WHERE id=%s", (amount, src))
            cur.execute("UPDATE accounts SET balance = balance + %s WHERE id=%s", (amount, dst))
            cur.execute(
                "INSERT INTO transactions (account_id, type, amount) VALUES (%s, 'transf_out', %s)",
                (src, amount),
            )
            cur.execute(
                "INSERT INTO transactions (account_id, type, amount) VALUES (%s, 'transf_in', %s)",
                (dst, amount),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def search(account_id=None, kind=None, date_from=None, date_to=None, limit=1000):
        sql = """SELECT t.id, t.account_id, c.full_name, t.type, t.amount, t.created_at
                 FROM transactions t
                 JOIN accounts a ON a.id = t.account_id
                 JOIN customers c ON c.id = a.customers_id
                 WHERE 1=1"""
        params = []
        if account_id:
            sql += " AND t.account_id = %s"
            params.append(account_id)
        if kind:
            sql += " AND t.type = %s"
            params.append(kind)
        if date_from:
            sql += " AND DATE(t.created_at) >= %s"
            params.append(date_from)
        if date_to:
            sql += " AND DATE(t.created_at) <= %s"
            params.append(date_to)
        sql += " ORDER BY t.created_at DESC, t.id DESC LIMIT %s"
        params.append(limit)
        return run_query(sql, tuple(params), fetch=True)


# ---------------------------------------------------------------- dashboard
class stats():
    """Read-only queries that feed the dashboard."""

    @staticmethod
    def kpis():
        n_cust = run_query("SELECT COUNT(*) FROM customers", fetch=True)[0][0]
        n_acc, total = run_query(
            "SELECT COUNT(*), COALESCE(SUM(balance), 0) FROM accounts", fetch=True)[0]
        today = run_query(
            "SELECT COUNT(*) FROM transactions WHERE DATE(created_at) = CURDATE()",
            fetch=True)[0][0]
        return n_cust, n_acc, total, today

    @staticmethod
    def by_type():
        rows = run_query(
            "SELECT type, COUNT(*), COALESCE(SUM(amount), 0) FROM transactions GROUP BY type",
            fetch=True)
        data = {r[0]: (r[1], r[2]) for r in rows}
        return [(t, *data.get(t, (0, Decimal(0)))) for t in TYPE_COLORS]

    @staticmethod
    def last_days(n=7):
        start = date.today() - timedelta(days=n - 1)
        rows = run_query(
            """SELECT DATE(created_at), type, SUM(amount) FROM transactions
               WHERE created_at >= %s AND type IN ('deposit', 'withdrawal')
               GROUP BY DATE(created_at), type""",
            (start,), fetch=True)
        acc = {(d, t): float(s) for d, t, s in rows}
        days = [start + timedelta(days=i) for i in range(n)]
        return (days,
                [acc.get((d, "deposit"), 0.0) for d in days],
                [acc.get((d, "withdrawal"), 0.0) for d in days])

    @staticmethod
    def top_customers(limit=5):
        return run_query(
            """SELECT c.full_name, COUNT(a.id), COALESCE(SUM(a.balance), 0) AS total
               FROM customers c LEFT JOIN accounts a ON a.customers_id = c.id
               GROUP BY c.id, c.full_name
               ORDER BY total DESC LIMIT %s""",
            (limit,), fetch=True)

    @staticmethod
    def recent(limit=8):
        return run_query(
            """SELECT t.created_at, t.type, c.full_name, t.amount
               FROM transactions t
               JOIN accounts a ON a.id = t.account_id
               JOIN customers c ON c.id = a.customers_id
               ORDER BY t.created_at DESC, t.id DESC LIMIT %s""",
            (limit,), fetch=True)


# ------------------------------------------------------------- UI helpers
def setup_style(root):
    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT,
                rowheight=28, borderwidth=0, font=(FONT, 10))
    s.configure("Treeview.Heading", background="#f8fafc", foreground=MUTED,
                font=(FONT, 9, "bold"), relief="flat", padding=(8, 6))
    # workaround so Treeview row tags (colours) work on every Tk 8.6 build
    fg = [e for e in s.map("Treeview", query_opt="foreground") if e[:2] != ("!disabled", "!selected")]
    bg = [e for e in s.map("Treeview", query_opt="background") if e[:2] != ("!disabled", "!selected")]
    s.map("Treeview", foreground=[("selected", "white")] + fg, background=[("selected", BLUE)] + bg)
    s.map("Treeview.Heading", background=[("active", BORDER)])
    for name, color, hover, fg_color in (
        ("Primary", BLUE, "#1d4ed8", "white"),
        ("Success", GREEN, "#15803d", "white"),
        ("Danger", RED, "#b91c1c", "white"),
        ("Ghost", "#e2e8f0", "#cbd5e1", TEXT),
    ):
        s.configure(f"{name}.TButton", background=color, foreground=fg_color,
                    font=(FONT, 10, "bold"), padding=(14, 7), borderwidth=0, focusthickness=0)
        s.map(f"{name}.TButton", background=[("active", hover)])
    s.configure("TEntry", padding=6)
    s.configure("TCombobox", padding=6)
    s.configure("Card.TCheckbutton", background=CARD, foreground=TEXT, font=(FONT, 10))


def card(parent, title=None):
    """White rounded-look panel. Returns (outer_frame, body_frame)."""
    outer = tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
    if title:
        tk.Label(outer, text=title, bg=CARD, fg=TEXT, font=(FONT, 12, "bold")).pack(
            anchor="w", padx=14, pady=(12, 0))
    body = tk.Frame(outer, bg=CARD)
    body.pack(fill="both", expand=True, padx=14, pady=12)
    return outer, body


def field(parent, label, var, row, width=28):
    tk.Label(parent, text=label, bg=CARD, fg=MUTED, font=(FONT, 9)).grid(
        row=row * 2, column=0, sticky="w", pady=(10, 0))
    entry = ttk.Entry(parent, textvariable=var, width=width)
    entry.grid(row=row * 2 + 1, column=0, sticky="ew")
    return entry


def combo(parent, label, var, row, width=32):
    tk.Label(parent, text=label, bg=CARD, fg=MUTED, font=(FONT, 9)).grid(
        row=row * 2, column=0, sticky="w", pady=(10, 0))
    cb = ttk.Combobox(parent, textvariable=var, state="readonly", width=width)
    cb.grid(row=row * 2 + 1, column=0, sticky="ew")
    return cb


def button(parent, text, command, style="Primary"):
    return ttk.Button(parent, text=text, command=command, style=f"{style}.TButton", cursor="hand2")


def sort_by(tree, col, reverse):
    """Sort a Treeview by clicking its column heading."""
    def key(item):
        value = item[0].replace(",", "")
        try:
            return (0, float(value))
        except ValueError:
            return (1, value.lower())

    rows = [(tree.set(k, col), k) for k in tree.get_children("")]
    rows.sort(key=key, reverse=reverse)
    for index, (_, k) in enumerate(rows):
        tree.move(k, "", index)
    tree.heading(col, command=lambda: sort_by(tree, col, not reverse))


def make_tree(parent, columns, height=10):
    """columns = list of (id, heading, width, anchor)."""
    frame = tk.Frame(parent, bg=CARD)
    ids = [c[0] for c in columns]
    tree = ttk.Treeview(frame, columns=ids, show="headings", height=height, selectmode="browse")
    for cid, head, width, anchor in columns:
        tree.heading(cid, text=head, anchor=anchor,
                     command=lambda c=cid: sort_by(tree, c, False))
        tree.column(cid, width=width, anchor=anchor, stretch=True)
    scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    tree.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")
    for t, color in TYPE_COLORS.items():
        tree.tag_configure(t, foreground=color)
    tree.tag_configure("odd", background="#f8fafc")
    return frame, tree


def fill_tree(tree, rows, tags=None):
    tree.delete(*tree.get_children())
    for i, row in enumerate(rows):
        row_tags = []
        if tags:
            row_tags.append(tags[i])
        if i % 2:
            row_tags.append("odd")
        tree.insert("", "end", values=row, tags=tuple(row_tags))


def export_tree_csv(tree, name):
    path = filedialog.asksaveasfilename(
        defaultextension=".csv", filetypes=[("CSV file", "*.csv")],
        initialfile=f"{name}_{date.today()}.csv")
    if not path:
        return
    cols = tree["columns"]
    items = tree.get_children("")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([tree.heading(c)["text"] for c in cols])
        for k in items:
            writer.writerow(tree.item(k)["values"])
    messagebox.showinfo("Export", f"{len(items)} rows saved to:\n{path}")


def safe(action):
    """Run action(); show friendly dialogs instead of crashing. True = success."""
    try:
        action()
        return True
    except ValueError as e:
        messagebox.showwarning("Invalid input", str(e))
    except Error as e:
        msg = str(e)
        if e.errno == 1062:
            msg = "This email is already used by another customer."
        elif e.errno == 1451:
            msg = ("This record is linked to other data (for example an account with "
                   "transaction history) and cannot be deleted.")
        elif e.errno == 3819:
            msg = "The data violates a database rule (check email, name or amount)."
        elif e.errno in (1264, 1690):
            msg = "The amount is too large for the database."
        messagebox.showerror("Database error", msg)
    return False


# ------------------------------------------------------------------ charts
def nice_max(v):
    if v <= 0:
        return 1.0
    exp = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * exp:
            return m * exp
    return 10 * exp


def short(v):
    v = float(v)
    for unit, div in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if abs(v) >= div:
            return f"{v / div:.1f}".rstrip("0").rstrip(".") + unit
    return f"{v:.0f}"


def draw_grid(canvas, l, t, r, b, vmax):
    for i in range(5):
        y = b - (b - t) * i / 4
        canvas.create_line(l, y, r, y, fill=BORDER)
        canvas.create_text(l - 8, y, text=short(vmax * i / 4), anchor="e",
                           fill=MUTED, font=(FONT, 8))


class BarChart(tk.Canvas):
    """data = list of (label, value, colour, caption)."""

    def __init__(self, parent, **kw):
        super().__init__(parent, bg=CARD, highlightthickness=0, **kw)
        self.data = []
        self.bind("<Configure>", lambda e: self.redraw())

    def set_data(self, data):
        self.data = data
        self.redraw()

    def redraw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 80 or h < 80:
            return
        l, t, r, b = 52, 16, w - 14, h - 44
        vmax = nice_max(max([float(d[1]) for d in self.data] + [0]))
        draw_grid(self, l, t, r, b, vmax)
        if not self.data or all(float(d[1]) == 0 for d in self.data):
            self.create_text((l + r) / 2, (t + b) / 2, text="No transactions yet",
                             fill=MUTED, font=(FONT, 11))
        slot = (r - l) / max(len(self.data), 1)
        bar_w = min(64, slot * 0.55)
        for i, (label, value, color, caption) in enumerate(self.data):
            cx = l + slot * (i + 0.5)
            y = b - (b - t) * float(value) / vmax
            self.create_rectangle(cx - bar_w / 2, y, cx + bar_w / 2, b, fill=color, outline="")
            if float(value) > 0:
                self.create_text(cx, y - 9, text=short(value), fill=TEXT, font=(FONT, 9, "bold"))
            self.create_text(cx, b + 14, text=label, fill=TEXT, font=(FONT, 9))
            self.create_text(cx, b + 29, text=caption, fill=MUTED, font=(FONT, 8))


class LineChart(tk.Canvas):
    """labels = x labels; series = list of (name, values, colour)."""

    def __init__(self, parent, **kw):
        super().__init__(parent, bg=CARD, highlightthickness=0, **kw)
        self.labels = []
        self.series = []
        self.bind("<Configure>", lambda e: self.redraw())

    def set_data(self, labels, series):
        self.labels = labels
        self.series = series
        self.redraw()

    def redraw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 100 or h < 100:
            return
        l, t, r, b = 52, 36, w - 20, h - 28
        top = max([max(v) for _, v, _ in self.series if v] + [0])
        draw_grid(self, l, t, r, b, nice_max(top))
        vmax = nice_max(top)
        n = len(self.labels)
        if n == 0:
            return

        def xpos(i):
            return l + (r - l) * i / (n - 1) if n > 1 else (l + r) / 2

        for i, label in enumerate(self.labels):
            self.create_text(xpos(i), b + 14, text=label, fill=MUTED, font=(FONT, 8))
        if top == 0:
            self.create_text((l + r) / 2, (t + b) / 2, text="No activity in this period",
                             fill=MUTED, font=(FONT, 11))
        lx = l
        for name, values, color in self.series:
            pts = []
            for i, v in enumerate(values):
                pts += [xpos(i), b - (b - t) * v / vmax]
            if len(pts) >= 4:
                self.create_line(*pts, fill=color, width=2.5)
            for i in range(len(values)):
                x, y = pts[2 * i], pts[2 * i + 1]
                self.create_oval(x - 4, y - 4, x + 4, y + 4, fill=color, outline=CARD, width=1)
            # legend
            self.create_rectangle(lx, 12, lx + 10, 22, fill=color, outline="")
            self.create_text(lx + 16, 17, text=name, anchor="w", fill=TEXT, font=(FONT, 9))
            lx += 110


class KpiCard(tk.Frame):
    def __init__(self, parent, title, color, caption=""):
        super().__init__(parent, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        tk.Frame(self, bg=color, width=5).pack(side="left", fill="y")
        body = tk.Frame(self, bg=CARD)
        body.pack(side="left", fill="both", expand=True, padx=14, pady=12)
        tk.Label(body, text=title, bg=CARD, fg=MUTED, font=(FONT, 10)).pack(anchor="w")
        self.value = tk.Label(body, text="-", bg=CARD, fg=TEXT, font=(FONT, 20, "bold"))
        self.value.pack(anchor="w")
        self.caption = tk.Label(body, text=caption, bg=CARD, fg=MUTED, font=(FONT, 9))
        self.caption.pack(anchor="w")

    def set(self, value, caption=None):
        self.value.config(text=value)
        if caption is not None:
            self.caption.config(text=caption)


class Banner(tk.Frame):
    """Dark brand banner that reproduces the GDC logo layout on the dashboard."""

    def __init__(self, parent):
        super().__init__(parent, bg=LOGO_BG)
        self.logo = load_image("banner_logo.png")  # keep a reference
        if self.logo:
            tk.Label(self, image=self.logo, bg=LOGO_BG).pack(side="left", padx=(28, 0), pady=14)
        else:
            tk.Label(self, text="|  GDC", bg=LOGO_BG, fg="white", font=("Georgia", 34)).pack(
                side="left", padx=(28, 0), pady=14)

        # tagline block, same wording as the logo
        mid = tk.Frame(self, bg=LOGO_BG)
        mid.pack(side="left", padx=34)
        tk.Label(mid, text=spaced("GESTION DES COMPTES"), bg=LOGO_BG, fg="white",
                 font=("Georgia", 16)).pack(anchor="w")
        tk.Label(mid, text=spaced("VOTRE CRÉDIBILITÉ, NOTRE CAP"), bg=LOGO_BG, fg="#a1a1aa",
                 font=("Georgia", 9)).pack(anchor="w", pady=(8, 0))

        # welcome + date on the right
        right = tk.Frame(self, bg=LOGO_BG)
        right.pack(side="right", padx=28)
        tk.Label(right, text="Welcome back", bg=LOGO_BG, fg="white",
                 font=(FONT, 14, "bold")).pack(anchor="e")
        self.date_lbl = tk.Label(right, text="", bg=LOGO_BG, fg="#a1a1aa", font=(FONT, 10))
        self.date_lbl.pack(anchor="e", pady=(4, 0))
        self.update_date()

    def update_date(self):
        self.date_lbl.config(text=datetime.now().strftime("%A, %d %B %Y"))


# -------------------------------------------------------------------- pages
class DashboardPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app = app
        for c in range(4):
            self.columnconfigure(c, weight=1, uniform="kpi")
        self.rowconfigure(2, weight=3)
        self.rowconfigure(3, weight=2)

        self.banner = Banner(self)
        self.banner.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 12))

        self.k_customers = KpiCard(self, "Customers", BLUE, "registered clients")
        self.k_accounts = KpiCard(self, "Accounts", PURPLE, "open accounts")
        self.k_balance = KpiCard(self, "Total balance", GREEN, "money held by the bank")
        self.k_today = KpiCard(self, "Transactions today", ORANGE, "operations since midnight")
        for i, k in enumerate((self.k_customers, self.k_accounts, self.k_balance, self.k_today)):
            k.grid(row=1, column=i, sticky="ew", padx=(0, 12 if i < 3 else 0))

        # row 2: charts
        row1 = tk.Frame(self, bg=BG)
        row1.grid(row=2, column=0, columnspan=4, sticky="nsew", pady=12)
        row1.rowconfigure(0, weight=1)
        row1.columnconfigure(0, weight=3, uniform="r1")
        row1.columnconfigure(1, weight=2, uniform="r1")
        c1, b1 = card(row1, "Deposits vs withdrawals - last 7 days")
        c1.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self.line = LineChart(b1, height=190)
        self.line.pack(fill="both", expand=True)
        c2, b2 = card(row1, "Money moved by type")
        c2.grid(row=0, column=1, sticky="nsew")
        self.bar = BarChart(b2, height=190)
        self.bar.pack(fill="both", expand=True)

        # row 3: tables
        row2 = tk.Frame(self, bg=BG)
        row2.grid(row=3, column=0, columnspan=4, sticky="nsew")
        row2.rowconfigure(0, weight=1)
        row2.columnconfigure(0, weight=2, uniform="r2")
        row2.columnconfigure(1, weight=3, uniform="r2")
        c3, b3 = card(row2, "Top customers by balance")
        c3.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        f3, self.top_tree = make_tree(b3, [
            ("name", "Customer", 160, "w"),
            ("accounts", "Accounts", 70, "center"),
            ("balance", "Balance", 110, "e"),
        ], height=5)
        f3.pack(fill="both", expand=True)
        c4, b4 = card(row2, "Recent transactions")
        c4.grid(row=0, column=1, sticky="nsew")
        f4, self.recent_tree = make_tree(b4, [
            ("date", "Date", 130, "w"),
            ("type", "Type", 100, "w"),
            ("customer", "Customer", 150, "w"),
            ("amount", "Amount", 100, "e"),
        ], height=5)
        f4.pack(fill="both", expand=True)

    def refresh(self):
        self.banner.update_date()
        n_cust, n_acc, total, today = stats.kpis()
        self.k_customers.set(str(n_cust))
        self.k_accounts.set(str(n_acc))
        self.k_balance.set(money(total))
        self.k_today.set(str(today))

        days, deposits, withdrawals = stats.last_days(7)
        self.line.set_data([d.strftime("%d %b") for d in days],
                           [("Deposits", deposits, GREEN), ("Withdrawals", withdrawals, RED)])
        self.bar.set_data([(TYPE_LABELS[t], float(s), TYPE_COLORS[t], f"{c} tx")
                           for t, c, s in stats.by_type()])

        fill_tree(self.top_tree, [(n, c, money(t)) for n, c, t in stats.top_customers(5)])
        recent = stats.recent(8)
        fill_tree(self.recent_tree,
                  [(str(r[0])[:16], TYPE_LABELS.get(r[1], r[1]), r[2], money(r[3])) for r in recent],
                  tags=[r[1] for r in recent])


class CustomersPage(tk.Frame):
    COLS = [
        ("id", "ID", 50, "center"),
        ("name", "Full name", 190, "w"),
        ("email", "Email", 230, "w"),
        ("since", "Customer since", 110, "center"),
        ("accounts", "Accounts", 80, "center"),
        ("balance", "Total balance", 120, "e"),
    ]

    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app = app
        self.selected_id = None
        self.name_var = tk.StringVar()
        self.email_var = tk.StringVar()
        self.search_var = tk.StringVar()
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        form_card, form = card(self, "Customer details")
        form_card.grid(row=0, column=0, sticky="ns", padx=(0, 16))
        field(form, "Full name", self.name_var, 0)
        field(form, "Email", self.email_var, 1)
        btns = tk.Frame(form, bg=CARD)
        btns.grid(row=4, column=0, sticky="ew", pady=(18, 0))
        btns.columnconfigure((0, 1), weight=1)
        button(btns, "Add", self.add, "Primary").grid(row=0, column=0, sticky="ew", padx=(0, 4), pady=(0, 6))
        button(btns, "Update", self.update, "Ghost").grid(row=0, column=1, sticky="ew", padx=(4, 0), pady=(0, 6))
        button(btns, "Delete", self.delete, "Danger").grid(row=1, column=0, sticky="ew", padx=(0, 4))
        button(btns, "Clear", self.clear, "Ghost").grid(row=1, column=1, sticky="ew", padx=(4, 0))
        button(btns, "Open account for selected", self.open_account, "Success").grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))

        list_card, body = card(self, "All customers")
        list_card.grid(row=0, column=1, sticky="nsew")
        bar = tk.Frame(body, bg=CARD)
        bar.pack(fill="x", pady=(0, 8))
        tk.Label(bar, text="Search", bg=CARD, fg=MUTED, font=(FONT, 9)).pack(side="left")
        entry = ttk.Entry(bar, textvariable=self.search_var, width=28)
        entry.pack(side="left", padx=8)
        entry.bind("<KeyRelease>", lambda e: self.refresh())
        button(bar, "Export CSV", lambda: export_tree_csv(self.tree, "customers"), "Ghost").pack(side="right")
        self.count_lbl = tk.Label(bar, text="", bg=CARD, fg=MUTED, font=(FONT, 9))
        self.count_lbl.pack(side="right", padx=12)
        frame, self.tree = make_tree(body, self.COLS)
        frame.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

    def refresh(self):
        rows = customers.all(self.search_var.get())
        fill_tree(self.tree, [(r[0], r[1], r[2], r[3], r[4], money(r[5])) for r in rows])
        self.count_lbl.config(text=f"{len(rows)} customers")

    def clear(self):
        self.selected_id = None
        self.name_var.set("")
        self.email_var.set("")
        self.tree.selection_remove(self.tree.selection())

    def on_select(self, _event):
        sel = self.tree.selection()
        if sel:
            values = self.tree.item(sel[0])["values"]
            self.selected_id = int(values[0])
            self.name_var.set(str(values[1]))
            self.email_var.set(str(values[2]))

    def _need_selection(self):
        if self.selected_id is None:
            messagebox.showinfo("Customers", "Select a customer in the table first.")
            return False
        return True

    def add(self):
        c = customers(self.name_var.get(), self.email_var.get())
        if safe(c.add):
            self.clear()
            self.app.refresh_all()

    def update(self):
        if not self._need_selection():
            return
        c = customers(self.name_var.get(), self.email_var.get(), id=self.selected_id)
        if safe(lambda: c.update_customers(self.name_var.get(), self.email_var.get())):
            self.app.refresh_all()

    def delete(self):
        if not self._need_selection():
            return
        if not messagebox.askyesno("Delete", "Delete this customer?"):
            return
        c = customers("", "", id=self.selected_id)
        if safe(c.delete):
            self.clear()
            self.app.refresh_all()

    def open_account(self):
        if not self._need_selection():
            return
        if safe(lambda: accounts.open(self.selected_id, Decimal("0.00"))):
            self.app.refresh_all()
            messagebox.showinfo("Account", "A new account with a zero balance was opened.")


class AccountsPage(tk.Frame):
    COLS = [
        ("id", "Account", 70, "center"),
        ("owner", "Owner", 240, "w"),
        ("balance", "Balance", 140, "e"),
        ("since", "Opened on", 110, "center"),
    ]

    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app = app
        self.customer_var = tk.StringVar()
        self.initial_var = tk.StringVar(value="0.00")
        self.search_var = tk.StringVar()
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        form_card, form = card(self, "Open a new account")
        form_card.grid(row=0, column=0, sticky="ns", padx=(0, 16))
        self.customer_cb = combo(form, "Customer", self.customer_var, 0)
        field(form, "Initial deposit", self.initial_var, 1)
        button(form, "Open account", self.open_account, "Primary").grid(
            row=4, column=0, sticky="ew", pady=(18, 0))
        ttk.Separator(form).grid(row=5, column=0, sticky="ew", pady=18)
        tk.Label(form, text="Select an account in the table to delete it\n(balance must be zero and\nwithout transaction history).",
                 bg=CARD, fg=MUTED, font=(FONT, 9), justify="left").grid(row=6, column=0, sticky="w")
        button(form, "Delete selected account", self.delete, "Danger").grid(
            row=7, column=0, sticky="ew", pady=(8, 0))

        list_card, body = card(self, "All accounts")
        list_card.grid(row=0, column=1, sticky="nsew")
        bar = tk.Frame(body, bg=CARD)
        bar.pack(fill="x", pady=(0, 8))
        tk.Label(bar, text="Search", bg=CARD, fg=MUTED, font=(FONT, 9)).pack(side="left")
        entry = ttk.Entry(bar, textvariable=self.search_var, width=28)
        entry.pack(side="left", padx=8)
        entry.bind("<KeyRelease>", lambda e: self.refresh())
        button(bar, "Export CSV", lambda: export_tree_csv(self.tree, "accounts"), "Ghost").pack(side="right")
        self.summary_lbl = tk.Label(bar, text="", bg=CARD, fg=MUTED, font=(FONT, 9))
        self.summary_lbl.pack(side="right", padx=12)
        frame, self.tree = make_tree(body, self.COLS)
        frame.pack(fill="both", expand=True)

    def refresh(self):
        names = customers.names()
        self.customer_cb["values"] = [f"{i} - {n}" for i, n in names]
        rows = accounts.all(self.search_var.get())
        fill_tree(self.tree, [(r[0], r[1], money(r[2]), r[3]) for r in rows])
        total = sum((r[2] for r in rows), Decimal(0))
        self.summary_lbl.config(text=f"{len(rows)} accounts - total {money(total)}")

    def open_account(self):
        if not self.customer_var.get():
            return messagebox.showinfo("Account", "Select a customer first.")
        cid = int(self.customer_var.get().split(" - ")[0])

        def action():
            raw = self.initial_var.get().strip().replace(",", ".") or "0"
            try:
                initial = Decimal(raw)
            except InvalidOperation:
                raise ValueError("Initial deposit must be a number.")
            if initial < 0:
                raise ValueError("Initial deposit cannot be negative.")
            accounts.open(cid, initial.quantize(Decimal("0.01")))

        if safe(action):
            self.initial_var.set("0.00")
            self.app.refresh_all()

    def delete(self):
        sel = self.tree.selection()
        if not sel:
            return messagebox.showinfo("Account", "Select an account in the table first.")
        acc_id = int(self.tree.item(sel[0])["values"][0])
        if not messagebox.askyesno("Delete", f"Delete account {acc_id}?"):
            return
        if safe(lambda: accounts.delete(acc_id)):
            self.app.refresh_all()


class TransactionsPage(tk.Frame):
    COLS = [
        ("id", "ID", 60, "center"),
        ("account", "Account", 70, "center"),
        ("owner", "Owner", 170, "w"),
        ("type", "Type", 110, "w"),
        ("amount", "Amount", 110, "e"),
        ("date", "Date", 150, "center"),
    ]

    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app = app
        self._accounts = {}
        self.acc_var = tk.StringVar()
        self.dst_var = tk.StringVar()
        self.amount_var = tk.StringVar()
        self.kind_var = tk.StringVar(value="All")
        self.from_var = tk.StringVar()
        self.to_var = tk.StringVar()
        self.only_sel = tk.BooleanVar(value=False)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        # ---- left: new operation
        form_card, form = card(self, "New operation")
        form_card.grid(row=0, column=0, sticky="ns", padx=(0, 16))
        self.acc_cb = combo(form, "Account", self.acc_var, 0, width=34)
        self.acc_cb.bind("<<ComboboxSelected>>", lambda e: self.on_account_change())
        self.balance_lbl = tk.Label(form, text="Balance: -", bg=CARD, fg=GREEN, font=(FONT, 13, "bold"))
        self.balance_lbl.grid(row=2, column=0, sticky="w", pady=(8, 0))
        field(form, "Amount", self.amount_var, 2)
        self.dst_cb = combo(form, "Transfer to (only for transfers)", self.dst_var, 3, width=34)
        button(form, "Deposit", self.deposit, "Success").grid(row=8, column=0, sticky="ew", pady=(18, 0))
        button(form, "Withdraw", self.withdraw, "Danger").grid(row=9, column=0, sticky="ew", pady=(6, 0))
        button(form, "Transfer", self.transfer, "Primary").grid(row=10, column=0, sticky="ew", pady=(6, 0))

        # ---- right: history with filters
        list_card, body = card(self, "Transaction history")
        list_card.grid(row=0, column=1, sticky="nsew")
        bar1 = tk.Frame(body, bg=CARD)
        bar1.pack(fill="x")
        tk.Label(bar1, text="Type", bg=CARD, fg=MUTED, font=(FONT, 9)).pack(side="left")
        type_cb = ttk.Combobox(bar1, textvariable=self.kind_var, state="readonly", width=13,
                               values=["All"] + list(TYPE_LABELS.values()))
        type_cb.pack(side="left", padx=(6, 14))
        type_cb.bind("<<ComboboxSelected>>", lambda e: safe(self.load_history))
        tk.Label(bar1, text="From", bg=CARD, fg=MUTED, font=(FONT, 9)).pack(side="left")
        e1 = ttk.Entry(bar1, textvariable=self.from_var, width=11)
        e1.pack(side="left", padx=(6, 10))
        tk.Label(bar1, text="To", bg=CARD, fg=MUTED, font=(FONT, 9)).pack(side="left")
        e2 = ttk.Entry(bar1, textvariable=self.to_var, width=11)
        e2.pack(side="left", padx=(6, 10))
        for e in (e1, e2):
            e.bind("<Return>", lambda ev: safe(self.load_history))
        ttk.Checkbutton(bar1, text="Selected account only", variable=self.only_sel,
                        style="Card.TCheckbutton", command=lambda: safe(self.load_history)).pack(side="left", padx=8)

        bar2 = tk.Frame(body, bg=CARD)
        bar2.pack(fill="x", pady=(8, 8))
        tk.Label(bar2, text="Dates as YYYY-MM-DD", bg=CARD, fg=MUTED, font=(FONT, 8)).pack(side="left")
        button(bar2, "Export CSV", lambda: export_tree_csv(self.tree, "transactions"), "Ghost").pack(side="right")
        button(bar2, "Apply", lambda: safe(self.load_history), "Primary").pack(side="right", padx=6)
        for text, days in (("All time", None), ("30 days", 30), ("7 days", 7), ("Today", 0)):
            button(bar2, text, lambda d=days: self.set_range(d), "Ghost").pack(side="right", padx=2)

        frame, self.tree = make_tree(body, self.COLS)
        frame.pack(fill="both", expand=True)
        self.summary_lbl = tk.Label(body, text="", bg=CARD, fg=MUTED, font=(FONT, 9))
        self.summary_lbl.pack(anchor="w", pady=(8, 0))

    @staticmethod
    def _id(text):
        return int(text.split(" - ")[0])

    @staticmethod
    def _label(r):
        return f"{r[0]} - {r[1]} ({money(r[2])})"

    def refresh(self):
        rows = accounts.all()
        self._accounts = {r[0]: r for r in rows}
        labels = [self._label(r) for r in rows]
        self.acc_cb["values"] = labels
        self.dst_cb["values"] = labels
        current = self.acc_var.get()
        if current:  # keep the selection but refresh its label (balance changed)
            match = [l for l in labels if self._id(l) == self._id(current)]
            self.acc_var.set(match[0] if match else "")
        self.dst_var.set("")
        self.update_balance()
        self.load_history()

    def update_balance(self):
        if self.acc_var.get():
            row = self._accounts.get(self._id(self.acc_var.get()))
            self.balance_lbl.config(text=f"Balance: {money(row[2])}" if row else "Balance: -")
        else:
            self.balance_lbl.config(text="Balance: -")

    def on_account_change(self):
        self.update_balance()
        if self.only_sel.get():
            safe(self.load_history)

    def set_range(self, days):
        if days is None:
            self.from_var.set("")
            self.to_var.set("")
        else:
            self.from_var.set(str(date.today() - timedelta(days=days)))
            self.to_var.set("")
        safe(self.load_history)

    def load_history(self):
        acc = None
        if self.only_sel.get() and self.acc_var.get():
            acc = self._id(self.acc_var.get())
        kind = LABEL_TO_TYPE.get(self.kind_var.get())
        rows = transactions.search(acc, kind, parse_date(self.from_var.get()), parse_date(self.to_var.get()))
        fill_tree(self.tree,
                  [(r[0], r[1], r[2], TYPE_LABELS.get(r[3], r[3]), money(r[4]), str(r[5])) for r in rows],
                  tags=[r[3] for r in rows])
        money_in = sum((r[4] for r in rows if r[3] in ("deposit", "transf_in")), Decimal(0))
        money_out = sum((r[4] for r in rows if r[3] in ("withdrawal", "transf_out")), Decimal(0))
        self.summary_lbl.config(
            text=f"{len(rows)} transactions - in: {money(money_in)} - out: {money(money_out)}")

    def _run(self, needs_dst, action):
        if not self.acc_var.get():
            return messagebox.showinfo("Transaction", "Select an account first.")
        if needs_dst and not self.dst_var.get():
            return messagebox.showinfo("Transfer", "Select a destination account.")

        def wrapped():
            action(parse_amount(self.amount_var.get()))

        if safe(wrapped):
            self.amount_var.set("")
            self.app.refresh_all()

    def deposit(self):
        self._run(False, lambda a: transactions.deposit(self._id(self.acc_var.get()), a))

    def withdraw(self):
        self._run(False, lambda a: transactions.withdraw(self._id(self.acc_var.get()), a))

    def transfer(self):
        self._run(True, lambda a: transactions.transfer(
            self._id(self.acc_var.get()), self._id(self.dst_var.get()), a))


# ---------------------------------------------------------------- main app
class BankApp(tk.Tk):
    NAV = ["Dashboard", "Customers", "Accounts", "Transactions"]

    def __init__(self):
        super().__init__()
        self.title("GDC Bank - Management Console")
        self.geometry("1280x840")
        self.minsize(1120, 740)
        self.configure(bg=BG)
        setup_style(self)
        self.current = None

        # ---- logo: window / taskbar icon + sidebar logo (references kept on self)
        self.icon_img = load_image("app_icon.png")
        if self.icon_img:
            self.iconphoto(True, self.icon_img)
        self.side_logo = load_image("sidebar_logo.png")

        # ---- sidebar
        side = tk.Frame(self, bg=SIDEBAR, width=210)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        if self.side_logo:
            tk.Label(side, image=self.side_logo, bg=SIDEBAR).pack(pady=(22, 4))
        else:
            tk.Label(side, text="GDC Bank", bg=SIDEBAR, fg="white", font=(FONT, 18, "bold")).pack(
                anchor="w", padx=22, pady=(26, 0))
        tk.Label(side, text="Management console", bg=SIDEBAR, fg="#a1a1aa", font=(FONT, 9)).pack(
            pady=(0, 22))
        self.nav = {}
        for name in self.NAV:
            lbl = tk.Label(side, text=name, bg=SIDEBAR, fg="white", font=(FONT, 11),
                           anchor="w", padx=22, pady=12, cursor="hand2")
            lbl.pack(fill="x")
            lbl.bind("<Button-1>", lambda e, n=name: self.show(n))
            lbl.bind("<Enter>", lambda e, n=name: self._hover(n, True))
            lbl.bind("<Leave>", lambda e, n=name: self._hover(n, False))
            self.nav[name] = lbl
        tk.Label(side, text="● Connected to " + DB_CONFIG["database"], bg=SIDEBAR, fg="#4ade80",
                 font=(FONT, 9)).pack(side="bottom", anchor="w", padx=22, pady=18)

        # ---- main area
        main = tk.Frame(self, bg=BG)
        main.pack(side="left", fill="both", expand=True)
        header = tk.Frame(main, bg=BG)
        header.pack(fill="x", padx=24, pady=(20, 10))
        self.title_lbl = tk.Label(header, text="", bg=BG, fg=TEXT, font=(FONT, 22, "bold"))
        self.title_lbl.pack(side="left")
        button(header, "Refresh", self.refresh_all, "Ghost").pack(side="right")
        self.stamp_lbl = tk.Label(header, text="", bg=BG, fg=MUTED, font=(FONT, 9))
        self.stamp_lbl.pack(side="right", padx=12)

        container = tk.Frame(main, bg=BG)
        container.pack(fill="both", expand=True, padx=24, pady=(0, 20))
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)
        self.pages = {
            "Dashboard": DashboardPage(container, self),
            "Customers": CustomersPage(container, self),
            "Accounts": AccountsPage(container, self),
            "Transactions": TransactionsPage(container, self),
        }
        for page in self.pages.values():
            page.grid(row=0, column=0, sticky="nsew")

        self.show("Dashboard")
        self.after(30000, self._auto_refresh)

    def _hover(self, name, inside):
        if name != self.current:
            self.nav[name].config(bg=SIDEBAR_HOVER if inside else SIDEBAR)

    def show(self, name):
        self.current = name
        for n, lbl in self.nav.items():
            lbl.config(bg=SIDEBAR_ACTIVE if n == name else SIDEBAR)
        self.title_lbl.config(text=name)
        self.pages[name].tkraise()
        safe(self.pages[name].refresh)
        self._stamp()

    def _stamp(self):
        self.stamp_lbl.config(text="Updated " + datetime.now().strftime("%H:%M:%S"))

    def refresh_all(self):
        def run():
            for page in self.pages.values():
                page.refresh()

        safe(run)
        self._stamp()

    def _auto_refresh(self):
        """Keep the dashboard live (every 30 s) without popping up errors."""
        if self.current == "Dashboard":
            try:
                self.pages["Dashboard"].refresh()
                self._stamp()
            except Error:
                pass
        self.after(30000, self._auto_refresh)


if __name__ == "__main__":
    try:
        get_connection().close()
    except Error as e:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Connection failed", f"Cannot connect to MySQL:\n{e}")
        root.destroy()
    else:
        BankApp().mainloop()