# Define and parse command line arguments.
import argparse
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Dict

from tqdm import tqdm

# Regular expression to identify (and parse) the supported file names.
# This might change depending on what type of file I'm moving, and what naming convention
# my camera at the time used.
# It's very likely easy to consolidate the first 2 into one expression, but heh, it's past
# midnight right now.
# Videos go to a different directory, so let's make sure not to match them at the same
# time as photos (the backup I'm dealing with is messy like that).
FILE_NAME_REGEX = "^[IMG|PANO]+_([0-9]{8})_([0-9]{6})"
# FILE_NAME_REGEX = "([0-9]{8})_([0-9]{6})"
# FILE_NAME_REGEX = "^VID+_([0-9]{8})_([0-9]{6})"

# Define and parse the arguments.
parser = argparse.ArgumentParser(
    description="Helper script to sort photos in folders based on their name"
)

parser.add_argument(
    "-p", "--path",
    help="The path to the directory containing the photos"
)

args = parser.parse_args()

# Read the files (and count them for later).
files = os.listdir(args.path)
n_files = len(files)

# Track the files that need moving. This dict associates the absolute path of a directory
# to the names of files that must be moved to this directory.
files_to_move: Dict[str, List[str]] = {}
# Iterate over the file names.
for file in files:
    # Check if the file name matches the regular expression.
    match = re.match(FILE_NAME_REGEX, file)
    if match is not None:
        # If so, extract the date and parse it.
        date = datetime.strptime(match.group(1), "%Y%m%d")

        # Figure out the absolute path of the directory that this file must go into.
        # This directory is yyyy/mm (under the directory given by args.path), based on the
        # date indicated in the file's name.
        dir_path = date.strftime("%Y/%m")
        dir_path = os.path.join(args.path, dir_path)

        # Record the file in the dictionary.
        if dir_path not in files_to_move:
            files_to_move[dir_path] = []
        files_to_move[dir_path].append(file)
    else:
        # If the file name does not match, ajust the number of files to move.
        n_files -= 1

print(f"Moving {n_files} files")

# Show a nice progress bar.
with tqdm(total=n_files) as pbar:
    for dir_path, files in files_to_move.items():
        # Create each directory if necessary.
        Path(dir_path).mkdir(parents=True, exist_ok=True)

        for file in files:
            # Figure out the file's absolute source and destination paths.
            src_path = os.path.join(args.path, file)
            dst_path = os.path.join(dir_path, file)

            # Move the file.
            shutil.move(src_path, dst_path)

            # Update the progress bar.
            pbar.update(1)
