import calendar
import csv
import io
import os
import re
import time
from datetime import datetime
from functools import wraps
from itertools import combinations

from dotenv import load_dotenv
from flask import Flask
from flask_mysqldb import MySQL
from flask_wtf.csrf import CSRFError, CSRFProtect
from flask import render_template, request, redirect, url_for, session, abort, Response
from werkzeug.security import generate_password_hash, check_password_hash

# Reads .env into os.environ, so passwords stay out of the code and out of git
# Looks next to app.py, so it's found even when a web host starts the app from another folder
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

def required_setting(name):
    # Stop right away with a clear message, instead of failing later on some random page
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is missing from .env. See .env.example for what to put there.")
    return value

app = Flask(__name__)

# Secret key signs the session cookie so users can't edit it
app.secret_key = required_setting("SECRET_KEY")

# Lax stops other sites from sending this cookie along with their form posts
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Every POST must now carry the csrf_token from its form, or it's refused with a 400.
# A token normally expires after an hour, which would throw away a form left open that long,
# so None keeps it good until the browser session ends
app.config['WTF_CSRF_TIME_LIMIT'] = None
csrf = CSRFProtect(app)

# MySQL stuff. ta_app can only read and change rows, not drop or alter tables
# Host and database name only need setting when hosted (PythonAnywhere uses its own names)
app.config['MYSQL_HOST'] = os.environ.get("MYSQL_HOST") or 'localhost'
app.config['MYSQL_USER'] = required_setting("MYSQL_USER")
app.config['MYSQL_PASSWORD'] = required_setting("MYSQL_PASSWORD")
app.config['MYSQL_DB'] = os.environ.get("MYSQL_DB") or 'ta_scheduler'

# The admin account stays hard coded, but only its hash is stored. Set by set_admin_password.py
ADMIN_PASSWORD_HASH = required_setting("ADMIN_PASSWORD_HASH")

mysql = MySQL(app)
# How every query works below: cursor() opens a way to send SQL, execute() runs it,

# Reject uploads over 1 MB (a schedule CSV is a few KB)
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024

# Put @role_required("Admin") or @role_required("TA") above a route to stop manual address placement
# How it works: decorator() receives the route function, and wrapper() runs in its place
# on every visit. wrapper checks the role first and only then calls the real route
def role_required(role):
    def decorator(view):
        @wraps(view) # keeps the route's real name so url_for() still finds it
        def wrapper(*args, **kwargs):
            if session.get("role") != role:
                return redirect(url_for("login"))
            return view(*args, **kwargs) # role matches, run the actual route
        return wrapper
    return decorator

@app.errorhandler(CSRFError)
def csrf_failed(error):
    return render_template("expired.html"), 400

# After this many wrong passwords, that login is blocked until the failures are old enough
MAX_LOGIN_FAILURES = 5
LOGIN_BLOCK_SECONDS = 300

# {(address, username): [times of recent failures]}. Kept in memory, so a restart clears it
failed_logins = {}

def recent_failures(attempt):
    cutoff = time.time() - LOGIN_BLOCK_SECONDS
    failures = [failed_at for failed_at in failed_logins.get(attempt, []) if failed_at > cutoff]

    if failures:
        failed_logins[attempt] = failures
    else:
        failed_logins.pop(attempt, None) # nothing recent, so stop tracking it

    return failures

@app.route("/", methods=["GET", "POST"]) #Login stuff
def login():

    error = None

    if request.method == "POST":

        username = request.form["username"]
        password = request.form["password"]
        role = request.form["role"]

        # Address and username together, so one person's typos can't block everyone on campus
        attempt = (request.remote_addr, username.lower())

        if len(recent_failures(attempt)) >= MAX_LOGIN_FAILURES:
            return render_template("login.html", error="Too many failed attempts. Try again in a few minutes.")

        #Hard coded Admin account for Prof Brown
        if role == "Admin":

            if username == "admin" and check_password_hash(ADMIN_PASSWORD_HASH, password):
                failed_logins.pop(attempt, None)
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
                failed_logins.pop(attempt, None)
                session.clear() # wipe any old login before starting a new one
                session["user_id"] = user[0]
                session["name"] = user[1]
                session["username"] = username
                session["role"] = "TA"
                return redirect(url_for("ta"))

        failed_logins.setdefault(attempt, []).append(time.time())

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
    all_schedules = get_schedules()
    in_progress = [schedule for schedule in all_schedules if schedule[4] != "approved"]

    cur = mysql.connection.cursor()
    cur.execute("SELECT COUNT(*) FROM users WHERE role = 'TA'")
    ta_count = cur.fetchone()[0]
    cur.close()

    return render_template(
        "admin.html",
        in_progress=len(in_progress),
        approved=len(all_schedules) - len(in_progress),
        ta_count=ta_count,
        short=sum(shifts_short(schedule[0]) for schedule in in_progress if schedule[4] == "open"),
        tasks=admin_tasks(all_schedules, in_progress, ta_count)
    )

@app.route("/ta") #TA dash
@role_required("TA")
def ta():
    cur = mysql.connection.cursor()

    # Each open schedule with how many of its shifts this TA hasn't answered.
    # LEFT JOIN keeps unanswered shifts, and COUNT(sa.shift_id) skips them
    cur.execute(
        """
        SELECT s.id, s.name, COUNT(sh.id) - COUNT(sa.shift_id)
        FROM schedules s
        JOIN shifts sh ON sh.schedule_id = s.id
        LEFT JOIN shift_availability sa ON sa.shift_id = sh.id AND sa.user_id = %s
        WHERE s.status = 'open'
        GROUP BY s.id
        ORDER BY s.created_at DESC
        """,
        (session["user_id"],)
    )
    waiting = [row for row in cur.fetchall() if row[2] > 0]

    # Same rule as My Shifts: only approved schedules count
    cur.execute(
        """
        SELECT COUNT(*)
        FROM assignments a
        JOIN shifts sh ON sh.id = a.shift_id
        JOIN schedules s ON s.id = sh.schedule_id
        WHERE a.user_id = %s AND s.status = 'approved'
        """,
        (session["user_id"],)
    )
    shift_count = cur.fetchone()[0]

    cur.close()

    return render_template("ta.html", waiting=waiting, shift_count=shift_count)

def render_users(error=None, message=None):
    cur = mysql.connection.cursor()

    # List columns explicitly so password hashes never reach the page
    cur.execute("SELECT id, name, username, contact, role FROM users")

    data = cur.fetchall()

    cur.close()

    return render_template(
        "users.html",
        users=data,
        error=error,
        message=message
    )

@app.route("/users")
@role_required("Admin")
def users():
    return render_users()

@app.route("/users/<int:user_id>/delete", methods=["POST"]) #admin deletes a TA account
@role_required("Admin")
def delete_user(user_id):

    cur = mysql.connection.cursor()

    # role = 'TA' so this can only ever remove TA accounts
    cur.execute("SELECT name FROM users WHERE id = %s AND role = 'TA'", (user_id,))
    user = cur.fetchone()

    if not user:
        cur.close()
        abort(404)

    # Deleting removes the TA's assignments too, which would leave a finished schedule short.
    # So schedules that are filled or approved have to be rejected/reopened first
    cur.execute(
        f"""
        SELECT DISTINCT s.name
        FROM assignments a
        JOIN shifts sh ON sh.id = a.shift_id
        JOIN schedules s ON s.id = sh.schedule_id
        WHERE a.user_id = %s AND s.status IN ({", ".join(["%s"] * len(LOCKED_STATUSES))})
        ORDER BY s.name
        """,
        (user_id, *LOCKED_STATUSES)
    )
    locked = [row[0] for row in cur.fetchall()]

    if locked:
        cur.close()
        which = "that schedule" if len(locked) == 1 else "those schedules"
        return render_users(error=(
            f"{user[0]} can't be deleted because they're assigned to {', '.join(locked)}. "
            f"Reject or reopen {which} first, then try again."
        ))

    # Their availability answers and assignments are removed too because of ON DELETE CASCADE
    cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
    mysql.connection.commit()
    cur.close()

    return render_users(message=f"Deleted {user[0]}.")

def get_schedules():
    cur = mysql.connection.cursor()

    # LEFT JOIN so empty schedules still show up with 0 shifts
    cur.execute("""
        SELECT s.id, s.name, COUNT(sh.id), s.created_at, s.status, s.start_date, s.end_date
        FROM schedules s
        LEFT JOIN shifts sh ON sh.schedule_id = s.id
        GROUP BY s.id
        ORDER BY s.created_at DESC
    """)

    data = cur.fetchall()
    cur.close()
    return data

def parse_date(value):
    # Date inputs send "YYYY-MM-DD"; None means blank or not a real date
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None

def validate_dates(form):
    # Returns (start, end, error). Dates are optional, but it's both or neither
    start_text = form.get("start_date", "").strip()
    end_text = form.get("end_date", "").strip()

    if not start_text and not end_text:
        return None, None, None

    start = parse_date(start_text)
    end = parse_date(end_text)

    if not start or not end:
        return None, None, "Enter both a start and end date, or leave both blank"
    if start >= end:
        return None, None, "Start date must be before end date"

    return start, end, None

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

# Lab: a TA's one section for the semester. Help Session: a weekly walk-in session
SHIFT_TYPES = ["Lab", "Help Session"]

# The inputs in one row of the shift table, in the same order as a shift's values
SHIFT_FIELDS = UPLOAD_COLUMNS + ["shift_type"]

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
        # shift_type is optional so older files still upload; their shifts count as labs
        row["shift_type"] = row.get("shift_type", "").title() or "Lab"

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
            INSERT INTO shifts (schedule_id, day, start_time, end_time, location, tas_needed, shift_type)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
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

@app.template_filter("clock")
def clock(value):
    # "HH:MM" -> "1:30 PM" for showing on pages. The 24-hour text stays everywhere else,
    # since the time inputs need it and it sorts/compares correctly
    hour, minute = map(int, value.split(":")[:2])
    return f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"

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
    shift_type = form.get("shift_type", "")

    try:
        tas_needed = int(form.get("tas_needed", ""))
    except ValueError:
        tas_needed = 0 # not a whole number, so make it fail the "at least 1" check below

    if shift_type not in SHIFT_TYPES:
        return None, "Shift type must be Lab or Help Session"
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

    return (day, start, end, location, tas_needed, shift_type), None

def get_schedule(schedule_id):
    cur = mysql.connection.cursor()
    cur.execute("SELECT id, name, status, start_date, end_date FROM schedules WHERE id = %s", (schedule_id,))
    schedule = cur.fetchone()
    cur.close()

    if not schedule:
        abort(404) # no schedule with that id -> show "Not Found"

    return schedule # (id, name, status, start_date, end_date), so schedule[2] is the status

def get_shifts(schedule_id):
    cur = mysql.connection.cursor()

    # FIELD() sorts by weekday order instead of alphabetically
    cur.execute("""
        SELECT id, day, start_time, end_time, location, tas_needed, shift_type
        FROM shifts
        WHERE schedule_id = %s
        ORDER BY FIELD(day, 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'), start_time
    """, (schedule_id,))

    # Same rows, but with the times turned into "HH:MM" text for the pages.
    # Each shift is (id, day, start, end, location, tas_needed, shift_type)
    shifts = [
        (row[0], row[1], format_time(row[2]), format_time(row[3]), row[4], row[5], row[6])
        for row in cur.fetchall()
    ]

    cur.close()
    return shifts

def shift_fields(form, key):
    # Every input in a shift's row has a name ending with the shift's id
    return {field: form.get(f"{field}-{key}", "") for field in SHIFT_FIELDS}

def new_shift_rows(form):
    # Every new row uses the same input names, so getlist returns one list per column
    # with the rows in page order. zip turns those columns back into rows
    columns = [form.getlist(f"{field}-new") for field in SHIFT_FIELDS]
    rows = [dict(zip(SHIFT_FIELDS, values)) for values in zip(*columns)]

    # A row only counts if something was typed into it.
    # Its day, type, and TA count always have a value, so they can't tell us
    return [row for row in rows if row["start_time"] or row["end_time"] or row["location"].strip()]

# form is passed in when re-showing the page after an error, so nothing typed is lost
def render_schedule_edit(schedule_id, error=None, message=None, form=None):
    schedule = get_schedule(schedule_id)
    shifts = get_shifts(schedule_id)

    details = (schedule[1], schedule[3], schedule[4])
    new_rows = []

    if form:
        details = (form.get("name", ""), form.get("start_date", ""), form.get("end_date", ""))
        new_rows = new_shift_rows(form)

        # Locked shifts send no inputs, so they keep their saved values
        if schedule[2] not in LOCKED_STATUSES:
            shifts = [(shift[0], *shift_fields(form, shift[0]).values()) for shift in shifts]

    return render_template(
        "schedule_edit.html",
        schedule=schedule,
        details=details,
        shifts=shifts,
        new_rows=new_rows,
        days=DAYS,
        shift_types=SHIFT_TYPES,
        shift_months=SHIFT_MONTHS,
        locked=schedule[2] in LOCKED_STATUSES,
        error=error,
        message=message
    )

@app.route("/schedules/<int:schedule_id>") #admin edits one schedule
@role_required("Admin")
def edit_schedule(schedule_id):
    # ?saved=1 is added by the redirect after a successful save
    return render_schedule_edit(schedule_id, message="Changes saved" if request.args.get("saved") else None)

@app.route("/schedules/<int:schedule_id>/save", methods=["POST"]) #one Save for the name, dates, and all shifts
@role_required("Admin")
def save_schedule(schedule_id):

    schedule = get_schedule(schedule_id)

    name = request.form["name"].strip()
    start_date, end_date, date_error = validate_dates(request.form)

    error = validate_schedule_name(name) or date_error

    updates = []
    deletes = []
    inserts = []

    # Dates and the name can always change, but shifts stay locked once the schedule is filled
    if schedule[2] not in LOCKED_STATUSES:

        for number, shift in enumerate(get_shifts(schedule_id), start=1):

            # A Delete button sends its shift's id; that row doesn't need to be valid
            if request.form.get("delete_shift") == str(shift[0]):
                deletes.append((shift[0], schedule_id))
                continue

            values, shift_error = validate_shift(shift_fields(request.form, shift[0]))

            if shift_error:
                error = error or f"Shift {number}: {shift_error}"
            else:
                updates.append((*values, shift[0], schedule_id))

        for number, row in enumerate(new_shift_rows(request.form), start=1):
            values, shift_error = validate_shift(row)

            if shift_error:
                error = error or f"New shift {number}: {shift_error}"
            else:
                inserts.append((schedule_id, *values))

    # Nothing is saved unless everything is valid
    if error:
        return render_schedule_edit(schedule_id, error=error, form=request.form)

    cur = mysql.connection.cursor()

    try:
        cur.execute(
            "UPDATE schedules SET name = %s, start_date = %s, end_date = %s WHERE id = %s",
            (name, start_date, end_date, schedule_id)
        )

        # schedule_id in the WHERE keeps these changes inside this schedule
        cur.executemany(
            """
            UPDATE shifts
            SET day = %s, start_time = %s, end_time = %s, location = %s, tas_needed = %s, shift_type = %s
            WHERE id = %s AND schedule_id = %s
            """,
            updates
        )
        cur.executemany("DELETE FROM shifts WHERE id = %s AND schedule_id = %s", deletes)

        cur.executemany(
            """
            INSERT INTO shifts (schedule_id, day, start_time, end_time, location, tas_needed, shift_type)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            inserts
        )

        # One commit for everything, so it's all or nothing
        mysql.connection.commit()
    except Exception:
        mysql.connection.rollback()
        raise
    finally:
        cur.close()

    # Redirect so refreshing the page can't add the new shifts a second time
    return redirect(url_for("edit_schedule", schedule_id=schedule_id, saved=1))

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

# How far the prof can move a schedule's dates when copying it
SHIFT_MONTHS = [1, 5, 8, 12]

def add_months(day, months):
    # Count months from year 0 so going past December rolls into the next year
    total = day.year * 12 + day.month - 1 + months
    year, month = total // 12, total % 12 + 1

    # Jan 31 + 1 month has no Feb 31, so stop at the last day of the new month.
    # monthrange returns (first weekday, number of days)
    last_day = calendar.monthrange(year, month)[1]

    return day.replace(year=year, month=month, day=min(day.day, last_day))

def new_dates(schedule, form):
    # Returns (start, end, error) for a copy of the schedule
    shift_by = form.get("shift_by", "")

    if shift_by == "custom":
        start, end, error = validate_dates(form)
        # validate_dates allows two blanks, but a copy has to end up with dates
        if not start and not error:
            error = "Enter a start and end date for the new schedule"
        return start, end, error

    if not shift_by.isdigit() or int(shift_by) not in SHIFT_MONTHS:
        return None, None, "Choose how far to move the dates"
    if not schedule[3]:
        return None, None, "This schedule has no dates to move. Enter new dates instead."

    months = int(shift_by)
    return add_months(schedule[3], months), add_months(schedule[4], months), None

@app.route("/schedules/<int:schedule_id>/copy", methods=["POST"]) #admin copies a schedule to new dates
@role_required("Admin")
def copy_schedule(schedule_id):

    schedule = get_schedule(schedule_id)

    name = request.form["name"].strip()
    start_date, end_date, date_error = new_dates(schedule, request.form)

    error = validate_schedule_name(name) or date_error

    if error:
        return render_schedule_edit(schedule_id, error=error)

    cur = mysql.connection.cursor()

    try:
        # status isn't listed, so the copy starts as a draft
        cur.execute(
            "INSERT INTO schedules (name, start_date, end_date) VALUES (%s, %s, %s)",
            (name, start_date, end_date)
        )
        new_id = cur.lastrowid

        # INSERT ... SELECT copies the rows inside MySQL. Availability and assignments
        # are left behind because each term has different TAs and class schedules
        cur.execute(
            """
            INSERT INTO shifts (schedule_id, day, start_time, end_time, location, tas_needed, shift_type)
            SELECT %s, day, start_time, end_time, location, tas_needed, shift_type
            FROM shifts
            WHERE schedule_id = %s
            """,
            (new_id, schedule_id)
        )

        # One commit for the schedule and its shifts, so it's all or nothing
        mysql.connection.commit()
    except Exception:
        mysql.connection.rollback()
        raise
    finally:
        cur.close()

    return redirect(url_for("edit_schedule", schedule_id=new_id))

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

# Order TAs are listed in when assigning: best fit first
LEVEL_ORDER = {"Preferred": 0, "Available": 1, "No answer": 2, "Unavailable": 3}

def shift_label(shift):
    return f"{shift[1]} {clock(shift[2])}-{clock(shift[3])}"

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

def find_conflicts(shifts, assigned, tas, answers):
    # Returns a list of (text, shift ids), one for each conflict.
    # Two help sessions isn't a conflict: the prof may need that when few TAs are free
    conflicts = []

    for ta_id, name in tas:
        mine = [shift for shift in shifts if ta_id in assigned.get(shift[0], ())]

        # A TA can change their answer to Unavailable after being assigned
        for shift in mine:
            if answers.get((shift[0], ta_id)) == "Unavailable":
                conflicts.append((f"{name} is now Unavailable for {shift_label(shift)}", [shift[0]]))

        # combinations gives every pair of this TA's shifts once
        for first, second in combinations(mine, 2):
            pair = f"{shift_label(first)} and {shift_label(second)}"

            if shifts_overlap(first, second):
                conflicts.append((f"{name} has overlapping shifts: {pair}", [first[0], second[0]]))
            elif first[6] == "Lab" and second[6] == "Lab":
                conflicts.append((f"{name} has two labs: {pair}", [first[0], second[0]]))

    return conflicts

def fill_problems(schedule_id):
    # Everything stopping the schedule from being marked filled; empty means it's ready
    shifts = get_shifts(schedule_id)

    if not shifts:
        return ["The schedule has no shifts"]

    tas, answers = get_tas_and_answers(schedule_id)
    assigned = get_assignments(schedule_id)

    problems = []
    for shift in shifts:
        picked = assigned.get(shift[0], set())
        if len(picked) != shift[5]:
            problems.append(f"{shift_label(shift)} has {len(picked)} of {shift[5]} TAs")

    # The _ ignores the shift ids, which only the assign page needs
    return problems + [text for text, _ in find_conflicts(shifts, assigned, tas, answers)]

def shifts_short(schedule_id):
    # How many of the schedule's shifts have fewer TAs than they need
    assigned = get_assignments(schedule_id)
    return sum(1 for shift in get_shifts(schedule_id) if len(assigned.get(shift[0], ())) < shift[5])

def next_step(schedule):
    # What the prof should do next on an unfinished schedule, as (text, url, link text)
    schedule_id, name, shift_count, _, status = schedule[:5]
    edit_url = url_for("edit_schedule", schedule_id=schedule_id)
    assign_url = url_for("assign_schedule", schedule_id=schedule_id)

    if status == "draft" and shift_count == 0:
        return f"Add shifts to {name}", edit_url, "Add Shifts"
    if status == "draft":
        return f"Publish {name} so TAs can submit availability", edit_url, "Open Schedule"
    if status == "filled":
        return f"Approve or reject {name}", assign_url, "Review"

    if too_many_help_times(get_shifts(schedule_id)):
        return f"Choose the help session times for {name}", url_for("best_times", schedule_id=schedule_id), "Best Times"

    if fill_problems(schedule_id):
        tas, answers = get_tas_and_answers(schedule_id)
        responded = len({user_id for _, user_id in answers})
        return f"Assign TAs to {name} ({responded} of {len(tas)} TAs have responded)", assign_url, "Assign TAs"

    return f"Mark {name} as filled", assign_url, "Review"

def admin_tasks(all_schedules, in_progress, ta_count):
    # The dashboard to-do list, as (text, url, link text, status). Status is None for app-wide tasks
    tasks = []

    if get_join_code() == "CHANGE-ME":
        tasks.append(("Change the default join code before sharing it", url_for("join_code"), "Change Code", None))
    elif ta_count == 0:
        tasks.append(("Share the join code so TAs can register", url_for("join_code"), "View Code", None))

    if not all_schedules:
        tasks.append(("Create your first schedule", url_for("schedules"), "Get Started", None))

    for schedule in in_progress:
        tasks.append((*next_step(schedule), schedule[4]))

    return tasks

# Auto-fill stops after this many tries and keeps the best answer it found.
# Only a huge schedule gets near this; it keeps the page from hanging
MAX_FILL_STEPS = 200000

def shifts_overlap(a, b):
    # "HH:MM" text sorts in time order, so < works here.
    # Two shifts overlap when each one starts before the other ends
    return a[1] == b[1] and a[2] < b[3] and b[2] < a[3]

def can_take(shift, held):
    # held is the shifts a TA already has. A TA gets at most one of each type
    # (one lab, one help session, so 3-5 hours a week) and never two at once
    return not any(other[6] == shift[6] or shifts_overlap(shift, other) for other in held)

def auto_fill(shifts, tas, answers):
    # Picks TAs for every shift and returns {shift_id: set of user_ids}.
    # Tries the possible combinations and keeps the best one, where best means:
    #   1. the most spots filled   2. the most Preferred picks
    #   3. the fewest TAs with two shifts, so the work is spread out

    # Who said yes to each shift, best answers first. No answer doesn't count as a yes
    willing = {}
    for shift in shifts:
        rows = [(ta_id, answers.get((shift[0], ta_id))) for ta_id, _ in tas]
        rows = [row for row in rows if row[1] in ("Preferred", "Available")]
        willing[shift[0]] = sorted(rows, key=lambda row: LEVEL_ORDER[row[1]])

    # One entry per TA needed, hardest shifts first, because they have the fewest options.
    # min() is there because a shift can't use more TAs than said yes to it
    spots = []
    for shift in sorted(shifts, key=lambda shift: len(willing[shift[0]])):
        spots.extend([shift] * min(shift[5], len(willing[shift[0]])))

    held = {ta_id: [] for ta_id, _ in tas} # the shifts each TA has so far
    picks = [] # the (shift_id, ta_id) pairs chosen so far
    best_score = (-1, 0, 0)
    best_picks = []
    steps = 0

    # Backtracking: try a TA for this spot, work through the remaining spots,
    # then undo that choice and try the next TA
    def search(index, filled, preferred, doubled):
        nonlocal best_score, best_picks, steps
        steps += 1

        # Tuples compare left to right, which matches the order of the rules above.
        # This is the best this path could still reach; if that can't beat
        # the best answer so far, there's no point going further
        left = len(spots) - index
        if steps > MAX_FILL_STEPS or (filled + left, preferred + left, -doubled) <= best_score:
            return

        if left == 0:
            best_score = (filled, preferred, -doubled)
            best_picks = list(picks)
            return

        shift = spots[index]

        for ta_id, level in willing[shift[0]]:
            if can_take(shift, held[ta_id]):
                has_shift = len(held[ta_id]) > 0

                held[ta_id].append(shift)
                picks.append((shift[0], ta_id))
                search(index + 1, filled + 1, preferred + (level == "Preferred"), doubled + has_shift)
                picks.pop() # undo, so the next TA starts from the same place
                held[ta_id].pop()

        # Also try leaving this spot empty, since that can free a TA for a harder shift
        search(index + 1, filled, preferred, doubled)

    search(0, 0, 0, 0)

    assigned = {}
    for shift_id, ta_id in best_picks:
        assigned.setdefault(shift_id, set()).add(ta_id)

    return assigned

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

    conflicts = find_conflicts(shifts, assigned, tas, answers)

    # {shift_id: conflict texts}, so each row can show its own. A pair shows on both of its rows
    conflict_notes = {}
    for text, shift_ids in conflicts:
        for shift_id in shift_ids:
            conflict_notes.setdefault(shift_id, []).append(text)

    return render_template(
        "schedule_assign.html",
        schedule=schedule,
        locked=schedule[2] in LOCKED_STATUSES,
        shifts=shifts,
        options=options,
        assigned=assigned,
        conflict_count=len(conflicts),
        conflict_notes=conflict_notes,
        too_many_help=too_many_help_times(shifts),
        responded=len({user_id for _, user_id in answers}), # a set counts each TA once, however many shifts they answered
        ta_count=len(tas),
        errors=errors,
        message=message
    )

@app.route("/schedules/<int:schedule_id>/assign") #admin assigns TAs to shifts
@role_required("Admin")
def assign_schedule(schedule_id):
    return render_assign(schedule_id)

@app.route("/schedules/<int:schedule_id>/autofill", methods=["POST"]) #admin asks for suggested assignments
@role_required("Admin")
def autofill_schedule(schedule_id):

    schedule = get_schedule(schedule_id)

    if schedule[2] != "open":
        return render_assign(schedule_id, errors=["Auto-fill only works while the schedule is open"])

    shifts = get_shifts(schedule_id)
    tas, answers = get_tas_and_answers(schedule_id)

    assigned = auto_fill(shifts, tas, answers)

    short = [f"{shift_label(shift)} has {len(assigned.get(shift[0], ()))} of {shift[5]} TAs"
             for shift in shifts if len(assigned.get(shift[0], ())) < shift[5]]

    # Nothing is written here: the picks go back as checked boxes for the prof to review and save
    return render_assign(
        schedule_id,
        errors=["Auto-fill couldn't staff every shift:"] + short if short else None,
        message="Auto-fill checked the boxes below. Review them, then save. Nothing is saved yet.",
        assigned=assigned
    )

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

# The prof holds this many help sessions a week
HELP_SESSIONS_PER_WEEK = 2

# Options run Monday to Thursday in the 4-9 PM window: 2 hours long, starting on the hour
HELP_OPTION_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday"]
HELP_OPTION_STARTS = [16, 17, 18, 19]
HELP_OPTION_HOURS = 2

def help_shifts(shifts):
    return [shift for shift in shifts if shift[6] == "Help Session"]

def too_many_help_times(shifts):
    return len(help_shifts(shifts)) > HELP_SESSIONS_PER_WEEK

def rank_help_times(shifts, tas, answers):
    # Returns a list of (shift, {level: TA names}), the times most TAs can work first
    rows = []

    for shift in help_shifts(shifts):
        groups = {level: [] for level in LEVEL_ORDER}

        for ta_id, name in tas:
            groups[answers.get((shift[0], ta_id), "No answer")].append(name)

        rows.append((shift, groups))

    # Most TAs who can work, then most Preferred. The minus signs put big numbers first.
    # sorted() keeps ties in the order they came in, which is by day and time
    return sorted(rows, key=lambda row: (
        -(len(row[1]["Preferred"]) + len(row[1]["Available"])),
        -len(row[1]["Preferred"])
    ))

def render_best_times(schedule_id, error=None, message=None):
    schedule = get_schedule(schedule_id)
    tas, answers = get_tas_and_answers(schedule_id)

    return render_template(
        "best_times.html",
        schedule=schedule,
        rows=rank_help_times(get_shifts(schedule_id), tas, answers),
        levels=LEVEL_ORDER, # looping over a dict gives its keys, best answers first
        locked=schedule[2] in LOCKED_STATUSES,
        responded=len({user_id for _, user_id in answers}),
        ta_count=len(tas),
        error=error,
        message=message
    )

@app.route("/schedules/<int:schedule_id>/best-times") #admin compares help session times
@role_required("Admin")
def best_times(schedule_id):
    return render_best_times(schedule_id)

@app.route("/schedules/<int:schedule_id>/best-times/options", methods=["POST"]) #admin adds the usual time options
@role_required("Admin")
def add_help_options(schedule_id):

    schedule = get_schedule(schedule_id)

    if schedule[2] in LOCKED_STATUSES:
        return render_best_times(schedule_id, error="Shifts are locked while the schedule is filled or approved")

    # Skip times already on the schedule, so clicking twice can't make duplicates
    existing = {(shift[1], shift[2], shift[3]) for shift in help_shifts(get_shifts(schedule_id))}

    options = []
    for day in HELP_OPTION_DAYS:
        for start in HELP_OPTION_STARTS:
            start_time = f"{start}:00"
            end_time = f"{start + HELP_OPTION_HOURS}:00"

            if (day, start_time, end_time) not in existing:
                options.append((schedule_id, day, start_time, end_time))

    cur = mysql.connection.cursor()
    cur.executemany(
        """
        INSERT INTO shifts (schedule_id, day, start_time, end_time, shift_type)
        VALUES (%s, %s, %s, %s, 'Help Session')
        """,
        options
    )
    mysql.connection.commit()
    cur.close()

    return render_best_times(schedule_id, message=f"Added {len(options)} help session options")

@app.route("/schedules/<int:schedule_id>/best-times/keep", methods=["POST"]) #admin keeps the chosen times
@role_required("Admin")
def keep_help_times(schedule_id):

    schedule = get_schedule(schedule_id)

    if schedule[2] in LOCKED_STATUSES:
        return render_best_times(schedule_id, error="Shifts are locked while the schedule is filled or approved")

    options = help_shifts(get_shifts(schedule_id))
    keep = request.form.getlist("keep")

    # Compared as text, since that's how the checkboxes send ids.
    # Going through this schedule's own shifts means a tampered id can't touch another schedule
    removed = [(shift[0], schedule_id) for shift in options if str(shift[0]) not in keep]

    if len(removed) == len(options):
        return render_best_times(schedule_id, error="Check at least one time to keep")

    cur = mysql.connection.cursor()
    # ON DELETE CASCADE also removes the TA answers for these shifts
    cur.executemany("DELETE FROM shifts WHERE id = %s AND schedule_id = %s", removed)
    mysql.connection.commit()
    cur.close()

    return render_best_times(schedule_id, message=f"Kept {len(options) - len(removed)} help session times. Add a room to each on the schedule page, then assign TAs.")

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
    writer.writerow(UPLOAD_COLUMNS + ["shift_type", "assigned_tas"])

    for shift in get_shifts(schedule_id):
        ta_names = sorted(names[user_id] for user_id in assigned.get(shift[0], set()))
        writer.writerow([
            shift[1], shift[2], shift[3],
            csv_safe(shift[4] or ""),
            shift[5],
            shift[6],
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
    # The (user_id, shift_id) primary key turns a resubmission into an update:
    # REPLACE swaps out the old answer for that shift instead of erroring.
    # It works the same on MySQL and MariaDB. Its delete-then-insert is safe here
    # because nothing else references these rows
    cur.executemany(
        """
        REPLACE INTO shift_availability (user_id, shift_id, level)
        VALUES (%s, %s, %s)
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
        SELECT s.id, s.name, sh.day, sh.start_time, sh.end_time, sh.location, sh.shift_type
        FROM assignments a
        JOIN shifts sh ON sh.id = a.shift_id
        JOIN schedules s ON s.id = sh.schedule_id
        WHERE a.user_id = %s AND s.status = 'approved'
        ORDER BY s.name, FIELD(sh.day, 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'), sh.start_time
        """,
        (session["user_id"],)
    )

    shifts = [
        (row[0], row[1], row[2], format_time(row[3]), format_time(row[4]), row[5], row[6])
        for row in cur.fetchall()
    ]

    cur.close()

    return render_template("my_shifts.html", shifts=shifts)

if __name__ == "__main__":
    # The debugger can run code from the browser, so it's off unless .env turns it on
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")