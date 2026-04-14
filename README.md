# Finzytrack

Open-source personal finance application built on [Beancount](https://beancount.github.io/) double-entry bookkeeping. Import statements from a wide variety of sources, explore your finances through customizable dashboards, and optionally let AI help you along the way — all while keeping full ownership of your data in a plain-text ledger file.

<!-- TODO: Replace with actual screenshots -->
![Financial Overview Dashboard](docs/screenshots/dashboard.png)
*Customizable dashboards with KPI cards, charts, and interactive widgets*

![Import & Transaction Review](docs/screenshots/import.png)
*Import statements from OFX, CSV, XLS, email, or AI-assisted parsing, then review and categorize*

## Features

- **Double-entry bookkeeping** — built on Beancount, the powerful plain-text accounting system
- **Import a wide variety of statements** — rule-based importers for OFX, CSV, XLS files, or directly from email
- **Customizable dashboards** — KPI cards, charts, tables, and pivot tables in configurable grid layouts
- **Auto-categorization** — train on your past transactions or use AI assistance
- **Powerful querying** — SQL and BQL queries against your financial data
- **Optional AI assistance** — parse statements, create import rules, enter transactions in natural language, build dashboards, and answer financial questions
- **Your data, your control** — everything stored in a single plain-text Beancount ledger file with zero lock-in

## Documentation

Full documentation at [docs.finzytrack.com](https://docs.finzytrack.com), including:

- [Quick Start](https://docs.finzytrack.com/quick-start/) — get up and running from first launch
- [Installation](https://docs.finzytrack.com/installation/) — download and install on macOS, Linux, or Windows
- [Views](https://docs.finzytrack.com/views/) — guide to each screen in the app
- [Reference](https://docs.finzytrack.com/reference/dashboard-recipes/) — technical reference for recipes, rules, queries, and configuration

## Development Setup

```bash
git clone git@github.com:sagarbehere/finzytrack.git
cd finzytrack

# Backend
python3 -m venv venv
source venv/bin/activate
cd backend
pip install -r requirements.txt

# Frontend
cd ../frontend
npm install
```

See [Building from Source](https://docs.finzytrack.com/development/building/) for full development instructions.

## License

This project is licensed under the [GNU General Public License v2.0](COPYING).
