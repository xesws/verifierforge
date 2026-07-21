CREATE TABLE departments (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, location TEXT NOT NULL, budget INTEGER NOT NULL
);
INSERT INTO departments VALUES
    (1, 'Engineering', 'San Francisco', 500000),
    (2, 'Research', 'New York', 400000),
    (3, 'Sales', 'Chicago', 300000),
    (4, 'Operations', 'Austin', 250000),
    (5, 'Design', 'Remote', 200000);

CREATE TABLE employees (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, department_id INTEGER NOT NULL,
    title TEXT NOT NULL, salary INTEGER NOT NULL, hire_date TEXT NOT NULL, active INTEGER NOT NULL
);
INSERT INTO employees VALUES
    (1, 'Ada', 1, 'Engineer', 150000, '2020-01-15', 1),
    (2, 'Grace', 1, 'Staff Engineer', 170000, '2018-06-01', 1),
    (3, 'Linus', 2, 'Researcher', 160000, '2019-09-10', 1),
    (4, 'Margaret', 2, 'Researcher', 155000, '2021-03-20', 1),
    (5, 'Alan', 3, 'Account Executive', 130000, '2017-11-05', 1),
    (6, 'Barbara', 3, 'Sales Manager', 145000, '2016-04-12', 1),
    (7, 'Donald', 4, 'Operations Manager', 135000, '2020-08-08', 1),
    (8, 'Edsger', 4, 'Analyst', 110000, '2022-02-28', 0),
    (9, 'Hedy', 5, 'Designer', 125000, '2019-12-01', 1),
    (10, 'Ken', 5, 'Design Lead', 140000, '2018-07-19', 1),
    (11, 'Frances', 1, 'Engineer', 120000, '2023-01-05', 1),
    (12, 'Claude', 2, 'Intern', 80000, '2024-05-01', 1);

CREATE TABLE projects (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, department_id INTEGER NOT NULL,
    lead_employee_id INTEGER NOT NULL, budget INTEGER NOT NULL, status TEXT NOT NULL
);
INSERT INTO projects VALUES
    (1, 'Atlas', 1, 2, 200000, 'active'),
    (2, 'Beacon', 2, 3, 150000, 'active'),
    (3, 'Comet', 3, 6, 90000, 'planned'),
    (4, 'Delta', 4, 7, 110000, 'active'),
    (5, 'Echo', 5, 10, 75000, 'complete'),
    (6, 'Forge', 1, 1, 180000, 'active'),
    (7, 'Genome', 2, 4, 210000, 'planned'),
    (8, 'Horizon', 3, 5, 60000, 'complete');

CREATE TABLE employee_projects (
    employee_id INTEGER NOT NULL, project_id INTEGER NOT NULL, hours INTEGER NOT NULL
);
INSERT INTO employee_projects VALUES
    (1, 1, 50), (1, 6, 70), (2, 1, 80), (2, 6, 30),
    (3, 2, 100), (3, 7, 20), (4, 2, 40), (4, 7, 90),
    (5, 3, 75), (5, 8, 25), (6, 3, 100), (7, 4, 120),
    (8, 4, 20), (9, 5, 60), (10, 5, 100), (11, 1, 30),
    (11, 6, 60), (12, 2, 10);
