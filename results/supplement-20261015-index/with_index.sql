-- Common PostgreSQL / openGauss DDL. Loader selects an isolated filmbench_sN schema.
CREATE TABLE countries (
    country_code varchar(2) PRIMARY KEY,
    country_name varchar(50) NOT NULL UNIQUE,
    continent varchar(20) NOT NULL
);
CREATE TABLE movies (
    movieid bigint PRIMARY KEY,
    title varchar(100) NOT NULL,
    country varchar(2) NOT NULL REFERENCES countries(country_code),
    year_released integer NOT NULL,
    runtime integer,
    replica integer NOT NULL,
    UNIQUE(title, country, year_released, replica)
);
CREATE TABLE people (
    peopleid bigint PRIMARY KEY,
    first_name varchar(30),
    surname varchar(30) NOT NULL,
    born integer NOT NULL,
    died integer,
    gender varchar(1) NOT NULL,
    replica integer NOT NULL,
    UNIQUE(surname, first_name, replica)
);
CREATE TABLE credits (
    movieid bigint NOT NULL REFERENCES movies(movieid),
    peopleid bigint NOT NULL REFERENCES people(peopleid),
    credited_as varchar(1) NOT NULL,
    PRIMARY KEY(movieid, peopleid, credited_as)
);
CREATE INDEX credits_person_idx ON credits(peopleid, movieid);
