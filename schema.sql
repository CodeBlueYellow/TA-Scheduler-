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
