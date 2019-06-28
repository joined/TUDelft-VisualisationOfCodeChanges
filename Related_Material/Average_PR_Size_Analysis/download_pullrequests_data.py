#!/usr/bin/env python3

import pygit2
import requests
import pprint
import csv
import sys

if len(sys.argv) < 4:
    print('Usage: {} organization repository access_token'.format(sys.argv[0]))
    sys.exit(1)

repo_github_org, repo_name = sys.argv[1], sys.argv[2]

headers = {"Authorization": "bearer {}".format(sys.argv[3])}

def run_query(query):
    request = requests.post('https://api.github.com/graphql', json={'query': query}, headers=headers)
    if request.status_code == 200:
        return request.json()
    else:
        raise Exception("Query failed to run by returning code of {}. {}".format(request.status_code, query))

# We run the first query without pagination
first_query = """
{{
  repository(owner: "{}", name: "{}") {{
    pullRequests(first: 100, states: [MERGED]) {{
      edges {{
        cursor
        node {{
          number
          title
          id
          mergedAt
          changedFiles
          mergeCommit {{
            oid
          }}
        }}
      }}
    }}
  }}
}}""".format(repo_github_org, repo_name)

# All the pull requests are stored in this list
all_results_data = []

result = run_query(first_query)
result_data = result['data']['repository']['pullRequests']['edges']

print('Organisation: {}, Repository: {}'.format(repo_github_org, repo_name))
print('For the first query, got {} pull requests'.format(len(result_data)))
all_results_data.extend(result_data)

# Repeat increasing pagination till we are over with all the PRs
while True:
    last_cursor = all_results_data[-1]['cursor']

    query = """
    {{
      repository(owner: "{}", name: "{}") {{
        pullRequests(first: 100, states: [MERGED], after: "{}") {{
          edges {{
            cursor
            node {{
              number
              title
              id
              mergedAt
              changedFiles
              mergeCommit {{
                oid
              }}
            }}
          }}
        }}
      }}
    }}""".format(repo_github_org, repo_name, last_cursor)

    result = run_query(query)
    result_data = result['data']['repository']['pullRequests']['edges']
    all_results_data.extend(result_data)

    print('Got {} more pull requests, total is now {}'.format(len(result_data), len(all_results_data)))

    if len(result_data) == 0:
        break

print('Finished downloading PR info')

# Save results to CSV
outfile = 'output_{}_{}.csv'.format(repo_github_org, repo_name)

print('Writing output to {}'.format(outfile))

with open(outfile, 'w', newline='') as csvfile:
    fieldnames = ['id', 'number', 'title', 'mergeCommit', 'changedFiles', 'mergedAt']
    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
    writer.writeheader()

    for pull_request in all_results_data:
        mergeCommitInfo = pull_request['node']['mergeCommit']
        mergeCommitHash = mergeCommitInfo['oid'] if mergeCommitInfo else None

        writer.writerow({
            'id': pull_request['node']['id'],
            'number': pull_request['node']['number'],
            'title': pull_request['node']['title'],
            'mergeCommit': mergeCommitHash,
            'changedFiles': pull_request['node']['changedFiles'],
            'mergedAt': pull_request['node']['mergedAt']
        })

print('Done.')
