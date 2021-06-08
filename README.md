Visualisation of Code Changes for Code Review
---------------------------------------------

- `Backend`: the Python Flask app that coordinates the method extraction and caching
- `Engine`: the JavaParser method call extraction engine
- `Frontend`: the web-based review interface


### How to run the tool
First, clone the repository. Then follow the instructions below to setup frontend and backend.
#### Frontend

1. Serve the code in `Frontend/` with a web server on port e.g. 80
2. The frontend code expects the backend to be available on port 5000 of the same address, modify this if using a different port.

#### Backend
1. Install [ripgrep](https://github.com/BurntSushi/ripgrep).
2. Install the JDK.
3. Install sqlite3.
4. Enter the `Backend/` folder, create a Python3 virtual environment and install the dependencies

```
python -m venv venv
source venv/bin/activate
pip install requests flask-cors pysqlite3
```
5. Create two folders `diffs` and `cloned_repos`.
6. Initialize the SQLite database with `sqlite3 mydb.db < schema.sql`
7. Modify the auth data in `main.py`
8. Copy `mcextractor.jar` from `Engine` next to `main.py`
9. Start the application with `python main.py`, by default it will be served on port 5000.
