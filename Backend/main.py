import sqlite3
import re
import requests
import os
import subprocess
from time import sleep
from flask import Flask, request, abort, redirect, url_for, g, jsonify
from concurrent.futures import ThreadPoolExecutor
from flask_cors import CORS

DIFFS_DIR = 'diffs'
CLONED_REPOS_DIR = 'cloned_repos'
DATABASE = 'mydb.db'
PR_URL_REGEX = r'^https?:\/\/(?:www\.)?github\.com\/(.*?)\/(.*?)\/pull\/(\w*?)$'
DIFF_FILES_REGEX = r'^diff --git a\/(.*?\.java) b\/(.*?\.java)(?:\r\n|\r|\n)(?!deleted)'

# compile regex to improve performance
pr_url_regex = re.compile(PR_URL_REGEX)
diff_files_regex = re.compile(DIFF_FILES_REGEX, re.M)

executor = ThreadPoolExecutor(4)
app = Flask(__name__)
CORS(app)

### routes ###
@app.route('/debug')
def debug():
    # dump all the tables in the db for debugging purposes
    tables = ['repositories', 'reviews', 'modifiedfiles', 'methodcalls']
    # used to order tables in output
    mapping = {
        'repositories': '1-repositories',
        'reviews': '0-reviews',
        'modifiedfiles': '2-modifiedfiles',
        'methodcalls': '3-methodcalls'
    }
    output = {}
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    for table in tables:
        rows = cur.execute('SELECT * FROM {}'.format(table)).fetchall()
        output[mapping[table]] = [dict(ix) for ix in rows]
    conn.close()

    return jsonify(status='ok', data=output)

@app.route('/review/start')
def start_review():
    # check that a diff url is provided
    pr_url = request.args.get('pr')
    if not pr_url:
        app.logger.error('No pull request URL provided')
        return jsonify(status='error', error='Pull request URL not provided')

    # check that the diff url is valid
    matches = pr_url_regex.match(pr_url)
    if not matches:
        app.logger.error('Invalid PR URL provided: {}'.format(pr_url))
        return jsonify(status='error', error='Invalid pull request URL')
    user, repo, pull_id = matches.group(1), matches.group(2), matches.group(3)

    # check if this review was already started
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    res = cur.execute('SELECT * FROM reviews WHERE pr_url = ?', (pr_url, ))
    row = res.fetchone()

    # if there is already a record for the current pull request
    if row:
        review_id = row['id']
        app.logger.info('Review already exists with id {}'.format(row['id']))
        # return its info
        return jsonify(status='ok', data={'review_status': row['status'], 'id': review_id})

    # otherwise, we get first the pr info from the github api.
    api_pull_url = 'https://api.github.com/repos/{}/{}/pulls/{}'.format(user, repo, pull_id)
    app.logger.info('Getting info from Github API, URL: {}'.format(api_pull_url))
    resp = requests.get(api_pull_url, auth=('joined', '14570db4b1e5b63c6d2e6678c4721533c3f2bfd8'))
    if resp.status_code != 200:
        app.logger.error('Problem with Github API')
        return jsonify(status='error', error='Error getting pull request info from Github')
    pr_json_info = resp.json()
    base_commit_sha = pr_json_info['base']['sha']
    head_commit_sha = pr_json_info['head']['sha']
    app.logger.info('Got info from Github API. Base commit: {}, head commit: {}'.format(base_commit_sha, head_commit_sha))

    # check if the repository has already been cloned
    res = cur.execute('SELECT * FROM repositories WHERE user = ? AND repo = ?', (user, repo))
    row = res.fetchone()
    if row:
        # if the repository has already been cloned
        app.logger.info('Repository already exists')
        repository_id = row['id']
    else:
        # if it has not been cloned, clone it
        app.logger.info('Cloning the repository')
        cur.execute('INSERT INTO repositories VALUES (null, ?, ?, ?)', (user, repo, 'cloning'))
        conn.commit()
        executor.submit(clone_repository, user, repo)
        repository_id = cur.lastrowid

    app.logger.info('Creating review in database')
    cur.execute('INSERT INTO reviews VALUES (null, ?, ?, ?, ?, ?)',
        ('processing', repository_id, pr_url, base_commit_sha, head_commit_sha)
    )
    review_id = cur.lastrowid

    conn.commit()
    conn.close()

    executor.submit(update_diff, cur.lastrowid)

    return jsonify(status='ok', data={'review_status': 'processing', 'id': review_id})

# update the diff corresponding to the pull request. either for the first time or the following ones
def update_diff(review_id):
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    # get pr url so that we can retrieve the url from the github api
    res = cur.execute('SELECT * FROM reviews WHERE id = ?', (review_id, ))
    row = res.fetchone()
    diff_url = row['pr_url'] + '.diff'
    app.logger.info('Getting diff for PR from URL {}'.format(diff_url))
    resp = requests.get(diff_url)
    diff = resp.text

    app.logger.info('Writing diff to file')
    with open("{}/{}.diff".format(DIFFS_DIR, review_id), "w") as diff_file:
        diff_file.write(diff)

    matches = diff_files_regex.findall(diff)
    app.logger.info('Found {} modified Java files, storing them in the db'.format(len(matches)))
    for old_filename, new_filename in matches:
        cur.execute('INSERT INTO modifiedfiles VALUES (null, ?, ?, ?)',
            (review_id, old_filename, new_filename)
        )

    if not matches:
        cur.execute('UPDATE reviews SET status = "ready" WHERE id = ?', (review_id, ))
        conn.commit()
        conn.close()
    else:
        conn.commit()
        conn.close()
        executor.submit(compute_methodcalls, review_id)

# method call extraction
def compute_methodcalls(review_id):
    app.logger.info('Computing method calls')
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    res = cur.execute('SELECT pr_url, base_commit_sha, repo_id FROM reviews WHERE id = ?', (review_id, ))
    row = res.fetchone()
    matches = pr_url_regex.match(row[0])
    user, repo = matches.group(1), matches.group(2)
    base_commit_sha, repo_id = row[1], row[2]
    # check that the repo was cloned.
    # to compute the method calls we need to be sure the repo is fully cloned.
    while True:
        res = cur.execute('SELECT status FROM repositories WHERE id = ?', (repo_id, ))
        row = res.fetchone()
        repo_status = row[0]
        if repo_status == 'cloned':
            break
        app.logger.info('Waiting for the git clone to finish...')
        sleep(1)
    repo_folder = os.path.abspath('{}/{}_{}'.format(CLONED_REPOS_DIR, user, repo))
    # pull to have the latest version of the repo
    app.logger.info('Pulling latest version of repo')
    s = subprocess.run(['git', 'fetch', '--all'], cwd=repo_folder)
    app.logger.info(s)
    # checkout the repo at the version corresponding to the base commit of the PR
    app.logger.info('Checking out the repository at commit {}'.format(base_commit_sha))
    s = subprocess.run(['git', 'checkout', base_commit_sha], cwd=repo_folder)
    app.logger.info('Applying diff')
    s = subprocess.run(['patch', '-p1', '-i', '../../{}/{}.diff'.format(DIFFS_DIR, review_id)], cwd=repo_folder, stdout=subprocess.PIPE)
    app.logger.info(s)
    res = cur.execute('SELECT new_filename FROM modifiedfiles WHERE review_id = ?', (review_id, ))
    mod_files = [row[0] for row in res]
    app.logger.info('Extracting method calls from {} modified files'.format(len(mod_files)))
    command = ['java', '-Xmx1024m', '-jar', 'mcextractor.jar', repo_folder]
    command.extend(mod_files)
    app.logger.info('Issuing command {}'.format(command))
    s = subprocess.run(command, stdout=subprocess.PIPE)
    methodcalls = [mc for mc in s.stdout.splitlines() if len(mc)]
    app.logger.info('Extracted {} method calls, storing them in the db'.format(len(methodcalls)))
    for mc in methodcalls:
        app.logger.info('Storing method call {}'.format(mc))
        o_file, o_s_l, o_s_c, o_e_l, o_e_c, method_call, short_qual, long_qual, d_file, d_s_l, d_s_c, d_e_l, d_e_c = mc.decode('utf-8').split(';')
        method_call = method_call.replace('&%&', ';')
        cur.execute('INSERT INTO methodcalls VALUES (null, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (review_id, o_file, o_s_l, o_s_c, o_e_l, o_e_c, method_call, short_qual, long_qual, d_file, d_s_l, d_s_c, d_e_l, d_e_c))
    app.logger.info('Restoring working tree')
    s = subprocess.run(['git', 'checkout', '--', '.'], cwd=repo_folder, stdout=subprocess.PIPE)
    s = subprocess.run(['git', 'clean', '-qfdx'], cwd=repo_folder, stdout=subprocess.PIPE)
    app.logger.info('Marking the review as ready')
    cur.execute('UPDATE reviews SET status = "ready" WHERE id = ?', (review_id, ))
    conn.commit()
    conn.close()

# clone a repository from github and update its status in the db
def clone_repository(user, repo):
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    repository_url = 'https://github.com/{}/{}'.format(user, repo)
    cur.execute('SELECT id FROM repositories WHERE user = ? AND repo = ?', (user, repo))
    repository_id = cur.fetchone()[0]
    app.logger.info('Issuing git clone {}'.format(repository_url))
    subprocess.run(['git', 'clone', '-q', repository_url, '{}_{}'.format(user, repo)], cwd=CLONED_REPOS_DIR, stderr=subprocess.STDOUT)
    cur.execute('UPDATE repositories SET status = "cloned" WHERE id = ?', (repository_id, ))
    conn.commit()
    conn.close()

@app.route('/review/<int:review_id>/methodcalls')
def dump_methodcalls(review_id):
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    rows = cur.execute('SELECT * FROM methodcalls WHERE review_id = ?', (review_id, )).fetchall()
    output = [dict(ix) for ix in rows]
    conn.close()

    return jsonify(status='ok', data=output)

@app.route('/review/<int:review_id>/file')
def get_file(review_id):
    # check that a file path is provided
    file_path = request.args.get('path')
    if not file_path:
        app.logger.error('No file path provided')
        return jsonify(status='error', error='file_path_not_provided')
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    row = cur.execute('SELECT base_commit_sha, pr_url FROM reviews WHERE id = ?', (review_id, )).fetchone()
    if not row:
        app.logger.error('Review with id {} not existing'.format(review_id))
        return jsonify(status='error', error='review_not_existing')
    conn.close()
    base_commit_sha, pr_url = row[0], row[1]
    matches = pr_url_regex.match(pr_url)
    user, repo = matches.group(1), matches.group(2)
    repo_folder = os.path.abspath('{}/{}_{}'.format(CLONED_REPOS_DIR, user, repo))
    app.logger.info('Checking out base commit {}'.format(base_commit_sha))
    command = ['git', 'checkout', base_commit_sha]
    s = subprocess.run(command, cwd=repo_folder, stdout=subprocess.PIPE)
    app.logger.info('Applying diff')
    s = subprocess.run(['patch', '-p1', '-i', '../../{}/{}.diff'.format(DIFFS_DIR, review_id)], cwd=repo_folder, stdout=subprocess.PIPE)
    app.logger.info('Reading file')
    try:
        contents = open('{}/{}'.format(repo_folder, file_path), 'r').read()
        app.logger.info('Restoring state of repository')
        s = subprocess.run(['git', 'checkout', '--', '.'], cwd=repo_folder, stdout=subprocess.PIPE)
        s = subprocess.run(['git', 'clean', '-qfdx'], cwd=repo_folder, stdout=subprocess.PIPE)
        return jsonify(status='ok', data=contents)
    except:
        app.logger.info('Restoring state of repository')
        s = subprocess.run(['git', 'checkout', '--', '.'], cwd=repo_folder, stdout=subprocess.PIPE)
        s = subprocess.run(['git', 'clean', '-qfdx'], cwd=repo_folder, stdout=subprocess.PIPE)
        return jsonify(status='error', error='file_not_found')


@app.route('/review/<int:review_id>/diff')
def get_diff(review_id):
    # check that a file path is provided
    file_path = request.args.get('path')
    if not file_path:
        app.logger.error('No file path provided')
        return jsonify(status='error', error='file_path_not_provided')
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    row = cur.execute('SELECT base_commit_sha, pr_url FROM reviews WHERE id = ?', (review_id, )).fetchone()
    if not row:
        app.logger.error('Review with id {} not existing'.format(review_id))
        return jsonify(status='error', error='review_not_existing')
    conn.close()
    base_commit_sha, pr_url = row[0], row[1]
    matches = pr_url_regex.match(pr_url)
    user, repo = matches.group(1), matches.group(2)
    repo_folder = os.path.abspath('{}/{}_{}'.format(CLONED_REPOS_DIR, user, repo))
    app.logger.info('Checking out base commit {}'.format(base_commit_sha))
    command = ['git', 'checkout', base_commit_sha]
    s = subprocess.run(command, cwd=repo_folder, stdout=subprocess.PIPE)
    app.logger.info('Applying diff')
    s = subprocess.run(['patch', '-p1', '-i', '../../{}/{}.diff'.format(DIFFS_DIR, review_id)], cwd=repo_folder, stdout=subprocess.PIPE)
    app.logger.info('Reading file')
    app.logger.info('Getting diff of file')
    subprocess.run(['git', 'add', '-N', '.'], cwd=repo_folder, stdout=subprocess.PIPE)
    s = subprocess.run(['git', '--no-pager', 'diff', '-U99999999', file_path], cwd=repo_folder, stdout=subprocess.PIPE)
    app.logger.info('Restoring state of repository')
    subprocess.run(['git', 'reset'], cwd=repo_folder, stdout=subprocess.PIPE)
    subprocess.run(['git', 'checkout', '--', '.'], cwd=repo_folder, stdout=subprocess.PIPE)
    subprocess.run(['git', 'clean', '-qfdx'], cwd=repo_folder, stdout=subprocess.PIPE)
    if s.returncode != 0:
        return jsonify(status='error', error='file_not_found')
    else:
        contents = s.stdout.decode('utf-8')
        return jsonify(status='ok', data=contents)

if __name__ == '__main__':
    app.run()
