import csv
import pandas as pd
import git
import re
import os
import tempfile
import subprocess
import sys

debug = False

if len(sys.argv) != 3:
    print(f'Usage: {sys.argv[0]} repo_org repo_name')
    sys.exit(1)

repo_org = sys.argv[1]
repo_name = sys.argv[2]

# path of the binary of gumtree
gumtree_path = '/home/joined/Downloads/gumtree-20161230-2.1.0-SNAPSHOT/bin/gumtree'

def getFineGrainedStats(firstFileContents, secondFileContents):
    '''
    This function uses gumtree to extract fine grained differences from two Java files.
    It takes as parameters two strings containing the contents of the files.
    '''

    # create two temporary files materializing on disk the content
    # of the source code
    firstFile = tempfile.NamedTemporaryFile(suffix='.java', delete=False)
    firstFile.write(firstFileContents.encode())
    firstFile.seek(0)
    firstFile.close()

    secondFile = tempfile.NamedTemporaryFile(suffix='.java', delete=False)
    secondFile.write(secondFileContents.encode())
    secondFile.seek(0)
    secondFile.close()

    # invoke gumtree to compute the differences and capture the output
    result = subprocess.check_output([gumtree_path, 'diff', firstFile.name, secondFile.name]).decode()

    # delete the two files
    os.unlink(firstFile.name)
    os.unlink(secondFile.name)

    return result

def extractPackageFromFileContents(fileContents):
    '''
    Given the contents of a Java file as a string, uses a regular expression
    to extract the name of the package which the file belongs to.
    '''
    packageNameMatch = re.search(progPackageRegex, fileContents)
    if not packageNameMatch:
        return None
    return packageNameMatch.group(1)

def printIfDebug(s):
    if debug:
        print(s)

def getCommitStats(commit):
    printIfDebug(f'Processing commit {commit.hexsha}')
    changedFiles = commit.stats.files.keys()
    changedJavaFiles = [changedFile
                        for changedFile in changedFiles
                        if changedFile.rstrip().endswith('.java')]

    modifiedPackages = set()

    for filePath in changedJavaFiles:
        printIfDebug(f'- Analyzing changes to file {filePath}')

        newFileExists, oldFileExists = True, True

        # if the file was renamed in the current commit, we will get the renaming
        # in the path of the file. we can tell the file was renamed because there is a
        # fat arrow in the path to indicate the renaming
        if '=>' in filePath:
            printIfDebug('-- File was renamed/moved')
            # there are two main possibilities for the renaming: either a partial
            # renaming (only a part of the part) or a full rename.
            # we handle the two cases distinctly because in the first case curly braces
            # are used to indicate the partial rename
            partialRenamingMatch = re.search(progRenamedFileRegexPattern, filePath)
            if partialRenamingMatch:
                printIfDebug(f'group1 {partialRenamingMatch.group(1)}, group2 {partialRenamingMatch.group(2)}')
                # in this case the renaming is indicated as follows
                # {old/partial/path => new/partial/path}/file.java

                # it turns out that one of the two partial paths (old and new)
                # can be empty, meaning that either the file was moved into a subfolder
                # which was just created or it was moved to its parent folder
                # the following logic deals with this.
                if partialRenamingMatch.group(1) == '':
                    oldPath = re.sub(r'{.*}/', '', filePath)
                    newPath = re.sub(r'{.*}', partialRenamingMatch.group(2), filePath)
                elif partialRenamingMatch.group(2) == '':
                    newPath = re.sub(r'{.*}/', '', filePath)
                    oldPath = re.sub(r'{.*}', partialRenamingMatch.group(1), filePath)
                else:
                    oldPath = re.sub(r'{.*}', partialRenamingMatch.group(1), filePath)
                    newPath = re.sub(r'{.*}', partialRenamingMatch.group(2), filePath)
            else:
                # in this case it's
                # oldpath/file1.java => newpath/file2.java
                oldPath, newPath = filePath.split(' => ')

            # if the file was renamed we know that it existed both previous to and
            # after the commit so no need for the logic that follows that checks for this
            newFileContents = repo.git.show(f'{commit}:{newPath}')
            oldFileContents = repo.git.show(f'{commit}^1:{oldPath}')
        else:
            # the file could have been created by the current commit or also deleted by it
            # we need some logic to handle this cases because git will give an error
            # if we ask for a file that does not exist
            try:
                newFileContents = repo.git.show(f'{commit}:{filePath}')
            except:
                newFileExists = False
                newFileContents = ' '

            try:
                oldFileContents = repo.git.show(f'{commit}^1:{filePath}')
            except:
                oldFileContents = ' '
                oldFileExists = False

        if newFileExists and not oldFileExists:
            printIfDebug('-- File was created in this commit')
            newPackageName = extractPackageFromFileContents(newFileContents)
            oldPackageName = None
        elif oldFileExists and not newFileExists:
            printIfDebug('-- File was deleted in this commit')
            newPackageName = None
            oldPackageName = extractPackageFromFileContents(oldFileContents)
        else:
            # at least one among old and new file must exist so if we arrive here
            # we know that they both exist.
            newPackageName = extractPackageFromFileContents(newFileContents)
            oldPackageName = extractPackageFromFileContents(oldFileContents)

        printIfDebug(f'-- New package name: {newPackageName}, Old package name: {oldPackageName}')

        if oldPackageName:
            modifiedPackages.add(oldPackageName)
        if newPackageName:
            modifiedPackages.add(newPackageName)

        # getFineGrainedStats(oldFileContents, newFileContents)

    printIfDebug(f'- Modified packages: {modifiedPackages}')

    return modifiedPackages

if __name__ == '__main__':
    pullRequestsData = f'output_{repo_org}_{repo_name}.csv'
    # read the csv file containing the pull requests info of the repo
    df = pd.read_csv(pullRequestsData)
    # for some pull requests, there is no merge commit info.
    # not sure why. this Github API is not really well documented.
    # we just drop the pull requests without merge commit info.
    df.dropna(subset=['mergeCommit'], inplace=True)

    # we compile some regexes in advance for speed reasons.
    # regex to match the package name in a java file
    packageRegexPattern = r'package (.+);'
    progPackageRegex = re.compile(packageRegexPattern)
    # regex to match the renaming of a file
    renamedFileRegexPattern = r'{(.*)? => (.*)?}'
    progRenamedFileRegexPattern = re.compile(renamedFileRegexPattern)

    repo = git.Repo(repo_name)

    def processCommit(mergeCommitSHA):
        # for some reason, not all the merge commits as returned by the Github API
        # are valid. the API itself is not really documented so this is not solvable
        try:
            commit = repo.commit(mergeCommitSHA)
        except:
            return None
        else:
            return len(getCommitStats(commit))

    # create new column in the dataframe containing the number of changed packages
    df['changedPackages'] = df.mergeCommit.apply(processCommit)
    # save output to csv
    df.to_csv(f'output_{repo_org}_{repo_name}_withPackageInfo.csv', index=False)
