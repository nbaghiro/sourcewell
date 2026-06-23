-- Runs once on first Postgres init (as POSTGRES_USER against POSTGRES_DB=sourcewell).
CREATE DATABASE sourcewell_test;

\connect sourcewell
CREATE EXTENSION IF NOT EXISTS vector;

\connect sourcewell_test
CREATE EXTENSION IF NOT EXISTS vector;
