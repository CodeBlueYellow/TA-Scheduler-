import csv
import io
import os
from datetime import datetime
from functools import wraps

from flask import Flask
from flask_mysqldb import MySQL
from flask import render_template, request, redirect, url_for, session, abort
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

# Reject uploads over 1 MB (a schedule CSV is a few KB)
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024

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

def get_schedules():
    cur = mysql.connection.cursor()

    # LEFT JOIN so empty schedules still show up with 0 shifts
    cur.execute("""
        SELECT s.id, s.name, COUNT(sh.id), s.created_at
        FROM schedules s
        LEFT JOIN shifts sh ON sh.schedule_id = s.id
        GROUP BY s.id
        ORDER BY s.created_at DESC
    """)

    data = cur.fetchall()
    cur.close()
    return data

def validate_schedule_name(name):
    # Shared by create, upload, and update
    if not name:
        return "Schedule name is required"
    if len(name) > 100:
        return "Schedule name must be 100 characters or less"
    return None

@app.route("/schedules") #admin views all schedules
@role_required("Admin")
def schedules():
    return render_template("schedules.html", schedules=get_schedules())

@app.route("/schedules/new", methods=["POST"]) #admin creates an empty schedule
@role_required("Admin")
def new_schedule():

    name = request.form["name"].strip()

    error = validate_schedule_name(name)

    if error:
        return render_template("schedules.html", schedules=get_schedules(), error=error)

    cur = mysql.connection.cursor()
    cur.execute("INSERT INTO schedules (name) VALUES (%s)", (name,))
    mysql.connection.commit()
    schedule_id = cur.lastrowid
    cur.close()

    # Go straight to the new schedule so the prof can start adding shifts
    return redirect(url_for("edit_schedule", schedule_id=schedule_id))

UPLOAD_COLUMNS = ["day", "start_time", "end_time", "location", "tas_needed"]

def read_schedule_csv(file):
    # Returns (shift values, errors). Every row is checked before anything is
    # saved so a bad file never leaves a half-built schedule behind
    try:
        # utf-8-sig drops the hidden marker Excel puts at the start of "CSV UTF-8" files
        text = file.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        return [], ["The file couldn't be read. Save it from Excel as \"CSV UTF-8\" and try again."]

    reader = csv.DictReader(io.StringIO(text))

    # Ignore capitalization and extra spaces in the header row
    headers = [h.strip().lower() for h in (reader.fieldnames or [])]
    missing = [col for col in UPLOAD_COLUMNS if col not in headers]

    if missing:
        return [], ["Missing column(s): " + ", ".join(missing)
                    + ". The first row must be: " + ",".join(UPLOAD_COLUMNS)]

    reader.fieldnames = headers

    shifts = []
    errors = []

    # Row 1 is the header, so the first shift is row 2 (matches Excel's row numbers)
    for row_number, row in enumerate(reader, start=2):

        row = {key: (value or "").strip() for key, value in row.items() if key}

        # Excel often leaves trailing rows of empty commas; skip them
        if not any(row.values()):
            continue

        row["day"] = row["day"].capitalize()
        row["tas_needed"] = row["tas_needed"] or "1"

        values, error = validate_shift(row)

        if error:
            errors.append(f"Row {row_number}: {error}")
        else:
            shifts.append(values)

    if not shifts and not errors:
        errors.append("The file has no shifts in it")

    return shifts, errors

@app.route("/schedules/upload", methods=["POST"]) #admin uploads a prebuilt schedule
@role_required("Admin")
def upload_schedule():

    name = request.form["name"].strip()
    file = request.files.get("file")

    errors = []

    name_error = validate_schedule_name(name)
    if name_error:
        errors.append(name_error)

    if not file or not file.filename:
        errors.append("Choose a CSV file to upload")
    elif not file.filename.lower().endswith(".csv"):
        errors.append("The file must be a .csv (in Excel: File > Save As > CSV UTF-8)")
    else:
        shifts, file_errors = read_schedule_csv(file)
        errors.extend(file_errors)

    if errors:
        return render_template("schedules.html", schedules=get_schedules(), upload_errors=errors)

    cur = mysql.connection.cursor()

    try:
        cur.execute("INSERT INTO schedules (name) VALUES (%s)", (name,))
        schedule_id = cur.lastrowid

        cur.executemany(
            """
            INSERT INTO shifts (schedule_id, day, start_time, end_time, location, tas_needed)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            [(schedule_id, *values) for values in shifts]
        )

        # One commit for the schedule and all its shifts, so it's all or nothing
        mysql.connection.commit()
    except Exception:
        mysql.connection.rollback()
        raise
    finally:
        cur.close()

    return redirect(url_for("edit_schedule", schedule_id=schedule_id))

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

def format_time(value):
    # MySQL TIME columns come back as timedelta; the time input wants "HH:MM"
    minutes = int(value.total_seconds()) // 60
    return f"{minutes // 60:02d}:{minutes % 60:02d}"

def parse_time(value):
    # 24-hour from the time inputs, plus 12-hour ("1:30 PM") since Excel often saves times that way
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M:%S %p"):
        try:
            return datetime.strptime(value.strip(), fmt).time()
        except ValueError:
            pass
    return None

def validate_shift(form):
    # Returns (values, error) so add and edit share the same rules
    day = form.get("day", "")
    start = parse_time(form.get("start_time", ""))
    end = parse_time(form.get("end_time", ""))
    location = form.get("location", "").strip()

    try:
        tas_needed = int(form.get("tas_needed", ""))
    except ValueError:
        tas_needed = 0

    if day not in DAYS:
        return None, "Day must be Monday to Friday"
    if not start or not end:
        return None, "Start and end times are required"
    if start >= end:
        return None, "Start time must be before end time"
    if len(location) > 100:
        return None, "Location must be 100 characters or less"
    if tas_needed < 1:
        return None, "TAs needed must be at least 1"

    return (day, start, end, location, tas_needed), None

def get_schedule(schedule_id):
    cur = mysql.connection.cursor()
    cur.execute("SELECT id, name FROM schedules WHERE id = %s", (schedule_id,))
    schedule = cur.fetchone()
    cur.close()

    if not schedule:
        abort(404)

    return schedule

def render_schedule_edit(schedule_id, error=None, message=None):
    schedule = get_schedule(schedule_id)

    cur = mysql.connection.cursor()

    # FIELD() sorts by weekday order instead of alphabetically
    cur.execute("""
        SELECT id, day, start_time, end_time, location, tas_needed
        FROM shifts
        WHERE schedule_id = %s
        ORDER BY FIELD(day, 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'), start_time
    """, (schedule_id,))

    shifts = [
        (row[0], row[1], format_time(row[2]), format_time(row[3]), row[4], row[5])
        for row in cur.fetchall()
    ]

    cur.close()

    return render_template(
        "schedule_edit.html",
        schedule=schedule,
        shifts=shifts,
        days=DAYS,
        error=error,
        message=message
    )

@app.route("/schedules/<int:schedule_id>") #admin edits one schedule
@role_required("Admin")
def edit_schedule(schedule_id):
    return render_schedule_edit(schedule_id)

@app.route("/schedules/<int:schedule_id>/update", methods=["POST"]) #rename schedule
@role_required("Admin")
def update_schedule(schedule_id):

    get_schedule(schedule_id)

    name = request.form["name"].strip()

    error = validate_schedule_name(name)

    if error:
        return render_schedule_edit(schedule_id, error=error)

    cur = mysql.connection.cursor()
    cur.execute(
        "UPDATE schedules SET name = %s WHERE id = %s",
        (name, schedule_id)
    )
    mysql.connection.commit()
    cur.close()

    return render_schedule_edit(schedule_id, message="Schedule updated")

@app.route("/schedules/<int:schedule_id>/delete", methods=["POST"])
@role_required("Admin")
def delete_schedule(schedule_id):

    get_schedule(schedule_id)

    cur = mysql.connection.cursor()
    # Shifts are removed too because of ON DELETE CASCADE
    cur.execute("DELETE FROM schedules WHERE id = %s", (schedule_id,))
    mysql.connection.commit()
    cur.close()

    return redirect(url_for("schedules"))

@app.route("/schedules/<int:schedule_id>/shifts/add", methods=["POST"])
@role_required("Admin")
def add_shift(schedule_id):

    get_schedule(schedule_id)

    values, error = validate_shift(request.form)

    if error:
        return render_schedule_edit(schedule_id, error=error)

    cur = mysql.connection.cursor()
    cur.execute(
        """
        INSERT INTO shifts (schedule_id, day, start_time, end_time, location, tas_needed)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (schedule_id, *values)
    )
    mysql.connection.commit()
    cur.close()

    return redirect(url_for("edit_schedule", schedule_id=schedule_id))

@app.route("/schedules/<int:schedule_id>/shifts/<int:shift_id>/edit", methods=["POST"])
@role_required("Admin")
def edit_shift(schedule_id, shift_id):

    values, error = validate_shift(request.form)

    if error:
        return render_schedule_edit(schedule_id, error=error)

    cur = mysql.connection.cursor()

    # schedule_id in the WHERE stops editing a shift through another schedule's URL
    cur.execute(
        """
        UPDATE shifts
        SET day = %s, start_time = %s, end_time = %s, location = %s, tas_needed = %s
        WHERE id = %s AND schedule_id = %s
        """,
        (*values, shift_id, schedule_id)
    )
    mysql.connection.commit()
    cur.close()

    return render_schedule_edit(schedule_id, message="Shift updated")

@app.route("/schedules/<int:schedule_id>/shifts/<int:shift_id>/delete", methods=["POST"])
@role_required("Admin")
def delete_shift(schedule_id, shift_id):

    cur = mysql.connection.cursor()
    cur.execute(
        "DELETE FROM shifts WHERE id = %s AND schedule_id = %s",
        (shift_id, schedule_id)
    )
    mysql.connection.commit()
    cur.close()

    return redirect(url_for("edit_schedule", schedule_id=schedule_id))

if __name__ == "__main__":
    app.run(debug=True)