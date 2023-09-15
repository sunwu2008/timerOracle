from flask import Flask, request, render_template, send_from_directory, redirect, url_for
import cx_Oracle
from configparser import ConfigParser
import pandas as pd
import csv
import os
from apscheduler.schedulers.background import BackgroundScheduler
import sqlite3
import datetime

app = Flask(__name__)

# Read database configuration from ini file
config = ConfigParser()
config.read('config.ini')

DB_CONFIG = {
    'host': config.get('oracle', 'host'),
    'port': config.get('oracle', 'port'),
    'user': config.get('oracle', 'user'),
    'password': config.get('oracle', 'password'),
    'sid': config.get('oracle', 'sid')
}
#scheduler server
scheduler = BackgroundScheduler()
scheduler.start()

# Log database 
DATABASE_NAME = "tasks.db"


def init_db():
    with sqlite3.connect(DATABASE_NAME) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY,
                query TEXT,
                time_to_run TEXT,
                status TEXT,
                filename TEXT
            )
        """)


init_db()


@app.route('/')
def index():
    current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with sqlite3.connect(DATABASE_NAME) as conn:
        # Update task status if time_to_run has passed and status is still "scheduled"
        conn.execute("UPDATE tasks SET status = 'completed' WHERE time_to_run <= ? AND status = 'scheduled'",
                     (current_time,))
        tasks = conn.execute("SELECT * FROM tasks ORDER BY time_to_run DESC").fetchall()
    return render_template('index.html', tasks=tasks)


@app.route('/get_latest_tasks')
def get_latest_tasks():
    current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with sqlite3.connect(DATABASE_NAME) as conn:
        # Update task status if time_to_run has passed and status is still "scheduled"
        conn.execute("UPDATE tasks SET status = 'completed' WHERE time_to_run <= ? AND status = 'scheduled'",
                     (current_time,))
        tasks = conn.execute("SELECT * FROM tasks ORDER BY time_to_run DESC").fetchall()
    tasks_list = [{'id': task[0], 'query': task[1], 'time_to_run': task[2], 'status': task[3], 'filename': task[4]} for
                  task in tasks]
    return {'tasks': tasks_list}


@app.route('/schedule_job', methods=['POST'])
def schedule_job():
    query = request.form['query']
    time_to_run = request.form.get('time_to_run') or request.form.get('manual_time_to_run')
    if "T" in time_to_run:
        time_to_run = time_to_run.replace("T", " ") + ":00"
    if not query or not time_to_run:
        return {"error": "Both SQL query and time to run must be provided."}, 400

    with sqlite3.connect(DATABASE_NAME) as conn:
        conn.execute("INSERT INTO tasks (query, time_to_run, status, filename) VALUES (?, ?, ?, ?)",
                     (query, time_to_run, 'scheduled', None))

    scheduler.add_job(execute_sql, 'date', [query, time_to_run], run_date=time_to_run)
    return redirect(url_for('index'))


@app.route('/download/<filename>')
def download_file(filename):
    return send_from_directory(os.getcwd(), filename)


def execute_sql(query, time_to_run):
    try:
        dsn_tns = cx_Oracle.makedsn(DB_CONFIG['host'], DB_CONFIG['port'], sid=DB_CONFIG['sid'])
        connection = cx_Oracle.connect(user=DB_CONFIG['user'], password=DB_CONFIG['password'], dsn=dsn_tns)
        cursor = connection.cursor()
        cursor.execute(query)

        rows = cursor.fetchall()
        column_names = [i[0] for i in cursor.description]
        df = pd.DataFrame(rows, columns=column_names)

        csv_filename = "result_" + time_to_run.replace(":", "_").replace(" ", "_") + ".csv"
        df.to_csv(csv_filename, index=False, quoting=csv.QUOTE_NONNUMERIC)

        cursor.close()
        connection.close()

        # Update task in the SQLite database
        with sqlite3.connect(DATABASE_NAME) as conn:
            conn.execute("UPDATE tasks SET status = ?, filename = ? WHERE query = ? AND time_to_run = ?",
                         ('completed', csv_filename, query, time_to_run))

    except Exception as e:
        print(f"Error: {str(e)}")


if __name__ == '__main__':
    app.run(debug=True)
