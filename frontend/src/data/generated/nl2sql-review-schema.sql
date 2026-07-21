CREATE TABLE departments (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, location TEXT NOT NULL, budget INTEGER NOT NULL
);

CREATE TABLE employees (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, department_id INTEGER NOT NULL,
    title TEXT NOT NULL, salary INTEGER NOT NULL, hire_date TEXT NOT NULL, active INTEGER NOT NULL
);

CREATE TABLE projects (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, department_id INTEGER NOT NULL,
    lead_employee_id INTEGER NOT NULL, budget INTEGER NOT NULL, status TEXT NOT NULL
);

CREATE TABLE employee_projects (
    employee_id INTEGER NOT NULL, project_id INTEGER NOT NULL, hours INTEGER NOT NULL
);
