# TA Scheduler

A website for scheduling lab TAs. The professor creates a schedule of lab shifts and help sessions. TAs log in and mark which shifts they can work. The professor then assigns TAs to shifts and downloads the finished schedule.

The README has two parts:

- **Part 1: Using the app.** For the professor. No technical knowledge needed.
- **Part 2: Setting it up.** For whoever installs and hosts it.

---

# Part 1: Using the app

You only need a web browser and the website address you were given.

## Logging in

1. Go to the website address.
2. Choose **Admin**, enter the username `admin`, and enter your admin password.

Your dashboard shows a to-do list of what to do next, so you can always start there.

## First-time setup

1. **Change the join code.** The join code is a password that TAs use to create their accounts, which stops strangers from signing up. Open **Join Code**, pick a new code, and save it.
2. **Send the TAs the website address and the join code.** Each TA clicks **Register**, fills in their details with the join code, and then logs in as a **TA**.

## Each semester

1. **Create a schedule.** Go to **Schedules** and either:
   - build the schedule in the app, adding each lab and help session with its day, time, location and number of TAs needed, or
   - upload a spreadsheet. Download the template from the Schedules page, fill it in with Excel, save it as CSV, and upload it.
2. **Publish the schedule.** TAs can't see a schedule until you publish it.
3. **TAs submit their availability.** For each shift they choose *Unavailable*, *Available* or *Preferred*. Your dashboard shows how many TAs have responded so far.
4. **Choose help session times** if the app asks you to. The **Best Times** page shows which time options suit the most TAs.
5. **Assign TAs.** You can click **Auto-Fill** to get a suggested assignment, then change anything you like.
6. **Approve the schedule.** TAs can then see their shifts under **My Shifts**.
7. **Download** the finished schedule as a spreadsheet to keep or share.

You can also **copy** an old schedule to new dates, which saves time next semester.

## If something goes wrong

- **Forgot the admin password?** The person who set up the app can reset it (see Part 2).
- **"Page expired" message?** A form was open for too long. Go back, refresh the page, and try again.
- **The website won't load?** Contact whoever maintains the hosting (see Part 2).

---

# Part 2: Setting it up

## Why it needs real hosting

This is a Python (Flask) app with a MySQL database. TAs submit their availability from their own devices, so the app must run on a server that's reachable on the internet. Running it on one person's laptop would only work while that laptop is on, and only for that person. Static hosts such as GitHub Pages can't run it either. GitHub is the right place to store and share the **code**, not to run the app.

The simplest option is **[PythonAnywhere](https://www.pythonanywhere.com)**. Its free tier runs Flask apps and includes a MySQL database. Another option is a server run by the university or department, which may be the better choice for official use because the app stores student names and contact details.

## What's in the project

| File | What it is |
|---|---|
| `app.py` | The whole web app |
| `schema.sql` | Creates the database tables. It's safe to run again because it doesn't delete data |
| `templates/`, `static/` | The web pages, the stylesheet and the CSV template |
| `.env.example` | A template for the private settings file `.env` |
| `set_admin_password.py` | Sets or resets the admin password |
| `requirements.txt` | The Python packages the app needs |

The `.env` file holds the passwords and secret key. **Never commit it to git or share it.** Each installation creates its own.

## Running it on your own computer (for testing)

You'll need Python 3.10 or newer and MySQL 8.

```bash
# 1. Install the packages, ideally in a virtual environment
python -m venv venv
venv\Scripts\activate            # Windows (on Mac/Linux: source venv/bin/activate)
pip install -r requirements.txt

# 2. Create the database, then its tables, as the MySQL root user
mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS ta_scheduler"
mysql -u root -p ta_scheduler < schema.sql
```

On Windows, if `mysql` isn't recognized, use its full path, for example `"C:\Program Files\MySQL\MySQL Server 9.6\bin\mysql.exe"`. PowerShell doesn't support `<`, so run the second command through `cmd /c '...'`.

3. Create the app's own MySQL account. Log in with `mysql -u root -p` and run the following, using a password you choose:

   ```sql
   CREATE USER 'ta_app'@'localhost' IDENTIFIED BY 'choose-a-password';
   GRANT SELECT, INSERT, UPDATE, DELETE ON ta_scheduler.* TO 'ta_app'@'localhost';
   ```

4. Copy `.env.example` to `.env` and fill it in:
   - `SECRET_KEY`: generate one with `python -c "import secrets; print(secrets.token_hex(32))"`
   - `MYSQL_PASSWORD`: the password from step 3
   - leave `MYSQL_HOST` and `MYSQL_DB` blank
5. Set the admin password with `python set_admin_password.py`.
6. Start the app with `python app.py`, then open http://127.0.0.1:5000.

If you change `schema.sql` later, run it again as root. The app's `ta_app` account can change data but not tables.

## Hosting on PythonAnywhere

In the steps below, replace `USERNAME` with the PythonAnywhere username and `REPO` with the name of the GitHub repository folder.

### 1. Create the account

Sign up for a free **Beginner** account at [pythonanywhere.com](https://www.pythonanywhere.com). Use the **professor's or department's** email, not a student's, so the app keeps running after the student leaves. The site will be at `https://USERNAME.pythonanywhere.com`, so choose the username with that in mind.

### 2. Create the database

1. Open the **Databases** tab.
2. Set a **MySQL password**. This is separate from the account password, so write it down.
3. Under **Create a database**, enter `ta_scheduler`. PythonAnywhere names it `USERNAME$ta_scheduler`.

The MySQL username is the same as the account username. The database host appears at the top of the tab: `USERNAME.mysql.pythonanywhere-services.com`.

### 3. Get the code

Open the **Consoles** tab and start a **Bash** console:

```bash
git clone https://github.com/OWNER/REPO.git
cd REPO
```

If the repository is private, git asks for a username and password. For the password, paste a GitHub **personal access token** (GitHub → Settings → Developer settings → Personal access tokens). Your real GitHub password won't work. You can also make the repository public, because the code contains no passwords (they live only in `.env`).

### 4. Install the packages

Check which Python versions the **Web** tab offers (step 7), and use the newest one here. For example, for 3.13:

```bash
python3.13 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 5. Create the tables

Still in the console, run the following and enter the MySQL password from step 2 when asked:

```bash
mysql -u USERNAME -h USERNAME.mysql.pythonanywhere-services.com -p 'USERNAME$ta_scheduler' < schema.sql
```

Skip the `ta_app` account step from the local setup, because PythonAnywhere doesn't allow extra MySQL users.

### 6. Create `.env` and the admin password

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"   # copy the output
nano .env
```

Fill it in as follows (in nano, Ctrl+O then Enter saves and Ctrl+X exits):

```
SECRET_KEY=<the output you just copied>
MYSQL_USER=USERNAME
MYSQL_PASSWORD=<the MySQL password from step 2>
MYSQL_HOST=USERNAME.mysql.pythonanywhere-services.com
MYSQL_DB='USERNAME$ta_scheduler'
FLASK_DEBUG=0
```

Keep the single quotes around `MYSQL_DB`. Then run `python set_admin_password.py` and choose the professor's admin password.

### 7. Create the web app

1. Open the **Web** tab and click **Add a new web app**.
2. Choose **Manual configuration** (not "Flask") and the same Python version as in step 4.
3. On the configuration page, set:
   - **Source code:** `/home/USERNAME/REPO`
   - **Virtualenv:** `/home/USERNAME/REPO/venv`
   - **Static files:** URL `/static/`, directory `/home/USERNAME/REPO/static`
   - **Force HTTPS:** on
4. Click the **WSGI configuration file** link, delete everything in it, and replace it with:
   ```python
   import sys

   path = "/home/USERNAME/REPO"
   if path not in sys.path:
       sys.path.insert(0, path)

   from app import app as application
   ```
   Then save.
5. Click the green **Reload** button and visit `https://USERNAME.pythonanywhere.com`.

If the page shows an error, open the **error log** link on the Web tab. The last lines say what went wrong, which is usually a typo in `.env` or the WSGI file.

### Free-tier renewal

PythonAnywhere turns free web apps off after 3 months unless someone logs in and clicks **"Run until 3 months from today"** on the Web tab. They email a reminder first. A paid plan removes this.

## Maintenance

- **Reset the admin password:** in a Bash console, `cd REPO`, `source venv/bin/activate`, run `python set_admin_password.py`, then click **Reload** on the Web tab.
- **Back up the data:** download each finished schedule as CSV from the app. For a full backup, run this in a Bash console:
  `mysqldump -u USERNAME -h USERNAME.mysql.pythonanywhere-services.com -p --no-tablespaces 'USERNAME$ta_scheduler' > backup.sql`
- **Update the code:** push the changes to GitHub, then in a Bash console run `cd REPO` and `git pull`. Run the step 5 command again if `schema.sql` changed, then click **Reload**.
