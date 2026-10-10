-- TA Scheduler database setup
-- Run with: mysql -u root -p < schema.sql

CREATE DATABASE IF NOT EXISTS ta_scheduler;
USE ta_scheduler;

CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(50),
    role VARCHAR(20),
    username VARCHAR(50) NOT NULL UNIQUE,
    contact VARCHAR(100),
    password_hash VARCHAR(255) NOT NULL
);

CREATE TABLE IF NOT EXISTS availability (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100),
    monday VARCHAR(20),
    tuesday VARCHAR(20),
    wednesday VARCHAR(20),
    thursday VARCHAR(20),
    friday VARCHAR(20)
);

-- Stores app-wide values like the TA join code
CREATE TABLE IF NOT EXISTS settings (
    name VARCHAR(50) PRIMARY KEY,
    value VARCHAR(100) NOT NULL
);

INSERT IGNORE INTO settings (name, value) VALUES ('join_code', 'CHANGE-ME');

-- A schedule is a named set of TA shifts (e.g. "CS 101 Fall 2026")
-- join_code is unused for now: TAs register with the universal code in settings.
-- It's kept (empty) in case each schedule gets its own code later.
CREATE TABLE IF NOT EXISTS schedules (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    join_code VARCHAR(50) UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- For databases created before join_code was made optional
ALTER TABLE schedules MODIFY join_code VARCHAR(50) NULL;

-- One row per shift; deleting a schedule deletes its shifts
CREATE TABLE IF NOT EXISTS shifts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    schedule_id INT NOT NULL,
    day VARCHAR(10) NOT NULL,
    start_time TIME NOT NULL,
    end_time TIME NOT NULL,
    location VARCHAR(100),
    tas_needed INT NOT NULL DEFAULT 1,
    FOREIGN KEY (schedule_id) REFERENCES schedules(id) ON DELETE CASCADE
);
