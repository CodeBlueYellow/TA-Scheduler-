import os
from functools import wraps

from flask import Flask
from flask_mysqldb import MySQL
from flask import render_template, request, redirect, url_for, session
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

# Secret key signs the session cookie so users can't edit it
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-later")

# MySQL stuff
app.config['MYSQL_HOST'] = 'localhost'
app.config['MYSQL_USER'] = 'root'
app.config['MYSQL_PASSWORD'] = 'Password'  # change later
app.config['MYSQL_DB'] = 'ta_scheduler'

mysql = MySQL(app)

# Put @role_required("Admin") or @role_required("TA") above a route to stop manual address placement
def role_required(role):
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if session.get("role") != role:
                return redirect(url_for("login"))
            return view(*args, **kwargs)
        return wrapper
    return decorator

@app.route("/", methods=["GET", "POST"]) #Login stuff
def login():

    error = None

    if request.method == "POST":

        username = request.form["username"]
        password = request.form["password"]
        role = request.form["role"]

        #Hard coded Admin account for Prof Brown
        if role == "Admin":

            if username == "admin" and password == "admin123":
                session.clear()
                session["username"] = username
                session["role"] = "Admin"
                return redirect(url_for("admin"))

        #TA login - look up the account and compare password hashes
        if role == "TA":

            cur = mysql.connection.cursor()
            cur.execute(
                "SELECT id, name, password_hash FROM users WHERE username = %s AND role = 'TA'",
                (username,)
            )
            user = cur.fetchone()
            cur.close()

            if user and check_password_hash(user[2], password):
                session.clear()
                session["user_id"] = user[0]
                session["name"] = user[1]
                session["username"] = username
                session["role"] = "TA"
                return redirect(url_for("ta"))

        # Same message whether the username or the password was wrong
        error = "Invalid username or password"

    return render_template("login.html", error=error)

def get_join_code():
    cur = mysql.connection.cursor()
    cur.execute("SELECT value FROM settings WHERE name = 'join_code'")
    row = cur.fetchone()
    cur.close()
    return row[0] if row else None

@app.route("/register", methods=["GET", "POST"]) #TA account creation
def register():

    error = None

    if request.method == "POST":

        name = request.form["name"].strip()
        username = request.form["username"].strip()
        contact = request.form["contact"].strip()
        password = request.form["password"]
        join_code = request.form["join_code"].strip()

        cur = mysql.connection.cursor()
        cur.execute("SELECT id FROM users WHERE username = %s", (username,))
        taken = cur.fetchone()

        if join_code != get_join_code():
            error = "Invalid join code"
        elif taken:
            error = "That username is already taken"
        elif len(password) < 8:
            error = "Password must be at least 8 characters"
        else:
            cur.execute(
                """
                INSERT INTO users (name, role, username, contact, password_hash)
                VALUES (%s, 'TA', %s, %s, %s)
                """,
                (name, username, contact, generate_password_hash(password))
            )
            mysql.connection.commit()
            cur.close()
            return redirect(url_for("login"))

        cur.close()

    return render_template("register.html", error=error)

@app.route("/join-code", methods=["GET", "POST"]) #admin manages join code
@role_required("Admin")
def join_code():

    message = None

    if request.method == "POST":

        new_code = request.form["join_code"].strip()

        if new_code:
            cur = mysql.connection.cursor()
            cur.execute(
                "UPDATE settings SET value = %s WHERE name = 'join_code'",
                (new_code,)
            )
            mysql.connection.commit()
            cur.close()
            message = "Join code updated"

    return render_template("join_code.html", join_code=get_join_code(), message=message)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/admin") #admin dash
@role_required("Admin")
def admin():
    return render_template("admin.html")

@app.route("/ta") #TA dash
@role_required("TA")
def ta():
    return render_template("ta.html")

@app.route("/home")
@role_required("Admin")
def home():
    return render_template("home.html")

@app.route("/test-db")
@role_required("Admin")
def test_db():

    cur = mysql.connection.cursor()
    cur.execute("SELECT 1")
    cur.close()

    return "Database works!"

@app.route("/availability", methods=["GET", "POST"])
@role_required("TA")
def availability():

    if request.method == "POST":

        name = request.form["name"]
        monday = request.form["monday"]
        tuesday = request.form["tuesday"]
        wednesday = request.form["wednesday"]
        thursday = request.form["thursday"]
        friday = request.form["friday"]

        cur = mysql.connection.cursor()

        cur.execute("""
            INSERT INTO availability
            (name, monday, tuesday, wednesday, thursday, friday)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            name,
            monday,
            tuesday,
            wednesday,
            thursday,
            friday
        ))

        mysql.connection.commit()
        cur.close()

        return redirect(url_for("ta"))

    return render_template("availability.html")

@app.route("/availability-checker")
@role_required("Admin")
def availability_checker():

    cur = mysql.connection.cursor()

    cur.execute("""
        SELECT name, monday, tuesday, wednesday, thursday, friday
        FROM availability
    """)

    data = cur.fetchall()

    cur.close()

    return render_template(
        "availability_checker.html",
        availability=data
    )

@app.route("/users")
@role_required("Admin")
def users():

    cur = mysql.connection.cursor()

    # List columns explicitly so password hashes never reach the page
    cur.execute("SELECT id, name, username, contact, role FROM users")

    data = cur.fetchall()

    cur.close()

    return render_template(
        "users.html",
        users=data
    )

if __name__ == "__main__":
    app.run(debug=True)