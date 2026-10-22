# Deploy MediCare HMS on Render (portfolio demo)

This project uses Flask + SQLAlchemy. For an online demo, use a managed PostgreSQL database rather than a local SQLite file or a database running only on your laptop.

## 1. Before pushing to GitHub

- Keep `run.py` and `requirements.txt` at the repository root.
- Check `.gitignore`; never commit `.env`, passwords, database dumps, or real patient data.
- The project is configured to accept Render's `postgres://` / `postgresql://` database URL and use the installed `psycopg` driver.
- Keep your existing local MySQL settings; `DATABASE_URL` overrides them when set.

## 2. Push to GitHub from Windows CMD

Open CMD in this project directory (the one containing `run.py`):

```cmd
git init
git add .
git status
git commit -m "Prepare MediCare HMS for deployment"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/hospital-management-system.git
git push -u origin main
```

Create the empty repository on GitHub first. Replace `YOUR_USERNAME`. If Git says `remote origin already exists`, use `git remote set-url origin https://github.com/YOUR_USERNAME/hospital-management-system.git`. Review `git status` before committing and do not upload secrets or patient data.

## 3. Create the PostgreSQL database

1. Sign in to Render and create a PostgreSQL database.
2. Copy its **Internal Database URL** for a web service hosted on Render. Keep it private.
3. Create a Web Service connected to the GitHub repository.

## 4. Web Service settings

- Runtime: Python
- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn run:app`
- Root directory: leave blank if `run.py` is at the repository root; otherwise set it to the folder containing `run.py`.

In the Web Service's Environment settings add:

- `DATABASE_URL` = the Internal Database URL from your Render PostgreSQL database
- `SECRET_KEY` = a newly generated, long, random secret (do not reuse a public/demo value)
- `SESSION_COOKIE_SECURE` = `1`
- `HMS_ADMIN_PASSWORD` = a strong, private administrator password, set **before** running the seed command

Deploy the service and inspect its logs. Do not put secrets in source code or GitHub.

## 5. Create the schema and load demo seed data ONCE

After the web service can connect to PostgreSQL, open its Shell if available for your service/plan and run from the project root:

```bash
python seed.py
```

This calls the project's `app.seed.seed()` function, creates the tables, and inserts synthetic demo users, departments, doctors, patients, appointments, consultation records, and invoices. It intentionally skips seeding when roles already exist. Run it once against a fresh demo database. Do not run it repeatedly as a way to reset data. If your hosting plan does not provide a Shell, use a one-off job or run the command from a secure local environment with the deployed `DATABASE_URL` set temporarily; never commit the URL.

## 6. Demo accounts in this seed script

- Receptionist: `reception` / `Reception@123`
- Doctor accounts: `dr.sharma`, `dr.iyer`, `dr.khan`, `dr.rao` / `Doctor@123`
- Admin: `admin` / the private password configured through `HMS_ADMIN_PASSWORD` before seeding

For a public portfolio, advertise only the receptionist demo button/account. Never publish the administrator password. The current application lets authenticated roles change data, so use only synthetic data and assume shared demo records can be modified by visitors. Do not publish administrator credentials. Consider read-only demo mode before broad public sharing.

## 7. Test

Open the Render URL in a private/incognito browser window. Confirm login, role permissions, appointments, and logout. If startup fails, check Render logs and confirm `DATABASE_URL` is the PostgreSQL Internal Database URL, `psycopg` installed successfully, and `run.py` is at the configured root.
