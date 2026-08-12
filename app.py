from flask import Flask
from flask_mysqldb import MySQL
from flask import render_template, request, redirect, url_for

app = Flask(__name__)

app.config['MYSQL_HOST'] = 'localhost'
app.config['MYSQL_USER'] = 'root'
app.config['MYSQL_PASSWORD'] = 'Password' #change password
app.config['MYSQL_DB'] = 'ta_scheduler'

mysql=MySQL(app)

@app.route("/", methods=["GET", "POST"])
def login():

    if request.method == "POST":
        return redirect(url_for("home"))

    return render_template("login.html")

@app.route("/home")
def home():
    return render_template("home.html")

@app.route("/test-db")
def test_db():
    cur = mysql.connection.cursor()
    cur.execute("SELECT 1")
    cur.close()
    return "Database works!"

@app.route("/createUser", methods=["GET", "POST"])
def add_user_page():

    if request.method == "POST":

        name = request.form["name"]
        role = request.form["role"]

        cur = mysql.connection.cursor()

        cur.execute(
            "INSERT INTO users (name, role) VALUES (%s, %s)",
            (name, role)
        )

        mysql.connection.commit()
        cur.close()

        return redirect(url_for("users"))

    return render_template("createUser.html")

@app.route("/availability", methods=["GET", "POST"])
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
            INSERT INTO availability (name, monday, tuesday, wednesday, thursday, friday)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (name, monday, tuesday, wednesday, thursday, friday))

        mysql.connection.commit()
        cur.close()

        return redirect(url_for("home"))

    return render_template("availability.html")

@app.route("/availability-checker")
def availability_checker():

    cur = mysql.connection.cursor()
    cur.execute("SELECT name, monday, tuesday, wednesday, thursday, friday FROM availability")
    data = cur.fetchall()
    cur.close()

    return render_template("availability_checker.html", availability=data)

@app.route("/users")
def users():
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM users")
    data = cur.fetchall()
    return render_template("users.html", users=data)

if __name__ == "__main__":
    app.run(debug=True)

#test