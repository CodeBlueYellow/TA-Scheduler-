import csv
import io
import os
import re
from datetime import datetime
from functools import wraps

from flask import Flask
from flask_mysqldb import MySQL
from flask import render_template, request, redirect, url_for, session, abort, Response
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
# How every query works below: cursor() opens a way to send SQL, execute() runs it,
# fetchone()/fetchall() read the results, commit() saves changes, close() cleans up

# Reject uploads over 1 MB (a schedule CSV is a few KB)
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024

# Put @role_required("Admin") or @role_required("TA") above a route to stop manual address placement
# How it works: decorator() receives the route function, and wrapper() runs in its place
# on every visit. wrapper checks the role first and only then calls the real route
def role_required(role):
    def decorator(view):
        @wraps(view) # keeps the route's real name so url_for() still finds it
        def wrapper(*args, **kwargs):
            # Not logged in, or logged in as the wrong role -> back to login
            if session.get("role") != role:
                return redirect(url_for("login"))
            return view(*args, **kwargs) # role matches, run the actual route
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

            # check_password_hash hashes what they typed and compares it to the stored hash
            if user and check_password_hash(user[2], password):
                session.clear() # wipe any old login before starting a new one
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
        # %s is a placeholder MySQL fills in safely, so typed input can't change the SQL (stops SQL injection)
        cur.execute("SELECT id FROM users WHERE username = %s", (username,))
        taken = cur.fetchone() # None if nobody has that username

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
                (name, username, contact, generate_password_hash(password)) # store a hash, never the real password
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
        SELECT s.id, s.name, COUNT(sh.id), s.created_at, s.status
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

    # DictReader uses the header row as keys, so each row comes out like {"day": "Monday", "start_time": "09:00", ...}
    reader = csv.DictReader(io.StringIO(text))

    # Ignore capitalization and extra spaces in the header row
    headers = [h.strip().lower() for h in (reader.fieldnames or [])]
    missing = [col for col in UPLOAD_COLUMNS if col not in headers]

    if missing:
        return [], ["Missing column(s): " + ", ".join(missing)
                    + ". The first row must be: " + ",".join(UPLOAD_COLUMNS)]

    reader.fieldnames = headers # use the cleaned-up headers as the keys

    shifts = []
    errors = []

    # Row 1 is the header, so the first shift is row 2 (matches Excel's row numbers)
    for row_number, row in enumerate(reader, start=2):

        # Trim spaces from every value. "if key" drops extra cells that have no header above them
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
        schedule_id = cur.lastrowid # the id MySQL just gave the new schedule

        # executemany runs the same INSERT once per shift.
        # *values spreads each shift's tuple out into the remaining columns
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
        mysql.connection.rollback() # something failed, so undo everything since the last commit
        raise
    finally:
        cur.close() # runs whether it worked or not

    return redirect(url_for("edit_schedule", schedule_id=schedule_id))

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

def format_time(value):
    # MySQL TIME columns come back as timedelta; the time input wants "HH:MM"
    # e.g. 9 hours 30 min -> 570 minutes -> "09:30" (:02d pads with a leading zero)
    minutes = int(value.total_seconds()) // 60
    return f"{minutes // 60:02d}:{minutes % 60:02d}"

def parse_time(value):
    # 24-hour from the time inputs, plus 12-hour ("1:30 PM") since Excel often saves times that way
    # Try each format until one works; None means none of them matched
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
        tas_needed = 0 # not a whole number, so make it fail the "at least 1" check below

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
    cur.execute("SELECT id, name, status FROM schedules WHERE id = %s", (schedule_id,))
    schedule = cur.fetchone()
    cur.close()

    if not schedule:
        abort(404) # no schedule with that id -> show "Not Found"

    return schedule # (id, name, status), so schedule[2] is the status

def get_shifts(schedule_id):
    cur = mysql.connection.cursor()

    # FIELD() sorts by weekday order instead of alphabetically
    cur.execute("""
        SELECT id, day, start_time, end_time, location, tas_needed
        FROM shifts
        WHERE schedule_id = %s
        ORDER BY FIELD(day, 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'), start_time
    """, (schedule_id,))

    # Same rows, but with the times turned into "HH:MM" text for the pages.
    # Each shift is (id, day, start, end, location, tas_needed)
    shifts = [
        (row[0], row[1], format_time(row[2]), format_time(row[3]), row[4], row[5])
        for row in cur.fetchall()
    ]

    cur.close()
    return shifts

def render_schedule_edit(schedule_id, error=None, message=None):
    schedule = get_schedule(schedule_id)

    return render_template(
        "schedule_edit.html",
        schedule=schedule,
        shifts=get_shifts(schedule_id),
        days=DAYS,
        locked=schedule[2] in LOCKED_STATUSES,
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

# Schedule life cycle: draft -> open -> filled -> approved
#   draft: only the prof sees it    open: TAs submit availability, prof assigns
#   filled: every shift staffed     approved: final, TAs see their shifts
# Every allowed (current status, new status) pair and the message shown after it.
# open -> filled isn't here: save_assignments sets it, after checking every shift is staffed
STATUS_CHANGES = {
    ("draft", "open"): "Schedule published. TAs can now see it.",
    ("open", "draft"): "Schedule unpublished. TAs can no longer see it.",
    ("filled", "approved"): "Schedule approved.",
    ("filled", "open"): "Schedule rejected. Assignments and shifts can be changed again.",
    ("approved", "open"): "Schedule reopened. Assignments and shifts can be changed again.",
}

# Once every shift is staffed, shifts can't change until the schedule is rejected or reopened
LOCKED_STATUSES = ("filled", "approved")

def set_status(schedule_id, status):
    cur = mysql.connection.cursor()
    cur.execute(
        "UPDATE schedules SET status = %s WHERE id = %s",
        (status, schedule_id)
    )
    mysql.connection.commit()
    cur.close()

@app.route("/schedules/<int:schedule_id>/status", methods=["POST"]) #publish/unpublish/approve/reject/reopen
@role_required("Admin")
def change_schedule_status(schedule_id):

    schedule = get_schedule(schedule_id)

    new_status = request.form["status"]
    change = (schedule[2], new_status) # e.g. ("filled", "approved"), looked up in STATUS_CHANGES

    # Approve/reject/reopen buttons are on the assign page, so answer there;
    # publish/unpublish are on the editor
    on_assign_page = schedule[2] in LOCKED_STATUSES

    if change not in STATUS_CHANGES:
        error = "That status change isn't allowed"
        if on_assign_page:
            return render_assign(schedule_id, errors=[error])
        return render_schedule_edit(schedule_id, error=error)

    if change == ("filled", "approved"):
        # Check again in case something changed since it was marked filled
        problems = fill_problems(schedule_id)
        if problems:
            return render_assign(schedule_id, errors=["The schedule can't be approved yet:"] + problems)

    set_status(schedule_id, new_status)

    if on_assign_page:
        return render_assign(schedule_id, message=STATUS_CHANGES[change])
    return render_schedule_edit(schedule_id, message=STATUS_CHANGES[change])

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

SHIFTS_LOCKED_ERROR = "Shifts can't be changed while the schedule is filled or approved. Reject or reopen it first."

@app.route("/schedules/<int:schedule_id>/shifts/add", methods=["POST"])
@role_required("Admin")
def add_shift(schedule_id):

    schedule = get_schedule(schedule_id)

    if schedule[2] in LOCKED_STATUSES:
        return render_schedule_edit(schedule_id, error=SHIFTS_LOCKED_ERROR)

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

    schedule = get_schedule(schedule_id)

    if schedule[2] in LOCKED_STATUSES:
        return render_schedule_edit(schedule_id, error=SHIFTS_LOCKED_ERROR)

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

    schedule = get_schedule(schedule_id)

    if schedule[2] in LOCKED_STATUSES:
        return render_schedule_edit(schedule_id, error=SHIFTS_LOCKED_ERROR)

    cur = mysql.connection.cursor()
    cur.execute(
        "DELETE FROM shifts WHERE id = %s AND schedule_id = %s",
        (shift_id, schedule_id)
    )
    mysql.connection.commit()
    cur.close()

    return redirect(url_for("edit_schedule", schedule_id=schedule_id))

# Order TAs are listed in when assigning: best fit first
LEVEL_ORDER = {"Preferred": 0, "Available": 1, "No answer": 2, "Unavailable": 3}

def shift_label(shift):
    return f"{shift[1]} {shift[2]}-{shift[3]}"

def get_tas_and_answers(schedule_id):
    # Returns every TA as (id, name), and their answers as {(shift_id, user_id): level}
    cur = mysql.connection.cursor()

    cur.execute("SELECT id, name FROM users WHERE role = 'TA' ORDER BY name")
    tas = cur.fetchall()

    cur.execute(
        """
        SELECT sa.shift_id, sa.user_id, sa.level
        FROM shift_availability sa
        JOIN shifts sh ON sh.id = sa.shift_id
        WHERE sh.schedule_id = %s
        """,
        (schedule_id,)
    )
    # e.g. {(12, 3): "Preferred"} means TA 3 said Preferred for shift 12
    answers = {(shift_id, user_id): level for shift_id, user_id, level in cur.fetchall()}

    cur.close()
    return tas, answers

def get_assignments(schedule_id):
    # {shift_id: set of user_ids} for the schedule's shifts
    cur = mysql.connection.cursor()
    cur.execute(
        """
        SELECT a.shift_id, a.user_id
        FROM assignments a
        JOIN shifts sh ON sh.id = a.shift_id
        WHERE sh.schedule_id = %s
        """,
        (schedule_id,)
    )

    assigned = {}
    for shift_id, user_id in cur.fetchall():
        # setdefault makes an empty set the first time a shift shows up, then we add the TA to it
        assigned.setdefault(shift_id, set()).add(user_id)

    cur.close()
    return assigned

def fill_problems(schedule_id):
    # Everything stopping the schedule from being marked filled; empty means it's ready
    shifts = get_shifts(schedule_id)

    if not shifts:
        return ["The schedule has no shifts"]

    _, answers = get_tas_and_answers(schedule_id) # _ means "ignore this one" (the TA list isn't needed)
    assigned = get_assignments(schedule_id)

    problems = []
    for shift in shifts:
        picked = assigned.get(shift[0], set())
        if len(picked) != shift[5]:
            problems.append(f"{shift_label(shift)} has {len(picked)} of {shift[5]} TAs")
        # A TA can change their answer to Unavailable after being assigned
        if any(answers.get((shift[0], user_id)) == "Unavailable" for user_id in picked):
            problems.append(f"{shift_label(shift)} has a TA who is now Unavailable")

    return problems

def render_assign(schedule_id, errors=None, message=None, assigned=None):
    schedule = get_schedule(schedule_id)
    shifts = get_shifts(schedule_id)
    tas, answers = get_tas_and_answers(schedule_id)

    if assigned is None:
        assigned = get_assignments(schedule_id)

    # For each shift, every TA as (id, name, level). sorted() keeps the
    # alphabetical order within each level, so it's Preferred A-Z, then Available A-Z...
    options = {}
    for shift in shifts:
        # .get(..., "No answer") is used when the TA never answered for this shift
        rows = [(ta_id, name, answers.get((shift[0], ta_id), "No answer")) for ta_id, name in tas]
        options[shift[0]] = sorted(rows, key=lambda row: LEVEL_ORDER[row[2]])

    return render_template(
        "schedule_assign.html",
        schedule=schedule,
        locked=schedule[2] in LOCKED_STATUSES,
        shifts=shifts,
        options=options,
        assigned=assigned,
        responded=len({user_id for _, user_id in answers}), # a set counts each TA once, however many shifts they answered
        ta_count=len(tas),
        errors=errors,
        message=message
    )

@app.route("/schedules/<int:schedule_id>/assign") #admin assigns TAs to shifts
@role_required("Admin")
def assign_schedule(schedule_id):
    return render_assign(schedule_id)

@app.route("/schedules/<int:schedule_id>/assign", methods=["POST"])
@role_required("Admin")
def save_assignments(schedule_id):

    schedule = get_schedule(schedule_id)

    if schedule[2] != "open":
        return render_assign(schedule_id, errors=["Assignments can only be changed while the schedule is open"])

    shifts = get_shifts(schedule_id)
    tas, answers = get_tas_and_answers(schedule_id)
    ta_ids = {ta_id for ta_id, _ in tas}

    # Only read checkboxes for this schedule's shifts, and only real TA ids
    assigned = {}
    errors = []
    for shift in shifts:
        # getlist returns every checked box for this shift. Each box's value is a TA id,
        # so keep only real numbers that belong to real TAs
        picked = {int(value) for value in request.form.getlist(f"assign-{shift[0]}")
                  if value.isdigit() and int(value) in ta_ids}
        assigned[shift[0]] = picked

        if any(answers.get((shift[0], user_id)) == "Unavailable" for user_id in picked):
            errors.append(f"{shift_label(shift)}: uncheck TAs marked Unavailable")
        if len(picked) > shift[5]:
            errors.append(f"{shift_label(shift)} needs {shift[5]} TAs but {len(picked)} are checked")

    if errors:
        # Pass the checkboxes back so the prof doesn't lose their picks
        return render_assign(schedule_id, errors=errors, assigned=assigned)

    cur = mysql.connection.cursor()

    try:
        # Replace the whole schedule's assignments, so unchecked boxes are removed
        cur.execute(
            """
            DELETE FROM assignments
            WHERE shift_id IN (SELECT id FROM shifts WHERE schedule_id = %s)
            """,
            (schedule_id,)
        )
        # Turn {shift: {TAs}} into one (shift_id, user_id) row per checked box
        cur.executemany(
            "INSERT INTO assignments (shift_id, user_id) VALUES (%s, %s)",
            [(shift_id, user_id) for shift_id, picked in assigned.items() for user_id in picked]
        )

        # One commit for the delete and inserts, so it's all or nothing
        mysql.connection.commit()
    except Exception:
        mysql.connection.rollback()
        raise
    finally:
        cur.close()

    # mark_filled is only sent when the "Save & Mark as Filled" button was clicked
    if not request.form.get("mark_filled"):
        return render_assign(schedule_id, message="Assignments saved")

    problems = fill_problems(schedule_id)

    if problems:
        return render_assign(schedule_id, errors=["Assignments saved, but the schedule can't be marked filled yet:"] + problems)

    set_status(schedule_id, "filled")

    return render_assign(schedule_id, message="Schedule marked as filled. Assignments are now locked.")

def csv_safe(value):
    # Excel runs cells starting with these as formulas, and names/locations are typed
    # in by users, so a leading ' makes Excel show them as plain text instead
    if value and value[0] in "=+-@":
        return "'" + value
    return value

@app.route("/schedules/<int:schedule_id>/export.csv") #admin downloads a completed schedule
@role_required("Admin")
def export_schedule(schedule_id):

    schedule = get_schedule(schedule_id)

    # Before it's filled, assignments are still changing, so an export would mislead
    if schedule[2] not in LOCKED_STATUSES:
        return render_assign(schedule_id, errors=["Only filled or approved schedules can be exported"])

    tas, _ = get_tas_and_answers(schedule_id)
    names = dict(tas) # {id: name} so we can look up each assigned TA's name
    assigned = get_assignments(schedule_id)

    # StringIO is a file that lives in memory, so nothing gets saved to disk
    output = io.StringIO()
    writer = csv.writer(output) # handles quoting for us (e.g. names with commas)

    # Same columns as the upload template plus assigned_tas, which upload ignores,
    # so an export can be uploaded again as next term's starting schedule
    writer.writerow(UPLOAD_COLUMNS + ["assigned_tas"])

    for shift in get_shifts(schedule_id):
        ta_names = sorted(names[user_id] for user_id in assigned.get(shift[0], set()))
        writer.writerow([
            shift[1], shift[2], shift[3],
            csv_safe(shift[4] or ""),
            shift[5],
            "; ".join(csv_safe(name) for name in ta_names)
        ])

    # Letters, numbers, - and _ only, so the name is safe in the download header
    filename = re.sub(r"[^A-Za-z0-9_-]+", "_", schedule[1]).strip("_") or "schedule"

    # utf-8-sig adds the marker Excel needs to show accented names correctly.
    # "attachment" in the header makes the browser download the file instead of showing it
    return Response(
        output.getvalue().encode("utf-8-sig"),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'}
    )

# Statuses a TA is allowed to see; drafts stay private to the prof.
# TAs can only submit availability while a schedule is open
TA_VISIBLE_STATUSES = ("open", "filled", "approved")

# What each status means from a TA's point of view
TA_STATUS_LABELS = {
    "open": "Taking availability",
    "filled": "Being finalized",
    "approved": "Approved",
}

@app.route("/ta/schedules") #TA views published schedules
@role_required("TA")
def ta_schedules():

    cur = mysql.connection.cursor()
    cur.execute(
        """
        SELECT s.id, s.name, COUNT(sh.id), s.status
        FROM schedules s
        LEFT JOIN shifts sh ON sh.schedule_id = s.id
        WHERE s.status IN %s
        GROUP BY s.id
        ORDER BY s.created_at DESC
        """,
        (TA_VISIBLE_STATUSES,) # the tuple fills in as ('open', 'filled', 'approved')
    )
    data = cur.fetchall()
    cur.close()

    return render_template("ta_schedules.html", schedules=data, status_labels=TA_STATUS_LABELS)

AVAILABILITY_LEVELS = ["Unavailable", "Available", "Preferred"]

def get_ta_schedule(schedule_id):
    schedule = get_schedule(schedule_id)

    # 404 instead of "not allowed" so TAs can't tell which draft IDs exist
    if schedule[2] not in TA_VISIBLE_STATUSES:
        abort(404)

    return schedule

# answers is passed in when re-showing the form after an error, so the TA's picks
# aren't lost; otherwise it's loaded from the database
def render_ta_schedule(schedule_id, error=None, message=None, answers=None):
    schedule = get_ta_schedule(schedule_id)

    if answers is None:
        # This TA's saved answers for this schedule, as {shift_id: level}
        cur = mysql.connection.cursor()
        cur.execute(
            """
            SELECT sa.shift_id, sa.level
            FROM shift_availability sa
            JOIN shifts sh ON sh.id = sa.shift_id
            WHERE sa.user_id = %s AND sh.schedule_id = %s
            """,
            (session["user_id"], schedule_id)
        )
        answers = dict(cur.fetchall()) # turns [(12, "Preferred"), ...] into {12: "Preferred", ...}
        cur.close()

    # Assignments can still change until the prof approves, so only show them after that
    my_shifts = set()
    if schedule[2] == "approved":
        my_shifts = {shift_id for shift_id, users in get_assignments(schedule_id).items()
                     if session["user_id"] in users}

    return render_template(
        "ta_schedule.html",
        schedule=schedule,
        shifts=get_shifts(schedule_id),
        levels=AVAILABILITY_LEVELS,
        answers=answers,
        my_shifts=my_shifts,
        status_label=TA_STATUS_LABELS[schedule[2]],
        error=error,
        message=message
    )

@app.route("/ta/schedules/<int:schedule_id>") #TA views one schedule and their availability
@role_required("TA")
def ta_schedule(schedule_id):
    return render_ta_schedule(schedule_id)

@app.route("/ta/schedules/<int:schedule_id>/availability", methods=["POST"]) #TA submits availability
@role_required("TA")
def submit_availability(schedule_id):

    schedule = get_ta_schedule(schedule_id)

    if schedule[2] != "open":
        return render_ta_schedule(schedule_id, error="This schedule is no longer taking availability")

    # Only read answers for shifts that really belong to this schedule,
    # so a tampered form can't write to another schedule's shifts
    shifts = get_shifts(schedule_id)

    answers = {}
    for shift in shifts:
        level = request.form.get(f"shift-{shift[0]}")
        if level in AVAILABILITY_LEVELS:
            answers[shift[0]] = level

    if len(answers) < len(shifts):
        # Pass the answers back so the TA doesn't lose what they picked
        return render_ta_schedule(schedule_id, error="Choose an answer for every shift", answers=answers)

    cur = mysql.connection.cursor()
    # The (user_id, shift_id) primary key turns a resubmission into an update.
    # If the row already exists, ON DUPLICATE KEY UPDATE changes its level instead of
    # erroring. "AS new" names the row we tried to insert, so new.level is the new answer
    cur.executemany(
        """
        INSERT INTO shift_availability (user_id, shift_id, level)
        VALUES (%s, %s, %s) AS new
        ON DUPLICATE KEY UPDATE level = new.level
        """,
        [(session["user_id"], shift_id, level) for shift_id, level in answers.items()]
    )
    mysql.connection.commit()
    cur.close()

    return render_ta_schedule(schedule_id, message="Availability saved")

@app.route("/ta/my-shifts") #TA views their assigned shifts
@role_required("TA")
def my_shifts():

    cur = mysql.connection.cursor()

    # Start from this TA's assignments, then JOIN to shifts (for times) and schedules (for the name).
    # Only approved schedules, since assignments can still change before that
    cur.execute(
        """
        SELECT s.id, s.name, sh.day, sh.start_time, sh.end_time, sh.location
        FROM assignments a
        JOIN shifts sh ON sh.id = a.shift_id
        JOIN schedules s ON s.id = sh.schedule_id
        WHERE a.user_id = %s AND s.status = 'approved'
        ORDER BY s.name, FIELD(sh.day, 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'), sh.start_time
        """,
        (session["user_id"],)
    )

    shifts = [
        (row[0], row[1], row[2], format_time(row[3]), format_time(row[4]), row[5])
        for row in cur.fetchall()
    ]

    cur.close()

    return render_template("my_shifts.html", shifts=shifts)

if __name__ == "__main__":
    app.run(debug=True)