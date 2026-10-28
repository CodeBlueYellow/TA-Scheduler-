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

-- Stores app-wide values like the TA join code
CREATE TABLE IF NOT EXISTS settings (
    name VARCHAR(50) PRIMARY KEY,
    value VARCHAR(100) NOT NULL
);

INSERT IGNORE INTO settings (name, value) VALUES ('join_code', 'CHANGE-ME');

-- A schedule is a named set of TA shifts (e.g. "CS 101 Fall 2026")
-- join_code is unused for now: TAs register with the universal code in settings.
-- It's kept (empty) in case each schedule gets its own code later.
-- status: draft -> open (TAs can see it and submit availability)
--         -> filled (every shift staffed) -> approved
--         Rejecting a filled schedule, or reopening an approved one, sends it back to open
CREATE TABLE IF NOT EXISTS schedules (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    join_code VARCHAR(50) UNIQUE,
    status VARCHAR(20) NOT NULL DEFAULT 'draft',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- For databases created before join_code was made optional
ALTER TABLE schedules MODIFY join_code VARCHAR(50) NULL;

-- For databases created before status existed. MySQL has no
-- "ADD COLUMN IF NOT EXISTS", so check first to keep this file safe to re-run
SET @has_status := (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'schedules' AND column_name = 'status'
);
SET @sql := IF(@has_status = 0,
    "ALTER TABLE schedules ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'draft'",
    'SELECT 1');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

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

-- One answer per TA per shift (Unavailable, Available, or Preferred).
-- The primary key stops duplicates, so resubmitting updates the old answer
CREATE TABLE IF NOT EXISTS shift_availability (
    user_id INT NOT NULL,
    shift_id INT NOT NULL,
    level VARCHAR(20) NOT NULL,
    PRIMARY KEY (user_id, shift_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (shift_id) REFERENCES shifts(id) ON DELETE CASCADE
);

-- Which TA works which shift. A TA can only be on a shift once
CREATE TABLE IF NOT EXISTS assignments (
    shift_id INT NOT NULL,
    user_id INT NOT NULL,
    PRIMARY KEY (shift_id, user_id),
    FOREIGN KEY (shift_id) REFERENCES shifts(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
